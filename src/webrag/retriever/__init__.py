"""问答链路：问答缓存优先 + 本地知识库检索 + 联网检索兜底（开关可控）。

新流程（docs/architecture.md §6）：
1. **问答缓存检索**——lookup_qa_cache：嵌入用户问题，到 webrag_qa 检索相似历史问题；
   命中（score ≥ qa_min_score）直接返回历史摘要 + 来源（不联网、不调 LLM）；
2. **本地知识库检索**——retrieve_offline：查管理后台入库的离线知识库（webrag_offline_kb，
   不联网）；问答请求 use_web_search=False 时是唯一检索源，未命中由 main 返回「信息不足」；
3. **联网兜底**——retrieve_web（use_web_search=True 时才执行）：搜索 → 并行抓取 → 清洗切块 → 嵌入 →
   临时 collection（qa_<id>）检索 → 重排，供 LLM 生成带引用回答；
4. **缓存落库**——save_qa_record：生成完成后把「用户问题 + 摘要 + 来源」写入
   webrag_qa（best-effort，失败不阻断响应）。

接口契约（docs/api.md §3）：lookup_qa_cache / retrieve_offline / retrieve_web / save_qa_record
负责人：#6 检索链路（A：查询召回；B：重排/调优）。
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable

from src.webrag import chunker, crawler, parser
from src.webrag.config import load_settings
from src.webrag.embedder import BGE3Embedder, EmbedResult
from src.webrag.logger import get_logger
from src.webrag.milvus_store import MilvusStore
from src.webrag.query_rewriter import INTENT_CONFIG, QueryIntent, RewriteResult
from src.webrag.schemas import AskResponse, Chunk, QAHit, SearchResult

_embedder: BGE3Embedder | None = None
_reranker = None
_log = get_logger("retriever")


def get_embedder() -> BGE3Embedder:
    global _embedder
    if _embedder is None:
        settings = load_settings()
        _embedder = BGE3Embedder(settings.embed_model_path, device=settings.embed_device)
        _embedder.load()
    return _embedder


def _get_reranker():
    """bge-reranker 懒加载；模型缺失/加载失败时返回 None（检索降级为不重排）。

    用 sentence-transformers CrossEncoder 加载（FlagReranker 与 transformers v5 不兼容：
    prepare_for_model 已移除）。
    """
    global _reranker
    if _reranker is not None:
        return _reranker or None
    try:
        from sentence_transformers import CrossEncoder

        settings = load_settings()
        # CPU-only（2026-08-05 起放弃 GPU）：显式 device="cpu"，避免有 CUDA 的机器自动走 GPU
        _reranker = CrossEncoder(settings.reranker_model_path, max_length=1024, device="cpu")
    except Exception as exc:
        _log.warning("retriever.reranker_load_failed", extra={"fields": {"error": str(exc)}})
        _reranker = False
    return _reranker or None


def warmup(settings=None) -> BGE3Embedder:
    """启动时预加载检索模型：BGE-M3 嵌入 + bge-reranker 重排（消除首个 /ask 的加载耗时）。

    - 嵌入模型加载失败抛 RuntimeError（调用方决定降级：保留懒加载兜底）；
    - 重排模型加载失败已在内部降级（本次不重排，检索照常），不抛异常。
    返回已加载的嵌入器（main.py 存入 app.state 供 /ask 复用）。
    """
    settings = settings or load_settings()
    embedder = get_embedder()
    _get_reranker()
    return embedder


def _time_left(deadline: float | None) -> float:
    """距离 /ask 整体预算（main 传入的 deadline）剩余秒数；无预算时视为充足。"""
    return float("inf") if deadline is None else deadline - time.monotonic()


def _notify(progress: Callable[[str], None] | None, message: str) -> None:
    """阶段进度回调：progress 为 None 时静默（/ask 整包路径不展示进度）。"""
    if progress is not None:
        progress(message)


def retrieve_web(
    question: str, store: MilvusStore, embedder: BGE3Embedder, settings, top_k: int, qvec: EmbedResult | None = None,
    deadline: float | None = None,
    progress: Callable[[str], None] | None = None,
    rewrite_result: RewriteResult | None = None,
    llm_client=None,
    web_top_n: int | None = None,
) -> list[SearchResult]:
    """联网链路（问答缓存未命中后的兜底检索）：搜索 → 并行抓取 → 清洗切块 → 嵌入 → 临时 collection（qa_<id>）→ 检索 → 用后即清。

    P0 优化：
    - 动态权重：根据 query_rewriter 的意图分类，调整 dense/sparse WeightedRanker 权重；
    - 小→大 chunk：检索用小 chunk (256)，结果展开为大 chunk (1024) 送 LLM；
    - 多路检索融合：改写后的多个 query 分别检索，RRF 融合结果。

    时延控制（CPU 嵌入 ~1.5s/块，.reasonix/bench.py）：
    - 总嵌入块数封顶 max_web_chunks_total（默认 24 ≈ 36s），按页取正文主体在前；
    - 抓取预算：deadline 有限时按剩余时间缩放抓取页数、抓取超时封顶 10s——
      否则抓取阶段可能吃光预算（实测 5 页 72s），嵌入被截断，临时库为空，本轮白跑；
    - deadline 预算（main 传入）剩余 <30s 时不再嵌入新页，给临时库检索/重排/LLM 留时间。
    web_top_n（请求级，1–20）：联网搜索的网页数量，缺省 settings.crawler.top_urls——
    决定搜索 API 的 top_n 与抓取页数上限；抓取页数仍受 deadline 预算封顶（超预算按预算抓取）。
    """
    remaining = _time_left(deadline)
    if remaining < 30:  # 预算不足：跳过联网（给临时库检索/重排/LLM 留时间）
        _log.warning("retriever.web_budget_insufficient", extra={"fields": {"remaining_s": round(remaining, 1)}})
        return []

    _notify(progress, "正在联网搜索…")
    # 请求级网页数量：web_top_n（1–20）覆盖 settings.crawler.top_urls；抓取页数仍受 deadline 预算封顶
    search_top_n = max(1, min(int(web_top_n), 20)) if web_top_n else settings.crawler.top_urls
    # 时效性锚定：问题含「近日/近期/最新」等相对时间词时（query_rewriter 已注入本地当前时间），
    # 把具体日期拼进搜索词——搜索引擎对带明确日期/时间窗的查询更易返回近期内容（命中率优化）
    search_query = question
    if rewrite_result is not None and rewrite_result.time_aware and rewrite_result.time_context:
        search_query = f"{question}（{rewrite_result.time_context}）"
    hits = crawler.search(
        search_query,
        top_n=search_top_n,
        provider=settings.search_provider,
        api_key=settings.search_api_key,
    )
    if not hits:
        return []

    # 抓取预算：每页最坏 ~2×超时+5s（robots 检查 + 超时 + 重试一次），
    # 按剩余时间缩放抓取页数，保证抓取后仍有预算嵌入。
    fetch_timeout = min(settings.crawler.request_timeout_seconds, 10)
    if deadline is not None:
        n_pages = max(1, min(len(hits), search_top_n, int(remaining / (fetch_timeout * 2 + 5))))
    else:
        n_pages = min(len(hits), search_top_n)

    # 小→大 chunk 配置
    use_two_level = settings.chunker.enable_two_level
    all_parent_chunks: dict[int, list[Chunk]] = {}   # url_idx → parent_chunks
    all_child_mappings: dict[int, dict[int, int]] = {}  # url_idx → child_to_parent

    docs: list[tuple[list[Chunk], EmbedResult]] = []
    hit_by_url = {h.url: h for h in hits}
    _notify(progress, f"正在抓取网页（共 {n_pages} 页）…")
    fetched = crawler.fetch_many(
        [h.url for h in hits[:n_pages]],
        timeout_seconds=fetch_timeout,
        delay_seconds=settings.crawler.request_delay_seconds,
    )
    remaining_chunks = settings.retriever.max_web_chunks_total
    url_idx = 0
    for i, (url, html) in enumerate(fetched, 1):
        if remaining_chunks <= 0:
            break
        if _time_left(deadline) < 30:  # 预算收尾：给临时库检索 + 重排 + LLM 留时间
            _log.warning("retriever.web_budget_embedding_stopped", extra={"fields": {"remaining_s": round(_time_left(deadline), 1)}})
            break
        if not html:
            _log.warning("retriever.fetch_failed", extra={"fields": {"url": url}})
            continue
        _notify(progress, f"正在清洗并嵌入网页内容（{i}/{len(fetched)}）…")
        try:
            doc = parser.parse(html, url)
            if not doc.text:
                continue
            if not doc.title and url in hit_by_url:
                doc.title = hit_by_url[url].title

            if use_two_level:
                # 两级粒度：小块嵌入检索 + 大块送 LLM
                child_chunks, parent_chunks, child_to_parent = chunker.chunk_two_level(
                    doc,
                    child_size=settings.chunker.child_chunk_size,
                    parent_size=settings.chunker.parent_chunk_size,
                    overlap=settings.chunker.overlap,
                    respect_paragraph=settings.chunker.respect_paragraph,
                )
                if not child_chunks:
                    continue
                all_parent_chunks[url_idx] = parent_chunks
                all_child_mappings[url_idx] = child_to_parent
                chunks = child_chunks
                url_idx += 1
            else:
                chunks = chunker.chunk(
                    doc,
                    chunk_size=settings.chunker.chunk_size,
                    overlap=settings.chunker.overlap,
                    respect_paragraph=settings.chunker.respect_paragraph,
                )
                if not chunks:
                    continue
            # CPU 嵌入时延控制：每页最多取前 max_chunks_per_page 块（正文主体在前），
            # 且全链路不超过 max_web_chunks_total 块（防 5 页×12 块=60 块≈90s 超时）
            chunks = chunks[: min(settings.retriever.max_chunks_per_page, remaining_chunks)]
            remaining_chunks -= len(chunks)
            emb = embedder.embed([c.text for c in chunks])
            docs.append((chunks, emb))
        except Exception as exc:
            _log.warning("retriever.fetch_clean_failed", extra={"fields": {"url": url, "error": str(exc)}})

    if not docs:
        return []

    _notify(progress, "正在检索临时网页库…")

    # P0: 动态权重——根据意图调整 dense/sparse 比例
    if rewrite_result:
        intent_config = INTENT_CONFIG.get(rewrite_result.intent, INTENT_CONFIG[QueryIntent.GENERAL])
        dense_w, sparse_w = intent_config["dense_weight"], intent_config["sparse_weight"]
    else:
        dense_w, sparse_w = 0.5, 0.5

    # P0: 多路查询融合——原始问题 + 改写查询 + HyDE 草稿
    search_queries: list[tuple[str, EmbedResult | None]] = [(question, qvec)]
    if rewrite_result:
        for rq in rewrite_result.rewritten_queries:
            if rq and rq != question:
                search_queries.append((rq, None))  # None = 需要嵌入
        if rewrite_result.hyde_text and rewrite_result.hyde_text != question:
            search_queries.append((f"hyde:{rewrite_result.hyde_text}", None))

    tmp_name = f"qa_{uuid.uuid4().hex[:8]}"
    try:
        if not store.has_collection(tmp_name):
            store.create_collection(tmp_name)
        for chunks, emb in docs:
            store.add(tmp_name, chunks, emb)

        # 多路检索 + RRF 融合
        all_results: list[SearchResult] = []
        for sq, sq_vec in search_queries:
            vec = sq_vec or embedder.embed([sq])
            results = store.search(tmp_name, vec, top_k, dense_weight=dense_w, sparse_weight=sparse_w)
            all_results.extend(results)

        # RRF 融合去重（Reciprocal Rank Fusion）
        merged = _rrf_fuse(all_results, k=60)
    finally:
        try:
            store.drop_collection(tmp_name)
        except Exception:
            pass

    # P0: 小→大 chunk 展开
    if use_two_level and all_parent_chunks:
        expanded = _expand_all_to_parents(merged, docs, all_parent_chunks, all_child_mappings)
        if expanded:
            merged = expanded

    _log.info(
        "retriever.web_search",
        extra={"fields": {"pages": len(fetched), "chunks": sum(len(c) for c, _ in docs), "results": len(merged[:top_k])}},
    )
    return merged[:top_k]


def retrieve_offline(
    question: str,
    store: MilvusStore,
    embedder: BGE3Embedder,
    settings,
    top_k: int,
    qvec: EmbedResult | None = None,
    progress: Callable[[str], None] | None = None,
    rewrite_result: RewriteResult | None = None,
) -> list[SearchResult]:
    """本地知识库检索（离线库 webrag_offline_kb，dense+sparse 混合，不联网）。

    开关关闭（AskRequest.use_web_search=False）时 /ask 的唯一检索源；开关开启时结果与
    联网检索合并（main._retrieve_for）。
    - 离线库未建 / 为空 / Milvus 异常 → []（main 据此返回「信息不足」，不阻断服务）；
    - qvec 复用 lookup_qa_cache 的嵌入结果（省一次 CPU 嵌入 ~8s）；
    - P0 动态权重：与联网链路一致，按意图调整 dense/sparse 比例。
    """
    _notify(progress, "正在检索本地知识库…")
    collection = settings.milvus_offline_collection
    if not collection:
        return []
    try:
        if not store.has_collection(collection):
            return []
        vec = qvec or embedder.embed([question])
        if rewrite_result:
            intent_config = INTENT_CONFIG.get(rewrite_result.intent, INTENT_CONFIG[QueryIntent.GENERAL])
            dense_w, sparse_w = intent_config["dense_weight"], intent_config["sparse_weight"]
        else:
            dense_w, sparse_w = 0.5, 0.5
        results = store.search(collection, vec, top_k, dense_weight=dense_w, sparse_weight=sparse_w)
        _log.info("retriever.offline_search", extra={"fields": {"hits": len(results)}})
        return results
    except Exception as exc:
        # 本地库异常降级为空结果：main 返回「信息不足」，绝不因本地库故障抛 5xx
        _log.warning("retriever.offline_search_failed", extra={"fields": {"error": str(exc)}})
        return []


def _rrf_fuse(results: list[SearchResult], k: int = 60) -> list[SearchResult]:
    """RRF（Reciprocal Rank Fusion）融合多路检索结果，去重取最高分。"""
    # 按 chunk.text 去重，同一文本保留最高分
    seen: dict[str, float] = {}
    for r in results:
        key = r.chunk.text
        if key not in seen or r.score > seen[key]:
            seen[key] = r.score
    # 按分数降序
    merged = sorted(
        [SearchResult(chunk=r.chunk, score=seen[r.chunk.text]) for r in results if r.chunk.text in seen],
        key=lambda x: x.score, reverse=True,
    )
    # 真正去重输出
    unique: list[SearchResult] = []
    seen_texts: set[str] = set()
    for r in merged:
        if r.chunk.text not in seen_texts:
            seen_texts.add(r.chunk.text)
            unique.append(r)
    return unique


def _expand_all_to_parents(
    merged: list[SearchResult],
    docs: list[tuple[list[Chunk], EmbedResult]],
    all_parent_chunks: dict[int, list[Chunk]],
    all_child_mappings: dict[int, dict[int, int]],
) -> list[SearchResult]:
    """把所有检索结果的 child chunk 展开为 parent chunk。"""
    # 构建全局 child→parent 映射
    global_child_chunks: list[Chunk] = []
    global_parent_chunks: list[Chunk] = []
    global_mapping: dict[int, int] = {}  # global_child_idx → global_parent_idx
    child_offset = 0

    for url_idx, (child_chunks, _) in enumerate(docs):
        parents = all_parent_chunks.get(url_idx, [])
        mapping = all_child_mappings.get(url_idx, {})
        if not parents or not mapping:
            continue
        global_child_chunks.extend(child_chunks)
        global_parent_chunks.extend(parents)
        for ci, pi in mapping.items():
            global_mapping[child_offset + ci] = len(global_parent_chunks) - len(parents) + pi
        child_offset += len(child_chunks)

    if not global_mapping:
        return merged

    return chunker.expand_to_parents(merged, global_parent_chunks, global_mapping, global_child_chunks)


def rerank(question: str, results: list[SearchResult], settings, rewrite_result: RewriteResult | None = None) -> list[SearchResult]:
    """bge-reranker 精排：对 Top-k 按 query↔chunk 相关度重排，截断到 rerank_top_n（main 在联网检索后调用）。

    P0: 根据意图动态调整 rerank_top_n。
    P1: 重排后上下文去重（Jaccard n-gram）+ 来源质量分层。
    """
    reranker = _get_reranker()
    if reranker is None:
        results = _apply_p1_postprocessing(results, settings)
        return results
    pairs = [[question, r.chunk.text] for r in results]
    try:
        import torch

        scores = reranker.predict(
            pairs,
            activation_fn=torch.nn.Sigmoid(),  # v1 模型输出 logits → 归一化到 [0,1]
            show_progress_bar=False,
        )
        scores = [float(s) for s in scores]
    except Exception as exc:
        _log.warning("retriever.rerank_failed", extra={"fields": {"error": str(exc)}})
        results = _apply_p1_postprocessing(results, settings)
        return results
    ranked = sorted(zip(results, scores), key=lambda rs: rs[1], reverse=True)

    # P0: 动态 rerank_top_n
    if rewrite_result:
        intent_config = INTENT_CONFIG.get(rewrite_result.intent, INTENT_CONFIG[QueryIntent.GENERAL])
        n = intent_config["rerank_top_n"]
    else:
        n = settings.retriever.rerank_top_n or len(results)

    min_score = getattr(settings.retriever, "rerank_min_score", 0.0)
    ranked = [(r, s) for r, s in ranked if s >= min_score]
    results = [SearchResult(chunk=r.chunk, score=float(s)) for r, s in ranked[:n]]

    # P1: 后处理（去重 + 质量分层）
    results = _apply_p1_postprocessing(results, settings)
    return results


def _apply_p1_postprocessing(results: list[SearchResult], settings) -> list[SearchResult]:
    """P1 后处理管线：上下文去重 → 来源质量分层 → 按质量分微调排序。"""
    if not results:
        return results

    # ① 上下文去重
    if getattr(settings.retriever, "enable_context_dedup", False):
        threshold = getattr(settings.retriever, "dedup_jaccard_threshold", 0.75)
        ngram_n = getattr(settings.retriever, "dedup_ngram_n", 3)
        results = _deduplicate_contexts(results, threshold=threshold, ngram_n=ngram_n)

    # ② 来源质量分层
    if getattr(settings.retriever, "enable_source_quality", False):
        results = _apply_source_quality(results)

    return results


def _ngrams(text: str, n: int = 3) -> set[str]:
    """生成 n-gram 字符集（用于 Jaccard 去重）。"""
    text = text.strip()
    if len(text) < n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _jaccard_ngram(text1: str, text2: str, n: int = 3) -> float:
    """两个文本的 n-gram Jaccard 相似度。"""
    ng1, ng2 = _ngrams(text1, n), _ngrams(text2, n)
    if not ng1 or not ng2:
        return 0.0
    return len(ng1 & ng2) / len(ng1 | ng2)


def _deduplicate_contexts(
    results: list[SearchResult], threshold: float = 0.75, ngram_n: int = 3,
) -> list[SearchResult]:
    """n-gram Jaccard 去重：相同网页或高度相似的 chunk 只保留第一个（最高分）。

    按 rerank 分数降序遍历，跳过与已保留 chunk 的 n-gram Jaccard ≥ threshold 的重复项。
    """
    if len(results) <= 1:
        return results
    kept: list[SearchResult] = []
    kept_urls: set[str] = set()
    for r in results:
        url = r.chunk.metadata.url
        # 同 URL 只保留第一个（最高分）
        if url and url in kept_urls:
            continue
        # n-gram Jaccard 去重
        is_dup = False
        for k in kept:
            sim = _jaccard_ngram(r.chunk.text, k.chunk.text, ngram_n)
            if sim >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(r)
            if url:
                kept_urls.add(url)
    return kept


# P1: 来源质量分层——域名白名单加分
_SOURCE_WHITELIST: dict[str, float] = {
    # 高权威源（+0.15 分）
    "wikipedia.org": 0.15,
    "github.com": 0.10,
    "docs.python.org": 0.15,
    "developer.mozilla.org": 0.15,
    "arxiv.org": 0.12,
    "stackoverflow.com": 0.08,
    # 官方文档源（+0.10 分）
    "pypi.org": 0.10,
    "npmjs.com": 0.10,
    "pytorch.org": 0.12,
    "tensorflow.org": 0.12,
    # 新闻源（+0.05 分）
    "bbc.com": 0.08,
    "reuters.com": 0.08,
    "zhihu.com": 0.03,
    "csdn.net": 0.02,
    "juejin.cn": 0.02,
}


def _domain_quality_score(url: str) -> float:
    """根据域名白名单返回质量加分（0.0 ~ 0.15）。"""
    if not url:
        return 0.0
    import re
    for domain, bonus in _SOURCE_WHITELIST.items():
        if re.search(re.escape(domain), url, re.IGNORECASE):
            return bonus
    return 0.0


def _apply_source_quality(results: list[SearchResult]) -> list[SearchResult]:
    """为每个结果附加来源质量分（domain_quality），并按 0.9*rerank_score + 0.1*quality 微调排序。

    不改变原始 score 字段，仅在排序时做微调——质量分的影响不会扭曲 reranker 的核心决策。
    """
    if not results:
        return results
    for r in results:
        quality = _domain_quality_score(r.chunk.metadata.url)
        # 附加质量分到元数据（供后续引用校验或前端展示）
        r.chunk.metadata.__dict__["quality_score"] = quality
    # 微调排序：rerank_score 主导 (0.9)，质量分辅助 (0.1)
    results.sort(key=lambda r: 0.9 * r.score + 0.1 * getattr(r.chunk.metadata, "quality_score", 0.0), reverse=True)
    return results


def lookup_qa_cache(
    question: str,
    store: MilvusStore,
    embedder: BGE3Embedder,
    collection: str,
    settings,
    progress: Callable[[str], None] | None = None,
) -> tuple[QAHit | None, EmbedResult]:
    """问答缓存检索（/ask 第一步）：嵌入用户问题 → 检索历史相似问题（webrag_qa）。

    P1 优化：在 dense 向量匹配基础上，叠加 jieba 关键词 Jaccard 相似度作为辅助分。
    综合分 = (1 - w) * dense + w * jaccard，减少「换一种问法但关键词重合」的缓存漏检。

    返回 (hit, qvec)：
    - hit：Top-1 综合分数 ≥ qa_min_score 的历史记录（摘要 + 来源），
      命中即由 main.py 直接返回（不联网、不调 LLM）；否则 None；
    - qvec：本次嵌入的问题向量，未命中时复用于联网检索（省一次 CPU 嵌入 ~8s）；
    - 缓存库不可用（未建 collection / Milvus 异常）→ (None, qvec) 自动降级联网，不阻断服务。
    """
    _notify(progress, "正在检索历史问答缓存…")
    qvec = embedder.embed([question])
    try:
        hits = store.search_qa(collection, qvec, settings.retriever.qa_top_k)
    except Exception as exc:
        _log.warning("retriever.cache_search_failed", extra={"fields": {"error": str(exc)}})
        return None, qvec
    if not hits:
        _log.info("retriever.cache_lookup", extra={"fields": {"hit": False, "candidates": 0, "threshold": settings.retriever.qa_min_score}})
        return None, qvec

    # P1: jieba 关键词 Jaccard 辅助评分
    jaccard_weight = getattr(settings.retriever, "qa_jaccard_weight", 0.0)
    jaccard_min = getattr(settings.retriever, "qa_jaccard_min", 0.0)

    if jaccard_weight > 0:
        try:
            import jieba

            q_keywords = set(jieba.cut(question))
            best_combined = -1.0
            best_hit: QAHit | None = None

            for hit in hits:
                c_keywords = set(jieba.cut(hit.question)) if hit.question else set()
                if not q_keywords or not c_keywords:
                    jaccard = 0.0
                else:
                    intersection = len(q_keywords & c_keywords)
                    union = len(q_keywords | c_keywords)
                    jaccard = intersection / union if union > 0 else 0.0

                # 仅对 jaccard 足够高的候选启用辅助评分
                if jaccard >= jaccard_min:
                    combined = (1 - jaccard_weight) * hit.score + jaccard_weight * jaccard
                else:
                    combined = hit.score

                if combined > best_combined:
                    best_combined = combined
                    best_hit = hit

            if best_hit is not None and best_combined >= settings.retriever.qa_min_score:
                _notify(progress, "命中历史问答缓存，直接返回")
                _log.info("retriever.cache_lookup", extra={"fields": {"hit": True, "score": round(best_combined, 4), "threshold": settings.retriever.qa_min_score, "candidates": len(hits)}})
                return best_hit, qvec
            _log.info("retriever.cache_lookup", extra={"fields": {"hit": False, "score": round(best_combined, 4), "threshold": settings.retriever.qa_min_score, "candidates": len(hits)}})
            return None, qvec
        except ImportError:
            # jieba 未安装 → 降级为纯 dense
            pass

    # 无 jaccard → 原始 dense-only 逻辑
    best = hits[0]
    if best.score >= settings.retriever.qa_min_score:
        _notify(progress, "命中历史问答缓存，直接返回")
        _log.info("retriever.cache_lookup", extra={"fields": {"hit": True, "score": round(best.score, 4), "threshold": settings.retriever.qa_min_score, "candidates": len(hits)}})
        return best, qvec
    _log.info("retriever.cache_lookup", extra={"fields": {"hit": False, "score": round(best.score, 4), "threshold": settings.retriever.qa_min_score, "candidates": len(hits)}})
    return None, qvec


def save_qa_record(
    question: str,
    response: AskResponse,
    store: MilvusStore,
    embedder: BGE3Embedder,
    collection: str,
    settings,
    qvec: EmbedResult | None = None,
) -> bool:
    """把「用户问题 + 摘要 + 来源」写入问答缓存（/ask 未命中、生成完成后调用）。

    规则（api.md §2）：
    - 无来源或空摘要（如 LLM 直答兜底 direct=true）→ 不入库，返回 False；
    - 任一环节失败仅告警，不抛异常——缓存写入绝不影响本次回答（best-effort）；
    - qvec 可复用 lookup_qa_cache 的嵌入结果，避免重复嵌入（省 ~8s）。
    """
    try:
        if not response or not response.answer or not response.sources:
            return False
        sources_json = json.dumps([s.model_dump() for s in response.sources], ensure_ascii=False)
        if qvec is None:
            qvec = embedder.embed([question])
        n = store.add_qa(collection, [question], [response.answer], [sources_json], qvec)
        return n > 0
    except Exception as exc:
        _log.warning("retriever.cache_write_failed", extra={"fields": {"error": str(exc)}})
        return False
