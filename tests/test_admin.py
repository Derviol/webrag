"""管理后台（/admin/*）接口测试：mock MySQL（FakeAdminDB）、Milvus 与 BGE-M3 嵌入，离线快速。

覆盖：登录成功/失败、鉴权 401、文本与文件入库（202→done）、参数校验 422、
入库失败落 failed、删除（先清 Milvus 块再删记录）、Milvus 不可用时保留记录、/health 形状不变。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.webrag.main as main_mod
from src.webrag.admin import auth
from src.webrag.admin import ingest as admin_ingest
from src.webrag.admin import routes as admin_routes
from src.webrag.admin.db import AdminDBError
from src.webrag.embedder import EmbedResult

# ── 替身：内存版 AdminDB（替代真实 MySQL） ──


class FakeAdminDB:
    """实现 AdminDB 全部被调用方法；表用 dict 模拟。"""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.docs: dict[int, dict] = {}
        self.convs: dict[int, dict] = {}
        self._next_id = 1

    def ensure_schema(self) -> None:
        pass

    def create_user(self, username: str, password_hash: str, role: str = "user") -> int:
        if username in self.users:
            raise AdminDBError(f"用户名 {username} 已存在")
        uid = "uid_" + str(len(self.users) + 1)
        self.users[username] = {
            "id": len(self.users) + 1, "uid": uid, "username": username,
            "password_hash": password_hash, "role": role,
        }
        return self.users[username]["id"]

    def get_user_by_username(self, username: str) -> dict | None:
        return self.users.get(username)

    def get_user_by_uid(self, uid: str) -> dict | None:
        for u in self.users.values():
            if u["uid"] == uid:
                return u
        return None

    def update_user_password(self, username: str, password_hash: str) -> bool:
        if username not in self.users:
            return False
        self.users[username]["password_hash"] = password_hash
        return True

    def count_users(self) -> int:
        return len(self.users)

    def create_conversation(self, uid: str, title: str, messages: list) -> int:
        conv_id = self._next_id
        self._next_id += 1
        self.convs[conv_id] = {
            "id": conv_id, "uid": uid, "title": title or "新对话",
            "messages": list(messages),
            "created_at": "2026-08-08T10:00:00", "updated_at": "2026-08-08T10:00:00",
        }
        return conv_id

    def list_conversations(self, uid: str) -> list[dict]:
        rows = [
            {"id": c["id"], "title": c["title"], "created_at": c["created_at"], "updated_at": c["updated_at"]}
            for c in self.convs.values() if c["uid"] == uid
        ]
        return sorted(rows, key=lambda r: r["updated_at"], reverse=True)

    def get_conversation(self, conv_id: int, uid: str) -> dict | None:
        c = self.convs.get(conv_id)
        if not c or c["uid"] != uid:
            return None
        return dict(c)

    def save_conversation(self, conv_id: int, uid: str, title: str, messages: list) -> bool:
        c = self.convs.get(conv_id)
        if not c or c["uid"] != uid:
            return False
        c["title"] = title or "新对话"
        c["messages"] = list(messages)
        c["updated_at"] = "2026-08-08T11:00:00"
        return True

    def delete_conversation(self, conv_id: int, uid: str) -> bool:
        c = self.convs.get(conv_id)
        if not c or c["uid"] != uid:
            return False
        return self.convs.pop(conv_id, None) is not None

    def create_document(self, *, doc_ref, title, source_type, file_name, content) -> int:
        doc_id = self._next_id
        self._next_id += 1
        self.docs[doc_id] = {
            "id": doc_id, "doc_ref": doc_ref, "title": title, "source_type": source_type,
            "file_name": file_name, "status": "processing", "error_message": "",
            "chunk_count": 0, "char_count": len(content), "content": content,
            "created_at": "2026-08-06 10:00:00", "updated_at": "2026-08-06 10:00:00",
        }
        return doc_id

    def update_document_status(self, doc_id, status, error_message="", chunk_count=0) -> None:
        if doc_id in self.docs:
            self.docs[doc_id].update(status=status, error_message=error_message, chunk_count=chunk_count)

    def get_document(self, doc_id) -> dict | None:
        return self.docs.get(doc_id)

    def list_documents(self, limit=100, offset=0):
        rows = sorted(self.docs.values(), key=lambda d: d["id"], reverse=True)
        return rows[offset : offset + limit]

    def count_documents(self) -> int:
        return len(self.docs)

    def delete_document(self, doc_id) -> bool:
        return self.docs.pop(doc_id, None) is not None


# ── 替身：嵌入器 与 MilvusStore（记录调用 + 故障开关） ──


class FakeEmbedder:
    def embed(self, texts):
        dim = 1024
        return EmbedResult(dense=[[0.01] * dim for _ in texts], sparse=[{} for _ in texts])


class FakeMilvusStore:
    def __init__(self, uri):
        self.uri = uri
        self.added: list[tuple[str, str, int]] = []  # (collection, doc_ref, chunk_count)
        self.deleted: list[tuple[str, str]] = []
        self.fail_add = False
        self.fail_delete = False

    def connect(self):
        pass

    def ensure_offline_collection(self, name):
        return None

    def add_offline(self, collection, chunks, vectors, doc_ref):
        if self.fail_add:
            raise RuntimeError("模拟 Milvus 写入失败")
        self.added.append((collection, doc_ref, len(chunks)))
        holder.counts[doc_ref] = len(chunks)  # 跨实例共享（模拟 Milvus 持久化）
        return len(chunks)

    def delete_by_doc_ref(self, collection, doc_ref):
        if self.fail_delete:
            raise RuntimeError("模拟 Milvus 不可用")
        self.deleted.append((collection, doc_ref))
        return holder.counts.pop(doc_ref, 0)  # 与真实实现一致：返回删除前块数


class StoreHolder:
    """捕获每次 fake MilvusStore 实例，供测试断言/触发故障；counts 模拟持久化。"""

    def __init__(self):
        self.store: FakeMilvusStore | None = None
        self.fail_add = False
        self.fail_delete = False
        self.counts: dict[str, int] = {}  # doc_ref → 块数（跨实例共享）


def _fake_milvus_factory(uri):
    holder.store = FakeMilvusStore(uri)
    holder.store.fail_add = holder.fail_add
    holder.store.fail_delete = holder.fail_delete
    return holder.store


holder = StoreHolder()


def _sync_ingest(db, settings, **kwargs) -> None:
    """异步入库改为同步执行（测试内确定性断言状态）。"""
    admin_ingest._ingest_job(db, settings, **kwargs)


@pytest.fixture
def client(monkeypatch):
    # lifespan 免真实连接：Milvus / 模型 / MySQL 全部替换
    monkeypatch.setattr(main_mod.MilvusStore, "connect", lambda self: None)
    monkeypatch.setattr(main_mod.MilvusStore, "health", lambda self: False)
    monkeypatch.setattr(main_mod.retriever, "get_embedder", lambda: None)
    monkeypatch.setattr(main_mod.retriever, "_get_reranker", lambda: None)
    fake_db = FakeAdminDB()
    monkeypatch.setattr(main_mod, "AdminDB", lambda settings: fake_db)
    # 入库链路：假嵌入 + 假 Milvus（ingest 与 routes 两处类引用都替换）+ 同步执行
    holder.store = None
    holder.fail_add = False
    holder.fail_delete = False
    holder.counts = {}
    monkeypatch.setattr(admin_ingest, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(admin_ingest, "MilvusStore", _fake_milvus_factory)
    monkeypatch.setattr(admin_routes, "MilvusStore", _fake_milvus_factory)
    monkeypatch.setattr(admin_routes.ingest, "ingest_document_async", _sync_ingest)
    # 登录限流依赖 Redis → 测试中禁用（Redis 不可用时本来就是放行的）
    monkeypatch.setattr(auth, "_get_redis", lambda settings: None)

    with TestClient(main_mod.app) as c:
        yield c, fake_db


def _mk_admin(fake_db, username="admin", password="secret123") -> None:
    fake_db.create_user(username, auth.hash_password(password), role="admin")


def _login_token(c, username="admin", password="secret123") -> str:
    resp = c.post("/admin/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ── 登录 ──


def test_login_success_and_token_usable(client):
    c, fake_db = client
    _mk_admin(fake_db)
    resp = c.post("/admin/auth/login", json={"username": "admin", "password": "secret123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert body["username"] == "admin"
    assert body["expires_in"] > 0
    # token 可直接用于管理接口
    resp2 = c.get("/admin/documents", headers={"Authorization": f"Bearer {body['token']}"})
    assert resp2.status_code == 200


def test_login_wrong_password(client):
    c, fake_db = client
    _mk_admin(fake_db)
    resp = c.post("/admin/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_login_unknown_user(client):
    c, _ = client
    resp = c.post("/admin/auth/login", json={"username": "nobody", "password": "x"})
    assert resp.status_code == 401


def test_login_empty_fields_422(client):
    c, _ = client
    assert c.post("/admin/auth/login", json={"username": "", "password": ""}).status_code == 422


# ── 鉴权 ──


def test_admin_apis_require_token(client):
    c, _ = client
    assert c.get("/admin/documents").status_code == 401
    assert c.get("/admin/documents/1").status_code == 401
    assert c.delete("/admin/documents/1").status_code == 401
    assert c.post("/admin/documents", json={"content": "x"}).status_code == 401


def test_invalid_token_401(client):
    c, _ = client
    resp = c.get("/admin/documents", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


# ── 文本入库 ──


def test_create_text_document_done(client):
    c, fake_db = client
    _mk_admin(fake_db)
    h = {"Authorization": f"Bearer {_login_token(c)}"}

    resp = c.post("/admin/documents", json={"title": "BGE-M3 说明", "content": "BGE-M3 支持 dense+sparse 双向量。" * 20}, headers=h)
    assert resp.status_code == 202
    doc_id = resp.json()["id"]

    detail = c.get(f"/admin/documents/{doc_id}", headers=h).json()["document"]
    assert detail["status"] == "done"
    assert detail["chunk_count"] > 0
    assert detail["char_count"] > 0
    assert detail["title"] == "BGE-M3 说明"

    # 离线库确实收到写入（doc_ref 唯一，块数与记录一致）
    assert holder.store is not None
    assert holder.store.added
    collection, doc_ref, n = holder.store.added[-1]
    assert collection == "webrag_offline_kb"
    assert n == detail["chunk_count"]
    assert doc_ref.startswith("offline_")


def test_create_text_empty_content_422(client):
    c, fake_db = client
    _mk_admin(fake_db)
    h = {"Authorization": f"Bearer {_login_token(c)}"}
    resp = c.post("/admin/documents", json={"content": "   "}, headers=h)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_text_over_char_limit_422(client, monkeypatch):
    c, fake_db = client
    _mk_admin(fake_db)
    h = {"Authorization": f"Bearer {_login_token(c)}"}
    # 调小字符上限（共享 settings 实例，monkeypatch 自动还原）
    monkeypatch.setattr(main_mod.settings.admin, "max_chars_per_doc", 20)
    resp = c.post("/admin/documents", json={"content": "x" * 21}, headers=h)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ── 文件入库 ──


def test_upload_markdown_done(client):
    c, fake_db = client
    _mk_admin(fake_db)
    h = {"Authorization": f"Bearer {_login_token(c)}"}

    resp = c.post(
        "/admin/documents/upload",
        files={"file": ("部署手册.md", "# 部署\n\n1. docker compose up -d\n2. 验收 health".encode(), "text/markdown")},
        data={"title": "部署手册"},
        headers=h,
    )
    assert resp.status_code == 202
    doc_id = resp.json()["id"]
    detail = c.get(f"/admin/documents/{doc_id}", headers=h).json()["document"]
    assert detail["status"] == "done"
    assert detail["source_type"] == "md"
    assert detail["file_name"] == "部署手册.md"


def test_upload_html_uses_parser(client):
    """HTML 走 parser（trafilatura）清洗：脚本剔除、标签去除、正文提取。

    （trafilatura 对短 <nav> 文本有保留局限，见 parser 模块说明——只断言确定行为。）
    """
    c, fake_db = client
    _mk_admin(fake_db)
    h = {"Authorization": f"Bearer {_login_token(c)}"}

    html = (
        "<html><head><title>测试页面</title></head><body>"
        "<nav>导航垃圾</nav><script>var x = 1;</script>"
        "<p>这是正文第一段，包含有效知识内容。</p><p>这是正文第二段。</p>"
        "</body></html>"
    ).encode()
    resp = c.post("/admin/documents/upload", files={"file": ("page.html", html, "text/html")}, headers=h)
    assert resp.status_code == 202
    detail = c.get(f"/admin/documents/{resp.json()['id']}", headers=h).json()["document"]
    assert detail["status"] == "done"
    assert "这是正文第一段" in detail["content"]
    assert "这是正文第二段" in detail["content"]
    assert "var x = 1" not in detail["content"]  # <script> 内容剔除
    assert "<script>" not in detail["content"]  # 标签去除（存的是纯文本）


def test_upload_file_too_big_422(client, monkeypatch):
    c, fake_db = client
    _mk_admin(fake_db)
    h = {"Authorization": f"Bearer {_login_token(c)}"}
    monkeypatch.setattr(main_mod.settings.admin, "max_file_bytes", 100)
    resp = c.post("/admin/documents/upload", files={"file": ("big.txt", b"x" * 200, "text/plain")}, headers=h)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ── 入库失败 → failed 状态 ──


def test_ingest_failure_records_failed(client):
    c, fake_db = client
    _mk_admin(fake_db)
    h = {"Authorization": f"Bearer {_login_token(c)}"}

    holder.fail_add = True
    resp = c.post("/admin/documents", json={"content": "入库会失败的文档内容。"}, headers=h)
    assert resp.status_code == 202
    detail = c.get(f"/admin/documents/{resp.json()['id']}", headers=h).json()["document"]
    assert detail["status"] == "failed"
    assert "模拟" in detail["error_message"]


# ── 列表 / 删除 ──


def test_list_and_delete_document(client):
    c, fake_db = client
    _mk_admin(fake_db)
    h = {"Authorization": f"Bearer {_login_token(c)}"}

    c.post("/admin/documents", json={"title": "A", "content": "文档 A 内容"}, headers=h)
    c.post("/admin/documents", json={"title": "B", "content": "文档 B 内容"}, headers=h)

    lst = c.get("/admin/documents", headers=h).json()
    assert lst["total"] == 2
    assert [d["title"] for d in lst["documents"]] == ["B", "A"]  # 倒序

    doc_id = lst["documents"][0]["id"]
    del_resp = c.delete(f"/admin/documents/{doc_id}", headers=h)
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted_chunks"] > 0
    # Milvus 侧收到删除
    assert holder.store.deleted and holder.store.deleted[-1][0] == "webrag_offline_kb"
    assert c.get("/admin/documents", headers=h).json()["total"] == 1
    # 删除后再查 → 404
    assert c.get(f"/admin/documents/{doc_id}", headers=h).status_code == 404
    assert c.delete(f"/admin/documents/{doc_id}", headers=h).status_code == 404


def test_delete_when_milvus_down_keeps_record(client):
    """Milvus 不可用时不删 MySQL 记录（防孤儿块），返回统一错误信封（503）。"""
    c, fake_db = client
    _mk_admin(fake_db)
    h = {"Authorization": f"Bearer {_login_token(c)}"}

    created = c.post("/admin/documents", json={"content": "待删除的文档"}, headers=h).json()
    doc_id = created["id"]
    holder.fail_delete = True
    resp = c.delete(f"/admin/documents/{doc_id}", headers=h)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
    # 记录仍在
    assert c.get(f"/admin/documents/{doc_id}", headers=h).status_code == 200


# ── 与现有功能隔离：/health 形状不变 ──


def test_health_shape_unchanged(client):
    c, _ = client
    body = c.get("/health").json()
    assert set(body) == {"status", "milvus", "embed_model", "embed_model_loaded", "llm_temperature", "web_top_n"}


# ── 账户系统（/auth/*）：注册 / 登录 / 会话校验 / 权限 ──


def test_register_login_me(client):
    c, _ = client
    resp = c.post("/auth/register", json={"username": "alice", "password": "pass123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "user"
    assert body["uid"] and body["token"]
    h = {"Authorization": "Bearer " + body["token"]}

    me = c.get("/auth/me", headers=h)
    assert me.status_code == 200
    assert me.json()["username"] == "alice"
    assert me.json()["role"] == "user"

    # 用户名重复 → 409
    dup = c.post("/auth/register", json={"username": "alice", "password": "pass456"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "USER_EXISTS"

    # 登录成功 / 密码错误 401 / 短密码注册 422
    assert c.post("/auth/login", json={"username": "alice", "password": "pass123"}).status_code == 200
    assert c.post("/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert c.post("/auth/register", json={"username": "bob", "password": "123"}).status_code == 422


def test_ask_requires_login(client):
    """/ask 与 /ask/stream 未登录 → 401（登录后才可以提问）。"""
    c, _ = client
    assert c.post("/ask", json={"question": "hi"}).status_code == 401
    assert c.post("/ask/stream", json={"question": "hi"}).status_code == 401


def test_normal_user_blocked_from_admin(client):
    """普通用户直访后台：/admin/* API 403 拦截，/admin/auth/me 403。"""
    c, _ = client
    token = c.post("/auth/register", json={"username": "bob", "password": "pass123"}).json()["token"]
    h = {"Authorization": "Bearer " + token}
    assert c.get("/admin/documents", headers=h).status_code == 403
    assert c.get("/admin/auth/me", headers=h).status_code == 403
    # 普通用户用后台登录接口 → 403
    assert c.post("/admin/auth/login", json={"username": "bob", "password": "pass123"}).status_code == 403


def test_admin_login_via_unified_account(client):
    """管理员：/admin/auth/login 与 /auth/login 均可登录，/admin/auth/me 返回 admin 角色。"""
    c, fake_db = client
    _mk_admin(fake_db)
    token = _login_token(c)
    me = c.get("/admin/auth/me", headers={"Authorization": "Bearer " + token})
    assert me.status_code == 200
    assert me.json()["role"] == "admin"
    resp = c.post("/auth/login", json={"username": "admin", "password": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


# ── 聊天记录（/chat/*）：CRUD + uid 归属 ──


def _login_user(c, username, password="pass123"):
    r = c.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_chat_conversations_crud_and_ownership(client):
    """会话创建/列表/保存/详情/删除，且他人会话一律 404（归属隔离）。"""
    c, _ = client
    h1 = _login_user(c, "alice")
    h2 = _login_user(c, "bob")

    assert c.get("/chat/conversations", headers=h1).json()["conversations"] == []

    # 创建（首问即建）
    resp = c.post(
        "/chat/conversations",
        json={"title": "第一个问题", "messages": [{"role": "user", "content": "什么是RAG？"}]},
        headers=h1,
    )
    assert resp.status_code == 201
    conv = resp.json()["conversation"]
    assert conv["title"] == "第一个问题"
    assert conv["uid"] is not None and len(conv["messages"]) == 1
    conv_id = conv["id"]

    # 列表（不含 messages）
    lst = c.get("/chat/conversations", headers=h1).json()["conversations"]
    assert len(lst) == 1 and lst[0]["id"] == conv_id and "messages" not in lst[0]

    # 保存（回答完成后 PUT 整段）
    msgs = [
        {"role": "user", "content": "什么是RAG？"},
        {"role": "assistant", "content": "RAG 是检索增强生成[1]。", "sources": []},
    ]
    assert c.put(f"/chat/conversations/{conv_id}", json={"title": "RAG 问答", "messages": msgs}, headers=h1).status_code == 200
    detail = c.get(f"/chat/conversations/{conv_id}", headers=h1).json()["conversation"]
    assert detail["title"] == "RAG 问答"
    assert len(detail["messages"]) == 2

    # 归属：bob 看不到 / 改不了 / 删不了 alice 的会话
    assert c.get(f"/chat/conversations/{conv_id}", headers=h2).status_code == 404
    assert c.put(f"/chat/conversations/{conv_id}", json={"title": "hack", "messages": []}, headers=h2).status_code == 404
    assert c.delete(f"/chat/conversations/{conv_id}", headers=h2).status_code == 404

    # 删除（同步删库）
    assert c.delete(f"/chat/conversations/{conv_id}", headers=h1).status_code == 200
    assert c.get("/chat/conversations", headers=h1).json()["conversations"] == []
    assert c.get(f"/chat/conversations/{conv_id}", headers=h1).status_code == 404


def test_chat_requires_login(client):
    c, _ = client
    assert c.get("/chat/conversations").status_code == 401
    assert c.post("/chat/conversations", json={"messages": []}).status_code == 401
