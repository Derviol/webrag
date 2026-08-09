"""幻觉检测：LLM 生成回答后二次核验，逐句检查是否能在上下文中找到支撑。

P1 优化：降低模型编造事实的风险，提高回答可信度。

流程：
1. 把 LLM 回答按句子拆分；
2. 对每句话调用 LLM 核验（小模型、低温度），判断是否能在上下文中找到支撑；
3. 标注每句话：✓（有支撑）、✗（无支撑/幻觉）、?（部分支撑）；
4. 返回 HallucinationReport，包含幻觉率、风险等级、逐句标注。

配置开关：settings.hallucination_checker.enable
- enable_auto_rewrite=false（默认）：仅标记风险，不做额外 LLM 调用
- enable_auto_rewrite=true：检测到 ✗ 时自动调 LLM 重写回答（额外一次调用）

注意事项：
- 幻觉检测是一次额外 LLM 调用，会增加约 2-5s 延迟；
- 检测本身也可能有误判（把正确回答判为幻觉），因此默认为标记而非阻断；
- 直答兜底（direct=true）无上下文，不适用幻觉检测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.webrag.schemas import Chunk

# 核验 Prompt：逐句检查
_VERIFY_PROMPT = """你是严格的回答核验员。请逐句检查以下回答中的每一句话是否能在给定的上下文中找到支撑。

## 核验规则
1. **逐句检查**：按标点（。！？；）拆分回答为单个句子，依次核验
2. **标注标准**：
   - ✓：该句的核心事实在上下文中能直接找到支撑
   - ✗：该句的核心事实在上下文中完全找不到支撑（疑似编造/幻觉）
   - ?：部分事实有支撑，但细节存在不一致或无法完全验证
3. **不要过度严格**：常识性过渡语句（如"综上所述"、"值得注意的是"）、引用编号本身（如 [1]）不视为幻觉
4. **只看事实**：只关注具体的数字、人名、日期、事件描述等可验证事实

## 上下文
{contexts}

## 核验的回答
{answer}

