"""milvus_store 集成测试：add/search 双向量混合检索 round-trip。

需要真实 Milvus（docker compose up -d）+ 显式开关：
    WEBRAG_MILVUS_TEST=1 uv run pytest tests/test_milvus_store.py -v
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("WEBRAG_MILVUS_TEST") != "1",
    reason="需启动 Milvus 并设置 WEBRAG_MILVUS_TEST=1",
)


def test_add_search_roundtrip():
    from pymilvus import utility

    from src.webrag.config import load_settings
    from src.webrag.embedder import EmbedResult
    from src.webrag.milvus_store import MilvusStore
    from src.webrag.schemas import Chunk, ChunkMetadata

    settings = load_settings()
    store = MilvusStore(settings.milvus_uri)
    store.connect()

    name = "test_qa_roundtrip"
    if utility.has_collection(name):
        store.drop_collection(name)
    try:
        store.create_collection(name)
        chunks = [
            Chunk(text="BGE-M3 支持稠密与稀疏双向量。", metadata=ChunkMetadata(url="https://x.com/1", title="t1", seq=1)),
            Chunk(text="Milvus 支持混合检索。", metadata=ChunkMetadata(url="https://x.com/2", title="t2", seq=2)),
        ]
        emb = EmbedResult(
            dense=[[0.1] * 1024, [0.9] * 1024],
            sparse=[{1: 0.5, 2: 0.3}, {7: 0.8, 9: 0.4}],
        )
        n = store.add(name, chunks, emb)
        assert n == 2

        qvec = EmbedResult(dense=[[0.9] * 1024], sparse=[{7: 0.8, 9: 0.4}])
        results = store.search(name, qvec, top_k=5)
        assert len(results) == 2
        assert results[0].chunk.metadata.url == "https://x.com/2"  # 更相似的那条排前
        assert results[0].chunk.text == "Milvus 支持混合检索。"
        assert results[0].score > 0
    finally:
        store.drop_collection(name)


def test_search_empty_collection_returns_empty():
    from pymilvus import utility

    from src.webrag.config import load_settings
    from src.webrag.embedder import EmbedResult
    from src.webrag.milvus_store import MilvusStore

    settings = load_settings()
    store = MilvusStore(settings.milvus_uri)
    store.connect()

    name = "test_qa_empty"
    if utility.has_collection(name):
        store.drop_collection(name)
    try:
        store.create_collection(name)
        qvec = EmbedResult(dense=[[0.1] * 1024], sparse=[{1: 0.5}])
        assert store.search(name, qvec, top_k=5) == []
    finally:
        store.drop_collection(name)


def test_qa_cache_add_search_roundtrip():
    """问答缓存（question → 摘要 + 来源）round-trip：add_qa → search_qa → sources JSON 解析。"""
    import json as json_lib

    from pymilvus import utility

    from src.webrag.config import load_settings
    from src.webrag.embedder import EmbedResult
    from src.webrag.milvus_store import MilvusStore

    settings = load_settings()
    store = MilvusStore(settings.milvus_uri)
    store.connect()

    name = "test_qa_cache_rt"
    if utility.has_collection(name):
        store.drop_collection(name)
    try:
        store.create_qa_collection(name)
        sources_a = json_lib.dumps([{"index": 1, "title": "BGE-M3 文档", "url": "https://x.com/1"}], ensure_ascii=False)
        sources_b = json_lib.dumps([{"index": 1, "title": "Milvus 文档", "url": "https://x.com/2"}], ensure_ascii=False)
        n = store.add_qa(
            name,
            ["BGE-M3 支持双向量吗？", "Milvus 支持混合检索吗？"],
            ["支持，dense+sparse 双向量。[1]", "支持加权融合检索。[1]"],
            [sources_a, sources_b],
            EmbedResult(dense=[[0.1] * 1024, [0.9] * 1024], sparse=[]),
        )
        assert n == 2

        qvec = EmbedResult(dense=[[0.9] * 1024], sparse=[])  # 语义贴近第二条
        hits = store.search_qa(name, qvec, top_k=2)
        assert len(hits) == 2
        assert hits[0].question == "Milvus 支持混合检索吗？"
        assert hits[0].summary == "支持加权融合检索。[1]"
        assert hits[0].score > 0
        # sources JSON 解析回 Source[]
        assert hits[0].sources[0].url == "https://x.com/2"
        assert hits[0].sources[0].index == 1
    finally:
        store.drop_collection(name)
