"""DeepSeek 生成 + 引用标注解析。

接口契约（docs/api.md §3）：generate(question, contexts) -> Answer{answer, sources}
引用规范见 api.md §2；负责人：#7 LLM 接入。
"""

from __future__ import annotations

import re
import time

from src.webrag.logger import current_metrics, get_logger
from src.webrag.schemas import AskResponse, Chunk, Source

_log = get_logger("llm")

PROMPT_TEMPLATE = """你是严谨的信息检索问答助手。你的回答必须严格基于下方提供的上下文片段。

## 回答规则
1. **逐段核实**：先找出与问题相关的上下文片段，逐一确认是否包含答案
2. **引用标注**：每句基于上下文的事实陈述，在句末标注来源编号 [n]
3. **不确定就坦白**：如果上下文中信息不完整或相互矛盾，明确说明「根据现有资料无法确定」
4. **禁止编造**：不要添加上下文未提及的任何数字、人名、日期或具体细节
5. **结构化**：如有多个要点，使用分点列出
6. **详尽完整**：在资料允许的范围内尽量展开回答，分点说明细节与依据，避免一句话结论

## 上下文
{contexts}

{time_note}## 用户问题
{question}

## 回答
"""

# 三级级联兜底（docs/architecture.md §6）：检索无结果时让模型直接作答
DIRECT_PROMPT_TEMPLATE = """你是知识问答助手。请直接根据你的知识回答用户问题。

要求：
- 回答准确、条理清晰；不确定的内容明确说明「不确定」，不要编造；
- 不添加引用标注（本回答无检索资料支撑）。

{time_note}用户问题：{question}
"""

_CITE_RE = re.compile(r"\[(\d+)\]")


def _time_note(time_context: str) -> str:
    """时效性问题的时间基准提示：非空时插入 Prompt（「近日/近期」需以当前时间为基准理解）。

    空串返回空（模板 format 渲染为空，既有 Prompt 逐字不变）；time_context 由
    query_rewriter 检测到相对时间词后注入（例：2026年8月8日），main 透传至此。
    """
    if not time_context:
        return ""
    return (
        f"## 时间基准\n"
        f"当前时间：{time_context}。问题涉及「近日/近期/最新/今天」等时效性表述时，"
        f"请以此时间为基准理解时间范围，避免给出与当前时间不符的过期结论。\n\n"
    )


