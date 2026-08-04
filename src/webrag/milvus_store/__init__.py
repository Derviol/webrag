"""向量库访问封装。

接口契约（docs/api.md §3）：
- create_collection(name)
- add(collection, chunks, vectors) -> int
- search(collection, vectors, top_k) -> list[SearchResult]
- drop_collection(name)
负责人：#5 向量库开发。注意：需 Milvus >= 2.4 支持 sparse 字段。
"""

from __future__ import annotations

from pymilvus import Collection, connections, utility

from src.webrag.schemas import SearchResult
from .schema import build_indexes, build_schema


class MilvusStore:
    def __init__(self, uri: str):
        self._uri = uri
        self._connected = False

    def connect(self) -> None:
        connections.connect(alias="default", uri=self._uri)
        self._connected = True

    def health(self) -> bool:
        try:
            if not self._connected:
                self.connect()
            utility.get_server_version()  # 真实 gRPC 往返；pymilvus 2.4 无 connections.get_connection
            return True
        except Exception:
            return False

    def create_collection(self, name: str) -> Collection:
        """建 collection + 索引 + 加载。已存在时由调用方用 utility.has_collection 判断。"""
        collection = Collection(name=name, schema=build_schema(), using="default")
        build_indexes(collection)
        collection.load()
        return collection

    def add(self, collection_name: str, chunks, vectors) -> int:
        """批量写入：chunks + vectors（EmbedResult，含 dense + sparse）。

        TODO(#5)：拼接 entities（text/url/title/publish_time/seq + dense_vec + sparse_vec），
        返回写入行数。ingest.py 按此签名调用。
        """
        raise NotImplementedError("MilvusStore.add() 待 #5 实现")

    def search(self, collection_name: str, vectors, top_k: int) -> list[SearchResult]:
        """Top-k 混合检索（dense + sparse，weighted reranker 融合）。

        TODO(#5)：query() 带两个向量字段 + WeightedRanker，返回 SearchResult[]。
        """
        raise NotImplementedError("MilvusStore.search() 待 #5 实现")

    def drop_collection(self, name: str) -> None:
        """删除 collection（临时库 qa_<id> 用后即清）。不存在时抛错，由调用方 guard。"""
        Collection(name, using="default").drop()
