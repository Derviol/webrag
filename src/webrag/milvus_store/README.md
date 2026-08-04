# milvus_store — 向量库

## 职责

- Milvus collection / schema / 索引设计（dense + sparse 双向量字段；元数据：url、title、publish_time）；预建知识库 collection 与问答临时 collection（qa_<id>）双模式；
- 批量写入（upsert）与查询封装（向量检索 + 过滤）；
- 初始化脚本（scripts/init_milvus.py）与连接管理。

## 所属角色

- 向量库开发（#5）。

## 接口约定

| 操作 | 签名 | 说明 |
| --- | --- | --- |
| 写入 | add(chunks, vectors) | 返回成功/失败计数 |
| 查询 | search(query_vector, top_k, filters?) | 返回 SearchResult[]（含元数据） |
| 对齐 | 与 embedder 维度、chunker 元数据字段一致 | 变更需三方同步 |

## 验收标准

- [ ] 万级向量检索 p95 时延达标（基线记入 config）；
- [ ] 元数据字段可随检索结果带回（用于引用标注）；
- [ ] 索引参数随数据量增长可调。
