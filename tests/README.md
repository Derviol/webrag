# tests — 测试

## 职责

- 单元测试：各模块核心逻辑（parser、chunker、引用解析、查询改写、幻觉检测等）；
- 集成测试：端到端问答链路（mock 外部 API）+ 管理后台（真实 MySQL/Milvus 或 fake）；
- 回归保障：变更提交前必须全绿。

## 测试清单

| 文件 | 覆盖 |
| --- | --- |
| test_api.py | /ask、/ask/stream（SSE）、/health、错误码 |
| test_retriever.py / test_p1_retriever.py | 缓存优先 / 联网兜底 / 重排 / 去重 / 来源质量分层 |
| test_query_rewriter.py / test_followup.py | 查询改写 / 追问检测 / 时效锚定 |
| test_llm.py / test_hallucination_checker.py | 引用解析与校验 / 幻觉检测 |
| test_admin.py | 管理后台（登录 / 入库 / 删除 / 角色校验 / 限流） |
| test_milvus_store.py | 问答缓存与离线库读写（fake 或真实 Milvus） |
| test_chunker.py / test_parser.py / test_crawler.py | 切块 / 清洗 / 采集单测 |
| test_config.py / test_logger.py / test_p2_eval_feedback.py | 配置加载 / 结构化日志 / 反馈闭环 |

## 约定

- 使用 pytest；网络请求（搜索、DeepSeek、Milvus）用 mock/fixture；
- 无 CI：提交前作者本机 `uv run pytest` + `uv run ruff check` 全绿。
