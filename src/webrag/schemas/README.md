# schemas — 数据契约

> 权威契约文档：docs/api.md；本文件为模块内速览，字段以 api.md 为准。

## 职责

- 定义模块间与对外的数据模型：AskRequest、AskResponse、Chunk、SearchResult 等；
- 重点定义**引用标注格式**：回答中的 `[1][2]` 序号 ↔ sources 列表（URL、标题）的映射规则。

## 所属角色

- 初版由项目协调 / 架构（#1）定义；
- 由后端 API / 前端（#8）维护，所有模块必须遵守。

## 关键契约（草案）

| 模型 | 字段 |
| --- | --- |
| AskRequest | question（必填）、temperature（float，可选 0–2；缺省用 settings.llm.temperature，控制生成随机性）、use_web_search（bool，可选，默认 false；仅显式开启才允许联网搜索，否则只检索本地知识库：问答缓存 + 离线库，未查到返回信息不足）、web_top_n（int，可选 1–20；缺省 settings.crawler.top_urls，联网搜索的网页数量，仅 use_web_search=true 生效）、client_time（string，可选；前端宿主机本地时间 ISO 8601，时效性问题「近日/近期/今天」的时间基准，缺省回落服务端本地时间）、history（list[ChatMessage]，可选，≤40 条；当前问题之前的多轮对话消息，服务端据此判定追问并改写为自包含问题后再检索生成，缺省空=单轮提问不触发） |
| ChatMessage | role（user\|assistant）、content（≤20000 字符）——多轮对话历史单条消息 |
| AskResponse | answer、sources[{index, title, url}]、direct（bool，默认 false；true=LLM 直答兜底）、cached（bool，默认 false；true=问答缓存命中） |
| Chunk | text、metadata{url, title, publish_time, seq} |
| SearchResult | chunk、score |
| QAHit | question、summary、sources[{index, title, url}]、score（问答缓存命中，retriever.lookup_qa_cache 返回） |

## 约定

- 字段变更 = 破坏性变更，先同步 llm / retriever / 前端，再改代码；
- 新增字段保持向后兼容。
