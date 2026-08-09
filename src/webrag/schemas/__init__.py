"""数据契约包。完整契约见 docs/api.md，模型定义见 models.py。"""

from .models import (
    AskRequest,
    AskResponse,
    ChatMessage,
    Chunk,
    ChunkMetadata,
    Document,
    FeedbackRecord,
    FeedbackRequest,
    FeedbackStats,
    QAHit,
    SearchHit,
    SearchResult,
    Source,
)

__all__ = [
    "AskRequest",
    "AskResponse",
    "ChatMessage",
    "Chunk",
    "ChunkMetadata",
    "Document",
    "FeedbackRecord",
    "FeedbackRequest",
    "FeedbackStats",
    "QAHit",
    "SearchHit",
    "SearchResult",
    "Source",
]
