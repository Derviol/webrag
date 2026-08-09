"""retriever 单测：问答缓存检索/落库 + 联网链路时延控制 + 重排阈值（离线，mock 依赖）。"""

import time
from types import SimpleNamespace

from src.webrag import retriever
from src.webrag.query_rewriter import QueryIntent, RewriteResult
from src.webrag.schemas import (
    AskResponse,
    Chunk,
    ChunkMetadata,
    Document,
    QAHit,
    SearchHit,
    SearchResult,
    Source,
)

# ---- 通用 fake ----

def _qa_hit(score=0.9, question="BGE-M3 支持双向量吗？", summary="支持。[1]", url="https://x.com/1") -> QAHit:
    return QAHit(
        question=question,
        summary=summary,
        sources=[Source(index=1, title="t1", url=url)],
        score=score,
    )


def _qa_settings(**overrides) -> SimpleNamespace:
    """lookup/save 所需的最小 settings（SimpleNamespace，避免依赖真实配置）。"""
    base = {
        "retriever": SimpleNamespace(
            qa_top_k=3,
            qa_min_score=0.8,
            top_k=8,
            enable_rerank=True,
            rerank_top_n=3,
            rerank_min_score=0.6,
            max_chunks_per_page=12,
            max_web_chunks_total=24,
        ),
        "crawler": SimpleNamespace(top_urls=5, request_timeout_seconds=3, request_delay_seconds=0),
        "chunker": SimpleNamespace(chunk_size=512, overlap=64, respect_paragraph=True),
        "search_provider": "bing",
        "search_api_key": "",
    }
    return SimpleNamespace(**{**base, **overrides})


class _FakeEmbedder:
    def __init__(self):
        self.embed_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        return SimpleNamespace(dense=[[0.0] * 4] * len(texts), sparse=[{}] * len(texts))


class _FakeStore:
    def __init__(self, qa_hits=None, add_fail=False):
        self.qa_hits = qa_hits or []
        self.search_qa_calls = 0
        self.add_calls: list[tuple] = []
        self.add_fail = add_fail

    def search_qa(self, collection, vectors, top_k):
        self.search_qa_calls += 1
        return self.qa_hits

    def add_qa(self, collection, questions, summaries, sources_json, vectors):
        if self.add_fail:
            raise RuntimeError("milvus down")
        self.add_calls.append((questions, summaries, sources_json))
        return len(questions)


# ---- 问答缓存检索 lookup_qa_cache ----

def test_lookup_qa_cache_hit_above_threshold():
    store = _FakeStore(qa_hits=[_qa_hit(score=0.93)])
    hit, qvec = retriever.lookup_qa_cache(
        "BGE-M3 支持双向量吗？", store, _FakeEmbedder(), "webrag_qa", _qa_settings()
    )
    assert hit is not None
    assert hit.summary == "支持。[1]"
    assert hit.sources[0].url == "https://x.com/1"
    assert qvec is not None  # 问题向量复用于联网检索


def test_lookup_qa_cache_below_threshold_misses():
    store = _FakeStore(qa_hits=[_qa_hit(score=0.5)])  # < qa_min_score=0.8
    hit, qvec = retriever.lookup_qa_cache("完全无关的问题", store, _FakeEmbedder(), "webrag_qa", _qa_settings())
    assert hit is None
    assert qvec is not None


def test_lookup_qa_cache_empty_collection_misses():
    hit, _ = retriever.lookup_qa_cache("问题", _FakeStore(qa_hits=[]), _FakeEmbedder(), "webrag_qa", _qa_settings())
    assert hit is None


def test_lookup_qa_cache_store_error_degrades_to_miss():
    """缓存库不可用（异常）→ (None, qvec) 降级联网，不抛异常。"""

    class BoomStore(_FakeStore):
        def search_qa(self, collection, vectors, top_k):
            raise RuntimeError("collection not found")

    hit, qvec = retriever.lookup_qa_cache("问题", BoomStore(), _FakeEmbedder(), "webrag_qa", _qa_settings())
    assert hit is None
    assert qvec is not None  # 嵌入结果仍返回（联网检索复用）


def test_lookup_qa_cache_reports_progress():
    msgs: list[str] = []
    retriever.lookup_qa_cache(
        "问题", _FakeStore(qa_hits=[_qa_hit(score=0.9)]), _FakeEmbedder(), "webrag_qa",
        _qa_settings(), progress=msgs.append,
    )
    assert msgs[0] == "正在检索历史问答缓存…"
    assert any("命中历史问答缓存" in m for m in msgs)


