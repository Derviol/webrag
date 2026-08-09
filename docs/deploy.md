# 部署文档：Docker 一键部署

> 状态：使用中（`docker compose up -d` 一键启动全部服务：基础设施 + 应用容器 + 自动建库）。

## 部署架构

```text
浏览器 ──> localhost:8000 ──> webrag-app (Docker，FastAPI：爬虫/嵌入/DeepSeek/前端)
                                  │
        ┌─────────────────────────┼────────────────────────┐
        ▼                         ▼                        ▼
milvus-standalone          etcd (元数据)  minio (存储)   redis (去重/限流)
                                                            │
                                                        mysql (管理后台)
```

- 容器内互连用 **compose 服务名**（`milvus:19530` / `redis:6379` / `mysql:3306`），由 compose 的 `environment` 注入覆盖；
- 本机 Attu 与本地 uv 应用仍通过 `http://localhost:19530` 直连 Milvus，两者互不干扰；
- BGE-M3 / reranker 模型（2GB+）不进镜像、不入 git，由 `./models:/app/models:ro` 挂载（模型获取见 quickstart.md §1）。

## 服务清单

| 服务 | 镜像/构建 | 端口 | 作用 |
| --- | --- | --- | --- |
| `etcd` | quay.io/coreos/etcd:v3.5.5 | 内部 | Milvus 元数据存储 |
| `minio` | minio/minio:RELEASE.2023-03-20T20-16-18Z | 9000 / 9001(console) | 向量与日志持久化，控制台账号 minioadmin/minioadmin |
| `milvus` | milvusdb/milvus:v2.4.24 | **19530** / 9091 | 向量数据库本体 |
| `redis` | redis:7-alpine | 6379 | 缓存与限流，AOF 持久化 |
| `mysql` | mysql:8.0 | 3306 | 管理后台（离线知识入库）：管理员账号 + 文档记录，utf8mb4，named volume 持久化 |
| `webrag-app` | 本项目 Dockerfile | **8000** | FastAPI 应用 + 前端页面 |
| `init` | 同 webrag-app 镜像 | 无 | 一次性建库任务（幂等，milvus healthy 后自动执行） |

数据持久化：etcd / minio / milvus / redis / mysql 均为 named volume（`docker compose down` 不丢数据，`down -v` 清空）。

## 一键启动

前置：`models/` 目录含 BGE-M3 与 reranker（不入 git，自行下载放置，见 quickstart.md §1 模型获取），`.env` 已填 DeepSeek / 搜索 API Key。

> 镜像构建说明：**torch 已随 uv 统一管理**——Dockerfile 的 `uv sync --frozen` 按 uv.lock 直接安装 CPU 版 torch（`torch>=2.2,<2.7` 经 `[tool.uv] find-links` 阿里云 CPU 源解析为 `2.6.0+cpu`，约 200MB），**无手动安装步骤**。CPU 瘦身：torch 的 CPU wheel 元数据自带的 triton + nvidia-*（~2GB，纯 CPU 推理用不到）通过 pyproject `[tool.uv] exclude-dependencies` 从解析图剔除，uv.lock 与镜像均不含它们，镜像约 1GB；Dockerfile 内 `import torch` 自检保证剔除不过头（构建期 fail-fast）。

```bash
# 1. 一键启动：构建应用镜像（首次约 5-15 分钟）+ 启动全部服务 + 自动建库
docker compose up -d

# 2. 确认就绪：webrag-app 应为 healthy；init 容器应 Exited (0)（建库成功）
docker compose ps
docker compose logs init        # 查看建库结果

# 3. 验收
curl http://localhost:8000/health
# → {"status":"ok","milvus":true,"embed_model":true}
```

打开 http://localhost:8000 即可问答。

## 问答缓存（新流程）

- 问答缓存 collection 名为 `webrag_qa`（.env 可用 `MILVUS_QA_COLLECTION` 覆盖，默认即此值）；
- 首次提问走联网链路，回答完成后**自动把「问题 + 摘要 + 来源」写入问答缓存**；
- 同问 / 近似问第二次起命中缓存：回答直接返回历史摘要 + 来源（响应 `cached=true`），前端显示「⚡ 命中历史问答缓存」，秒回不联网；
- 缓存命中阈值 `retriever.qa_min_score`（默认 0.80）在 config/settings.yaml 调整（宁高勿低）；
- 旧预建知识库 `webrag_kb` 已废弃，可清理：Attu 中删除，或 `docker compose exec init python -c "from pymilvus import utility; utility.drop_collection('webrag_kb')"`。

## 本机开发（不走容器）

```bash
docker compose up -d --scale init=0   # 只起基础设施（跳过 init 服务）
uv run python scripts/init_milvus.py     # 手动建库（幂等）
uv run uvicorn src.webrag.main:app --reload   # 本地起应用，连 localhost:19530
```

> `docker compose up -d` 会自动拉起 webrag-app 与 init；本地开发想只跑基础设施时，用
> `docker compose up -d etcd minio milvus redis mysql`（按服务名精确启动）。

