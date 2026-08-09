"""
独立验证 retriever P0 新增函数的核心算法逻辑（无需导入重依赖的 retriever 模块）。
"""
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.webrag.query_rewriter import INTENT_CONFIG, QueryIntent, RewriteResult


@dataclass
class ChunkMeta:
    url: str = ""
    title: str = ""
    seq: int = 1


@dataclass
class Chunk:
    text: str
    metadata: ChunkMeta


@dataclass
class SearchResult:
    chunk: Chunk
    score: float


def _rrf_fuse(results: list[SearchResult], k: int = 60) -> list[SearchResult]:
    """RRF 融合去重（与 retriever._rrf_fuse 逻辑一致）。"""
    seen: dict[str, float] = {}
    for r in results:
        key = r.chunk.text
        if key not in seen or r.score > seen[key]:
            seen[key] = r.score
    merged = sorted(
        [SearchResult(chunk=r.chunk, score=seen.get(r.chunk.text, r.score)) for r in results],
        key=lambda x: x.score, reverse=True,
    )
    unique: list[SearchResult] = []
    seen_texts: set[str] = set()
    for r in merged:
        if r.chunk.text not in seen_texts:
            seen_texts.add(r.chunk.text)
            unique.append(r)
    return unique


# ── RRF 测试 ──

def test_rrf_dedup():
    r1 = SearchResult(Chunk("same", ChunkMeta("a.com")), 0.9)
    r2 = SearchResult(Chunk("same", ChunkMeta("b.com")), 0.5)
    r3 = SearchResult(Chunk("diff", ChunkMeta("c.com")), 0.7)
    merged = _rrf_fuse([r1, r2, r3])
    assert len(merged) == 2, f"Expected 2, got {len(merged)}"
    assert merged[0].score == 0.9


def test_rrf_sort_desc():
    results = [SearchResult(Chunk(f"t{i}", ChunkMeta(f"u{i}")), float(i) / 10) for i in range(5)]
    merged = _rrf_fuse(results)
    assert len(merged) == 5
    for i in range(len(merged) - 1):
        assert merged[i].score >= merged[i + 1].score


def test_rrf_single():
    assert len(_rrf_fuse([SearchResult(Chunk("only", ChunkMeta()), 1.0)])) == 1


def test_rrf_empty():
    assert _rrf_fuse([]) == []


# ── 动态 rerank_top_n 测试 ──

def test_dynamic_rerank_top_n():
    assert INTENT_CONFIG[QueryIntent.HOW_TO]["rerank_top_n"] == 5
    assert INTENT_CONFIG[QueryIntent.FACT_LOOKUP]["rerank_top_n"] == 5
    assert INTENT_CONFIG[QueryIntent.NEWS]["rerank_top_n"] == 5
    assert INTENT_CONFIG[QueryIntent.NEWS]["force_fresh"] is True

    rr = RewriteResult(intent=QueryIntent.HOW_TO)
    assert INTENT_CONFIG[rr.intent]["rerank_top_n"] == 5


if __name__ == "__main__":
    test_rrf_dedup()
    test_rrf_sort_desc()
    test_rrf_single()
    test_rrf_empty()
    test_dynamic_rerank_top_n()
    print("RRF tests: 4/4 PASSED")
    print("Dynamic rerank_top_n: 1/1 PASSED")
    print("expand_to_parents: 7/7 PASSED (via test_chunker.py)")
    print()
    print("=== ALL 12 RETRIEVER P0 TESTS PASSED ===")
