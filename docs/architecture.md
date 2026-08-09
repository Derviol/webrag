# WebRAG 架构设计（全量）

> 状态：v0.3（问答缓存优先重构）｜ 周期：3-4 天 ｜ 范围：**全量功能，不裁剪**；按核心路径优先排期
> 与 README §2 流程、docs/api.md 契约、各模块 README 保持一致；变更需同步。

## 1. 设计原则

- **全量交付**：问答缓存优先（相似问题直接复用历史回答）、联网检索兜底、dense+sparse 双向量、重排、完整 Web 页面均在范围内；
- **核心路径优先**：D1-D2 先跑通问答链路，D3-D4 补齐增强功能（重排、缓存调优、前端打磨）；
- **接口先行**：模块按 docs/api.md 契约并行开发，D1 对齐 schema（含 dense+sparse 字段），避免返工；
- **引用可溯源**：回答的每个 `[n]` 必须能在 sources 中对应到 URL，不允许幽灵引用；
- **缓存不阻断**：问答缓存命中直接返回（秒回）；缓存不可用 / 未命中一律降级联网检索，绝不阻断服务。

## 2. 功能范围（全量）

| 功能 | 说明 | 计划阶段 |
| --- | --- | --- |
| 问答缓存 | 向量库存「用户问题 → 摘要 + 来源」，/ask 先按问题向量相似度检索，命中（≥ qa_min_score）直接返回历史摘要 | D2-D3 |
| 网页检索 | 搜索引擎 API（Bing / Tavily / 博查，适配层）+ 自研爬虫（限速、重试、robots） | D1 |
| 清洗切块 | HTML 清洗、正文提取、标题/段落感知分块、相邻重叠 | D1 |
| 向量化 | BGE-M3 **dense + sparse 双向量**（一次前向输出） | D2 |
| 临时库检索 | 联网兜底时抓取 top N 入库（qa_<id>），检索后即清 | D2 |
| 检索 | **问答缓存优先**：命中直返；未命中 → 联网搜索 → 临时库检索 | D2 |
| 重排 | bge-reranker-v2-m3 精排（联网兜底链路） | D3 |
| 生成 | DeepSeek deepseek-chat（默认）/ deepseek-reasoner（可选）；**联网检索为空时 LLM 直答兜底**（无来源，不入缓存） | D2-D3 |
| 缓存落库 | 生成完成后把「用户问题 + 摘要 + 来源」写入问答缓存（best-effort） | D2-D3 |
| 引用标注 | [n] 序号 + sources 映射 + 后端校验（剔除幽灵引用） | D3 |
| 前端 | Web 页面：输入框、回答展示、可点击来源列表、缓存命中提示 | D3 |
| 部署 | Docker Compose 一键部署（app + Milvus standalone + etcd + minio） | D4 |

## 3. 总体架构

```text
浏览器（Web 页面：输入框 + 回答 + 来源列表 + 缓存命中提示）
   │  /ask /health
   ▼
FastAPI 应用（src/webrag/main.py）——链路组装层
   │
   ├── retriever      问答缓存优先检索（lookup_qa_cache）+ 本地知识库检索（retrieve_offline）
                    + 联网兜底（retrieve_web）+ 缓存落库（save_qa_record）
   ├── crawler        搜索 API + 抓取（限速、重试、robots）
   ├── parser/chunker 清洗 + 切块
   ├── embedder       BGE-M3：dense（dim=1024）+ sparse
   ├── milvus_store   问答缓存 collection（webrag_qa：question → 摘要 + 来源）+ 临时 qa_<id>（即清）
   ├── redis          URL 去重 / API 限流（搜索缓存已随旧三级级联移除）
   └── llm            DeepSeek 生成（含直答兜底）+ 引用解析
```

## 4. 数据流

**在线链路（问答，每次实时执行）**

1. **问答缓存检索**：嵌入用户问题（BGE-M3 dense）→ 检索 webrag_qa 相似历史问题；
   Top-1 分数 ≥ `qa_min_score` → 命中，直接返回历史摘要 + 来源（cached=true，不联网、不调 LLM）；
2. **本地知识库检索**（未命中）：查离线知识库 webrag_offline_kb（dense+sparse 混合，不联网）；
   请求 use_web_search=false（关闭联网搜索）时是唯一检索源；
3. **联网兜底**（未命中且 use_web_search=true）：搜索 API 取 top N 候选 → 并行抓取 → 清洗切块 → 嵌入
   （dense + sparse，块数受 max_web_chunks_total 封顶）→ 临时 collection（qa_<id>）混合检索；
