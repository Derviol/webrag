# retriever — 问答链路（缓存优先 + 联网兜底）

## 职责

- **问答缓存检索**（`lookup_qa_cache`）：嵌入用户问题 → 检索 webrag_qa 历史相似问题，
  Top-1 余弦 ≥ `qa_min_score` 命中 → 返回历史摘要 + 来源（不联网、不调 LLM，cached=true）；
  未命中时返回已嵌入的问题向量（qvec）供联网检索复用（一次问答只嵌入一次问题）；
- **联网兜底检索**（`retrieve_web`）：未命中时 搜索 → 并行抓取 → 清洗切块 → 嵌入 →
  临时 collection（qa_<id>）检索（块数封顶 + deadline 预算控制时延）；
- **缓存落库**（`save_qa_record`）：生成完成后把「用户问题 + 摘要 + 来源」写入问答缓存
  （best-effort，失败不影响本次回答；直答兜底无来源不入库）；
- 重排：`rerank` —— bge-reranker-v2-m3 对联网结果精排（rerank_min_score 剔除噪声）。

## 接口约定

- 输入：用户问题 → 输出：`QAHit`（缓存命中）或 `SearchResult[]`（联网检索）；
- 上游：milvus_store（webrag_qa / qa_<id>）、embedder；下游：main.py（组装）、llm（拼装上下文）。

## 验收标准

- [x] 同问 / 近似问命中缓存秒回（cached=true），答案与来源可溯源；
- [x] 未命中走联网兜底，问题向量只嵌入一次（qvec 复用）；
- [x] `qa_min_score` 误命中率经 eval 评测（宁高勿低：命中返回整段历史摘要）。
