"""config 单测：QueryRewriterSettings、两级 chunk 配置、Settings 完整性。

负责人：P0 检索优化。
"""

import tempfile
from pathlib import Path

from src.webrag.config import (
    ChunkerSettings,
    QueryRewriterSettings,
    RetrieverSettings,
    Settings,
    _apply,
)

# ── QueryRewriterSettings ──


def test_query_rewriter_defaults():
    qr = QueryRewriterSettings()
    assert qr.enable is True
    assert qr.enable_intent is True
    assert qr.enable_multi_rewrite is True
    assert qr.enable_hyde is True


def test_query_rewriter_override():
    qr = QueryRewriterSettings(enable=False, enable_hyde=False)
    assert qr.enable is False
    assert qr.enable_hyde is False
    assert qr.enable_intent is True  # 未被覆盖，保留默认
    assert qr.enable_multi_rewrite is True


# ── ChunkerSettings 两级粒度 ──


def test_chunker_two_level_defaults():
    c = ChunkerSettings()
    assert c.enable_two_level is False  # 默认关闭
    assert c.child_chunk_size == 256
    assert c.parent_chunk_size == 1024
    assert c.chunk_size == 512  # 原有字段不变


def test_chunker_two_level_override():
    c = ChunkerSettings(enable_two_level=True, child_chunk_size=128, parent_chunk_size=2048)
    assert c.enable_two_level is True
    assert c.child_chunk_size == 128
    assert c.parent_chunk_size == 2048


# ── Settings 完整性 ──


def test_settings_includes_query_rewriter():
    s = Settings()
    assert hasattr(s, "query_rewriter")
    assert isinstance(s.query_rewriter, QueryRewriterSettings)
    assert s.query_rewriter.enable is True


def test_settings_all_blocks_present():
    """确保所有配置块都已定义。"""
    s = Settings()
    assert hasattr(s, "llm")
    assert hasattr(s, "crawler")
    assert hasattr(s, "chunker")
    assert hasattr(s, "retriever")
    assert hasattr(s, "query_rewriter")
    assert hasattr(s, "server")


def test_retriever_defaults_unchanged():
    r = RetrieverSettings()
    assert r.top_k == 10
    assert r.enable_rerank is True
    assert r.rerank_top_n == 5
    assert r.rerank_min_score == 0.6
    assert r.enable_qa_cache is True
    assert r.qa_min_score == 0.80


# ── _apply 函数 ──


def test_apply_only_known_fields():
    """_apply 只取目标 dataclass 的已知字段，未知键忽略。"""

    class Target:
        def __init__(self, a=1, b=2):
            self.a = a
            self.b = b

    from dataclasses import dataclass

    @dataclass
    class TestDC:
        a: int = 1
        b: int = 2

    result = _apply({"a": 10, "b": 20, "unknown_field": 99}, TestDC)
    assert result.a == 10
    assert result.b == 20


def test_apply_empty_dict_uses_defaults():
    result = _apply({}, ChunkerSettings)
    assert result.chunk_size == 512
    assert result.overlap == 64


def test_apply_none_uses_defaults():
    result = _apply(None, ChunkerSettings)
    assert result.chunk_size == 512


# ── 从 YAML 加载 ──


def test_load_settings_from_yaml_with_query_rewriter():
    """模拟 YAML 包含 query_rewriter 配置块。"""
    import yaml

    yaml_content = {
        "query_rewriter": {
            "enable": True,
            "enable_intent": True,
            "enable_multi_rewrite": False,
            "enable_hyde": True,
        },
        "chunker": {
            "chunk_size": 512,
            "overlap": 64,
            "enable_two_level": True,
            "child_chunk_size": 256,
            "parent_chunk_size": 1024,
        },
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.dump(yaml_content, f)
        tmp_path = f.name

    try:
        # load_settings 会读 .env → 我们需要确保 .env 存在（项目根目录）
        # 这里仅测试 _apply 行为，不真正调用 load_settings
        raw = yaml.safe_load(Path(tmp_path).read_text(encoding="utf-8")) or {}
        qr = _apply(raw.get("query_rewriter"), QueryRewriterSettings)
        assert qr.enable is True
        assert qr.enable_multi_rewrite is False

        chunker = _apply(raw.get("chunker"), ChunkerSettings)
        assert chunker.enable_two_level is True
        assert chunker.child_chunk_size == 256
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_load_settings_preserves_existing_fields():
    """加载时不影响 query_rewriter 之外的已有配置。"""
    s = Settings()
    assert s.llm.model == "deepseek-chat"
    assert s.retriever.top_k == 10
    assert s.server.ask_timeout_seconds == 105
