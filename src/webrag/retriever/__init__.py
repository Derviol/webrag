"""检索链路：查询分析 + Top-k 混合检索 + 重排。

接口契约（docs/api.md §3）：retrieve(question, collection) -> list[SearchResult]
负责人：#6 检索链路（A：查询分析/召回；B：重排/调优）。
"""

from __future__ import annotations

from src.webrag.schemas import SearchResult


def analyze_query(question: str) -> dict:
    """查询分析：检索词提取、是否触发联网检索的判断、改写。

    TODO(#6 A)：返回 QueryPlan（检索词、是否需要联网、知识库优先等）。
    """
    raise NotImplementedError("analyze_query() 待 #6 实现")


def retrieve(question: str, collection: str) -> list[SearchResult]:
    """知识库优先 Top-k 混合检索（dense+sparse），候选不足时触发联网抓取。

    TODO(#6)：调用 milvus_store 混合检索 + bge-reranker 重排 + 按序编号。
    """
    raise NotImplementedError("retrieve() 待 #6 实现")
