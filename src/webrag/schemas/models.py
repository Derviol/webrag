"""数据契约（pydantic 模型）。

权威契约：docs/api.md §4；本文件必须与其保持一致。
字段变更 = 破坏性变更，先走 api.md 变更流程（§5）再改代码。
"""

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class Source(BaseModel):
    index: int  # 引用序号，与回答中的 [n] 一一对应
    title: str
    url: str


class AskResponse(BaseModel):
    answer: str
    sources: list[Source] = []


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


class Document(BaseModel):
    """parser.parse 的输出：清洗后的网页正文。"""

    title: str = ""
    text: str
    publish_time: str = ""
    url: str = ""
