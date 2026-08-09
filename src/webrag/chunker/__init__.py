"""文本切块：Document -> list[Chunk]。

接口契约（docs/api.md §3）：chunk(doc, chunk_size, overlap, respect_paragraph) -> list[Chunk]
负责人：#3 数据清洗 / 切块。
"""

from __future__ import annotations

import re

from src.webrag.schemas import Chunk, Document

# 句子边界：中文句号/叹号/问号/分号直接切；英文句点仅在后跟空白/行尾时切
# （避免把小数 3.14、版本号 v2.0 拦腰切断），切块时优先保整句
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;])|(?<=[.])(?=\s|$)")


def chunk(doc: Document, chunk_size: int = 512, overlap: int = 64, respect_paragraph: bool = True) -> list[Chunk]:
    """正文按段落感知切块，相邻块重叠 overlap 字符；块带元数据（url/title/publish_time/seq）。

    - respect_paragraph=True：以空行分段，段内不拦腰（超过 chunk_size 的长段按句/硬切兜底）；
    - respect_paragraph=False：整篇按句切 + 字符上限兜底；
    - overlap 钳制在 [0, chunk_size//2]，保证重叠不会反客为主。
    """
    text = (doc.text or "").strip()
    if not text:
        return []

    chunk_size = max(64, int(chunk_size))
    overlap = min(max(0, int(overlap)), chunk_size // 2)

    if respect_paragraph:
        units: list[str] = []
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if para:
                units.extend(_split_segment(para, chunk_size))
    else:
        units = _split_segment(text, chunk_size)

    chunks: list[str] = []
    cur = ""
    for unit in units:
        if not cur:
            cur = unit
        elif len(cur) + len(unit) + 2 <= chunk_size:
            cur += "\n\n" + unit
        else:
            chunks.append(cur)
            cur = (cur[-overlap:].strip() + "\n\n" + unit) if overlap else unit
    if cur:
        chunks.append(cur)

    return [
        Chunk(
            text=c,
            metadata={
                "url": doc.url,
                "title": doc.title,
                "publish_time": doc.publish_time,
                "seq": i + 1,
            },
        )
        for i, c in enumerate(chunks)
    ]


# ── 小→大（Small-to-Big）两级粒度切块（P0 优化）──


def chunk_two_level(
    doc: Document,
    child_size: int = 256,
    parent_size: int = 1024,
    overlap: int = 64,
    respect_paragraph: bool = True,
) -> tuple[list[Chunk], list[Chunk], dict[int, int]]:
    """两级粒度切块：小 chunk 用于检索（提高精度），大 chunk 送 LLM（保留上下文）。

    Returns:
        child_chunks:  小粒度块列表（用于嵌入和检索）
        parent_chunks: 大粒度块列表（用于送 LLM）
        child_to_parent: child_idx → parent_idx 映射表
    """
    child_chunks = chunk(doc, chunk_size=child_size, overlap=overlap, respect_paragraph=respect_paragraph)
    parent_chunks = chunk(doc, chunk_size=parent_size, overlap=overlap, respect_paragraph=respect_paragraph)

    if not child_chunks or not parent_chunks:
        return child_chunks, parent_chunks, {}

    # 建立 child → parent 映射：每个 child chunk 的文本是哪个 parent chunk 的子串
    child_to_parent: dict[int, int] = {}
    for i, child in enumerate(child_chunks):
        for j, parent in enumerate(parent_chunks):
            if child.text in parent.text:
                child_to_parent[i] = j
                break

    return child_chunks, parent_chunks, child_to_parent


def expand_to_parents(
    results: list,  # list[SearchResult]
    parent_chunks: list[Chunk],
    child_to_parent: dict[int, int],
    child_chunks: list[Chunk],
) -> list:
    """将检索结果中的 child chunk 展开为 parent chunk，去重。

    多个 child 映射到同一 parent 时只保留最高分的那条。
    """
    from src.webrag.schemas import SearchResult

    parent_scores: dict[int, float] = {}  # parent_idx → best_score
    parent_results: dict[int, SearchResult] = {}

    for r in results:
        # 找到这个 SearchResult 对应的 child idx
        child_idx = None
        for ci, cc in enumerate(child_chunks):
            if cc.text == r.chunk.text:
                child_idx = ci
                break
        if child_idx is None:
            # 直接匹配不到，尝试子串匹配
            for ci, cc in enumerate(child_chunks):
                if cc.text in r.chunk.text or r.chunk.text in cc.text:
                    child_idx = ci
                    break
        if child_idx is None:
            continue

        pi = child_to_parent.get(child_idx)
        if pi is None:
            continue

        if pi not in parent_scores or r.score > parent_scores[pi]:
            parent_scores[pi] = r.score
            parent_chunk = parent_chunks[pi]
            parent_results[pi] = SearchResult(
                chunk=Chunk(text=parent_chunk.text, metadata=parent_chunk.metadata),
                score=r.score,
            )

    # 按分数降序返回
    return sorted(parent_results.values(), key=lambda x: x.score, reverse=True)


def _split_segment(segment: str, chunk_size: int) -> list[str]:
    """把一段文本切成 ≤ chunk_size 的单元：优先按句，超长句硬切。"""
    if len(segment) <= chunk_size:
        return [segment]
    pieces = [p for p in _SENTENCE_BOUNDARY.split(segment) if p.strip()]
    units: list[str] = []
    buf = ""
    for piece in pieces:
        if len(piece) > chunk_size:
            if buf:
                units.append(buf)
                buf = ""
            for i in range(0, len(piece), chunk_size):
                units.append(piece[i : i + chunk_size])
        elif len(buf) + len(piece) <= chunk_size:
            buf += piece
        else:
            units.append(buf)
            buf = piece
    if buf:
        units.append(buf)
    return units
