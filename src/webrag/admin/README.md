# admin — 管理后台（离线知识入库）

## 职责

- 账户登录（账号密码 + PBKDF2 + JWT，统一账户存 **MySQL** `webrag_admin.users`，role=admin；
  历史 `admin_users` 首次启动自动迁移为 users，role='admin'；普通用户由前端「账户」模块注册）＋
  **角色校验**：/admin/* 仅 role=admin 可访问（普通用户 403，前端直访由 GET /admin/auth/me 判断用户组）；
- 离线知识文档入库：上传文件（.txt / .md / .html）或粘贴文本 → 复用项目既有
  **parser → chunker → embedder** 管线解析 → 写入独立 Milvus collection
  `webrag_offline_kb`（标准 KB schema + `doc_ref` 字段，见 milvus_store 的
  `build_offline_schema`），文档记录与原文备份存 MySQL `admin_documents`；
- 入库为后台异步任务（status: processing → done / failed），前端轮询详情；
- 删除文档：先按 doc_ref 批量清除离线库知识块，再删记录。

## 所属角色

- 管理后台（离线知识入库）——独立模块，不依赖其他模块的内部实现。

## 接口约定（HTTP，见 docs/api.md §1.4）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /admin/auth/login | 登录（Bearer JWT；Redis 限流 15min/5 次失败） |
| POST | /admin/documents | JSON {title?, content} 粘贴文本入库（202 立即返回） |
| POST | /admin/documents/upload | multipart {file, title?} 文件入库（.txt/.md/.html） |
| GET | /admin/documents | 列表（倒序，不含 content；limit/offset 分页） |
| GET | /admin/documents/{id} | 详情（含 content 原文；入库中轮询此接口看状态） |
| DELETE | /admin/documents/{id} | 删除（先清 Milvus 块，再删 MySQL 记录） |

> 除 login 外均需 `Authorization: Bearer <token>`；错误信封复用 api.md §1.1，
> 新增错误码 UNAUTHORIZED（401）/ NOT_FOUND（404）/ TOO_MANY_ATTEMPTS（429）。

## 模块结构

- `db.py`：MySQL DAO（每操作独立短连接 + 快速失败；自动建表幂等）；
- `auth.py`：PBKDF2 密码哈希（stdlib）+ PyJWT 签发/校验 + Redis 登录限流（best-effort）；
- `ingest.py`：解析→切块→嵌入→入库（后台线程；BGE-M3 与 /ask 共享单实例）；
- `routes.py`：FastAPI 路由（`admin_router`，main.py 挂载）。

## 配置

- 连接信息（.env）：`MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`、`ADMIN_JWT_SECRET`、
  `MILVUS_OFFLINE_COLLECTION`；
- 可调参数（config/settings.yaml `admin:` 段）：token 有效期、上传大小上限、单文档字符上限；
- 切块参数复用 `chunker:` 段（与问答链路粒度一致）。

## 首次使用

```bash
# 1. 启动（MySQL 随 compose 一起起）
docker compose up -d
# 2. 创建管理员账号
docker compose exec webrag-app uv run --no-sync python scripts/init_admin.py \
  --username admin --password <你的密码>
# 3. 浏览器打开 http://localhost:8000/admin/ 登录并入库
```

## 验收标准

- [ ] 登录：错误密码 401、连续 5 次失败锁定 15 分钟（Redis 起时）；
- [ ] 无 token / 过期 token 访问管理接口 → 401 统一错误信封；
- [ ] 文本与 .txt/.md/.html 文件入库：状态 processing → done，chunk_count 正确；
- [ ] 超大文件 / 空内容 / 超字符上限 → 422 VALIDATION_ERROR；
- [ ] 删除文档后离线库无残留（Milvus delete_by_doc_ref 生效）；
- [ ] MySQL 未启动时：/ask 不受影响，管理接口返回 503 信封。
