# config — 配置管理

## 职责

- 统一管理运行配置：分块参数、检索 Top-k、模型名称、连接地址、重排开关等；
- 提供 `.env.example` 模板，规范密钥与连接信息的注入方式。

## 所属角色

- 项目协调 / 架构（#1）负责维护；
- 各模块负责人向 config 提出配置项需求，不在代码里硬编码参数。

## 交付物

- settings.yaml：环境无关的可调参数（分块大小、Top-k、问答缓存阈值 qa_min_score、模型名、超时等）；
- .env.example：密钥与连接信息模板（真实值放本地 .env，忽略列表中排除、不进入同步）。

## 管理后台（admin）配置项

- `.env`：`MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`（管理后台账号库）、`ADMIN_JWT_SECRET`（JWT 签名密钥，
  务必改为随机长串）、`MILVUS_OFFLINE_COLLECTION`（离线知识库 collection，默认 webrag_offline_kb）；
- settings.yaml `admin:` 段：`token_ttl_seconds`（登录有效期，默认 12h）、`max_file_bytes`（上传大小上限）、
  `max_chars_per_doc`（单文档字符上限）；切块参数复用 `chunker:` 段。

## 追问检测与改写（query_rewriter 段新增）

- `enable_followup`（默认 true）：追问检测与改写总开关——判定当前问题是否依赖历史消息（追问），
  若是则改写为自包含完整问题后再走检索与生成；需请求携带 `history` 字段（前端传当前问题之前的消息），
  不传则视为单轮提问、不触发；
- `followup_max_history`（默认 6）：构造 LLM 判定上下文时最多携带的历史消息条数（取最近 N 条）；
- `followup_max_chars`（默认 3000）：历史文本总长上限（字符），超出截断较早轮次（优先保留最近对话）。

## 约定

- 密钥（API Key、密码）只进 .env，绝不进 settings.yaml 或代码；
- 新增配置项需在 docs/CHANGELOG.md 说明用途与默认值；
- `DEEPSEEK_MODEL`（.env）可覆盖 settings.yaml 的 `llm.model`：连接信息类变量 env 优先，
  部署环境按实例定制模型时无需改 yaml（settings.yaml 保持环境无关）。
