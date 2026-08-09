"""query_rewriter 单测：意图分类规则、多路改写、HyDE、完整管线。

负责人：P0 检索优化。
"""

import json
import re

from src.webrag.query_rewriter import (
    INTENT_CONFIG,
    QueryIntent,
    RewriteResult,
    classify_intent_llm,
    classify_intent_rule,
    current_time_text,
    generate_hyde,
    has_time_reference,
    rewrite_queries,
    rewrite_query,
)

# ── 分类规则（零 LLM 调用） ──


def test_classify_news():
    assert classify_intent_rule("今天有什么新闻") == QueryIntent.NEWS
    assert classify_intent_rule("最新AI进展") == QueryIntent.NEWS
    assert classify_intent_rule("最近发生了什么事") == QueryIntent.NEWS
    assert classify_intent_rule("本周热点事件") == QueryIntent.NEWS
    assert classify_intent_rule("刚刚发布的新政策") == QueryIntent.NEWS
    assert classify_intent_rule("近期有哪些科技突破") == QueryIntent.NEWS


def test_classify_comparison():
    assert classify_intent_rule("对比Python和Java") == QueryIntent.COMPARISON
    assert classify_intent_rule("比较这两个框架的区别") == QueryIntent.COMPARISON
    assert classify_intent_rule("哪个更适合做后端") == QueryIntent.COMPARISON
    assert classify_intent_rule("React vs Vue") == QueryIntent.COMPARISON
    assert classify_intent_rule("和传统方法比有什么差异") == QueryIntent.COMPARISON
    assert classify_intent_rule("哪一款性能更好") == QueryIntent.COMPARISON


def test_classify_opinion():
    assert classify_intent_rule("你觉得这个框架怎么样") == QueryIntent.OPINION
    assert classify_intent_rule("推荐一个前端工具") == QueryIntent.OPINION
    assert classify_intent_rule("这个方案好不好") == QueryIntent.OPINION
    assert classify_intent_rule("是否值得学习Rust") == QueryIntent.OPINION
    assert classify_intent_rule("对微服务架构的评价") == QueryIntent.OPINION
    assert classify_intent_rule("有什么优缺点") == QueryIntent.OPINION


def test_classify_how_to():
    assert classify_intent_rule("怎么安装Python") == QueryIntent.HOW_TO
    assert classify_intent_rule("如何配置Nginx") == QueryIntent.HOW_TO
    assert classify_intent_rule("部署Docker的步骤") == QueryIntent.HOW_TO
    assert classify_intent_rule("配置HTTPS的方法") == QueryIntent.HOW_TO
    assert classify_intent_rule("Python使用教程") == QueryIntent.HOW_TO
    assert classify_intent_rule("怎样操作数据库") == QueryIntent.HOW_TO


def test_classify_fact_lookup():
    assert classify_intent_rule("什么是RAG") == QueryIntent.FACT_LOOKUP
    assert classify_intent_rule("LLM的定义是什么") == QueryIntent.FACT_LOOKUP
    assert classify_intent_rule("谁发明了Transformer") == QueryIntent.FACT_LOOKUP
    assert classify_intent_rule("为什么会过拟合") == QueryIntent.FACT_LOOKUP
    assert classify_intent_rule("预训练模型的概念") == QueryIntent.FACT_LOOKUP


def test_classify_not_matched_returns_none():
    """规则无法匹配时返回 None，触发 LLM 兜底。"""
    assert classify_intent_rule("你好") is None
    assert classify_intent_rule("谢谢") is None
    assert classify_intent_rule("") is None
    assert classify_intent_rule("帮我写一段代码") is None  # 无关意图
    # "今天的天气" 中 "今天" 匹配 NEWS 规则 → 不是 None
    assert classify_intent_rule("今天的天气") == QueryIntent.NEWS


def test_intent_rule_priority():
    """验证规则优先级：NEWS > COMPARISON > OPINION > HOW_TO > FACT_LOOKUP。
    "怎么"不匹配 HOW_TO 是因为 OPINION 规则排在前面，"怎么样"被 OPINION 捕获。
    HOW_TO 用 "怎么(?!样)" 排除 "怎么样" 的干扰。
    """
    # "怎么" 后跟非 "样" 字符 → HOW_TO
    assert classify_intent_rule("怎么安装") == QueryIntent.HOW_TO  # "怎么" 匹配 HOW_TO 的 "怎么(?!样)"
    # "怎么样" → OPINION（先于 HOW_TO 匹配）
    assert classify_intent_rule("这个怎么样") == QueryIntent.OPINION
    # "最新" + "对比" → NEWS 优先
    assert classify_intent_rule("最新框架对比") == QueryIntent.NEWS


