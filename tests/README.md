# tests — 测试

## 职责

- 单元测试：各模块核心逻辑（parser、chunker、引用解析等）；
- 集成测试：端到端问答链路（mock 外部 API）；
- 回归保障：变更生效前必须通过。

## 所属角色

- 测试 / 评测（#9）统筹；
- **各模块负责人为各自模块补单测**（分工表各角色）。

## 约定

- 使用 pytest；网络请求（搜索、DeepSeek、Milvus）用 mock/fixture；
- 每次变更在 PR 中说明测试影响；合入 dev 前本机 `uv run pytest` 全绿；CI 接入可选（M4）。
