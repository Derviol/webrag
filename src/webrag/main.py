"""FastAPI 入口：/ask（问答）、/health（探活）+ 前端静态托管。

负责人：#8 后端 API / 前端。
链路组装（架构文档 §4）：retriever（问答缓存优先检索 + 联网兜底）→ llm（生成 + 引用校验）；
问答缓存命中直接返回历史摘要 + 来源（cached=true，不联网不调 LLM）；联网检索为空时走
llm.generate_direct 直答兜底（direct=true，无来源不入缓存）。
错误码映射见 docs/api.md §1.1：VALIDATION_ERROR / SEARCH_FAILED / TIMEOUT / LLM_FAILED / EMPTY_RESULT / INTERNAL_ERROR。
"""

from __future__ import annotations

import json
import queue
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.webrag import llm, retriever
from src.webrag.accounts import accounts_router, require_login
from src.webrag.admin import AdminDB, admin_router
from src.webrag.chat_routes import chat_router
from src.webrag.config import PROJECT_ROOT, load_settings
from src.webrag.feedback_store import compute_stats, export_stats_json, save_feedback
from src.webrag.hallucination_checker import (
    check_hallucination,
    check_hallucination_fast,
)
from src.webrag.logger import (
    LogConfig,
    RequestMetrics,
    bind_request,
    current_metrics,
    get_logger,
    new_request_id,
    registry,
    setup_logging,
    unbind_request,
)
from src.webrag.milvus_store import MilvusStore
from src.webrag.query_rewriter import rewrite_followup, rewrite_query
from src.webrag.schemas import (
    AskRequest,
    AskResponse,
    FeedbackRequest,
    FeedbackStats,
    SearchResult,
)

settings = load_settings()
log = get_logger("main")


class AppError(Exception):
    """统一错误信封，格式见 docs/api.md §1。"""

    def __init__(self, code: str, message: str, status: int = 500):
        self.code = code
        self.message = message
        self.status = status


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 结构化日志初始化：JSONL（logs/app.log，轮转）+ 控制台；幂等，日志目录自动创建
    setup_logging(
        LogConfig(**{k: v for k, v in vars(settings.logging).items() if k in LogConfig.__dataclass_fields__})
    )
    store = MilvusStore(settings.milvus_uri)
    try:
        store.connect()
    except Exception:
        pass  # Milvus 未启动时服务仍可起，/health 会如实上报
    app.state.store = store
    # 模型预加载（首个 /ask 不再支付 BGE-M3/reranker 加载耗时，~10-60s；
    # uvicorn 等 lifespan 完成后才开始服务）。加载失败仅告警——首次 /ask 仍走懒加载兜底。
    app.state.embedder = None
    try:
        app.state.embedder = retriever.warmup()
    except Exception as exc:
        log.warning("main.warmup_failed", extra={"fields": {"error": str(exc)}})
    # 管理后台（离线知识入库）：MySQL 可用则建表，不可用仅告警——/ask 不受影响，
    # 管理接口按需重试建表并在连接失败时返回 503 统一错误信封
    app.state.admindb = AdminDB(settings)
    try:
        app.state.admindb.ensure_schema()
    except Exception as exc:
        log.warning("admin.db_unavailable", extra={"fields": {"error": str(exc)}})
    yield


app = FastAPI(title="WebRAG", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": "请求参数不合法：" + str(exc.errors())}},
    )


@app.get("/health")
def health() -> dict:
    store: MilvusStore = app.state.store
    return {
        "status": "ok",
        "milvus": store.health(),
        "embed_model": Path(settings.embed_model_path).is_dir(),  # BGE-M3 本地目录就绪
        "embed_model_loaded": getattr(app.state, "embedder", None) is not None,  # 预加载完成
        "llm_temperature": settings.llm.temperature,  # 生成默认温度（前端滑杆初始值）
        "web_top_n": settings.crawler.top_urls,  # 联网搜索网页数量默认值（前端滑杆初始值）
    }


