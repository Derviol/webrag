# 本地开发快速开始

> 单人开发 + Git 工作流。本文是本地环境的完整步骤；Docker 一键部署见 [deploy.md](deploy.md)。

## 1. 环境准备

环境由 **uv** 统一管理（依赖清单 `pyproject.toml` + 版本锁 `uv.lock` + 解释器 `.python-version`）。

```bash
# ① 拉取代码（Git）
git clone https://github.com/Derviol/webrag.git
cd webrag

# ② 安装 uv（每台机器一次）：
#    Windows: pip install uv
#    macOS/Linux: 官方一键安装（https://docs.astral.sh/uv/）

# ③ 一键同步环境：自动下载 Python 3.11 + 创建 .venv + 安装全部依赖（含 CPU 版 torch，走阿里云源）
uv sync

# ④ 复制配置模板并填入真实值
cp .env.example .env
#    必填：DEEPSEEK_API_KEY；联网搜索按需填 SEARCH_PROVIDER / SEARCH_API_KEY
#    其他连接信息默认指向 localhost（Docker 基础设施），一般不用改
```

### 模型获取（models/，不入 git）

`models/` 已被 .gitignore 排除（BGE-M3 约 2GB+），需自行准备：

- `models/bge-m3/`：HuggingFace `BAAI/bge-m3`（dense + sparse）
- `models/bge-reranker-large/`：HuggingFace `BAAI/bge-reranker-large`

可下载后放置到对应路径，或从旧机器 / 局域网拷贝。缺模型时服务可启动但 `/health` 的 `embed_model` 为 false，/ask 无法嵌入。

### 环境约定（单人维护）

- 依赖变更：改 `pyproject.toml` → `uv lock` 更新锁 → `uv sync` 应用 → 提交 pyproject 与 uv.lock；
- `uv.lock` 是唯一版本真相，禁手改；禁止绕过 uv 直接 `pip install` 后不更新锁；
- **torch 随 uv 统一管理（CPU-only）**：pyproject 依赖 `torch>=2.2,<2.7`，`[tool.uv] find-links` 挂阿里云 CPU 源——win/linux 解析 `2.6.0+cpu`，macOS 回落 PyPI CPU 版；nvidia-*/triton（~2GB，纯 CPU 用不到）已通过 `exclude-dependencies` 剔除；
- 未激活 venv 也能跑命令：`uv run <cmd>` 自动使用项目环境。

## 2. 启动基础设施（Docker）

Milvus + Redis + MySQL 由 docker-compose 编排（详见 [deploy.md](deploy.md)）：

```bash
docker compose up -d            # 一键启动全部服务（含 webrag-app 与自动建库）
docker compose ps               # milvus 标 healthy 即可（init 容器 Exited 0 说明建库成功）
```

本地开发只想跑基础设施、不启动应用容器：

```bash
docker compose up -d --scale init=0 etcd minio milvus redis mysql
```

> Redis / MySQL / Milvus 连接地址的默认值见 `.env.example`；容器内应用走 compose 服务名（`milvus:19530` / `redis:6379` / `mysql:3306`），本机直连用 localhost。

## 3. 初始化数据库

```bash
# 建问答缓存 collection（webrag_qa）——幂等，可重跑（milvus healthy 后执行）
uv run python scripts/init_milvus.py

# 创建管理员账号（管理后台用；普通用户可在前端「账户」模块注册）
uv run python scripts/init_admin.py --username admin --password <你的密码>
```

## 4. 启动服务

```bash
uv run uvicorn src.webrag.main:app --reload
```

- 问答页：http://localhost:8000 （注册/登录后提问；左侧「生成参数」可调温度 / 联网开关 / 搜索网页数量）
- 管理后台：http://localhost:8000/admin/ （管理员登录后上传离线知识文档）
- 接口文档：http://localhost:8000/docs （FastAPI 自带 OpenAPI）

## 5. 示例请求

```bash
# 整包 JSON（需先登录拿 token，见 /auth/login）
curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"question": "2025 年大模型行业有哪些重要进展？"}'

# 探活
curl http://localhost:8000/health
# → {"status":"ok","milvus":true,"embed_model":true,...}
```

## 6. 常用命令速查

```bash
uv sync                                     # 还原环境（改 pyproject 后执行）
uv run pytest                               # 全部测试
uv run ruff check                           # lint
uv run python scripts/test_query.py "问题"  # 命令行冒烟一条问题
uv run python eval/run_eval.py              # 评测（Recall@k / MRR）
docker compose up -d                        # 起全部服务（需 Docker Desktop）
docker compose logs webrag-app -f           # 看应用日志
```

## 7. 端口速查

| 项 | 地址 | 说明 |
| --- | --- | --- |
| Web 服务 | http://localhost:8000 | /ask、/ask/stream、/health、/admin、/auth、/chat |
| Milvus | localhost:19530 | 本机 Attu 2.x 连接（服务端锁 v2.4，勿装 Attu 3.x） |
| MinIO 控制台 | localhost:9001 | 排查用，账号 minioadmin/minioadmin |
| MySQL | localhost:3306 | 账户 / 聊天记录 / 后台文档 |
| Redis | 容器内 `redis:6379` | 宿主映射端口见 docker-compose.yml（本机 6379 常被原生 Redis 占用） |
| Python | 3.11 | `.python-version` 锁定，uv 自动装（容器镜像 3.12-slim） |
| 依赖锁 | uv.lock | 唯一版本真相，禁手改 |
| 模型 | models/ | BGE-M3 + reranker，gitignore 不入库 |

## 8. 部署环境

本地开发仅用于调试；部署环境直接用 Docker 一键启动（含 webrag-app 容器与自动建库），见 [deploy.md](deploy.md)。
