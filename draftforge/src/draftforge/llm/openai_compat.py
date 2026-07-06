"""Mixin for providers that speak an OpenAI-compatible /chat/completions API.

Shared by OpenRouter and Ollama. A provider using this mixin must define
`self.base_url` (required) and may define `self.api_key` (optional — omitted
from headers when None, e.g. for a local Ollama server).
"""

from __future__ import annotations

import httpx

from draftforge.llm.base import LLMMessage, TokenUsage


class OpenAICompatMixin:
    """Implements `_raw_complete` against an OpenAI-compatible chat endpoint."""

    def _raw_complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, TokenUsage]:
        base_url = self.base_url.rstrip("/")  # type: ignore[attr-defined]
        url = f"{base_url}/chat/completions"

        headers = {"Content-Type": "application/json"}
        api_key = getattr(self, "api_key", None)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        timeout = getattr(self, "timeout", 60.0)
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
        usage_raw = data.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
            total_tokens=usage_raw.get(
                "total_tokens",
                usage_raw.get("prompt_tokens", 0) + usage_raw.get("completion_tokens", 0),
            ),
        )
        return text, usage
