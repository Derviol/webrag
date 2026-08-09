"""命令行单测一条问题（#6 / #9 用）。

用法：
    python scripts/test_query.py "2025 年大模型行业有哪些重要进展？"
    python scripts/test_query.py --verbose "问题"   # 打印检索明细
    python scripts/test_query.py --json "问题"      # 输出与 /ask 同构的 JSON

流程：retriever.lookup_qa_cache（问答缓存优先，命中直返历史摘要+来源）→
      retrieve_web + rerank（联网兜底）→ llm.generate → 引用解析 → save_qa_record 落库；
      联网为空走 generate_direct 直答兜底（无来源，不入缓存）。
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
from src.webrag.milvus_store import MilvusStore
from src.webrag.schemas import AskResponse


def main() -> None:
    ap = argparse.ArgumentParser(description="命令行单测一条问题")
    ap.add_argument("question", help="用户问题")
    ap.add_argument("--verbose", action="store_true", help="打印检索明细")
    ap.add_argument("--json", action="store_true", help="输出与 /ask 同构的 JSON")
    args = ap.parse_args()

    settings = load_settings()
    store = MilvusStore(settings.milvus_uri)
    store.connect()
    embedder = retriever.get_embedder()

    try:
        # ① 问答缓存优先：命中直接返回历史摘要 + 来源（不联网、不调 LLM）
        hit, qvec = retriever.lookup_qa_cache(
            args.question, store, embedder, settings.milvus_qa_collection, settings
        )
        if hit is not None:
            resp = AskResponse(answer=hit.summary, sources=hit.sources, cached=True)
            if args.verbose:
                print(f"[cache] 命中历史问答：{hit.question} score={hit.score:.4f}")
            if args.json:
                print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
            else:
                print(f"\n==== 回答（命中历史问答缓存） ====\n{resp.answer}\n==== 来源 ====")
                for s in resp.sources:
                    print(f"  [{s.index}] {s.title}  {s.url}")
            return

        # ② 未命中 → 联网兜底检索 + 重排
        results = retriever.retrieve_web(
            args.question, store, embedder, settings, settings.retriever.top_k, qvec=qvec
        )
        if settings.retriever.enable_rerank and results:
            results = retriever.rerank(args.question, results, settings)
        contexts = [r.chunk for r in results]
        if args.verbose:
            print(f"[retriever] 联网召回 {len(contexts)} 条（top_k={settings.retriever.top_k}，重排={settings.retriever.enable_rerank}）")
            for i, r in enumerate(results, 1):
                print(f"[retriever]   [{i}] {r.chunk.metadata.title} score={r.score:.4f}")

        if not contexts:
            if settings.retriever.enable_llm_direct:
                client = llm.DeepSeekClient(settings.deepseek_api_key, model=settings.llm.model)
                answer = client.generate_direct(args.question)
                resp = AskResponse(answer=answer, sources=[], direct=True)  # 无来源 → 不入问答缓存
                if args.json:
                    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
                else:
                    print(f"\n==== 回答（LLM 直答兜底，无检索资料） ====\n{resp.answer}")
                return
            print(json.dumps({"error": {"code": "EMPTY_RESULT", "message": "检索无结果，无法作答"}}, ensure_ascii=False))
            sys.exit(1)

        client = llm.DeepSeekClient(settings.deepseek_api_key, model=settings.llm.model)
        answer = client.generate(args.question, contexts)
        resp = llm.build_response(answer, contexts)

        # ③ 缓存落库（best-effort：失败不影响本次回答）
        retriever.save_qa_record(
            args.question, resp, store, embedder, settings.milvus_qa_collection, settings, qvec=qvec
        )

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