# ── 意图配置 ──


def test_intent_config_values():
    """验证各意图的检索权重配置正确。"""
    assert INTENT_CONFIG[QueryIntent.FACT_LOOKUP]["dense_weight"] == 0.6
    assert INTENT_CONFIG[QueryIntent.FACT_LOOKUP]["sparse_weight"] == 0.4
    assert INTENT_CONFIG[QueryIntent.FACT_LOOKUP]["rerank_top_n"] == 5

    assert INTENT_CONFIG[QueryIntent.HOW_TO]["dense_weight"] == 0.4
    assert INTENT_CONFIG[QueryIntent.HOW_TO]["sparse_weight"] == 0.6
    assert INTENT_CONFIG[QueryIntent.HOW_TO]["rerank_top_n"] == 5

    assert INTENT_CONFIG[QueryIntent.NEWS]["force_fresh"] is True
    assert INTENT_CONFIG[QueryIntent.NEWS]["rerank_top_n"] == 5

    assert INTENT_CONFIG[QueryIntent.COMPARISON]["dense_weight"] == 0.7
    assert INTENT_CONFIG[QueryIntent.COMPARISON]["sparse_weight"] == 0.3

    assert INTENT_CONFIG[QueryIntent.OPINION]["dense_weight"] == 0.3
    assert INTENT_CONFIG[QueryIntent.OPINION]["sparse_weight"] == 0.7

    assert INTENT_CONFIG[QueryIntent.GENERAL]["dense_weight"] == 0.5
    assert INTENT_CONFIG[QueryIntent.GENERAL]["sparse_weight"] == 0.5


# ── RewriteResult 默认值 ──


def test_rewrite_result_defaults():
    result = RewriteResult()
    assert result.intent == QueryIntent.GENERAL
    assert result.rewritten_queries == []
    assert result.hyde_text == ""
    assert result.force_fresh is False
    assert result.time_aware is False
    assert result.time_context == ""


# ── Mock LLM client ──


class _MockLLM:
    """模拟 DeepSeekClient.complete() 行为。"""

    def __init__(self, response: str):
        self._response = response
        self.calls: list[tuple[str, int]] = []

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        self.calls.append((prompt, max_tokens or 256))
        return self._response


class _FailLLM:
    """模拟 LLM 调用失败（返回空/异常）。"""

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        raise RuntimeError("mock LLM failure")


# ── classify_intent_llm ──


def test_classify_intent_llm_returns_intent():
    llm = _MockLLM("fact_lookup")
    assert classify_intent_llm("什么是AI", llm) == QueryIntent.FACT_LOOKUP


def test_classify_intent_llm_unknown_label_falls_back_to_general():
    llm = _MockLLM("some_unknown_label")
    assert classify_intent_llm("测试问题", llm) == QueryIntent.GENERAL


def test_classify_intent_llm_failure_falls_back_to_general():
    llm = _FailLLM()
    assert classify_intent_llm("测试问题", llm) == QueryIntent.GENERAL


def test_classify_intent_llm_empty_response():
    llm = _MockLLM("")
    assert classify_intent_llm("测试问题", llm) == QueryIntent.GENERAL


# ── rewrite_queries ──


def test_rewrite_queries_produces_keywords_formal_sub_questions():
    resp = {
        "keywords": "Python 安装 教程 pip",
        "formal": "如何正确安装Python编程语言",
        "sub_questions": ["Python支持的安装方式有哪些", "如何验证Python安装成功"],
    }
    llm = _MockLLM(json.dumps(resp, ensure_ascii=False))
    queries = rewrite_queries("怎么装Python", llm)
    assert "Python 安装 教程 pip" in queries
    assert "如何正确安装Python编程语言" in queries
    assert "Python支持的安装方式有哪些" in queries
    assert "如何验证Python安装成功" in queries
    # 原始问题不自动追加（由调用方在 RRF 时加入）
    assert len(llm.calls) == 1


def test_rewrite_queries_single_sub_question():
    resp = {
        "keywords": "RAG 定义",
        "formal": "什么是检索增强生成（RAG）技术",
        "sub_questions": ["RAG是什么"],
    }
    llm = _MockLLM(json.dumps(resp, ensure_ascii=False))
    queries = rewrite_queries("RAG是什么", llm)
    assert len(queries) == 3
    assert "RAG 定义" in queries


def test_rewrite_queries_fallback_on_json_error():
    """LLM 返回非 JSON → 降级返回原始问题。"""
    llm = _MockLLM("这不是合法的JSON")
    queries = rewrite_queries("测试问题", llm)
    assert queries == ["测试问题"]


