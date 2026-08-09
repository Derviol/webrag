# 结构化日志系统（docs/logging.md）

> 实现：`src/webrag/logger/`（零第三方依赖，stdlib `logging` + `json`）。
> 本文件为日志事件 schema 的权威文档，改动需同步。

## 1. 设计目标

替代模块内散落的 `print(f"[x] ...")`，提供可检索、可统计、可追踪的结构化日志：

- **每行一条 JSON**（JSONL）：`ts` / `level` / `logger` / `event` + 结构化字段 + 请求上下文；
- **请求级指标**：每次 `/ask`（含 `/ask/stream`）落一条 `*.completed` 事件，字段含
  **时间、耗时（总 + 分阶段）、命中率（缓存命中 + 检索结果数）、token 消耗、首 token 时间（TTFT）**；
- **聚合统计**：每 `stats_interval`（默认 50）个请求输出一条 `stats.periodic`（缓存命中率 /
  空结果率 / 错误分布 / 平均与 p95 耗时 / 平均 token / 平均 TTFT）；另有只读接口 `GET /logs/stats`。

## 2. 存储与配置

| 项 | 默认 | 说明 |
| --- | --- | --- |
| 文件 | `logs/app.log` | JSONL，按行追加；`logs/` 已 gitignore |
| 轮转 | 10MB × 5 | 超出轮转 `app.log.1` ~ `.5`（RotatingFileHandler） |
| 控制台 | 开 | Docker stdout（`docker compose logs webrag-app`） |
| 控制台格式 | 可读文本 | `logging.console_json: true` 时也输出 JSON（容器日志采集） |

配置在 `config/settings.yaml` 的 `logging:` 段（`config.py` 的 `LogSettings` dataclass，字段一一对应）。
Docker 部署下 `./logs:/app/logs` 卷将日志持久化到宿主（docker-compose.yml）。

```bash
# 宿主上查看（示例）
tail -f logs/app.log | jq 'select(.event == "ask.completed") | {ts, outcome, duration_ms, cached, tokens, ttft_ms}'
```

## 3. 通用字段（每条事件都有）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ts` | string | ISO8601 时间戳（UTC，毫秒精度） |
| `level` | string | DEBUG / INFO / WARNING / ERROR |
| `logger` | string | `webrag.<模块>`（main / llm / retriever / crawler / embedder / admin.ingest / …） |
| `event` | string | 事件名（点分，如 `ask.completed`、`llm.call`） |
| `request_id` | string | 请求级 trace id（12 位 hex），同请求的跨事件关联键；仅请求上下文中事件携带 |
| `question` | string | 用户问题（**截断 120 字符**，隐私）；仅请求上下文中事件携带 |
| `exception` | string | 异常堆栈（`exc_info=True` 时） |

## 4. 事件清单

### 4.1 请求级（`ask.completed` / `ask.stream.completed`）——核心指标

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `endpoint` | string | `ask` / `ask.stream` |
| `outcome` | string | `cache_hit` / `success` / `empty` / `error:<CODE>`（CODE 见 docs/api.md §1.1） |
| `duration_ms` | number | 请求总耗时 |
| `segments_ms` | object | 分阶段耗时：`rewrite_ms` / `cache_lookup_ms` / `retrieve_ms` / `generate_ms` / `hallucination_check_ms` / `cache_save_ms`（相邻阶段标记之差；提前失败的阶段缺失） |
| `cached` | bool/null | 问答缓存命中/未命中；null = 未走缓存路径（缓存关闭或强制跳过） |
| `cache_score` | number/null | 缓存命中判定分（top-1 综合分） |
| `direct` | bool/null | LLM 直答兜底 |
| `retrieval_hits` | int/null | 检索返回的片段数（命中率分母） |
| `tokens` | object | `{prompt, completion, total}` 本请求全部 LLM 调用之和 |
| `llm_calls` | array | 每次 LLM 调用明细：`{call, model, duration_ms, ttft_ms, prompt_tokens, completion_tokens, error}` |
| `ttft_ms` | number/null | **首 token 时间**：流式从请求开始到首个 delta 输出；非流式无此概念为 null |
| `answer_len` | int/null | 回答字符数 |
| `use_web_search` / `web_top_n` | bool/int/null | 请求参数 |

