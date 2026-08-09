"""离线评测脚本：检索（recall@k, MRR）+ 生成（关键词命中率）两维度。

用法：
  python eval/run_eval.py                     # 使用默认 QA 集 + mock 检索
  python eval/run_eval.py --live              # 调用实际服务（需 Docker 运行中）
  python eval/run_eval.py --qa custom.json    # 自定义评测集
  python eval/run_eval.py --output report.json  # 输出 JSON 报告

Mock 模式（默认）：用内置的模拟检索结果（eval/sample_retrieval.json）验证评测逻辑，
无需启动 Docker / LLM。适用于 CI 回归。
Live 模式：向实际服务 /ask 发请求，测量端到端指标。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent

# ---- 评测数据结构 ----


@dataclass
class QARecord:
    id: str
    question: str
    expected_keywords: list[str]
    expected_sources: list[str]
    type: str  # fact_lookup | how_to | news | comparison | opinion


@dataclass
class RetrievalMetrics:
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float  # Mean Reciprocal Rank
    total_queries: int


@dataclass
class GenerationMetrics:
    keyword_precision: float  # 期望关键词命中率
    total_keywords: int
    hit_keywords: int


@dataclass
class EvalReport:
    retrieval: RetrievalMetrics
    generation: GenerationMetrics
    by_intent: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_question: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0


# ---- 加载 QA 集 ----


def load_qa_set(path: Path) -> list[QARecord]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        QARecord(
            id=r["id"],
            question=r["question"],
            expected_keywords=r.get("expected_keywords", []),
            expected_sources=r.get("expected_sources", []),
            type=r.get("type", "fact_lookup"),
        )
        for r in raw
    ]


# ---- 检索评估 ----


def _any_domain_hit(expected_domains: list[str], urls: list[str]) -> bool:
    """任一期望域名出现在检索结果中 → 命中。"""
    if not expected_domains:
        return True  # 无期望来源 → 不扣分
    for ed in expected_domains:
        for u in urls:
            if ed.lower() in u.lower():
                return True
    return False


def compute_recall(qa: QARecord, retrieved_urls: list[str], k: int) -> bool:
    """Recall@k：前 k 个检索结果中是否包含任一期望来源域名。"""
    return _any_domain_hit(qa.expected_sources, retrieved_urls[:k])


def compute_mrr_single(qa: QARecord, retrieved_urls: list[str]) -> float:
    """单条 MRR：第一个期望来源在结果中的排名的倒数。"""
    if not qa.expected_sources:
        return 1.0  # 无期望来源 → 满分
    for rank, url in enumerate(retrieved_urls, 1):
        for ed in qa.expected_sources:
            if ed.lower() in url.lower():
                return 1.0 / rank
    return 0.0


def evaluate_retrieval(qas: list[QARecord], results: dict[str, list[str]]) -> RetrievalMetrics:
    """对评测集中的每个问题，计算 Recall@1/3/5 和 MRR。"""
    n = len(qas)
    r1 = sum(1 for q in qas if compute_recall(q, results.get(q.id, []), 1))
    r3 = sum(1 for q in qas if compute_recall(q, results.get(q.id, []), 3))
    r5 = sum(1 for q in qas if compute_recall(q, results.get(q.id, []), 5))
    mrr_sum = sum(compute_mrr_single(q, results.get(q.id, [])) for q in qas)
    return RetrievalMetrics(
        recall_at_1=r1 / n if n else 0,
        recall_at_3=r3 / n if n else 0,
        recall_at_5=r5 / n if n else 0,
        mrr=mrr_sum / n if n else 0,
        total_queries=n,
    )


# ---- 生成评估 ----


def _keyword_hit_count(answer: str, keywords: list[str]) -> tuple[int, int]:
    """统计回答中命中了多少期望关键词（大小写不敏感）。"""
    if not keywords:
        return 0, 0
    lower_answer = answer.lower()
    hit = sum(1 for kw in keywords if kw.lower() in lower_answer)
    return hit, len(keywords)


def evaluate_generation(qas: list[QARecord], answers: dict[str, str]) -> GenerationMetrics:
    """关键词命中率：期望关键词出现在生成回答中的比例。"""
    total_hit = 0
    total_kw = 0
    for q in qas:
        ans = answers.get(q.id, "")
        hit, n_kw = _keyword_hit_count(ans, q.expected_keywords)
        total_hit += hit
        total_kw += n_kw
    return GenerationMetrics(
        keyword_precision=total_hit / total_kw if total_kw else 0,
        total_keywords=total_kw,
        hit_keywords=total_hit,
    )


# ---- 分意图统计 ----


def compute_by_intent(qas: list[QARecord], results: dict[str, list[str]], answers: dict[str, str]) -> dict[str, Any]:
    """按意图类型分组统计。"""
    by_type: dict[str, list[QARecord]] = {}
    for q in qas:
        by_type.setdefault(q.type, []).append(q)
    out: dict[str, Any] = {}
    for intent_type, group in by_type.items():
        out[intent_type] = {
            "count": len(group),
            "recall@3": sum(1 for q in group if compute_recall(q, results.get(q.id, []), 3)) / len(group),
            "mrr": sum(compute_mrr_single(q, results.get(q.id, [])) for q in group) / len(group),
            "keyword_rate": sum(
                _keyword_hit_count(answers.get(q.id, ""), q.expected_keywords)[0]
                for q in group
            )
            / max(sum(len(q.expected_keywords) for q in group), 1),
        }
    return out


# ---- Mock 检索结果 ----


_MOCK_URLS: dict[str, list[str]] = {
    "fact_001": ["https://zh.wikipedia.org/wiki/检索增强生成", "https://zhuanlan.zhihu.com/p/rag-intro", "https://blog.csdn.net/rag-tutorial"],
    "fact_002": ["https://zh.wikipedia.org/wiki/自注意力", "https://arxiv.org/abs/1706.03762", "https://paperswithcode.com/method/attention"],
    "fact_003": ["https://docs.python.org/zh-cn/3/glossary.html", "https://realpython.com/python-gil/"],
    "fact_004": ["https://developer.mozilla.org/zh-CN/docs/Web/HTTP/Status", "https://www.w3.org/Protocols/rfc2616/"],
    "fact_005": ["https://www.docker.com/resources/what-container", "https://www.redhat.com/zh/topics/containers/containers-vs-vms"],
    "fact_006": ["https://milvus.io/docs/overview.md", "https://zilliz.com/what-is-vector-database"],
    "howto_001": ["https://docs.docker.com/engine/install/ubuntu/", "https://docs.docker.com/engine/install/"],
    "howto_002": ["https://docs.python.org/zh-cn/3/library/venv.html", "https://packaging.python.org/guides/installing-using-pip-and-virtual-environments/"],
    "howto_003": ["https://git-scm.com/book/zh/v2/Git-分支-分支的新建与合并", "https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/addressing-merge-conflicts"],
    "howto_004": ["https://nginx.org/en/docs/http/ngx_http_proxy_module.html", "https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/"],
    "howto_005": ["https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html", "https://dev.mysql.com/doc/refman/8.0/en/backup-and-recovery.html"],
    "howto_006": ["https://fastapi.tiangolo.com/tutorial/request-files/", "https://fastapi.tiangolo.com/zh/tutorial/request-files/"],
    "news_001": ["https://www.36kr.com/ai-news", "https://www.jiqizhixin.com/"],
    "news_002": ["https://www.ithome.com/", "https://36kr.com/"],
    "news_003": ["https://docs.python.org/zh-cn/3/whatsnew/", "https://peps.python.org/"],
    "news_004": ["https://www.freebuf.com/", "https://www.secrss.com/"],
    "news_005": ["https://github.com/trending", "https://www.oschina.net/project"],
    "news_006": ["https://www.eet-china.com/", "https://www.semi.org/"],
    "compare_001": ["https://redis.io/docs/about/", "https://memcached.org/about"],
    "compare_002": ["https://graphql.org/learn/", "https://restfulapi.net/"],
    "compare_003": ["https://www.mysql.com/", "https://www.postgresql.org/"],
    "compare_004": ["https://react.dev/", "https://vuejs.org/"],
    "compare_005": ["https://martinfowler.com/articles/microservices.html", "https://microservices.io/"],
    "compare_006": ["https://kubernetes.io/docs/concepts/overview/", "https://docs.docker.com/engine/swarm/"],
    "opinion_001": ["https://www.zhihu.com/question/python-vs-go", "https://stackoverflow.com/questions/python-vs-golang"],
    "opinion_002": ["https://2024.stateofjs.com/", "https://www.zhihu.com/question/frontend-framework-2026"],
    "opinion_003": ["https://aws.amazon.com/cn/cloud-vs-on-premises/", "https://cloud.google.com/why-google-cloud"],
    "opinion_004": ["https://google.github.io/eng-practices/review/", "https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests"],
    "opinion_005": ["https://www.atlassian.com/agile", "https://www.zhihu.com/question/agile-vs-waterfall"],
    "opinion_006": ["https://docs.python.org/zh-cn/3/library/pdb.html", "https://wiki.python.org/moin/PythonDebuggingTools"],
    "edge_001": [],
    "edge_002": ["https://zh.wikipedia.org/wiki/瑞利散射", "https://spaceplace.nasa.gov/blue-sky/"],
}

_MOCK_ANSWERS: dict[str, str] = {
    "fact_001": "RAG（检索增强生成）是一种结合信息检索和大语言模型的技术架构。它先从知识库中检索相关文档，然后将检索结果作为上下文提供给大语言模型生成回答。",
    "fact_002": "自注意力机制是Transformer的核心组件，通过Query、Key、Value三个矩阵计算序列中每个位置与其他位置的相关性权重。",
    "fact_003": "Python的GIL（全局解释器锁）限制了同一时刻只有一个线程执行Python字节码。这导致CPU密集型任务在多线程下无法加速。",
    "fact_004": "HTTP 301表示永久重定向，搜索引擎会将权重转移到新URL；302表示临时重定向，搜索引擎保留原URL的索引。",
    "fact_005": "Docker容器共享宿主内核，启动快（秒级），资源利用高效；虚拟机运行完整OS，隔离性强但启动慢（分钟级），开销大。",
    "fact_006": "向量数据库专门存储和检索高维向量（embedding），支持近似最近邻搜索。Milvus是开源向量数据库，支持混合检索和分布式部署。",
    "howto_001": "在Ubuntu上安装Docker：sudo apt update && sudo apt install docker.io。在CentOS上：sudo yum install docker。",
    "howto_002": "Python虚拟环境创建：python -m venv myenv，激活：source myenv/bin/activate，安装包：pip install package。",
    "howto_003": "Git合并冲突解决：git merge后冲突文件会有标记，手动编辑后git add，最后git commit完成合并。",
    "howto_004": "Nginx反向代理配置：在location块中使用proxy_pass指令指向后端服务地址。",
    "howto_005": "MySQL备份使用mysqldump命令导出数据，恢复用mysql命令导入备份文件。",
    "howto_006": "FastAPI文件上传使用UploadFile类型参数，通过File()函数声明，可以接收单个或多个文件。",
    "news_001": "近期AI大模型领域进展包括：更多开源模型发布，多模态能力增强，推理能力提升。",
    "news_003": "Python最新版本引入了多项PEP新特性，包括性能优化和语法改进。",
    "compare_001": "Redis支持多种数据结构并可将数据持久化到磁盘，Memcached仅支持简单键值对且纯内存存储。",
    "compare_002": "REST API有固定端点，可能过度获取数据；GraphQL支持按需查询，前端指定所需字段，减少数据传输。",
    "compare_003": "MySQL擅长简单查询，PostgreSQL在处理复杂事务和JSON数据方面更强，扩展性也更好。",
    "compare_004": "React使用虚拟DOM和JSX，有庞大的生态；Vue.js学习曲线更平缓，模板语法更直观。",
    "compare_005": "微服务架构部署独立、扩展灵活但通信复杂；单体架构部署简单但扩展困难，适合小型项目。",
    "compare_006": "Kubernetes提供自动编排和扩展能力，集群管理复杂但功能强大；Docker Swarm更简洁易用，适合中小规模。",
    "opinion_001": "Python适合快速开发个人项目，开发速度快；Go语言在性能和并发方面更强，适用于需要高并发的场景。",
    "opinion_004": "代码审查最佳实践包括：小批量提交、明确审查标准、使用自动化工具辅助、注重代码质量和团队规范。",
    "edge_001": "问题太短，无法确定具体意图。",
    "edge_002": "天空呈蓝色是因为阳光穿过大气层时，波长较短的蓝光更容易被空气分子散射（瑞利散射）。",
}


def mock_retrieve(qa: QARecord) -> list[str]:
    """模拟检索：返回预定义的 URL 列表。"""
    return _MOCK_URLS.get(qa.id, [])


def mock_generate(qa: QARecord) -> str:
    """模拟生成：返回预定义的答案。"""
    return _MOCK_ANSWERS.get(qa.id, "无法回答。")


# ---- Live 模式（调用实际服务）----


def live_retrieve(qa: QARecord, base_url: str, timeout: int) -> list[str]:
    """实际调用 /ask 获取检索来源 URL。"""
    import requests

    try:
        resp = requests.post(
            f"{base_url}/ask",
            json={"question": qa.question, "use_web_search": True},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [s["url"] for s in data.get("sources", [])]
    except Exception as exc:
        print(f"  [WARN] {qa.id} 请求失败：{exc}")
        return []


def live_generate(qa: QARecord, base_url: str, timeout: int) -> str:
    """实际调用 /ask 获取回答。"""
    import requests

    try:
        resp = requests.post(
            f"{base_url}/ask",
            json={"question": qa.question, "use_web_search": True},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("answer", "")
    except Exception as exc:
        print(f"  [WARN] {qa.id} 请求失败：{exc}")
        return ""


# ---- 报告输出 ----


def print_report(report: EvalReport, qas: list[QARecord]) -> None:
    """控制台格式化输出评测报告。"""
    sep = "=" * 60
    print(f"\n{sep}")
    print("  WebRAG 离线评测报告")
    print(sep)
    print(f"\n  总问题数：{len(qas)}")
    print(f"  耗时：{report.elapsed_seconds:.1f}s\n")

    r = report.retrieval
    print("【检索评估】")
    print(f"  Recall@1  : {r.recall_at_1:.1%}")
    print(f"  Recall@3  : {r.recall_at_3:.1%}")
    print(f"  Recall@5  : {r.recall_at_5:.1%}")
    print(f"  MRR       : {r.mrr:.3f}")

    g = report.generation
    print("\n【生成评估】")
    print(f"  关键词命中率 : {g.keyword_precision:.1%}  ({g.hit_keywords}/{g.total_keywords})")

    print("\n【分意图评估】")
    print(f"  {'意图':<14} {'数量':>4} {'Recall@3':>9} {'MRR':>7} {'关键词':>8}")
    print(f"  {'-'*44}")
    for intent, stats in sorted(report.by_intent.items()):
        print(
            f"  {intent:<14} {stats['count']:>4} "
            f"{stats['recall@3']:>8.1%} {stats['mrr']:>7.3f} "
            f"{stats['keyword_rate']:>8.1%}"
        )

    # 未命中明细
    print("\n【未命中 Recall@3 的问题】")
    for pq in report.per_question:
        if not pq["recall_3_hit"]:
            print(f"  ✗ {pq['id']}: \"{pq['question'][:40]}...\"")
            if pq.get("retrieved_urls"):
                print(f"    → 检索到: {pq['retrieved_urls'][:3]}")
    print(f"\n{sep}\n")


def export_report_json(report: EvalReport, path: Path) -> None:
    """导出 JSON 格式报告（可被 CI 解析）。"""
    data = {
        "total_queries": report.retrieval.total_queries,
        "elapsed_seconds": report.elapsed_seconds,
        "retrieval": {
            "recall_at_1": report.retrieval.recall_at_1,
            "recall_at_3": report.retrieval.recall_at_3,
            "recall_at_5": report.retrieval.recall_at_5,
            "mrr": report.retrieval.mrr,
        },
        "generation": {
            "keyword_precision": report.generation.keyword_precision,
            "total_keywords": report.generation.total_keywords,
            "hit_keywords": report.generation.hit_keywords,
        },
        "by_intent": report.by_intent,
        "per_question": [
            {
                k: v for k, v in pq.items()
                if k != "retrieved_urls"  # URL 列表太长，JSON 中省略
            }
            for pq in report.per_question
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  JSON 报告已输出：{path}")


# ---- 主流程 ----


def run_eval(qa_path: Path, live: bool = False, base_url: str = "http://localhost:8000", timeout: int = 120) -> EvalReport:
    """执行离线评测并返回报告。"""
    qas = load_qa_set(qa_path)
    print(f"\n  加载 {len(qas)} 条评测数据（来源：{qa_path.name}）")
    print(f"  模式：{'Live（实际服务）' if live else 'Mock（模拟检索）'}\n")

    start = time.monotonic()
    results: dict[str, list[str]] = {}
    answers: dict[str, str] = {}

    for qa in qas:
        if live:
            urls = live_retrieve(qa, base_url, timeout)
            ans = live_generate(qa, base_url, timeout)
        else:
            urls = mock_retrieve(qa)
            ans = mock_generate(qa)
        results[qa.id] = urls
        answers[qa.id] = ans

    elapsed = time.monotonic() - start

    # 计算指标
    retrieval_metrics = evaluate_retrieval(qas, results)
    generation_metrics = evaluate_generation(qas, answers)
    by_intent = compute_by_intent(qas, results, answers)

    per_question = []
    for qa in qas:
        per_question.append({
            "id": qa.id,
            "question": qa.question,
            "type": qa.type,
            "recall_3_hit": compute_recall(qa, results.get(qa.id, []), 3),
            "mrr": compute_mrr_single(qa, results.get(qa.id, [])),
            "keyword_hit_rate": _keyword_hit_count(answers.get(qa.id, ""), qa.expected_keywords)[0]
            / max(len(qa.expected_keywords), 1),
            "retrieved_urls": results.get(qa.id, []),
            "answer_preview": answers.get(qa.id, "")[:100],
        })

    return EvalReport(
        retrieval=retrieval_metrics,
        generation=generation_metrics,
        by_intent=by_intent,
        per_question=per_question,
        elapsed_seconds=elapsed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="WebRAG 离线评测脚本")
    parser.add_argument("--qa", default=str(EVAL_DIR / "qa_set.json"), help="评测集路径")
    parser.add_argument("--live", action="store_true", help="调用实际服务（需 Docker 运行中）")
    parser.add_argument("--base-url", default="http://localhost:8000", help="实际服务地址（live 模式）")
    parser.add_argument("--timeout", type=int, default=120, help="每问超时秒数（live 模式）")
    parser.add_argument("--output", default="", help="JSON 报告输出路径（可选）")
    args = parser.parse_args()

    qa_path = Path(args.qa)
    if not qa_path.exists():
        print(f"错误：评测集不存在：{qa_path}")
        sys.exit(1)

    report = run_eval(
        qa_path,
        live=args.live,
        base_url=args.base_url,
        timeout=args.timeout,
    )
    print_report(report, load_qa_set(qa_path))

    if args.output:
        export_report_json(report, Path(args.output))


if __name__ == "__main__":
    main()
