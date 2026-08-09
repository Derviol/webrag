"""管理后台 HTTP 接口（/admin/*）：登录 + 离线知识文档管理。

认证：除 `POST /admin/auth/login` 外均需 `Authorization: Bearer <token>`（auth.require_admin）。
错误信封复用 main.AppError（api.md §1.1 统一结构；本模块新增错误码：
UNAUTHORIZED / NOT_FOUND / TOO_MANY_ATTEMPTS，见 api.md §1.4）。

隔离性：本路由只读写「管理库（MySQL webrag_admin）+ 离线知识库（Milvus webrag_offline_kb）」，
不触碰 /ask 问答链路的 webrag_qa / 临时库与任何现有状态。
负责人：管理后台（离线知识入库）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field

from src.webrag.config import load_settings
from src.webrag.milvus_store import MilvusStore

from . import auth, ingest
from .db import AdminDB, AdminDBError

admin_router = APIRouter(prefix="/admin", tags=["admin"])


def _app_error(code: str, message: str, status: int = 500):
    """懒导入 main.AppError（避免 main ↔ admin 循环导入）。"""
    from src.webrag.main import AppError

    return AppError(code, message, status)


def _get_db(request: Request) -> AdminDB:
    """取应用级 AdminDB 并幂等确保表结构；MySQL 不可用 → 503 统一错误信封。"""
    db: AdminDB = request.app.state.admindb
    try:
        db.ensure_schema()
    except AdminDBError as exc:
        raise _app_error("INTERNAL_ERROR", f"管理库不可用：{exc}", 503) from exc
    return db


# ---- 请求模型 ----


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class CreateTextRequest(BaseModel):
    """粘贴文本入库：title 可空（默认取正文前 30 字符）；content 必填。"""
    title: str = Field(default="", max_length=512)
    content: str = Field(..., min_length=1)


# ---- 认证 ----


@admin_router.post("/auth/login")
def login(req: LoginRequest, request: Request) -> dict:
    """管理员登录：校验账号密码 → 签发 JWT（有效期 settings.admin.token_ttl_seconds）。

    连续失败限流：15 分钟内同一用户名失败 ≥5 次锁定（Redis；Redis 不可用自动放行）。
    """
    settings = load_settings()
    db = _get_db(request)

    if not auth.check_login_allowed(settings, req.username):
        raise _app_error("TOO_MANY_ATTEMPTS", "登录失败次数过多，请 15 分钟后再试", 429)

    row = db.get_user_by_username(req.username)
    if row is None or not auth.verify_password(req.password, row["password_hash"]):
        n = auth.record_login_failure(settings, req.username)
        left = max(0, 5 - n)
        raise _app_error("UNAUTHORIZED", f"用户名或密码错误（剩余尝试次数：{left}）", 401)

    auth.clear_login_failures(settings, req.username)
    if row.get("role") != "admin":
        raise _app_error("FORBIDDEN", "无权限登录管理后台（仅管理员可访问）", 403)
    token = auth.create_token(settings, req.username, role=row.get("role", "user"), uid=row.get("uid", ""))
    return {
        "token": token,
        "token_type": "bearer",
        "expires_in": settings.admin.token_ttl_seconds,
        "username": req.username,
        "role": row.get("role", "user"),
        "uid": row.get("uid", ""),
    }


@admin_router.get("/auth/me")
def admin_me(claims: Annotated[dict, Depends(auth.require_admin)]) -> dict:
    """当前后台登录用户信息（含 role/uid）：前端直访 /admin/ 时判断用户组——admin 放行，普通用户 403。"""
    return {"username": claims["sub"], "role": claims.get("role", "user"), "uid": claims.get("uid", "")}


# ---- 文档入库 ----


def _create_and_ingest(
    db: AdminDB, *, title: str, content: str, source_type: str, file_name: str = ""
) -> dict:
    """构建 Document（html 过 parser 清洗）→ 校验 → 落库（processing，存清洗后正文）→ 异步入库 → 202。"""
    settings = load_settings()
    content = content or ""
    if not content.strip():
        raise _app_error("VALIDATION_ERROR", "文档内容为空", 422)
    doc_ref = ingest.new_doc_ref()
    try:
        doc = ingest.to_document(title, content, doc_ref, source_type)
    except ValueError as exc:
        raise _app_error("VALIDATION_ERROR", str(exc), 422) from exc
    if len(doc.text) > settings.admin.max_chars_per_doc:
        raise _app_error(
            "VALIDATION_ERROR",
            f"文档超过字符上限（{settings.admin.max_chars_per_doc} 字符，当前 {len(doc.text)}）",
            422,
        )
    clean_title = (title.strip() or doc.title or doc.text.strip()[:30] or "未命名文档").strip()
    doc_id = db.create_document(
        doc_ref=doc_ref, title=clean_title, source_type=source_type, file_name=file_name, content=doc.text
    )
    ingest.ingest_document_async(db, settings, doc_id=doc_id, doc_ref=doc_ref, doc=doc)
    return {"id": doc_id, "status": "processing", "title": clean_title, "chunk_count": 0}


@admin_router.post("/documents", status_code=202)
def create_document_text(req: CreateTextRequest, request: Request, _admin: str = Depends(auth.require_admin)) -> dict:
    """粘贴文本入库（JSON {title?, content}）：立即返回 202，解析入库在后台执行。"""
    return _create_and_ingest(_get_db(request), title=req.title, content=req.content, source_type="text")


@admin_router.post("/documents/upload", status_code=202)
def upload_document(
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()] = "",
    _admin: str = Depends(auth.require_admin),
) -> dict:
    """文件入库（multipart：file + 可选 title）。支持 .txt / .md / .html；大小上限见 settings.admin。"""
    settings = load_settings()
    raw = file.file.read(settings.admin.max_file_bytes + 1)
    if len(raw) > settings.admin.max_file_bytes:
        raise _app_error(
            "VALIDATION_ERROR",
            f"文件超过大小上限（{settings.admin.max_file_bytes // 1024 // 1024}MB）",
            422,
        )
    suffix = Path(file.filename or "").suffix.lower()
    source_type = "html" if suffix == ".html" else ("md" if suffix in (".md", ".markdown") else "text")
    content = raw.decode("utf-8", errors="replace")
    file_name = file.filename or ""
    return _create_and_ingest(
        _get_db(request),
        title=title,
        content=content,
        source_type=source_type,
        file_name=file_name,
    )


# ---- 文档查询 / 删除 ----


@admin_router.get("/documents")
def list_documents(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    _admin: str = Depends(auth.require_admin),
) -> dict:
    """文档列表（按创建倒序，不含 content 原文；limit ≤ 500）。"""
    limit = min(max(int(limit), 1), 500)
    offset = max(int(offset), 0)
    db = _get_db(request)
    rows = db.list_documents(limit=limit, offset=offset)
    return {"documents": rows, "total": db.count_documents()}


@admin_router.get("/documents/{doc_id}")
def get_document(doc_id: int, request: Request, _admin: str = Depends(auth.require_admin)) -> dict:
    """文档详情（含 content 原文与入库状态；processing 期间前端轮询本接口）。"""
    row = _get_db(request).get_document(doc_id)
    if row is None:
        raise _app_error("NOT_FOUND", "文档不存在", 404)
    return {"document": row}


@admin_router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, request: Request, _admin: str = Depends(auth.require_admin)) -> dict:
    """删除文档：先按 doc_ref 清离线库知识块（Milvus 不可用则保留记录并报错，防孤儿块），再删记录。"""
    settings = load_settings()
    db = _get_db(request)
    row = db.get_document(doc_id)
    if row is None:
        raise _app_error("NOT_FOUND", "文档不存在", 404)

    store = MilvusStore(settings.milvus_uri)
    try:
        store.connect()
        deleted = store.delete_by_doc_ref(settings.milvus_offline_collection, row["doc_ref"])
    except Exception as exc:
        raise _app_error("INTERNAL_ERROR", f"离线知识块删除失败（记录保留，请稍后重试）：{exc}", 503) from exc

    db.delete_document(doc_id)
    return {"status": "ok", "deleted_chunks": deleted}