def _map_retrieve_error(exc: Exception) -> AppError:
    """检索/抓取阶段异常 → api.md §1.1 错误码。"""
    from src.webrag.crawler import FetchError

    if isinstance(exc, FetchError):
        code = "TIMEOUT" if ("超时" in str(exc) or "Timeout" in str(exc)) else "SEARCH_FAILED"
        return AppError(code, str(exc))
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return AppError("TIMEOUT", str(exc))
    if isinstance(exc, ValueError):
        return AppError("SEARCH_FAILED", str(exc))
    return AppError("INTERNAL_ERROR", str(exc))


def _make_llm_client(
    settings, timeout_seconds: int | None = None, temperature: float | None = None
) -> llm.DeepSeekClient:
    """构造 LLM client；temperature 为请求级覆盖（None = 用 settings.llm.temperature）。"""
    return llm.DeepSeekClient(
        settings.deepseek_api_key,
        model=settings.llm.model,
        temperature=settings.llm.temperature if temperature is None else temperature,
        max_tokens=settings.llm.max_tokens,
        timeout_seconds=timeout_seconds or settings.llm.timeout_seconds,
    )


def _llm_timeout(settings, deadline: float) -> int:
    """LLM 调用超时 = min(配置值, 预算剩余-2s)，保证整条 /ask 不超 ask_timeout_seconds。"""
    left = int(deadline - time.monotonic()) - 2
    return min(settings.llm.timeout_seconds, max(10, left))


def _time_context_for(rewrite_result) -> str:
    """时效性问题的本地当前时间（query_rewriter 注入，如「2026年8月8日」）；非时效性问题返回空串。

    rewrite_result.time_aware=True 表示问题含「近日/近期/最新」等相对时间词——
    此时把当前时间一并传给 LLM 生成，让模型以当前日期为基准理解时效性表述（命中率优化）。
    """
    if rewrite_result is not None and rewrite_result.time_aware:
        return rewrite_result.time_context
    return ""


def _retrieve_for(req: AskRequest, deadline: float, qvec=None, progress=None, rewrite_result=None, llm_client=None, question: str | None = None) -> list[SearchResult]:
    """本地知识库检索（离线库，不联网）+ 联网兜底（开关开启时）+ 重排 + 预算检查：/ask 与 /ask/stream 共用。失败抛 AppError。

    - use_web_search=False：仅检索本地离线知识库（webrag_offline_kb），结果为空由调用方返回「信息不足」；
    - use_web_search=True：离线结果 + 联网结果合并后统一重排；联网检索失败但离线有结果时降级为离线结果，
      不阻断回答（离线结果为空才把联网错误映射为 AppError，保持原错误语义）；
    - qvec：问答缓存检索已嵌入的问题向量（复用，省一次 CPU 嵌入 ~8s）；
    - rewrite_result：Query 改写结果（意图 + 改写查询 + HyDE，P0 优化）；
    - llm_client：DeepSeek 客户端（供检索链路内多路检索使用）；
    - question：实际检索的问题文本（追问改写后的自包含完整问题，追问业务）；缺省用 req.question；
    - progress：阶段进度回调（/ask/stream 在检索线程内收集，经 SSE status 事件实时转发；
      /ask 整包路径不传，行为不变）。
    """
    q = question or req.question
    store: MilvusStore = app.state.store
    results: list[SearchResult] = []
    try:
        results = retriever.retrieve_offline(
            q,
            store,
            retriever.get_embedder(),
            settings,
            settings.retriever.top_k,
            qvec=qvec,
            progress=progress,
            rewrite_result=rewrite_result,
        )
        if req.use_web_search:
            try:
                results += retriever.retrieve_web(
                    q,
                    store,
                    retriever.get_embedder(),
                    settings,
                    settings.retriever.top_k,
                    qvec=qvec,
                    deadline=deadline,
                    progress=progress,
                    rewrite_result=rewrite_result,
                    llm_client=llm_client,
                    web_top_n=req.web_top_n,
                )
            except NotImplementedError:
                raise
            except Exception as exc:
                if not results:
                    raise
                # 本地知识库已有结果：联网失败仅告警，降级用离线结果继续作答
                log.warning("ask.web_fallback_offline", extra={"fields": {"error": str(exc)}})
        if settings.retriever.enable_rerank and results:
            results = retriever.rerank(q, results, settings, rewrite_result=rewrite_result)
    except NotImplementedError:
        raise
    except Exception as exc:
        raise _map_retrieve_error(exc) from exc

    if time.monotonic() >= deadline - 10:
        raise AppError("TIMEOUT", "处理超时，请稍后重试")
    return results


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, _claims: Annotated[dict, Depends(require_login)]) -> AskResponse:
    """登录后提问：需 `Authorization: Bearer <token>`（require_login，任意角色）；未登录 401。
    请求级指标生命周期包装（主逻辑见 _ask）：request_id / 耗时 / token / 命中 / 首token。

    任何退出路径（成功 / AppError / 未知异常）都会先落一条 ask.completed 事件再返回。
    """
    rid = new_request_id()
    metrics = RequestMetrics(
        rid, req.question, endpoint="ask",
        use_web_search=req.use_web_search, web_top_n=req.web_top_n,
    )
    bind_request(rid, req.question, metrics)
    try:
        return _ask(req, metrics)
    except AppError as exc:
        metrics.set_outcome_if_unset(f"error:{exc.code}")
        raise
    except Exception:
        metrics.set_outcome_if_unset("error:INTERNAL_ERROR")
        raise
    finally:
        metrics.finish()
        unbind_request()


