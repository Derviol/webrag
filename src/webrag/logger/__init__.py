"""结构化日志系统：JSONL 文件 + 控制台，请求级指标（时间 / 耗时 / 命中率 / token / 首token时间）。

设计约定（docs/logging.md 为权威文档，改动需同步）：
- **零依赖**：stdlib `logging` + `json`，不引入 structlog 等第三方库（pyproject 依赖纪律）；
- **独立模块**：不 import 任何 webrag 内部模块（避免循环依赖），配置以参数传入（duck-typed）；
- **事件式日志**：每条日志一行 JSON，`event` 字段即日志消息（如 ask.completed / llm.call / stats.periodic），
  结构化字段经 `extra={"fields": {...}}` 传入；时间戳 `ts` 为 ISO8601（UTC）；
- **请求上下文**（thread-local）：`request_id` / `question` / 当前 `RequestMetrics`——子线程（如 /ask/stream
  的检索线程）需显式 `bind_request()`，否则其日志不带 request_id；
- **指标归属**：LLM 调用等通过 `current_metrics()` 自动归入当前请求；独立使用（scripts）时退化为独立事件；
- **隐私**：问题文本入日志截断 120 字符。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

_ROOT_LOGGER_NAME = "webrag"
_QUESTION_MAX_LEN = 120  # 隐私：问题入日志的截断长度


# ── 配置（与 config/settings.yaml 的 logging 段对齐；字段名必须一致，main 用字段过滤转换） ──


@dataclass
class LogConfig:
    """日志配置。缺省值保证「不配置也能用」；与 config.py 的 LogSettings 字段一一对应。"""

    level: str = "INFO"
    log_dir: str | Path = "logs"
    file_name: str = "app.log"
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5
    console: bool = True
    console_json: bool = False
    stats_interval: int = 50


# ── Formatter ──


class JsonFormatter(logging.Formatter):
    """结构化 JSON 格式化器：ts / level / logger / event + extra["fields"] + 请求上下文。

    单行 JSON（ensure_ascii=False 保留中文，default=str 兜底非 JSON 类型），
    便于 grep / jq 离线分析；异常以 exception 字段携带堆栈。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            payload.update(fields)
        ctx = _ctx
        if ctx.request_id:
            payload["request_id"] = ctx.request_id
        if ctx.question:
            payload["question"] = ctx.question
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """控制台可读格式（ts level logger event key=value…）；console_json=true 时用 JsonFormatter。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} {record.levelname:<7} {record.name} {record.getMessage()}"
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            line += " | " + " ".join(f"{k}={v}" for k, v in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ── 配置入口（幂等；force=True 用于测试重定向） ──

_setup_lock = threading.Lock()
_setup_done = False


def setup_logging(config: Any | None = None, *, force: bool = False) -> None:
    """配置 webrag.* 根 logger：JSONL 轮转文件（logs/app.log）+ 可选控制台 handler。

    - 幂等：进程内只配置一次（force=True 时重配并替换既有 handler，供测试定向临时目录）；
    - 可传 LogConfig 或 config.py 的 LogSettings（duck-typed，缺省字段用默认值）；
    - 日志目录自动创建；目录/文件不可写时降级为仅控制台，绝不抛异常阻断服务。
    """
    global _setup_done
    cfg = config or LogConfig()
    with _setup_lock:
        if _setup_done and not force:
            return
        _setup_done = True

        root = logging.getLogger(_ROOT_LOGGER_NAME)
        for h in list(root.handlers):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

        root.setLevel(getattr(logging, str(getattr(cfg, "level", "INFO")).upper(), logging.INFO))
        root.propagate = False  # 不重复进 uvicorn 的根日志

        file_handler: logging.Handler | None = None
        try:
            log_dir = Path(getattr(cfg, "log_dir", "logs"))
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / getattr(cfg, "file_name", "app.log"),
                maxBytes=int(getattr(cfg, "max_bytes", 10 * 1024 * 1024)),
                backupCount=int(getattr(cfg, "backup_count", 5)),
                encoding="utf-8",
            )
            file_handler.setFormatter(JsonFormatter())
            root.addHandler(file_handler)
        except Exception:
            file_handler = None  # 目录/权限问题：降级仅控制台

        if getattr(cfg, "console", True):
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(
                JsonFormatter() if getattr(cfg, "console_json", False) else TextFormatter()
            )
            root.addHandler(console_handler)

        interval = int(getattr(cfg, "stats_interval", 50) or 0)
        registry.interval = max(0, interval)


def get_logger(name: str) -> logging.Logger:
    """返回 webrag.<name> logger；未 setup_logging 时仍可安全使用（WARNING+ 落 stderr 兜底）。"""
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


# ── 请求上下文（thread-local；子线程需显式 bind） ──


class _Ctx(threading.local):
    request_id: str | None = None
    question: str | None = None
    metrics: RequestMetrics | None = None


_ctx = _Ctx()


def new_request_id() -> str:
    """生成请求级 trace id（12 位 hex，日志内跨事件关联）。"""
    return uuid.uuid4().hex[:12]


def bind_request(request_id: str | None, question: str | None = None, metrics: RequestMetrics | None = None) -> None:
    """绑定当前线程的请求上下文；/ask/stream 的检索子线程需在其入口再 bind 一次。"""
    _ctx.request_id = request_id
    _ctx.question = (question or "")[: _QUESTION_MAX_LEN] or None
    _ctx.metrics = metrics


def unbind_request() -> None:
    _ctx.request_id = None
    _ctx.question = None
    _ctx.metrics = None


def current_metrics() -> RequestMetrics | None:
    """当前线程绑定的 RequestMetrics（无则 None——独立调用场景不聚合）。"""
    return _ctx.metrics


# ── 请求级指标聚合 ──


class RequestMetrics:
    """单次 /ask（或 /ask/stream）的指标聚合器。

    - mark(name)：阶段完成时间点，finish 时按相邻 mark 计算阶段耗时；
    - record_llm(...)：LLM 调用汇总（token / 耗时 / TTFT），由 llm 模块经 current_metrics() 调用；
    - finish()：落一条 `ask.completed` / `ask.stream.completed` 事件并更新全局统计。
    """

    def __init__(
        self,
        request_id: str,
        question: str,
        endpoint: str = "ask",
        use_web_search: bool = False,
        web_top_n: int | None = None,
    ):
        self.request_id = request_id
        self.question = question[: _QUESTION_MAX_LEN]
        self.endpoint = endpoint
        self.use_web_search = use_web_search
        self.web_top_n = web_top_n
        self.t0 = time.monotonic()
        self.marks: dict[str, float] = {}
        self.llm_calls: list[dict[str, Any]] = []
        self.cached: bool | None = None  # True=缓存命中 False=查过未命中 None=未走缓存路径
        self.cache_score: float | None = None
        self.direct: bool | None = None
        self.followup: bool | None = None  # 追问改写：True=判定为追问并已改写 None/False=未触发/非追问（评测追问效果用）
        self.retrieval_hits: int | None = None
        self.ttft_ms: float | None = None  # 请求级首 token 时间（仅流式）
        self.answer_len: int | None = None
        self.outcome: str = "incomplete"  # cache_hit | success | empty | error:<CODE>
        self._summary: dict[str, Any] = {}

    def mark(self, name: str) -> None:
        """记录阶段完成时间点（阶段耗时 = 相邻 mark 之差）。"""
        self.marks[name] = time.monotonic()

    def note_first_token(self) -> None:
        """首个 delta 输出时调用一次：请求级 TTFT（自请求开始）。"""
        if self.ttft_ms is None:
            self.ttft_ms = round((time.monotonic() - self.t0) * 1000, 1)

    def record_llm(
        self,
        call: str,
        duration_ms: float,
        ttft_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        error: str | None = None,
    ) -> None:
        """LLM 调用汇总（llm 模块调用；token 缺失时记 None，汇总按 0 处理）。"""
        self.llm_calls.append(
            {
                "call": call,
                "duration_ms": round(duration_ms, 1),
                "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "error": error,
            }
        )

    def set_cache(self, hit: bool, score: float | None = None) -> None:
        self.cached = hit
        self.cache_score = score

    def set_retrieval(self, n: int) -> None:
        self.retrieval_hits = n

    def set_followup(self, is_followup: bool) -> None:
        """记录追问改写结果（True=判定为追问并已改写；False=非追问/未触发）。"""
        self.followup = is_followup
    def set_outcome(self, outcome: str) -> None:
        self.outcome = outcome

    def set_outcome_if_unset(self, outcome: str) -> None:
        if self.outcome == "incomplete":
            self.outcome = outcome

    # ── 汇总 ──

    def summary(self) -> dict[str, Any]:
        """全部指标字段（event 由日志消息提供，不在此列）。"""
        total_ms = round((time.monotonic() - self.t0) * 1000, 1)
        segments: dict[str, float] = {}
        prev = self.t0
        for name in self.marks:
            segments[f"{name}_ms"] = round((self.marks[name] - prev) * 1000, 1)
            prev = self.marks[name]
        prompt = sum(c["prompt_tokens"] or 0 for c in self.llm_calls)
        completion = sum(c["completion_tokens"] or 0 for c in self.llm_calls)
        return {
            "endpoint": self.endpoint,
            "outcome": self.outcome,
            "duration_ms": total_ms,
            "segments_ms": segments,
            "llm_calls": self.llm_calls,
            "tokens": {"prompt": prompt, "completion": completion, "total": prompt + completion},
            "cached": self.cached,
            "cache_score": round(self.cache_score, 4) if self.cache_score is not None else None,
            "direct": self.direct,
            "followup": self.followup,
            "retrieval_hits": self.retrieval_hits,
            "ttft_ms": self.ttft_ms,
            "answer_len": self.answer_len,
            "use_web_search": self.use_web_search,
            "web_top_n": self.web_top_n,
        }

    def finish(self) -> dict[str, Any]:
        """落一条 <endpoint>.completed 事件 + 更新全局统计；重复调用返回缓存结果。"""
        if self._summary:
            return self._summary
        self._summary = self.summary()
        get_logger("main").info(f"{self.endpoint}.completed", extra={"fields": self._summary})
        registry.record(self)
        registry.report_if_due()
        return self._summary


# ── 全局统计（命中率 / 耗时 / token / 错误分布） ──


class StatsRegistry:
    """进程内请求统计：缓存命中率、EMPTY 率、错误分布、耗时与 token 均值。

    - record(m)：每次请求完成时更新；
    - report_if_due()：每 interval 个请求输出一条 stats.periodic（interval<=0 关闭）；
    - snapshot()：GET /logs/stats 只读快照。
    线程安全（lock 保护读写）。
    """

    def __init__(self, interval: int = 50):
        self.interval = interval if interval and interval > 0 else 0
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.requests = 0
            self.cache_hits = 0
            self.cache_misses = 0
            self.empty = 0
            self.errors: Counter = Counter()
            self.durations: list[float] = []
            self.tokens: list[int] = []
            self.ttft: list[float] = []

    def record(self, m: RequestMetrics) -> None:
        with self._lock:
            self.requests += 1
            if m.cached is True:
                self.cache_hits += 1
            elif m.cached is False:
                self.cache_misses += 1
            if m.outcome == "empty":
                self.empty += 1
            elif m.outcome.startswith("error:"):
                self.errors[m.outcome] += 1
            self.durations.append(m._summary.get("duration_ms", 0.0))
            self.tokens.append(m._summary.get("tokens", {}).get("total", 0))
            if m.ttft_ms is not None:
                self.ttft.append(m.ttft_ms)

    @staticmethod
    def _p95(values: list[float]) -> float | None:
        if not values:
            return None
        s = sorted(values)
        return round(s[int(0.95 * (len(s) - 1))], 1)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            decided = self.cache_hits + self.cache_misses
            return {
                "requests": self.requests,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "cache_hit_rate": round(self.cache_hits / decided, 4) if decided else None,
                "empty_count": self.empty,
                "empty_rate": round(self.empty / self.requests, 4) if self.requests else None,
                "error_count": sum(self.errors.values()),
                "errors": dict(self.errors),
                "avg_duration_ms": round(mean(self.durations), 1) if self.durations else None,
                "p95_duration_ms": self._p95(self.durations),
                "avg_tokens": round(mean(self.tokens), 1) if self.tokens else None,
                "avg_ttft_ms": round(mean(self.ttft), 1) if self.ttft else None,
            }

    def report_if_due(self) -> None:
        if not self.interval:
            return
        with self._lock:
            due = self.requests > 0 and self.requests % self.interval == 0
        if due:
            get_logger("main").info("stats.periodic", extra={"fields": self.snapshot()})


# 进程内单例：main 经 GET /logs/stats 暴露 snapshot()；setup_logging 设置 interval
registry = StatsRegistry()