# ---- 缓存落库 save_qa_record ----

def _resp(answer="带来源回答[1]。", sources=None, direct=False) -> AskResponse:
    return AskResponse(
        answer=answer,
        sources=sources if sources is not None else [Source(index=1, title="t", url="https://x.com/1")],
        direct=direct,
    )


def test_save_qa_record_stores_question_summary_sources():
    store = _FakeStore()
    ok = retriever.save_qa_record(
        "用户问题", _resp(), store, _FakeEmbedder(), "webrag_qa", _qa_settings(),
        qvec=SimpleNamespace(dense=[[0.0] * 4], sparse=[{}]),
    )
    assert ok is True
    assert len(store.add_calls) == 1
    questions, summaries, sources_json = store.add_calls[0]
    assert questions == ["用户问题"]
    assert summaries == ["带来源回答[1]。"]
    assert '"url": "https://x.com/1"' in sources_json[0]  # 来源序列化为 JSON 存储


def test_save_qa_record_skips_direct_answer_without_sources():
    """直答兜底（无来源）不入缓存。"""
    store = _FakeStore()
    ok = retriever.save_qa_record(
        "问题", _resp(sources=[], direct=True), store, _FakeEmbedder(), "webrag_qa", _qa_settings()
    )
    assert ok is False
    assert store.add_calls == []


def test_save_qa_record_skips_empty_answer():
    store = _FakeStore()
    ok = retriever.save_qa_record("问题", _resp(answer=""), store, _FakeEmbedder(), "webrag_qa", _qa_settings())
    assert ok is False


def test_save_qa_record_failure_does_not_raise():
    ok = retriever.save_qa_record("问题", _resp(), _FakeStore(add_fail=True), _FakeEmbedder(), "webrag_qa", _qa_settings())
    assert ok is False  # best-effort：失败仅告警，不抛异常


# ---- 联网链路时延控制：总块数封顶 + deadline 预算 ----

def _web_settings(**overrides) -> SimpleNamespace:
    """retrieve_web 所需的最小 settings（SimpleNamespace，避免依赖真实配置）。"""
    base = {
        "search_provider": "bing",
        "search_api_key": "",
        "crawler": SimpleNamespace(
            top_urls=5, request_timeout_seconds=3, request_delay_seconds=0,
        ),
        "chunker": SimpleNamespace(chunk_size=512, overlap=64, respect_paragraph=True, enable_two_level=False),
        "retriever": SimpleNamespace(max_web_chunks_total=2, max_chunks_per_page=4),
    }
    return SimpleNamespace(**{**base, **overrides})


class _FakeCrawler:
    def __init__(self, n_pages=2):
        self.hits = [SearchHit(title=f"t{i}", url=f"https://p{i}.com", snippet="s") for i in range(n_pages)]
        self.search_calls = 0
        self.search_kwargs = None  # 最近一次 search 的关键字参数（验证 top_n）
        self.fetch_urls: list[str] = []  # 最近一次 fetch_many 的 URL 列表（验证抓取页数）

    def search(self, *a, **k):
        self.search_calls += 1
        self.search_query = a[0] if a else None  # 最近一次搜索的 query（验证时间锚定改写）
        self.search_kwargs = k
        return self.hits

    def fetch_many(self, urls, **k):
        self.fetch_urls = list(urls)
        return [(u, "<html>") for u in urls]


class _FakeWebStore:
    def __init__(self):
        self.added: list[tuple[str, int]] = []

    def create_collection(self, name):
        pass

    def has_collection(self, name):
        return False

    def add(self, name, chunks, vectors):
        self.added.append((name, len(chunks)))
        return len(chunks)

    def search(self, name, qvec, top_k, dense_weight=0.5, sparse_weight=0.5):
        return [SearchResult(chunk=Chunk(text="x", metadata=ChunkMetadata(url="https://p0.com", title="t0")), score=1.0)]

    def drop_collection(self, name):
        pass


