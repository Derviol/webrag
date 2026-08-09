"""管理后台认证：PBKDF2 密码哈希 + JWT 签发/校验 + 登录失败限流（Redis，best-effort）。

- 密码哈希：stdlib `hashlib.pbkdf2_hmac`（SHA-256、600k 迭代、16 字节随机盐，OWASP 建议），
  存储格式 `pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>`，校验时逐项比对（无外部依赖）；
- JWT：PyJWT HS256，payload {sub: username, iat, exp}，密钥取 .env 的 ADMIN_JWT_SECRET；
  未配置密钥时用进程内随机密钥并告警（重启后 token 失效，仅限本地开发误配场景）；
- 登录限流：Redis INCR + EXPIRE——15 分钟内同一用户名连续失败 ≥5 次锁定 15 分钟；
  Redis 不可用时自动放行（管理后台不因缓存故障锁死，登录本身仍靠账号密码把关）。

负责人：管理后台（离线知识入库）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import jwt
from fastapi import Header

from src.webrag.config import Settings
from src.webrag.logger import get_logger

_log = get_logger("admin.auth")

_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_ALGO = "pbkdf2_sha256"

# 登录限流参数（15 分钟窗口内连续失败 ≥ _MAX_FAILS 次 → 锁定 15 分钟）
_RATE_WINDOW_SECONDS = 15 * 60
_MAX_FAILS = 5

_redis_client = None  # 懒加载；False 表示不可用


def _get_redis(settings: Settings):
    """Redis 客户端懒加载；不可用（未启动/超时）返回 None，调用方放行。"""
    global _redis_client
    if _redis_client is None:
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=1,
                socket_timeout=1,
                decode_responses=True,
            )
            client.ping()
            _redis_client = client
        except Exception:
            _redis_client = False
    return _redis_client or None


# ---- 密码哈希（stdlib PBKDF2，无外部依赖） ----


def hash_password(password: str) -> str:
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{_HASH_ALGO}${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.split("$", 3)
        if algo != _HASH_ALGO:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


# ---- JWT 签发 / 校验 ----


def _secret(settings: Settings) -> str:
    """JWT 密钥：.env 的 ADMIN_JWT_SECRET 优先；为空时进程内随机密钥 + 一次性告警。"""
    if settings.admin_jwt_secret:
        return settings.admin_jwt_secret
    if not getattr(_secret, "_fallback", None):
        _secret._fallback = secrets.token_hex(32)
        _log.warning("admin.jwt_secret_missing", extra={"fields": {"hint": "配置 .env 的 ADMIN_JWT_SECRET 后重启"}})
    return _secret._fallback


def create_token(settings: Settings, username: str, role: str = "user", uid: str = "") -> str:
    """签发 JWT：payload {sub, role, uid, iat, exp}。role 用于后台访问控制（admin 才可进 /admin/*）。"""
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "uid": uid,
        "iat": now,
        "exp": now + settings.admin.token_ttl_seconds,
    }
    return jwt.encode(payload, _secret(settings), algorithm="HS256")


def verify_token_claims(settings: Settings, token: str) -> dict:
    """校验 JWT 并返回 payload（含 sub/role/uid）；失败抛 AppError(UNAUTHORIZED)（懒导入避免循环依赖）。"""
    from src.webrag.main import AppError

    try:
        payload = jwt.decode(token, _secret(settings), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AppError("UNAUTHORIZED", "登录已过期，请重新登录", 401) from None
    except jwt.InvalidTokenError:
        raise AppError("UNAUTHORIZED", "无效的登录凭证", 401) from None
    if not payload.get("sub"):
        raise AppError("UNAUTHORIZED", "无效的登录凭证", 401)
    return payload


def verify_token(settings: Settings, token: str) -> str:
    """校验 JWT 并返回 username；失败抛 AppError(UNAUTHORIZED)。"""
    return str(verify_token_claims(settings, token)["sub"])


def require_admin(authorization: str = Header(default="")) -> dict:
    """FastAPI 依赖：校验 `Authorization: Bearer <token>` 且 role=admin，通过返回 claims（含 username/role/uid）。

    用法：`_admin: dict = Depends(auth.require_admin)`。
    普通用户（role != admin）→ 403 FORBIDDEN（后台接口仅管理员可访问）。
    """
    from src.webrag.config import load_settings
    from src.webrag.main import AppError

    if not authorization.startswith("Bearer "):
        raise AppError("UNAUTHORIZED", "缺少登录凭证，请先登录", 401)
    claims = verify_token_claims(load_settings(), authorization[7:].strip())
    if claims.get("role") != "admin":
        raise AppError("FORBIDDEN", "无权限访问管理后台（仅管理员可访问）", 403)
    return claims


# ---- 登录失败限流（Redis，best-effort） ----


def check_login_allowed(settings: Settings, username: str) -> bool:
    """该用户名是否仍允许尝试登录（失败次数 < 上限）。Redis 不可用 → 放行。"""
    redis = _get_redis(settings)
    if redis is None:
        return True
    try:
        key = f"admin:login:fail:{username}"
        return int(redis.get(key) or 0) < _MAX_FAILS
    except Exception:
        return True


def record_login_failure(settings: Settings, username: str) -> int:
    """记录一次失败；返回累计失败次数。Redis 不可用 → 返回 0（不生效）。"""
    redis = _get_redis(settings)
    if redis is None:
        return 0
    try:
        key = f"admin:login:fail:{username}"
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, _RATE_WINDOW_SECONDS)
        n, _ = pipe.execute()
        return int(n)
    except Exception:
        return 0


def clear_login_failures(settings: Settings, username: str) -> None:
    redis = _get_redis(settings)
    if redis is None:
        return
    try:
        redis.delete(f"admin:login:fail:{username}")
    except Exception:
        pass
