# WebRAG 接口契约

> 状态：使用中 ｜ 权威契约：本文件 ｜ 模块内速览见 schemas/README.md
> 变更流程：字段变更 = 破坏性变更，先同步 llm / retriever / 前端与 schemas/README.md，再改代码。

## 1. 对外接口（HTTP，FastAPI）

### 1.1 POST /ask

提交问题，返回带来源标注的回答。**需登录**：`Authorization: Bearer <token>`（/auth/login 签发，见 §1.5）；未登录/无效/过期 → 401 UNAUTHORIZED。

请求体：

```json
{
  "question": "2025 年大模型行业有哪些重要进展？",
  "temperature": 0.7
}
```

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| question | string | ✅ | 问题（1–2000 字符） |
| temperature | float | 可选 | 生成温度 **0–2**（DeepSeek 范围），控制回答随机性：越低越保守稳定、越高越发散；缺省取 settings.llm.temperature（默认 0.3）。命中问答缓存时不调模型，该参数不生效 |
| use_web_search | bool | 可选 | **联网搜索开关**（默认 **false**，opt-in）：仅当显式开启（true）时才允许联网搜索。false 时仅检索本地知识库（问答缓存 + 离线知识库 webrag_offline_kb），不联网；未查到内容返回 EMPTY_RESULT「信息不足」（不走 LLM 直答兜底） |
| web_top_n | int | 可选 | 联网搜索的**网页数量**（**1–20**）：控制搜索并抓取多少网页供参考；缺省取 settings.crawler.top_urls（默认 5）。仅 use_web_search=true 时生效；抓取页数仍受服务端时延预算（server.ask_timeout_seconds）封顶，超预算时按预算抓取 |
| client_time | string | 可选 | **前端宿主机本地时间**（ISO 8601，如 `2026-08-07T14:30:00+08:00`）：问题含「近日/近期/今天」等相对时间词时，作为时效性锚定的当前时间基准——联网搜索词拼入该日期、LLM Prompt 注入「时间基准」段；缺省或解析失败回落服务端本地时间 |
| history | array | 可选 | **多轮对话历史**（**当前问题之前**的消息，时间正序，每项 `{role: "user"\|"assistant", content: string}`，≤40 条，单条 ≤20000 字符）：服务端据此判断当前问题是否为**追问**（省略主语/指代前文对象等），若是则改写为自包含完整问题后再检索与生成（见 §2 第 6 条）。缺省为空——视为单轮提问，不触发追问改写 |

成功响应 200：

