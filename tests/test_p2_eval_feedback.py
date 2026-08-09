"""P2 评测与反馈闭环测试：评测集加载 → 检索评估 → 生成评估 → 反馈存储 → 统计分析。"""
from __future__ import annotations

import json

# 测试需要模块路径
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.run_eval import (
    QARecord,
    _any_domain_hit,
    _keyword_hit_count,
    compute_by_intent,
    compute_mrr_single,
    compute_recall,
    evaluate_generation,
    evaluate_retrieval,
    load_qa_set,
)

# ── 模块导入验证 ──


def test_qa_set_loads():
    """验证评测集 JSON 格式有效且条目足够。"""
    path = Path(__file__).resolve().parent.parent / "eval" / "qa_set.json"
    qas = load_qa_set(path)
    assert len(qas) >= 30, f"评测集不足 30 条（实际 {len(qas)}）"

    # 检查五类意图都有覆盖
    types = {q.type for q in qas}
    expected = {"fact_lookup", "how_to", "news", "comparison", "opinion"}
    assert types == expected, f"意图类型不全：{types}"

    # 检查每个 QA 都有 id
    ids = [q.id for q in qas]
    assert len(set(ids)) == len(ids), "QA id 重复"


# ── 域名命中 ──


def test_domain_hit_exact():
    assert _any_domain_hit(["wikipedia.org"], ["https://zh.wikipedia.org/wiki/RAG"]) is True


def test_domain_hit_case_insensitive():
    assert _any_domain_hit(["WiKiPeDiA.oRg"], ["https://zh.Wikipedia.org/"]) is True


def test_domain_hit_miss():
    assert _any_domain_hit(["python.org"], ["https://example.com/python"]) is False


def test_domain_hit_empty_expected():
    """无期望来源 → 不扣分，总是命中。"""
    assert _any_domain_hit([], ["https://any.com"]) is True


def test_domain_hit_multiple_expected():
    assert _any_domain_hit(["redhat.com", "docker.com"], ["https://www.docker.com/resources"]) is True


# ── Recall ──


def test_recall_k():
    qa = QARecord("t", "test", [], ["python.org"], "fact_lookup")
    urls = ["https://example.com", "https://python.org/doc", "https://other.com"]
    assert compute_recall(qa, urls, 1) is False  # 第 1 个不匹配
    assert compute_recall(qa, urls, 2) is True   # 第 2 个匹配
    assert compute_recall(qa, urls, 3) is True


def test_recall_no_expected():
    qa = QARecord("t", "test", [], [], "fact_lookup")
    assert compute_recall(qa, ["https://example.com"], 1) is True


# ── MRR ──


def test_mrr_first_rank():
    qa = QARecord("t", "test", [], ["python.org"], "fact_lookup")
    assert compute_mrr_single(qa, ["https://python.org"]) == 1.0


def test_mrr_second_rank():
    qa = QARecord("t", "test", [], ["python.org"], "fact_lookup")
    assert compute_mrr_single(qa, ["https://x.com", "https://python.org"]) == 0.5


def test_mrr_miss():
    qa = QARecord("t", "test", [], ["python.org"], "fact_lookup")
    assert compute_mrr_single(qa, ["https://x.com", "https://y.com"]) == 0.0


def test_mrr_no_expected():
    qa = QARecord("t", "test", [], [], "fact_lookup")
    assert compute_mrr_single(qa, []) == 1.0


# ── 关键词命中 ──


def test_keyword_hit_all():
    hit, total = _keyword_hit_count("Python是一种非常优秀的编程语言", ["Python", "编程语言"])
    assert hit == 2 and total == 2


def test_keyword_hit_partial():
    hit, total = _keyword_hit_count("Python适合数据分析和机器学习", ["Python", "编程语言", "机器学习"])
    assert hit == 2 and total == 3


def test_keyword_case_insensitive():
    hit, total = _keyword_hit_count("使用PYTHON开发", ["python"])
    assert hit == 1 and total == 1


def test_keyword_empty():
    hit, total = _keyword_hit_count("任意回答", [])
    assert hit == 0 and total == 0


def test_keyword_miss():
    hit, total = _keyword_hit_count("Java是一种编程语言", ["Python"])
    assert hit == 0 and total == 1


# ── 检索评估 ──


def test_evaluate_retrieval_full():
    qas = [
        QARecord("1", "q1", [], ["python.org"], "fact"),
        QARecord("2", "q2", [], ["docker.com"], "how_to"),
        QARecord("3", "q3", [], [], "news"),
        QARecord("4", "q4", [], ["python.org"], "fact"),
    ]
    results = {
        "1": ["https://python.org"],
        "2": ["https://docker.com/doc", "https://docker.com/blog"],
        "3": ["https://some-news.com"],
        "4": ["https://wrong.com", "https://also-wrong.com", "https://python.org/doc"],
    }
    metrics = evaluate_retrieval(qas, results)
    assert metrics.total_queries == 4
    assert metrics.recall_at_1 >= 0.5  # 1,2 命中
    assert metrics.recall_at_3 == 1.0  # 4 在第三位命中（3 无期望来源永远 True）
    assert metrics.mrr >= 0.7


# ── 生成评估 ──


def test_evaluate_generation():
    qas = [
        QARecord("1", "q1", ["Python", "编程"], [], "fact"),
        QARecord("2", "q2", ["Docker", "容器"], [], "how_to"),
    ]
    answers = {
        "1": "Python是一种很棒的编程语言",
        "2": "Docker是一个容器平台",
    }
    metrics = evaluate_generation(qas, answers)
    assert metrics.total_keywords == 4
    assert metrics.hit_keywords == 4
    assert metrics.keyword_precision == 1.0