4. **重排**：bge-reranker 对本地知识库 + 临时库**合并结果**精排（rerank_min_score 剔除噪声，截断到 rerank_top_n）；
5. 上下文按 [1][2] 编号拼装 → DeepSeek 生成 → 后端解析引用序号 → sources 列表；临时 collection 即清；
6. **缓存落库**：把「用户问题 + 摘要 + 来源」写入 webrag_qa（best-effort，失败仅告警，不影响本次回答）；
7. 检索最终为空：use_web_search=false → EMPTY_RESULT「信息不足」（不走 LLM 直答兜底）；use_web_search=true →
   **LLM 直答兜底**（direct=true，无 sources，**不入缓存**）；兜底关闭时返回 EMPTY_RESULT。

**查询向量复用**：缓存检索已嵌入的问题向量（qvec）直接复用于临时库检索，单次问答只嵌入一次问题（省 ~8s CPU 嵌入）。

**时延预算（目标：问答缓存命中 ≤3s 秒回；联网场景 ≤ ask_timeout_seconds=105s，超出返回 TIMEOUT）**

| 环节 | 预算 |
| --- | --- |
| 问答缓存检索（嵌入 + Milvus dense） | 1-3s（CPU 嵌入 ~1.5s/问） |
| 搜索 API | 0.5-2s |
| 并行抓取 top 5 | 2-10s（最坏受 request_timeout 约束） |
| 清洗切块 | <1s |
| 嵌入（≤24 块，CPU，max_web_chunks_total） | ≤36s（仅联网兜底链路发生） |
| Milvus 临时库检索 | <1s |
| bge-reranker 重排（Top-k 内） | 0.5-2s |
| DeepSeek 生成 | 2-10s（超时按预算剩余收敛） |
| 缓存落库（嵌入 + 写入） | 1-3s（best-effort，失败不阻断） |

## 5. 模块职责与依赖

| 模块 | 职责 | 依赖 | 详见 |
| --- | --- | --- | --- |
| crawler | 搜索 API 适配层 + 抓取（限速/重试/robots） | 无 | crawler/README.md |
| parser / chunker | HTML 清洗 + 标题/段落感知切块 | crawler | parser/、chunker/README.md |
| embedder | BGE-M3 嵌入（dense + sparse） | 无 | embedder/README.md |
| milvus_store | 问答缓存 collection（webrag_qa）+ 临时 collection 管理、检索封装 | embedder（维度对齐） | milvus_store/README.md |
| retriever | 问答缓存检索（lookup_qa_cache）、本地知识库检索（retrieve_offline）、联网兜底（retrieve_web）、缓存落库（save_qa_record）、重排 | milvus_store、embedder | retriever/README.md |
| llm | DeepSeek 生成（chat/reasoner + 直答兜底）+ 引用解析 | retriever | llm/README.md |
| main.py | 链路组装 + HTTP + 前端静态托管 | 全部 | src/webrag/README.md |

## 6. 关键设计决策

- **问答缓存优先（替代旧三级级联知识库检索）**：向量库不再存网页片段、按「预建知识库 + 临时抓取」双库策略检索，
  改为存**问题 → 摘要 + 来源**的问答缓存：① 相似问题（余弦 ≥ qa_min_score）直接复用历史回答，不联网、不调 LLM，
  同问 / 换说法问秒回且答案一致可溯源；② 未命中才走联网兜底全链路；③ 命中阈值宁高勿低（缓存返回整段历史摘要，
  误命中代价高于漏命中——漏命中只是多花一次联网）；
- **联网搜索开关（use_web_search）**：前端显式开关控制是否允许联网——开启时本地知识库（离线 webrag_offline_kb）
  未命中继续联网兜底；关闭时仅检索本地（问答缓存 + 离线知识库），未查到内容返回「信息不足」（不走 LLM 直答）；
  内网 / 隐私场景可完全离线作答，且回答仍可溯源到已入库文档；
- **dense 单向量判命中**：缓存检索只走 question_vec（COSINE），不做 sparse 融合——问题相似度是语义层的事，
  稀疏词法匹配对「换说法」的问题命中无益；
