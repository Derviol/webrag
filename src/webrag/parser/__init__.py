"""清洗与正文提取：HTML -> Document。

接口契约（docs/api.md §3）：parse(html, url) -> Document
负责人：#3 数据清洗 / 切块。
"""

from __future__ import annotations

from src.webrag.schemas import Document


def parse(html: str, url: str) -> Document:
    """HTML 去噪（导航/广告/脚本）→ 正文 + 元数据（标题、发布时间）。

    TODO(#3)：基于 trafilatura 实现；空正文/失败页面需明确标记。
    """
    raise NotImplementedError("parse() 待 #3 实现")
