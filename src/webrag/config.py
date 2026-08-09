"""配置加载：.env（密钥/连接）+ config/settings.yaml（可调参数）。

约定（config/README.md）：
- 密钥只进 .env，绝不进 settings.yaml；
- 新增配置项需在 PR 说明用途与默认值。
负责人：#1 项目协调 / 架构。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


@dataclass
class LLMSettings:
    model: str = "deepseek-chat"
    temperature: float = 0.3
    max_tokens: int = 2048
    timeout_seconds: int = 60


@dataclass
class CrawlerSettings:
    top_urls: int = 5
    request_timeout_seconds: int = 15
    request_delay_seconds: float = 1.0


@dataclass
class ChunkerSettings:
    chunk_size: int = 512
    overlap: int = 64
    respect_paragraph: bool = True
    # 小→大（Small-to-Big）两级粒度（P0）
    enable_two_level: bool = False   # 启用两级切块：小 chunk 检索 + 大 chunk 送 LLM
    child_chunk_size: int = 256      # 检索用的小块
    parent_chunk_size: int = 1024    # LLM 上下文用的大块


@dataclass
class RetrieverSettings:
    top_k: int = 10
    enable_rerank: bool = True
    rerank_top_n: int = 5
    rerank_min_score: float = 0.6  # 重排后低于此分数的片段剔除（噪声不进 LLM 上下文）
    max_chunks_per_page: int = 12  # 联网抓取时每页最多块数（CPU-only 实测 ~1.5s/块）
    max_web_chunks_total: int = 24  # 联网链路嵌入总块数封顶（5 页×12 块会超前端超时；24 块≈36s）
    enable_llm_direct: bool = True  # 兜底：联网检索为空时 LLM 直答；关闭则返回 EMPTY_RESULT
    enable_qa_cache: bool = True  # 问答缓存优先：/ask 先查历史相似问题（webrag_qa）
    qa_min_score: float = 0.80  # 缓存命中阈值：问题向量余弦 ≥ 此值才算命中（需评测调参，宁高勿低）
    qa_top_k: int = 3  # 缓存检索候选数（取 top-1 判定命中）
    # P1: QA缓存 jieba 关键词辅助评分
    qa_jaccard_weight: float = 0.30  # Jaccard 在综合分中的权重（0=纯dense，1=纯Jaccard）
    qa_jaccard_min: float = 0.30  # Jaccard 低于此值的不参与辅助评分（避免噪声）
    # P1: 上下文去重
    enable_context_dedup: bool = True  # 重排后对 LLM 上下文做 Jaccard n-gram 去重
    dedup_jaccard_threshold: float = 0.75  # Jaccard 去重阈值（超过视为重复）
    dedup_ngram_n: int = 3  # Jaccard 去重的 n-gram 大小
    # P1: 来源质量分层
    enable_source_quality: bool = True  # 对检索结果附加来源质量分


@dataclass
class HallucinationCheckerSettings:
    """幻觉检测配置（P1 优化）。"""
    enable: bool = True                     # 是否启用幻觉检测
    enable_auto_rewrite: bool = False       # 检测到幻觉后是否自动重写回答（额外LLM调用，默认关）


@dataclass
class QueryRewriterSettings:
    """Query 改写与扩展配置（P0 优化）。"""
    enable: bool = True                   # 是否启用 Query 改写管线
    enable_intent: bool = True            # 意图分类
    enable_multi_rewrite: bool = True     # 多路改写（关键词/正式/子问题）
    enable_hyde: bool = True              # HyDE 草稿生成
    enable_followup: bool = True          # 追问检测与改写（多轮对话：判定当前问题是否依赖历史，改写为自包含问题）
    followup_max_history: int = 6         # 追问改写携带的历史消息条数上限（取最近 N 条）
    followup_max_chars: int = 3000        # 追问改写的历史文本总长上限（超出截断较早轮次）


@dataclass
class ServerSettings:
    host: str = "0.0.0.0"
    port: int = 8000
    ask_timeout_seconds: int = 105  # /ask 整体预算：超时返回 TIMEOUT，避免挂死（< 前端 200s）


@dataclass
class EvalSettings:
    """评测与反馈闭环配置（P2 优化）。"""
    enable_feedback: bool = True          # 是否开启在线反馈收集
    feedback_log_path: str = "logs/feedback.jsonl"  # 反馈日志路径


@dataclass
class AdminSettings:
    """管理后台（离线知识入库）配置。密钥与连接信息放 .env（MYSQL_* / ADMIN_JWT_SECRET）。"""
    token_ttl_seconds: int = 43200           # 登录 token 有效期（12h）
    max_file_bytes: int = 10 * 1024 * 1024   # 上传文件大小上限（10MB）
    max_chars_per_doc: int = 300_000         # 单文档字符上限（≈600 块，CPU 嵌入约 15min，超限拒绝）


@dataclass
class LogSettings:
    """结构化日志配置（对应 src/webrag/logger 的 LogConfig；字段名必须一致）。"""
    level: str = "INFO"                     # DEBUG / INFO / WARNING / ERROR
    log_dir: str = "logs"                   # 日志目录（相对项目根，已 gitignore）
    file_name: str = "app.log"              # 主日志文件名（JSONL，按行结构化）
    max_bytes: int = 10 * 1024 * 1024       # 单文件上限 10MB，超出轮转
    backup_count: int = 5                   # 保留轮转文件数
    console: bool = True                    # 同时输出控制台（Docker stdout）
    console_json: bool = False              # 控制台也输出 JSON（true 便于容器日志采集）
    stats_interval: int = 50                # 每 N 个请求输出一次聚合统计（命中率/耗时/token）


@dataclass
class Settings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    crawler: CrawlerSettings = field(default_factory=CrawlerSettings)
    chunker: ChunkerSettings = field(default_factory=ChunkerSettings)
    retriever: RetrieverSettings = field(default_factory=RetrieverSettings)
    query_rewriter: QueryRewriterSettings = field(default_factory=QueryRewriterSettings)
    hallucination_checker: HallucinationCheckerSettings = field(default_factory=HallucinationCheckerSettings)
    eval: EvalSettings = field(default_factory=EvalSettings)
    admin: AdminSettings = field(default_factory=AdminSettings)
    server: ServerSettings = field(default_factory=ServerSettings)
    logging: LogSettings = field(default_factory=LogSettings)

    # ---- .env 中的连接信息 ----
    deepseek_api_key: str = ""
    search_provider: str = "bing"
    search_api_key: str = ""
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "webrag_kb"  # 遗留：预建知识库（已废弃，/ask 改走问答缓存；见 schema.py COLLECTION_KB）
    milvus_qa_collection: str = "webrag_qa"  # 问答缓存 collection（question → 摘要 + 来源）
    milvus_offline_collection: str = "webrag_offline_kb"  # 离线知识库 collection（管理后台入库，独立于问答链路）
    redis_url: str = "redis://localhost:6379"
    embed_model_path: str = "./models/bge-m3"
    reranker_model_path: str = "./models/bge-reranker-large"
    embed_device: str = "cpu"  # CPU-only（放弃 GPU）：保留字段仅为兼容，勿设 cuda
    # ---- 管理后台（离线知识入库）：MySQL 账号库 + JWT ----
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "webrag"
    mysql_password: str = ""
    mysql_database: str = "webrag_admin"
    admin_jwt_secret: str = ""  # JWT 签名密钥（.env 的 ADMIN_JWT_SECRET；为空时签发校验均告警降级）


def _apply(section: dict | None, cls):
    """只取 yaml 中该 dataclass 已知的键，未知键忽略（容忍多余配置）。"""
    section = section or {}
    known = {k: v for k, v in section.items() if k in cls.__dataclass_fields__}
    return cls(**known)


@lru_cache(maxsize=1)
def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    s = Settings(
        llm=_apply(raw.get("llm"), LLMSettings),
        crawler=_apply(raw.get("crawler"), CrawlerSettings),
        chunker=_apply(raw.get("chunker"), ChunkerSettings),
        retriever=_apply(raw.get("retriever"), RetrieverSettings),
        query_rewriter=_apply(raw.get("query_rewriter"), QueryRewriterSettings),
        hallucination_checker=_apply(raw.get("hallucination_checker"), HallucinationCheckerSettings),
        eval=_apply(raw.get("eval"), EvalSettings),
        admin=_apply(raw.get("admin"), AdminSettings),
        server=_apply(raw.get("server"), ServerSettings),
        logging=_apply(raw.get("logging"), LogSettings),
    )
    s.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
    # DEEPSEEK_MODEL：.env 覆盖 settings.yaml 的 llm.model（连接信息类，env 优先；
    # 值为空时视作未设置，回退 yaml/默认值）
    s.llm.model = os.getenv("DEEPSEEK_MODEL") or s.llm.model
    s.search_provider = os.getenv("SEARCH_PROVIDER", "bing")
    s.search_api_key = os.getenv("SEARCH_API_KEY", "")
    s.milvus_uri = os.getenv("MILVUS_URI", "http://localhost:19530")
    s.milvus_collection = os.getenv("MILVUS_COLLECTION", "webrag_kb")
    s.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    s.embed_model_path = os.getenv("EMBED_MODEL_PATH", "./models/bge-m3")
    s.reranker_model_path = os.getenv("RERANKER_MODEL_PATH", "./models/bge-reranker-large")
    s.embed_device = os.getenv("EMBED_DEVICE", "cpu")
    s.milvus_qa_collection = os.getenv("MILVUS_QA_COLLECTION", "webrag_qa")
    # ---- 管理后台（离线知识入库）：MySQL + JWT + 离线库 collection ----
    s.milvus_offline_collection = os.getenv("MILVUS_OFFLINE_COLLECTION", "webrag_offline_kb")
    s.mysql_host = os.getenv("MYSQL_HOST", "localhost")
    s.mysql_port = int(os.getenv("MYSQL_PORT", "3306"))
    s.mysql_user = os.getenv("MYSQL_USER", "webrag")
    s.mysql_password = os.getenv("MYSQL_PASSWORD", "")
    s.mysql_database = os.getenv("MYSQL_DATABASE", "webrag_admin")
    s.admin_jwt_secret = os.getenv("ADMIN_JWT_SECRET", "")
    return s