def test_evaluate_generation_partial():
    qas = [
        QARecord("1", "q1", ["Python", "Java"], [], "fact"),
    ]
    answers = {"1": "Python是一门好语言"}
    metrics = evaluate_generation(qas, answers)
    assert metrics.keyword_precision == 0.5
    assert metrics.hit_keywords == 1
    assert metrics.total_keywords == 2


# ── 分意图统计 ──


def test_compute_by_intent():
    qas = [
        QARecord("1", "q1", ["a"], ["x.com"], "fact"),
        QARecord("2", "q2", ["b", "c"], ["y.com"], "how_to"),
        QARecord("3", "q3", [], [], "fact"),
    ]
    results = {"1": ["https://x.com"], "2": [], "3": []}
    answers = {"1": "has a", "2": "has b", "3": ""}
    stats = compute_by_intent(qas, results, answers)
    assert "fact" in stats
    assert "how_to" in stats
    assert stats["fact"]["count"] == 2
    assert stats["how_to"]["count"] == 1


# ── 反馈存储 ──


def test_feedback_save_and_load():
    from src.webrag.feedback_store import (
        compute_stats,
        load_all_feedback,
        save_feedback,
    )
    from src.webrag.schemas import FeedbackRequest

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test_feedback.jsonl"

        # 写入 2 条 good + 1 条 bad
        save_feedback(FeedbackRequest(
            question="Python怎么安装？", answer="用pip安装。",
            sources=[], feedback_type="good", cached=False,
        ), path=path)
        save_feedback(FeedbackRequest(
            question="什么是RAG？", answer="检索增强生成。",
            sources=[], feedback_type="good", cached=True,
        ), path=path)
        save_feedback(FeedbackRequest(
            question="xxx", answer="???",
            sources=[], feedback_type="bad", cached=False,
        ), path=path)

        records = load_all_feedback(path)
        assert len(records) == 3
        assert records[0].feedback_type == "good"
        assert records[2].feedback_type == "bad"

        stats = compute_stats(path)
        assert stats.total == 3
        assert stats.good == 2
        assert stats.bad == 1
        assert stats.good_rate == pytest.approx(2 / 3)
        assert stats.by_model["cached"]["good"] == 1
        assert stats.by_model["cached"]["bad"] == 0
        assert stats.by_model["fresh"]["good"] == 1
        assert stats.by_model["fresh"]["bad"] == 1
        assert len(stats.recent_bad) == 1


def test_feedback_empty_stats():
    from src.webrag.feedback_store import compute_stats

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nonexistent.jsonl"
        stats = compute_stats(path)
        assert stats.total == 0
        assert stats.good_rate == 0.0


def test_feedback_export():
    from src.webrag.feedback_store import export_stats_json, save_feedback
    from src.webrag.schemas import FeedbackRequest

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "f.jsonl"
        out_dir = Path(tmp) / "reports"
        save_feedback(FeedbackRequest(
            question="Q", answer="A", sources=[],
            feedback_type="good", cached=False,
        ), path=path)

        exported = export_stats_json(path, out_dir)
        assert exported.exists()
        data = json.loads(exported.read_text())
        assert data["total"] == 1
        assert data["good_rate"] == 1.0


# ── Schema 验证 ──


def test_feedback_request_validation():
    import pydantic

    from src.webrag.schemas import FeedbackRequest

    # 合法请求
    req = FeedbackRequest(question="Q", answer="A", feedback_type="good")
    assert req.feedback_type == "good"

    req2 = FeedbackRequest(question="Q", answer="A", feedback_type="bad")
    assert req2.feedback_type == "bad"

    # 非法 feedback_type
    with pytest.raises(pydantic.ValidationError):
        FeedbackRequest(question="Q", answer="A", feedback_type="other")

    # 空问题
    with pytest.raises(pydantic.ValidationError):
        FeedbackRequest(question="", answer="A", feedback_type="good")


def test_feedback_stats_model():
    from src.webrag.schemas import FeedbackStats
    stats = FeedbackStats(total=10, good=8, bad=2, good_rate=0.8)
    d = stats.model_dump()
    assert d["total"] == 10
    assert d["good"] == 8
    assert d["good_rate"] == 0.8


# ── 评测脚本集成（mock 模式） ──


def test_mock_eval_run():
    """验证 mock 模式评测脚本能正常运行并产生报告。"""
    from eval.run_eval import QARecord

    qas = [
        QARecord("t1", "test1", ["Python"], ["python.org"], "fact"),
        QARecord("t2", "test2", ["Docker"], ["docker.com"], "how_to"),
        QARecord("t3", "test3", [], [], "news"),
    ]

    # 用少量数据测试核心流程（避免依赖 _MOCK_URLS）
    results = {"t1": ["https://python.org"], "t2": ["https://docker.com"], "t3": []}
    answers = {"t1": "Python很棒", "t2": "Docker很好", "t3": "无答案"}

    from eval.run_eval import compute_by_intent, evaluate_generation, evaluate_retrieval
    r = evaluate_retrieval(qas, results)
    g = evaluate_generation(qas, answers)
    bi = compute_by_intent(qas, results, answers)

    assert r.total_queries == 3
    assert r.recall_at_1 >= 2 / 3
    assert g.keyword_precision == 1.0
    assert len(bi) == 3  # fact + how_to + news
