# milvus_store — 向量库

## 职责

- Milvus collection / schema / 索引设计：
  - **问答缓存 collection**（webrag_qa）：question → 摘要 + 来源（dense question_vec，COSINE）；
  - 联网临时 collection（qa_<id>）：dense + sparse 双向量 + 网页元数据（url/title/publish_time），用后即清；
- 批量写入（upsert）与查询封装（向量检索 + 过滤）；
- 初始化脚本（scripts/init_milvus.py）与连接管理。

## 接口约定

| 操作 | 签名 | 说明 |
| --- | --- | --- |
| 建库（临时库） | create_collection(name) | dense+sparse 双向量，索引 + 加载 |
| 建库（问答缓存） | create_qa_collection(name) | question→摘要+来源，question_vec 索引 + 加载 |
| 写入（临时库） | add(collection, chunks, vectors) | 返回写入行数 |
| 写入（问答缓存） | add_qa(collection, questions, summaries, sources_json, vectors) | 返回写入行数 |
| 查询（临时库） | search(collection, vectors, top_k) | SearchResult[]（dense+sparse 混合，WeightedRanker） |
| 查询（问答缓存） | search_qa(collection, vectors, top_k) | QAHit[]（dense-only COSINE，question_vec） |
| 删除 | drop_collection(name) | 临时库用后即清 |

> 对齐：与 embedder 维度（1024）、schemas QAHit 字段一致；变更需三方同步。

## 验收标准

- [ ] 万级向量检索 p95 时延达标（基线记入 config）；
- [ ] 问答缓存写入/检索 round-trip 正确（sources JSON 可解析回 Source[]）；
- [ ] 索引参数随数据量增长可调。
