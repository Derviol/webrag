"""chunker 单测：段落感知、重叠、序号、空文档、长段落兜底 + 两级粒度（P0）。"""

from src.webrag.chunker import chunk, chunk_two_level, expand_to_parents
from src.webrag.schemas import Chunk, Document, SearchResult


def _doc(text: str, url: str = "https://example.com/a", title: str = "T") -> Document:
    return Document(title=title, text=text, url=url)


def test_empty_or_blank_text_yields_no_chunks():
    assert chunk(_doc("")) == []
    assert chunk(_doc("   \n\n  ")) == []


def test_single_small_document_one_chunk():
    cs = chunk(_doc("你好。这是测试内容。"))
    assert len(cs) == 1
    assert cs[0].text == "你好。这是测试内容。"
    assert cs[0].metadata.url == "https://example.com/a"
    assert cs[0].metadata.title == "T"
    assert cs[0].metadata.seq == 1


def test_paragraphs_joined_until_size_limit():
    text = "第一段。\n\n第二段。\n\n第三段。"
    cs = chunk(_doc(text), chunk_size=30, overlap=0)
    # 三段共 ~18 字符 < 30 → 全部并入一块
    assert len(cs) == 1
    assert "第一段。" in cs[0].text and "第三段。" in cs[0].text


def test_overlap_carried_to_next_chunk():
    text = "甲" * 200 + "。" + "乙" * 200
    cs = chunk(_doc(text), chunk_size=120, overlap=20, respect_paragraph=False)
    assert len(cs) >= 2
    assert cs[0].text[-20:] in cs[1].text[:40]  # 前块结尾出现在后块开头


def test_seq_numbering_continuous():
    text = ("内容段落" * 200 + "。\n\n") * 3
    cs = chunk(_doc(text, url="u", title="t"), chunk_size=100, overlap=10)
    assert len(cs) >= 2
    for i, c in enumerate(cs, 1):
        assert c.metadata.seq == i
        assert c.metadata.url == "u"
        assert c.metadata.title == "t"


def test_long_sentence_hard_split_fallback():
    # 无标点超长串：按 chunk_size 硬切兜底，不丢字符
    text = "无" * 500
    cs = chunk(_doc(text), chunk_size=128, overlap=0, respect_paragraph=False)
    assert len(cs) >= 3
    assert "".join(c.text for c in cs).replace("无", "") == ""


def test_overlap_clamped_to_half_chunk_size():
    text = "甲" * 1000
    cs = chunk(_doc(text), chunk_size=100, overlap=999)
    clamped = 100 // 2  # overlap 钳制到 chunk_size//2
    for c in cs[:-1]:
        # 块长上限 = chunk_size + overlap + 分隔符（重叠按设计叠加在块尾）
        assert len(c.text) <= 100 + clamped + 2
    # 重叠确实生效：前块结尾出现在后块开头
    assert cs[0].text[-clamped:] in cs[1].text[: clamped + 2]


def test_empty_lines_ignored():
    text = "一段。\n\n\n\n   \n\n二段。"
    cs = chunk(_doc(text))
    assert len(cs) == 1
    assert "一段。" in cs[0].text and "二段。" in cs[0].text


def test_english_period_with_space_splits_sentences():
    # 英文句点+空格处切开，块内句子完整
    text = "Hello world. This is a RAG system! Great, isn't it?"
    cs = chunk(_doc(text), chunk_size=200, overlap=0, respect_paragraph=False)
    assert len(cs) == 1  # 全部并入一块
    assert "Hello world." in cs[0].text


def test_english_period_keeps_decimals():
    # 回归：句点后紧跟数字（小数/版本号）不切开
    text = "版本 3.14 发布，PHP 8.2 可用。"
    cs = chunk(_doc(text), chunk_size=12, overlap=0, respect_paragraph=False)
    joined = "||".join(c.text for c in cs)
    assert "3.14" in joined
    assert "8.2" in joined


