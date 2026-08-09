"""crawler 单测：搜索适配层（Bing 无 Key 降级抓页）、未知 provider、fetch 重试与 robots。"""

import time

import pytest

from src.webrag import crawler


def test_search_bing_html_fallback(monkeypatch):
    html = """<html><body>
      <li class="b_algo"><h2><a href="https://x.com/a">标题A &amp; 副题</a></h2><p>摘要A内容。</p></li>
      <li class="b_algo"><h2><a href="https://x.com/b">标题B</a></h2></li>
    </body></html>"""

    class FakeResp:
        text = html
        encoding = "utf-8"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(crawler.requests, "get", lambda *a, **k: FakeResp())
    hits = crawler.search("测试", top_n=5, provider="bing", api_key="")
    assert len(hits) == 2
    assert hits[0].url == "https://x.com/a"
    assert hits[0].title == "标题A & 副题"
    assert "摘要A" in hits[0].snippet


def test_search_unknown_provider_raises():
    with pytest.raises(ValueError):
        crawler.search("q", provider="yahoo")


def test_search_top_n_capped(monkeypatch):
    html = "".join(
        f'<li class="b_algo"><h2><a href="https://x.com/{i}">t{i}</a></h2><p>s</p></li>'
        for i in range(10)
    )

    class FakeResp:
        text = html
        encoding = "utf-8"

        def raise_for_status(self):
            pass

    class FakeRequests:
        @staticmethod
        def get(*a, **k):
            return FakeResp()

    monkeypatch.setattr(crawler, "requests", FakeRequests)
    hits = crawler.search("q", top_n=3, provider="bing", api_key="")
    assert len(hits) == 3


def test_fetch_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("boom")
        return type("R", (), {"text": "<html>ok</html>", "encoding": "utf-8", "raise_for_status": lambda s: None})()

    monkeypatch.setattr(crawler.requests, "get", flaky)
    monkeypatch.setattr(crawler, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(crawler.time, "sleep", lambda s: None)
    html = crawler.fetch("https://x.com/p")
    assert "ok" in html
    assert calls["n"] == 2  # 首次失败 + 重试成功


def test_fetch_gives_up_after_retries(monkeypatch):
    def always_fail(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(crawler.requests, "get", always_fail)
    monkeypatch.setattr(crawler, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(crawler.time, "sleep", lambda s: None)
    with pytest.raises(crawler.FetchError):
        crawler.fetch("https://x.com/p")


def test_fetch_respects_robots(monkeypatch):
    monkeypatch.setattr(crawler, "_robots_allowed", lambda url: False)
    with pytest.raises(crawler.FetchError, match="robots"):
        crawler.fetch("https://x.com/p")


def test_fetch_rejects_non_http_url():
    with pytest.raises(crawler.FetchError):
        crawler.fetch("file:///etc/passwd")


def test_normalize_url():
    assert crawler.normalize_url("HTTPS://Example.COM:443/a/b/?q=1#frag") == "https://example.com/a/b?q=1"
    assert crawler.normalize_url("http://x.com/") == "http://x.com/"
    assert crawler.normalize_url("https://x.com/a/") == "https://x.com/a"
    assert crawler.normalize_url("http://x.com:80/a") == "http://x.com/a"  # 默认端口去除
    assert crawler.normalize_url("https://x.com:8080/a") == "https://x.com:8080/a"  # 非默认端口保留
    assert crawler.normalize_url("not a url") == "not a url"  # 非 http(s) 原样返回
    assert crawler.normalize_url("https://x.com:bad/a") == "https://x.com:bad/a"  # 非法端口不炸


def test_seen_url_deduplicates_with_normalization(monkeypatch):
    class FakeRedis:
        """模拟 SET NX：已存在返回 False；记录调用参数。"""

        def __init__(self):
            self.keys: set[str] = set()
            self.calls: list[tuple] = []

        def set(self, key, value, **kwargs):
            self.calls.append((key, value, kwargs))
            if key in self.keys:
                return False
            self.keys.add(key)
            return True

    fake = FakeRedis()
    monkeypatch.setattr(crawler, "_redis", lambda url: fake)

    assert crawler.seen_url("https://x.com/a", redis_url="redis://localhost:6379") is False  # 首次
    assert crawler.seen_url("https://x.com/a/", redis_url="redis://localhost:6379") is True  # 归一化后重复
    assert crawler.seen_url("https://X.COM/a", redis_url="redis://localhost:6379") is True  # host 大小写不敏感
    assert crawler.seen_url("https://x.com/b", redis_url="redis://localhost:6379") is False  # 新 URL
    assert len(fake.keys) == 2  # x.com/a 与 x.com/b
    # 默认 7 天 TTL + NX 语义传给 Redis
    assert fake.calls[0][2]["ex"] == 7 * 86400
    assert fake.calls[0][2]["nx"] is True


def test_seen_url_ttl_zero_persists(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.keys: set[str] = set()
            self.last_kwargs = {}

        def set(self, key, value, **kwargs):
            self.last_kwargs = kwargs
            if key in self.keys:
                return False
            self.keys.add(key)
            return True

    fake = FakeRedis()
    monkeypatch.setattr(crawler, "_redis", lambda url: fake)
    crawler.seen_url("https://x.com/a", redis_url="r", ttl_seconds=0)
    assert "ex" not in fake.last_kwargs  # ttl<=0 不带过期，长期保留


def test_seen_url_disabled_without_redis(monkeypatch):
    monkeypatch.setattr(crawler, "_redis", lambda url: None)
    # Redis 未配置 / 不可用：恒返回 False（不去重，不阻断）
    assert crawler.seen_url("https://x.com/a") is False
    assert crawler.seen_url("https://x.com/a", redis_url="redis://localhost:6379") is False


def test_fetch_many_parallel_and_ordered(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url, *a, **k):
        calls.append(url)
        return f"<html>{url}</html>"

    monkeypatch.setattr(crawler, "fetch", fake_fetch)
    urls = ["https://x.com/1", "https://x.com/2", "https://x.com/3"]
    out = crawler.fetch_many(urls)
    assert [u for u, _ in out] == urls  # 顺序与输入一致
    assert all(h for _, h in out)
    assert sorted(calls) == sorted(urls)  # 每个 URL 都抓取


def test_fetch_many_isolates_failures(monkeypatch):
    def flaky(url, *a, **k):
        if url.endswith("bad"):
            raise crawler.FetchError("boom")
        return "<html>ok</html>"

    monkeypatch.setattr(crawler, "fetch", flaky)
    out = crawler.fetch_many(["https://x.com/good", "https://x.com/bad", "https://x.com/ok"])
    assert out[0] == ("https://x.com/good", "<html>ok</html>")
    assert out[1][1] is None  # 失败项标记 None，不阻断其他
    assert out[2][1] == "<html>ok</html>"


def test_fetch_many_runs_in_parallel(monkeypatch):
    def slow(url, *a, **k):
        time.sleep(0.15)
        return "<html/>"

    monkeypatch.setattr(crawler, "fetch", slow)
    t0 = time.monotonic()
    crawler.fetch_many(["https://x.com/a", "https://x.com/b", "https://x.com/c", "https://x.com/d"], max_workers=4)
    wall = time.monotonic() - t0
    assert wall < 0.55  # 串行需 0.6s，并行 ≈ 0.15s


def test_fetch_many_empty_input():
    assert crawler.fetch_many([]) == []

