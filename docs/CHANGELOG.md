# 变更记录

> 单人开发 + Git 维护（main/dev，GitHub: Derviol/webrag）。记录开发全过程的变更与决策
> （契约定版、功能迭代、性能优化、部署收口）。历史条目中的多角色署名（#N / Reasonix）
> 均为作者本人推进开发时的记录角色，统一由一人完成。
>
> 格式：`MM-DD | 变更人 | 变更 | 影响模块 | 备注`

## 2026-08-08

| 日期 | 改动人 | 变更 | 影响模块 | 自测 |
| --- | --- | --- | --- | --- |
| 08-08 | Reasonix | **文档体系重构（单人维护 + Git）**：根 README 重写为单人开发 + Git 工作流（去掉 11 人分工 / Resilio 协作 / 里程碑规划）；quickstart 改 git clone 流程；architecture 状态改已交付、删除 4 天排期、补齐功能清单；deploy / api / config / 各模块 README 去除角色归属与协作引用；删除 TEAM_GUIDE.md（团队速览已不适用）；本文件头改为 Git 记录说明 | 全部（文档） | ✅ git 提交推送
| 08-08 | Reasonix | **追问检测与改写（多轮对话补全）**：① AskRequest 新增可选 history（当前问题之前的消息，{role: user\|assistant, content}，≤40 条），前端 app.jsx/app.js 每次提问携带上一轮完整历史（buildPayload 归一化：滤 loading/streaming、取最近 20 条）；② query_rewriter 新增追问改写（needs_followup_llm 规则预筛：指代/承接词或超短问才调 LLM，其余跳过省调用；LLM 一次调用判定+改写输出 {is_followup, rewritten}；失败/非法响应降级原文）——判定为追问时改写后的自包含完整问题替代原文贯穿 改写管线/缓存检索/本地与联网检索/生成/缓存落库（缓存键也用改写后问题，同义追问可命中）；③ 配置新增 query_rewriter.enable_followup/followup_max_history(6)/followup_max_chars(3000)；④ RequestMetrics 新增 followup 标志（评测追问效果）；⑤ 无 history 或改写失败行为与单轮一致（不阻断）；新增 20 条单测 + 6 条链路集成测试；api.md §1.1/§2/§3/§4、schemas/config README 同步；app.jsx 重编译验证 app.js | schemas / query_rewriter / main / config / logger / static / tests / docs | ✅ ruff / pytest 57 通过 / Babel 编译 / node --check |
| 08-08 | Reasonix | **登录系统 + 聊天记录 MySQL 化**：① 统一账户表 users（role: user/admin，旧 admin_users 自动迁移为 role=admin，现有管理员不丢）；② 新增 /auth/register ·/auth/login ·/auth/me（JWT 载荷含 role/uid，与后台共用密钥）；③ /ask 与 /ask/stream 强制登录（未登录 401）；④ 聊天记录存 MySQL chat_conversations（uid 归属 + messages JSON），新增 /chat/conversations CRUD（归属校验，删除同步删库）；⑤ 前端左侧会话列表改服务端同步 + 每条 hover 删除按钮；侧边栏底部「管理后台」改「账户」模块（登录/注册/退出），管理员登录后顶部新增「后台管理」入口；⑥ /admin/* 角色校验（非 admin 403）+ GET /admin/auth/me + 控制台前端直访判断用户组（普通用户无权限页）；⑦ init_admin.py 建管理员 role=admin；api.md §1.1/§1.3/§1.4 更新 + 新增 §1.5/§1.6；app.jsx 重编译 app.js | admin / accounts（新）/ chat_routes（新）/ main / config / scripts / static / tests / docs | ✅ ruff / pytest / Babel 编译 |

## 2026-08-07

| 日期 | 改动人 | 变更 | 影响模块 | 自测 |
| --- | --- | --- | --- | --- |
| 08-07 | Reasonix | **时效性查询锚定（近日/近期命中率优化，时间取前端宿主本地时间）**：① 前端 api.js 每次请求恒附带 client_time（宿主机器本地时间，ISO 8601 带时区偏移，`buildPayload` 内取 `new Date()` 实时生成）→ AskRequest 新增可选 client_time；② query_rewriter 检测相对时间词（近日/近期/最新/今天/本月/今年…）后做时效性锚定——RewriteResult 新增 time_aware/time_context，时间文本以客户端时间为准（`fromisoformat` 解析，**不做时区转换**，保留用户本地日期），缺省/非法回落服务端本地时间（`datetime.now(timezone.utc).astimezone()`，DTZ005 合规）；③ retriever.retrieve_web 联网搜索词拼入当前日期（「近日股市」→「近日股市（2026年8月7日）」，搜索引擎更易命中近期内容）；时间锚定查询并入多路改写列表参与临时库检索；④ llm generate/generate_direct/stream_generate/stream_generate_direct 新增可选 time_context，命中时效性问题时 Prompt 插入「时间基准」段；main /ask 与 /ask/stream 透传 client_time→rewrite→LLM；非时效性问题行为逐字不变；⑤ 新增 10 条单测（时间词检测 / 客户端时间解析与回落 / 改写注入 / 非时效不改写 / 搜索词锚定 ×2 / API 透传 client_time→time_context）；api.md §1.1、schemas README 同步；api.js 版本号 bump 防浏览器旧缓存 | query_rewriter / retriever / llm / main / schemas / static / tests / docs | ✅ ruff / pytest |
| 08-07 | Reasonix | **前端可显式选择联网搜索网页数量（1–20）**：AskRequest 新增可选 web_top_n（1–20，缺省 settings.crawler.top_urls=5，仅 use_web_search=true 生效）；retriever.retrieve_web 新增 web_top_n 参数——搜索 top_n 与抓取页数上限按请求级覆盖（仍受 deadline 预算封顶），缺省回落配置；/health 新增 web_top_n 下发默认值（settings.crawler.top_urls）；前端「生成参数」新增「搜索网页数量」滑杆（1–20、步进 1、数值实时显示、默认读 /health、随 /ask 与 /ask/stream 透传、请求中禁用、「联网搜索」关闭时整组置灰禁用）；api.md §1.1/§1.2/§3/§4（顺带修正 §4 中 use_web_search 默认值笔误为 false）、schemas README、static README 同步；新增 5 条单测（/ask 透传 / 缺省不传 / 越界 0·21 返回 422 / /health 默认值 / retriever 按 web_top_n 搜索并封顶 20） | schemas / retriever / main / static / tests / docs | ✅ ruff（本次改动文件全绿）/ pytest |
| 08-07 | Reasonix | **前端联网搜索开关 + 离线知识库接入问答链路**：AskRequest 新增 use_web_search（默认 true）——开启时本地知识库未命中继续联网兜底；关闭时仅检索本地（问答缓存 webrag_qa + 离线知识库 webrag_offline_kb），未查到返回 EMPTY_RESULT「信息不足」（不走 LLM 直答兜底）；retriever 新增 retrieve_offline（dense+sparse 混合 / qvec 复用 / 意图动态权重 / 未建库与异常降级空）；_retrieve_for 改为离线+联网结果合并统一重排，联网失败但离线有结果时降级作答；前端左侧「生成参数」栏新增「联网搜索」switch（随请求透传、请求中禁用、状态文案联动）+ 关闭时初始提示文案 + EMPTY_RESULT 文案改「信息不足」+ offline:// 来源非链接展示（标注「本地知识库文档」）；api.md §1.1/§1.3/§1.4/§2/§3/§4、architecture.md §3/§4/§5/§6、schemas/static README 同步；顺带修复 retriever 未使用的 rewrite_query import（F401）与两处 import 排版（I001）；新增 10 条单测 | schemas / retriever / main / static / tests / docs | ✅ ruff（本次改动文件全绿）/ pytest 232 通过（3 条 Milvus 集成跳过） |
| 08-07 | Reasonix | **存量 ruff 报错清零**：修复 5 个文件 21 条既有 lint（未动业务逻辑）——feedback_store 移除未用 json/os import、损坏行跳过补日志（S112）、export 时间戳改 timezone.utc（DTZ005，与 save_feedback 一致）；test_config 移除未用 load_settings/os/PROJECT_ROOT import 与多余空行；test_query_rewriter 移除未用 MagicMock；test_p2_eval_feedback 5 处 import 排序/去重；test_hallucination_checker 补 risk 断言（RUF059 变有用） | feedback_store / tests | ✅ ruff src+tests+scripts 全绿 / pytest 232 通过（3 跳过） |
| 08-07 | Reasonix | **联网搜索开关默认改为关闭（opt-in）**：用户反馈「未勾选联网仍联网搜索」——根因是开关默认开启（checked/默认 true），未显式操作即联网。改为：① AskRequest.use_web_search 默认 false（仅显式开启才允许联网，API 客户端同理 fail-safe）；② 前端开关默认不勾选（状态文案「关闭」）；③ eval Live 模式 /ask 请求显式 use_web_search=true 保持原语义；④ test_api 联网链路用例全部显式传 true，新增「不传字段默认不联网」用例；⑤ api.md/schemas README/static README 默认值同步；顺带清理 eval/run_eval.py 4 条既有 lint（F401 math、F541 ×3） | schemas / static / eval / tests / docs | ✅ ruff src+tests+scripts+eval 全绿 / pytest 233 通过（3 跳过）/ 容器重建后 openapi 默认 false |

## 2026-08-06

| 日期 | 改动人 | 变更 | 影响模块 | 自测 |
| --- | --- | --- | --- | --- |
| 08-06 | Reasonix | **全 Docker 部署收口**：docker-compose 全部常驻服务（etcd/minio/milvus/redis/mysql/webrag-app）加 `restart: unless-stopped`（Docker Desktop 重启后自动恢复，根治容器全灭问题）；redis 宿主端口 6379→6380（本机 6379 被原生 Redis 占用，容器内应用走服务名 redis:6379 不受影响）；镜像构建成功（uv sync --frozen，torch 2.6.0+cpu）；容器内端到端验收：/health milvus=true、/admin/ 页面、admin 登录、入库 done、删除全部通过 | docker-compose | ✅ docker compose up -d --build 全服务 healthy / 容器内 e2e 全绿 |
| 08-06 | Reasonix | **新增管理后台（离线知识入库）**：/admin/* 独立子系统——管理员登录（PBKDF2+JWT，账号存 MySQL webrag_admin）、上传 .txt/.md/.html 或粘贴文本、复用 parser→chunker→embedder 管线后台异步解析入库（processing→done/failed），知识块写入独立 Milvus collection webrag_offline_kb（标准 schema + doc_ref，支持按文档删除）；文档记录/原文备份存 MySQL（自动建表）；新增错误码 UNAUTHORIZED/NOT_FOUND/TOO_MANY_ATTEMPTS；前端 static/admin/（登录+入库+列表+删除）；scripts/init_admin.py 建管理员；依赖新增 pymysql/PyJWT/python-multipart（uv.lock 重生成）；docker-compose 新增 mysql:8.0 服务（named volume，随服务启动，webrag-app 注入 MYSQL_HOST=mysql）；config 新增 admin 段与 MYSQL_*/ADMIN_JWT_SECRET/MILVUS_OFFLINE_COLLECTION；api.md §1.4/§3、deploy.md、config README 同步；与 /ask 完全隔离（webrag_qa/临时库/health 形状不动）；新增 tests/test_admin.py 16 条 | admin（新模块）/ milvus_store / main / config / static / scripts / docker-compose / tests / docs | ✅ ruff（新文件）/ pytest 222 通过（3 条 Milvus 集成跳过） |
| 08-06 | Reasonix | **管理后台端到端修复**（真机测试发现）：① delete_by_doc_ref 返回真实删除块数（pymilvus 2.4 的 delete() 不含被删主键列表，改删除前 count(*) 计数）；② create_document 落库 char_count（前端字符数此前恒 0）；tests 同步（fake 跨实例共享计数，模拟 Milvus 持久化）；ruff 修复 milvus_store import 排版 | admin / milvus_store / tests | ✅ ruff / pytest 222 通过 / 真机 e2e（真实 MySQL+Milvus+BGE-M3）全绿 |