def test_english_sentence_split_multi_chunk():
    # 多个英文短句，切块后句子仍完整（不出现半句开头）
    # 注意：chunk_size 下限 64，文本需超过 64 字符才会切出多块
    text = "One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. Eleven. Twelve. "
    cs = chunk(_doc(text), chunk_size=64, overlap=0, respect_paragraph=False)
    assert len(cs) >= 2
    assert "One." in cs[0].text and "Two." in cs[0].text


# ── 小→大（Small-to-Big）两级粒度切块（P0 优化）──


def _long_doc() -> Document:
    """辅助：生成一个足够长的测试文档。"""
    paragraphs = [
        "第一部分：这是关于Python编程语言的介绍。Python是一种高级编程语言，具有简洁的语法和强大的功能。",
        "第二部分：Python在数据科学和人工智能领域有广泛应用。NumPy、Pandas等库提供了强大的数据处理能力。",
        "第三部分：安装Python可以从官网下载。安装完成后需要配置环境变量，验证安装是否成功。",
        "第四部分：RAG（检索增强生成）是一种结合信息检索和文本生成的AI技术。它通过检索外部知识来增强LLM的回答。",
        "第五部分：向量数据库是RAG系统的核心组件。Milvus、Chroma等是常用的向量数据库选择。",
    ]
    text = "\n\n".join(paragraphs)
    return _doc(text)


def test_chunk_two_level_produces_child_and_parent():
    doc = _long_doc()
    child_chunks, parent_chunks, mapping = chunk_two_level(
        doc, child_size=128, parent_size=512, overlap=16, respect_paragraph=True,
    )
    assert len(child_chunks) >= 1
    assert len(parent_chunks) >= 1
    assert len(child_chunks) >= len(parent_chunks)  # 小块数 ≥ 大块数
    assert len(mapping) <= len(child_chunks)  # mapping 覆盖部分 child


def test_chunk_two_level_mapping_is_correct():
    """每个被映射的 child chunk 文本确实是其 parent chunk 的子串。"""
    doc = _long_doc()
    child_chunks, parent_chunks, mapping = chunk_two_level(
        doc, child_size=128, parent_size=512, overlap=16,
    )
    for ci, pi in mapping.items():
        assert child_chunks[ci].text in parent_chunks[pi].text, (
            f"Child[{ci}] text not found in Parent[{pi}]"
        )


def test_chunk_two_level_small_document():
    """短文档：只有一块 child 和一块 parent。"""
    doc = _doc("这是一段很短的测试文本。")
    child_chunks, parent_chunks, mapping = chunk_two_level(
        doc, child_size=256, parent_size=1024, overlap=64,
    )
    assert len(child_chunks) == 1
    assert len(parent_chunks) == 1
    assert mapping == {0: 0}


def test_chunk_two_level_empty_document():
    child_chunks, parent_chunks, mapping = chunk_two_level(
        _doc(""), child_size=256, parent_size=1024, overlap=64,
    )
    assert child_chunks == []
    assert parent_chunks == []
    assert mapping == {}

    child_chunks, parent_chunks, mapping = chunk_two_level(
        _doc("   \n\n  "), child_size=256, parent_size=1024, overlap=64,
    )
    assert child_chunks == []
    assert parent_chunks == []
    assert mapping == {}


def test_chunk_two_level_metadata_preserved():
    """两级切块保留文档元数据。"""
    doc = _doc("测试内容。" * 50, url="https://test.com", title="测试标题")
    child_chunks, parent_chunks, _ = chunk_two_level(
        doc, child_size=64, parent_size=256, overlap=16,
    )
    for c in child_chunks:
        assert c.metadata.url == "https://test.com"
        assert c.metadata.title == "测试标题"
    for p in parent_chunks:
        assert p.metadata.url == "https://test.com"
        assert p.metadata.title == "测试标题"


def test_chunk_two_level_seq_continuous():
    """序号连续递增。"""
    doc = _doc("段落内容。" * 100)
    child_chunks, parent_chunks, _ = chunk_two_level(
        doc, child_size=64, parent_size=256, overlap=16,
    )
    for i, c in enumerate(child_chunks, 1):
        assert c.metadata.seq == i
    for i, p in enumerate(parent_chunks, 1):
        assert p.metadata.seq == i


# ── expand_to_parents ──


