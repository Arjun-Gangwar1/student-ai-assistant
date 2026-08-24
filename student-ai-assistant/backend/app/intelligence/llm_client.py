"""
Provider-abstracted LLM client.

Two changes from the original beyond the provider switch:

  * Calls are async. The Groq SDK's sync client blocks the event loop for the
    whole completion; under APScheduler, a sync call inside the pipeline stalls
    every other request in the process for ~1s per item.

  * Provider comes from LLM_PROVIDER, not from `app_env == "fallback"`. Deriving
    it from the environment name made the fallback unreachable in production,
    which is the one place a provider outage actually matters. The plan's own
    warning about Groq's stability is the reason this abstraction exists.
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.utils import token_budget

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Any provider-side failure, normalised across SDKs."""


class LLMQuotaExhausted(LLMError):
    """A per-day quota was hit. Retrying within the same minute cannot help."""


class BaseLLMClient(ABC):
    name: str
    model: str          # set per-instance from settings, not a class constant

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        json_mode: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> str:
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Yield answer text as it is generated.

        Streaming does not make generation faster — it makes the *wait* start
        producing output at roughly 500ms instead of showing nothing for the full
        second-plus. That gap is most of what separates a chat that feels instant
        from one that feels broken.
        """
        ...


def _wrap(exc: Exception) -> LLMError:
    """
    Normalise a provider exception, distinguishing a per-day quota hit (not
    worth retrying for minutes) from anything transient (worth retrying).
    """
    message = str(exc)
    if "per day" in message.lower():
        return LLMQuotaExhausted(f"groq: {type(exc).__name__}: {exc}")
    return LLMError(f"groq: {type(exc).__name__}: {exc}")


def _estimate_tokens(messages: list[dict], max_tokens: int) -> int:
    """
    Rough token estimate for budget accounting — not billing-accurate.

    ~4 characters/token is the standard rule of thumb for English text. This
    deliberately overestimates a little (few completions use the full
    max_tokens ceiling) because the budget exists to avoid *causing* a 429,
    so erring conservative is the safe direction.
    """
    input_chars = sum(len(m.get("content", "")) for m in messages)
    return (input_chars // 4) + max_tokens


class GroqClient(BaseLLMClient):
    name = "groq"

    def __init__(self) -> None:
        from groq import AsyncGroq

        if not settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not set")
        self.model = settings.groq_model
        self._client = AsyncGroq(api_key=settings.groq_api_key, timeout=30.0)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(LLMError) & retry_if_not_exception_type(LLMQuotaExhausted),
        reraise=True,
    )
    async def chat(
        self,
        messages: list[dict],
        json_mode: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> str:
        kwargs = self._kwargs(messages, temperature, max_tokens, reasoning_effort)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise _wrap(exc) from exc
        token_budget.record(_estimate_tokens(messages, max_tokens))
        return resp.choices[0].message.content or ""

    def _kwargs(self, messages, temperature, max_tokens, reasoning_effort) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # gpt-oss models generate hidden reasoning tokens before answering, which
        # is most of their latency. "low" roughly halves it, and for grounded
        # retrieval answers — where the facts are already in the prompt — there
        # is little for deep reasoning to add.
        effort = reasoning_effort or settings.groq_reasoning_effort
        if effort and effort != "default" and "gpt-oss" in self.model:
            # Passed via extra_body rather than as a named argument: the pinned
            # groq SDK (0.11.0) predates reasoning_effort and rejects it with a
            # TypeError, while the API itself accepts it. extra_body forwards
            # unknown fields verbatim, so this works across SDK versions.
            kwargs["extra_body"] = {"reasoning_effort": effort}
        return kwargs

    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        kwargs = self._kwargs(messages, temperature, max_tokens, reasoning_effort)
        kwargs["stream"] = True
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if text := getattr(delta, "content", None):
                    yield text
        except Exception as exc:
            raise _wrap(exc) from exc
        token_budget.record(_estimate_tokens(messages, max_tokens))


class DeepInfraClient(BaseLLMClient):
    """Fallback provider, OpenAI-compatible API."""

    name = "deepinfra"

    def __init__(self) -> None:
        from openai import AsyncOpenAI

        if not settings.deepinfra_api_key:
            raise LLMError("DEEPINFRA_API_KEY is not set")
        self.model = settings.deepinfra_model
        self._client = AsyncOpenAI(
            api_key=settings.deepinfra_api_key,
            base_url="https://api.deepinfra.com/v1/openai",
            timeout=30.0,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(LLMError),
        reraise=True,
    )
    async def chat(
        self,
        messages: list[dict],
        json_mode: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"deepinfra: {type(exc).__name__}: {exc}") from exc
        return resp.choices[0].message.content or ""

    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        try:
            stream = await self._client.chat.completions.create(
                model=self.model, messages=messages, temperature=temperature,
                max_tokens=max_tokens, stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                if text := getattr(chunk.choices[0].delta, "content", None):
                    yield text
        except Exception as exc:
            raise LLMError(f"deepinfra stream: {type(exc).__name__}: {exc}") from exc


PROVIDERS: dict[str, type[BaseLLMClient]] = {
    "groq": GroqClient,
    "deepinfra": DeepInfraClient,
}

_llm: BaseLLMClient | None = None


def llm() -> BaseLLMClient:
    """The configured client, constructed once."""
    global _llm
    if _llm is None:
        provider = settings.llm_provider
        try:
            _llm = PROVIDERS[provider]()
        except Exception as exc:
            # Fall through to any other configured provider rather than leaving
            # the whole app unable to answer a question.
            logger.error("Primary LLM provider %r unavailable: %s", provider, exc)
            for alt_name, alt_cls in PROVIDERS.items():
                if alt_name == provider:
                    continue
                try:
                    _llm = alt_cls()
                    logger.warning("Falling back to LLM provider %r", alt_name)
                    break
                except Exception:
                    continue
            else:
                raise LLMError(
                    f"No usable LLM provider. Set GROQ_API_KEY or DEEPINFRA_API_KEY."
                ) from exc
        logger.info("LLM provider: %s (%s)", _llm.name, _llm.model)
    return _llm


def reset_llm() -> None:
    """Drop the cached client — used by tests and after a config change."""
    global _llm
    _llm = None


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_response(raw: str) -> dict:
    """
    Parse a model's JSON output defensively.

    Even in JSON mode models occasionally wrap output in a code fence or emit a
    short preamble, so try progressively more forgiving strategies rather than
    discarding a response that is nearly valid.
    """
    if not raw:
        return {}

    text = _FENCE_RE.sub("", raw.strip()).strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost {...} span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass

    logger.warning("Unparseable LLM JSON: %s", raw[:200])
    return {}
