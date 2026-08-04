"""命令行单测一条问题（#6 / #9 用）。

用法：
    python scripts/test_query.py "2025 年大模型行业有哪些重要进展？"
    python scripts/test_query.py --verbose "问题"   # 打印查询分析与检索明细
    python scripts/test_query.py --json "问题"      # 输出与 /ask 同构的 JSON

流程：retriever.analyze_query → retrieve（知识库 / 临时抓取）→ llm.generate → 引用解析
输出：answer + sources（引用格式与 docs/api.md §2 一致）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 从项目根导入 src

from src.webrag import llm, retriever
from src.webrag.config import load_settings


def main() -> None:
    ap = argparse.ArgumentParser(description="命令行单测一条问题")
    ap.add_argument("question", help="用户问题")
    ap.add_argument("--collection", default=None, help="知识库 collection（默认 settings.milvus_collection）")
    ap.add_argument("--verbose", action="store_true", help="打印查询分析与检索明细")
    ap.add_argument("--json", action="store_true", help="输出与 /ask 同构的 JSON")
    args = ap.parse_args()

    settings = load_settings()
    collection = args.collection or settings.milvus_collection

    try:
        plan = retriever.analyze_query(args.question)
        if args.verbose:
            print(f"[query] 分析结果：{plan}")

        results = retriever.retrieve(args.question, collection)
        contexts = [r.chunk for r in results]
        if args.verbose:
            print(f"[retriever] 召回 {len(contexts)} 条（top_k={settings.retriever.top_k}，重排={settings.retriever.enable_rerank}）")
            for i, r in enumerate(results, 1):
                print(f"[retriever]   [{i}] {r.chunk.metadata.title} score={r.score:.4f}")

        if not contexts:
            print(json.dumps({"error": {"code": "EMPTY_RESULT", "message": "检索无结果，无法作答"}}, ensure_ascii=False))
            sys.exit(1)

        client = llm.DeepSeekClient(settings.deepseek_api_key, model=settings.llm.model)
        answer = client.generate(args.question, contexts)
        resp = llm.build_response(answer, contexts)

        if args.json:
            print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
        else:
            print(f"\n==== 回答 ====\n{resp.answer}\n==== 来源 ====")
            for s in resp.sources:
                print(f"  [{s.index}] {s.title}  {s.url}")
    except NotImplementedError as exc:
        print(f"[test_query] 依赖模块尚未实现（{exc}），等待对应分支合入。")
        sys.exit(2)


if __name__ == "__main__":
    main()
