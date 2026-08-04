"""Milvus collection schema 定义 —— D1 全组对齐点（唯一权威）。

约定（架构文档 §6）：
- dense 维度与 embedder 对齐（BGE-M3 = 1024）；
- 变更 schema 必须先同步 #4 embedder 与 docs/api.md。
负责人：#5 向量库开发。
"""

from pymilvus import CollectionSchema, DataType, FieldSchema

DENSE_DIM = 1024  # BGE-M3 dense 向量维度
COLLECTION_KB = "webrag_kb"  # 预建知识库 collection 名
COLLECTION_TMP_PREFIX = "qa_"  # 问答临时 collection 前缀，如 qa_<uuid>


def build_schema() -> CollectionSchema:
    """知识库 collection schema：文本元数据 + dense + sparse 双向量。"""
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="url", dtype=DataType.VARCHAR, max_length=2048),
        FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="publish_time", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="seq", dtype=DataType.INT64),
        FieldSchema(name="dense_vec", dtype=DataType.FLOAT_VECTOR, dim=DENSE_DIM),
        FieldSchema(name="sparse_vec", dtype=DataType.SPARSE_FLOAT_VECTOR),
    ]
    return CollectionSchema(fields=fields, description="WebRAG 网页检索知识库（dense + sparse）")


def build_indexes(collection) -> None:
    """dense：IVF_FLAT + COSINE（数据量大后换 HNSW）；sparse：倒排索引，metric 固定 IP。"""
    collection.create_index(
        "dense_vec",
        {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}},
    )
    collection.create_index(
        "sparse_vec",
        {"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP"},
    )
