# crawler — 网页采集

## 职责

- 对接搜索引擎 API（Bing / Tavily / 博查等）获取候选 URL；
- 抓取候选网页 HTML；
- 调度与限速、失败重试、robots 合规、反爬规避；
- URL 去重（Redis）：重复 URL 不重复抓取。

## 接口约定

- 输入：查询词 query → 输出：候选 URL 列表（含标题、摘要）；
- 输入：URL → 输出：原始 HTML（交给 parser）；
- `fetch_many(urls, timeout_seconds, delay_seconds, max_workers)` → `[(url, html|None)]` 并行抓取（共享全局限速，单条失败不阻断）；
- `seen_url(url, redis_url="", ttl_seconds=604800)` → 该 URL 是否在去重窗口内已抓取过（原子 check-and-set，Redis 不可用恒 False）。

## 验收标准

- [x] 单条 query 稳定返回 N 条可抓取候选；
- [x] 限速策略下不被目标站封禁；
- [x] 明确声明 robots / 版权合规策略；
- [x] URL 去重（Redis）：重复 URL 不重复抓取；Redis 不可用自动降级，不阻断主链路。

## URL 去重设计说明

- **原子性**：`SET key 1 NX EX ttl` 单命令完成「检查 + 写入」，无并发重复抓取窗口；
- **归一化**：`normalize_url` 统一 scheme/host 大小写、去 fragment 与默认端口、去尾斜杠，`https://x.com/a/` 与 `https://x.com/a` 视为同一 URL；
- **实时性权衡**：`fetch()` 本身不内置去重——联网问答（临时 qa_ 库）需要新鲜内容，同一 URL 隔天再问应重新抓取；需要去重的调用方在 fetch 前先调 `seen_url` 判重，命中即跳过（架构 §4 增量更新）。
