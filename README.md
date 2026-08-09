# WebRAG — 联网检索增强问答系统

> 单人开发完成 ✅ ｜ Git 托管：<https://github.com/Derviol/webrag>（`main` / `dev` 分支）
> 架构见 [docs/architecture.md](docs/architecture.md)｜接口契约见 [docs/api.md](docs/api.md)｜部署见 [docs/deploy.md](docs/deploy.md)

## 1. 项目简介

一个**面向联网信息的 RAG（检索增强生成）问答系统**：

- 用户输入自然语言问题，系统先检索**历史问答缓存**——相似问题直接复用已存摘要 + 来源，秒回；
- 未命中时按需**联网检索**网页，或检索**离线知识库**（管理后台上传的文档），抓取清洗后交给大模型生成**带数据源标注**的回答；
- 每个回答附可点击的引用来源（URL），保证可溯源；回答成功后自动存入问答缓存，越问越快；
- 支持**多轮对话**（追问自动改写为自包含问题）、**SSE 流式输出**、**时效性锚定**（「近日/近期/最新」按客户端时间定位）。

### 功能特性

| 特性 | 说明 |
| --- | --- |
| 问答缓存 | 向量库 `webrag_qa` 存「问题 → 摘要 + 来源」，相似问题（余弦 ≥ `qa_min_score`）秒回 `cached=true`，不联网、不调 LLM |
| 联网检索 | 搜索 API（Bing / Tavily / 博查适配层）+ 自研爬虫（限速 / 重试 / robots / Redis URL 去重）；前端开关控制（**opt-in，默认关闭**） |
| 离线知识库 | 管理后台上传 .txt/.md/.html → parser → chunker → embedder 管线异步入库（`webrag_offline_kb`）；内网 / 隐私场景可完全离线作答 |
| 多轮对话 | 请求携带 `history` 时自动判定**追问**并改写为自包含完整问题（LLM 判定+改写，失败降级原文）；缓存键用改写后问题，同义追问可命中 |
| 时效性锚定 | 「近日/近期/今天」等相对时间词以**客户端本地时间**（`client_time`）为基准：搜索词拼入日期、LLM Prompt 注入「时间基准」段 |
| 混合检索 | BGE-M3 **dense + sparse 双向量** + bge-reranker 精排（噪声剔除 / Jaccard 上下文去重 / 来源质量分层 / 意图动态权重） |
| 查询改写 | 意图分类 + 多路改写（关键词 / 正式 / 子问题）+ HyDE 草稿，均可独立开关 |
| 流式输出 | `/ask/stream` SSE：检索进度 `status` 事件 + 生成 `delta` 打字机渲染；**缓存命中同样分块流式输出摘要** |
| 幻觉检测 | 生成后逐句核验（`hallucination_checker`，可自动重写）；后端引用校验剔除幽灵引用 |
| 账户与记录 | JWT 账户系统（user / admin，PBKDF2）+ 聊天记录 MySQL 持久化（`/chat/conversations` CRUD）+ 管理后台独立子系统 |
| 结构化日志 | JSONL `logs/app.log`：请求级指标（分阶段耗时 / 命中率 / token / TTFT）+ 周期性聚合统计 + `GET /logs/stats` |
| 反馈闭环 | `POST /feedback` 在线反馈收集 + `eval/` 评测集（Recall@k / MRR 基线报告） |

### 技术栈

| 组件 | 选型 |
| --- | --- |
| 向量数据库 | Milvus v2.4.24（standalone，Docker Compose 编排，客户端 pymilvus 2.4.x） |
| Embedding 模型 | BGE-M3（dense + sparse 双向量，本地 `models/`） |
| 重排模型 | bge-reranker（本地 `models/bge-reranker-large`，sentence-transformers CrossEncoder） |
| LLM | DeepSeek（OpenAI 兼容接口；模型名由 `settings.yaml llm.model` / `.env DEEPSEEK_MODEL` 配置） |
| 网页采集 | 搜索引擎 API 适配层（Bing / Tavily / 博查）+ 自研爬虫（requests + trafilatura） |
| 存储 | Redis（URL 去重 / 登录限流）、MySQL 8.0（账户 / 聊天记录 / 后台文档） |
| 服务框架 | Python 3.11（uv 统一管理环境；容器镜像 3.12-slim）+ FastAPI + 原生 JS 前端 |
| 开发质量 | pytest（单测 + 集成）+ ruff（lint）+ 结构化日志 |

## 2. 系统流程