def _ask(req: AskRequest, metrics: RequestMetrics) -> AskResponse:
    """问答链路：缓存优先 → 本地知识库 → 联网兜底（开关开启时）→ 生成 → 引用校验 → 缓存落库（api.md §2）。

    ① 问答缓存检索（webrag_qa 相似问题）：命中直接返回历史摘要 + 来源（cached=True，
    不联网、不调 LLM）；
    ② 未命中 → 本地知识库检索（retriever.retrieve_offline，webrag_offline_kb，不联网）；
    use_web_search=True 时再叠加联网检索（retriever.retrieve_web），两路结果合并后统一重排 →
    LLM 生成 → 引用校验；
    ③ 生成完成后把「用户问题 + 摘要 + 来源」写入问答缓存（best-effort，失败不影响本次回答）；
    ④ 检索为空：use_web_search=False（关闭联网）时返回 EMPTY_RESULT「信息不足」；开启时按
    settings.retriever.enable_llm_direct 走 LLM 直答兜底（direct=True，sources 为空，无来源不入缓存）；
    关闭兜底则同样返回 EMPTY_RESULT。
    整体时延由 server.ask_timeout_seconds 预算约束：检索阶段 deadline 收尾、
    剩余时间不足 10s 时返回 TIMEOUT（而非挂死到前端超时）。

    P0 优化：Query 改写（意图分类 + 多路改写 + HyDE）+ 动态检索权重 + 小→大 chunk。
    """
    deadline = time.monotonic() + settings.server.ask_timeout_seconds

    # 追问业务：判定当前问题是否依赖历史消息（追问），若是则改写为自包含完整问题。
    # 改写后的 question 贯穿 改写管线 / 缓存检索 / 本地与联网检索 / 生成 / 缓存落库；
    # metrics 与日志保留原始问题（req.question）供追溯。任何失败降级用原文，不阻断主流程。
    llm_client = _make_llm_client(settings, _llm_timeout(settings, deadline), temperature=req.temperature)
    question = req.question
    if settings.query_rewriter.enable_followup and req.history:
        try:
            followup = rewrite_followup(
                req.question, req.history, llm_client,
                max_history=settings.query_rewriter.followup_max_history,
                max_chars=settings.query_rewriter.followup_max_chars,
            )
            if followup.is_followup:
                question = followup.rewritten
                log.info(
                    "ask.followup_rewritten",
                    extra={"fields": {"original": req.question, "rewritten": question}},
                )
            metrics.set_followup(followup.is_followup)
        except Exception as exc:
            log.warning("ask.followup_failed", extra={"fields": {"error": str(exc)}})
            # 降级：改写失败不影响主流程，用原始问题继续

    # P0: Query 改写管线（意图分类 + 多路改写 + HyDE）
    rewrite_result = None
    if settings.query_rewriter.enable:
        try:
            rewrite_result = rewrite_query(
                question,
                llm_client,
                enable_intent=settings.query_rewriter.enable_intent,
                enable_multi_rewrite=settings.query_rewriter.enable_multi_rewrite,
                enable_hyde=settings.query_rewriter.enable_hyde,
                client_time=req.client_time,
            )
        except Exception as exc:
            log.warning("ask.rewrite_failed", extra={"fields": {"error": str(exc)}})
            # 降级：改写失败不影响主流程，用原始问题继续

    metrics.mark("rewrite")

    # ① 问答缓存优先：命中直接返回，不联网不调 LLM
    # P0: 时效性问题（news）强制跳过缓存
    skip_cache = rewrite_result is not None and rewrite_result.force_fresh
    if settings.retriever.enable_qa_cache and not skip_cache:
        try:
            hit, qvec = retriever.lookup_qa_cache(
                question,
                app.state.store,
                retriever.get_embedder(),
                settings.milvus_qa_collection,
                settings,
            )
        except Exception as exc:
            raise _map_retrieve_error(exc) from exc
        metrics.mark("cache_lookup")
        if hit is not None:
            metrics.set_cache(True, hit.score)
            resp = AskResponse(answer=hit.summary, sources=hit.sources, cached=True)
            metrics.set_outcome("cache_hit")
            metrics.answer_len = len(resp.answer)
            return resp
        metrics.set_cache(False)
    else:
        qvec = None

    # ② 未命中 → 本地知识库检索（复用查询向量 qvec，避免二次嵌入）+ 联网兜底（开关开启时）
    results = _retrieve_for(req, deadline, qvec=qvec, question=question, rewrite_result=rewrite_result, llm_client=llm_client)
    metrics.mark("retrieve")
    metrics.set_retrieval(len(results))
    if not results:
        # 关闭联网搜索：仅检索本地知识库，未查到内容返回「信息不足」（不走 LLM 直答兜底）
        if not req.use_web_search:
            metrics.set_outcome("empty")
            raise AppError("EMPTY_RESULT", "信息不足：本地知识库中未检索到相关内容，请开启联网搜索或在管理后台补充知识")
        if not settings.retriever.enable_llm_direct:
            metrics.set_outcome("empty")
            raise AppError("EMPTY_RESULT", "检索无结果，无法作答")
        try:
            answer = _make_llm_client(
                settings, _llm_timeout(settings, deadline), temperature=req.temperature
            ).generate_direct(question, time_context=_time_context_for(rewrite_result))
        except Exception as exc:
            raise AppError("LLM_FAILED", f"DeepSeek 直答兜底失败：{exc}") from exc
        metrics.mark("generate")
        resp = AskResponse(answer=answer, sources=[], direct=True)  # 无来源 → 不入问答缓存
        metrics.set_outcome("success")
        metrics.direct = True
        metrics.answer_len = len(answer)
        return resp

    contexts = [r.chunk for r in results]
    try:
        answer = _make_llm_client(
            settings, _llm_timeout(settings, deadline), temperature=req.temperature
        ).generate(question, contexts, time_context=_time_context_for(rewrite_result))
    except Exception as exc:
        raise AppError("LLM_FAILED", f"DeepSeek 调用失败：{exc}") from exc
    metrics.mark("generate")
    metrics.answer_len = len(answer)

    resp = llm.build_response(answer, contexts)

    # P1: 幻觉检测（生成后核验）
    if settings.hallucination_checker.enable:
        try:
            # 先用快速启发式检测（零成本）
            fast_report = check_hallucination_fast(answer, contexts)
            if fast_report.has_hallucination:
                # 有风险 → 再用 LLM 精确核验
                hc_client = _make_llm_client(settings, min(30, _llm_timeout(settings, deadline)))
                report = check_hallucination(
                    answer, contexts, hc_client,
                    enable_auto_rewrite=settings.hallucination_checker.enable_auto_rewrite,
                )
                resp.hallucination_risk = report.risk
                resp.hallucination_rate = report.hallucination_rate
                if report.has_hallucination:
                    log.warning(
                        "ask.hallucination_risk",
                        extra={"fields": {"risk": report.risk, "hallucination_rate": report.hallucination_rate}},
                    )
            else:
                resp.hallucination_risk = "none"
                resp.hallucination_rate = 0.0
        except Exception as exc:
            log.warning("ask.hallucination_check_failed", extra={"fields": {"error": str(exc)}})
    metrics.mark("hallucination_check")

    # ③ 缓存落库：best-effort（失败仅告警，不影响本次回答）
    # 落库键用改写后的完整问题（追问业务）：同义追问改写一致 → 后续同问可命中缓存
    retriever.save_qa_record(
        question, resp, app.state.store, retriever.get_embedder(),
        settings.milvus_qa_collection, settings, qvec=qvec,
    )
    metrics.mark("cache_save")
    metrics.set_outcome("success")
    return resp


