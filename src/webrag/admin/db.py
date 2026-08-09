"""管理后台 MySQL 存取：统一账户（users）+ 聊天记录（chat_conversations）+ 文档记录。

- 每操作独立连接（pymysql，connect_timeout=2 快速失败），天然线程安全；
- `ensure_schema()` 幂等建表（admin_users 历史遗留 / admin_documents / users / chat_conversations），
  服务启动与每次操作前调用；users 表首次出现时自动把旧 admin_users 数据迁移为 role='admin' 账户；
- 连接失败抛 `AdminDBError`，由路由层映射为统一错误信封，绝不静默吞掉导致数据丢失；
- 所有文本列 utf8mb4（与 compose mysql 服务 charset 对齐）；messages 用 MySQL JSON 列。

负责人：管理后台（离线知识入库）。
"""

from __future__ import annotations

import json
import secrets
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

# 表结构 DDL：CREATE TABLE IF NOT EXISTS 幂等，启动/操作前反复执行安全
_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS admin_users (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      username VARCHAR(64) NOT NULL,
      password_hash VARCHAR(512) NOT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uk_username (username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_documents (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      doc_ref VARCHAR(64) NOT NULL,
      title VARCHAR(512) NOT NULL,
      source_type VARCHAR(16) NOT NULL,             -- text / md / html
      file_name VARCHAR(512) NOT NULL DEFAULT '',
      status VARCHAR(16) NOT NULL DEFAULT 'processing',  -- processing / done / failed
      error_message VARCHAR(1024) NOT NULL DEFAULT '',
      chunk_count INT NOT NULL DEFAULT 0,
      char_count INT NOT NULL DEFAULT 0,
      content LONGTEXT NOT NULL,                    -- 原文备份（审计 / 失败重试）
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_doc_ref (doc_ref),
      KEY idx_status (status),
      KEY idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      uid VARCHAR(32) NOT NULL,                          -- 会话归属标识（token 内携带）
      username VARCHAR(64) NOT NULL,
      password_hash VARCHAR(512) NOT NULL,
      role VARCHAR(16) NOT NULL DEFAULT 'user',          -- user / admin（admin 可进后台）
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uk_username (username),
      UNIQUE KEY uk_uid (uid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_conversations (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      uid VARCHAR(32) NOT NULL,                          -- 归属用户（users.uid）
      title VARCHAR(200) NOT NULL DEFAULT '新对话',
      messages JSON NOT NULL,                            -- 整段会话消息（前端扁平模型，JSON 序列化）
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      KEY idx_uid_updated (uid, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


class AdminDBError(Exception):
    """管理库（MySQL）不可用或操作失败。"""


class AdminDB:
    """管理后台数据访问层。每个方法独立短连接（本地 MySQL 连接开销 <5ms）。"""

    def __init__(self, settings):
        self._host = settings.mysql_host
        self._port = settings.mysql_port
        self._user = settings.mysql_user
        self._password = settings.mysql_password
        self._database = settings.mysql_database

    # ---- 连接 ----

    @contextmanager
    def _conn(self):
        try:
            conn = pymysql.connect(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                database=self._database,
                charset="utf8mb4",
                connect_timeout=2,  # 快速失败：MySQL 未起时不阻塞服务（约 2s 内放弃）
                cursorclass=DictCursor,
                autocommit=True,
            )
        except Exception as exc:
            raise AdminDBError(f"MySQL 连接失败（{self._host}:{self._port}/{self._database}）：{exc}") from exc
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _execute(self, sql: str, params: tuple = ()) -> tuple[int, int]:
        """执行写语句，返回 (lastrowid, rowcount)。"""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid, cur.rowcount

    def _fetch(self, sql: str, params: tuple = (), one: bool = False):
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() if one else cur.fetchall()

    # ---- schema ----

    def ensure_schema(self) -> None:
        """幂等建表 + 旧管理员迁移。失败抛 AdminDBError（调用方降级处理）。"""
        try:
            with self._conn() as conn, conn.cursor() as cur:
                for ddl in _SCHEMA_SQL:
                    cur.execute(ddl)
                self._migrate_legacy_admins(conn, cur)
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"管理库建表失败：{exc}") from exc

    @staticmethod
    def _migrate_legacy_admins(conn, cur) -> None:
        """历史 admin_users（仅管理员）→ users 表（role='admin'）一次性幂等迁移。

        users 表引入统一账户（普通用户 + 管理员同表，role 区分）；admin_users 仅历史部署存在。
        INSERT IGNORE：用户名已存在的行跳过（幂等，重复执行安全），uid 用 legacy_<id> 保证唯一。
        """
        cur.execute(
            "SELECT COUNT(*) AS n FROM information_schema.tables"
            " WHERE table_schema = DATABASE() AND table_name = 'admin_users'"
        )
        row = cur.fetchone()
        if not row or not row.get("n"):
            return
        cur.execute(
            "INSERT IGNORE INTO users (uid, username, password_hash, role, created_at)"
            " SELECT CONCAT('legacy_', id), username, password_hash, 'admin', created_at FROM admin_users"
        )

    # ---- 账户（统一 users 表：普通用户 + 管理员，role 区分） ----

    def create_user(self, username: str, password_hash: str, role: str = "user") -> int:
        """创建账户（默认普通用户；建管理员传 role='admin'）；用户名重复抛 AdminDBError。返回新用户 id。"""
        uid = secrets.token_hex(8)  # 16 位十六进制 uid，会话归属标识（token 内携带）
        try:
            last_id, _ = self._execute(
                "INSERT INTO users (uid, username, password_hash, role) VALUES (%s, %s, %s, %s)",
                (uid, username, password_hash, role),
            )
            return last_id
        except pymysql.err.IntegrityError as exc:
            raise AdminDBError(f"用户名 {username} 已存在") from exc
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"创建账户失败：{exc}") from exc

    def get_user_by_username(self, username: str) -> dict | None:
        try:
            return self._fetch(
                "SELECT id, uid, username, password_hash, role, created_at FROM users WHERE username = %s",
                (username,),
                one=True,
            )
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"查询账户失败：{exc}") from exc

    def get_user_by_uid(self, uid: str) -> dict | None:
        try:
            return self._fetch(
                "SELECT id, uid, username, password_hash, role, created_at FROM users WHERE uid = %s",
                (uid,),
                one=True,
            )
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"查询账户失败：{exc}") from exc

    def update_user_password(self, username: str, password_hash: str) -> bool:
        """重置账户密码（init_admin --force 用）；返回是否真的更新了一行。"""
        try:
            _, rowcount = self._execute(
                "UPDATE users SET password_hash = %s WHERE username = %s",
                (password_hash, username),
            )
            return rowcount > 0
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"重置账户密码失败：{exc}") from exc

    def count_users(self) -> int:
        try:
            row = self._fetch("SELECT COUNT(*) AS n FROM users", one=True)
            return int(row["n"]) if row else 0
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"统计账户失败：{exc}") from exc

    # ---- 聊天记录（chat_conversations，按 uid 归属） ----

    def create_conversation(self, uid: str, title: str, messages: list) -> int:
        """创建会话（messages 序列化为 JSON 列）；返回会话 id。"""
        try:
            last_id, _ = self._execute(
                "INSERT INTO chat_conversations (uid, title, messages) VALUES (%s, %s, %s)",
                (uid, (title or "新对话")[:200], json.dumps(messages, ensure_ascii=False)),
            )
            return int(last_id)
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"创建会话失败：{exc}") from exc

    def list_conversations(self, uid: str) -> list[dict]:
        """会话列表（不含 messages 大字段，按更新时间倒序）。"""
        try:
            rows = self._fetch(
                "SELECT id, title, created_at, updated_at FROM chat_conversations"
                " WHERE uid = %s ORDER BY updated_at DESC, id DESC",
                (uid,),
            )
            for r in rows:
                r["created_at"] = self._fmt_dt(r.get("created_at"))
                r["updated_at"] = self._fmt_dt(r.get("updated_at"))
            return rows
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"查询会话列表失败：{exc}") from exc

    def get_conversation(self, conv_id: int, uid: str) -> dict | None:
        """按 id + uid 查会话（归属校验：他人会话返回 None）。"""
        try:
            row = self._fetch(
                "SELECT id, title, messages, created_at, updated_at FROM chat_conversations"
                " WHERE id = %s AND uid = %s",
                (conv_id, uid),
                one=True,
            )
            if row is None:
                return None
            row["messages"] = json.loads(row["messages"] or "[]")
            row["created_at"] = self._fmt_dt(row.get("created_at"))
            row["updated_at"] = self._fmt_dt(row.get("updated_at"))
            return row
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"查询会话失败：{exc}") from exc

    def save_conversation(self, conv_id: int, uid: str, title: str, messages: list) -> bool:
        """保存整段会话（标题 + 消息 + 更新时间）；归属不符返回 False（未更新）。"""
        try:
            _, rowcount = self._execute(
                "UPDATE chat_conversations SET title = %s, messages = %s, updated_at = CURRENT_TIMESTAMP"
                " WHERE id = %s AND uid = %s",
                ((title or "新对话")[:200], json.dumps(messages, ensure_ascii=False), conv_id, uid),
            )
            return rowcount > 0
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"保存会话失败：{exc}") from exc

    def delete_conversation(self, conv_id: int, uid: str) -> bool:
        """删除会话（归属校验）；返回是否真的删掉一行。"""
        try:
            _, rowcount = self._execute(
                "DELETE FROM chat_conversations WHERE id = %s AND uid = %s",
                (conv_id, uid),
            )
            return rowcount > 0
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"删除会话失败：{exc}") from exc

    @staticmethod
    def _fmt_dt(value) -> str | None:
        """MySQL DATETIME → ISO 8601 字符串（前端 new Date 可直接解析）。"""
        if not value:
            return None
        return str(value).replace(" ", "T")

    # ---- 文档记录 ----

    def create_document(
        self, *, doc_ref: str, title: str, source_type: str, file_name: str, content: str
    ) -> int:
        """插入文档记录（status=processing），返回文档 id。char_count = 清洗后正文长度。"""
        try:
            last_id, _ = self._execute(
                "INSERT INTO admin_documents (doc_ref, title, source_type, file_name, status, char_count, content)"
                " VALUES (%s, %s, %s, %s, 'processing', %s, %s)",
                (doc_ref, title[:512], source_type, file_name[:512], len(content), content),
            )
            return int(last_id)
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"创建文档记录失败：{exc}") from exc

    def update_document_status(
        self, doc_id: int, status: str, error_message: str = "", chunk_count: int = 0
    ) -> None:
        """入库线程回写状态：done（chunk_count）或 failed（error_message）。"""
        try:
            self._execute(
                "UPDATE admin_documents SET status = %s, error_message = %s, chunk_count = %s"
                " WHERE id = %s",
                (status, (error_message or "")[:1024], int(chunk_count), doc_id),
            )
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"更新文档状态失败：{exc}") from exc

    def get_document(self, doc_id: int) -> dict | None:
        try:
            return self._fetch(
                "SELECT id, doc_ref, title, source_type, file_name, status, error_message,"
                " chunk_count, char_count, content, created_at, updated_at"
                " FROM admin_documents WHERE id = %s",
                (doc_id,),
                one=True,
            )
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"查询文档失败：{exc}") from exc

    def list_documents(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """文档列表（不含 content 大字段，按创建时间倒序）。"""
        try:
            return self._fetch(
                "SELECT id, title, source_type, file_name, status, error_message,"
                " chunk_count, char_count, created_at, updated_at"
                " FROM admin_documents ORDER BY id DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"查询文档列表失败：{exc}") from exc

    def count_documents(self) -> int:
        try:
            row = self._fetch("SELECT COUNT(*) AS n FROM admin_documents", one=True)
            return int(row["n"]) if row else 0
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"统计文档数失败：{exc}") from exc

    def delete_document(self, doc_id: int) -> bool:
        """删除文档记录；返回是否真的删掉一行（入库线程并发回写时可能已无行）。"""
        try:
            _, rowcount = self._execute("DELETE FROM admin_documents WHERE id = %s", (doc_id,))
            return rowcount > 0
        except AdminDBError:
            raise
        except Exception as exc:
            raise AdminDBError(f"删除文档记录失败：{exc}") from exc
