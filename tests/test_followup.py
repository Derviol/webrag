"""query_rewriter 追问检测与改写单测：规则预筛、历史压缩、LLM 判定/改写、降级路径。

负责人：追问业务（多轮对话补全）。
"""

import json

from src.webrag.query_rewriter import (
    FollowupResult,
    _format_history,
    needs_followup_llm,
    rewrite_followup,
)


class _MockLLM:
    """模拟 DeepSeekClient.complete() 行为。"""

    def __init__(self, response: str):
        self._response = response
        self.calls: list[tuple[str, int]] = []

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        self.calls.append((prompt, max_tokens or 256))
        return self._response


class _FailLLM:
    """模拟 LLM 调用失败（抛异常）。"""

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        raise RuntimeError("mock LLM failure")


HISTORY = [
    {"role": "user", "content": "BGE-M3 是什么？"},
    {"role": "assistant", "content": "BGE-M3 是智源的多语言嵌入模型，支持 100+ 语言。"},
]


# ── 规则预筛 needs_followup_llm ──


def test_needs_followup_no_history():
    """无历史消息 → 单轮提问，不触发追问判定。"""
    assert needs_followup_llm("它支持双向量吗", []) is False


def test_needs_followup_refer_word_triggers_llm():
    """含指代/承接词（它/这个/上述/另外…）→ 必须走 LLM 判定。"""
    assert needs_followup_llm("它支持双向量吗", HISTORY) is True
    assert needs_followup_llm("这个模型的部署要求呢", HISTORY) is True
    assert needs_followup_llm("上述方法有什么缺点", HISTORY) is True
    assert needs_followup_llm("那第二个方案呢", HISTORY) is True
    assert needs_followup_llm("还有别的选择吗", HISTORY) is True
    assert needs_followup_llm("除此之外呢", HISTORY) is True


def test_needs_followup_short_question_triggers_llm():
    """超短问（≤12 字符）→ 大概率省略主语，走 LLM 判定。"""
    assert needs_followup_llm("部署呢？", HISTORY) is True
    assert needs_followup_llm("为什么？", HISTORY) is True
    assert needs_followup_llm("支持双向量吗？", HISTORY) is True


def test_needs_followup_full_question_skipped():
    """较长且含明确主题 → 视为完整问题，跳过 LLM（省调用）。"""
    assert needs_followup_llm("BGE-M3 支持稠密和稀疏双向量吗", HISTORY) is False
    assert needs_followup_llm("Python 和 Java 的区别是什么", HISTORY) is False
    assert needs_followup_llm("什么是 RAG", HISTORY) is False
    assert needs_followup_llm("如何配置 Nginx", HISTORY) is False


# ── 历史压缩 _format_history ──


def test_format_history_keeps_order():
    hist = [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
        {"role": "assistant", "content": "第二答"},
    ]
    text = _format_history(hist, max_history=4, max_chars=1000)
    assert text == "用户：第一问\n助手：第一答\n用户：第二问\n助手：第二答"


def test_format_history_takes_recent():
    """超过 max_history 时只保留最近 N 条。"""
    hist = [{"role": "user", "content": f"第{i}问"} for i in range(10)]
    text = _format_history(hist, max_history=3, max_chars=1000)
    assert "第7问" in text
    assert "第0问" not in text
    assert text.count("用户：") == 3


def test_format_history_chars_budget_truncates_old():
    """总长超 max_chars 时丢弃较早轮次（保留最近的）。"""
    hist = [{"role": "user", "content": "一"}, {"role": "assistant", "content": "二"}, {"role": "user", "content": "三四五六七八九"}]
    # 预算只放得下最后一条：更早轮次全部丢弃
    text = _format_history(hist, max_history=10, max_chars=12)
    assert text == "用户：三四五六七八九"


def test_format_history_skips_invalid_entries():
    hist = [
        {"role": "system", "content": "x"},     # 非法角色
        {"role": "user", "content": "正常问题"},
        {"role": "assistant", "content": ""},   # 空内容
        {"role": "user", "content": None},      # 非字符串内容
    ]
    text = _format_history(hist, max_history=10, max_chars=1000)
    assert text == "用户：正常问题"