@app.post("/ask/stream")
def ask_stream(req: AskRequest, _claims: Annotated[dict, Depends(require_login)]) -> StreamingResponse:
    """登录后提问：需 `Authorization: Bearer <token>`（require_login）；未登录 401（SSE 前 JSON 错误信封）。
    SSE 流式问答（api.md §1.3）：检索阶段实时推送 status 进度事件（正在检索历史问答缓存… / 正在联网搜索…等），
    问答缓存命中同样流式输出（已存摘要分块推 delta，打字机效果，done 仍带 cached=true）；
    未命中则 LLM 生成阶段逐段推送 delta，结束推送 done（含最终 answer+sources），失败推送 error。
    /ask（整包 JSON）保持兼容，二者共用问答缓存 + 联网检索与预算逻辑。
    """
    return StreamingResponse(
        _ask_stream_events(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",  # SSE 禁止中间缓存
            "X-Accel-Buffering": "no",  # 反向代理（nginx）关闭缓冲
        },
    )


def _sse_event(event: str, data: str) -> str:
    """SSE 事件帧：event + data。data 含换行时拆成多行 data:，客户端按 \\n 拼接还原。"""
    lines = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {event}\n{lines}\n\n"


def _sse_error(exc: AppError) -> str:
    """错误事件帧：data 为 {code, message}（错误码同 api.md §1.1）。"""
    payload = json.dumps({"code": exc.code, "message": exc.message}, ensure_ascii=False)
    return _sse_event("error", payload)