```text
用户问题（可选携带 history / client_time / 生成参数）
   │
   ▼
① 请求预处理 ──► 追问改写（多轮补全为自包含问题）→ 时效性锚定 → 查询改写（意图/多路/HyDE）
   │
   ▼
② 问答缓存检索 ──► 嵌入问题（qvec）→ 检索 webrag_qa 相似历史问题
   │ 命中（余弦 ≥ qa_min_score）
   ▼
③ 秒回：已存摘要 + 来源（cached=true，不联网不调 LLM；/ask/stream 打字机输出）
   │ 未命中
   ▼
④ 本地知识库检索 ──► webrag_offline_kb（dense+sparse 混合，不联网；use_web_search=false 时唯一检索源）
   │ 结果不足 且 use_web_search=true
   ▼
⑤ 联网兜底 ──► 搜索 API → 并行抓取（预算封顶）→ 清洗切块 → 嵌入 → 临时库 qa_<id> 混合检索
   ▼
⑥ 合并重排 ──► 本地 + 临时库结果统一 bge-reranker 精排（去重 + 噪声剔除）
   ▼
⑦ 生成 ──► 上下文拼装 → DeepSeek 生成（强制输出 [1][2] 引用）→ 幻觉检测 → 引用校验
   ▼
⑧ 缓存落库 ──► 存储「用户问题 + 摘要 + 来源」入 webrag_qa（best-effort）
   ▼
⑨ 输出 ──► 回答 + 数据源标注列表（URL 去重、按引用序号对应；/ask 整包 或 /ask/stream SSE）
```

**引用标注机制**：Prompt 中要求模型仅在给定上下文范围内回答，并以 `[序号]` 标注来源；后端将序号映射回 URL 列表随回答一起返回（越界引用剔除，宁可少引用不可错引用），前端渲染为可点击链接。

**降级策略**：`use_web_search=false` 且本地检索为空 → `EMPTY_RESULT`「信息不足」（不走 LLM 直答）；联网开启但检索为空 → LLM 直答兜底（`direct=true`，无来源，不入缓存）。任一下游失败（搜索 / 抓取 / LLM / 缓存写入）均不阻断整体进程，返回对应错误码或降级（见 docs/api.md §1.1）。

## 3. 目录结构

```text
webrag/
├── README.md                       # 项目总览
├── pyproject.toml + uv.lock + .python-version   # 依赖与版本锁（uv 统一管理）
├── .env.example                   # 密钥与连接信息模板（复制为 .env 填写）
├── docker-compose.yml             # 一键部署编排（Milvus + Redis + MySQL + 应用）
├── Dockerfile                     # 应用镜像（纯 CPU，uv sync --frozen）
├── config/settings.yaml           # 环境无关的可调参数（分块/检索/生成/超时等）
├── src/webrag/
│   ├── main.py                    # FastAPI 入口：/ask、/ask/stream、/health、/logs/stats
│   ├── accounts.py                # 账户系统（/auth/*：注册/登录/会话）
│   ├── chat_routes.py             # 聊天记录（/chat/*，MySQL 持久化）
│   ├── schemas/                   # 请求/响应数据模型（契约落点）
│   ├── crawler/                   # 搜索 API 适配层 + 抓取（限速/重试/robots/URL 去重）
│   ├── parser/                    # HTML 清洗与正文提取（trafilatura）
│   ├── chunker/                   # 切块（标题/段落感知 + 重叠 + 两级粒度可选）
│   ├── query_rewriter/            # 查询预处理：意图 / 多路改写 / HyDE / 追问改写 / 时效锚定
│   ├── embedder/                  # BGE-M3 向量化（dense + sparse）
│   ├── milvus_store/              # Milvus collection 管理 + 混合检索封装
│   ├── retriever/                 # 问答缓存 / 离线知识库 / 联网兜底 / 重排 / 缓存落库
│   ├── llm/                       # DeepSeek 调用 + Prompt 模板 + 引用解析
│   ├── hallucination_checker/     # 幻觉检测（逐句核验）
│   ├── feedback_store/            # 在线反馈收集
│   ├── logger/                    # 结构化日志（JSONL + 请求级指标，见 docs/logging.md）
│   └── admin/                     # 管理后台（登录 / 离线知识入库 / 文档管理）
├── static/                        # 前端：问答页 + 管理后台（原生 JS，无框架）
├── scripts/
│   ├── init_milvus.py             # 建库（幂等，webrag_qa）
│   ├── init_admin.py              # 创建管理员账号
│   └── test_query.py              # 命令行单测一条问题
├── tests/                         # pytest 单测 + 集成测试（15 个测试文件）
├── eval/                          # 评测集 qa_set.json + run_eval.py + reports/
└── docs/                          # 设计 / 接口 / 部署 / 快速开始 / 日志 / 变更记录
```