## 2026-08-05

| 日期 | 改动人 | 变更 | 影响模块 | 自测 |
| --- | --- | --- | --- | --- |
| 08-05 | Reasonix | **前端温度默认值读配置**：/health 新增 llm_temperature（settings.llm.temperature），前端加载时拉取并应用为滑杆初始值（5s 超时；拉取失败保留 HTML 兜底 0.3；用户已手动拖动则不覆盖）；api.md §1.2 同步；health 单测补充断言 | main / static / tests / docs | ✅ ruff / pytest |
| 08-05 | Reasonix | **温度参数支持**：AskRequest 新增可选 temperature（0–2，缺省 settings.llm.temperature），/ask 与 /ask/stream 均透传至 LLM client（_make_llm_client 支持按请求覆盖）；前端新增左侧「生成参数」栏——温度滑杆（0–2、步进 0.1）实时显示数值并随请求提交，回答越低越保守、越高越发散；api.md §1.1/§1.3/§4 与 schemas README 同步；新增 5 条单测（透传/默认值/0 值/越界 422/流式透传） | schemas / main / static / tests / docs | ✅ ruff / pytest |
| 08-05 | Reasonix | **问答缓存命中前端流式输出**：/ask/stream 缓存命中不再直接 done——先推 status「⚡ 命中历史问答缓存，正在输出…」，再分块推送已存摘要 delta（≤24 字符、句末标点断、块间 30ms 打字机节奏），最后 done（cached=true，delta 拼接 == done.answer，符合 §1.3 约定 2）；前端 onDelta 不再覆盖缓存状态提示（cacheHit 标记）；SSE 缓存测试由「无 delta」改「delta 拼接 == 摘要」；api.md §1.3 事件表更新 | main / static / tests / docs | ✅ ruff / pytest |
| 08-05 | Reasonix | **构建逻辑更新：纯 CPU 镜像瘦身**：修正上轮"CPU wheel 镜像小 2GB"的错误结论（实测 Linux 的 +cpu wheel 元数据自带 triton + nvidia-*，容器会真装上 ~2GB）；新增 `[tool.uv] exclude-dependencies` 剔除 13 个 nvidia-* + triton（纯 CPU 推理用不到；该机制本职即拦截传递依赖），uv.lock 重新生成不含它们；Dockerfile 重构——ENV 移至 uv sync 前、新增 `import torch` + 无 CUDA 断言构建期自检（防排除过头运行时才炸）；deploy/quickstart 文档同步 | 环境 / Dockerfile / docs | ✅ uv lock 无 nvidia/triton / uv sync 幂等 / ruff / pytest |
| 08-05 | Reasonix | **torch 回归 uv 统一管理**：撤销 `[tool.uv] exclude-dependencies=["torch"]`，pyproject 改为直接依赖 `torch>=2.2,<2.7` + `[tool.uv] find-links` 挂阿里云 pytorch-wheels CPU 源（扁平 wheelhouse 只能 find-links；本地版本优先：win/linux 解析 2.6.0+cpu，macOS 回落 PyPI CPU 版 2.6.0）；uv.lock 重新生成（torch/sympy/networkx/jinja2/mpmath 等回归锁内；nvidia-*/triton 条目为 win wheel 元数据 marker 残留，实际安装不触发）；uv sync 不再需要 --inexact；Dockerfile 删除手动装 torch 块（uv sync --frozen 一步装齐）；quickstart/README/deploy 文档同步 | 环境 / Dockerfile / docs | ✅ uv lock 135 包 / uv sync 幂等 / torch 2.6.0+cpu cuda:False / ruff / pytest |
| 08-05 | Reasonix | **问答缓存优先重构（替代旧 KB 三级级联）**：/ask 改为 ① 向量库 webrag_qa 按问题相似度检索历史问答（question→摘要+来源），命中（≥qa_min_score）直返 cached=true（不联网不调 LLM）；② 未命中走联网兜底（retriever.retrieve_web + rerank）→ LLM → 引用校验；③ 完成后 save_qa_record 落库（best-effort；直答无来源不入库）；新增 AskResponse.cached / QAHit / milvus_store add_qa·search_qa·create_qa_collection；删除 keyword 模块（BM25）、scripts/ingest.py、旧三级级联与 Redis 搜索缓存、search_dense；init_milvus 改建 webrag_qa（遗留 webrag_kb 提示清理）；config 增 enable_qa_cache/qa_min_score/qa_top_k/MILVUS_QA_COLLECTION；前端缓存命中提示；清理垃圾文件并补 .rsignore（*.bak/*.orig/*.rej） | retriever / milvus_store / main / schemas / config / scripts / static / tests / docs | ✅ ruff / pytest 70 通过（3 条 Milvus 集成跳过） |
| 08-05 | Reasonix | **/ask/stream 检索进度实时上报**：retriever.retrieve / _retrieve_web 新增可选 progress 回调（关键词/向量检索、精排、联网搜索、抓取页数、清洗嵌入 i/N、临时库检索等阶段）；main 将检索放入独立线程，进度经队列实时转发为 SSE status 事件（含生成前「正在生成回答…」）；前端状态条随阶段实时更新；/ask 整包路径不传回调、行为不变；新增 3 条单测（retriever 两阶段回调 + SSE 转发） | retriever / main / static / tests / docs | ✅ ruff / pytest |
| 08-05 | Reasonix | **新增 /ask/stream SSE 流式输出**：llm 新增 stream_generate / stream_generate_direct（OpenAI stream=True 逐段产出）；main 新增 /ask/stream（delta→done/error 事件流，检索/超时/LLM 失败一律 error 事件收尾，_retrieve_for 与 /ask 共用检索+预算逻辑）；前端 api.js 新增 askStream（fetch ReadableStream 解析 SSE）、render.js 打字机增量渲染（结束后 renderAnswer 统一消毒+引用化）；/ask 整包 JSON 保持兼容；新增 5 条 SSE 单测 | llm / main / static / tests / docs | ✅ ruff / pytest |
| 08-05 | Reasonix | **模型预加载 + 检索修复**：① BGE-M3/reranker 改启动预加载（新增 retriever.warmup，main lifespan 调用，首个 /ask 不再付模型加载耗时；health 增 embed_model_loaded）；② 联网链路抓取预算（按剩余 deadline 缩放抓取页数、超时封顶 10s，实测「三角洲主播巅峰赛」0 条/93.5s → 2 条/21.6s）；③ 重排先行再判联网（防无关稠密命中抑制联网补充）；④ 新增回归测试 | retriever / main / tests | ✅ ruff / pytest 64 通过 / 端到端 /ask 带引用 |
| 08-05 | Reasonix | **新增 DEEPSEEK_MODEL 配置**：.env 的 DEEPSEEK_MODEL 覆盖 settings.yaml 的 llm.model（env 优先，空值回退 yaml）；.env.example / config/README 同步 | config | ✅ load_settings 验证 / ruff / pytest |
| 08-05 | #1 | **torch 移出 uv 管理**：pyproject 移除 torch 依赖与 pytorch-cpu 索引/sources，新增 `[tool.uv] exclude-dependencies = ["torch"]`（uv.lock 不再含 torch 及 nvidia-*/triton/sympy 等 17 个依赖）；torch 改为各机器手动安装（GPU 装 CUDA wheel，CPU 装 CPU 版）；**同步依赖一律 `uv sync --inexact`**（普通 uv sync 会删除手动安装的 torch 及其运行时依赖） | 环境/全部 | ✅ uv lock 113 包；uv run 不触发下载；torch 2.6.0+cu124 cuda:True |

