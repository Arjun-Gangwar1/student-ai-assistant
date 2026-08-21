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
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Any provider-side failure, normalised across SDKs."""


class BaseLLMClient(ABC):
    name: str
    model: str

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        json_mode: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> str:
        ...


class GroqClient(BaseLLMClient):
    name = "groq"
    model = "llama-3.1-8b-instant"

    def __init__(self) -> None:
        from groq import AsyncGroq

        if not settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not set")
        self._client = AsyncGroq(api_key=settings.groq_api_key, timeout=30.0)

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
            raise LLMError(f"groq: {type(exc).__name__}: {exc}") from exc
        return resp.choices[0].message.content or ""


class DeepInfraClient(BaseLLMClient):
    """Fallback — same Llama model, OpenAI-compatible API."""

    name = "deepinfra"
    model = "meta-llama/Meta-Llama-3.1-8B-Instruct"

    def __init__(self) -> None:
        from openai import AsyncOpenAI

        if not settings.deepinfra_api_key:
            raise LLMError("DEEPINFRA_API_KEY is not set")
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
