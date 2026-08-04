# WebRAG 接口契约

> 状态：初稿 v0.2 ｜ 权威契约：本文件 ｜ 模块内速览见 schemas/README.md
> 变更流程：字段变更 = 破坏性变更，先同步 llm / retriever / 前端与 schemas/README.md，再改代码。

## 1. 对外接口（HTTP，FastAPI）

### 1.1 POST /ask

提交问题，返回带来源标注的回答。

请求体：

```json
{
  "question": "2025 年大模型行业有哪些重要进展？"
}
```

成功响应 200：

```json
{
  "answer": "2025 年大模型行业在……[1]……[2]……",
  "sources": [
    {"index": 1, "title": "xxx 官网", "url": "https://example.com/a"},
    {"index": 2, "title": "yyy 报道", "url": "https://example.com/b"}
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| answer | string | 回答正文，引用以 [n] 标注 |
| sources | array | 数据源列表，index 从 1 起，与 [n] 一一对应 |
| sources[].index | int | 引用序号 |
| sources[].title | string | 网页标题 |
| sources[].url | string | 来源 URL |

失败响应（统一结构）：

```json
{
  "error": {"code": "TIMEOUT", "message": "抓取网页超时"}
}
```

| code | 含义 |
| --- | --- |
| VALIDATION_ERROR | 请求参数不合法 |
| SEARCH_FAILED | 搜索 API 调用失败 / 无候选结果 |
| TIMEOUT | 抓取或 LLM 超时 |
| LLM_FAILED | DeepSeek 调用失败 |
| EMPTY_RESULT | 检索无结果，无法作答 |
| INTERNAL_ERROR | 其他内部错误 |

### 1.2 GET /health

```json
{"status": "ok", "milvus": true, "embed_model": true}
```

## 2. 引用标注规范

1. 模型仅依据给定上下文作答，上下文片段按 [1]..[k] 编号，Prompt 要求以 [n] 引用；
2. 后端解析 answer 中的 [n]：n 必须在 sources 范围内，否则剔除该标记（宁可少引用，不可错引用）；
3. 同一 URL 多次出现时合并为一个 source，保留第一次出现的 index；
4. 检索无结果时返回 EMPTY_RESULT，不要求模型硬答。

## 3. 模块间接口（内部契约）

| 模块 | 方法 | 输入 | 输出 |
| --- | --- | --- | --- |
| crawler | search(query, top_n, provider, api_key) | str | list[SearchHit{title,url,snippet}] |
| crawler | fetch(url, timeout_seconds, delay_seconds) | str | str（HTML） |
| parser | parse(html, url) | str, str | Document{title,text,publish_time,url} |
| chunker | chunk(doc, chunk_size, overlap, respect_paragraph) | Document | list[Chunk] |
| embedder | embed(texts) | list[str] | EmbedResult{dense, sparse}，dense dim=1024 |
| milvus_store | create_collection(name) | str | None |
| milvus_store | add(collection, chunks, vectors) | str, list[Chunk], EmbedResult | int（写入数） |
| milvus_store | search(collection, vectors, top_k) | str, EmbedResult, int | list[SearchResult] |
| milvus_store | drop_collection(name) | str | None |
| retriever | analyze_query(question) | str | QueryPlan（dict：检索词 / 是否联网 / 知识库优先等，字段待 #6 定） |
| retriever | retrieve(question, collection) | str, str | list[SearchResult]（重排后按序编号） |
| llm | generate(question, contexts) | str, list[Chunk] | str（answer 正文，含 [n] 引用） |
| llm | build_response(answer, contexts) | str, list[Chunk] | AskResponse{answer, sources}（引用解析 + 校验） |

> 带默认参数的接口（crawler.search / fetch、chunker.chunk）：参数可配置，默认值与 config/settings.yaml 对应，调用方按显式传参。

## 4. 数据模型（草案）

| 模型 | 字段 |
| --- | --- |
| AskRequest | question: str |
| AskResponse | answer: str, sources: list[Source] |
| Source | index: int, title: str, url: str |
| Chunk | text: str, metadata: ChunkMetadata |
| ChunkMetadata | url, title, publish_time, seq |
| EmbedResult | dense: list[list[float]]（dim=1024）, sparse: list[dict]（{token_id: weight}） |
| QueryPlan | 字段待 #6 定（检索词 / 是否联网 / 知识库优先） |
| SearchHit | title, url, snippet |
| SearchResult | chunk: Chunk, score: float |
| Document | title, text, publish_time, url |

## 5. 变更流程

1. 提出方在 PR 中说明变更点与影响模块；
2. 同步更新本文件与 schemas/README.md；
3. llm / retriever / 前端 / milvus_store 联调通过后合入。
