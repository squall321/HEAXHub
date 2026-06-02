"""LLM provider adapters (Anthropic, OpenAI, local gateway, stub).

All providers expose ``complete(*, system: str, user: str) -> str`` and are
expected to return a raw JSON string. JSON parsing/validation happens in
:mod:`app.services.manifest_llm`.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)


class BaseLLMProvider:
    """Abstract base. Implementations are synchronous (Celery-friendly)."""

    name: str = "base"

    def complete(self, *, system: str, user: str) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"
    _ENDPOINT = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"

    def __init__(self, *, api_key: str, model: str, max_tokens: int, temperature: float) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def complete(self, *, system: str, user: str) -> str:
        if not self._api_key:
            raise RuntimeError("LLM_API_KEY is empty for Anthropic provider")
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._API_VERSION,
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(self._ENDPOINT, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()
        # Anthropic returns content as a list of blocks; concat all "text" blocks.
        parts = []
        for block in body.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts).strip()


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class OpenAIProvider(BaseLLMProvider):
    name = "openai"
    _ENDPOINT = "https://api.openai.com/v1/chat/completions"

    def __init__(self, *, api_key: str, model: str, max_tokens: int, temperature: float) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def complete(self, *, system: str, user: str) -> str:
        if not self._api_key:
            raise RuntimeError("LLM_API_KEY is empty for OpenAI provider")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(self._ENDPOINT, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()
        try:
            return body["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected OpenAI response shape: {body!r}") from exc


# ---------------------------------------------------------------------------
# Local gateway
# ---------------------------------------------------------------------------


class LocalLLMProvider(BaseLLMProvider):
    name = "local"

    def __init__(self, *, base_url: str, model: str, max_tokens: int, temperature: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def complete(self, *, system: str, user: str) -> str:
        if not self._base_url:
            raise RuntimeError("LLM_LOCAL_ENDPOINT is empty for local provider")
        payload = {
            "system": system,
            "user": user,
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{self._base_url}/complete", json=payload)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and "text" in body:
            return str(body["text"]).strip()
        # Fall back to raw response (assume the gateway already returned JSON-as-text).
        return resp.text.strip()


# ---------------------------------------------------------------------------
# Stub provider (deterministic — used when LLM_API_KEY is empty)
# ---------------------------------------------------------------------------


class StubLLMProvider(BaseLLMProvider):
    """Returns a deterministic JSON shell so the pipeline works without API keys."""

    name = "stub"

    def __init__(self, *, model: str) -> None:
        self._model = model

    def complete(self, *, system: str, user: str) -> str:  # noqa: ARG002
        # The real shape is filled in by manifest_llm._stub_result() — we just
        # return a placeholder so any direct caller of complete() still works.
        return json.dumps(
            {
                "manifest_draft": {},
                "confidence": {},
                "open_questions": [],
                "developer_change_request": {
                    "summary": "stub provider — no LLM call made",
                    "required_files": [],
                    "suggested_files": [],
                    "rationale": "LLM_API_KEY not configured; using stub.",
                },
            }
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_provider() -> BaseLLMProvider:
    settings = get_settings()
    provider = (settings.llm_provider or "anthropic").lower()
    if provider == "stub":
        return StubLLMProvider(model=settings.llm_model)
    if provider == "anthropic":
        if not settings.llm_api_key:
            logger.info("LLM_API_KEY missing — falling back to stub provider")
            return StubLLMProvider(model=settings.llm_model)
        return AnthropicProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
    if provider == "openai":
        if not settings.llm_api_key:
            logger.info("LLM_API_KEY missing — falling back to stub provider")
            return StubLLMProvider(model=settings.llm_model)
        return OpenAIProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
    if provider == "local":
        if not settings.llm_local_endpoint:
            logger.info("LLM_LOCAL_ENDPOINT missing — falling back to stub provider")
            return StubLLMProvider(model=settings.llm_model)
        return LocalLLMProvider(
            base_url=settings.llm_local_endpoint,
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
    raise ValueError(f"Unknown LLM provider: {provider}")
