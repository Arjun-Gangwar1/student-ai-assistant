"""
Provider-abstracted LLM client.
Swap Groq → DeepInfra / Together / Cerebras by changing LLM_PROVIDER env var.
All providers expose the same OpenAI-compatible /chat/completions endpoint.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)


class BaseLLMClient(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], json_mode: bool = False, temperature: float = 0.1) -> str:
        ...


class GroqClient(BaseLLMClient):
    MODEL = "llama-3.1-8b-instant"

    def __init__(self):
        self._client = Groq(api_key=settings.groq_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def chat(self, messages: list[dict], json_mode: bool = False, temperature: float = 0.1) -> str:
        kwargs: dict[str, Any] = {
            "model": self.MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1024,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""


class DeepInfraClient(BaseLLMClient):
    """Fallback — same Llama model via DeepInfra OpenAI-compatible API."""
    MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"

    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(
            api_key=settings.deepinfra_api_key,
            base_url="https://api.deepinfra.com/v1/openai",
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def chat(self, messages: list[dict], json_mode: bool = False, temperature: float = 0.1) -> str:
        kwargs: dict[str, Any] = {
            "model": self.MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1024,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""


def get_llm_client() -> BaseLLMClient:
    """Factory — swap provider without touching call sites."""
    if settings.deepinfra_api_key and settings.app_env == "fallback":
        logger.info("Using DeepInfra LLM client")
        return DeepInfraClient()
    return GroqClient()


# Singleton
_llm: BaseLLMClient | None = None


def llm() -> BaseLLMClient:
    global _llm
    if _llm is None:
        _llm = get_llm_client()
    return _llm


def parse_json_response(raw: str) -> dict:
    """Safely parse LLM JSON output, even if it has markdown fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse LLM JSON: {raw[:200]}")
        return {}