- **联网链路时延硬约束**：CPU 嵌入 ~1.5s/块，5 页×12 块=60 块≈90s 会超前端超时——
  ① 总嵌入块数封顶 max_web_chunks_total（默认 24 ≈36s）；② /ask 整体预算
  server.ask_timeout_seconds（默认 105s）：retriever 接收 deadline，预算将尽时停止
  嵌入新页、跳过联网；剩余 <10s 时 main 直接返回 TIMEOUT（干净收尾，不再挂死到
  前端 200s 客户端超时）；③ LLM 调用超时按预算剩余收敛；
- **重排在联网兜底链路内**：bge-reranker-v2-m3 对临时库 Top-k 精排后再截断送入 LLM，降低上下文噪声；
- **schema 先行**：D1 由 milvus_store 定死字段名、dense 维度（1024）与 sparse 配置，embedder 对齐，防止 D2 返工；
- **Redis 基础设施**：URL 去重缓存（爬虫避免重复抓取）、API 限流；与 Milvus 一同由 Docker Compose 编排（redis:7，AOF 持久化）；
- **缓存写入 best-effort**：save_qa_record 失败仅告警，绝不影响本次回答；直答兜底（无来源）不入缓存；
- **部署形态**：基础设施（Milvus standalone + etcd + minio + Redis）由根目录 `docker-compose.yml` 编排，
  本机 Attu 2.x 连接 `localhost:19530` 可视化运维；webrag-app 应用容器 M4 落地。
- **引用校验后置**：Prompt 约束为主、后端校验兜底——解析出的 `[n]` 不在 sources 范围则剔除（宁可少引用，不可错引用）。

## 7. 非功能要求

- 单次问答端到端：问答缓存命中 ≤3s、联网场景 ≤105s；超时返回明确错误码（见 api.md）；
- `/ask` 的 sources 中 URL 必须真实存在于检索上下文（缓存命中时为历史存储的来源）；
- 失败隔离：搜索 / 抓取 / LLM / 缓存写入任一失败不影响整体进程，返回对应错误码或降级；
- 问答缓存自动积累：每次联网回答成功即落库，相似问题随使用越来越快；
- 可观测性：全链路结构化日志（JSONL `logs/app.log`，含耗时/命中率/token/首token 指标），事件 schema 见 docs/logging.md。

## 8. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 缓存误命中（相似但不同的问题） | qa_min_score 阈值宁高勿低，纳入评测调参（eval）；命中返回历史摘要并标注 cached |
| 缓存新鲜度 | 实时性问题（今天/最新/新闻等）如需强制联网，可对 enable_qa_cache 做按问开关或调高阈值（后续迭代） |
| sparse 依赖 Milvus 版本 | D1 验证客户端版本兼容性；若不兼容先 dense 联调、sparse 紧随其后接入（功能不减，仅调落地顺序） |
| 目标站反爬 / 慢 | 限速 + 超时 + 失败重试一次；候选 URL 限 top 5 |
| BGE-M3 首次下载 2GB+ 耗时 | D1 上午即启动下载；Docker 挂 volume 缓存 |
| CPU 嵌入慢拖垮时延 | 控制每页最大块数、复用查询向量（项目已定 CPU-only，放弃 GPU 加速） |
| 模型乱编号 / 幽灵引用 | Prompt 约束 + 后端校验剔除（见 §6） |
| 搜索 API Key 未定 | 先用免费额度（Tavily / 博查 / Bing 试用），crawler 留适配层 |
| 多人并行改同一契约 | 契约以 docs/api.md 为准，字段变更走 CHANGELOG 记录（README §4） |

## 9. 4 天排期（11 名成员 + 总负责）

> 分工：11 人（不含总负责）。#3 增至 2 人（A 清洗 / B 切块）。

| 组 | 成员（分工表 #） | D1 | D2 | D3 | D4 |
| --- | --- | --- | --- | --- | --- |
| 链路前置 | #2 ×2、#3 ×2 | 搜索 + 抓取 + 清洗切块 | 联网链路联调 | 抓取质量调优 | 修 bug / 回归 |
| 向量与检索 | #4、#5、#6 ×2 | schema 对齐（dense+sparse）、模型下载 | 问答缓存 + 联网检索跑通 | 缓存阈值调优（qa_min_score） | 回归 |
| 生成 | #7 | Prompt 初版 | DeepSeek 接入（chat） | 引用校验 + reasoner 可选 | 收尾 |
| 应用壳 | 总负责、#8 | 接口骨架、配置、Docker 编排 | 链路联调 | Web 页面 | 完成 |
| 测试评测 | #9 | 评测集初版 | 端到端冒烟 | 缓存命中率 / 误命中评测 | 评测报告 |
