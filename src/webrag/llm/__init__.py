"""DeepSeek 生成 + 引用标注解析。

接口契约（docs/api.md §3）：generate(question, contexts) -> Answer{answer, sources}
引用规范见 api.md §2；负责人：#7 LLM 接入。
"""

from __future__ import annotations

import re

from src.webrag.schemas import AskResponse, Chunk, Source

PROMPT_TEMPLATE = """你是信息检索问答助手。请仅依据给定的上下文片段回答用户问题。

上下文片段按 [1]..[k] 编号。回答时：
- 只使用上下文中的信息，在对应句末标注引用 [n]；
- 上下文不足以回答时，明确说明，不要编造；
- 不要引用未给出的编号。

上下文：
{contexts}

用户问题：{question}
"""

_CITE_RE = re.compile(r"\[(\d+)\]")


class DeepSeekClient:
    """DeepSeek 调用封装（OpenAI 兼容接口）。

    TODO(#7)：deepseek-chat 默认 / deepseek-reasoner 可选，超时与重试。
    """

    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        self.api_key = api_key
        self.model = model

    def generate(self, question: str, contexts: list[Chunk]) -> str:
        raise NotImplementedError("DeepSeekClient.generate() 待 #7 实现")


def parse_citations(answer: str, contexts: list[Chunk]) -> list[Source]:
    """从回答解析 [n] 引用 → sources（api.md §2 校验规则）。

    - n 超出 contexts 范围：剔除（幽灵引用）；
    - 同一 URL 多次引用：合并，保留第一次出现的 index。
    """
    sources: list[Source] = []
    seen_urls: set[str] = set()
    for n in sorted({int(m) for m in _CITE_RE.findall(answer)}):
        if not 1 <= n <= len(contexts):
            continue
        meta = contexts[n - 1].metadata
        if meta.url in seen_urls:
            continue
        seen_urls.add(meta.url)
        sources.append(Source(index=n, title=meta.title, url=meta.url))
    return sources


def build_response(answer: str, contexts: list[Chunk]) -> AskResponse:
    """生成 AskResponse：answer + 校验后的 sources（api.md §2）。"""
    return AskResponse(answer=answer, sources=parse_citations(answer, contexts))