def _chunk_text(text: str, size: int = 24) -> list[str]:
    """按 size 字符切块，优先在句末标点（。！？；换行）处断，避免句子被切碎。"""
    chunks: list[str] = []
    i, n = 0, len(text)
    while i < n:
        j = min(i + size, n)
        for k in range(j - 1, i, -1):
            if text[k] in "。！？；\n":
                j = k + 1
                break
        chunks.append(text[i:j])
        i = j
    return chunks


def _stream_answer(stream, deadline: float, parts: list[str]):
    """迭代 LLM 流：逐段 yield SSE delta 帧并累积全文到 parts。

    预算耗尽抛 AppError(TIMEOUT)（调用方按错误事件下发，前端保留已输出片段）；
    底层异常原样上抛，由调用方映射为 LLM_FAILED。
    """
    for delta in stream:
        if time.monotonic() >= deadline - 2:
            raise AppError("TIMEOUT", "生成超时，已输出部分内容")
        if delta:
            m = current_metrics()
            if m is not None:
                m.note_first_token()  # 首个 delta：请求级首 token 时间（TTFT）
            parts.append(delta)
            yield _sse_event("delta", delta)


def _ask_stream_events(req: AskRequest):
    """请求级指标生命周期包装（事件主逻辑见 _ask_stream_impl）：request_id / 耗时 / token / 命中 / TTFT。

    生成器被完整消费或客户端中断时，都会在 finally 落一条 ask.stream.completed 事件。
    """
    rid = new_request_id()
    metrics = RequestMetrics(
        rid, req.question, endpoint="ask.stream",
        use_web_search=req.use_web_search, web_top_n=req.web_top_n,
    )
    bind_request(rid, req.question, metrics)
    try:
        yield from _ask_stream_impl(req, metrics)
    except Exception:
        metrics.set_outcome_if_unset("error:INTERNAL_ERROR")
        raise
    finally:
        metrics.finish()
        unbind_request()