> `models/`（BGE-M3 / reranker，2GB+）与 `logs/`、`.env` 已被 .gitignore 排除，不入库；模型需自行下载放置（见 docs/quickstart.md）。

## 4. 快速开始

本地开发环境准备（详细命令见 [docs/quickstart.md](docs/quickstart.md)）：

```bash
# 1. 拉取代码 + 同步环境（uv 自动装 Python 3.11 + 全部依赖，含 CPU 版 torch）
git clone https://github.com/Derviol/webrag.git && cd webrag
uv sync

# 2. 配置密钥：复制模板并填入 DeepSeek / 搜索 API Key
cp .env.example .env

# 3. 启动基础设施（Milvus + Redis + MySQL；可选 --scale init=0 跳过自动建库）
docker compose up -d

# 4. 初始化（幂等可重跑）：建问答缓存 collection；创建管理员账号
uv run python scripts/init_milvus.py
uv run python scripts/init_admin.py --username admin --password <你的密码>

# 5. 启动服务
uv run uvicorn src.webrag.main:app --reload
```

打开 http://localhost:8000 → 注册/登录 → 开始问答；管理后台 http://localhost:8000/admin/（管理员账号登录后可上传离线知识文档）。

## 5. Docker 部署

`docker compose up -d` 一键启动全部服务（首次构建应用镜像约 5–15 分钟），详见 [docs/deploy.md](docs/deploy.md)：

| 服务 | 作用 |
| --- | --- |
| `etcd` / `minio` | Milvus 元数据 / 存储依赖 |
| `milvus` | 向量数据库（standalone，端口 19530；本机 Attu 2.x 可视化连接） |
| `redis` | URL 去重 / 登录限流（容器内走服务名 `redis:6379`） |
| `mysql` | 账户 / 聊天记录 / 后台文档（utf8mb4，named volume 持久化） |
| `webrag-app` | FastAPI 应用 + 前端（端口 8000，`restart: unless-stopped`） |
| `init` | 一次性建库任务（幂等，milvus healthy 后自动执行，Exited 0 即成功） |

验收：`curl http://localhost:8000/health` → `{"status":"ok","milvus":true,"embed_model":true}`。

## 6. 测试与质量

```bash
uv run pytest          # 全部测试（单测 + 集成，网络请求已 mock）
uv run ruff check      # lint（src + tests + scripts + eval 全绿）
uv run python scripts/test_query.py "你的问题"   # 命令行冒烟一条问题
uv run python eval/run_eval.py                   # 评测（Recall@k / MRR，见 eval/README.md）
```

- 测试清单见 [tests/README.md](tests/README.md)；改动后本地 `uv run pytest` + `ruff` 全绿再提交；
- 结构化日志事件 schema 见 [docs/logging.md](docs/logging.md)。

## 7. 开发与维护约定（单人 + Git）

- **分支**：`main` 为稳定版，功能开发在 `dev` 分支，合并后推送 GitHub（`origin`）；
- **提交**：`chore` / `feat` / `fix` / `docs` 前缀的简短提交信息；影响接口 / 行为变更的记录追加到 [docs/CHANGELOG.md](docs/CHANGELOG.md)；
- **环境**：依赖变更改 `pyproject.toml` → `uv lock` 更新 → 提交 pyproject + uv.lock（`uv.lock` 是唯一版本真相，禁手改）；
- **密钥**：一律走 `.env`（gitignore），绝不含在代码 / settings.yaml / 提交中；
- **模型**：BGE-M3 与 reranker 不入库，换机器时下载放置到 `models/`（路径见 .env.example）。

## 8. 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | 架构设计（链路 / 数据流 / 时延预算 / 关键决策） |
| [docs/api.md](docs/api.md) | 接口契约（权威：/ask、/ask/stream、/admin、/auth、/chat） |
| [docs/deploy.md](docs/deploy.md) | Docker 部署与运维（compose / 管理后台 / 常见问题） |
| [docs/quickstart.md](docs/quickstart.md) | 本地开发快速开始（环境 / 命令 / 端口速查） |
| [docs/logging.md](docs/logging.md) | 结构化日志事件 schema（JSONL 指标） |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 变更记录（开发全过程的决策与优化） |
| `src/webrag/*/README.md` | 各模块职责 / 接口约定 / 验收标准 |

> 项目开发于 2026-08 初至中旬（见 docs/CHANGELOG.md），单人全栈完成：从基础设施编排、检索链路（缓存优先 + 联网兜底）到管理后台、多轮对话与评测闭环。
