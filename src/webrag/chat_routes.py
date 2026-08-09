"""聊天记录接口（/chat/*）：按登录用户（uid）存取会话历史（MySQL chat_conversations）。

- 所有接口需 `Authorization: Bearer <token>`（accounts.require_login），uid 取自 token；
- 会话归属校验：查/改/删均按 id + uid 过滤，他人会话一律 404（不泄露存在性）；
- messages 为整段会话消息数组（前端扁平模型），JSON 序列化存 MySQL JSON 列；
- 删除会话同步删除该用户数据库中的整行记录（前端左侧删除按钮调用）。

负责人：聊天记录 MySQL 化。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from src.webrag.accounts import require_login
from src.webrag.admin.db import AdminDB, AdminDBError

chat_router = APIRouter(prefix="/chat", tags=["chat"])


def _app_error(code: str, message: str, status: int = 500):
    from src.webrag.main import AppError

    return AppError(code, message, status)


def _get_db(request: Request) -> AdminDB:
    db: AdminDB = request.app.state.admindb
    try:
        db.ensure_schema()
    except AdminDBError as exc:
        raise _app_error("INTERNAL_ERROR", f"聊天记录库不可用：{exc}", 503) from exc
    return db


def _default_title(messages: list) -> str:
    """标题缺省取第一条用户消息前 30 字。"""
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "user" and m.get("content"):
            return str(m["content"])[:30] or "新对话"
    return "新对话"


class ConversationCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    messages: list = Field(default_factory=list)


class ConversationSave(BaseModel):
    title: str = Field(default="", max_length=200)
    messages: list = Field(default_factory=list)


# ---- 会话 CRUD ----


@chat_router.get("/conversations")
def list_conversations(request: Request, claims: Annotated[dict, Depends(require_login)]) -> dict:
    """当前用户的会话列表（不含 messages 大字段，按更新时间倒序）。"""
    rows = _get_db(request).list_conversations(claims["uid"])
    return {"conversations": rows}


@chat_router.post("/conversations", status_code=201)
def create_conversation(
    req: ConversationCreate, request: Request, claims: Annotated[dict, Depends(require_login)]
) -> dict:
    """创建会话（首问即建，title 缺省取问题前 30 字）；返回含 id/时间戳的完整会话。"""
    db = _get_db(request)
    title = (req.title.strip() or _default_title(req.messages))[:200]
    conv_id = db.create_conversation(claims["uid"], title, req.messages)
    row = db.get_conversation(conv_id, claims["uid"])
    return {"conversation": row}


@chat_router.get("/conversations/{conv_id}")
def get_conversation(
    conv_id: int, request: Request, claims: Annotated[dict, Depends(require_login)]
) -> dict:
    """会话详情（含 messages）；归属不符 404 NOT_FOUND。"""
    row = _get_db(request).get_conversation(conv_id, claims["uid"])
    if row is None:
        raise _app_error("NOT_FOUND", "会话不存在", 404)
    return {"conversation": row}


@chat_router.put("/conversations/{conv_id}")
def save_conversation(
    conv_id: int, req: ConversationSave, request: Request, claims: Annotated[dict, Depends(require_login)]
) -> dict:
    """保存整段会话（标题 + 消息，回答完成后调用）；归属不符 404。"""
    db = _get_db(request)
    title = (req.title.strip() or _default_title(req.messages))[:200]
    if not db.save_conversation(conv_id, claims["uid"], title, req.messages):
        raise _app_error("NOT_FOUND", "会话不存在", 404)
    return {"status": "ok"}


@chat_router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: int, request: Request, claims: Annotated[dict, Depends(require_login)]
) -> dict:
    """删除会话（同步删除该用户 MySQL 中的记录）；归属不符 404。"""
    if not _get_db(request).delete_conversation(conv_id, claims["uid"]):
        raise _app_error("NOT_FOUND", "会话不存在", 404)
    return {"status": "ok"}
