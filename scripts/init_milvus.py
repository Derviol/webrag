"""初始化 Milvus：创建知识库 collection（dense+sparse）、索引并加载。

用法：
    python scripts/init_milvus.py     # 读 .env 的 MILVUS_URI / MILVUS_COLLECTION

临时 collection（qa_<id>）不在此创建：问答时由 milvus_store 动态创建、用后即清。
负责人：#5 向量库开发；schema 唯一权威在 src/webrag/milvus_store/schema.py。
"""

import sys
from pathlib import Path

# 保证从项目根导入 src（直接运行 scripts/xxx.py 时 cwd 不在 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymilvus import utility

from src.webrag.config import load_settings
from src.webrag.milvus_store import MilvusStore
from src.webrag.milvus_store.schema import DENSE_DIM, build_schema


def main() -> None:
    settings = load_settings()
    collection = settings.milvus_collection

    store = MilvusStore(settings.milvus_uri)
    store.connect()
    print(f"[milvus] connected: {settings.milvus_uri}")

    if utility.has_collection(collection):
        print(f"[milvus] collection '{collection}' 已存在，跳过创建")
    else:
        store.create_collection(collection)
        print(f"[milvus] collection '{collection}' 创建完成（dense dim={DENSE_DIM} + sparse）")
        print(f"[milvus] schema 字段: id/text/url/title/publish_time/seq/dense_vec/sparse_vec")

    print("[milvus] 初始化完成。启动服务后可访问 GET /health 确认连接状态")


if __name__ == "__main__":
    main()
