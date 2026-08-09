"""账户系统（前端登录）：注册 / 登录 / 会话校验。

- 统一账户存 MySQL `users` 表（role: user/admin，管理员由 init_admin.py 创建或走 /admin 登录）；
- 密码哈希与 JWT 复用 admin.auth（PBKDF2 + HS256，密钥 ADMIN_JWT_SECRET），载荷 {sub, role, uid, iat, exp}；
- `require_login` 依赖：校验 Bearer token 并返回 claims，供 /ask、/ask/stream 与 /chat/* 使用（任意登录用户）；
- 登录成功即签发 token（前端存 localStorage，/auth/me 用于启动时恢复会话）。

负责人：登录系统 / 聊天记录 MySQL 化。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from src.webrag.admin import auth
from src.webrag.admin.db import AdminDB, AdminDBError
from src.webrag.config import load_settings

accounts_router = APIRouter(prefix="/auth", tags=["auth"])


def _app_error(code: str, message: str, status: int = 500):
    from src.webrag.main import AppError

    return AppError(code, message, status)


def _get_db(request: Request) -> AdminDB:
    db: AdminDB = request.app.state.admindb
    try:
        db.ensure_schema()
    except AdminDBError as exc:
        raise _app_error("INTERNAL_ERROR", f"账户库不可用：{exc}", 503) from exc
    return db


def require_login(authorization: str = Header(default="")) -> dict:
    """FastAPI 依赖：校验 `Authorization: Bearer <token>`，通过返回 claims {sub=username, role, uid}。

    用法：`claims: dict = Depends(require_login)`。
    未登录/无效/过期 → 401 UNAUTHORIZED（前端弹出登录提示）。
    """
    if not authorization.startswith("Bearer "):
        raise _app_error("UNAUTHORIZED", "请先登录后再提问", 401)
    return auth.verify_token_claims(load_settings(), authorization[7:].strip())


# ---- 请求模型 ----


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, pattern=r"^[\w.\-@]+$")
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


def _token_response(settings, row: dict) -> dict:
    token = auth.create_token(settings, row["username"], role=row["role"], uid=row["uid"])
    return {
        "token": token,
        "token_type": "bearer",
        "expires_in": settings.admin.token_ttl_seconds,
        "username": row["username"],
        "role": row["role"],
        "uid": row["uid"],
    }


# ---- 接口 ----


@accounts_router.post("/register")
def register(req: RegisterRequest, request: Request) -> dict:
    """注册普通用户（role='user'）；用户名重复 → 409 USER_EXISTS。成功即签发 token（自动登录）。"""
    settings = load_settings()
    db = _get_db(request)
    try:
        db.create_user(req.username, auth.hash_password(req.password), role="user")
    except AdminDBError as exc:
        raise _app_error("USER_EXISTS", str(exc), 409) from exc
    row = db.get_user_by_username(req.username)
    return _token_response(settings, row)


@accounts_router.post("/login")
def login(req: LoginRequest, request: Request) -> dict:
    """账户登录（普通用户 + 管理员均可）；校验账号密码 → 签发 JWT。"""
    settings = load_settings()
    db = _get_db(request)
    row = db.get_user_by_username(req.username)
    if row is None or not auth.verify_password(req.password, row["password_hash"]):
        raise _app_error("UNAUTHORIZED", "用户名或密码错误", 401)
    return _token_response(settings, row)


@accounts_router.get("/me")
def me(claims: Annotated[dict, Depends(require_login)]) -> dict:
    """会话校验：返回当前登录用户信息（前端启动时恢复会话 / 判断用户组用）。"""
    return {"username": claims["sub"], "role": claims.get("role", "user"), "uid": claims.get("uid", "")}
