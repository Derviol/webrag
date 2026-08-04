# WebRAG — 联网检索增强问答系统

> **状态：执行阶段（3-4 天，全量任务）** 架构设计见 docs/architecture.md，接口契约见 docs/api.md；Git 分支协作（§4）与 Docker 部署（§5）已确定。

## 1. 项目简介

一个**面向联网信息的 RAG（检索增强生成）问答系统**：

- 用户输入自然语言问题；
- 系统自动检索相关**网页**，抓取并清洗内容，切块后向量化入库；
- 检索命中的片段作为上下文送入大模型，生成**带数据源标注**的回答；
- 每个回答都会附上引用来源（URL），保证可溯源；
- 当前周期：**3-4 天交付全量功能**，架构与排期见 docs/architecture.md。

**技术栈**

| 组件 | 选型 |
| --- | --- |
| 向量数据库 | Milvus（客户端 pymilvus 已装；最终以 Docker Compose 部署 standalone） |
| Embedding 模型 | BGE-M3（BAAI/bge-m3，dense + sparse 双向量） |
| LLM | DeepSeek（deepseek-chat / deepseek-reasoner，OpenAI 兼容接口） |
| 网页采集 | 搜索引擎 API（Bing / Tavily / 博查）+ 自研爬虫（requests + trafilatura） |
| 缓存 / 限流 | Redis（URL 去重、搜索缓存、API 限流；Docker 与 Milvus 一同部署） |
| 重排 | bge-reranker-v2-m3 |
| 服务框架 | Python 3.11（uv 统一管理环境），FastAPI + Web 页面（输入框/回答/来源列表） |
| 协作与部署 | Git 分支协作（见 §4）；Docker Compose 部署（见 §5） |
| 评测 | 自建 QA 评测集 + 检索指标（Recall@k、MRR）+ 生成质量人工评估 |

## 2. 系统流程

```text
用户问题
   │
   ▼
① 查询分析 ──► 提取检索词、判断是否触发联网检索
   ▼
② 网页检索 ──► 搜索引擎 API / 爬虫抓取候选网页
   ▼
③ 清洗切块 ──► HTML 清洗 → 正文提取 → 分块（标题/段落感知）
   ▼
④ 向量化 ──► BGE-M3 生成 dense + sparse 向量
   ▼
⑤ 入库 ──► Milvus：预建知识库 collection + 问答临时 collection，写入元数据（URL、标题、时间）
   ▼
⑥ 检索 ──► 查询向量 Top-k 检索 + bge-reranker 重排
   ▼
⑦ 生成 ──► 上下文拼装 → DeepSeek 生成回答（强制输出 [1][2] 引用标记）
   ▼
⑧ 输出 ──► 回答 + 数据源标注列表（URL 去重、按引用序号对应）
```

**引用标注机制**：Prompt 中要求模型仅在给定上下文范围内回答，并以 `[序号]` 标注来源；后端将序号映射回 URL 列表随回答一起返回，前端渲染为可点击链接。

## 3. 目录结构（计划）

```text
web-rag/
├── README.md
├── pyproject.toml + uv.lock + .python-version  # 依赖清单 / 版本锁 / 解释器版本（uv 统一管理）
├── .env.example              # API Key、Milvus 连接等配置模板
├── config/
│   └── settings.yaml         # 分块参数、检索 Top-k、模型名等
├── static/                   # 前端页面（#8 交付）
├── src/webrag/
│   ├── main.py               # FastAPI 入口（/ask、/health）
│   ├── schemas/              # 请求/响应数据模型
│   ├── crawler/              # ② 搜索与抓取
│   ├── parser/               # ③ 清洗与正文提取
│   ├── chunker/              # ③ 切块策略
│   ├── embedder/             # ④ BGE-M3 向量化
│   ├── milvus_store/         # ⑤ Milvus collection 管理与写入
│   ├── retriever/            # ⑥ 检索 + 重排
│   └── llm/                  # ⑦ DeepSeek 调用 + Prompt 模板 + 引用解析
├── scripts/
│   ├── init_milvus.py        # 建 collection、索引
│   ├── ingest.py             # 预建知识库：批量抓取入库
│   └── test_query.py         # 命令行单测一条问题
├── tests/                    # 单元 / 集成测试
├── eval/                     # 评测集与评测脚本
└── docs/                     # 设计文档、接口文档
```

> 每个模块目录内含 README.md（如 `src/webrag/crawler/README.md`），写明该模块的职责、负责人、接口约定与验收标准。

