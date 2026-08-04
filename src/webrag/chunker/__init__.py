"""文本切块：Document -> list[Chunk]。

接口契约（docs/api.md §3）：chunk(doc) -> list[Chunk]
负责人：#3 数据清洗 / 切块。
"""

from __future__ import annotations

from src.webrag.schemas import Chunk, Document


def chunk(doc: Document, chunk_size: int = 512, overlap: int = 64, respect_paragraph: bool = True) -> list[Chunk]:
    """正文按标题/段落感知切块，相邻块重叠；块带元数据（url/title/seq）。

    TODO(#3)：块大小与 BGE-M3 输入上限匹配，参数从 config 读取。
    """
    raise NotImplementedError("chunk() 待 #3 实现")
