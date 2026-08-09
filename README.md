# WebRAG — 联网检索增强问答系统

> **状态：执行阶段（3-4 天，全量任务）** 架构设计见 docs/architecture.md，接口契约见 docs/api.md；Resilio Sync 协作（§4）与 Docker 部署（§5）已确定。

## 1. 项目简介

一个**面向联网信息的 RAG（检索增强生成）问答系统**：

- 用户输入自然语言问题；
- 系统先检索**历史问答缓存**（相似问题直接复用已存摘要 + 来源，秒回）；
- 未命中时自动检索相关**网页**，抓取并清洗内容，交给大模型生成**带数据源标注**的回答；
- 每个回答都会附上引用来源（URL），保证可溯源；回答成功后存入问答缓存，越问越快；
- 当前周期：**3-4 天交付全量功能**，架构与排期见 docs/architecture.md。

**技术栈**

| 组件 | 选型 |
| --- | --- |
| 向量数据库 | Milvus（客户端 pymilvus 已装；最终以 Docker Compose 部署 standalone） |
| Embedding 模型 | BGE-M3（BAAI/bge-m3，dense + sparse 双向量） |
| LLM | DeepSeek（deepseek-chat / deepseek-reasoner，OpenAI 兼容接口） |
| 网页采集 | 搜索引擎 API（Bing / Tavily / 博查）+ 自研爬虫（requests + trafilatura） |
| 缓存 / 限流 | Redis（URL 去重、API 限流；Docker 与 Milvus 一同部署） |
| 重排 | bge-reranker-v2-m3 |
| 服务框架 | Python 3.11（uv 统一管理环境），FastAPI + Web 页面（输入框/回答/来源列表） |
| 协作与部署 | Resilio Sync 同步 + 成员本地开发（见 §4）；Docker Compose 部署（见 §5） |
| 评测 | 自建 QA 评测集 + 检索指标（Recall@k、MRR）+ 生成质量人工评估 |

## 2. 系统流程

```text
用户问题
   │
   ▼
① 问答缓存检索 ──► 嵌入问题，到 webrag_qa 检索相似历史问题
   │ 命中（相似度 ≥ qa_min_score）
   ▼
② 返回已存摘要 + 来源（cached=true，秒回，不联网不调 LLM）
   │ 未命中
   ▼
③ 网页检索 ──► 搜索引擎 API / 爬虫抓取候选网页
   ▼
④ 清洗切块 ──► HTML 清洗 → 正文提取 → 分块（标题/段落感知）
   ▼
⑤ 向量化 ──► BGE-M3 生成 dense + sparse 向量 → 临时库检索 → 重排
   ▼
⑥ 生成 ──► 上下文拼装 → DeepSeek 生成回答（强制输出 [1][2] 引用标记）
   ▼
⑦ 缓存落库 ──► 存储「用户问题 + 摘要 + 来源」入 webrag_qa（best-effort）
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
│   ├── crawler/              # ③ 搜索与抓取
│   ├── parser/               # ④ 清洗与正文提取
│   ├── chunker/              # ④ 切块策略
│   ├── embedder/             # ⑤ BGE-M3 向量化
│   ├── milvus_store/         # ⑥ Milvus collection 管理与写入
│   ├── retriever/            # ⑦ 检索 + 重排
│   └── llm/                  # ⑧ DeepSeek 调用 + Prompt 模板 + 引用解析
├── scripts/
│   ├── init_milvus.py        # 建 collection、索引
│   └── test_query.py         # 命令行单测一条问题
├── tests/                    # 单元 / 集成测试
├── eval/                     # 评测集与评测脚本
└── docs/                     # 设计文档、接口文档
```

> 每个模块目录内含 README.md（如 `src/webrag/crawler/README.md`），写明该模块的职责、负责人、接口约定与验收标准。

## 4. 协作规范（Resilio Sync + 模块文件夹，无 Git）

> ⚡ 团队成员先看根目录 **TEAM_GUIDE.md**（30 秒速览），细节在本节。

团队用 **Resilio Sync** 实时同步项目主文件夹（本目录）。成员**直接在同步夹内、只改动自己负责的模块目录**（§7 分工即目录边界），保存即同步到全队。

**目录边界（§7 分工映射）**

| 成员 | 只动这些目录 |
| --- | --- |
| #2 爬虫（2 人） | src/webrag/crawler/ |
| #3 清洗切块（2 人） | src/webrag/parser/、src/webrag/chunker/ |
| #4 Embedding | src/webrag/embedder/ |
| #5 向量库 | src/webrag/milvus_store/ |
| #6 检索（2 人） | src/webrag/retriever/ |
| #7 LLM | src/webrag/llm/ |
| #8 后端/前端 | src/webrag/main.py、src/webrag/schemas/、static/ |
| #9 测试评测 | tests/、eval/ |
| #1 总负责 | 其余一切（docs/、config/、scripts/、pyproject 等） |

**共享文件 owner 制**（不属于任何单一模块；他人需改时先记 CHANGELOG / 与 owner 打招呼，由 owner 动手或确认）

