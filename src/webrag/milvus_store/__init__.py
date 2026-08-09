"""向量库访问封装。

接口契约（docs/api.md §3）：
- create_collection(name) / create_qa_collection(name)
- add(collection, chunks, vectors) -> int / add_qa(collection, questions, summaries, sources_json, vectors)
- search(collection, vectors, top_k) -> list[SearchResult] / search_qa(collection, vectors, top_k) -> list[QAHit]
- drop_collection(name)
负责人：#5 向量库开发。注意：需 Milvus >= 2.4 支持 sparse 字段。
"""

from __future__ import annotations

import json
import time

from pymilvus import Collection, connections, utility

from src.webrag.schemas import Chunk, QAHit, SearchResult, Source

from .schema import (
    build_indexes,
    build_offline_schema,
    build_qa_indexes,
    build_qa_schema,
    build_schema,
)


def _fit_varchar(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节截断（Milvus VARCHAR max_length 按字节计，超长写入会报错）。"""
    if not s:
        return ""
    raw = s.encode("utf-8")
    if len(raw) <= max_bytes:
        return s
    return raw[:max_bytes].decode("utf-8", "ignore")


def _hits_to_results(hits) -> list[SearchResult]:
    """hybrid_search 命中 → SearchResult[]（search 共用）。"""
    results: list[SearchResult] = []
    for hit in hits[0]:
        entity = hit.entity
        results.append(
            SearchResult(
                chunk=Chunk(
                    text=entity.get("text") or "",
                    metadata={
                        "url": entity.get("url") or "",
                        "title": entity.get("title") or "",
                        "publish_time": entity.get("publish_time") or "",
                        "seq": int(entity.get("seq") or 0),
                    },
                ),
                score=float(hit.score),
            )
        )
    return results


class MilvusStore:
    def __init__(self, uri: str):
        self._uri = uri
        self._connected = False

    def connect(self) -> None:
        connections.connect(alias="default", uri=self._uri)
        self._connected = True

    def health(self) -> bool:
        try:
            if not self._connected:
                self.connect()
            utility.get_server_version()  # 真实 gRPC 往返；pymilvus 2.4 无 connections.get_connection
            return True
        except Exception:
            return False

    def has_collection(self, name: str) -> bool:
        """检查 collection 是否存在（替代直接调 pymilvus.utility.has_collection，统一接口）。"""
        return utility.has_collection(name)

    def create_collection(self, name: str) -> Collection:
        """建 collection + 索引 + 加载。已存在时由调用方用 utility.has_collection 判断。"""
        collection = Collection(name=name, schema=build_schema(), using="default")
        build_indexes(collection)
        collection.load()
        return collection

    def add(self, collection_name: str, chunks, vectors) -> int:
        """批量写入：chunks + vectors（EmbedResult，含 dense + sparse），返回写入行数。

        网页元数据不可控，写入前按 schema 上限截断（title≤512 / url≤2048 / text≤65535）。
        """
        from pymilvus import Collection

        collection = Collection(collection_name, using="default")
        collection.load()  # load 幂等，insert 前确保可写
        rows = []
        for i, chunk in enumerate(chunks):
            rows.append(
                {
                    "text": _fit_varchar(chunk.text, 65535),
                    "url": _fit_varchar(chunk.metadata.url, 2048),
                    "title": _fit_varchar(chunk.metadata.title, 512),
                    "publish_time": _fit_varchar(chunk.metadata.publish_time, 64),
                    "seq": chunk.metadata.seq or (i + 1),
                    "dense_vec": vectors.dense[i],
                    "sparse_vec": vectors.sparse[i],
                }
            )
        result = collection.insert(rows)
        collection.flush()
        return len(result.primary_keys)

    # ---- 离线知识库（管理后台入库，独立于问答链路；见 build_offline_schema）----

    def create_offline_collection(self, name: str) -> Collection:
        """建离线知识库 collection（build_offline_schema）+ 索引 + 加载。已存在由调用方判断。"""
        collection = Collection(name=name, schema=build_offline_schema(), using="default")
        build_indexes(collection)
        collection.load()
        return collection

    def ensure_offline_collection(self, name: str) -> Collection:
        """离线 collection 存在则返回，否则创建（幂等；并发下创建失败会回查存在性）。"""
        if not self.has_collection(name):
            try:
                return self.create_offline_collection(name)
            except Exception:
                if self.has_collection(name):  # 并发创建竞态：另一请求已建好
                    return Collection(name, using="default")
                raise
        return Collection(name, using="default")

    def add_offline(self, collection_name: str, chunks, vectors, doc_ref: str) -> int:
        """批量写入离线知识块：chunks + vectors（EmbedResult）+ doc_ref，返回写入行数。

        与 add() 同字段上限（text≤65535 / title≤512 / url≤2048 / publish_time≤64），
        额外写入 doc_ref（文档归属，删除时按此字段批量清除）。
        """
        from pymilvus import Collection

        collection = Collection(collection_name, using="default")
        collection.load()  # load 幂等，insert 前确保可写
        rows = []
        for i, chunk in enumerate(chunks):
            rows.append(
                {
                    "text": _fit_varchar(chunk.text, 65535),
                    "url": _fit_varchar(chunk.metadata.url, 2048),
                    "title": _fit_varchar(chunk.metadata.title, 512),
                    "publish_time": _fit_varchar(chunk.metadata.publish_time, 64),
                    "seq": chunk.metadata.seq or (i + 1),
                    "doc_ref": _fit_varchar(doc_ref, 128),
                    "dense_vec": vectors.dense[i],
                    "sparse_vec": vectors.sparse[i],
                }
            )
        result = collection.insert(rows)
        collection.flush()
        return len(result.primary_keys)

    def delete_by_doc_ref(self, collection_name: str, doc_ref: str) -> int:
        """按 doc_ref 删除该文档的全部知识块（管理后台删除文档用），返回删除行数。

        doc_ref 为服务端生成的 offline_<uuid>（无引号等特殊字符），表达式安全；
        collection 不存在 / 无匹配时返回 0，不抛错。
        注意：pymilvus 2.4 的 delete() 返回对象不含被删主键列表，故删除前先用
        count(*) 查询计数，返回的是"删除前该文档的块数"（用于前端展示）。
        """
        from pymilvus import Collection

        try:
            if not self.has_collection(collection_name):
                return 0
            collection = Collection(collection_name, using="default")
            collection.load()
            expr = f'doc_ref == "{doc_ref}"'
            counted = collection.query(expr=expr, output_fields=["count(*)"])
            n = int(counted[0]["count(*)"]) if counted else 0
            collection.delete(expr=expr)
            collection.flush()
            return n
        except Exception as exc:
            raise RuntimeError(f"按文档删除知识块失败（{collection_name}/{doc_ref}）：{exc}") from exc

    def search(self, collection_name: str, vectors, top_k: int, dense_weight: float = 0.5, sparse_weight: float = 0.5) -> list[SearchResult]:
        """Top-k 混合检索：dense + sparse 双向量加权融合（WeightedRanker），返回 SearchResult[]。

        - vectors 为 EmbedResult（单条查询：dense[0] + sparse[0]）；
        - sparse 向量为空时自动降级为 dense-only，避免空查询报错；
        - dense_weight / sparse_weight：动态权重（P0 优化），根据查询意图调节。
        """
        from pymilvus import AnnSearchRequest, Collection, WeightedRanker

        collection = Collection(collection_name, using="default")
        collection.load()  # load 幂等（pymilvus 2.4 无 is_loaded 属性，直接加载）

        dense_vec = vectors.dense[0]
        sparse_vec = vectors.sparse[0] if vectors.sparse else {}
        # pymilvus 2.4.15：hybrid_search 要求 AnnSearchRequest 对象（dict 已被移除）
        reqs = [
            AnnSearchRequest(
                data=[dense_vec],
                anns_field="dense_vec",
                param={"metric_type": "COSINE", "params": {}},
                limit=top_k,
            )
        ]
        if sparse_vec:
            reqs.append(
                AnnSearchRequest(
                    data=[sparse_vec],
                    anns_field="sparse_vec",
                    param={"metric_type": "IP"},
                    limit=top_k,
                )
            )
        ranker = WeightedRanker(dense_weight, sparse_weight) if len(reqs) == 2 else WeightedRanker(1.0)
        hits = collection.hybrid_search(
            reqs,
            rerank=ranker,  # pymilvus 2.4.15：第二参名为 rerank（旧名 ranker 已移除）
            limit=top_k,
            output_fields=["text", "url", "title", "publish_time", "seq"],
        )
        return _hits_to_results(hits)

    def drop_collection(self, name: str) -> None:
        """删除 collection（临时库 qa_<id> 用后即清）。不存在时抛错，由调用方 guard。"""
        Collection(name, using="default").drop()

    # ---- 问答缓存（question → 摘要 + 来源，/ask 先查后答，见架构 §6）----

    def create_qa_collection(self, name: str) -> Collection:
        """建问答缓存 collection（build_qa_schema）+ 索引 + 加载。已存在由调用方判断。"""
        collection = Collection(name=name, schema=build_qa_schema(), using="default")
        build_qa_indexes(collection)
        collection.load()
        return collection

    def add_qa(
        self,
        collection_name: str,
        questions: list[str],
        summaries: list[str],
        sources_json: list[str],
        vectors,
    ) -> int:
        """写入问答缓存：问题原文 + 摘要 + 来源(JSON 串) + 问题向量，返回写入行数。

        字段按 schema 上限截断（question≤2048 / summary≤65535 / sources≤8192，UTF-8 字节）。
        """
        from pymilvus import Collection

        collection = Collection(collection_name, using="default")
        collection.load()  # load 幂等，insert 前确保可写
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for i, q in enumerate(questions):
            rows.append(
                {
                    "question": _fit_varchar(q, 2048),
                    "summary": _fit_varchar(summaries[i], 65535),
                    "sources": _fit_varchar(sources_json[i], 8192),
                    "created_at": now,
                    "question_vec": vectors.dense[i],
                }
            )
        result = collection.insert(rows)
        collection.flush()
        return len(result.primary_keys)

    def search_qa(self, collection_name: str, vectors, top_k: int) -> list[QAHit]:
        """问答缓存 Top-k 检索（dense-only COSINE，question_vec）：返回 QAHit[]（分数降序）。

        sparse 不参与：问题相似度用 dense 语义余弦即可，避免稀疏词法噪声误命中。
        """
        from pymilvus import AnnSearchRequest, Collection, WeightedRanker

        collection = Collection(collection_name, using="default")
        collection.load()

        req = AnnSearchRequest(
            data=[vectors.dense[0]],
            anns_field="question_vec",
            param={"metric_type": "COSINE", "params": {}},
            limit=top_k,
        )
        hits = collection.hybrid_search(
            [req],
            rerank=WeightedRanker(1.0),
            limit=top_k,
            output_fields=["question", "summary", "sources"],
        )
        out: list[QAHit] = []
        for hit in hits[0]:
            entity = hit.entity
            out.append(
                QAHit(
                    question=entity.get("question") or "",
                    summary=entity.get("summary") or "",
                    sources=_parse_sources_json(entity.get("sources") or ""),
                    score=float(hit.score),
                )
            )
        return out


def _parse_sources_json(raw: str) -> list[Source]:
    """问答缓存 sources 字段（JSON 数组 [{index,title,url}]）→ Source[]；解析失败返回空列表。"""
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except Exception:
        return []
    sources: list[Source] = []
    for it in items:
        if not isinstance(it, dict) or not it.get("url"):
            continue
        sources.append(
            Source(
                index=int(it.get("index", 0)),
                title=str(it.get("title", "")),
                url=str(it["url"]),
            )
        )
    return sources
