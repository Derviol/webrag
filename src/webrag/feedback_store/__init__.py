"""反馈存储模块（P2 优化）：JSONL 文件持久化 + 统计分析。

在线反馈收集闭环：用户对回答点 👍/👎 → POST /feedback 落库 →
GET /feedback/stats 输出统计数据 → 驱动检索与 Prompt 迭代。

存储格式：每行一条 JSON 记录（JSONL），路径 logs/feedback.jsonl。
无需数据库依赖，可直接用 grep/jq 离线分析。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.webrag.logger import get_logger
from src.webrag.schemas import FeedbackRecord, FeedbackRequest, FeedbackStats

_log = get_logger("feedback_store")

# 反馈日志路径（项目根/logs/feedback.jsonl）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_PATH = _PROJECT_ROOT / "logs" / "feedback.jsonl"


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def save_feedback(req: FeedbackRequest, path: Path | None = None) -> FeedbackRecord:
    """追加一条反馈到 JSONL 文件。

    返回写入的 FeedbackRecord（含时间戳）。
    """
    target = path or _DEFAULT_PATH
    _ensure_dir(target)

    record = FeedbackRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        question=req.question,
        answer=req.answer,
        sources=req.sources,
        feedback_type=req.feedback_type,
        cached=req.cached,
        direct=req.direct,
        hallucination_risk=req.hallucination_risk,
    )

    line = record.model_dump_json(ensure_ascii=False) + "\n"
    with open(target, "a", encoding="utf-8") as f:
        f.write(line)

    return record


def load_all_feedback(path: Path | None = None) -> list[FeedbackRecord]:
    """加载全部反馈记录（按写入顺序）。"""
    target = path or _DEFAULT_PATH
    if not target.exists():
        return []
    records: list[FeedbackRecord] = []
    with open(target, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(FeedbackRecord.model_validate_json(line))
            except Exception as exc:
                _log.warning("feedback_store.skip_corrupt_line", extra={"fields": {"error": str(exc)}})
                continue
    return records


def compute_stats(path: Path | None = None) -> FeedbackStats:
    """计算反馈统计数据。"""
    records = load_all_feedback(path)
    total = len(records)
    if total == 0:
        return FeedbackStats()

    good = sum(1 for r in records if r.feedback_type == "good")
    bad = total - good

    # 按缓存/新鲜分组
    cached_good = sum(1 for r in records if r.cached and r.feedback_type == "good")
    cached_bad = sum(1 for r in records if r.cached and r.feedback_type == "bad")
    fresh_good = sum(1 for r in records if not r.cached and r.feedback_type == "good")
    fresh_bad = sum(1 for r in records if not r.cached and r.feedback_type == "bad")

    # 最近 10 条差评
    bad_records = [r for r in records if r.feedback_type == "bad"]
    bad_records.sort(key=lambda r: r.timestamp, reverse=True)
    recent_bad = [
        {
            "timestamp": r.timestamp[:19],
            "question": r.question[:80],
            "answer_preview": r.answer[:120],
            "hallucination_risk": r.hallucination_risk or "N/A",
        }
        for r in bad_records[:10]
    ]

    return FeedbackStats(
        total=total,
        good=good,
        bad=bad,
        good_rate=good / total,
        by_model={
            "cached": {"good": cached_good, "bad": cached_bad},
            "fresh": {"good": fresh_good, "bad": fresh_bad},
        },
        recent_bad=recent_bad,
    )


def export_stats_json(path: Path | None = None, output_dir: Path | None = None) -> Path:
    """导出统计报告 JSON 文件，返回文件路径。"""
    stats = compute_stats(path)
    out_dir = output_dir or (_PROJECT_ROOT / "eval" / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"feedback_stats_{timestamp}.json"
    out_path.write_text(stats.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path
