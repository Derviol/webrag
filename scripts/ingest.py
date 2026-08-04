"""预建知识库批量抓取入库（D2 交付，爬虫 #2 + 向量库 #5 协作）。

用法：
    python scripts/ingest.py "大模型行业动态" "AI 芯片"    # 每个参数为一个主题（转成搜索词）
    python scripts/ingest.py --dry-run "主题"              # 只打印计划，不抓取不入库

流程（每主题）：crawler.search → fetch → parser.parse → chunker.chunk
              → embedder.embed（dense + sparse）→ milvus_store.add
去重：Redis 缓存已入库 URL（SADD webrag:ingest:urls），命中则跳过；
     Redis 不可用时降级为本次运行内内存去重（不阻断入库）。
依赖：先运行 python scripts/init_milvus.py 建库。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 从项目根导入 src

from src.webrag import chunker, crawler, parser
from src.webrag.config import load_settings
from src.webrag.embedder import BGE3Embedder
from src.webrag.milvus_store import MilvusStore

REDIS_URL_KEY = "webrag:ingest:urls"  # Redis 已入库 URL 集合


def _redis_client(redis_url: str):
    """Redis 不可用时降级为 None（本次运行内内存去重），不阻断 ingest。"""
    try:
        import redis

        client = redis.Redis.from_url(redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:
        print(f"[ingest] 警告：Redis 不可用（{exc}），本次仅按内存去重")
        return None


def _url_seen(redis_client, url: str, memory_seen: set[str]) -> bool:
    return url in memory_seen or (redis_client is not None and redis_client.sismember(REDIS_URL_KEY, url))


def _mark_url(redis_client, url: str, memory_seen: set[str]) -> None:
    memory_seen.add(url)
    if redis_client is not None:
        redis_client.sadd(REDIS_URL_KEY, url)


def ingest_topic(store: MilvusStore, embedder: BGE3Embedder, redis_client, memory_seen: set[str], topic: str, settings) -> int:
    """抓取单个主题并入库，返回入库 chunk 数。"""
    hits = crawler.search(
        topic,
        top_n=settings.crawler.top_urls,
        provider=settings.search_provider,
        api_key=settings.search_api_key,
    )
    print(f"[ingest] 主题「{topic}」候选 {len(hits)} 条")

    inserted = 0
    for hit in hits:
        if _url_seen(redis_client, hit.url, memory_seen):
            print(f"[ingest]  跳过（已入库）：{hit.url}")
            continue
        try:
            html = crawler.fetch(hit.url, settings.crawler.request_timeout_seconds)
            doc = parser.parse(html, hit.url)
            chunks = chunker.chunk(
                doc,
                chunk_size=settings.chunker.chunk_size,
                overlap=settings.chunker.overlap,
            )
            if not chunks:
                print(f"[ingest]  跳过（无正文）：{hit.url}")
                continue
            emb = embedder.embed([c.text for c in chunks])
            n = store.add(settings.milvus_collection, chunks, emb)
            _mark_url(redis_client, hit.url, memory_seen)
            inserted += n
            print(f"[ingest]  入库 {n} 块：{hit.url}")
        except NotImplementedError:
            raise
        except Exception as exc:
            print(f"[ingest]  失败：{hit.url} -> {exc}")
    return inserted


def main() -> None:
    ap = argparse.ArgumentParser(description="预建知识库批量抓取入库")
    ap.add_argument("topics", nargs="+", help="主题（搜索词），可多个")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不抓取不入库")
    args = ap.parse_args()

    settings = load_settings()
    if args.dry_run:
        for t in args.topics:
            print(f"[dry-run] 将抓取主题「{t}」（top {settings.crawler.top_urls}）并写入 {settings.milvus_collection}")
        return

    store = MilvusStore(settings.milvus_uri)
    store.connect()
    embedder = BGE3Embedder(settings.embed_model_path)
    embedder.load()
    redis_client = _redis_client(settings.redis_url)
    memory_seen: set[str] = set()

    total = 0
    for topic in args.topics:
        try:
            total += ingest_topic(store, embedder, redis_client, memory_seen, topic, settings)
        except NotImplementedError as exc:
            print(f"[ingest] 终止：依赖模块尚未实现（{exc}）。")
            print("[ingest] 提示：等待 crawler / parser / chunker / embedder / milvus_store.add 对应分支合入后重试。")
            sys.exit(2)
    print(f"[ingest] 完成，共入库 {total} 块。")


if __name__ == "__main__":
    main()