def _ask_stream_impl(req: AskRequest, metrics: RequestMetrics):
    """SSE 事件生成器：status*（检索阶段进度）→ delta*（生成）→ done | error。

    SSE 响应已以 200 开始、无法改状态码，故所有失败（检索/超时/LLM）一律以
    error 事件收尾，异常不外抛，保证事件流闭合。

    P0 优化：Query 改写 + 动态权重 + 强制联网（时效性问题跳缓存）。
    """
    deadline = time.monotonic() + settings.server.ask_timeout_seconds

    # 追问业务：判定当前问题是否依赖历史消息（追问），若是则改写为自包含完整问题。
    # 改写后的 question 贯穿 改写管线 / 缓存检索 / 检索 / 生成 / 缓存落库（与 /ask 一致）；
    # metrics 与日志保留原始问题（req.question）供追溯。任何失败降级用原文，不阻断主流程。
    client = _make_llm_client(settings, _llm_timeout(settings, deadline), temperature=req.temperature)
    question = req.question
    if settings.query_rewriter.enable_followup and req.history:
        try:
            followup = rewrite_followup(
                req.question, req.history, client,
                max_history=settings.query_rewriter.followup_max_history,
                max_chars=settings.query_rewriter.followup_max_chars,
            )
            if followup.is_followup:
                question = followup.rewritten
                log.info(
                    "ask.stream.followup_rewritten",
                    extra={"fields": {"original": req.question, "rewritten": question}},
                )
            metrics.set_followup(followup.is_followup)
        except Exception as exc:
            log.warning("ask.stream.followup_failed", extra={"fields": {"error": str(exc)}})
            # 降级：改写失败不影响主流程，用原始问题继续

    # P0: Query 改写管线（规则 + LLM）
    rewrite_result = None
    if settings.query_rewriter.enable:
        try:
            rewrite_result = rewrite_query(
                question,
                client,
                enable_intent=settings.query_rewriter.enable_intent,
                enable_multi_rewrite=settings.query_rewriter.enable_multi_rewrite,
                enable_hyde=settings.query_rewriter.enable_hyde,
                client_time=req.client_time,
            )
        except Exception:
            pass  # 降级

    metrics.mark("rewrite")

    # 检索是同步阻塞链路（耗时 10-60s），期间生成器无法 yield → 放到独立线程执行，
    # 线程内阶段进度（retriever.progress 回调）经队列实时转发为 SSE status 事件。
    progress_q: queue.Queue[str] = queue.Queue()
    box: dict = {}
    # P0: 把 rewrite_result 和 client 注入检索线程
    box["rewrite_result"] = rewrite_result
    box["llm_client"] = client

    def _retrieve_in_thread() -> None:
        # 子线程：显式绑定请求上下文（thread-local），保证其日志与指标带 request_id
        bind_request(metrics.request_id, req.question, metrics)
        try:
            # P0: 时效性问题跳缓存
            skip_cache = rewrite_result is not None and rewrite_result.force_fresh
            # ① 问答缓存优先：命中即直接出结果，不联网不调 LLM
            qvec = None
            if settings.retriever.enable_qa_cache and not skip_cache:
                hit, qvec = retriever.lookup_qa_cache(
                    question, app.state.store, retriever.get_embedder(),
                    settings.milvus_qa_collection, settings, progress=progress_q.put,
                )
                metrics.mark("cache_lookup")
                if hit is not None:
                    box["hit"] = hit
                    metrics.set_cache(True, hit.score)
                    return
                metrics.set_cache(False)
            # ② 未命中 → 联网兜底检索（复用查询向量）
            box["qvec"] = qvec
            box["results"] = _retrieve_for(
                req, deadline, qvec=qvec, progress=progress_q.put,
                rewrite_result=rewrite_result, llm_client=client, question=question,
            )
            metrics.mark("retrieve")
            metrics.set_retrieval(len(box["results"]))
        except Exception as exc:
            box["error"] = exc

    t = threading.Thread(target=_retrieve_in_thread, name="ask-retrieve", daemon=True)
    t.start()
    while t.is_alive():
        try:
            msg = progress_q.get(timeout=0.1)
        except queue.Empty:
            continue
        yield _sse_event("status", msg)
    while not progress_q.empty():  # 线程收尾瞬间的残余消息
        yield _sse_event("status", progress_q.get_nowait())

    if "error" in box:
        exc = box["error"]
        metrics.set_outcome_if_unset(
            f"error:{exc.code}" if isinstance(exc, AppError) else "error:INTERNAL_ERROR"
        )
        yield _sse_error(exc if isinstance(exc, AppError) else AppError("INTERNAL_ERROR", str(exc)))
        return

    if "hit" in box:  # 问答缓存命中：摘要分块流式输出（内容本地即有，打字机效果呈现）
        hit = box["hit"]
        summary = hit.summary
        metrics.set_outcome("cache_hit")
        metrics.answer_len = len(summary)
        yield _sse_event("status", "⚡ 命中历史问答缓存，正在输出…")
        for chunk in _chunk_text(summary):
            metrics.note_first_token()
            yield _sse_event("delta", chunk)
            time.sleep(0.03)  # 打字机节奏（缓存秒取，纯为前端呈现）
        payload = json.dumps(
            {
                "answer": summary,
                "sources": [s.model_dump() for s in hit.sources],
                "direct": False,
                "cached": True,
            },
            ensure_ascii=False,
        )
        yield _sse_event("done", payload)
        return
    results = box["results"]

    # client 已在上面为 Query 改写创建，直接复用
    # P0: HyDE 草稿也可嵌入用于检索（已在 retriever 内处理）

    if not results:
        # 关闭联网搜索：本地知识库未查到内容 → 信息不足（不走 LLM 直答兜底）
        if not req.use_web_search:
            metrics.set_outcome("empty")
            yield _sse_error(AppError("EMPTY_RESULT", "信息不足：本地知识库中未检索到相关内容，请开启联网搜索或在管理后台补充知识"))
            return
        if not settings.retriever.enable_llm_direct:
            metrics.set_outcome("empty")
            yield _sse_error(AppError("EMPTY_RESULT", "检索无结果，无法作答"))
            return
        yield _sse_event("status", "正在生成回答…")
        parts: list[str] = []
        try:
            yield from _stream_answer(
                client.stream_generate_direct(question, time_context=_time_context_for(rewrite_result)), deadline, parts
            )
        except AppError as exc:
            metrics.set_outcome_if_unset(f"error:{exc.code}")
            yield _sse_error(exc)
            return
        except Exception as exc:
            metrics.set_outcome_if_unset("error:LLM_FAILED")
            yield _sse_error(AppError("LLM_FAILED", f"DeepSeek 直答兜底失败：{exc}"))
            return
        # 直答兜底：无来源、无引用（direct=true，语义同 /ask；不入问答缓存）
        metrics.mark("generate")
        metrics.set_outcome("success")
        metrics.direct = True
        metrics.answer_len = len("".join(parts))
        payload = json.dumps(
            {"answer": "".join(parts), "sources": [], "direct": True, "cached": False},
            ensure_ascii=False,
        )
        yield _sse_event("done", payload)
        return

    contexts = [r.chunk for r in results]
    yield _sse_event("status", "正在生成回答…")
    parts: list[str] = []
    try:
        yield from _stream_answer(
            client.stream_generate(question, contexts, time_context=_time_context_for(rewrite_result)), deadline, parts
        )
    except AppError as exc:
        metrics.set_outcome_if_unset(f"error:{exc.code}")
        yield _sse_error(exc)
        return
    except Exception as exc:
        metrics.set_outcome_if_unset("error:LLM_FAILED")
        yield _sse_error(AppError("LLM_FAILED", f"DeepSeek 调用失败：{exc}"))
        return
    if not parts:
        metrics.set_outcome_if_unset("error:LLM_FAILED")
        yield _sse_error(AppError("LLM_FAILED", "模型返回空回答"))
        return
    metrics.mark("generate")
    answer_text = "".join(parts)
    resp = llm.build_response(answer_text, contexts)

    # P1: 幻觉检测（流式版本）
    h_risk = None
    h_rate = None
    if settings.hallucination_checker.enable:
        try:
            fast_report = check_hallucination_fast(answer_text, contexts)
            if fast_report.has_hallucination:
                hc_client = _make_llm_client(settings, min(30, _llm_timeout(settings, deadline)))
                report = check_hallucination(
                    answer_text, contexts, hc_client,
                    enable_auto_rewrite=settings.hallucination_checker.enable_auto_rewrite,
                )
                h_risk = report.risk
                h_rate = report.hallucination_rate
                if report.has_hallucination:
                    log.warning(
                        "ask.hallucination_risk",
                        extra={"fields": {"risk": report.risk, "hallucination_rate": report.hallucination_rate}},
                    )
                    yield _sse_event("status", f"⚠️ 回答可能包含不确定信息（风险等级：{report.risk}）")
            else:
                h_risk = "none"
                h_rate = 0.0
        except Exception as exc:
            log.warning("ask.hallucination_check_failed", extra={"fields": {"error": str(exc)}})
    metrics.mark("hallucination_check")

    # ③ 缓存落库：best-effort（失败仅告警，不影响已流式返回的内容）
    # 落库键用改写后的完整问题（追问业务）：同义追问改写一致 → 后续同问可命中缓存
    retriever.save_qa_record(
        question, resp, app.state.store, retriever.get_embedder(),
        settings.milvus_qa_collection, settings, qvec=box.get("qvec"),
    )
    metrics.mark("cache_save")
    metrics.set_outcome("success")
    metrics.answer_len = len(answer_text)
    metrics.direct = False
    payload = json.dumps(
        {
            "answer": resp.answer,
            "sources": [s.model_dump() for s in resp.sources],
            "direct": resp.direct,
            "cached": False,
            "hallucination_risk": h_risk,
            "hallucination_rate": h_rate,
        },
        ensure_ascii=False,
    )
    yield _sse_event("done", payload)


