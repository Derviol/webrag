"""P1 retriever 新增函数独立验证（复制函数体，避免 trafilatura 等 Docker 重依赖）。"""

# ══ 复制 retriever P1 函数体（避免导入链依赖 trafilatura/pymilvus） ══

def _ngrams(text: str, n: int = 3) -> set[str]:
    text = text.strip()
    if len(text) < n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _jaccard_ngram(text1: str, text2: str, n: int = 3) -> float:
    ng1, ng2 = _ngrams(text1, n), _ngrams(text2, n)
    if not ng1 or not ng2:
        return 0.0
    return len(ng1 & ng2) / len(ng1 | ng2)


_SOURCE_WHITELIST: dict[str, float] = {
    "wikipedia.org": 0.15,
    "github.com": 0.10,
    "docs.python.org": 0.15,
    "developer.mozilla.org": 0.15,
    "arxiv.org": 0.12,
    "stackoverflow.com": 0.08,
    "pypi.org": 0.10,
    "npmjs.com": 0.10,
    "pytorch.org": 0.12,
    "tensorflow.org": 0.12,
    "bbc.com": 0.08,
    "reuters.com": 0.08,
    "zhihu.com": 0.03,
    "csdn.net": 0.02,
    "juejin.cn": 0.02,
}


def _domain_quality_score(url: str) -> float:
    if not url:
        return 0.0
    import re
    for domain, bonus in _SOURCE_WHITELIST.items():
        if re.search(re.escape(domain), url, re.IGNORECASE):
            return bonus
    return 0.0


# ══ QA 缓存 jieba Jaccard 综合分逻辑 ══
def qa_jaccard_combined(dense_score: float, jaccard: float, jaccard_weight: float, jaccard_min: float) -> float:
    """模拟 lookup_qa_cache 的综合评分逻辑。"""
    if jaccard >= jaccard_min:
        return (1 - jaccard_weight) * dense_score + jaccard_weight * jaccard
    return dense_score


# ══ 测试 ══

# ── n-gram ──
def test_ngrams_normal():
    ngrams = _ngrams("abcdefg", n=3)
    assert len(ngrams) == 5
    assert "abc" in ngrams
    assert "efg" in ngrams


def test_ngrams_short():
    assert _ngrams("ab", n=3) == {"ab"}


def test_ngrams_empty():
    # strip() 后为空字符串，len < n → 返回 {""}
    assert _ngrams("  ", n=3) == {""}


# ── Jaccard ──
def test_jaccard_identical():
    assert _jaccard_ngram("abcde", "abcde", n=3) == 1.0


def test_jaccard_different():
    assert _jaccard_ngram("abcde", "vwxyz", n=3) == 0.0


def test_jaccard_partial():
    sim = _jaccard_ngram("abcde", "abcxy", n=3)
    assert 0.0 < sim < 1.0


def test_jaccard_near_duplicate():
    """两个高度相似但非完全相同的文本。"""
    t1 = "Python 是一种广泛使用的编程语言，由 Guido van Rossum 于 1991 年发布。"
    t2 = "Python 是一种广泛使用的编程语言，由 Guido van Rossum 发布于 1991 年。"
    sim = _jaccard_ngram(t1, t2, n=3)
    assert sim > 0.8  # 高度相似
    assert sim < 1.0  # 不完全相同


# ── 来源质量 ──
def test_domain_quality_wikipedia():
    assert _domain_quality_score("https://en.wikipedia.org/wiki/Python") == 0.15


def test_domain_quality_github():
    assert _domain_quality_score("https://github.com/user/repo") == 0.10


def test_domain_quality_mdn():
    assert _domain_quality_score("https://developer.mozilla.org/en-US/docs/Web") == 0.15


def test_domain_quality_docs_python():
    assert _domain_quality_score("https://docs.python.org/3/library/") == 0.15


def test_domain_quality_unknown():
    assert _domain_quality_score("https://random-blog.example.com/post") == 0.0


def test_domain_quality_empty():
    assert _domain_quality_score("") == 0.0


def test_domain_quality_case_insensitive():
    assert _domain_quality_score("https://en.Wikipedia.org/wiki/X") == 0.15


def test_domain_quality_zhihu_csdn():
    assert _domain_quality_score("https://zhuanlan.zhihu.com/p/123") == 0.03
    assert _domain_quality_score("https://blog.csdn.net/article/123") == 0.02


# ── QA 缓存 jieba Jaccard 综合分 ──
def test_qa_jaccard_boosts_score():
    """Jaccard 高时，综合分应高于纯 dense 分。"""
    combined = qa_jaccard_combined(dense_score=0.75, jaccard=0.90, jaccard_weight=0.30, jaccard_min=0.30)
    assert combined > 0.75  # 加分
    assert abs(combined - (0.7 * 0.75 + 0.3 * 0.90)) < 0.001


def test_qa_jaccard_no_boost_when_low():
    """Jaccard 低于阈值时，综合分 = 纯 dense 分。"""
    combined = qa_jaccard_combined(dense_score=0.75, jaccard=0.15, jaccard_weight=0.30, jaccard_min=0.30)
    assert combined == 0.75  # 不加分


def test_qa_jaccard_edge_threshold():
    """Jaccard 刚好等于阈值时，启用辅助评分——但 jaccard 低时会拉低综合分（符合预期：低Jaccard应惩罚）。"""
    combined = qa_jaccard_combined(dense_score=0.80, jaccard=0.30, jaccard_weight=0.30, jaccard_min=0.30)
    # jaccard=0.30 < dense=0.80，加权平均后应低于 dense
    assert combined < 0.80, f"Low jaccard should reduce score, got {combined}"
    assert combined > 0.60  # 但不应该太低


def test_qa_jaccard_zero_weight():
    """权重为 0 时，综合分 = 纯 dense 分。"""
    combined = qa_jaccard_combined(dense_score=0.80, jaccard=0.95, jaccard_weight=0.0, jaccard_min=0.30)
    assert combined == 0.80


# ── 边界 ──
def test_jaccard_empty():
    """两个空 text：ngram sets 相同 → Jaccard = 1.0。调用方应在上层跳过空文本。"""
    assert _jaccard_ngram("", "", n=3) == 1.0


def test_jaccard_one_empty():
    assert _jaccard_ngram("abc", "", n=3) == 0.0


def test_ngrams_whitespace():
    ng = _ngrams("  a  ", n=3)
    assert len(ng) > 0
