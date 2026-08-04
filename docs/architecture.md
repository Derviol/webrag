# WebRAG 架构设计（全量）

> 状态：初稿 v0.2 ｜ 周期：3-4 天 ｜ 范围：**全量功能，不裁剪**；按核心路径优先排期
> 与 README §2 流程、docs/api.md 契约、各模块 README 保持一致；变更需同步。

## 1. 设计原则

- **全量交付**：查询分析、dense+sparse 双向量、重排、预建知识库 + 临时抓取双策略、完整 Web 页面均在范围内；
- **核心路径优先**：D1-D2 先跑通 8 步主链路，D3-D4 补齐增强功能（重排、知识库扩充、前端打磨）；
- **接口先行**：模块按 docs/api.md 契约并行开发，D1 对齐 schema（含 dense+sparse 字段），避免返工；
- **引用可溯源**：回答的每个 `[n]` 必须能在 sources 中对应到 URL，不允许幽灵引用。

## 2. 功能范围（全量）

| 功能 | 说明 | 计划阶段 |
| --- | --- | --- |
| 查询分析 | 检索词提取、改写/扩写、是否触发联网检索的判断 | D2 |
| 网页检索 | 搜索引擎 API（Bing / Tavily / 博查，适配层）+ 自研爬虫（限速、重试、robots） | D1 |
| 清洗切块 | HTML 清洗、正文提取、标题/段落感知分块、相邻重叠 | D1 |
| 向量化 | BGE-M3 **dense + sparse 双向量**（一次前向输出） | D2 |
| 入库-预建库 | scripts/ingest.py 按主题批量抓取入库，长期复用 | D2-D3 |
| 入库-临时库 | 问答时抓取 top N 入库（qa_<id>），用后即清 | D2 |
| 检索 | Top-k 混合检索（dense + sparse 加权融合） | D2 |
| 重排 | bge-reranker-v2-m3 精排 | D3 |
| 生成 | DeepSeek deepseek-chat（默认）/ deepseek-reasoner（可选） | D2-D3 |
| 引用标注 | [n] 序号 + sources 映射 + 后端校验（剔除幽灵引用） | D3 |
| 前端 | Web 页面：输入框、回答展示、可点击来源列表 | D3 |
| 部署 | Docker Compose 一键部署（app + Milvus standalone + etcd + minio） | D4 |

## 3. 总体架构

```text
浏览器（Web 页面：输入框 + 回答 + 来源列表）
   │  /ask /health
   ▼
FastAPI 应用（src/webrag/main.py）——链路组装层
   │
   ├── query_analyzer  查询分析：检索词 / 联网判断 / 改写（在 retriever 内）
   ├── crawler         搜索 API + 抓取（限速、重试、robots）
   ├── parser/chunker  清洗 + 切块
   ├── embedder        BGE-M3：dense（dim=1024）+ sparse
   ├── milvus_store    预建知识库 collection（长期）+ 临时 qa_<id>（即清）
   ├── redis           缓存（URL 去重 / 搜索缓存 / API 限流）
   ├── retriever       Top-k 混合检索 + bge-reranker 重排
   └── llm             DeepSeek 生成 + 引用解析
```

## 4. 数据流

**离线链路（预建知识库）**

1. scripts/ingest.py 按主题批量抓取网页；
2. 清洗切块 → BGE-M3 嵌入（dense + sparse）→ 写入知识库 collection（元数据：URL、标题、时间）；
3. 增量更新：按 URL 去重，重复内容跳过。

**在线链路（问答，每次实时执行）**

1. 查询分析：提取检索词、判断是否触发联网检索；
2. 检索知识库 collection，Top-k 召回候选；
3. 候选不足或实时性要求高 → 联网抓取 top N 网页 → 临时 collection → 检索；
4. 混合分数融合（dense + sparse）→ bge-reranker 重排 → 截断；
5. 上下文按 [1][2] 编号拼装 → DeepSeek 生成；
6. 后端解析引用序号 → sources 列表 → 返回；临时 collection 异步清理。

**时延预算（目标：联网场景 ≤30s，知识库命中 ≤10s）**

| 环节 | 预算 |
| --- | --- |
| 查询分析 | <0.1s |
| 搜索 API | 0.5-2s |
| 并行抓取 top 5 | 2-10s |
| 清洗切块 | <1s |
| 嵌入（约 50 块，CPU） | 5-20s |
| Milvus 混合检索 | <1s |
| bge-reranker 重排（Top-k 内） | 0.5-2s |
| DeepSeek 生成 | 2-10s |

## 5. 模块职责与依赖

