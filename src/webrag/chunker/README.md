# chunker — 文本切块

## 职责

- 正文 → 语义完整的块：标题感知、段落边界、相邻块重叠；
- 块大小与 BGE-M3 输入上限匹配，配合检索效果调参；
- 输出块级元数据：来源 URL、标题路径、段落序号（用于引用定位）。

## 所属角色

- 数据清洗 / 切块（#3）。

## 接口约定

- 输入：Document → 输出：Chunk[]（格式见 schemas）；
- 下游：embedder（向量化）、milvus_store（入库）。

## 验收标准

- [x] 块内语义完整（不拦腰切断句子）：中文标点直接切；英文句点仅在后跟空白/行尾时切，不拆坏小数与版本号（tests/test_chunker.py）；
- [x] 参数（size / overlap / respect_paragraph）可在 config 中调整，随评测迭代。
