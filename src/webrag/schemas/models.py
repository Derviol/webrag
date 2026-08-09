"""数据契约（pydantic 模型）。

权威契约：docs/api.md §4；本文件必须与其保持一致。
字段变更 = 破坏性变更，先走 api.md 变更流程（§5）再改代码。
"""

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """多轮对话消息（追问改写的历史上下文，见 query_rewriter.rewrite_followup）。"""

    role: str = Field(..., pattern=r"^(user|assistant)$", description="消息角色：user 用户 / assistant 助手")
    content: str = Field(..., max_length=20000, description="消息正文")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="生成温度（0–2）：控制回答随机性；缺省用 settings.llm.temperature",
    )
    use_web_search: bool = Field(
        default=False,
        description="是否允许联网搜索（默认关闭，opt-in）：False 时仅检索本地知识库（问答缓存 + 离线知识库），"
        "未查到内容返回 EMPTY_RESULT（信息不足）；True 时本地知识库未命中可继续联网兜底",
    )
    web_top_n: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="联网搜索的网页数量（1–20）；缺省用 settings.crawler.top_urls。"
        "仅 use_web_search=True 时生效；抓取页数仍受服务端时延预算封顶",
    )
    client_time: str | None = Field(
        default=None,
        description="前端宿主机的本地时间（ISO 8601，如 2026-08-07T14:30:00+08:00）："
        "问题含「近日/近期/今天」等相对时间词时，以此作为时效性锚定基准（联网搜索词与 LLM 时间基准），"
        "比服务端时间更贴近用户；缺省或解析失败回落服务端本地时间",
    )
    history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=40,
        description="多轮对话历史（当前问题**之前**的消息，按时间正序，role 限 user/assistant）："
        "服务端据此判断当前问题是否为追问，若是则改写为自包含完整问题后再走检索与生成（query_rewriter 追问改写）。"
        "缺省为空——不传则视为单轮提问，不触发追问改写",
    )


class Source(BaseModel):
    index: int  # 引用序号，与回答中的 [n] 一一对应
    title: str
    url: str


class AskResponse(BaseModel):
    answer: str
    sources: list[Source] = []
    direct: bool = False  # True = 检索无结果，LLM 直接作答（兜底，api.md §2）
    cached: bool = False  # True = 命中问答缓存，answer/sources 为历史存储摘要（api.md §2）
    hallucination_risk: str | None = None  # P1: 幻觉检测风险等级（none|low|medium|high）
    hallucination_rate: float | None = None  # P1: 幻觉句占比（0.0~1.0）


class ChunkMetadata(BaseModel):
    url: str = ""
    title: str = ""
    publish_time: str = ""
    seq: int = 0


class Chunk(BaseModel):
    text: str
    metadata: ChunkMetadata = Field(default_factory=ChunkMetadata)


class SearchHit(BaseModel):
    """crawler.search 的输出：候选网页。"""

    title: str
    url: str
    snippet: str = ""


class SearchResult(BaseModel):
    """retriever 的输出：带分数的检索片段。"""

    chunk: Chunk
    score: float


# ── P2: 在线反馈收集 ──


class FeedbackRequest(BaseModel):
    """用户反馈：对某次回答的好/差评。"""

    question: str = Field(..., min_length=1)
    answer: str
    sources: list[Source] = []
    feedback_type: str = Field(..., pattern=r"^(good|bad)$")  # good=👍, bad=👎
    cached: bool = False
    direct: bool = False
    hallucination_risk: str | None = None


class FeedbackRecord(BaseModel):
    """反馈记录（存储到 JSONL）。"""

    timestamp: str
    question: str
    answer: str
    sources: list[Source] = []
    feedback_type: str
    cached: bool = False
    direct: bool = False
    hallucination_risk: str | None = None


class FeedbackStats(BaseModel):
    """反馈统计数据。"""

    total: int = 0
    good: int = 0
    bad: int = 0
    good_rate: float = 0.0
    by_model: dict[str, dict[str, int]] = {}  # {"cached": {good, bad}, "fresh": {good, bad}}
    recent_bad: list[dict[str, str]] = []  # 最近 10 条差评（问题+回答摘要）


class QAHit(BaseModel):
    """问答缓存命中：历史问题的 摘要 + 来源（webrag_qa 检索结果，api.md §4）。"""

    question: str = ""  # 命中的历史问题原文
    summary: str  # 历史存储的摘要（即当时 LLM 生成的回答，含 [n] 引用）
    sources: list[Source] = []  # 历史存储的来源（与摘要中的 [n] 一一对应）
    score: float = 0.0  # 与用户问题的问题向量余弦相似度


class Document(BaseModel):
    """parser.parse 的输出：清洗后的网页正文。"""

    title: str = ""
    text: str
    publish_time: str = ""
    url: str = ""