def test_format_history_empty_budget_returns_empty():
    hist = [{"role": "user", "content": "很长很长"}]
    assert _format_history(hist, max_history=10, max_chars=2) == ""


# ── rewrite_followup 主入口 ──


def test_rewrite_followup_no_history_skips_llm():
    llm = _MockLLM("")
    result = rewrite_followup("它支持双向量吗", [], llm)
    assert result.is_followup is False
    assert result.rewritten == "它支持双向量吗"
    assert result.skipped is True
    assert llm.calls == []  # 零 LLM 调用


def test_rewrite_followup_full_question_skips_llm():
    llm = _MockLLM("")
    result = rewrite_followup("BGE-M3 支持双向量吗", HISTORY, llm)
    assert result.is_followup is False
    assert result.skipped is True
    assert llm.calls == []


def test_rewrite_followup_is_followup_rewrites():
    llm = _MockLLM(
        json.dumps({"is_followup": True, "rewritten": "BGE-M3 支持稠密和稀疏双向量检索吗？"}, ensure_ascii=False)
    )
    result = rewrite_followup("支持双向量吗？", HISTORY, llm)
    assert result.is_followup is True
    assert result.rewritten == "BGE-M3 支持稠密和稀疏双向量检索吗？"
    assert result.skipped is False
    assert len(llm.calls) == 1


def test_rewrite_followup_not_followup_keeps_question():
    llm = _MockLLM(
        json.dumps({"is_followup": False, "rewritten": "BGE-M3 支持双向量吗？"}, ensure_ascii=False)
    )
    result = rewrite_followup("BGE-M3 支持双向量吗？", HISTORY, llm)
    assert result.is_followup is False
    assert result.rewritten == "BGE-M3 支持双向量吗？"


def test_rewrite_followup_empty_rewritten_falls_back():
    """is_followup=true 但 rewritten 为空 → 降级用原文。"""
    llm = _MockLLM(json.dumps({"is_followup": True, "rewritten": ""}, ensure_ascii=False))
    result = rewrite_followup("它呢？", HISTORY, llm)
    assert result.is_followup is False
    assert result.rewritten == "它呢？"


def test_rewrite_followup_rewritten_same_as_question_falls_back():
    """改写结果与原文相同（模型没真正改写）→ 视为非追问。"""
    llm = _MockLLM(json.dumps({"is_followup": True, "rewritten": "它呢？"}, ensure_ascii=False))
    result = rewrite_followup("它呢？", HISTORY, llm)
    assert result.is_followup is False
    assert result.rewritten == "它呢？"


def test_rewrite_followup_invalid_json_falls_back():
    llm = _MockLLM("这不是JSON")
    result = rewrite_followup("它呢？", HISTORY, llm)
    assert result.is_followup is False
    assert result.rewritten == "它呢？"


def test_rewrite_followup_markdown_json_fence():
    """LLM 返回带 markdown 围栏的 JSON → 仍能解析。"""
    resp = '```json\n{"is_followup": true, "rewritten": "BGE-M3 的部署要求是什么？"}\n```'
    llm = _MockLLM(resp)
    result = rewrite_followup("部署要求呢？", HISTORY, llm)
    assert result.is_followup is True
    assert result.rewritten == "BGE-M3 的部署要求是什么？"


def test_rewrite_followup_llm_failure_falls_back():
    llm = _FailLLM()
    result = rewrite_followup("它呢？", HISTORY, llm)
    assert result.is_followup is False
    assert result.rewritten == "它呢？"


def test_rewrite_followup_accepts_chatmessage_objects():
    """历史可以是 pydantic ChatMessage 对象（/ask 请求解析后的形态）。"""
    from src.webrag.schemas import ChatMessage

    hist = [ChatMessage(role="user", content="BGE-M3 是什么？")]
    llm = _MockLLM(
        json.dumps({"is_followup": True, "rewritten": "BGE-M3 的部署要求是什么？"}, ensure_ascii=False)
    )
    result = rewrite_followup("部署要求呢？", hist, llm)
    assert result.is_followup is True
    assert "BGE-M3" in result.rewritten


def test_rewrite_followup_defaults():
    result = FollowupResult()
    assert result.is_followup is False
    assert result.rewritten == ""
    assert result.skipped is False