def _patch_web_deps(monkeypatch, crawler: _FakeCrawler):
    monkeypatch.setattr(retriever, "crawler", crawler)

    def fake_parse(html, url):
        return Document(title=f"T-{url}", text="正文内容。" * 200, url=url)

    def fake_chunk(doc, **k):
        return [Chunk(text=f"{doc.url}#{i}", metadata=ChunkMetadata(url=doc.url, title=doc.title, seq=i + 1)) for i in range(4)]

    monkeypatch.setattr(retriever, "parser", SimpleNamespace(parse=fake_parse))
    monkeypatch.setattr(retriever, "chunker", SimpleNamespace(chunk=fake_chunk))
    # retrieve_web 内部 from pymilvus import utility → 需要跳过真实连接
    monkeypatch.setattr("pymilvus.utility", SimpleNamespace(has_collection=lambda n: False))


def test_retrieve_web_reports_progress(monkeypatch):
    """联网链路进度：搜索 → 抓取（页数）→ 清洗嵌入（i/N）→ 临时库检索 按序上报。"""
    crawler = _FakeCrawler(n_pages=2)
    _patch_web_deps(monkeypatch, crawler)
    store, embedder = _FakeWebStore(), _FakeEmbedder()
    settings = _web_settings()
    qvec = SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])
    msgs: list[str] = []

    retriever.retrieve_web("问题", store, embedder, settings, top_k=4, qvec=qvec, progress=msgs.append)

    assert msgs[0] == "正在联网搜索…"
    assert "正在抓取网页（共 2 页）…" in msgs
    assert any("正在清洗并嵌入网页内容" in m for m in msgs)
    assert msgs[-1] == "正在检索临时网页库…"


def test_retrieve_web_caps_total_chunks(monkeypatch):
    """联网链路嵌入总块数封顶：2 页×4 块，max_web_chunks_total=2 → 只嵌入第一页前 2 块。"""
    crawler = _FakeCrawler(n_pages=2)
    _patch_web_deps(monkeypatch, crawler)
    store, embedder = _FakeWebStore(), _FakeEmbedder()
    settings = _web_settings()
    qvec = SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])

    retriever.retrieve_web("问题", store, embedder, settings, top_k=4, qvec=qvec)

    assert crawler.search_calls == 1
    assert embedder.embed_calls == 1  # 整页只嵌一次（4 块截断到 2 块）
    assert len(store.added) == 1
    assert store.added[0][1] == 2


def test_retrieve_web_respects_deadline(monkeypatch):
    """deadline 已过：联网链路立即收尾，不嵌入任何块（给 LLM 留预算）。"""
    crawler = _FakeCrawler(n_pages=2)
    _patch_web_deps(monkeypatch, crawler)
    store, embedder = _FakeWebStore(), _FakeEmbedder()
    settings = _web_settings()
    qvec = SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])

    results = retriever.retrieve_web(
        "问题", store, embedder, settings, top_k=4, qvec=qvec, deadline=time.monotonic() - 10
    )
    assert results == []  # 预算不足直接返回空
    assert embedder.embed_calls == 0  # 一块都没嵌入
    assert store.added == []


def test_retrieve_web_honors_web_top_n(monkeypatch):
    """请求级 web_top_n：搜索 top_n 与抓取页数上限按请求覆盖（5 条命中 → 只搜索/抓 3 页）。"""
    crawler = _FakeCrawler(n_pages=5)
    _patch_web_deps(monkeypatch, crawler)
    store, embedder = _FakeWebStore(), _FakeEmbedder()
    settings = _web_settings()
    qvec = SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])

    retriever.retrieve_web("问题", store, embedder, settings, top_k=4, qvec=qvec, web_top_n=3)

    assert crawler.search_kwargs["top_n"] == 3  # 搜索只取 3 条
    assert len(crawler.fetch_urls) == 3  # 抓取页数 ≤ web_top_n


def test_retrieve_web_caps_web_top_n_at_20(monkeypatch):
    """web_top_n 超上限（99）：防御性封顶为 20；抓取页数 ≤ min(命中数, 20)。"""
    crawler = _FakeCrawler(n_pages=5)
    _patch_web_deps(monkeypatch, crawler)
    store, embedder = _FakeWebStore(), _FakeEmbedder()
    settings = _web_settings()
    qvec = SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])

    retriever.retrieve_web("问题", store, embedder, settings, top_k=4, qvec=qvec, web_top_n=99)

    assert crawler.search_kwargs["top_n"] == 20  # 封顶 20
    assert len(crawler.fetch_urls) == 5  # 命中只有 5 页 → 全部抓取


