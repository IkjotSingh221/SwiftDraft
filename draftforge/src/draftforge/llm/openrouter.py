"""OpenRouter provider — OpenAI-compatible chat endpoint."""

from __future__ import annotations

from draftforge.llm.base import LLMProvider
from draftforge.llm.openai_compat import OpenAICompatMixin

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(OpenAICompatMixin, LLMProvider):
    provider_name = "openrouter"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("base_url", DEFAULT_BASE_URL)
        super().__init__(**kwargs)
