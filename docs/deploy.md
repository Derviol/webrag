# 部署文档：Docker 基础设施

> 状态：v1（与根目录 docker-compose.yml 对齐）。M4 补充 webrag-app 应用容器（Dockerfile）。

## 部署架构

Milvus standalone（+ etcd 元数据 + minio 存储）与 Redis 由 Docker Compose 编排，
**本机 Attu 与本地 uv 应用都通过 `http://localhost:19530` 连接 Milvus**：

```text
本机 Attu (GUI) ─┐
                 ├──> localhost:19530 ──> milvus-standalone (Docker)
uv 应用 / scripts─┘                            │
                                              ├─ etcd (元数据)
                                              └─ minio (存储)
uv 应用 ──> localhost:6379 ──> redis (Docker，URL 去重/搜索缓存/限流)
```

## 服务清单

| 服务 | 镜像 | 端口 | 作用 |
| --- | --- | --- | --- |
| `etcd` | quay.io/coreos/etcd:v3.5.5 | 内部 | Milvus 元数据存储 |
| `minio` | minio/minio:RELEASE.2023-03-20T20-16-18Z | 9000 / 9001(console) | 向量与日志持久化，控制台账号 minioadmin/minioadmin |
| `milvus` | milvusdb/milvus:v2.4.24 | **19530** / 9091 | 向量数据库本体（Attu / 应用连接口） |
| `redis` | redis:7-alpine | 6379 | 缓存与限流，AOF 持久化 |

数据持久化：etcd / minio / milvus / redis 均为 named volume（`docker compose down` 不丢数据，`down -v` 清空）。

## 启动步骤

```bash
# 1. 启动全部服务（首次会拉镜像，约几分钟）
docker compose up -d

# 2. 确认就绪（milvus / redis 显示 healthy）
docker compose ps

# 3. 初始化 Milvus collection 与索引（建库，幂等可重跑）
uv run python scripts/init_milvus.py

# 4. 启动应用，/health 应显示 milvus: true
uv run uvicorn src.webrag.main:app --reload
```

## 本机 Attu 连接

Attu 是 Milvus 的可视化客户端（本机 GUI，不部署在 Docker）：

1. 安装 Attu 2.x（与 Milvus 2.4 服务端匹配，勿装 3.x）；
2. 启动后连接地址填 `http://localhost:19530`；
3. 连接成功后可浏览 collection（webrag_kb）、查询向量、查看索引。

> Attu 3.x 面向 Milvus 3.x；本仓库服务端锁 v2.4，请安装 Attu 2.x。

## 版本对齐（务必保持一致）

| 组件 | 版本 | 位置 |
| --- | --- | --- |
| Milvus 服务端 | v2.4.24（2.4 线最新稳定版，无 v2.4-latest 标签） | docker-compose.yml |
| pymilvus 客户端 | >=2.4,<2.5（解析到 2.4.x） | pyproject.toml |
| Attu（本机 GUI） | 2.x | 手动安装 |

## 常见问题

- **Milvus 反复重启 / 端口占用**：`docker compose ps` 看健康状态；先 `docker compose down` 再 `up -d`；容器名冲突时用 `docker compose down -v` 清数据后重试；
- **Attu 连不上**：确认 `docker compose ps` 中 milvus 为 healthy，且连接地址为 `http://localhost:19530`（非 https）；
- **首次 init_milvus 失败**：Milvus 冷启动需 1-2 分钟，等 healthy 后再跑 scripts/init_milvus.py；
- **Docker Hub 拉取失败（EOF / Interrupted）**：国内直连 docker.io 经常被限流。Windows 在 Docker Desktop → Settings → Docker Engine 的 daemon.json 中加 `registry-mirrors`（如 `https://docker.m.daocloud.io` / `https://docker.1ms.run` / `https://hub.rat.dev`），保存并重启 Docker Desktop 后重新 `docker compose up -d`；etcd 走 quay.io 不受影响。

## M4 待办

- webrag-app 应用容器（Dockerfile + compose 服务，含 BGE-M3 模型 volume 挂载）；
- 生产环境替换 minio 默认账号密码（环境变量注入）。