def test_retrieve_web_time_aware_anchors_search_query(monkeypatch):
    """时效性问题（近日/近期…）：联网搜索词拼入本地当前时间，提高时效内容命中率。"""
    crawler = _FakeCrawler(n_pages=2)
    _patch_web_deps(monkeypatch, crawler)
    store, embedder = _FakeWebStore(), _FakeEmbedder()
    settings = _web_settings()
    qvec = SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])
    rewrite_result = RewriteResult(intent=QueryIntent.NEWS, time_aware=True, time_context="2026年8月8日")

    retriever.retrieve_web(
        "近日股市表现", store, embedder, settings, top_k=4,
        qvec=qvec, rewrite_result=rewrite_result,
    )

    assert crawler.search_query == "近日股市表现（2026年8月8日）"


def test_retrieve_web_non_time_query_keeps_original(monkeypatch):
    """非时效性问题：联网搜索词保持原始问题，不注入时间。"""
    crawler = _FakeCrawler(n_pages=2)
    _patch_web_deps(monkeypatch, crawler)
    store, embedder = _FakeWebStore(), _FakeEmbedder()
    settings = _web_settings()
    qvec = SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])

    retriever.retrieve_web(
        "BGE-M3 支持双向量吗？", store, embedder, settings, top_k=4,
        qvec=qvec, rewrite_result=RewriteResult(intent=QueryIntent.GENERAL),
    )

    assert crawler.search_query == "BGE-M3 支持双向量吗？"


# ---- 本地知识库检索 retrieve_offline ----


class _OfflineFakeStore:
    """离线库检索所需的最小 store（has_collection + search）。"""

    def __init__(self, has=True, results=None, fail=False):
        self.has = has
        self.results = results or []
        self.fail = fail
        self.search_kwargs = None  # (collection, top_k, dense_weight, sparse_weight)

    def has_collection(self, name):
        return self.has

    def search(self, collection, qvec, top_k, dense_weight=0.5, sparse_weight=0.5):
        if self.fail:
            raise RuntimeError("milvus down")
        self.search_kwargs = (collection, top_k, dense_weight, sparse_weight)
        return self.results


def _offline_settings(**overrides) -> SimpleNamespace:
    base = {"milvus_offline_collection": "webrag_offline_kb"}
    return SimpleNamespace(**{**base, **overrides})


def test_retrieve_offline_hit_returns_results():
    r = SearchResult(chunk=Chunk(text="内部文档", metadata=ChunkMetadata(url="offline://x", title="T")), score=0.9)
    store = _OfflineFakeStore(results=[r])
    out = retriever.retrieve_offline("问题", store, _FakeEmbedder(), _offline_settings(), top_k=5)
    assert out == [r]
    assert store.search_kwargs[0] == "webrag_offline_kb"
    assert store.search_kwargs[1] == 5
    assert store.search_kwargs[2] == 0.5  # 默认 dense 权重


def test_retrieve_offline_collection_missing_returns_empty():
    """离线库未建：返回 []（main 据此返回「信息不足」），不触发检索。"""
    store = _OfflineFakeStore(has=False)
    out = retriever.retrieve_offline("问题", store, _FakeEmbedder(), _offline_settings(), top_k=5)
    assert out == []
    assert store.search_kwargs is None


def test_retrieve_offline_store_error_returns_empty():
    """Milvus 异常 → []（本地库故障绝不抛 5xx，main 降级为「信息不足」）。"""
    store = _OfflineFakeStore(fail=True)
    out = retriever.retrieve_offline("问题", store, _FakeEmbedder(), _offline_settings(), top_k=5)
    assert out == []


def test_retrieve_offline_reuses_qvec():
    """qvec 复用：问答缓存已嵌入 → 不重复嵌入（省一次 CPU 嵌入）。"""
    embedder = _FakeEmbedder()
    store = _OfflineFakeStore(results=[])
    qvec = SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])
    retriever.retrieve_offline("问题", store, embedder, _offline_settings(), top_k=5, qvec=qvec)
    assert embedder.embed_calls == 0


def test_retrieve_offline_reports_progress():
    msgs: list[str] = []
    store = _OfflineFakeStore(results=[])
    retriever.retrieve_offline("问题", store, _FakeEmbedder(), _offline_settings(), top_k=5, progress=msgs.append)
    assert msgs[0] == "正在检索本地知识库…"


