# schemas — 数据契约

> 权威契约文档：docs/api.md；本文件为模块内速览，字段以 api.md 为准。

## 职责

- 定义模块间与对外的数据模型：AskRequest、AskResponse、Chunk、SearchResult 等；
- 重点定义**引用标注格式**：回答中的 `[1][2]` 序号 ↔ sources 列表（URL、标题）的映射规则。

## 所属角色

- 初版由项目协调 / 架构（#1）定义；
- 由后端 API / 前端（#8）维护，所有模块必须遵守。

## 关键契约（草案）

| 模型 | 字段 |
| --- | --- |
| AskRequest | question（必填） |
| AskResponse | answer、sources[{index, title, url}] |
| Chunk | text、metadata{url, title, publish_time, seq} |
| SearchResult | chunk、score |

## 约定

- 字段变更 = 破坏性变更，先同步 llm / retriever / 前端，再改代码；
- 新增字段保持向后兼容。