### 4.2 LLM 调用级（`llm.call`）

每次 DeepSeek 调用（generate / generate_direct / complete / stream_generate / stream_generate_direct）实时落一条：
`call`、`model`、`duration_ms`、`ttft_ms`（流式 = 自调用开始到首个内容 delta）、
`prompt_tokens` / `completion_tokens` / `total_tokens`（非流式取 `resp.usage`；流式经
`stream_options={"include_usage": True}` 取末块 usage——DeepSeek 官方支持，usage 尾块 `choices` 恒为空数组）、
失败时 `error`（WARNING 级别）。

### 4.3 检索级（`retriever.*`）

| event | 字段 | 说明 |
| --- | --- | --- |
| `retriever.cache_lookup` | `hit` / `score` / `threshold` / `candidates` | 问答缓存判定明细（命中率归因） |
| `retriever.offline_search` | `hits` | 离线知识库返回片段数 |
| `retriever.web_search` | `pages` / `chunks` / `results` | 联网链路抓取页数 / 嵌入块数 / 最终结果数 |
| `retriever.reranker_load_failed` / `offline_search_failed` / `cache_search_failed` / `cache_write_failed` / `fetch_failed` / `fetch_clean_failed` / `rerank_failed` / `web_budget_*` | `error` / `url` / `remaining_s` | 降级与失败告警（WARNING） |

### 4.4 其他

- `main.warmup_failed` / `admin.db_unavailable` / `ask.rewrite_failed` / `ask.web_fallback_offline` /
  `ask.hallucination_risk`（含 `risk` / `hallucination_rate`）/ `ask.hallucination_check_failed`；
- `admin.jwt_secret_missing`、`admin.ingest_chunked` / `admin.ingest_done`（含 `doc_id` / `chunks` / `chars`）/
  `admin.ingest_failed`（含 `exception` 堆栈）；
- `crawler.bing_api_failed`；`feedback_store.skip_corrupt_line`；`feedback.submitted`；
- `embedder.embed`（DEBUG：`texts` / `duration_ms`）；
- `stats.periodic`：每 `stats_interval` 个请求一条聚合统计（见 §5）。

## 5. 聚合统计（命中率等）

`stats.periodic`（以及 `GET /logs/stats`）字段：

| 字段 | 说明 |
| --- | --- |
| `requests` | 累计请求数 |
| `cache_hit_rate` | 缓存命中率 = cache_hits / (cache_hits + cache_misses)（未走缓存路径的请求不计入分母） |
| `empty_count` / `empty_rate` | 检索为空返回「信息不足」的次数与占比 |
| `errors` / `error_count` | 按 outcome 分组的错误数 |
| `avg_duration_ms` / `p95_duration_ms` | 平均 / p95 耗时 |
| `avg_tokens` | 平均 token 消耗（全部 LLM 调用） |
| `avg_ttft_ms` | 平均首 token 时间（仅流式请求） |

统计为**进程内内存态**（重启清零），长期趋势用 `logs/app.log` 离线聚合（jq / 脚本）。

## 6. 代码约定

- 模块内：`_log = get_logger("<模块名>")`，`_log.info/warning/error("event.name", extra={"fields": {...}})`；
  消息即事件名，结构化数据一律进 `fields`；`extra` 的 key 只用 `fields`（避免与 logging 保留属性冲突）；
- 异常带堆栈：`exc_info=True`；不要拼 `str(exc)` 进事件名；
- 请求内任意位置可用 `current_metrics()` 取当前请求的 `RequestMetrics`（LLM 调用即如此归入请求）；
- 子线程必须显式 `bind_request(request_id, question, metrics)`（参考 main.py `_retrieve_in_thread`）；
- 日志调用必须**零成本降级**：不配置、目录不可写、脚本独立使用时均不能抛异常影响主链路。