| 文件 | owner | 说明 |
| --- | --- | --- |
| src/webrag/main.py（/ask 组装） | #8 | 集成多模块输出 |
| src/webrag/schemas/models.py | #8 维护 / 各模块提需求 | 契约落点，字段变更走 api.md §5 |
| src/webrag/schemas/__init__.py | #8 | 与 models.py 同步 |
| pyproject.toml + uv.lock | #1 独改 | 依赖变更统一收敛，防各人各加各的包 |
| config/settings.yaml | #1 | 参数集中管理 |
| docs/api.md | #1 定版 | 契约权威，变更人提需求 |
| docs/CHANGELOG.md | 变更人自记 | 协作记录（无 Git 即历史） |
| scripts/test_query.py | #1 | 联调脚本，牵动全链路 |

**协作纪律**

- 模块代码：在自己目录里随便改，保存即同步；**自测通过才算完成**（uv run pytest / ruff）；
- 共享文件：只有 owner 能改；改动前确认无人正在编辑；
- 半成品警示：保存即同步 = 未完成代码即刻全网可见，改完未自测前不要依赖别人的结果；
- 冲突：两人同时改同一文件 → Resilio 生成 `xxx (conflicted copy).py`，协商保留一份、删除其余；
- 备份：无版本历史，总负责每天下班对主文件夹做整体 zip 快照（至少保留 3 份），出问题用快照回滚。

**忽略清单（Resilio 客户端 → 文件夹设置 → 忽略列表）**

| 模式 | 原因 |
| --- | --- |
| `.venv/`、`__pycache__/`、`*.pyc` | 机器特定 / 缓存，同步即坏 |
| `.reasonix/`、`.idea/`、`.vscode/` | 工具 / IDE 本机特定 |
| `*.bak`、`*.orig`、`*.rej`、`Thumbs.db`、`desktop.ini`、`.DS_Store` | 备份 / 系统噪音 |

**密钥（.env）**

- 全量同步下 .env 会同步给所有成员（API Key 全队可见）；内部项目可接受则保持现状（总负责统一维护）；
- 需保密时在忽略列表加 `.env`，成员各自复制 `.env.example` 填写。

**模型分发**

- BGE-M3（2GB+）首次由 #4 下载到 models/，Resilio 局域网分发给全队（省 10 次重复下载）；models/ 默认参与同步。

**变更记录**

- 影响多人协作的决策与接口变更在 `docs/CHANGELOG.md` 追加记录；
- 契约/接口字段变更必须同步更新 api.md 与相关模块 README。

## 5. Docker 部署（基础设施已落盘，应用容器 M4）

**目标架构**：基础设施（Milvus + Redis）已由根目录 `docker-compose.yml` 编排；本机 Attu 2.x 与本地 uv 应用通过 `localhost:19530` 连接 Milvus；webrag-app 应用容器（Dockerfile）M4 完成，随 `docker compose up -d` 一键启动。

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
| `webrag-app` | 由本项目构建（M4 完成） | FastAPI 服务：爬虫、BGE-M3 嵌入、DeepSeek 调用 |

**部署步骤**（详细命令见 docs/deploy.md）：

1. 项目根目录执行 `docker compose up -d` —— 一键启动 Milvus（+etcd/minio）、Redis、webrag-app 应用容器，并自动建库（init 一次性任务，详见 docs/deploy.md）；
2. 本机 Attu 连接 `http://localhost:19530` 验证；`curl http://localhost:8000/health` 应显示 milvus: true；
3. 打开 http://localhost:8000 开始问答。

## 6. 快速开始（规划）

本地开发环境准备（详细命令见 docs/quickstart.md）：

1. 通过 Resilio 同步主文件夹到本机，执行 `uv sync`（自动下载 Python 3.11、创建 .venv 并安装全部依赖，含 CPU 版 torch）；
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
| 6 | 检索链路 | 2 | 问答缓存检索、联网兜底检索、重排、相关度调优、缓存阈值（qa_min_score）调优 | retriever 模块、检索评测报告 | src/webrag/retriever/ |  |
| 7 | LLM 接入 | 1 | DeepSeek 调用封装、Prompt 设计、引用标注与解析 | llm 模块、Prompt 版本库 | src/webrag/llm/ |  |
| 8 | 后端 API / 前端 | 1 | FastAPI 接口、引用渲染、Web 页面（前端规格见 static/README.md） | main.py、前端页面 | src/webrag/、schemas/、static/ |  |
| 9 | 测试 / 评测 / 汇报 | 1 | 评测集建设、指标计算、端到端回归 | eval 目录、评测报告 | tests/、eval/ |  |

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

- 检索策略：问答缓存优先——向量库存「问题 → 摘要 + 来源」，命中直接复用（未命中走联网兜底）
- 向量与检索：BGE-M3 dense + sparse 混合检索 + bge-reranker 重排
- 基础设施：Milvus standalone + Redis 由 Docker Compose 一同部署（§5）
- 协作与部署：Resilio Sync 同步协作（§4）、Docker Compose（§5）

**待确认**

- [ ] 项目正式名称（当前为占位名 WebRAG）
- [ ] 搜索引擎 API 选型（预算 / 免费额度；代码留适配层）
- [ ] 预建知识库首批主题范围（D2 前确定，供 ingest 脚本跑数据）