## 4. Git 协作规范

协作方式：**Git 分支协作**（11 人并行开发），托管平台 **GitHub**（见 §9）。无外网环境时可用共享目录 bare 仓库兜底。

**分支模型**

| 分支 | 用途 | 合并规则 |
| --- | --- | --- |
| `main` | 稳定可部署版本 | 仅接受 `dev` 的合并，发布时打 tag |
| `dev` | 每日集成分支 | 各 feature 分支合并至此，测试通过才可合并 |
| `feature/<模块>-<描述>` | 个人开发分支，如 `feature/crawler-robots` | 自测通过后发 PR，至少 1 人 Review |

**协作约定**

- 分支命名：功能 `feature/模块-描述`，修复 `fix/描述`；
- 提交信息用约定式提交：`feat:` / `fix:` / `docs:` / `refactor:` / `test:`；
- 合入 `dev` 前置条件：本地测试通过（`uv run pytest`）+ lint 通过（`uv run ruff check`）+ PR 至少 1 人 Review；
- 每天至少 push / pull 一次 `dev`，缩小冲突面；冲突由相关模块负责人协商解决；
- 密钥与本地环境只放 `.env`（已 gitignore）；依赖版本提交 `uv.lock`，成员 `uv sync` 还原环境；
- BGE-M3 模型（2GB+，gitignore 的 models/）不入库：各人首次自行下载，或从共享目录拷贝一次；
- 每周五 `dev` → `main` 合并并打 tag，保证周末始终有可演示版本。

**首次接入（局域网 bare 仓库场景）**

1. 总负责在一台机器（或共享目录）创建 bare 仓库：`git init --bare web-rag.git`；
2. 各成员 `git clone <路径>`（局域网路径或 `file://` 共享路径）；
3. 本机 `uv sync` + 复制 `.env.example` 为 `.env`。

**重大决策记录**

- 契约/接口字段变更等影响多人协作的决策，除提交信息外，在 `docs/CHANGELOG.md` 追加记录（非强制，便于跨分支留痕）。

## 5. Docker 部署（基础设施已落盘，应用容器 M4）

**目标架构**：基础设施（Milvus + Redis）已由根目录 `docker-compose.yml` 编排；本机 Attu 2.x 与本地 uv 应用通过 `localhost:19530` 连接 Milvus；webrag-app 应用容器（Dockerfile）M4 落地。

```text
本机 Attu (GUI) ─┐
                 ├──> localhost:19530 ──> milvus-standalone (Docker)
uv 应用 / scripts─┘                            │
                                              ├─ etcd (元数据)
                                              └─ minio (存储)
uv 应用 ──> localhost:6379 ──> redis (Docker，缓存/限流)
```

- Milvus standalone 依赖 etcd（元数据）与 minio（存储），三者均由官方镜像提供，数据挂 named volume；
- 本机 Attu 2.x 连接 `http://localhost:19530` 浏览 collection 与索引（详见 docs/deploy.md）；
- webrag-app 应用容器（Dockerfile）M4 阶段加入编排。

**服务编排**（根目录 `docker-compose.yml` 已落盘）：

| 服务 | 镜像 | 作用 |
| --- | --- | --- |
| `etcd` | quay.io/coreos/etcd | Milvus 元数据存储 |
| `minio` | minio/minio | 向量与日志数据持久化（挂 volume） |
| `milvus` | milvusdb/milvus:v2.4.24（standalone 模式） | 向量数据库本体 |
| `redis` | redis:7-alpine | 缓存（URL 去重 / 搜索缓存）与限流，挂 volume 持久化 |
| `webrag-app` | 由本项目构建（M4 加入） | FastAPI 服务：爬虫、BGE-M3 嵌入、DeepSeek 调用 |

**部署步骤**（详细命令见 docs/deploy.md）：

1. 项目根目录执行 `docker compose up -d`，启动 Milvus（+etcd/minio）与 Redis；
2. `uv run python scripts/init_milvus.py` 创建 collection 与索引；
3. 本机 Attu 连接 `http://localhost:19530` 验证；应用 `/health` 应显示 milvus: true。

## 6. 快速开始（规划）

本地开发环境准备（详细命令见 docs/quickstart.md）：

