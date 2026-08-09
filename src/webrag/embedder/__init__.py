"""BGE-M3 嵌入封装：dense + sparse 双向量。

接口契约（docs/api.md §3）：embed(texts) -> EmbedResult
负责人：#4 Embedding 服务。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.webrag.logger import get_logger

_log = get_logger("embedder")


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
        """加载 BGE-M3（FlagEmbedding，本地路径 models/bge-m3，一次加载进程内复用）。"""
        if self._model is not None:
            return
        try:
            from FlagEmbedding import BGEM3FlagModel
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("FlagEmbedding 未安装（uv sync 后重试）") from exc
        try:
            self._model = BGEM3FlagModel(self.model_path, use_fp16=False, device=self.device)
        except Exception as exc:
            raise RuntimeError(f"BGE-M3 模型加载失败（{self.model_path}）：{exc}") from exc

    def embed(self, texts: list[str]) -> EmbedResult:
        """一次前向输出 dense + sparse 双向量（dense dim=1024，与 schema.DENSE_DIM 对齐）。"""
        if not texts:
            return EmbedResult(dense=[], sparse=[])
        self.load()
        t0 = time.monotonic()
        try:
            out = self._model.encode(
                texts,
                batch_size=min(32, max(1, len(texts))),
                return_dense=True,
                return_sparse=True,
                max_length=8192,
            )
        except Exception as exc:
            raise RuntimeError(f"BGE-M3 嵌入失败：{exc}") from exc

        _log.debug(
            "embedder.embed",
            extra={"fields": {"texts": len(texts), "duration_ms": round((time.monotonic() - t0) * 1000, 1)}},
        )
        dense = [list(map(float, v)) for v in out["dense_vecs"]]
        sparse = [{int(k): float(v) for k, v in s.items()} for s in out["lexical_weights"]]
        return EmbedResult(dense=dense, sparse=sparse)