def _mk_chunk(text: str, url: str = "https://x.com", title: str = "T") -> Chunk:
    return Chunk(text=text, metadata={"url": url, "title": title, "seq": 1})


def _mk_result(chunk_text: str, score: float = 1.0) -> SearchResult:
    return SearchResult(chunk=_mk_chunk(chunk_text), score=score)


def test_expand_to_parents_basic():
    """基本场景：child chunk → parent chunk 展开。"""
    p0 = _mk_chunk("A" * 200, url="https://a.com", title="父块0")
    p1 = _mk_chunk("B" * 200, url="https://b.com", title="父块1")
    parent_chunks = [p0, p1]

    c0 = _mk_chunk("A" * 50)
    c1 = _mk_chunk("B" * 50)
    child_chunks = [c0, c1]

    child_to_parent = {0: 0, 1: 1}

    results = [_mk_result("A" * 50, score=1.0)]
    expanded = expand_to_parents(results, parent_chunks, child_to_parent, child_chunks)
    assert len(expanded) == 1
    assert expanded[0].chunk.text == p0.text
    assert expanded[0].chunk.metadata.url == "https://a.com"


def test_expand_to_parents_multiple_children_to_same_parent():
    """两个 child 映射到同一 parent → 只保留最高分。"""
    parent = _mk_chunk("X" * 200, url="https://x.com", title="唯一父块")
    parent_chunks = [parent]

    c0 = _mk_chunk("X" * 30)
    c1 = _mk_chunk("X" * 40)
    child_chunks = [c0, c1]
    child_to_parent = {0: 0, 1: 0}

    results = [_mk_result("X" * 30, score=0.6), _mk_result("X" * 40, score=0.9)]
    expanded = expand_to_parents(results, parent_chunks, child_to_parent, child_chunks)
    assert len(expanded) == 1
    assert expanded[0].score == 0.9  # 保留最高分


def test_expand_to_parents_no_mapping():
    """无映射 → 返回空列表。"""
    parent = _mk_chunk("X" * 200)
    child_to_parent: dict[int, int] = {}
    child_chunks: list[Chunk] = []
    results = [_mk_result("X" * 200)]
    expanded = expand_to_parents(results, [parent], child_to_parent, child_chunks)
    assert expanded == []


def test_expand_to_parents_child_not_found():
    """检索结果中 child 文本在 child_chunks 里找不到 → 跳过。"""
    p0 = _mk_chunk("A" * 200)
    parent_chunks = [p0]
    c0 = _mk_chunk("B" * 50)
    child_chunks = [c0]
    child_to_parent = {0: 0}

    results = [_mk_result("C" * 50)]  # 不匹配任何 child
    expanded = expand_to_parents(results, parent_chunks, child_to_parent, child_chunks)
    assert expanded == []


def test_expand_to_parents_results_sorted_by_score():
    """展开后按分数降序排列。"""
    p0 = _mk_chunk("P" * 200, url="https://a.com")
    p1 = _mk_chunk("Q" * 200, url="https://b.com")
    parent_chunks = [p0, p1]

    c0 = _mk_chunk("P" * 50)
    c1 = _mk_chunk("Q" * 50)
    child_chunks = [c0, c1]
    child_to_parent = {0: 0, 1: 1}

    results = [_mk_result("P" * 50, score=0.5), _mk_result("Q" * 50, score=0.9)]
    expanded = expand_to_parents(results, parent_chunks, child_to_parent, child_chunks)
    assert len(expanded) == 2
    assert expanded[0].score >= expanded[1].score
    assert expanded[0].score == 0.9


def test_expand_to_parents_substring_match():
    """子串匹配兜底：child 文本是检索结果文本的子串。"""
    parent = _mk_chunk("ABCDEFGHIJ" * 30, url="https://x.com")
    parent_chunks = [parent]
    child = _mk_chunk("ABCDEF")
    child_chunks = [child]
    child_to_parent = {0: 0}

    results = [_mk_result("ABCDEFGHIJ" * 3, score=1.0)]
    expanded = expand_to_parents(results, parent_chunks, child_to_parent, child_chunks)
    assert len(expanded) == 1
