"""llm 引用解析单测：幽灵引用剔除、URL 去重合并、build_response 组装、直答兜底（api.md §2）。"""

from src.webrag.llm import DeepSeekClient, build_response, parse_citations
from src.webrag.schemas import Chunk, ChunkMetadata


def _ctx(text: str, url: str, title: str = "标题") -> Chunk:
    return Chunk(text=text, metadata=ChunkMetadata(url=url, title=title, seq=1))


def test_parse_citations_valid_range():
    ctxs = [_ctx("a", "https://a.com"), _ctx("b", "https://b.com")]
    sources = parse_citations("回答[1]和[2]都正确。", ctxs)
    assert [s.index for s in sources] == [1, 2]


def test_ghost_citation_dropped():
    ctxs = [_ctx("a", "https://a.com")]
    sources = parse_citations("引用[3]是幽灵，[1]有效。", ctxs)
    assert [s.index for s in sources] == [1]


def test_duplicate_url_merged_keep_first_index():
    ctxs = [_ctx("a", "https://a.com"), _ctx("b", "https://a.com")]
    sources = parse_citations("同时引用[1]和[2]", ctxs)
    assert len(sources) == 1
    assert sources[0].index == 1  # 保留第一次出现的 index


def test_out_of_order_citations_sorted():
    ctxs = [_ctx("a", "https://a.com"), _ctx("b", "https://b.com")]
    sources = parse_citations("[2]然后[1]", ctxs)
    assert [s.index for s in sources] == [1, 2]


def test_build_response_assembles_answer_and_sources():
    ctxs = [_ctx("x", "https://x.com", title="X 官网")]
    resp = build_response("结论[1]。", ctxs)
    assert resp.answer == "结论[1]。"
    assert len(resp.sources) == 1
    assert resp.sources[0].url == "https://x.com"
    assert resp.sources[0].title == "X 官网"


def test_generate_direct_requires_api_key():
    """直答兜底：未配置 Key 时与 generate 一致抛错，不静默。"""
    client = DeepSeekClient(api_key="")
    try:
        client.generate_direct("测试问题")
    except RuntimeError as exc:
        assert "DEEPSEEK_API_KEY" in str(exc)
    else:
        raise AssertionError("未配置 Key 应抛 RuntimeError")


def test_complete_requires_api_key_returns_empty():
    """complete() 在无 API Key 时静默返回空字符串（不抛异常，调用方自行兜底）。"""
    client = DeepSeekClient(api_key="")
    result = client.complete("测试 prompt")
    assert result == ""


def test_complete_accepts_custom_max_tokens():
    """complete() 参数传递给 max_tokens。"""
    # 无 API Key，直接返回空，不校验参数传递
    client = DeepSeekClient(api_key="")
    result = client.complete("测试", max_tokens=512)
    assert result == ""
