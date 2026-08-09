"""幻觉检测模块单测：分句、快速检测、解析、各类边界。"""

from src.webrag.hallucination_checker import (
    HallucinationReport,
    SentenceCheck,
    _parse_verify_result,
    _split_sentences,
    check_hallucination_fast,
)
from src.webrag.schemas import Chunk, ChunkMetadata


# ── 分句 ──
def test_split_sentences_single():
    assert _split_sentences("这是一句话。") == ["这是一句话。"]


def test_split_sentences_multi():
    result = _split_sentences("第一句。第二句！第三句？第四句；")
    assert len(result) == 4
    assert result[0] == "第一句。"
    assert result[1] == "第二句！"


def test_split_sentences_empty():
    assert _split_sentences("") == []


def test_split_sentences_no_punctuation():
    result = _split_sentences("没有标点的长句")
    assert len(result) == 1
    assert result[0] == "没有标点的长句"


def test_split_sentences_newlines():
    result = _split_sentences("第一行\n第二行\n第三行")
    assert len(result) == 3


# ── 解析核验结果 ──
def test_parse_verify_result_valid_json():
    raw = '{"sentences": [{"text": "A", "label": "✓", "reason": "OK"}], "hallucination_rate": 0.0, "risk": "none"}'
    sentences, rate, risk = _parse_verify_result(raw)
    assert len(sentences) == 1
    assert sentences[0].label == "✓"
    assert sentences[0].text == "A"
    assert rate == 0.0
    assert risk == "none"


def test_parse_verify_result_with_markdown():
    raw = '```json\n{"sentences": [{"text": "X", "label": "✗", "reason": "not found"}], "hallucination_rate": 1.0, "risk": "high"}\n```'
    sentences, rate, risk = _parse_verify_result(raw)
    assert len(sentences) == 1
    assert sentences[0].label == "✗"
    assert rate == 1.0
    assert risk == "high"


def test_parse_verify_result_no_rate_auto_calc():
    raw = '{"sentences": [{"text": "A", "label": "✓"}, {"text": "B", "label": "✗"}, {"text": "C", "label": "✓"}], "hallucination_rate": 0.0, "risk": "low"}'
    sentences, rate, risk = _parse_verify_result(raw)
    assert len(sentences) == 3
    assert rate == 1.0 / 3  # auto-calculated
    assert risk == "low"


def test_parse_verify_result_invalid():
    sentences, rate, risk = _parse_verify_result("not json at all")
    assert sentences == []
    assert rate == 0.0
    assert risk == "none"


# ── 快速检测 ──
def test_fast_check_empty():
    report = check_hallucination_fast("", [])
    assert not report.has_hallucination
    assert report.error


def test_fast_check_all_in_context():
    chunk = Chunk(
        text="Python 是一种广泛使用的编程语言，由 Guido van Rossum 于 1991 年发布。",
        metadata=ChunkMetadata(url="https://example.com"),
    )
    report = check_hallucination_fast(
        "Python 是一种编程语言。它由 Guido van Rossum 发布。",
        [chunk],
    )
    assert not report.has_hallucination or report.risk == "none"


def test_fast_check_partial_out_of_context():
    chunk = Chunk(
        text="Python 是一种编程语言。",
        metadata=ChunkMetadata(url="https://example.com"),
    )
    report = check_hallucination_fast(
        "Python 是一种编程语言。Java 也是一种编程语言。",
        [chunk],
    )
    # "Java 也是一种编程语言" 的关键词不在上下文中
    # 至少有一个句子可能被标记为风险
    n_sents = len(report.sentences)
    assert n_sents >= 2, f"Expected at least 2 sentences, got {n_sents}"


def test_fast_check_citation_only():
    """纯引用标注不应被标记为幻觉。"""
    chunk = Chunk(text="some content", metadata=ChunkMetadata(url="https://x.com"))
    report = check_hallucination_fast("[1]", [chunk])
    assert report.sentences[0].label == "✓"


# ── HallucinationReport 数据类 ──
def test_report_defaults():
    report = HallucinationReport()
    assert report.hallucination_rate == 0.0
    assert report.risk == "none"
    assert report.sentences == []
    assert not report.has_hallucination


def test_report_with_hallucination():
    report = HallucinationReport(
        hallucination_rate=0.3,
        risk="medium",
        sentences=[SentenceCheck(text="fake", label="✗", reason="not found")],
        has_hallucination=True,
    )
    assert report.has_hallucination
    assert report.risk == "medium"
