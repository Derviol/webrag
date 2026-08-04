# retriever — 检索链路

## 职责

- 查询分析：关键词提取、改写/扩写、是否触发联网检索的判断；
- Top-k 检索：调用 milvus_store，返回候选片段；
- 重排：bge-reranker-v2-m3 对 Top-k 精排，相关度调优。

## 所属角色

- 检索链路（#6，2 人）：
  - A：查询分析与检索召回；
  - B：重排与相关度调优（配合 eval 评测）。

## 接口约定

- 输入：用户问题 → 输出：重排后的 SearchResult[]；
- 上游：milvus_store、embedder；下游：llm（拼装上下文）。

## 验收标准

- [ ] Recall@k / MRR 达到目标（基线在 eval/ 记录）；
- [ ] 检索结果按相关度排序，与引用标注一致。