## 2026-08-04

| 日期 | 改动人 | 变更 | 影响模块 | 自测 |
| --- | --- | --- | --- | --- |
| 08-04 | #1（全栈实现） | **联网问答超时修复**：CPU 嵌入 5 页×12 块≈90s 超前端 120s 超时——① 新增 retriever.max_web_chunks_total（默认 24 ≈36s）总块数封顶；② 新增 server.ask_timeout_seconds（默认 105s）整体预算：retrieve/_retrieve_web 接收 deadline 提前收尾、main 剩余 <10s 返回 TIMEOUT、LLM 超时按预算剩余收敛；③ 前端 api.js 超时 120s→200s、提示文案更新；④ 新增单测（封顶/预算收尾） | retriever / main / config / static / tests | ✅ ruff / pytest / 冒烟 |
| 08-04 | #1（全栈实现） | **检索三级级联优化**：① 新增 keyword 模块——查询改写（jieba 拆词+停用词，rewrite.py）+ BM25 倒排索引（标题×2+摘要，KeywordIndex，磁盘持久化 data/kw_index/）；② retriever 级联重构——BM25 首筛（命中足且分高免嵌入快路）→ dense 向量补召（新增 milvus_store.search_dense）→ 联网 → 重排；③ LLM 直答兜底（新增 generate_direct，检索为空时 direct=true 应答，不再硬返回 EMPTY_RESULT；AskResponse 新增 direct 字段）；④ ingest 同步写索引 + --refresh-index 全量重建；⑤ 新增配置 enable_keyword_stage/keyword_min_score/enable_llm_direct/KEYWORD_INDEX_PATH；⑥ 依赖新增 jieba；⑦ 清理根目录垃圾 txt、新增 .rsignore（Resilio 忽略清单）；⑧ api.md/architecture.md 契约同步 | 全部（keyword 新增 / retriever / milvus_store / llm / main / ingest / config） | ✅ ruff / pytest / 冒烟 |
| 08-04 | #1（全栈实现） | 全链路实现：crawler（bing/tavily/bocha 适配层 + 抓取限速/重试/robots）、parser（trafilatura）、chunker（段落感知+重叠）、embedder（BGE-M3 dense+sparse）、milvus_store（add/search 混合检索 WeightedRanker）、retriever（查询分析+双库检索+重排+Redis 搜索缓存）、llm（DeepSeek chat/reasoner + 引用解析）、main（/ask 组装+错误码+静态托管）、static 前端（问答页+消毒器）、Dockerfile + compose app 服务、tests 单测 | 全部 | ✅ ruff / pytest / 端到端 |
| 08-04 | #1 | 兼容性修复：① pymilvus 2.4.15 的 hybrid_search 要求 AnnSearchRequest 对象、第二参名改为 rerank（dict/ranker 已移除）；② Milvus VARCHAR max_length 按字节计，add 前 _fit_varchar 截断（title≤512B 等）；③ 重排改用 sentence-transformers CrossEncoder（FlagReranker 与 transformers v5 不兼容：prepare_for_model 移除）；④ robots.txt 用 requests 拉取 + fail-open 策略（CPython 3.11 can_fetch 在未读到时 fail-closed，会误拦全站） | milvus_store / retriever / crawler | ✅ |
| 08-04 | #1 | Bing 搜索 API Key 返回 401 → API 调用失败自动降级抓取 bing.com 搜索页（无 Key 同样走此路径）；Bing API 成功时仍优先 | crawler | ✅ |
| 08-04 | #1 | 新增配置项：retriever.min_kb_results（知识库召回不足触发联网）、RERANKER_MODEL_PATH（重排模型路径）、EMBED_DEVICE（cpu/cuda）、retriever.max_chunks_per_page（每页最大块数，CPU 嵌入时延控制） | config / retriever | ✅ |
| 08-04 | #1 | 环境统一：pip 改为 uv（新增 pyproject.toml / uv.lock / .python-version），删除 requirements*.txt 与 scripts/freeze_env.py | 全部 | ✅ uv sync、init_milvus 建库 |
| 08-04 | #1 | 基础设施落盘：docker-compose.yml（etcd/minio/milvus v2.4.24/redis），docs/deploy.md 重写，本机 Attu 2.x 连 localhost:19530 | 部署/全部 | ✅ docker compose config、init_milvus |
| 08-04 | #1 | pymilvus 锁 2.4.x（>=2.4,<2.5）；新增 setuptools>=77,<82（pkg_resources 兼容，82 起移除） | milvus_store | ✅ init_milvus |
| 08-04 | #1 | 修复 MilvusStore.health()：connections.get_connection（2.4 不存在）→ utility.get_server_version | milvus_store / main | ✅ /health milvus:true |
| 08-04 | #1 | 协作方式变更：Git → 局域网共享文件夹（README §4、api.md §5、各模块 README 同步） | 全部（文档） | ✅ 全库检索无残留 |
| 08-04 | #1 | 协作方式修正：手动共享文件夹 → **Resilio Sync 全量实时同步**（README §4 重写：忽略清单/.env 策略/冲突文件处理；api.md §5、quickstart、config/README 同步） | 全部（文档） | ✅ 全库检索无残留 |
| 08-04 | #1 | 协作方式最终定：**Git 分支协作**（放弃 Resilio Sync/共享文件夹；README §4 恢复 main/dev/feature + 局域网 bare 仓库选项；托管平台待定 §9） | 全部（文档） | ✅ 全库检索无残留 |
| 08-04 | #1 | 协作方式最终定：**Resilio Sync**（成员在本地工作文件夹开发，完成后拷回同步主文件夹；放弃 Git；GitHub 仓库保留作历史备份） | 全部（文档） | ✅ 全库检索无残留 |
| 08-04 | #1 | 协作模型定稿：成员在同步夹内**只改自己模块目录** + 共享文件 owner 制（README §4 重写）；新增前端规格 static/README.md（参考 knowforge 静态页模式） | 全部（文档） | ✅ 全库检索无残留 |
| 08-04 | #1 | 契约定版（api.md v0.2）：embedder 双向量 EmbedResult / llm 两阶段 generate+build_response / milvus_store.search 双向量签名 / 补 analyze_query 与 QueryPlan | 契约（#4/#5/#6/#7/#8） | ✅ 字段与 models.py 逐项对齐 |
| 08-04 | #2 | crawler 补全 URL 去重（Redis）：新增 seen_url / normalize_url（原子 check-and-set + TTL，Redis 不可用自动降级）；fetch 保持纯抓取保证联网实时性 | crawler / tests | ✅ ruff / pytest |
| 08-04 | #3 | parser/chunker 补测与增强：chunker 句边界支持英文句点（后跟空白才切，避免拆开 3.14/v2.0）；parser 新增真实语料测试（表格/日期/广告页脚剔除）；记录 trafilatura 对 nav 的保留局限 | parser / chunker / tests | ✅ ruff / pytest |
| 08-04 | #6 | retriever 重排噪声截断：新增 rerank_min_score（默认 0.6，低于阈值片段不进入 LLM 上下文，宁可空结果不可错引用）；联网触发词补「近期/近日」 | retriever / config / tests | ✅ ruff / pytest / 端到端 |
| 08-04 | #2/#6 | 性能优化（基准 .reasonix/bench.py）：crawler 新增 fetch_many 并行抓取（共享全局限速，串行 6.7s→并行 4.6s）；retriever 复用查询向量（原 KB 与临时库各嵌一次，省 ~8s）+ 接入 fetch_many；max_chunks_per_page 24→12（CPU 嵌入 1.5s/块） | crawler / retriever / config | ✅ ruff / pytest / 端到端 |
| 08-04 | #8 | 前端打磨（D3）：视觉升级（渐变/阴影/引用徽标/来源圆标/空状态）+ 交互（示例 chips、spinner、耗时反馈、错误码提示）；api.js 客户端超时兑底 120s→TIMEOUT；无障碍（aria-live/aria-busy/reduced-motion） | static | ✅ HTTP 全资源 200 / pytest |
| 08-04 | #1 | 一键部署（D4）：webrag-app 接入 compose 默认启动（去 profile）；容器内连库地址改 compose 服务名（milvus:19530 / redis:6379，environment 覆盖 .env）；新增 init 一次性建库服务；Dockerfile 打入 scripts/；deploy.md v2 重写 | 部署 | ✅ docker compose up -d 全服务启动，容器内 /ask 端到端 43s |
| 08-04 | #1 | 构建兼容（国内网络）：基础镜像 python:3.11-slim → 3.12-slim（docker.io 拉取被限流，本地已有 3.12）；uv 改用 pip 安装（ghcr.io 拉取截断，改走清华 PyPI） | Dockerfile / deploy.md | ✅ 镜像构建成功（torch 2.13+cpu） |

## 模板（新变更时复制到顶部）

```
MM-DD | #编号 | 一句话变更 | 影响模块 | ✅/❌ 自测结果
```
