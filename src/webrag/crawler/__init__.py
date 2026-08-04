"""网页采集：搜索 API 适配层 + 抓取。

接口契约（docs/api.md §3）：
- search(query, top_n) -> list[SearchHit]
- fetch(url) -> str（HTML）
负责人：#2 爬虫开发（A：搜索适配层；B：抓取/限速/重试）。
"""

from __future__ import annotations

from src.webrag.schemas import SearchHit


def search(query: str, top_n: int = 5, provider: str = "bing", api_key: str = "") -> list[SearchHit]:
    """按 SEARCH_PROVIDER 调用对应搜索 API，返回候选网页。

    TODO(#2 A)：实现 bing / tavily / bocha 适配层，未实现的 provider 抛错。
    """
    raise NotImplementedError("search() 待 #2 实现")


def fetch(url: str, timeout_seconds: int = 15, delay_seconds: float = 1.0) -> str:
    """抓取网页，返回原始 HTML。

    TODO(#2 B)：限速、失败重试一次、robots 合规检查。
    """
    raise NotImplementedError("fetch() 待 #2 实现")
