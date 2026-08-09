"""Milvus collection schema 定义 —— D1 全组对齐点（唯一权威）。

约定（架构文档 §6）：
- dense 维度与 embedder 对齐（BGE-M3 = 1024）；
- 变更 schema 必须先同步 #4 embedder 与 docs/api.md。
负责人：#5 向量库开发。
"""

from pymilvus import CollectionSchema, DataType, FieldSchema

DENSE_DIM = 1024  # BGE-M3 dense 向量维度
COLLECTION_KB = "webrag_kb"  # 预建知识库 collection 名（已废弃：/ask 改走问答缓存，见 build_qa_schema）
COLLECTION_QA = "webrag_qa"  # 问答缓存 collection 名（question → 摘要 + 来源）
COLLECTION_OFFLINE = "webrag_offline_kb"  # 离线知识库 collection 名（管理后台入库，独立于问答链路）
COLLECTION_TMP_PREFIX = "qa_"  # 联网问答临时 collection 前缀，如 qa_<uuid>


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


def build_offline_schema() -> CollectionSchema:
    """离线知识库 collection schema：知识块 + doc_ref（文档归属，支持按文档删除）。

    与 build_schema（标准 KB schema）字段一致，额外加 doc_ref 字段：
    管理后台入库的每份文档对应唯一 doc_ref（offline_<uuid>），
    删除文档时按 doc_ref 表达式批量删除该文档的全部块（delete_by_doc_ref）。
    """
    fields = build_schema().fields + [
        FieldSchema(name="doc_ref", dtype=DataType.VARCHAR, max_length=128),
    ]
    return CollectionSchema(fields=fields, description="WebRAG 离线知识库（管理后台入库，dense + sparse + doc_ref）")


def build_qa_schema() -> CollectionSchema:
    """问答缓存 collection schema：问题原文 + 摘要 + 来源(JSON) + dense 问题向量。

    语义：/ask 先按 question_vec 相似度检索历史问题，命中（score ≥ qa_min_score）
    直接返回存储的 summary + sources，不再联网 / 不调 LLM（架构 §6）。
    """
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="question", dtype=DataType.VARCHAR, max_length=2048),
        FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="sources", dtype=DataType.VARCHAR, max_length=8192),  # JSON: [{index,title,url}]
        FieldSchema(name="created_at", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="question_vec", dtype=DataType.FLOAT_VECTOR, dim=DENSE_DIM),
    ]
    return CollectionSchema(fields=fields, description="WebRAG 问答缓存（问题 → 摘要 + 来源）")


def build_qa_indexes(collection) -> None:
    """问答缓存索引：question_vec dense（COSINE）。单向量，无 sparse。"""
    collection.create_index(
        "question_vec",
        {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}},
    )