def test_retrieve_offline_dynamic_weights_with_rewrite():
    """P0 动态权重：与联网链路一致，按意图调整 dense/sparse 比例。"""
    rr = RewriteResult(intent=QueryIntent.HOW_TO)
    store = _OfflineFakeStore(results=[])
    retriever.retrieve_offline("问题", store, _FakeEmbedder(), _offline_settings(), top_k=5, rewrite_result=rr)
    want = retriever.INTENT_CONFIG[QueryIntent.HOW_TO]
    assert store.search_kwargs[2] == want["dense_weight"]
    assert store.search_kwargs[3] == want["sparse_weight"]


# ---- 重排 ----

def test_rerank_filters_below_threshold(monkeypatch):
    class FakeReranker:
        def predict(self, pairs, **kwargs):
            return [0.9, 0.3, 0.05]

    monkeypatch.setattr(retriever, "_get_reranker", lambda: FakeReranker())
    settings = SimpleNamespace(retriever=SimpleNamespace(rerank_top_n=3, rerank_min_score=0.6))
    results = [
        SearchResult(chunk=Chunk(text=f"c{i}", metadata=ChunkMetadata(url=f"https://x.com/{i}")), score=1.0)
        for i in range(3)
    ]
    out = retriever.rerank("q", results, settings)
    assert [r.chunk.metadata.url for r in out] == ["https://x.com/0"]  # 仅 0.9 那条保留
    assert out[0].score == 0.9


def test_rerank_all_below_threshold_returns_empty(monkeypatch):
    class FakeReranker:
        def predict(self, pairs, **kwargs):
            return [0.1, 0.2]

    monkeypatch.setattr(retriever, "_get_reranker", lambda: FakeReranker())
    settings = SimpleNamespace(retriever=SimpleNamespace(rerank_top_n=3, rerank_min_score=0.6))
    results = [
        SearchResult(chunk=Chunk(text="a", metadata=ChunkMetadata(url="https://x.com/1")), score=1.0),
        SearchResult(chunk=Chunk(text="b", metadata=ChunkMetadata(url="https://x.com/2")), score=1.0),
    ]
    assert retriever.rerank("q", results, settings) == []  # 全低于阈值 → 空（EMPTY_RESULT）


def test_rerank_reranker_unavailable_returns_unchanged(monkeypatch):
    """重排模型不可用 → 原样返回（降级不重排）。"""
    monkeypatch.setattr(retriever, "_get_reranker", lambda: None)
    results = [SearchResult(chunk=Chunk(text="a", metadata=ChunkMetadata(url="https://x.com/1")), score=1.0)]
    assert retriever.rerank("q", results, SimpleNamespace(retriever=SimpleNamespace(rerank_top_n=3, rerank_min_score=0.6))) == results


# ── RRF 融合（P0）──


def test_rrf_fuse_deduplicates_by_text():
    """相同文本的 chunk 保留最高分。"""
    r1 = SearchResult(chunk=Chunk(text="一样的文本", metadata=ChunkMetadata(url="https://a.com", title="A")), score=0.9)
    r2 = SearchResult(chunk=Chunk(text="一样的文本", metadata=ChunkMetadata(url="https://b.com", title="B")), score=0.5)
    r3 = SearchResult(chunk=Chunk(text="不同的文本", metadata=ChunkMetadata(url="https://c.com", title="C")), score=0.7)

    merged = retriever._rrf_fuse([r1, r2, r3])
    assert len(merged) == 2
    # 相同的取最高分
    same = [r for r in merged if r.chunk.text == "一样的文本"]
    assert len(same) == 1
    assert same[0].score == 0.9


def test_rrf_fuse_sorted_by_score_desc():
    """按分数降序排列。"""
    results = [
        SearchResult(chunk=Chunk(text=f"t{i}", metadata=ChunkMetadata(url=f"https://u{i}.com")), score=float(i) / 10)
        for i in range(5)
    ]
    merged = retriever._rrf_fuse(results)
    assert len(merged) == 5
    for i in range(len(merged) - 1):
        assert merged[i].score >= merged[i + 1].score


def test_rrf_fuse_empty():
    assert retriever._rrf_fuse([]) == []


def test_rrf_fuse_single_result():
    r = SearchResult(chunk=Chunk(text="唯一", metadata=ChunkMetadata(url="https://x.com")), score=1.0)
    merged = retriever._rrf_fuse([r])
    assert len(merged) == 1
    assert merged[0].chunk.text == "唯一"