1. 克隆仓库，安装 uv 后执行 `uv sync`（自动下载 Python 3.11、创建 .venv 并安装全部依赖，含 torch；详见 docs/quickstart.md）；
2. 复制 `.env.example` 为 `.env`，填入 DeepSeek、搜索服务 API Key 与 Milvus 连接地址；
3. `docker compose up -d` 启动 Milvus+Redis，再运行初始化脚本创建 Milvus collection 与索引；
4. 启动 FastAPI 服务，调用 `/ask` 接口提交问题，即可获得带来源标注的回答。

部署环境无需本地安装依赖，直接用 Docker 一键启动（见 §5）。

## 7. 团队分工（11 人建议）

**总负责**：任务拆解、进度跟进、架构与接口契约定版、config 维护、Docker 编排（docs/deploy.md）、每日按 §8 出口标准验收。

| # | 角色 | 人数 | 职责 | 主要交付物 | 对应目录 | 详情 |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 爬虫开发 | 2 | 搜索 API 对接、抓取调度、反爬与限速、robots 合规、URL 去重（Redis） | 采集模块、搜索适配层 | src/webrag/crawler/ |  |
| 3 | 数据清洗 / 切块 | 2 | HTML 清洗、正文提取、切块策略调优（A 清洗 / B 切块） | parser + chunker 模块、分块评测 | src/webrag/parser/、chunker/ |  |
| 4 | Embedding 服务 | 1 | BGE-M3 部署与调用封装、向量批量生成 | embedder 模块、批处理脚本 | src/webrag/embedder/ |  |
| 5 | 向量库开发 | 1 | Milvus collection 设计、索引与写入、元数据管理 | milvus_store 模块、建库脚本 | src/webrag/milvus_store/、scripts/ |  |
| 6 | 检索链路 | 2 | 查询分析、Top-k 检索、重排、相关度调优、搜索缓存（Redis） | retriever 模块、检索评测报告 | src/webrag/retriever/ |  |
| 7 | LLM 接入 | 1 | DeepSeek 调用封装、Prompt 设计、引用标注与解析 | llm 模块、Prompt 版本库 | src/webrag/llm/ |  |
| 8 | 后端 API / 前端 | 1 | FastAPI 接口、引用渲染、Web 页面 | main.py、前端页面 | src/webrag/、schemas/、static/ |  |
| 9 | 测试 / 评测 | 1 | 评测集建设、指标计算、端到端回归 | eval 目录、评测报告 | tests/、eval/ |  |

> 角色编号与各模块 README 的负责人标注一致（#1 = 总负责/架构）。**并行关键路径**：①②③④⑤ 为链路前置，⑥⑦⑧ 依赖前置完成后联调，⑨ 全程介入；任务吃紧时从「检索链路」或「前后端」抽人支援。

## 8. 里程碑

| 天 | 内容 | 出口标准 |
| --- | --- | --- |
| D1 | 骨架 + 搜索/爬虫 + 清洗切块 + Milvus schema 对齐（dense+sparse）+ BGE-M3 下载 | 单条 URL 产出规范 Chunk；Milvus 建库成功；模型就绪 |
| D2 | 查询分析 + dense/sparse 嵌入 + 混合检索 + DeepSeek 生成，主链路跑通；预建库 ingest 脚本 | 一条问题能返回基于网页内容的回答（预建库或临时抓取均可） |
| D3 | 重排接入 + 引用标注校验 + Web 页面 + 知识库扩充 + 联调 | /ask 返回 answer + sources，前端可点击溯源 |
| D4 | 测试 + 评测 + Docker 部署 + 文档归档 | `docker compose up -d` 一键启动；README/api/architecture 齐全 |

> 全量功能（查询分析、dense+sparse 双向量、重排、预建库 + 临时库双策略）均在交付范围内，按核心路径优先排期，详见 docs/architecture.md。

## 9. 待确认事项

**已确定（不阻塞开发）**

- 检索策略：双库并行——预建知识库（ingest 批量入库）+ 问答临时抓取
- 向量与检索：BGE-M3 dense + sparse 混合检索 + bge-reranker 重排
- 基础设施：Milvus standalone + Redis 由 Docker Compose 一同部署（§5）
- 协作与部署：Git 分支协作（§4）、Docker Compose（§5）
- Git 托管平台：**GitHub**（CI 可选接 GitHub Actions，M4 阶段）

**待确认**

- [ ] 项目正式名称（当前为占位名 WebRAG）
- [ ] 搜索引擎 API 选型（预算 / 免费额度；代码留适配层）
- [ ] 预建知识库首批主题范围（D2 前确定，供 ingest 脚本跑数据）
