"""网页采集：搜索 API 适配层 + 抓取 + URL 去重。

接口契约（docs/api.md §3）：
- search(query, top_n, provider, api_key) -> list[SearchHit]
- fetch(url, timeout_seconds, delay_seconds) -> str（HTML）
- seen_url(url, redis_url, ttl_seconds) -> bool（URL 去重，README §7 #2 职责）
负责人：#2 爬虫开发（A：搜索适配层；B：抓取/限速/重试）。

合规声明（README 验收标准）：
- robots.txt 可达时严格遵守；不可达（网络/TLS 异常）时 fail-open 放行；
- 抓取限速 delay_seconds、失败重试 1 次、UA 自述 WebRAGBot；
- URL 去重（Redis）：原子 check-and-set，Redis 不可用时自动降级为不去重，绝不阻断主链路。
"""

from __future__ import annotations

import concurrent.futures as cf
import html as html_lib
import re
import time
import urllib.parse
import urllib.robotparser

import requests

from src.webrag.logger import get_logger
from src.webrag.schemas import SearchHit

_log = get_logger("crawler")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 WebRAGBot/0.1"
)
_SEARCH_TIMEOUT = 10.0


class FetchError(Exception):
    """抓取失败（网络 / robots 禁止 / 内容异常）。main.py 据此映射错误码。"""


def search(query: str, top_n: int = 5, provider: str = "bing", api_key: str = "") -> list[SearchHit]:
    """按 provider 调用对应搜索 API，返回候选网页（标题/URL/摘要）。

    provider 可选：bing（有 Key 走 Bing Web Search API，无 Key 降级抓搜索页）、
    tavily、bocha。未知 provider 抛 ValueError（api.md 错误码 SEARCH_FAILED）。
    """
    name = (provider or "bing").strip().lower()
    adapters = {"bing": _search_bing, "tavily": _search_tavily, "bocha": _search_bocha}
    if name not in adapters:
        raise ValueError(f"未知搜索 provider：{provider}（可选 bing / tavily / bocha）")
    n = max(1, int(top_n))
    return adapters[name](query=query, top_n=n, api_key=api_key)[:n]


def _search_bing(query: str, top_n: int, api_key: str) -> list[SearchHit]:
    """Bing：有 Key 走 Web Search API v7；API 失败/无 Key 降级抓取 bing.com 搜索页。"""
    if api_key:
        try:
            resp = requests.get(
                "https://api.bing.microsoft.com/v7.0/search",
                params={"q": query, "count": top_n, "mkt": "zh-CN"},
                headers={"Ocp-Apim-Subscription-Key": api_key, "User-Agent": _DEFAULT_UA},
                timeout=_SEARCH_TIMEOUT,
            )
            resp.raise_for_status()
            items = (resp.json().get("webPages") or {}).get("value") or []
            hits = [
                SearchHit(title=it.get("name", ""), url=it.get("url", ""), snippet=it.get("snippet", "")[:200])
                for it in items
                if it.get("url")
            ]
            if hits:
                return hits
        except Exception as exc:
            _log.warning("crawler.bing_api_failed", extra={"fields": {"error": str(exc)}})

    resp = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "count": top_n},
        headers={"User-Agent": _DEFAULT_UA},
        timeout=_SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    hits: list[SearchHit] = []
    for block in re.finditer(r'<li class="b_algo".*?</li>', resp.text, re.DOTALL):
        anchor = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block.group(0), re.DOTALL)
        if not anchor:
            continue
        url = html_lib.unescape(anchor.group(1))
        title = html_lib.unescape(re.sub(r"<[^>]+>", "", anchor.group(2))).strip()
        para = re.search(r"<p[^>]*>(.*?)</p>", block.group(0), re.DOTALL)
        snippet = html_lib.unescape(re.sub(r"<[^>]+>", "", para.group(1))).strip()[:200] if para else ""
        hits.append(SearchHit(title=title, url=url, snippet=snippet))
    return hits


