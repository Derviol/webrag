"""管理后台（离线知识入库）：登录 + 文档上传/解析入库 + 独立离线知识库。

隔离设计（docs/api.md §1.4）：
- 路由前缀 `/admin`，除登录外均需 JWT（Authorization: Bearer <token>）；
- **MySQL**（webrag_admin 库）只存管理员账号与文档记录（原文备份 / 状态 / 块数）；
- **离线知识块**写入独立 Milvus collection `webrag_offline_kb`
  （标准 KB schema + doc_ref 字段），不触碰 /ask 问答链路的 webrag_qa / 临时库；
- 入库复用项目既有 parser / chunker / embedder 公共管线（见 ingest.py），
  保证解析入库的知识格式与项目要求一致；
- 入库为后台异步任务（processing → done / failed），CPU 嵌入不阻塞 /ask 请求。

对外入口：`admin_router`（main.py include 挂载）；数据层：`AdminDB`。
"""

from .db import AdminDB, AdminDBError
from .routes import admin_router

__all__ = ["AdminDB", "AdminDBError", "admin_router"]
