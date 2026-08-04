# 本地开发快速开始（规划）

## 环境准备

环境由 **uv** 统一管理（依赖清单 `pyproject.toml` + 版本锁 `uv.lock` + 解释器 `.python-version`，全部随仓库走）。

```bash
git clone <repo-url> && cd web-rag

# ① 安装 uv（每台机器一次）：
#    Windows: pip install uv
#    macOS/Linux: 见 https://docs.astral.sh/uv/ 官方一键安装

# ② 一键同步环境：自动下载 Python 3.11 + 创建 .venv + 安装全部依赖（含 torch CPU 版）
uv sync

# ③ 激活虚拟环境（uv 自动创建）
# Windows: .venv\Scripts\activate
source .venv/bin/activate

# ④ 启动基础设施（Milvus + Redis，Docker）：
#    docker compose up -d
#    本机 Attu 2.x 连接 http://localhost:19530（详见 docs/deploy.md）

# 复制配置模板并填入：
#   DEEPSEEK_API_KEY / SEARCH_API_KEY / MILVUS_URI
cp .env.example .env

# 初始化 Milvus collection 与索引（先确认 docker compose ps 中 milvus 为 healthy）
python scripts/init_milvus.py
```

### 环境统一约定（11 人团队）

- 环境定义**随仓库走**：`pyproject.toml` + `uv.lock` + `.python-version` 一并提交，每台机器只需 git + uv；
- 新成员 / 新机器：`uv sync` 一条命令还原全部环境，无需手动建 venv、装 Python、装 torch；
- 新增 / 升级依赖：改 `pyproject.toml` → `uv lock --upgrade` 更新锁 → `uv sync` 应用 → 提交 pyproject 与 uv.lock 变更；
- `uv.lock` 是唯一版本真相，禁止手改；禁止绕过 uv 直接 `pip install` 后不更新锁；
- torch 默认走 **CPU 索引**（pyproject 中 `pytorch-cpu`）；GPU 机器把该索引 url 换成对应 CUDA 版本后执行 `uv lock --upgrade-package torch && uv sync`；
- 未激活 venv 也能跑命令：`uv run <cmd>` 自动使用项目环境（如 `uv run uvicorn ...`）。

## 启动服务

```bash
# 未激活 venv 时：uv run uvicorn src.webrag.main:app --reload
# 已激活 venv 后：
uvicorn src.webrag.main:app --reload
```

## 示例请求

```bash
curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "2025 年大模型行业有哪些重要进展？"}'
```

## 部署环境

本地开发仅用于调试；部署环境直接用 Docker 一键启动，见 [deploy.md](deploy.md)。