```json
{
  "answer": "2025 年大模型行业在……[1]……[2]……",
  "sources": [
    {"index": 1, "title": "xxx 官网", "url": "https://example.com/a"},
    {"index": 2, "title": "yyy 报道", "url": "https://example.com/b"}
  ],
  "direct": false,
  "cached": false
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| answer | string | 回答正文，引用以 [n] 标注 |
| sources | array | 数据源列表，index 从 1 起，与 [n] 一一对应 |
| sources[].index | int | 引用序号 |
| sources[].title | string | 网页标题 |
| sources[].url | string | 来源 URL |
| direct | bool | **兜底标记**：true = 联网检索无结果，LLM 直接作答（sources 为空、无引用，不入缓存）；默认 false |
| cached | bool | **问答缓存命中标记**：true = 命中历史问答缓存，answer/sources 为已存摘要+来源（未联网、未调 LLM）；默认 false |

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

### 1.3 POST /ask/stream（SSE 流式问答）

与 /ask 等价（同一检索 / 预算逻辑），但 LLM 生成阶段以 **SSE**（`text/event-stream`）
流式返回，前端可边收边渲染，无需等待完整回答。请求体同 §1.1（`{"question": "..."}`，支持 temperature 字段，缺省取 settings.llm.temperature）。
**需登录**：同 §1.1，`Authorization: Bearer <token>`；未登录 401（SSE 开始前返回 JSON 错误信封）。

事件流（`event:` + `data:` 行，事件间空行分隔）：

| 事件 | data | 时机 |
| --- | --- | --- |
| status | 阶段进度（string，如「正在检索历史问答缓存…」「正在检索本地知识库…」「正在联网搜索…」「正在生成回答…」） | 检索与生成阶段实时推送 |
| delta | 文本增量（string） | LLM 生成中逐段推送；**问答缓存命中时同样分块推送已存摘要**（打字机效果，data 拼接 == done.answer） |
| done | `{answer, sources, direct, cached}`（JSON，结构同 §1.1 成功响应） | 生成结束，最后一个事件 |
| error | `{code, message}`（JSON，错误码同 §1.1） | 任意阶段失败（检索 / 超时 / LLM） |

约定：

1. 检索阶段以 status 事件实时上报阶段进度（关键词检索 → 向量检索 → 精排 → 联网搜索 → 抓取/清洗嵌入 → 生成回答），首个事件为 status 而非 delta；
2. 各 delta 拼接结果 == done.answer（含 [n] 引用）；引用解析在服务端完成，随 done 下发 sources；
3. 失败一律以 error 事件收尾（SSE 响应已 200 开始，无法改状态码）；仅参数校验失败（422）返回 JSON 错误信封；
4. 响应头 `Cache-Control: no-cache`（禁止中间缓存）、`X-Accel-Buffering: no`（反代关缓冲）；
5. 旧 POST /ask（整包 JSON）保持不变，向后兼容。

### 1.2 GET /health

```json
{
  "status": "ok",
  "milvus": true,
  "embed_model": true,
  "embed_model_loaded": true,
  "llm_temperature": 0.3,
  "web_top_n": 5
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| status | string | 服务状态（ok） |
| milvus | bool | Milvus 可达（未启动为 false，不阻塞服务） |
| embed_model | bool | BGE-M3 本地目录就绪 |
| embed_model_loaded | bool | 模型预加载完成 |
| llm_temperature | float | 生成默认温度（settings.llm.temperature），前端滑杆初始值 |
| web_top_n | int | 联网搜索网页数量默认值（settings.crawler.top_urls），前端滑杆初始值 |

### 1.4 管理后台（离线知识入库，/admin/*）

> 独立子系统：**写入**路径（登录 / 入库 / 删除）只读写 **MySQL（webrag_admin）** 与
> **离线知识库（Milvus `webrag_offline_kb`）**；/ask 问答链路会**读取**离线知识库
> 作为本地检索源（use_web_search 开关关闭时唯一检索源），但管理与问答互不阻塞：
> 管理接口异常不影响 /ask，/ask 读取离线库失败自动降级为「信息不足」。
> 模块见 `src/webrag/admin/README.md`；入库链路复用 parser → chunker → embedder 公共管线。

**认证**：除 login 外均需 `Authorization: Bearer <token>`（login 签发，有效期
settings.admin.token_ttl_seconds，默认 12h）；无/无效/过期 token → 401。
**角色校验**：token 的 role 必须为 `admin`，普通用户（role=user）→ 403 FORBIDDEN。
前端直访 /admin/ 时先调 GET /admin/auth/me 判断用户组：admin 放行、普通用户拦截（API 层同样拦截）。

#### POST /admin/auth/login

请求：`{"username": "...", "password": "..."}`（username ≤64，password ≤128）。

成功 200：

```json
{"token": "<jwt>", "token_type": "bearer", "expires_in": 43200, "username": "admin", "role": "admin", "uid": "legacy_1"}
```

失败：401 UNAUTHORIZED（用户名或密码错误）；403 FORBIDDEN（账号非管理员）；429 TOO_MANY_ATTEMPTS
（15 分钟内同一用户名连续失败 ≥5 次，Redis 限流；Redis 不可用时自动放行）。

#### GET /admin/auth/me

当前后台登录用户信息（鉴权同其他 /admin 接口，role 必须为 admin）：

```json
{"username": "admin", "role": "admin", "uid": "legacy_1"}
```

普通用户 token → 403 FORBIDDEN；无 token → 401。前端直访 /admin/ 页面时先调本接口判断用户组。

#### POST /admin/documents（粘贴文本入库）

请求 JSON：`{"title": "可选，≤512", "content": "正文，非空"}`。

成功 202（入库在后台异步执行，立即返回）：

```json
{"id": 1, "status": "processing", "title": "...", "chunk_count": 0}
```

失败：422 VALIDATION_ERROR（空内容 / 超 `admin.max_chars_per_doc` 字符上限 / HTML 正文提取为空）。

#### POST /admin/documents/upload（文件入库）

multipart/form-data：`file`（.txt / .md / .html，≤ `admin.max_file_bytes` 默认 10MB）+ 可选 `title`。
HTML 经 parser（trafilatura）自动清洗正文；Markdown/纯文本原文入库。成功 202，失败 422。

#### GET /admin/documents

列表（按创建倒序，不含 content 原文）。查询参数：`limit`（默认 100，上限 500）、`offset`。

```json
{"documents": [{"id": 1, "title": "...", "source_type": "text|md|html", "file_name": "...",
  "status": "processing|done|failed", "error_message": "", "chunk_count": 12,
  "char_count": 3000, "created_at": "2026-08-06 10:00:00", "updated_at": "..."}], "total": 1}
```

#### GET /admin/documents/{id}

详情（含 `content` 清洗后原文与状态；入库期间前端轮询此接口直到 status ≠ processing）。404 NOT_FOUND。

#### DELETE /admin/documents/{id}

删除：先按 doc_ref 批量清除离线库知识块（Milvus 不可用 → 503 INTERNAL_ERROR，**记录保留**防孤儿块），
再删 MySQL 记录。成功 200：`{"status": "ok", "deleted_chunks": 5}`；404 文档不存在。

**新增错误码**（信封结构同 §1.1）：`UNAUTHORIZED`（401）/ `FORBIDDEN`（403，非管理员访问后台）/ `NOT_FOUND`（404）/ `TOO_MANY_ATTEMPTS`（429）。

**首次使用**：`docker compose exec webrag-app uv run --no-sync python scripts/init_admin.py --username admin --password <密码>`，
浏览器打开 `http://localhost:8000/admin/`。

### 1.5 账户系统（/auth/*）

> 前端「账户」模块登录系统：统一账户存 MySQL `users` 表（role: user/admin）。普通用户注册即得
> （role=user）；管理员由 scripts/init_admin.py 创建（role=admin）或经旧 admin_users 自动迁移。
> 密码 PBKDF2（同后台）、JWT HS256（密钥 ADMIN_JWT_SECRET），载荷 {sub, role, uid, iat, exp}，
> 有效期 settings.admin.token_ttl_seconds（默认 12h）。token 与后台共用（同一密钥），前端存 localStorage。

#### POST /auth/register

注册普通用户（role 固定 user）。请求：`{"username": "≤64", "password": "6-128"}`。
成功 200：`{"token", "token_type", "expires_in", "username", "role": "user", "uid"}`（自动登录）。
失败：409 USER_EXISTS（用户名已存在）；422 VALIDATION_ERROR（格式不合法）。

#### POST /auth/login

账户登录（普通用户 + 管理员均可）。请求：`{"username", "password"}`。
成功 200：同 register 的响应（role 按账号实际角色）。失败：401 UNAUTHORIZED（用户名或密码错误）。

#### GET /auth/me

会话校验（需 Bearer token）：返回 `{"username", "role", "uid"}`——前端启动时恢复会话、
判断用户组用。401：未登录/无效/过期。

### 1.6 聊天记录（/chat/*）

> 登录用户的会话历史，存 MySQL `chat_conversations`（uid 归属 + messages JSON 列，
> 前端左侧列表与服务端实时同步：登录拉取各自历史，删除同步删库）。全部接口需 Bearer token；
> 归属校验：查/改/删按 id + uid，他人会话一律 404。

| 接口 | 说明 |
| --- | --- |
| GET /chat/conversations | 当前用户会话列表（不含 messages，按更新时间倒序）：`{"conversations": [{id, title, created_at, updated_at}]}` |
| POST /chat/conversations | 创建会话（首问即建）：`{"title"?, "messages"?}` → 201 `{"conversation": {id, title, messages, created_at, updated_at}}`；title 缺省取首条用户消息前 30 字 |
| GET /chat/conversations/{id} | 会话详情（含 messages）；归属不符 404 |
| PUT /chat/conversations/{id} | 保存整段会话（回答完成后）：`{"title"?, "messages"}` → `{"status": "ok"}`；归属不符 404 |
| DELETE /chat/conversations/{id} | 删除会话（同步删库）；归属不符 404 |

## 2. 引用标注规范

1. 模型仅依据给定上下文作答，上下文片段按 [1]..[k] 编号，Prompt 要求以 [n] 引用；
2. 后端解析 answer 中的 [n]：n 必须在 sources 范围内，否则剔除该标记（宁可少引用，不可错引用）；
3. 同一 URL 多次出现时合并为一个 source，保留第一次出现的 index；
4. 问答链路为「缓存优先 + 本地知识库 + 联网兜底」（见 architecture.md §6）：先检索问答缓存（webrag_qa），
   Top-1 相似度 ≥ settings.retriever.qa_min_score 时命中，直接返回历史摘要 + 来源（cached=true，
   不联网、不调 LLM）；未命中检索离线知识库（webrag_offline_kb，本地，不联网）；
   请求 use_web_search=true 时再叠加联网搜索 → 临时库检索 → 重排（两路结果合并统一重排），
   完成后把「用户问题 + 摘要 + 来源」写入问答缓存（best-effort，失败不影响本次回答）；
5. use_web_search=false（关闭联网搜索）时检索为空 → EMPTY_RESULT「信息不足」（本地知识库未查到内容，
   不走 LLM 直答兜底）；use_web_search=true 时联网检索为空走 **LLM 直答兜底**（direct=true，无 sources，
   **不入问答缓存**）；兜底关闭（settings.retriever.enable_llm_direct=false）或直答失败时返回
   EMPTY_RESULT / LLM_FAILED，不要求模型硬答。
6. **追问改写（多轮对话）**：请求携带 `history`（且 query_rewriter.enable_followup 开启）时，先判定当前问题
   是否为追问——规则预筛（指代/承接词、超短问）→ LLM 一次调用判定 + 改写（输出
   `{"is_followup", "rewritten"}`）；判定为追问时用改写后的**自包含完整问题**替代原始问题，贯穿 改写管线 /
   问答缓存检索 / 本地与联网检索 / 生成 / 缓存落库（缓存键也用改写后问题：同义追问可命中）；metrics 与日志
   保留原始问题供追溯。改写失败、判定非追问或未携带 history → 用原始问题，行为与单轮一致（不阻断主链路）。

## 3. 模块间接口（内部契约）

| 模块 | 方法 | 输入 | 输出 |
| --- | --- | --- | --- |
| crawler | search(query, top_n, provider, api_key) | str | list[SearchHit{title,url,snippet}] |
| crawler | fetch(url, timeout_seconds, delay_seconds) | str | str（HTML） |
| parser | parse(html, url) | str, str | Document{title,text,publish_time,url} |
| chunker | chunk(doc, chunk_size, overlap, respect_paragraph) | Document | list[Chunk] |
| embedder | embed(texts) | list[str] | EmbedResult{dense, sparse}，dense dim=1024 |
| milvus_store | create_collection(name) | str | None |
| milvus_store | create_qa_collection(name) | str | None（问答缓存 collection：question → 摘要 + 来源） |
| milvus_store | create_offline_collection(name) | str | None（离线知识库 collection：KB schema + doc_ref，管理后台入库用） |
| milvus_store | add(collection, chunks, vectors) | str, list[Chunk], EmbedResult | int（写入数） |
| milvus_store | add_offline(collection, chunks, vectors, doc_ref) | str, list[Chunk], EmbedResult, str | int（写入数，额外记 doc_ref 文档归属） |
| milvus_store | add_qa(collection, questions, summaries, sources_json, vectors) | str, list[str], list[str], list[str], EmbedResult | int（写入数） |
| milvus_store | delete_by_doc_ref(collection, doc_ref) | str, str | int（按文档删除知识块数，管理后台删除文档用） |
| milvus_store | search(collection, vectors, top_k) | str, EmbedResult, int | list[SearchResult]（dense+sparse 混合，临时库用） |
| milvus_store | search_qa(collection, vectors, top_k) | str, EmbedResult, int | list[QAHit]（dense-only COSINE，问答缓存用） |
| milvus_store | drop_collection(name) | str | None |
| retriever | lookup_qa_cache(question, store, embedder, collection, settings, progress?) | str, … | (QAHit\|None, EmbedResult)（问答缓存命中判定 + 问题向量复用） |
| retriever | retrieve_offline(question, store, embedder, settings, top_k, qvec?, progress?, rewrite_result?) | str, … | list[SearchResult]（本地知识库检索：离线库 webrag_offline_kb，dense+sparse 混合，不联网；未建库/异常返回空） |
| retriever | retrieve_web(question, store, embedder, settings, top_k, qvec?, deadline?, progress?, web_top_n?) | str, … | list[SearchResult]（联网兜底：搜索→抓取→切块→临时库检索；web_top_n：请求级网页数量 1–20，缺省 settings.crawler.top_urls，抓取页数受 deadline 预算封顶） |
| retriever | save_qa_record(question, response, store, embedder, collection, settings, qvec?) | str, AskResponse, … | bool（best-effort 写入问答缓存；无来源不入库） |
| retriever | rerank(question, results, settings) | str, list[SearchResult], … | list[SearchResult]（bge-reranker 精排，阈值剔除噪声） |
| llm | generate(question, contexts) | str, list[Chunk] | str（answer 正文，含 [n] 引用） |
| llm | generate_direct(question) | str | str（无上下文直答，兜底用；不确定时明说） |
| llm | build_response(answer, contexts) | str, list[Chunk] | AskResponse{answer, sources, direct}（引用解析 + 校验） |
| query_rewriter | rewrite_followup(question, history, llm_client, max_history?, max_chars?) | str, list, … | FollowupResult{is_followup, rewritten, skipped}（追问检测 + 改写：判定是否依赖历史，改写为自包含完整问题；失败降级原文，不抛异常） |

> 带默认参数的接口（crawler.search / fetch、chunker.chunk）：参数可配置，默认值与 config/settings.yaml 对应，调用方按显式传参。

## 4. 数据模型（草案）

| 模型 | 字段 |
| --- | --- |
| AskRequest | question: str, temperature: float（可选，0–2；缺省用 settings.llm.temperature，控制生成随机性）, use_web_search: bool（可选，默认 false；true=允许联网搜索，未命中本地库继续联网兜底；false=仅检索本地知识库，未查到返回信息不足）, web_top_n: int（可选，1–20；缺省 settings.crawler.top_urls，联网搜索的网页数量，仅 use_web_search=true 生效）, client_time: string（可选；前端宿主机本地时间 ISO 8601，时效性问题的时间基准，缺省回落服务端）, history: list[ChatMessage]（可选，≤40 条；多轮对话历史，服务端据此判定追问并改写，见 §2 第 6 条） |
| ChatMessage | role: str（user\|assistant）, content: str（≤20000 字符）（多轮对话历史单条消息，随 AskRequest.history 传入） |
| AskResponse | answer: str, sources: list[Source], direct: bool（默认 false）, cached: bool（默认 false，问答缓存命中标记） |
| Source | index: int, title: str, url: str |
| Chunk | text: str, metadata: ChunkMetadata |
| ChunkMetadata | url, title, publish_time, seq |
| EmbedResult | dense: list[list[float]]（dim=1024）, sparse: list[dict]（{token_id: weight}） |
| SearchHit | title, url, snippet |
| SearchResult | chunk: Chunk, score: float |
| QAHit | question: str, summary: str, sources: list[Source], score: float（问答缓存命中，见 retriever.lookup_qa_cache） |
| Document | title, text, publish_time, url |

## 5. 变更流程

1. 在 docs/CHANGELOG.md 记录变更点与影响模块；
2. 同步更新本文件与 schemas/README.md；
3. 修改代码并跑通测试（`uv run pytest` + `uv run ruff check` 全绿）后提交 Git。
