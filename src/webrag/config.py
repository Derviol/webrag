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
    max_tokens: int = 1024
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


@dataclass
class RetrieverSettings:
    top_k: int = 8
    enable_rerank: bool = True
    rerank_top_n: int = 3


@dataclass
class ServerSettings:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class Settings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    crawler: CrawlerSettings = field(default_factory=CrawlerSettings)
    chunker: ChunkerSettings = field(default_factory=ChunkerSettings)
    retriever: RetrieverSettings = field(default_factory=RetrieverSettings)
    server: ServerSettings = field(default_factory=ServerSettings)

    # ---- .env 中的连接信息 ----
    deepseek_api_key: str = ""
    search_provider: str = "bing"
    search_api_key: str = ""
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "webrag_kb"
    redis_url: str = "redis://localhost:6379"
    embed_model_path: str = "./models/bge-m3"


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
        server=_apply(raw.get("server"), ServerSettings),
    )
    s.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
    s.search_provider = os.getenv("SEARCH_PROVIDER", "bing")
    s.search_api_key = os.getenv("SEARCH_API_KEY", "")
    s.milvus_uri = os.getenv("MILVUS_URI", "http://localhost:19530")
    s.milvus_collection = os.getenv("MILVUS_COLLECTION", "webrag_kb")
    s.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    s.embed_model_path = os.getenv("EMBED_MODEL_PATH", "./models/bge-m3")
    return s