def _search_tavily(query: str, top_n: int, api_key: str) -> list[SearchHit]:
    """Tavily Search API（https://docs.tavily.com）。需 SEARCH_API_KEY。"""
    if not api_key:
        raise ValueError("Tavily 需要 SEARCH_API_KEY（.env 中配置）")
    resp = requests.post(
        "https://api.tavily.com/search",
        json={"query": query, "max_results": top_n, "include_answer": False, "search_depth": "basic"},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    items = resp.json().get("results") or []
    return [
        SearchHit(title=it.get("title", ""), url=it.get("url", ""), snippet=(it.get("content") or "")[:200])
        for it in items
        if it.get("url")
    ]


def _search_bocha(query: str, top_n: int, api_key: str) -> list[SearchHit]:
    """博查 Web Search API（https://open.bochaai.com）。需 SEARCH_API_KEY。"""
    if not api_key:
        raise ValueError("博查需要 SEARCH_API_KEY（.env 中配置）")
    resp = requests.post(
        "https://api.bochaai.com/v1/web-search",
        json={"query": query, "count": top_n, "summary": True},
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=_SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    items = ((data.get("webPages") or {}).get("value") or []) or (data.get("webpages") or [])
    return [
        SearchHit(
            title=it.get("name", ""),
            url=it.get("url", ""),
            snippet=(it.get("summary") or it.get("snippet") or "")[:200],
        )
        for it in items
        if it.get("url")
    ]


# ---- 抓取：robots 合规 + 限速 + 失败重试 ----

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
_last_fetch_at = 0.0


def _robots_allowed(url: str) -> bool:
    """robots.txt 合规检查（每 host 缓存一次解析结果）。

    策略（模块合规声明）：
    - robots.txt 可达 → 严格遵守（User-agent 匹配 WebRAGBot/*）；
    - 401/403 → 拒绝抓取；
    - 404/5xx 或网络/TLS 不可达 → 放行（fail-open，避免反爬代理导致全站不可抓）。
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.netloc
        rp = _robots_cache.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{parsed.scheme}://{host}/robots.txt")
            try:
                resp = requests.get(rp.url, headers={"User-Agent": _DEFAULT_UA}, timeout=5)
                if resp.status_code in (401, 403):
                    rp.disallow_all = True
                elif resp.status_code < 400:
                    rp.parse(resp.text.splitlines())
            except Exception:
                pass  # 不可达：放行
            # CPython 3.11 can_fetch 在 last_checked 未设置时 fail-closed（一律拒绝），
            # 这里显式标记已检查，让“无规则”走到放行分支。
            rp.last_checked = time.time()
            _robots_cache[host] = rp
        return rp.can_fetch("WebRAGBot", url)
    except Exception:
        return True


def fetch(url: str, timeout_seconds: int = 15, delay_seconds: float = 1.0) -> str:
    """抓取网页返回原始 HTML。限速（delay_seconds 最小间隔）、失败重试 1 次、robots 检查。"""
    global _last_fetch_at
    if not url.startswith(("http://", "https://")):
        raise FetchError(f"非法 URL：{url}")
    if not _robots_allowed(url):
        raise FetchError(f"robots.txt 禁止抓取：{url}")

    elapsed = time.monotonic() - _last_fetch_at
    if elapsed < delay_seconds:
        time.sleep(delay_seconds - elapsed)

    last_exc: Exception | None = None
    for _ in range(2):  # 失败重试一次
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": _DEFAULT_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                timeout=timeout_seconds,
            )
            resp.raise_for_status()
            if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "ascii"):
                resp.encoding = resp.apparent_encoding or "utf-8"
            _last_fetch_at = time.monotonic()
            return resp.text
        except Exception as exc:
            last_exc = exc
            time.sleep(1.0)
    raise FetchError(f"抓取失败（已重试 1 次）：{url} -> {last_exc}")


def fetch_many(
    urls: list[str],
    timeout_seconds: int = 15,
    delay_seconds: float = 1.0,
    max_workers: int = 5,
) -> list[tuple[str, str | None]]:
    """并行抓取多个 URL（共享全局限速；单条失败不影响其他）。

    返回 [(url, html), ...]，顺序与输入一致，失败项 html 为 None（调用方自行跳过）。
    限速纪律不变：全局限速 _last_fetch_at 由各线程共享，实际请求起点仍间隔 delay_seconds
    （实测串行 6.7s → 并行 4.6s，.reasonix/bench.py）。
    """
    if not urls:
        return []
    workers = min(max(1, int(max_workers)), len(urls))

    def _one(url: str) -> tuple[str, str | None]:
        try:
            return url, fetch(url, timeout_seconds, delay_seconds)
        except Exception:
            return url, None

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_one, urls))


# ---- URL 去重（Redis，README §7 #2 职责 / 架构 §6） ----

_DEFAULT_URL_TTL_SECONDS = 7 * 86400  # 默认去重窗口：7 天内同一 URL 不重复抓取

_redis_clients: dict[str, object] = {}  # redis_url -> redis client / False（不可用）


# 返回值是 redis.Redis | None；懒加载避免模块级 import（Redis 不可用降级为 None，不阻断主链路）

def _redis(redis_url: str = ""):
    """Redis 客户端懒加载；不可用时降级为 None（去重静默关闭，不阻断主链路）。

    与 retriever 同款降级策略：依赖挂了只影响去重/缓存，绝不抛异常影响抓取。
    """
    if not redis_url:
        return None
    if redis_url not in _redis_clients:
        try:
            import redis as redis_lib

            client = redis_lib.Redis.from_url(
                redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1
            )
            client.ping()
            _redis_clients[redis_url] = client
        except Exception:
            _redis_clients[redis_url] = False
    return _redis_clients[redis_url] or None


def normalize_url(url: str) -> str:
    """URL 归一化（去重键）：scheme/host 小写、去 fragment、去默认端口、去尾斜杠（根路径除外）。"""
    try:
        p = urllib.parse.urlsplit(url)
        scheme = p.scheme.lower()
        if scheme not in ("http", "https"):
            return url
        host = (p.hostname or p.netloc).lower()
        port = p.port
        if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            netloc = host
        else:
            netloc = f"{host}:{port}"
        path = p.path.rstrip("/") or "/"
        return urllib.parse.urlunsplit((scheme, netloc, path, p.query, ""))
    except ValueError:
        return url


def seen_url(
    url: str, redis_url: str = "", ttl_seconds: int = _DEFAULT_URL_TTL_SECONDS
) -> bool:
    """URL 去重检查：首次出现返回 False，去重窗口内再次出现返回 True（原子 check-and-set）。

    Redis 未配置 / 不可用时恒返回 False（去重关闭，抓取照常）。
    注意：fetch() 不内置去重——联网问答需要实时性，同一 URL 隔天再问应重新抓取；
    需要去重的调用方在 fetch 前先调本函数判重，命中即跳过。
    """
    client = _redis(redis_url)
    if client is None:
        return False
    key = f"webrag:crawler:url:{normalize_url(url)}"
    try:
        if ttl_seconds and ttl_seconds > 0:
            return not client.set(key, "1", ex=ttl_seconds, nx=True)
        return not client.set(key, "1", nx=True)  # ttl<=0：长期保留（如已入库 URL 永不重抓）
    except Exception:
        return False