## 管理后台（离线知识入库）

- MySQL（管理后台账号库）随 compose 一起启动（服务名 `mysql`，库 `webrag_admin`，账号/密码/库名用
  compose 环境变量 `MYSQL_ROOT_PASSWORD / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE` 覆盖，默认见 docker-compose.yml）；
- 离线知识库 collection `webrag_offline_kb`（.env 可用 `MILVUS_OFFLINE_COLLECTION` 覆盖）**首次入库时自动创建**，
  无需手动建库；与问答缓存 webrag_qa 相互独立；
- 首次使用创建管理员（MySQL healthy 后执行，容器内会注入 `MYSQL_HOST=mysql`）：

```bash
docker compose exec webrag-app uv run --no-sync python scripts/init_admin.py \
    --username admin --password <你的密码>
```

- 浏览器打开 `http://localhost:8000/admin/` 登录，上传 .txt/.md/.html 或粘贴文本入库；
  入库为后台异步（processing → done/failed），前端自动轮询；
- 接口契约见 docs/api.md §1.4；模块说明见 src/webrag/admin/README.md；
- 常用配置（config/settings.yaml `admin:` 段）：token 有效期、上传大小上限、单文档字符上限。

## 环境变量注入说明

- `.env` 通过 `env_file` 注入容器（密钥不写入镜像，构建时被 .dockerignore 排除）；
- compose `environment:` 覆盖容器内连库地址（`MILVUS_URI=http://milvus:19530`、`REDIS_URL=redis://redis:6379`、
  `MYSQL_HOST=mysql`）——`.env` 里的 localhost 仅本机直连可用；
- 应用内 `load_dotenv` 不覆盖已注入的环境变量，容器内以 compose 注入值为准。

## 本机 Attu 连接

Attu 是 Milvus 的可视化客户端（本机 GUI，不部署在 Docker）：

1. 安装 Attu 2.x（与 Milvus 2.4 服务端匹配，勿装 3.x）；
2. 启动后连接地址填 `http://localhost:19530`；
3. 连接成功后可浏览 collection（webrag_qa）、查询向量、查看索引。

> Attu 3.x 面向 Milvus 3.x；本仓库服务端锁 v2.4，请安装 Attu 2.x。

## 版本对齐（务必保持一致）

| 组件 | 版本 | 位置 |
| --- | --- | --- |
| Milvus 服务端 | v2.4.24（2.4 线最新稳定版，无 v2.4-latest 标签） | docker-compose.yml |
| pymilvus 客户端 | >=2.4,<2.5（解析到 2.4.x） | pyproject.toml |
| Attu（本机 GUI） | 2.x | 手动安装 |

## 常见问题

- **Milvus 反复重启 / 端口占用**：`docker compose ps` 看健康状态；先 `docker compose down` 再 `up -d`；容器名冲突时用 `docker compose down -v` 清数据后重试；
- **webrag-app 一直 starting**：`docker compose logs webrag-app` 看启动日志；`start_period` 30s 后仍未 healthy 多为 Milvus 未就绪或 8000 端口被占用；
- **/health 的 embed_model 为 false**：`models/` 目录缺失或挂载未生效（`docker compose exec webrag-app ls /app/models` 排查）；
- **init 建库失败**：`docker compose logs init` 看报错；等 milvus healthy 后 `docker compose run --rm init` 重跑（幂等）；
- **Attu 连不上**：确认 `docker compose ps` 中 milvus 为 healthy，且连接地址为 `http://localhost:19530`（非 https）；
- **Docker Hub 拉取失败（EOF / Interrupted）**：国内直连 docker.io 经常被限流。Windows 在 Docker Desktop → Settings → Docker Engine 的 daemon.json 中加 `registry-mirrors`（如 `https://docker.m.daocloud.io` / `https://docker.1ms.run` / `https://hub.rat.dev`），保存并重启 Docker Desktop 后重新 `docker compose up -d`；etcd 走 quay.io 不受影响；
- **构建时 ghcr.io 拉 uv 失败**：Dockerfile 已改 `RUN pip install uv`（清华 PyPI 源，见文件头注释），不再依赖 ghcr.io；仍失败多为网络波动，重试构建即可；
- **`docker compose up -d --build` 报 `x-docker-expose-session-sharedkey` gRPC 错误**：compose v5 的 bake 构建会话与 Docker Desktop 的兼容问题（普通 `docker build` 正常）。临时绕行：手动构建镜像后不带 `--build` 启动——
  ```bash
  docker build -t rag-webrag-app .
  docker tag rag-webrag-app rag-init
  docker compose up -d
  ```
  等 Docker Desktop 下个更新修复后再用 `--build`；
- **构建拉 python:3.11-slim 失败**：docker.io 被限流时，Dockerfile 基础镜像已固定为本地可得的 `python:3.12-slim`（pyproject 允许 3.10-3.13），无需改动。

## 待办

- 生产环境替换 minio 默认账号密码（环境变量注入）。