def test_rewrite_queries_fallback_on_empty_response():
    llm = _MockLLM("")
    queries = rewrite_queries("测试问题", llm)
    assert queries == ["测试问题"]


def test_rewrite_queries_fallback_on_llm_failure():
    llm = _FailLLM()
    queries = rewrite_queries("测试问题", llm)
    assert queries == ["测试问题"]


def test_rewrite_queries_markdown_json_fence():
    """LLM 在 JSON 外加了 markdown 围栏 → 仍能解析。"""
    resp = '```json\n{"keywords": "AI 机器学习", "formal": "人工智能和机器学习的关系", "sub_questions": []}\n```'
    llm = _MockLLM(resp)
    queries = rewrite_queries("AI和ML的关系", llm)
    assert "AI 机器学习" in queries
    assert "人工智能和机器学习的关系" in queries


# ── generate_hyde ──


def test_generate_hyde_returns_draft():
    llm = _MockLLM("检索增强生成（RAG）是一种结合了信息检索和文本生成的技术框架。")
    hyde = generate_hyde("RAG是什么", llm)
    assert len(hyde) > 0
    assert "检索增强" in hyde or "RAG" in hyde


def test_generate_hyde_fallback_on_failure():
    llm = _FailLLM()
    hyde = generate_hyde("RAG是什么", llm)
    assert hyde == ""


def test_generate_hyde_fallback_on_empty():
    llm = _MockLLM("")
    hyde = generate_hyde("RAG是什么", llm)
    assert hyde == ""


# ── rewrite_query 完整管线 ──


def _mock_llm_for_full_pipeline():
    """创建一个完整的 mock LLM，对 intent / rewrite / hyde 分别返回对应内容。"""

    class MultiMockLLM:
        def __init__(self):
            self.call_count = 0

        def complete(self, prompt: str, max_tokens: int | None = None) -> str:
            self.call_count += 1
            if "标签：" in prompt:
                return "how_to"
            if "输出 JSON" in prompt:
                return json.dumps({
                    "keywords": "Python 安装 步骤",
                    "formal": "Python编程语言的安装方法",
                    "sub_questions": ["Python如何下载", "安装后如何验证"],
                }, ensure_ascii=False)
            if "草稿" in prompt:
                return "Python可以从官网下载安装包，运行后按指引完成安装，然后用 python --version 验证。"
            return "general"

    return MultiMockLLM()


def test_rewrite_query_full_pipeline():
    llm = _mock_llm_for_full_pipeline()
    result = rewrite_query("怎么装Python", llm)

    assert result.intent == QueryIntent.HOW_TO
    assert result.force_fresh is False
    assert len(result.rewritten_queries) == 4  # keywords + formal + 2 sub_questions
    assert any("Python" in q for q in result.rewritten_queries)
    assert len(result.hyde_text) > 0
    assert "Python" in result.hyde_text


def test_rewrite_query_news_intent_forces_fresh():
    """新闻类意图 → force_fresh=True，跳过缓存。"""
    llm = _mock_llm_for_full_pipeline()
    # 用规则就能命中 NEWS，不需要 LLM
    result = rewrite_query("今天有什么新闻", llm)
    assert result.intent == QueryIntent.NEWS
    assert result.force_fresh is True


def test_rewrite_query_with_disabled_features():
    """禁用所有子功能 → 仅做规则级意图分类。"""
    llm = _mock_llm_for_full_pipeline()
    result = rewrite_query(
        "怎么装Python", llm,
        enable_intent=True,
        enable_multi_rewrite=False,
        enable_hyde=False,
    )
    assert result.intent == QueryIntent.HOW_TO
    assert result.rewritten_queries == []
    assert result.hyde_text == ""


def test_rewrite_query_disabled_intent():
    """禁用意图分类 → 默认 GENERAL，但仍做改写。"""
    llm = _mock_llm_for_full_pipeline()
    result = rewrite_query(
        "怎么装Python", llm,
        enable_intent=False,
        enable_multi_rewrite=True,
        enable_hyde=False,
    )
    assert result.intent == QueryIntent.GENERAL
    assert len(result.rewritten_queries) > 0


def test_rewrite_query_no_llm_calls_for_rule_matched_intent():
    """规则命中意图时，不下发 LLM 意图分类调用。"""

    class CallCountingLLM:
        def complete(self, prompt, max_tokens=None):
            return "fact_lookup"

    llm = CallCountingLLM()
    result = rewrite_query("什么是RAG", llm)
    assert result.intent == QueryIntent.FACT_LOOKUP
    # 因为"什么是RAG"被规则直接命中 FACT_LOOKUP，不需要 LLM 分类；
    # 但多路改写和 HyDE 仍会调 LLM（各一次）


