# src/webrag — 后端服务（链路组装层）

## 职责

- FastAPI 入口（main.py）：`/ask`（问答）、`/health`（探活）；
- 组装整条链路：问题 → 检索/生成 → 带来源标注的回答；
- 异常处理、日志、超时与限流策略。

## 所属角色

- 后端 API / 前端（#8）；
- 依赖各模块契约（见 schemas/）。

## 关键交付物

- main.py：应用入口与路由；
- 请求/响应的组装与错误码约定；
- OpenAPI 自动文档（FastAPI 自带）。

## 验收标准

- [ ] `/ask` 返回 answer + sources，格式符合 schemas 契约；
- [ ] `/health` 可被 docker compose 健康检查使用；
- [ ] 单条请求超时有兜底（返回错误信息而非挂死）。