class DeepSeekClient:
    """DeepSeek 调用封装（OpenAI 兼容接口）：deepseek-chat 默认 / deepseek-reasoner 可选。"""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        temperature: float = 0.3,
        max_tokens: int = 1024,
        timeout_seconds: int = 60,
        base_url: str = "https://api.deepseek.com",
    ):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url

    def _record(
        self,
        call: str,
        t0: float,
        usage=None,
        ttft_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """LLM 调用指标：耗时 / token / TTFT → 归入当前请求（current_metrics），并落 llm.call 事件。

        独立使用（scripts 等未 bind 请求上下文）时退化为仅事件，不做请求级聚合。
        """
        duration_ms = (time.monotonic() - t0) * 1000
        prompt = completion = total = None
        if usage is not None:
            prompt = getattr(usage, "prompt_tokens", None)
            completion = getattr(usage, "completion_tokens", None)
            total = getattr(usage, "total_tokens", None)
        m = current_metrics()
        if m is not None:
            m.record_llm(
                call, duration_ms, ttft_ms=ttft_ms,
                prompt_tokens=prompt, completion_tokens=completion, error=error,
            )
        fields: dict = {"call": call, "model": self.model, "duration_ms": round(duration_ms, 1)}
        if ttft_ms is not None:
            fields["ttft_ms"] = round(ttft_ms, 1)
        if prompt is not None:
            fields["prompt_tokens"] = prompt
        if completion is not None:
            fields["completion_tokens"] = completion
        if total is not None:
            fields["total_tokens"] = total
        if error:
            fields["error"] = error
        (_log.warning if error else _log.info)("llm.call", extra={"fields": fields})

    def generate(self, question: str, contexts: list[Chunk], time_context: str = "") -> str:
        """调用 DeepSeek 生成回答：仅依据给定上下文，按 [n] 标注引用（Prompt 见 PROMPT_TEMPLATE）。

        time_context：时效性问题（近日/近期…）的当前时间文本，插入 Prompt 作为时间基准；非时效性问题传空串。
        """
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置（.env）")
        if not contexts:
            raise ValueError("无上下文片段，无法生成")

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds)
        prompt = PROMPT_TEMPLATE.format(
            contexts=_format_contexts(contexts), question=question, time_note=_time_note(time_context)
        )
        t0 = time.monotonic()
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:
            self._record("generate", t0, error=str(exc))
            raise RuntimeError(f"DeepSeek 调用失败（{self.model}）：{exc}") from exc
        self._record("generate", t0, usage=getattr(resp, "usage", None))
        return (resp.choices[0].message.content or "").strip()

    def generate_direct(self, question: str, time_context: str = "") -> str:
        """兜底直答（三级级联第三级）：无上下文，模型直接基于自身知识回答。

        与 generate() 的区别：不拼上下文、不标引用（响应 direct=True，sources 为空，
        见 docs/api.md §2）。模型不确定时按 Prompt 要求明说，不硬答。
        time_context：时效性问题（近日/近期…）的当前时间文本（同 generate）。
        """
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置（.env）")

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds)
        prompt = DIRECT_PROMPT_TEMPLATE.format(question=question, time_note=_time_note(time_context))
        t0 = time.monotonic()
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:
            self._record("generate_direct", t0, error=str(exc))
            raise RuntimeError(f"DeepSeek 直答调用失败（{self.model}）：{exc}") from exc
        self._record("generate_direct", t0, usage=getattr(resp, "usage", None))
        return (resp.choices[0].message.content or "").strip()

    def complete(self, prompt: str, max_tokens: int | None = None) -> str:
        """轻量 completion：用于 Query 改写、意图分类等不需要完整上下文的短任务。

        不走 PROMPT_TEMPLATE，直接发送 prompt 作为 user message。
        返回原始文本，失败返回空字符串（调用方自行兜底）。
        """
        if not self.api_key:
            return ""

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds)
        t0 = time.monotonic()
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,  # 改写任务用低温度保证确定性
                max_tokens=max_tokens or 256,
            )
            self._record("complete", t0, usage=getattr(resp, "usage", None))
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            self._record("complete", t0, error=str(exc))
            return ""

    def stream_generate(self, question: str, contexts: list[Chunk], time_context: str = ""):
        """流式生成（/ask/stream 数据源，api.md §1.3）：逐段产出回答文本增量。

        Prompt/参数与 generate() 完全一致，仅 stream=True 逐段返回（含空 delta，
        调用方需跳过）；全文拼接结果 == generate() 的返回值，由调用方累积后
        再走 build_response 做引用解析（引用 [n] 只在 done 事件随 sources 下发）。
        底层异常包装为 RuntimeError，在迭代时抛出。
        time_context：时效性问题（近日/近期…）的当前时间文本（同 generate）。
        """
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置（.env）")
        if not contexts:
            raise ValueError("无上下文片段，无法生成")

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds)
        prompt = PROMPT_TEMPLATE.format(
            contexts=_format_contexts(contexts), question=question, time_note=_time_note(time_context)
        )
        t0 = time.monotonic()
        usage = None
        ttft_ms: float | None = None
        error: str | None = None
        try:
            stream = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
                stream_options={"include_usage": True},  # 末块带 usage（choices 恒为空数组），DeepSeek 官方支持
            )
            for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage  # usage-only 尾块：记录 token 后跳过
                    continue
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    if ttft_ms is None:
                        ttft_ms = (time.monotonic() - t0) * 1000  # 首个内容 delta：调用级首 token 时间
                    yield delta
        except Exception as exc:
            error = str(exc)
            raise RuntimeError(f"DeepSeek 调用失败（{self.model}）：{exc}") from exc
        finally:
            self._record("stream_generate", t0, usage=usage, ttft_ms=ttft_ms, error=error)

    def stream_generate_direct(self, question: str, time_context: str = ""):
        """流式直答兜底：无上下文版本，语义同 generate_direct()，流式产出增量。

        time_context：时效性问题（近日/近期…）的当前时间文本（同 generate_direct）。
        """
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置（.env）")

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_seconds)
        prompt = DIRECT_PROMPT_TEMPLATE.format(question=question, time_note=_time_note(time_context))
        t0 = time.monotonic()
        usage = None
        ttft_ms: float | None = None
        error: str | None = None
        try:
            stream = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
                stream_options={"include_usage": True},  # 末块带 usage（choices 恒为空数组），DeepSeek 官方支持
            )
            for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage  # usage-only 尾块：记录 token 后跳过
                    continue
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    if ttft_ms is None:
                        ttft_ms = (time.monotonic() - t0) * 1000  # 首个内容 delta：调用级首 token 时间
                    yield delta
        except Exception as exc:
            error = str(exc)
            raise RuntimeError(f"DeepSeek 直答调用失败（{self.model}）：{exc}") from exc
        finally:
            self._record("stream_generate_direct", t0, usage=usage, ttft_ms=ttft_ms, error=error)


def _format_contexts(contexts: list[Chunk]) -> str:
    """上下文按 [1]..[k] 编号，附标题与来源 URL，供模型标注引用。"""
    parts = []
    for i, c in enumerate(contexts, 1):
        title = c.metadata.title or c.metadata.url or "未命名来源"
        parts.append(f"[{i}] 标题：{title}\n来源：{c.metadata.url}\n内容：{c.text}")
    return "\n\n".join(parts)


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