# ── P2: 在线反馈收集 ──


@app.post("/feedback", response_model=dict)
def submit_feedback(req: FeedbackRequest) -> dict:
    """提交用户反馈（👍/👎）：追加到 feedback.jsonl，用于评测闭环。

    前端在用户对回答满意/不满意时调用，附带当前问答上下文和幻觉检测结果。
    反馈数据驱动后续检索参数调整和 Prompt 迭代（api.md §2.5）。
    """
    try:
        save_feedback(req)
        log.info(
            "feedback.submitted",
            extra={"fields": {"feedback_type": req.feedback_type, "cached": req.cached, "direct": req.direct}},
        )
        return {"status": "ok", "message": "反馈已记录"}
    except Exception as exc:
        raise AppError("INTERNAL_ERROR", f"反馈存储失败：{exc}") from exc


@app.get("/feedback/stats", response_model=FeedbackStats)
def feedback_stats() -> FeedbackStats:
    """反馈统计：总体好差评率、缓存 vs 新鲜分组、近期差评 Top-10。

    供前端管理面板或定期分析使用。
    """
    try:
        return compute_stats()
    except Exception as exc:
        raise AppError("INTERNAL_ERROR", f"统计查询失败：{exc}") from exc


@app.get("/feedback/export")
def feedback_export() -> dict:
    """导出反馈统计报告（JSON 文件）。"""
    try:
        path = export_stats_json()
        return {"status": "ok", "path": str(path)}
    except Exception as exc:
        raise AppError("INTERNAL_ERROR", f"导出失败：{exc}") from exc


# 管理后台（离线知识入库）路由：/admin/*（登录 + 文档入库管理；鉴权见 admin/auth.py）
app.include_router(admin_router)
app.include_router(accounts_router)
app.include_router(chat_router)


# 进程内日志统计快照（只读，运维/追踪用）：GET /logs/stats
@app.get("/logs/stats")
def logs_stats() -> dict:
    """当前进程内日志统计：请求数 / 缓存命中率 / 耗时 / token / 错误分布。

    命中率等指标同时随 stats.periodic 事件定期落盘（settings.yaml logging.stats_interval），
    本接口仅提供实时快照，不做持久化。
    """
    return registry.snapshot()

# 前端静态页（static/ 存在时挂载；/ask、/health 等路由优先于静态托管）
_static_dir = PROJECT_ROOT / "static"
if (_static_dir / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.server.host, port=settings.server.port)
