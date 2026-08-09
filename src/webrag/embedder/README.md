# embedder — BGE-M3 向量化

## 职责

- BGE-M3 模型加载与推理封装（dense + sparse 双向量输出，一次前向）；
- 批量嵌入、模型缓存（首次下载后复用，避免重复下载）；
- 提供与 milvus_store schema 对齐的向量格式。

## 接口约定

- 输入：文本列表 → 输出：向量列表 + 维度/类型说明（dense/sparse）；
- 下游：milvus_store（写入）、retriever（查询向量生成）。

## 验收标准

- [x] 单条嵌入时延达标（CPU 实测 ~1.5s/块，基线记入 config 注释）；
- [x] 维度与 collection schema 严格一致（dense dim=1024 + sparse 配置），避免写入报错；
- [x] 模型加载失败有可读错误（main.warmup_failed 日志 + /health embed_model_loaded 字段）。
