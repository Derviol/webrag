"""/ask、/health 接口测试：mock 问答缓存、联网检索与 LLM，验证链路组装与引用渲染（离线）。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.webrag.main as main_mod
from src.webrag.accounts import require_login as app_require_login
from src.webrag.schemas import Chunk, ChunkMetadata, QAHit, SearchResult, Source


@pytest.fixture
def client(monkeypatch):
    # lifespan 会尝试连接 Milvus（本机可能未启动，连接阻塞 ~10s/次）→ mock 掉
    monkeypatch.setattr(main_mod.MilvusStore, "connect", lambda self: None)
    monkeypatch.setattr(main_mod.MilvusStore, "health", lambda self: False)
    # 模型预加载（warmup 会真实加载 BGE-M3/reranker，~10-60s）→ mock 掉，保持测试离线快速
    monkeypatch.setattr(main_mod.retriever, "get_embedder", lambda: None)
    monkeypatch.setattr(main_mod.retriever, "_get_reranker", lambda: None)

    # 默认：问答缓存未命中 → 走联网兜底
    def fake_lookup(question, store, embedder, collection, settings, **kw):
        return None, SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])

    monkeypatch.setattr(main_mod.retriever, "lookup_qa_cache", fake_lookup)

    # 默认：本地知识库（离线库）无命中 → 走联网兜底
    def fake_retrieve_offline(question, store, embedder, settings, top_k, **kw):
        return []

    monkeypatch.setattr(main_mod.retriever, "retrieve_offline", fake_retrieve_offline)

    def fake_retrieve_web(question, store, embedder, settings, top_k, **kw):
        return [
            SearchResult(
                chunk=Chunk(
                    text="BGE-M3 一次前向同时输出稠密与稀疏向量。",
                    metadata=ChunkMetadata(url="https://x.com/1", title="BGE-M3 文档", seq=1),
                ),
                score=0.91,
            )
        ]

    monkeypatch.setattr(main_mod.retriever, "retrieve_web", fake_retrieve_web)
    # P0: rerank 会收到 rewrite_result 关键字参数 → **k 吸收，保持原语义（不重排）
    monkeypatch.setattr(main_mod.retriever, "rerank", lambda q, results, s, **k: results)
    monkeypatch.setattr(main_mod.retriever, "save_qa_record", lambda *a, **k: True)

    def fake_generate(self, question, contexts, **kw):
        return "BGE-M3 支持 dense + sparse 双向量[1]。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", fake_generate)
    # P0: Query 改写链路会调 complete()（多路改写/HyDE）→ mock 为空串，改写降级为原始问题，保持离线
    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "complete", lambda self, prompt, max_tokens=None: "")

    # 登录依赖：/ask 需登录（require_login），测试内统一放行（未登录 401 由 test_admin 的用例覆盖）
    main_mod.app.dependency_overrides[app_require_login] = lambda: {"sub": "tester", "role": "user", "uid": "tester_uid"}

    with TestClient(main_mod.app) as c:
        yield c
    main_mod.app.dependency_overrides.clear()


def test_health_returns_ok_shape(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "milvus" in body  # 布尔值（Milvus 未启动时为 False，不阻塞服务）
    assert "embed_model" in body
    assert body["llm_temperature"] == main_mod.settings.llm.temperature
    # 联网搜索网页数量默认值（前端滑杆初始值 = settings.crawler.top_urls）
    assert body["web_top_n"] == main_mod.settings.crawler.top_urls


# ---- /ask：未命中缓存 → 联网兜底全链路 ----

def test_ask_returns_answer_with_sources(client):
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "use_web_search": True})
    assert resp.status_code == 200
    body = resp.json()
    assert "[1]" in body["answer"]
    assert len(body["sources"]) == 1
    assert body["sources"][0]["index"] == 1
    assert body["sources"][0]["url"] == "https://x.com/1"
    assert body["sources"][0]["title"] == "BGE-M3 文档"
    assert body["cached"] is False
    assert body["direct"] is False


def test_ask_cache_hit_returns_stored_summary(client, monkeypatch):
    """问答缓存命中：直接返回已存摘要 + 来源（cached=true），不联网、不调 LLM。"""
    hit = QAHit(
        question="BGE-M3 支持双向量吗？",
        summary="支持，dense + sparse 双向量。[1]",
        sources=[Source(index=1, title="BGE-M3 文档", url="https://x.com/1")],
        score=0.95,
    )
    monkeypatch.setattr(
        main_mod.retriever, "lookup_qa_cache",
        lambda *a, **k: (hit, SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])),
    )

    # 命中路径绝不该走到联网检索 / LLM
    monkeypatch.setattr(main_mod.retriever, "retrieve_web", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应联网")))
    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", lambda self, q, c, **k: (_ for _ in ()).throw(AssertionError("不应调 LLM")))

    resp = client.post("/ask", json={"question": "BGE-M3 支持双向量吗？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["answer"] == "支持，dense + sparse 双向量。[1]"
    assert body["sources"][0]["url"] == "https://x.com/1"


def test_ask_cache_disabled_goes_web(client, monkeypatch):
    """enable_qa_cache=false：跳过缓存检索，直接联网兜底。"""
    monkeypatch.setattr(main_mod.settings.retriever, "enable_qa_cache", False)
    monkeypatch.setattr(main_mod.retriever, "lookup_qa_cache", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应查缓存")))
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "use_web_search": True})
    assert resp.status_code == 200
    assert "[1]" in resp.json()["answer"]


def test_ask_empty_result_llm_direct_fallback(client, monkeypatch):
    """兜底：联网检索为空 → LLM 直答（direct=true，无 sources，不入缓存）。"""
    monkeypatch.setattr(main_mod.retriever, "retrieve_web", lambda *a, **k: [])
    saved = []
    monkeypatch.setattr(main_mod.retriever, "save_qa_record", lambda *a, **k: saved.append(True) or True)

    def fake_direct(self, question, **kw):
        return "未检索到相关资料，直接回答如下……"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate_direct", fake_direct)

    resp = client.post("/ask", json={"question": "不存在的冷门问题", "use_web_search": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["direct"] is True
    assert body["sources"] == []
    assert "直接回答" in body["answer"]
    assert saved == []  # 直答无来源 → 不入问答缓存


def test_ask_save_qa_record_after_web_answer(client, monkeypatch):
    """联网回答成功后调用 save_qa_record 落库（best-effort）。"""
    saved = []
    monkeypatch.setattr(main_mod.retriever, "save_qa_record", lambda *a, **k: saved.append(True) or True)
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "use_web_search": True})
    assert resp.status_code == 200
    assert saved == [True]


def test_ask_empty_result_when_direct_disabled(client, monkeypatch):
    """兜底关闭（enable_llm_direct=false）时维持原行为：EMPTY_RESULT。"""
    monkeypatch.setattr(main_mod.retriever, "retrieve_web", lambda *a, **k: [])
    monkeypatch.setattr(main_mod.settings.retriever, "enable_llm_direct", False)

    resp = client.post("/ask", json={"question": "不存在的冷门问题", "use_web_search": True})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "EMPTY_RESULT"


# ---- 联网搜索开关（AskRequest.use_web_search）----


def _offline_result():
    return SearchResult(
        chunk=Chunk(
            text="内部产品文档：BGE-M3 部署要求 Python 3.11+。",
            metadata=ChunkMetadata(url="offline://offline_test", title="内部部署文档", seq=1),
        ),
        score=0.89,
    )


def test_ask_use_web_search_false_answers_from_offline(client, monkeypatch):
    """关闭联网搜索：仅检索本地知识库（离线库命中 → 正常作答），绝不触发联网检索。"""
    monkeypatch.setattr(main_mod.retriever, "retrieve_offline", lambda *a, **k: [_offline_result()])
    # 关闭开关后联网检索绝不该被调用
    monkeypatch.setattr(
        main_mod.retriever, "retrieve_web",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应联网检索")),
    )

    resp = client.post("/ask", json={"question": "BGE-M3 部署要求？", "use_web_search": False})
    assert resp.status_code == 200
    body = resp.json()
    assert "[1]" in body["answer"]
    assert body["sources"][0]["url"] == "offline://offline_test"
    assert body["sources"][0]["title"] == "内部部署文档"
    assert body["direct"] is False


def test_ask_use_web_search_false_empty_returns_insufficient(client, monkeypatch):
    """关闭联网搜索且本地知识库未命中：返回 EMPTY_RESULT「信息不足」，不走 LLM 直答兜底。"""
    monkeypatch.setattr(main_mod.retriever, "retrieve_offline", lambda *a, **k: [])
    monkeypatch.setattr(
        main_mod.llm.DeepSeekClient, "generate_direct",
        lambda self, q, **k: (_ for _ in ()).throw(AssertionError("关闭联网不应直答")),
    )

    resp = client.post("/ask", json={"question": "库外冷门问题", "use_web_search": False})
    assert resp.status_code == 500
    err = resp.json()["error"]
    assert err["code"] == "EMPTY_RESULT"
    assert "信息不足" in err["message"]


def test_ask_use_web_search_default_false_skips_web(client, monkeypatch):
    """不传 use_web_search（缺省默认关闭）：本地库未命中 → 信息不足，绝不联网。"""
    monkeypatch.setattr(main_mod.retriever, "retrieve_offline", lambda *a, **k: [])
    monkeypatch.setattr(
        main_mod.retriever, "retrieve_web",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("默认不应联网")),
    )

    resp = client.post("/ask", json={"question": "库外冷门问题"})  # 不传 use_web_search
    assert resp.status_code == 500
    err = resp.json()["error"]
    assert err["code"] == "EMPTY_RESULT"
    assert "信息不足" in err["message"]


def test_ask_web_failure_degrades_to_offline_results(client, monkeypatch):
    """开启联网：联网检索失败但本地知识库有结果 → 降级用离线结果作答（不报 SEARCH_FAILED）。"""
    monkeypatch.setattr(main_mod.retriever, "retrieve_offline", lambda *a, **k: [_offline_result()])

    def boom(*a, **k):
        raise RuntimeError("搜索服务不可用")

    monkeypatch.setattr(main_mod.retriever, "retrieve_web", boom)
    resp = client.post("/ask", json={"question": "BGE-M3 部署要求？", "use_web_search": True})  # 显式开启联网
    assert resp.status_code == 200
    assert "[1]" in resp.json()["answer"]
    assert resp.json()["sources"][0]["url"] == "offline://offline_test"


def test_ask_validation_error(client):
    resp = client.post("/ask", json={"question": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_ask_llm_failure_maps_to_llm_failed(client, monkeypatch):
    def boom(self, question, contexts, **kw):
        raise RuntimeError("api down")

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", boom)

    resp = client.post("/ask", json={"question": "测试问题", "use_web_search": True})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "LLM_FAILED"


# ---- 温度参数（AskRequest.temperature → LLM client）----


def test_ask_temperature_passed_to_llm(client, monkeypatch):
    """请求携带 temperature：覆盖 settings 默认值，透传到 LLM client。"""
    seen = {}

    def fake_generate(self, question, contexts, **kw):
        seen["temperature"] = self.temperature
        return "温度生效的回答。[1]"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", fake_generate)
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "temperature": 1.5, "use_web_search": True})
    assert resp.status_code == 200
    assert seen["temperature"] == 1.5


def test_ask_temperature_defaults_to_settings(client, monkeypatch):
    """未传 temperature：使用 settings.llm.temperature 默认值。"""
    seen = {}

    def fake_generate(self, question, contexts, **kw):
        seen["temperature"] = self.temperature
        return "默认温度回答。[1]"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", fake_generate)
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "use_web_search": True})
    assert resp.status_code == 200
    assert seen["temperature"] == main_mod.settings.llm.temperature


def test_ask_temperature_zero_forwarded(client, monkeypatch):
    """temperature=0 是合法值（非 None），必须透传而非回退默认。"""
    seen = {}

    def fake_generate(self, question, contexts, **kw):
        seen["temperature"] = self.temperature
        return "确定性回答。[1]"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", fake_generate)
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "temperature": 0, "use_web_search": True})
    assert resp.status_code == 200
    assert seen["temperature"] == 0


def test_ask_temperature_out_of_range_validation(client):
    """temperature 超出 0–2：422 VALIDATION_ERROR。"""
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "temperature": 3})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_ask_stream_temperature_passed_to_llm(client, monkeypatch):
    """SSE 流式同样透传 temperature。"""
    seen = {}

    def fake_stream(self, question, contexts, **kw):
        seen["temperature"] = self.temperature
        yield "流式温度回答[1]。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "stream_generate", fake_stream)
    resp = client.post("/ask/stream", json={"question": "BGE-M3 是什么？", "temperature": 1.2, "use_web_search": True})
    assert resp.status_code == 200
    assert "流式温度回答" in resp.text  # 消费 SSE 流（生成器惰性执行），同时验证内容
    assert seen["temperature"] == 1.2


# ---- 搜索网页数量（AskRequest.web_top_n → retrieve_web）----


def test_ask_web_top_n_forwarded_to_retriever(client, monkeypatch):
    """请求携带 web_top_n：透传到 retriever.retrieve_web（1–20 合法值）。"""
    seen = {}

    def spy(question, store, embedder, settings, top_k, **kw):
        seen.update(kw)
        return [
            SearchResult(
                chunk=Chunk(text="测试网页内容。", metadata=ChunkMetadata(url="https://x.com/2", title="t2", seq=1)),
                score=0.9,
            )
        ]

    monkeypatch.setattr(main_mod.retriever, "retrieve_web", spy)
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "use_web_search": True, "web_top_n": 12})
    assert resp.status_code == 200
    assert seen["web_top_n"] == 12


def test_ask_web_top_n_default_is_none(client, monkeypatch):
    """未传 web_top_n：透传 None（retriever 缺省回落 settings.crawler.top_urls）。"""
    seen = {}

    def spy(question, store, embedder, settings, top_k, **kw):
        seen.update(kw)
        return [
            SearchResult(
                chunk=Chunk(text="默认数量回答。", metadata=ChunkMetadata(url="https://x.com/3", title="t3", seq=1)),
                score=0.9,
            )
        ]

    monkeypatch.setattr(main_mod.retriever, "retrieve_web", spy)
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "use_web_search": True})
    assert resp.status_code == 200
    assert seen.get("web_top_n") is None


def test_ask_web_top_n_out_of_range_validation(client):
    """web_top_n 越界（0 / 21 / -1）：422 VALIDATION_ERROR（前端滑杆上限 20 与后端校验一致）。"""
    for bad in (0, 21, -1):
        resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "use_web_search": True, "web_top_n": bad})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ---- 时效性锚定：前端宿主机本地时间（client_time）----


def test_ask_client_time_anchors_rewrite_and_llm(client, monkeypatch):
    """client_time：时效性问题 → 改写以客户端本地时间为基准（不做时区转换），并透传 LLM time_context。"""
    seen = {}
    real_rewrite = main_mod.rewrite_query  # 先取真实函数，避免递归

    def spy_rewrite(question, llm_client, **kw):
        seen["client_time"] = kw.get("client_time")
        return real_rewrite(question, llm_client, **kw)

    monkeypatch.setattr(main_mod, "rewrite_query", spy_rewrite)

    def spy_generate(self, question, contexts, **kw):
        seen["time_context"] = kw.get("time_context")
        return "时效性回答[1]。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", spy_generate)

    resp = client.post("/ask", json={
        "question": "近日股市表现如何",
        "client_time": "2026-01-02T10:30:00+08:00",
        "use_web_search": True,
    })
    assert resp.status_code == 200
    assert seen["client_time"] == "2026-01-02T10:30:00+08:00"
    # +08:00 即客户端本地墙钟日期，保留原样（不做时区换算）
    assert seen["time_context"] == "2026年01月02日"


def test_ask_client_time_ignored_for_non_time_question(client, monkeypatch):
    """非时效性问题：即使携带 client_time 也不注入（LLM time_context 为空串）。"""
    seen = {}

    def spy_generate(self, question, contexts, **kw):
        seen["time_context"] = kw.get("time_context")
        return "普通回答[1]。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", spy_generate)

    resp = client.post("/ask", json={
        "question": "什么是RAG？",
        "client_time": "2026-01-02T10:30:00+08:00",
        "use_web_search": True,
    })
    assert resp.status_code == 200
    assert seen["time_context"] == ""


def test_ask_stream_client_time_forwarded(client, monkeypatch):
    """SSE 流式路径：client_time 同样透传到改写与 LLM（时间基准一致）。"""
    seen = {}
    real_rewrite = main_mod.rewrite_query

    def spy_rewrite(question, llm_client, **kw):
        seen["client_time"] = kw.get("client_time")
        return real_rewrite(question, llm_client, **kw)

    monkeypatch.setattr(main_mod, "rewrite_query", spy_rewrite)

    def spy_stream(self, question, contexts, **kw):
        seen["time_context"] = kw.get("time_context")
        yield "时效性流式回答[1]。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "stream_generate", spy_stream)

    resp = client.post("/ask/stream", json={
        "question": "近日有什么新闻",
        "client_time": "2026-01-02T10:30:00+08:00",
        "use_web_search": True,
    })
    assert resp.status_code == 200
    assert seen["client_time"] == "2026-01-02T10:30:00+08:00"
    assert seen["time_context"] == "2026年01月02日"
    assert "时效性流式回答" in resp.text


# ---- /ask/stream（SSE 流式，api.md §1.3）----


def _split_frames(body: str) -> list[str]:
    """SSE 响应体 → 事件帧列表（按空行切分）。"""
    return [f for f in body.split("\n\n") if f.strip()]


def test_ask_stream_returns_sse(client, monkeypatch):
    """SSE 流式：delta 逐段推送，done 事件携带完整 answer + sources（引用解析）。"""

    def fake_stream(self, question, contexts, **kw):
        yield "BGE-M3 支持"
        yield " dense + sparse[1]。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "stream_generate", fake_stream)

    resp = client.post("/ask/stream", json={"question": "BGE-M3 是什么？", "use_web_search": True})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    frames = _split_frames(resp.text)
    deltas = [f for f in frames if f.startswith("event: delta")]
    done = next(f for f in frames if f.startswith("event: done"))
    assert len(deltas) == 2
    # delta 帧的 data 拼接 == 最终 answer（done 事件 JSON 内一致）
    delta_text = "".join(
        line[6:] for f in deltas for line in f.split("\n") if line.startswith("data:")
    )
    assert delta_text == "BGE-M3 支持 dense + sparse[1]。"
    assert '"answer": "BGE-M3 支持 dense + sparse[1]。"' in done
    assert '"index": 1' in done
    assert '"url": "https://x.com/1"' in done
    assert '"direct": false' in done
    assert '"cached": false' in done


def test_ask_stream_cache_hit_streams_summary(client, monkeypatch):
    """问答缓存命中：SSE 分块流式输出已存摘要（status → delta* → done，cached=true）。"""
    hit = QAHit(
        question="BGE-M3 支持双向量吗？",
        summary="支持，dense + sparse 双向量。[1]",
        sources=[Source(index=1, title="BGE-M3 文档", url="https://x.com/1")],
        score=0.95,
    )
    monkeypatch.setattr(
        main_mod.retriever, "lookup_qa_cache",
        lambda *a, **k: (hit, SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])),
    )

    resp = client.post("/ask/stream", json={"question": "BGE-M3 支持双向量吗？"})
    assert resp.status_code == 200
    frames = _split_frames(resp.text)
    deltas = [f for f in frames if f.startswith("event: delta")]
    assert deltas  # 缓存命中同样流式输出（打字机效果）
    delta_text = "".join(
        line[6:] for f in deltas for line in f.split("\n") if line.startswith("data:")
    )
    assert delta_text == hit.summary  # delta 拼接 == done.answer（同 §1.3 约定 2）
    done = next(f for f in frames if f.startswith("event: done"))
    assert '"cached": true' in done
    assert '"answer": "支持，dense + sparse 双向量。[1]"' in done


def test_ask_stream_llm_failure_emits_error_event(client, monkeypatch):
    """SSE 失败路径：LLM 异常 → error 事件（LLM_FAILED），HTTP 层仍 200。"""

    def boom(self, question, contexts, **kw):
        raise RuntimeError("api down")
        yield  # pragma: no cover —— 仅使函数成为生成器

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "stream_generate", boom)

    resp = client.post("/ask/stream", json={"question": "测试问题", "use_web_search": True})
    assert resp.status_code == 200
    assert "event: error" in resp.text
    assert '"code": "LLM_FAILED"' in resp.text
    assert "api down" in resp.text


def test_ask_stream_direct_fallback(client, monkeypatch):
    """兜底流式：联网检索为空 → stream_generate_direct，done 事件 direct=true（不入缓存）。"""
    monkeypatch.setattr(main_mod.retriever, "retrieve_web", lambda *a, **k: [])
    saved = []
    monkeypatch.setattr(main_mod.retriever, "save_qa_record", lambda *a, **k: saved.append(True) or True)

    def fake_direct(self, question, **kw):
        yield "未检索到相关资料。"
        yield "直接回答如下。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "stream_generate_direct", fake_direct)

    resp = client.post("/ask/stream", json={"question": "不存在的冷门问题", "use_web_search": True})
    assert resp.status_code == 200
    body = resp.text
    assert "未检索到相关资料。直接回答如下。" in body
    assert '"direct": true' in body
    assert '"sources": []' in body
    assert saved == []  # 直答不入缓存


def test_ask_stream_empty_result_when_direct_disabled(client, monkeypatch):
    """兜底关闭时 SSE 同样返回 EMPTY_RESULT（error 事件，HTTP 200）。"""
    monkeypatch.setattr(main_mod.retriever, "retrieve_web", lambda *a, **k: [])
    monkeypatch.setattr(main_mod.settings.retriever, "enable_llm_direct", False)

    resp = client.post("/ask/stream", json={"question": "不存在的冷门问题", "use_web_search": True})
    assert resp.status_code == 200
    assert '"code": "EMPTY_RESULT"' in resp.text


def test_ask_stream_use_web_search_false_empty_emits_insufficient(client, monkeypatch):
    """SSE：关闭联网搜索且本地库未命中 → error 事件 EMPTY_RESULT（信息不足），HTTP 200。"""
    monkeypatch.setattr(main_mod.retriever, "retrieve_offline", lambda *a, **k: [])
    monkeypatch.setattr(
        main_mod.retriever, "retrieve_web",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应联网检索")),
    )

    resp = client.post("/ask/stream", json={"question": "库外冷门问题", "use_web_search": False})
    assert resp.status_code == 200
    assert '"code": "EMPTY_RESULT"' in resp.text
    assert "信息不足" in resp.text


def test_ask_stream_validation_error(client):
    """参数校验失败走 HTTP 422 + JSON 错误信封（非 SSE 响应，前端回退解析）。"""
    resp = client.post("/ask/stream", json={"question": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_ask_stream_status_events(client, monkeypatch):
    """SSE 进度：缓存检索 → 联网检索 → 生成的 progress 回调 → status 事件实时推送。"""

    def fake_lookup(question, store, embedder, collection, settings, **kw):
        progress = kw.get("progress")
        if progress:
            progress("正在检索历史问答缓存…")
        return None, SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])

    def fake_retrieve_web(question, store, embedder, settings, top_k, **kw):
        progress = kw.get("progress")
        if progress:
            progress("正在联网搜索…")
        return [
            SearchResult(
                chunk=Chunk(
                    text="BGE-M3 一次前向同时输出稠密与稀疏向量。",
                    metadata=ChunkMetadata(url="https://x.com/1", title="BGE-M3 文档", seq=1),
                ),
                score=0.91,
            )
        ]

    monkeypatch.setattr(main_mod.retriever, "lookup_qa_cache", fake_lookup)
    monkeypatch.setattr(main_mod.retriever, "retrieve_web", fake_retrieve_web)

    def fake_stream(self, question, contexts, **kw):
        yield "BGE-M3 支持双向量[1]。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "stream_generate", fake_stream)

    resp = client.post("/ask/stream", json={"question": "BGE-M3 是什么？", "use_web_search": True})
    assert resp.status_code == 200
    frames = _split_frames(resp.text)
    statuses = [f for f in frames if f.startswith("event: status")]
    # 缓存检索 + 联网搜索 + 生成前，共 3 条 status，且均在 delta 之前
    assert len(statuses) == 3
    first_delta = next(i for i, f in enumerate(frames) if f.startswith("event: delta"))
    assert all(i < first_delta for i, f in enumerate(frames) if f.startswith("event: status"))
    assert "正在检索历史问答缓存" in resp.text
    assert "正在联网搜索" in resp.text
    assert "正在生成回答" in resp.text


# ---- 追问业务：/ask 携带 history 时改写后的自包含问题贯穿链路 ----


def _mk_history():
    return [
        {"role": "user", "content": "BGE-M3 是什么？"},
        {"role": "assistant", "content": "BGE-M3 是智源的多语言嵌入模型。"},
    ]


def test_ask_followup_rewrites_question_through_pipeline(client, monkeypatch):
    """带 history 且判定为追问 → 改写后的完整问题贯穿 缓存检索 / 检索 / 生成。"""
    seen: dict = {}

    def fake_followup(question, history, llm_client, **kw):
        seen["original"] = question
        seen["history"] = history
        return SimpleNamespace(is_followup=True, rewritten="BGE-M3 的部署要求是什么？")

    monkeypatch.setattr(main_mod, "rewrite_followup", fake_followup)

    def fake_lookup(q, store, embedder, collection, settings, **kw):
        seen["cache_question"] = q
        return None, SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])

    monkeypatch.setattr(main_mod.retriever, "lookup_qa_cache", fake_lookup)

    def fake_retrieve_web(q, store, embedder, settings, top_k, **kw):
        seen["retrieve_question"] = q
        return [
            SearchResult(
                chunk=Chunk(
                    text="BGE-M3 支持本地部署。",
                    metadata=ChunkMetadata(url="https://x.com/f", title="部署文档", seq=1),
                ),
                score=0.9,
            )
        ]

    monkeypatch.setattr(main_mod.retriever, "retrieve_web", fake_retrieve_web)

    def fake_generate(self, q, contexts, **kw):
        seen["generate_question"] = q
        return "BGE-M3 支持本地部署[1]。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", fake_generate)

    resp = client.post("/ask", json={
        "question": "部署要求呢？",
        "use_web_search": True,
        "history": _mk_history(),
    })
    assert resp.status_code == 200
    assert "部署" in resp.json()["answer"]
    # 改写后的自包含问题贯穿缓存检索 / 联网检索 / 生成
    assert seen["cache_question"] == "BGE-M3 的部署要求是什么？"
    assert seen["retrieve_question"] == "BGE-M3 的部署要求是什么？"
    assert seen["generate_question"] == "BGE-M3 的部署要求是什么？"
    # 历史原样传给追问改写（原始问题 + 完整历史）
    assert seen["original"] == "部署要求呢？"
    assert [h.role for h in seen["history"]] == ["user", "assistant"]


def test_ask_without_history_skips_followup(client, monkeypatch):
    """不带 history → 不触发追问改写（单轮提问行为不变）。"""

    def fake_followup(*a, **k):
        raise AssertionError("无 history 不应调用追问改写")

    monkeypatch.setattr(main_mod, "rewrite_followup", fake_followup)
    resp = client.post("/ask", json={"question": "BGE-M3 是什么？", "use_web_search": True})
    assert resp.status_code == 200
    assert "BGE-M3 支持 dense + sparse 双向量[1]。" in resp.json()["answer"]


def test_ask_followup_failure_falls_back_to_original(client, monkeypatch):
    """追问改写异常 → 降级用原始问题继续（不阻断主链路）。"""

    def fake_followup(*a, **k):
        raise RuntimeError("mock followup failure")

    monkeypatch.setattr(main_mod, "rewrite_followup", fake_followup)

    def fake_generate(self, q, contexts, **kw):
        return f"针对「{q}」的回答"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", fake_generate)

    resp = client.post("/ask", json={
        "question": "部署要求呢？",
        "use_web_search": True,
        "history": _mk_history(),
    })
    assert resp.status_code == 200
    # 原始问题进入生成链路
    assert "针对「部署要求呢？」的回答" == resp.json()["answer"]


def test_ask_followup_not_followup_keeps_original(client, monkeypatch):
    """带 history 但 LLM 判定非追问 → 用原始问题（不改写）。"""

    def fake_followup(*a, **k):
        return SimpleNamespace(is_followup=False, rewritten="")

    monkeypatch.setattr(main_mod, "rewrite_followup", fake_followup)

    def fake_generate(self, q, contexts, **kw):
        return f"回答：{q}"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "generate", fake_generate)

    resp = client.post("/ask", json={
        "question": "什么是 RAG？",
        "use_web_search": True,
        "history": _mk_history(),
    })
    assert resp.status_code == 200
    assert resp.json()["answer"] == "回答：什么是 RAG？"


def test_ask_stream_followup_rewrites_question(client, monkeypatch):
    """/ask/stream 带 history：改写后的完整问题用于缓存检索与流式生成。"""
    seen: dict = {}

    def fake_followup(question, history, llm_client, **kw):
        return SimpleNamespace(is_followup=True, rewritten="BGE-M3 的部署要求是什么？")

    monkeypatch.setattr(main_mod, "rewrite_followup", fake_followup)

    def fake_lookup(q, store, embedder, collection, settings, **kw):
        seen["cache_question"] = q
        return None, SimpleNamespace(dense=[[0.0] * 4], sparse=[{}])

    monkeypatch.setattr(main_mod.retriever, "lookup_qa_cache", fake_lookup)

    def fake_stream(self, q, contexts, **kw):
        seen["stream_question"] = q
        yield f"针对「{q}」的部署说明[1]。"

    monkeypatch.setattr(main_mod.llm.DeepSeekClient, "stream_generate", fake_stream)

    resp = client.post("/ask/stream", json={
        "question": "部署要求呢？",
        "use_web_search": True,
        "history": _mk_history(),
    })
    assert resp.status_code == 200
    frames = _split_frames(resp.text)
    done = next(f for f in frames if f.startswith("event: done"))
    assert '"answer": "针对「BGE-M3 的部署要求是什么？」的部署说明[1]。"' in done
    assert seen["cache_question"] == "BGE-M3 的部署要求是什么？"
    assert seen["stream_question"] == "BGE-M3 的部署要求是什么？"