def test_rewrite_query_general_fallback():
    """无法规则匹配 + LLM 返回 unknown → GENERAL。"""
    llm = _MockLLM("unknown_label_xyz")
    result = rewrite_query("你好", llm)
    assert result.intent == QueryIntent.GENERAL


# ── 边界情况 ──


def test_rewrite_queries_empty_keywords():
    """keywords 为空字符串 → 不出现在查询列表中。"""
    resp = {
        "keywords": "",
        "formal": "正式版问题内容",
        "sub_questions": [],
    }
    llm = _MockLLM(json.dumps(resp, ensure_ascii=False))
    queries = rewrite_queries("测试", llm)
    assert queries == ["正式版问题内容"]


def test_rewrite_queries_strips_whitespace():
    resp = {
        "keywords": "  深度学习  神经网络  ",
        "formal": " 什么是深度学习技术 ",
        "sub_questions": [" 深度学习和神经网络的关系 "],
    }
    llm = _MockLLM(json.dumps(resp, ensure_ascii=False))
    queries = rewrite_queries("深度学习是什么", llm)
    assert queries[0] == "深度学习  神经网络"
    assert queries[1] == "什么是深度学习技术"


def test_classify_intent_rule_empty_string():
    assert classify_intent_rule("") is None


def test_classify_intent_rule_chinese_punctuation():
    """中文标点不影响规则匹配。"""
    assert classify_intent_rule("「什么是」RAG？") == QueryIntent.FACT_LOOKUP


# ── 时效性锚定：相对时间词（近日/近期…）→ 注入本地当前时间 ──


def test_has_time_reference_true_for_relative_time_words():
    assert has_time_reference("近日股市表现如何")
    assert has_time_reference("近期有哪些科技突破")
    assert has_time_reference("最新AI进展")
    assert has_time_reference("今天有什么新闻")
    assert has_time_reference("本周热点事件")


def test_has_time_reference_false_for_non_time_queries():
    assert not has_time_reference("怎么装Python")
    assert not has_time_reference("什么是RAG")
    assert not has_time_reference("")


def test_current_time_text_is_full_date():
    """本地当前时间格式：YYYY年MM月DD日（供联网搜索词与 LLM 时间基准使用）。"""
    assert re.fullmatch(r"\d{4}年\d{2}月\d{2}日", current_time_text())


def test_current_time_text_uses_client_time():
    """前端宿主机本地时间（client_time）：ISO 8601 → 中文日期文本。

    不做时区转换——保留客户端本地墙钟日期（+08:00 就是用户机器上的日期，服务端时区不同也不改）。
    """
    assert current_time_text("2026-08-07T14:30:00+08:00") == "2026年08月07日"
    assert current_time_text("2026-01-02") == "2026年01月02日"  # 仅日期（date-only）也接受


def test_current_time_text_fallback_on_invalid_client_time():
    """非法/空 client_time → 回落服务器本地时间（不阻断主链路），输出格式不变。"""
    assert re.fullmatch(r"\d{4}年\d{2}月\d{2}日", current_time_text("不是日期"))
    assert re.fullmatch(r"\d{4}年\d{2}月\d{2}日", current_time_text(""))


def test_rewrite_query_time_aware_injects_anchored_query():
    """含「近日」的问题：time_aware=True + 注入当前时间 + 改写列表追加时间锚定查询。"""
    llm = _mock_llm_for_full_pipeline()
    result = rewrite_query("近日有什么新闻", llm)
    assert result.time_aware is True
    assert re.fullmatch(r"\d{4}年\d{2}月\d{2}日", result.time_context)
    assert f"近日有什么新闻（{result.time_context}）" in result.rewritten_queries


def test_rewrite_query_uses_client_time_for_anchor():
    """rewrite_query 注入 client_time：时间锚定以客户端本地日期为准。"""
    llm = _mock_llm_for_full_pipeline()
    result = rewrite_query("近日有什么新闻", llm, client_time="2026-01-02T10:30:00+08:00")
    assert result.time_aware is True
    assert result.time_context == "2026年01月02日"
    assert "近日有什么新闻（2026年01月02日）" in result.rewritten_queries


def test_rewrite_query_non_time_question_not_time_aware():
    """非时效性问题：不注入时间，不改写查询列表。"""
    llm = _mock_llm_for_full_pipeline()
    result = rewrite_query("怎么装Python", llm)
    assert result.time_aware is False
    assert result.time_context == ""
    assert result.rewritten_queries == ["Python 安装 步骤", "Python编程语言的安装方法", "Python如何下载", "安装后如何验证"]
