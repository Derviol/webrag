"""BGE-M3 嵌入封装：dense + sparse 双向量。

接口契约（docs/api.md §3）：embed(texts) -> EmbedResult
负责人：#4 Embedding 服务。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmbedResult:
    dense: list[list[float]]  # dim=1024，与 milvus_store/schema.py 的 DENSE_DIM 对齐
    sparse: list[dict]  # 每项 {token_id: weight}，Milvus sparse 输入格式


class BGE3Embedder:
    def __init__(self, model_path: str, device: str = "cpu"):
        self.model_path = model_path
        self.device = device
        self._model = None

    def load(self) -> None:
        """加载 BGE-M3（FlagEmbedding）。首次下载 2GB+，D1 上午启动。

        TODO(#4)：模型加载 + 缓存到 model_path（避免重复下载）。
        """
        raise NotImplementedError("BGE3Embedder.load() 待 #4 实现")

    def embed(self, texts: list[str]) -> EmbedResult:
        """一次前向输出 dense + sparse 双向量。

        TODO(#4)：批量嵌入，维度必须与 schema.DENSE_DIM 一致。
        """
        raise NotImplementedError("BGE3Embedder.embed() 待 #4 实现")