# ── rerank 动态 rerank_top_n（P0）──


def test_rerank_with_rewrite_result_dynamic_top_n(monkeypatch):
    """根据意图动态调整 rerank_top_n：HOW_TO=5，而非默认 3。"""

    class FakeReranker:
        def predict(self, pairs, **kwargs):
            return [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]

    monkeypatch.setattr(retriever, "_get_reranker", lambda: FakeReranker())
    settings = SimpleNamespace(retriever=SimpleNamespace(rerank_top_n=3, rerank_min_score=0.3))
    results = [
        SearchResult(chunk=Chunk(text=f"c{i}", metadata=ChunkMetadata(url=f"https://x.com/{i}")), score=1.0)
        for i in range(6)
    ]
    rr = RewriteResult(intent=QueryIntent.HOW_TO)
    out = retriever.rerank("q", results, settings, rewrite_result=rr)
    assert len(out) == 5  # HOW_TO → rerank_top_n=5


def test_rerank_without_rewrite_result_uses_default(monkeypatch):
    """无 rewrite_result 时使用 settings 默认的 rerank_top_n=3。"""

    class FakeReranker:
        def predict(self, pairs, **kwargs):
            return [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]

    monkeypatch.setattr(retriever, "_get_reranker", lambda: FakeReranker())
    settings = SimpleNamespace(retriever=SimpleNamespace(rerank_top_n=3, rerank_min_score=0.3))
    results = [
        SearchResult(chunk=Chunk(text=f"c{i}", metadata=ChunkMetadata(url=f"https://x.com/{i}")), score=1.0)
        for i in range(6)
    ]
    out = retriever.rerank("q", results, settings)
    assert len(out) == 3  # 默认截断


# ── _expand_all_to_parents（P0）──


def test_expand_all_to_parents_integration():
    """测试 _expand_all_to_parents 的完整逻辑。"""
    p0 = Chunk(text="父块A" * 50, metadata=ChunkMetadata(url="https://a.com", title="A"))
    p1 = Chunk(text="父块B" * 50, metadata=ChunkMetadata(url="https://b.com", title="B"))

    c0 = Chunk(text="父块A" * 10, metadata=ChunkMetadata(url="https://a.com", title="A"))
    c1 = Chunk(text="父块A" * 15, metadata=ChunkMetadata(url="https://a.com", title="A"))
    c2 = Chunk(text="父块B" * 12, metadata=ChunkMetadata(url="https://b.com", title="B"))

    all_parent_chunks = {0: [p0, p1]}
    all_child_mappings = {0: {0: 0, 1: 0, 2: 1}}

    from types import SimpleNamespace as SN

    docs = [([c0, c1, c2], SN(dense=[[0.0]], sparse=[{}]))]

    results = [
        SearchResult(chunk=c0, score=0.8),
        SearchResult(chunk=c1, score=0.6),
        SearchResult(chunk=c2, score=0.9),
    ]
    expanded = retriever._expand_all_to_parents(results, docs, all_parent_chunks, all_child_mappings)
    # c0(0.8) 和 c1(0.6) → p0（取 max=0.8），c2(0.9) → p1
    assert len(expanded) == 2
    assert expanded[0].score == 0.9  # p1 的分数更高
    assert "父块B" in expanded[0].chunk.text
    assert expanded[1].score == 0.8  # p0


def test_expand_all_to_parents_no_mapping_returns_original():
    """无映射数据 → 返回原始结果。"""
    docs = [([], object())]
    all_parent_chunks = {}
    all_child_mappings = {}
    results = [SearchResult(chunk=Chunk(text="x", metadata=ChunkMetadata(url="https://x.com")), score=1.0)]
    out = retriever._expand_all_to_parents(results, docs, all_parent_chunks, all_child_mappings)
    assert out == results


# ── QA 缓存 settings 补充 query_rewriter 字段 ──


def test_qa_settings_unchanged():
    """验证 lookup/save 的 fake settings 不被 query_rewriter 字段影响。"""
    settings = _qa_settings()
    hit, qvec = retriever.lookup_qa_cache(
        "测试问题", _FakeStore(qa_hits=[]), _FakeEmbedder(), "webrag_qa", settings
    )
    assert hit is None
    assert qvec is not None
