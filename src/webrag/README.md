# src/webrag — 后端服务（链路组装层）

## 职责

- FastAPI 入口（main.py）：`/ask`（整包问答）、`/ask/stream`（SSE 流式）、`/health`（探活）、`/logs/stats`（日志统计）、静态托管；
- 组装整条链路：问题 → 预处理（追问改写 / 时效锚定 / 查询改写）→ 检索（问答缓存 / 离线知识库 / 联网兜底）→ 生成 → 带来源标注的回答；
- 账户系统（accounts.py：`/auth/*`）与聊天记录（chat_routes.py：`/chat/*`）；
- 异常处理、结构化日志、超时与预算策略（`server.ask_timeout_seconds`）。

## 依赖

- 各模块契约见 schemas/README.md 与 docs/api.md；模块间通过主链路顺序解耦。

## 关键交付物

- main.py：应用入口与路由（/ask 整包 + /ask/stream SSE + /health + /logs/stats）；
- accounts.py / chat_routes.py：JWT 账户系统与聊天记录 MySQL 持久化；
- logger/：结构化日志系统（JSONL + 请求级指标：耗时/命中率/token/首token，见 docs/logging.md）；
- 统一错误码信封（见 docs/api.md §1.1）。

## 验收标准

- [x] `/ask` 返回 answer + sources，格式符合 schemas 契约；
- [x] `/health` 可被 docker compose 健康检查使用（compose healthcheck 探测）；
- [x] 单条请求超时有兜底（整体预算 + TIMEOUT 错误码，不挂死）。