| 模块 | 职责 | 依赖 | 详见 |
| --- | --- | --- | --- |
| crawler | 搜索 API 适配层 + 抓取（限速/重试/robots） | 无 | crawler/README.md |
| parser / chunker | HTML 清洗 + 标题/段落感知切块 | crawler | parser/、chunker/README.md |
| embedder | BGE-M3 嵌入（dense + sparse） | 无 | embedder/README.md |
| milvus_store | 知识库 + 临时 collection 管理、混合检索封装 | embedder（维度对齐） | milvus_store/README.md |
| retriever | 查询分析、Top-k 混合检索、重排 | milvus_store、embedder | retriever/README.md |
| llm | DeepSeek 生成（chat/reasoner）+ 引用解析 | retriever | llm/README.md |
| main.py | 链路组装 + HTTP + 前端静态托管 | 全部 | src/webrag/README.md |

## 6. 关键设计决策

- **双库策略**：预建知识库（长期复用、覆盖稳定主题）+ 临时抓取（实时性兜底），由查询分析决定检索路径，可两者混合（知识库为主、联网补充）；
- **dense + sparse 混合检索**：BGE-M3 一次前向同时输出稠密与稀疏向量，Milvus 同一 collection 建两个向量字段，用 weighted reranker 融合分数（需 Milvus ≥ 2.4 / pymilvus 对应版本）；
- **重排在范围内**：bge-reranker-v2-m3 对 Top-k 精排后再截断送入 LLM，降低上下文噪声；
- **schema 先行**：D1 由 milvus_store 定死字段名、dense 维度（1024）与 sparse 配置，embedder 对齐，防止 D2 返工；
- **Redis 基础设施**：URL 去重缓存（爬虫避免重复抓取）、搜索结果缓存（同问秒回）、API 限流；与 Milvus 一同由 Docker Compose 编排（redis:7，AOF 持久化）。
- **部署形态**：基础设施（Milvus standalone + etcd + minio + Redis）由根目录 `docker-compose.yml` 编排，本机 Attu 2.x 连接 `localhost:19530` 可视化运维；webrag-app 应用容器 M4 落地。
- **引用校验后置**：Prompt 约束为主、后端校验兜底——解析出的 `[n]` 不在 sources 范围则剔除（宁可少引用，不可错引用）。

## 7. 非功能要求

- 单次问答端到端：联网场景 ≤30s、知识库命中 ≤10s；超时返回明确错误码（见 api.md）；
- `/ask` 的 sources 中 URL 必须真实存在于检索上下文；
- 失败隔离：搜索 / 抓取 / LLM 任一失败不影响整体进程，返回对应错误码；
- 预建知识库可增量更新，重复 URL 不重复入库。

## 8. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 全量功能 × 4 天周期 | 高并行度：5 条工作流并行（见 §9）；核心路径 D2 晚前可跑，增强功能按计划补齐 |
| sparse 依赖 Milvus 版本 | D1 验证客户端版本兼容性；若不兼容先 dense 联调、sparse 紧随其后接入（功能不减，仅调落地顺序） |
| 目标站反爬 / 慢 | 限速 + 超时 + 失败重试一次；候选 URL 限 top 5 |
| BGE-M3 首次下载 2GB+ 耗时 | D1 上午即启动下载；Docker 挂 volume 缓存 |
| CPU 嵌入慢拖垮时延 | 控制每页最大块数、并行嵌入；必要时换 GPU 机器 |
| 模型乱编号 / 幽灵引用 | Prompt 约束 + 后端校验剔除（见 §6） |
| 搜索 API Key 未定 | 先用免费额度（Tavily / 博查 / Bing 试用），crawler 留适配层 |
| 多人并行改同一契约 | 契约以 docs/api.md 为准，字段变更走 PR 同步（README §4） |

## 9. 4 天排期（11 名成员 + 总负责）

> 分工：11 人（不含总负责）。#3 增至 2 人（A 清洗 / B 切块）。

| 组 | 成员（分工表 #） | D1 | D2 | D3 | D4 |
| --- | --- | --- | --- | --- | --- |
| 链路前置 | #2 ×2、#3 ×2 | 搜索 + 抓取 + 清洗切块 | 预建库 ingest 脚本 + 首批数据 | 知识库扩充 | 修 bug / 回归 |
| 向量与检索 | #4、#5、#6 ×2 | schema 对齐（dense+sparse）、模型下载 | dense+sparse 嵌入 + 混合检索 | 重排接入 + 调优 | 回归 |
| 生成 | #7 | Prompt 初版 | DeepSeek 接入（chat） | 引用校验 + reasoner 可选 | 收尾 |
| 应用壳 | 总负责、#8 | 接口骨架、配置、Docker 编排 | 链路联调 | Web 页面 | 完成 |
| 测试评测 | #9 | 评测集初版 | 端到端冒烟 | 指标采集（含重排前后对比） | 评测报告 |
