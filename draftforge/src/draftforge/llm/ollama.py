"""Ollama provider — local, OpenAI-compatible chat endpoint, no API key."""

from __future__ import annotations

from draftforge.llm.base import LLMProvider
from draftforge.llm.openai_compat import OpenAICompatMixin

DEFAULT_BASE_URL = "http://localhost:11434/v1"


class OllamaProvider(OpenAICompatMixin, LLMProvider):
    provider_name = "ollama"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("base_url", DEFAULT_BASE_URL)
        kwargs.setdefault("api_key", None)
        super().__init__(**kwargs)
