"""清洗与正文提取：HTML -> Document。

接口契约（docs/api.md §3）：parse(html, url) -> Document
负责人：#3 数据清洗 / 切块。
"""

from __future__ import annotations

import trafilatura

from src.webrag.schemas import Document


def parse(html: str, url: str) -> Document:
    """HTML 去噪（导航/广告/脚本/评论区）→ 正文 + 元数据（标题、发布时间）。

    基于 trafilatura：
    - 正文提取失败（空正文 / 动态页）返回 text="" 的 Document，由调用方明确跳过，
      不静默丢弃；
    - 标题 / 发布时间来自 <meta> / <title>（trafilatura.extract_metadata），取不到为空串。
    """
    if not html or not html.strip():
        return Document(text="", url=url)

    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            include_links=False,
            favor_recall=False,
            output_format="txt",
        )
    except Exception:
        text = None

    meta = None
    try:
        meta = trafilatura.extract_metadata(html)
    except Exception:
        meta = None

    title = (meta.title if meta and meta.title else "").strip()
    # 标题可信度兜底：JS 变量串 / 超长串视为脏标题，调用方回退到搜索标题
    if not title or title.startswith("var ") or len(title) > 120:
        title = ""

    return Document(
        title=title,
        text=(text or "").strip(),
        publish_time=(meta.date if meta and meta.date else "").strip(),
        url=url,
    )
