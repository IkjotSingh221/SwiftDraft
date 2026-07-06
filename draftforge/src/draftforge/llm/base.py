"""Common LLM provider interface.

Every provider subclasses `LLMProvider` and implements only `_raw_complete`.
Retries, timeouts, and cost accounting all live here so a new provider file
never has to re-implement them — see the module docstrings in
`llm/registry.py` for the "one file + one registry entry" contract.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    text: str
    usage: TokenUsage
    model: str
    provider: str
    cost_usd: float


class LLMError(RuntimeError):
    """Raised when a provider call fails after exhausting all retries."""


class LLMProvider(ABC):
    """Abstract base for all LLM providers.

    Construction parameters carry everything needed to make calls AND to
    account for cost, so `complete()` keeps the exact signature the rest of
    the codebase depends on:

        complete(messages, *, model, max_tokens, temperature, json_mode)
    """

    provider_name: str = "base"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        cost_per_mtok_in: float = 0.0,
        cost_per_mtok_out: float = 0.0,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.cost_per_mtok_in = cost_per_mtok_in
        self.cost_per_mtok_out = cost_per_mtok_out
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    @abstractmethod
    def _raw_complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, TokenUsage]:
        """Provider-specific call. Returns (text, usage); raises on failure."""

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Retry/timeout/cost wrapper around `_raw_complete`.

        Retries with exponential backoff (`backoff_base * 2**attempt`) up to
        `max_retries` times, then raises `LLMError`.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                text, usage = self._raw_complete(
                    messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                )
                cost = self._compute_cost(usage)
                return LLMResponse(
                    text=text,
                    usage=usage,
                    model=model,
                    provider=self.provider_name,
                    cost_usd=cost,
                )
            except Exception as exc:  # noqa: BLE001 - intentionally broad; retried
                last_exc = exc
                if attempt == self.max_retries:
                    break
                sleep_s = self.backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    "%s.complete attempt %d/%d failed: %s (retrying in %.1fs)",
                    self.provider_name,
                    attempt,
                    self.max_retries,
                    exc,
                    sleep_s,
                )
                time.sleep(sleep_s)
        raise LLMError(
            f"{self.provider_name} complete() failed after {self.max_retries} attempts"
        ) from last_exc

    def _compute_cost(self, usage: TokenUsage) -> float:
        return (
            usage.prompt_tokens / 1_000_000 * self.cost_per_mtok_in
            + usage.completion_tokens / 1_000_000 * self.cost_per_mtok_out
        )
