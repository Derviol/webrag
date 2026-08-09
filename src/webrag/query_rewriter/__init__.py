"""Query 改写与扩展模块。

对用户问题进行意图分类、多路改写和 HyDE 草稿生成，
缩小用户口语化提问与文档书面表达之间的语义鸿沟；
并检测「近日/近期/最新」等相对时间词，注入本地当前时间（时效性锚定），
供联网搜索与 LLM 生成使用（时效性问题命中率优化）。

负责人：P0 检索优化。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class QueryIntent(str, Enum):
    FACT_LOOKUP = "fact_lookup"   # 查事实、定义、数据
    HOW_TO = "how_to"             # 操作步骤、方法教程
    NEWS = "news"                 # 新闻时事、最新动态
    COMPARISON = "comparison"     # 比较对比
    OPINION = "opinion"           # 评价、看法、建议
    GENERAL = "general"           # 无法归类


@dataclass
class RewriteResult:
    """Query 改写管线输出。"""
    intent: QueryIntent = QueryIntent.GENERAL
    rewritten_queries: list[str] = field(default_factory=list)   # 多路改写后的查询列表
    hyde_text: str = ""                                            # HyDE 草稿文本
    force_fresh: bool = False                                      # 是否强制联网（跳缓存）
    time_aware: bool = False                                      # 问题含相对时间词（近日/近期…），已做时效性锚定
    time_context: str = ""                                        # 注入的本地当前时间（例：2026年8月8日），供联网搜索与 LLM 使用


# ── 时效性锚定：相对时间词检测 + 本地当前时间注入 ──

# 「近日/近期/最新/今天…」这类相对时间词没有绝对日期，搜索引擎与 LLM 都难以定位"现在"；
# 检测到后注入本地当前时间（2026年8月8日）——联网搜索词带上具体日期更易命中近期内容，
# LLM 生成时也有了明确的时间基准（见 llm._time_note）。
_TIME_REFERENCE_RE = re.compile(
    r"近日|近期|最近|最新|今天|今日|昨天|昨日|前天|前日|明天|明日|后天|"
    r"本周|上周|下周|本月|上月|下月|上个月|这个月|今年|去年|明年|"
    r"近几天|近几日|这几天|近段时间|近段时日|这段时间|目前|眼下"
)


def current_time_text(client_time: str | None = None) -> str:
    """获取当前时间文本（例：2026年8月8日），用于时效性问题的查询改写。

    client_time：前端宿主机的本地时间（ISO 8601，如 2026-08-07T14:30:00+08:00）。
    「近日/今天」相对的是用户所在机器——优先用它，且**不做时区转换**（保留客户端本地
    日期，服务端时区不同也不改日期）；缺省或解析失败回落服务器本地时间
    （timezone.utc → astimezone()：DTZ005 要求 tz 感知，服务器本地即"此刻"）。
    """
    if client_time:
        try:
            return datetime.fromisoformat(client_time).strftime("%Y年%m月%d日")
        except (ValueError, TypeError):
            pass  # 非法格式：回落服务器时间，不阻断主链路
    return datetime.now(timezone.utc).astimezone().strftime("%Y年%m月%d日")


def has_time_reference(question: str) -> bool:
    """检测问题中是否含相对时间词（近日/近期/最新…），决定是否做时效性锚定。"""
    return bool(question) and bool(_TIME_REFERENCE_RE.search(question))


# ── 追问检测与改写（多轮对话补全） ──


@dataclass
class FollowupResult:
    """追问改写输出：当前问题是否依赖历史 + 改写后的自包含问题。"""
    is_followup: bool = False  # 是否判定为追问（依赖历史上下文才能完整理解）
    rewritten: str = ""        # 改写后的自包含完整问题（非追问时 = 原始问题）
    skipped: bool = False      # 规则预筛直接跳过（未调 LLM），视为非追问


# 指代/承接词：出现即高概率依赖上文（"它/这个/上述/另外…"），必须走 LLM 判定。
_FOLLOWUP_REFER_RE = re.compile(
    r"它|它们|他|他们|她|她们|这个|那个|这些|那些|该|其|其中|此|"
    r"上述|以上|如上|上文|前文|刚才|下面这个|第一个|第二个|后者|前者|"
    r"另外|还有|除此之外|同理|相比之下|分别|各自|也就是说|那么"
)

# 完整疑问句式词（什么/如何/为什么…）：含这些词且长度适中的问题通常带主题（「什么是 RAG」），
# 视为完整问题；极短（≤6 字符）仍走 LLM（「为什么？」虽含疑问词但无主题）。
_FOLLOWUP_QUERY_WORD_RE = re.compile(r"什么|谁|哪里|哪儿|多少|为什么|怎么|如何|怎样")


def needs_followup_llm(question: str, history: list) -> bool:
    """规则预筛：是否值得走 LLM 判定+改写。

    - 无历史 → False（单轮提问无上下文可补）；
    - 命中指代/承接词 → True（几乎必然追问）；
    - 超短问（≤12 字符，常见省略主语形态，如「部署呢？」「支持双向量吗？」）→ True；
      但含疑问词且长度适中（7–12 字符，如「什么是 RAG」）视为完整问题 → False；
    - 其余 → False（完整问题，直接按原文处理，省一次 LLM 调用）。

    预筛只负责「放行」：误判为 False 的代价是漏掉改写（用原文，无害）；
    误判为 True 的代价是多一次 LLM 调用（结果仍由模型判定）。
    """
    if not history:
        return False
    if _FOLLOWUP_REFER_RE.search(question):
        return True
    q = question.strip()
    if len(q) > 12:
        return False
    if len(q) <= 6:
        return True  # 极短（部署呢？/为什么？）
    # 7–12 字符：含完整疑问句式（什么/如何…）视为完整问题
    return not _FOLLOWUP_QUERY_WORD_RE.search(q)


def _format_history(history: list, max_history: int = 6, max_chars: int = 3000) -> str:
    """把消息列表压缩成 LLM 提示词用的历史文本：取最近 max_history 条，总长超 max_chars 截断较早轮次。

    history：ChatMessage 或 {role, content} dict 列表（时间正序）；非法元素（角色非 user/assistant、
    内容为空）跳过。输出为「用户：…\n助手：…」按时间正序的多行文本。
    """
    lines: list[str] = []
    total = 0
    for msg in reversed(history[-max_history:]):
        if isinstance(msg, dict):
            role, content = msg.get("role"), msg.get("content")
        else:
            role, content = getattr(msg, "role", None), getattr(msg, "content", None)
        if role not in ("user", "assistant") or not content:
            continue
        line = f"{'用户' if role == 'user' else '助手'}：{str(content).strip()}"
        if total + len(line) > max_chars:
            break  # 预算耗尽：丢弃更早的轮次
        lines.append(line)
        total += len(line)
    return "\n".join(reversed(lines))  # 逆序收集 → 还原时间正序


_FOLLOWUP_PROMPT = (
    "你是多轮对话助手。用户在连续提问中常省略前文提到的内容"
    "（如前文聊 BGE-M3 后问「它的部署要求呢」，省略了主题）。\n\n"
    "## 历史对话\n{history}\n\n"
    "## 当前问题\n{question}\n\n"
    "请判断：当前问题是否**依赖历史对话才能完整理解**（即追问，包括省略主语/宾语、"
    "用指代词指代前文对象、对前文补充提问等）。\n"
    "仅输出一个 JSON（不要其他文字）：\n"
    '{{"is_followup": true或false, "rewritten": "若 is_followup 为 true，'
    '把当前问题改写为不依赖历史的自包含完整问题（补全省略内容、保留意图与语气）；'
    '若为 false，rewritten 与当前问题完全相同"}}'
)


def rewrite_followup(
    question: str,
    history: list,
    llm_client,
    *,
    max_history: int = 6,
    max_chars: int = 3000,
) -> FollowupResult:
    """追问检测 + 改写：判定当前问题是否依赖历史消息，若是则改写为自包含完整问题。

    调用方应在 rewrite_query 之前调用：改写后的完整问题代替原始问题走意图分类 / 多路改写 /
    检索 / 生成（见 main._ask），让追问不再因缺上下文而检索跑偏、生成答非所问。

    策略（省 LLM 成本）：
    1. 无历史 → 直接非追问（零调用）；
    2. 含指代/承接词或超短问（needs_followup_llm）→ LLM 一次调用同时判定 + 改写；
    3. LLM 返回非法 JSON / 判定非追问 / 改写为空 → 降级为原始问题，不阻断主链路。

    Args:
        question: 用户当前问题
        history: 当前问题之前的历史消息（ChatMessage 或 {role, content} dict，时间正序）
        llm_client: DeepSeekClient 实例（complete）
        max_history: 历史上下文最多携带的消息条数（取最近 N 条）
        max_chars: 历史文本总长上限（超出截断较早轮次）

    Returns:
        FollowupResult：is_followup=True 时 rewritten 为改写后的自包含问题，否则为原始问题。
    """
    if not history:
        return FollowupResult(is_followup=False, rewritten=question, skipped=True)
    if not needs_followup_llm(question, history):
        return FollowupResult(is_followup=False, rewritten=question, skipped=True)

    prompt = _FOLLOWUP_PROMPT.format(
        history=_format_history(history, max_history, max_chars), question=question
    )
    try:
        raw = llm_client.complete(prompt, max_tokens=256)
        json_match = re.search(r"\{[\s\S]*\}", raw or "")
        if not json_match:
            return FollowupResult(is_followup=False, rewritten=question)
        data = json.loads(json_match.group(0))
        is_followup = data.get("is_followup") is True or str(data.get("is_followup", "")).strip().lower() == "true"
        rewritten = str(data.get("rewritten", "") or "").strip()
        if is_followup and rewritten and rewritten != question:
            return FollowupResult(is_followup=True, rewritten=rewritten)
        return FollowupResult(is_followup=False, rewritten=question)
    except Exception:
        # LLM 调用失败 / 响应非法：降级用原始问题，不影响主链路
        return FollowupResult(is_followup=False, rewritten=question)


# ── 意图 → 检索权重 + 参数配置 ──

INTENT_CONFIG = {
    QueryIntent.FACT_LOOKUP:  {"dense_weight": 0.6, "sparse_weight": 0.4, "rerank_top_n": 5, "force_fresh": False},
    QueryIntent.HOW_TO:       {"dense_weight": 0.4, "sparse_weight": 0.6, "rerank_top_n": 5, "force_fresh": False},
    QueryIntent.NEWS:         {"dense_weight": 0.5, "sparse_weight": 0.5, "rerank_top_n": 5, "force_fresh": True},
    QueryIntent.COMPARISON:   {"dense_weight": 0.7, "sparse_weight": 0.3, "rerank_top_n": 5, "force_fresh": False},
    QueryIntent.OPINION:      {"dense_weight": 0.3, "sparse_weight": 0.7, "rerank_top_n": 5, "force_fresh": False},
    QueryIntent.GENERAL:      {"dense_weight": 0.5, "sparse_weight": 0.5, "rerank_top_n": 5, "force_fresh": False},
}


# ── 规则级意图分类（关键词匹配，零成本） ──

_INTENT_RULES: list[tuple[QueryIntent, re.Pattern]] = [
    (QueryIntent.NEWS,        re.compile(r"最新|最近|今天|新闻|刚刚|近期|昨日|昨天|本周|本月|今年|最近.*发生|刚.*发布")),
    (QueryIntent.COMPARISON,  re.compile(r"对比|比较|区别|差异|哪个好|vs\.?|和.*比|哪.*更|哪个.*适合")),
    (QueryIntent.OPINION,     re.compile(r"评价|看法|认为|觉得|怎么样|好不好|建议|推荐|优缺点|是否值得")),
    (QueryIntent.HOW_TO,      re.compile(r"怎么(?!样)|如何|怎样|步骤|方法|教程|安装|配置|部署|操作|使用")),
    (QueryIntent.FACT_LOOKUP, re.compile(r"什么|谁|何时|哪里|多少|定义|概念|什么是|是谁|原因|为什么")),
]


def classify_intent_rule(question: str) -> QueryIntent | None:
    """基于关键词规则快速分类，返回 None 表示规则无法判断（需走 LLM）。"""
    for intent, pattern in _INTENT_RULES:
        if pattern.search(question):
            return intent
    return None


def classify_intent_llm(question: str, llm_client) -> QueryIntent:
    """用 LLM 进行精细意图分类（规则无法判断时调用）。"""
    prompt = (
        "分析以下用户问题的意图类型，仅输出一个标签（不要解释）：\n"
        "- fact_lookup: 查事实、定义、数据、概念\n"
        "- how_to: 操作步骤、方法教程、配置安装\n"
        "- news: 新闻时事、最新动态、近期事件\n"
        "- comparison: 比较对比、差异分析\n"
        "- opinion: 评价、看法、建议、推荐\n"
        "- general: 无法明确归类\n"
        f"\n问题：{question}\n标签："
    )
    try:
        raw = llm_client.complete(prompt, max_tokens=16).strip().lower()
        for intent in QueryIntent:
            if intent.value in raw:
                return intent
    except Exception:
        pass
    return QueryIntent.GENERAL


# ── 多路 Query 改写 ──

_REWRITE_PROMPT = (
    "将以下用户问题改写为信息检索友好版本，输出 JSON：\n"
    '1. "keywords": 提取核心关键词，用空格分隔（中英文混合）\n'
    '2. "formal": 用规范的书面语改写完整问题\n'
    '3. "sub_questions": 如果问题包含多个子问题则拆分，否则只含原问题一个元素\n'
    "\n原始问题：{question}\n输出 JSON："
)


def rewrite_queries(question: str, llm_client) -> list[str]:
    """多路改写：生成关键词版、正式版和子问题版，用于多路检索融合。"""
    try:
        raw = llm_client.complete(_REWRITE_PROMPT.format(question=question), max_tokens=512)
        # 提取 JSON（LLM 可能在前后加 markdown 围栏）
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            return [question]
        data = json.loads(json_match.group(0))
        queries: list[str] = []
        if kw := str(data.get("keywords", "")).strip():
            queries.append(kw)
        if formal := str(data.get("formal", "")).strip():
            queries.append(formal)
        for sq in data.get("sub_questions", []):
            if str(sq).strip():
                queries.append(str(sq).strip())
        return queries if queries else [question]
    except Exception:
        return [question]


# ── HyDE（Hypothetical Document Embeddings） ──

_HYDE_PROMPT = (
    "假设你已掌握相关知识，请针对以下问题写一段回答草稿（100-200字）。\n"
    "不需要完全准确，目的是用这段草稿的语义向量去匹配相关文档。\n"
    "\n问题：{question}\n草稿："
)


def generate_hyde(question: str, llm_client) -> str:
    """HyDE：生成假设性文档草稿，用草稿向量替代问题向量做检索。"""
    try:
        text = llm_client.complete(_HYDE_PROMPT.format(question=question), max_tokens=256)
        return text.strip()
    except Exception:
        return ""


# ── 主入口 ──

def rewrite_query(
    question: str,
    llm_client,
    *,
    enable_intent: bool = True,
    enable_multi_rewrite: bool = True,
    enable_hyde: bool = True,
    client_time: str | None = None,
) -> RewriteResult:
    """Query 预处理主入口。

    Args:
        question: 用户原始问题
        llm_client: DeepSeekClient 实例
        enable_intent: 是否启用意图分类
        enable_multi_rewrite: 是否启用多路改写
        enable_hyde: 是否启用 HyDE 草稿
        client_time: 前端宿主机本地时间（ISO 8601，可选）——时效性锚定的时间来源，
            优先于服务器时间（「近日/今天」相对用户所在机器）；缺省/非法回落服务器时间

    Returns:
        RewriteResult: 意图 + 改写查询列表 + HyDE 文本 + 是否强制联网 + 时效性锚定信息
    """
    # 1. 意图分类（规则优先 → LLM 兜底）
    intent = QueryIntent.GENERAL
    if enable_intent:
        intent = classify_intent_rule(question)
        if intent is None:
            intent = classify_intent_llm(question, llm_client)

    config = INTENT_CONFIG.get(intent, INTENT_CONFIG[QueryIntent.GENERAL])
    result = RewriteResult(
        intent=intent,
        force_fresh=config["force_fresh"],
    )

    # 2. 多路改写
    if enable_multi_rewrite:
        result.rewritten_queries = rewrite_queries(question, llm_client)

    # 3. HyDE
    if enable_hyde:
        result.hyde_text = generate_hyde(question, llm_client)

    # 4. 时效性锚定：检测相对时间词（近日/近期/最新…）→ 注入当前时间（前端宿主机时间优先）。
    #    时间锚定查询并入多路改写列表（联网检索用它命中近期内容），
    #    time_context 随 RewriteResult 传给 main → retriever（搜索词改写）与 LLM（时间基准）。
    if has_time_reference(question):
        result.time_aware = True
        result.time_context = current_time_text(client_time)
        anchored = f"{question}（{result.time_context}）"
        if anchored not in result.rewritten_queries:
            result.rewritten_queries.append(anchored)

    return result
