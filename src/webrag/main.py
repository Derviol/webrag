"""FastAPI 入口：/ask、/health + 前端静态托管（#8 交付后启用）。

负责人：#8 后端 API / 前端。当前为骨架：
- /health 已可用（真实检测 Milvus 连接）；
- /ask 待各模块实现后按架构文档 §4 在线链路组装。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.webrag.config import load_settings
from src.webrag.milvus_store import MilvusStore
from src.webrag.schemas import AskRequest, AskResponse

settings = load_settings()


class AppError(Exception):
    """统一错误信封，格式见 docs/api.md §1。"""

    def __init__(self, code: str, message: str, status: int = 500):
        self.code = code
        self.message = message
        self.status = status


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = MilvusStore(settings.milvus_uri)
    try:
        store.connect()
    except Exception:
        pass  # Milvus 未启动时服务仍可起，/health 会如实上报
    app.state.store = store
    yield


app = FastAPI(title="WebRAG", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.get("/health")
def health() -> dict:
    store: MilvusStore = app.state.store
    return {
        "status": "ok",
        "milvus": store.health(),
        "embed_model": False,  # TODO(#4)：模型加载后如实上报
    }


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    """在线链路（架构文档 §4）：查询分析 → 检索（知识库/临时抓取）→ 生成 → 引用校验。

    TODO(#8)：组装 retriever.analyze_query / retrieve 与 llm.generate / build_response；
    失败按错误码映射（SEARCH_FAILED / TIMEOUT / LLM_FAILED / EMPTY_RESULT）。
    """
    raise AppError("INTERNAL_ERROR", "问答链路尚未实现（代码骨架，等待各模块合入）")


# 前端静态页（#8 交付 static/ 目录后启用）
# app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.server.host, port=settings.server.port)
