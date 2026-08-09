"""logger 结构化日志系统单测：JSON 格式、文件写入、请求级指标聚合、命中率统计、上下文绑定。"""

import json
import logging
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.webrag.logger import (
    JsonFormatter,
    LogConfig,
    RequestMetrics,
    bind_request,
    current_metrics,
    get_logger,
    new_request_id,
    registry,
    setup_logging,
    unbind_request,
)


@pytest.fixture(autouse=True)
def _clean_logging():
    """隔离：重置全局统计与请求上下文，避免用例间相互污染。"""
    registry.reset()
    unbind_request()
    yield
    registry.reset()
    unbind_request()


def _read_log(tmp_path) -> list[dict]:
    """读取 tmp_path/app.log 全部事件。"""
    return [json.loads(line) for line in (tmp_path / "app.log").read_text(encoding="utf-8").splitlines()]


# ---- JSON 格式 ----


def test_json_formatter_emits_parseable_json():
    fmt = JsonFormatter()
    record = logging.LogRecord(
        name="webrag.test", level=logging.INFO, pathname="", lineno=0, msg="test.event", args=(), exc_info=None,
    )
    record.fields = {"k": 1, "中文": "值"}
    payload = json.loads(fmt.format(record))
    assert payload["event"] == "test.event"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "webrag.test"
    assert payload["k"] == 1
    assert payload["中文"] == "值"
    assert "ts" in payload
    datetime.fromisoformat(payload["ts"])  # ISO8601 可解析


# ---- 文件写入 + 请求上下文 ----


def test_setup_logging_writes_jsonl(tmp_path):
    setup_logging(LogConfig(log_dir=tmp_path, console=False, stats_interval=1000), force=True)
    get_logger("unittest").info("unit.hello", extra={"fields": {"n": 42}})
    get_logger("unittest").warning("unit.warn", extra={"fields": {"error": "boom"}})
    events = _read_log(tmp_path)
    assert [e["event"] for e in events] == ["unit.hello", "unit.warn"]
    assert events[0]["n"] == 42
    assert events[0]["logger"] == "webrag.unittest"
    assert events[1]["level"] == "WARNING"


