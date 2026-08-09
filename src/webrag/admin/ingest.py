"""离线知识入库：管理员提交的文档 → 解析 → 切块 → 嵌入 → 写入离线知识库（Milvus）。

链路复用项目既有公共管线（docs/api.md §3），保证入库知识格式与项目要求一致：
- HTML：`parser.parse`（trafilatura 去噪提取正文）；txt / md：原文直接使用；
- 切块：`chunker.chunk`（参数与 settings.chunker 对齐，与问答链路粒度一致）；
- 嵌入：`retriever.get_embedder()`（BGE-M3 进程内单实例，与 /ask 共享，不额外占显存/内存）；
- 存储：`MilvusStore.add_offline` → 独立 collection `webrag_offline_kb`
  （标准 KB schema + doc_ref 字段，支持按文档整体删除，不影响 webrag_qa / 临时库）。

入库为**后台线程异步执行**（POST /admin/documents 立即返回 processing）：
CPU 嵌入耗时长，串行线程内执行避免阻塞 /ask 等请求；
状态经 MySQL admin_documents 记录（processing → done / failed），前端轮询展示。
同进程内的入库嵌入调用用 `_embed_lock` 串行化（BGE-M3 单实例，防并发前向竞态）。

负责人：管理后台（离线知识入库）。
"""

from __future__ import annotations

import threading
import uuid

from src.webrag import chunker, parser
from src.webrag.logger import get_logger
from src.webrag.milvus_store import MilvusStore
from src.webrag.retriever import get_embedder

_log = get_logger("admin.ingest")

# 串行化同进程内的入库嵌入（BGE-M3 单实例；/ask 的嵌入不在本锁内，不互相阻塞）
_embed_lock = threading.Lock()


def new_doc_ref() -> str:
    """文档唯一引用（Milvus doc_ref / 展示用 url 后缀）。服务端生成，无特殊字符，表达式安全。"""
    return f"offline_{uuid.uuid4().hex}"


def to_document(title: str, content: str, doc_ref: str, source_type: str):
    """内容 → Document（复用项目契约）：html 走 parser 清洗；txt/md 原文直用。

    source_type：text / md / html（routes 从文件名后缀或请求体判定）。
    """
    from src.webrag.schemas import Document

    if source_type == "html":
        doc = parser.parse(content, url=f"offline://{doc_ref}")
        if not doc.text:
            raise ValueError("HTML 正文提取为空（动态渲染页面或无正文，请改用纯文本/Markdown）")
        doc.url = f"offline://{doc_ref}"
        return doc
    return Document(title=title, text=content, url=f"offline://{doc_ref}")


def _ingest_job(
    db,
    settings,
    *,
    doc_id: int,
    doc_ref: str,
    doc,
) -> None:
    """后台入库任务：切块 → 嵌入 → 写离线库 → 回写状态。任何失败落 failed 并留错误信息。

    doc 为路由层已构建好的 Document（html 已过 parser 清洗；txt/md 原文直用）。
    """
    try:
        chunks = chunker.chunk(
            doc,
            chunk_size=settings.chunker.chunk_size,
            overlap=settings.chunker.overlap,
            respect_paragraph=settings.chunker.respect_paragraph,
        )
        if not chunks:
            raise ValueError("文档清洗后无可入库内容")
        _log.info("admin.ingest_chunked", extra={"fields": {"doc_id": doc_id, "chunks": len(chunks)}})

        with _embed_lock:
            emb = get_embedder().embed([c.text for c in chunks])

        store = MilvusStore(settings.milvus_uri)
        store.connect()
        store.ensure_offline_collection(settings.milvus_offline_collection)
        n = store.add_offline(settings.milvus_offline_collection, chunks, emb, doc_ref)
        db.update_document_status(doc_id, "done", chunk_count=n)
        _log.info(
            "admin.ingest_done",
            extra={"fields": {"doc_id": doc_id, "chunks": n, "chars": len(doc.text), "collection": settings.milvus_offline_collection}},
        )
    except Exception as exc:  # 入库失败统一落 failed 状态，不让线程静默死亡
        _log.error("admin.ingest_failed", extra={"fields": {"doc_id": doc_id, "error": str(exc)}}, exc_info=True)
        try:
            db.update_document_status(doc_id, "failed", error_message=str(exc))
        except Exception as exc2:
            _log.error("admin.ingest_status_writeback_failed", extra={"fields": {"doc_id": doc_id, "error": str(exc2)}})


def ingest_document_async(
    db,
    settings,
    *,
    doc_id: int,
    doc_ref: str,
    doc,
) -> threading.Thread:
    """异步启动入库任务（daemon 线程），立即返回。"""
    t = threading.Thread(
        target=_ingest_job,
        kwargs={
            "db": db,
            "settings": settings,
            "doc_id": doc_id,
            "doc_ref": doc_ref,
            "doc": doc,
        },
        name=f"admin-ingest-{doc_id}",
        daemon=True,
    )
    t.start()
    return t
