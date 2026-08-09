"""初始化 Milvus：创建问答缓存 collection（question → 摘要 + 来源）、索引并加载。

用法：
    python scripts/init_milvus.py     # 读 .env 的 MILVUS_URI / MILVUS_QA_COLLECTION

临时 collection（qa_<id>）不在此创建：联网问答时由 milvus_store 动态创建、用后即清。
遗留的预建知识库 collection（webrag_kb，旧三级级联方案）已不再被 /ask 使用：
如需清理可在 Attu 中删除，或执行脚本末尾提示的 drop 命令。
负责人：#5 向量库开发；schema 唯一权威在 src/webrag/milvus_store/schema.py。
"""

import sys
from pathlib import Path

# 保证从项目根导入 src（直接运行 scripts/xxx.py 时 cwd 不在 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymilvus import utility

from src.webrag.config import load_settings
from src.webrag.milvus_store import MilvusStore


def main() -> None:
    settings = load_settings()
    collection = settings.milvus_qa_collection

    store = MilvusStore(settings.milvus_uri)
    store.connect()
    print(f"[milvus] connected: {settings.milvus_uri}")

    if utility.has_collection(collection):
        print(f"[milvus] collection '{collection}' 已存在，跳过创建")
    else:
        store.create_qa_collection(collection)
        print(f"[milvus] collection '{collection}' 创建完成（question_vec dim=1024）")
        print("[milvus] schema 字段: id/question/summary/sources/created_at/question_vec")

    legacy = settings.milvus_collection  # webrag_kb：旧知识库，已废弃
    if utility.has_collection(legacy):
        print(f"[milvus] 提示：遗留知识库 '{legacy}' 仍存在但已不被 /ask 使用，可手动清理：")
        print(f"        python -c \"from pymilvus import utility; utility.drop_collection('{legacy}')\"")

    print("[milvus] 初始化完成。启动服务后可访问 GET /health 确认连接状态")


if __name__ == "__main__":
    main()