def test_request_context_bound_and_truncated(tmp_path):
    setup_logging(LogConfig(log_dir=tmp_path, console=False, stats_interval=1000), force=True)
    rid = new_request_id()
    long_question = "BGE-M3 是阿里巴巴开源的多语言文本嵌入模型。" * 10  # > 120 字符
    bind_request(rid, long_question)
    try:
        get_logger("unittest").info("unit.ctx", extra={"fields": {}})
    finally:
        unbind_request()
    payload = json.loads((tmp_path / "app.log").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["request_id"] == rid
    assert len(payload["question"]) <= 120  # 隐私截断
    # 解绑后不再携带上下文
    get_logger("unittest").info("unit.nocontext", extra={"fields": {}})
    last = json.loads((tmp_path / "app.log").read_text(encoding="utf-8").splitlines()[-1])
    assert "request_id" not in last
    assert "question" not in last


# ---- 请求级指标聚合 ----


def test_request_metrics_aggregates(tmp_path):
    setup_logging(LogConfig(log_dir=tmp_path, console=False, stats_interval=1000), force=True)
    registry.reset()
    m = RequestMetrics(new_request_id(), "测试问题", endpoint="ask", use_web_search=True, web_top_n=3)
    m.mark("rewrite")
    m.set_cache(False)
    m.mark("cache_lookup")
    m.record_llm("complete", 1500.0, prompt_tokens=100, completion_tokens=20)
    m.mark("generate")
    m.record_llm("generate", 8000.0, ttft_ms=1200.5, prompt_tokens=900, completion_tokens=300)
    m.set_retrieval(5)
    m.answer_len = 123
    m.set_outcome("success")
    summary = m.finish()
    assert summary["endpoint"] == "ask"
    assert summary["outcome"] == "success"
    assert summary["duration_ms"] >= 0
    assert {"rewrite_ms", "cache_lookup_ms", "generate_ms"} <= set(summary["segments_ms"])
    assert summary["tokens"] == {"prompt": 1000, "completion": 320, "total": 1320}
    assert summary["cached"] is False
    assert summary["retrieval_hits"] == 5
    assert summary["answer_len"] == 123
    assert summary["ttft_ms"] is None  # 非流式无首 token 时间
    assert summary["llm_calls"][0]["call"] == "complete"
    assert summary["llm_calls"][1]["ttft_ms"] == 1200.5
    # finish 落一条 ask.completed 事件
    assert _read_log(tmp_path)[-1]["event"] == "ask.completed"
    # 全局统计已更新
    assert registry.requests == 1
    assert registry.cache_misses == 1
    assert registry.cache_hits == 0


def test_request_metrics_cache_hit_outcome():
    m = RequestMetrics(new_request_id(), "q", endpoint="ask.stream")
    m.set_cache(True, score=0.95)
    m.set_outcome("cache_hit")
    m.note_first_token()
    summary = m.summary()
    assert summary["cached"] is True
    assert summary["cache_score"] == 0.95
    assert summary["ttft_ms"] is not None  # 流式有首 token 时间
    assert summary["outcome"] == "cache_hit"


def test_current_metrics_bound_and_cleared():
    assert current_metrics() is None
    m = RequestMetrics(new_request_id(), "q")
    bind_request(m.request_id, "q", m)
    try:
        assert current_metrics() is m
    finally:
        unbind_request()
    assert current_metrics() is None


def test_llm_record_feeds_current_metrics(tmp_path):
    """llm 模块 _record 的指标自动归入当前请求（不联网，仅测链路）。"""
    from src.webrag.llm import DeepSeekClient

    setup_logging(LogConfig(log_dir=tmp_path, console=False, stats_interval=1000), force=True)
    m = RequestMetrics(new_request_id(), "q")
    bind_request(m.request_id, "q", m)
    try:
        client = DeepSeekClient(api_key="sk-test")
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        client._record("complete", time.monotonic(), usage=usage)
        assert m.llm_calls and m.llm_calls[0]["prompt_tokens"] == 10
        assert m.llm_calls[0]["call"] == "complete"
    finally:
        unbind_request()
    # 独立事件也落盘
    assert _read_log(tmp_path)[-1]["event"] == "llm.call"


# ---- 全局统计（命中率等） ----


def test_stats_registry_hit_rate_and_periodic(tmp_path):
    setup_logging(LogConfig(log_dir=tmp_path, console=False, stats_interval=2), force=True)
    registry.reset()
    for hit in (True, True, False):
        m = RequestMetrics(new_request_id(), "q", endpoint="ask")
        m.set_cache(hit)
        m.set_outcome("cache_hit" if hit else "success")
        m.finish()
    snap = registry.snapshot()
    assert snap["requests"] == 3
    assert snap["cache_hits"] == 2
    assert snap["cache_misses"] == 1
    assert snap["cache_hit_rate"] == pytest.approx(2 / 3, abs=1e-4)  # snapshot 四舍五入到 4 位
    assert snap["empty_rate"] == 0.0
    # interval=2 → 第 2 个请求后输出 1 条 stats.periodic（当时 2 次全命中）
    periodic = [e for e in _read_log(tmp_path) if e["event"] == "stats.periodic"]
    assert len(periodic) == 1
    assert periodic[0]["cache_hit_rate"] == 1.0


def test_stats_registry_error_and_empty_tracking():
    registry.reset()
    for outcome, cached in (("empty", False), ("error:LLM_FAILED", None), ("success", True)):
        m = RequestMetrics(new_request_id(), "q")
        if cached is not None:
            m.set_cache(cached)
        m.set_outcome(outcome)
        m.finish()
    snap = registry.snapshot()
    assert snap["empty_count"] == 1
    assert snap["error_count"] == 1
    assert snap["errors"] == {"error:LLM_FAILED": 1}
    # 命中率分母 = 有缓存判定的请求：empty（miss）与 success（hit）→ 2 个判定，命中 1 次；
    # error:LLM_FAILED 未走缓存路径（cached=None），不计入分母
    assert snap["cache_hit_rate"] == 0.5