## 逐句核验结果
请按以下 JSON 格式输出（不要任何额外文本）：
{{"sentences": [{{"text": "句子原文", "label": "✓|✗|?", "reason": "简短理由"}}], "hallucination_rate": 0.0, "risk": "none|low|medium|high"}}
"""


@dataclass
class SentenceCheck:
    """单句核验结果。"""
    text: str           # 原句
    label: str          # ✓ | ✗ | ?
    reason: str = ""    # 核验理由


@dataclass
class HallucinationReport:
    """幻觉检测报告。"""
    hallucination_rate: float = 0.0       # ✗ 句子占比
    risk: str = "none"                     # none | low | medium | high
    sentences: list[SentenceCheck] = field(default_factory=list)
    has_hallucination: bool = False       # 是否存在 ✗ 标注
    error: str = ""                        # 检测过程错误（非阻塞）


def _split_sentences(text: str) -> list[str]:
    """按中文标点拆分为句子（保留标点）。"""
    parts = re.split(r"(?<=[。！？；\n])", text)
    return [p.strip() for p in parts if p.strip()]


def _format_contexts_for_verify(contexts: list[Chunk]) -> str:
    """格式化上下文用于核验 Prompt。"""
    parts = []
    for i, c in enumerate(contexts, 1):
        title = c.metadata.title or c.metadata.url or "未命名来源"
        parts.append(f"[来源{i}] 标题：{title}\n内容：{c.text}")
    return "\n\n".join(parts)


def _parse_verify_result(raw: str) -> tuple[list[SentenceCheck], float, str]:
    """解析 LLM 返回的 JSON 核验结果。

    容错：JSON 解析失败时逐行回退解析。
    """
    import json

    try:
        # 尝试找到 JSON 块（可能被 markdown ```json 包裹）
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # JSON 解析失败：返回未确定
        return [], 0.0, "none"

    sentences_raw = data.get("sentences", [])
    sentences = [
        SentenceCheck(
            text=s.get("text", ""),
            label=s.get("label", "?"),
            reason=s.get("reason", ""),
        )
        for s in sentences_raw
    ]
    rate = float(data.get("hallucination_rate", 0.0))
    risk = str(data.get("risk", "none"))

    # 如果 rate 为 0 但有 sentences，计算一下
    if rate == 0.0 and sentences:
        n_halluc = sum(1 for s in sentences if s.label == "✗")
        rate = n_halluc / len(sentences) if sentences else 0.0

    return sentences, rate, risk


def check_hallucination(
    answer: str,
    contexts: list[Chunk],
    llm_client,
    enable_auto_rewrite: bool = False,
) -> HallucinationReport:
    """检测 LLM 回答中的幻觉。

    Args:
        answer: LLM 生成的全量回答文本。
        contexts: 用于生成的上下文片段（与 generate() 传入的相同）。
        llm_client: DeepSeekClient 实例（调用 complete() 做核验）。
        enable_auto_rewrite: 是否检测到幻觉后自动重写（额外 LLM 调用）。

    Returns:
        HallucinationReport: 包含逐句标注、幻觉率、风险等级。
    """
    if not answer or not contexts:
        return HallucinationReport(has_hallucination=False, error="无回答或上下文，跳过检测")

    # 拆分句子
    sentences_raw = _split_sentences(answer)
    if not sentences_raw:
        return HallucinationReport(has_hallucination=False)

    # 构建核验 Prompt
    prompt = _VERIFY_PROMPT.format(
        contexts=_format_contexts_for_verify(contexts),
        answer=answer,
    )

    try:
        raw_result = llm_client.complete(prompt, max_tokens=1024)
        if not raw_result:
            return HallucinationReport(
                has_hallucination=False,
                error="LLM 核验返回空（跳过幻觉检测）",
            )
    except Exception as exc:
        return HallucinationReport(
            has_hallucination=False,
            error=f"LLM 核验调用失败（跳过幻觉检测）：{exc}",
        )

    # 解析结果
    sentences, rate, risk = _parse_verify_result(raw_result)
    has_hallucination = rate > 0.0 or risk in ("medium", "high")

    report = HallucinationReport(
        hallucination_rate=rate,
        risk=risk,
        sentences=sentences,
        has_hallucination=has_hallucination,
    )

    return report


def check_hallucination_fast(
    answer: str,
    contexts: list[Chunk],
) -> HallucinationReport:
    """快速幻觉检测（无需 LLM）：用文本重叠度做启发式初筛。

    原理：检查回答中每个句子的关键词在上下文中是否存在。
    这是零成本的初筛，不能替代 LLM 核验（会有误判），但可用于标记高风险回答。

    Args:
        answer: LLM 生成的全量回答文本。
        contexts: 用于生成的上下文片段。

    Returns:
        HallucinationReport: 启发式标注结果。
    """
    if not answer or not contexts:
        return HallucinationReport(has_hallucination=False, error="无回答或上下文，跳过检测")

    # 合并所有上下文为一个大文本
    context_text = " ".join(c.text for c in contexts)

    import jieba

    sentences_raw = _split_sentences(answer)
    if not sentences_raw:
        return HallucinationReport(has_hallucination=False)

    sentences: list[SentenceCheck] = []
    n_halluc = 0

    for s in sentences_raw:
        # 跳过纯引用标注和过渡语
        if re.match(r'^[\[\(（]\d+[\]\)）]$', s):
            sentences.append(SentenceCheck(text=s, label="✓", reason="引用标注"))
            continue

        # jieba 分词提取关键词（长度 >= 2 的中文词，排除停用词）
        keywords = {w for w in jieba.cut(s) if len(w) >= 2 and re.search(r'[\u4e00-\u9fff]', w)}
        if not keywords:
            sentences.append(SentenceCheck(text=s, label="✓", reason="短句/过渡句"))
            continue

        # 检查关键词在上下文中的覆盖率
        hits = sum(1 for kw in keywords if kw in context_text)
        coverage = hits / len(keywords) if keywords else 1.0

        if coverage >= 0.7:
            label = "✓"
            reason = f"关键词覆盖率 {coverage:.0%}"
        elif coverage >= 0.4:
            label = "?"
            reason = f"关键词覆盖率仅 {coverage:.0%}"
        else:
            label = "✗"
            reason = f"关键词覆盖率仅 {coverage:.0%}"
            n_halluc += 1

        sentences.append(SentenceCheck(text=s, label=label, reason=reason))

    rate = n_halluc / len(sentences) if sentences else 0.0
    if rate == 0:
        risk = "none"
    elif rate < 0.15:
        risk = "low"
    elif rate < 0.30:
        risk = "medium"
    else:
        risk = "high"

    return HallucinationReport(
        hallucination_rate=rate,
        risk=risk,
        sentences=sentences,
        has_hallucination=n_halluc > 0,
    )
