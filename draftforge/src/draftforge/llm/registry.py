"""Model registry: loads `config/models.yaml` and constructs provider instances.

Data-driven by design: `PROVIDER_CLASSES` is the ONLY place provider classes
are referenced. Adding a new provider means adding one new provider file plus
one entry in this dict — nothing else in the codebase changes.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from draftforge.llm.anthropic import AnthropicProvider
from draftforge.llm.base import LLMProvider
from draftforge.llm.gemini import GeminiProvider
from draftforge.llm.ollama import OllamaProvider
from draftforge.llm.openrouter import OpenRouterProvider

PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
    "gemini": GeminiProvider,
}

DEFAULT_MODELS_YAML = Path(__file__).resolve().parents[3] / "config" / "models.yaml"


class ModelInfo(BaseModel):
    name: str
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    cost_per_mtok_in: float = 0.0
    cost_per_mtok_out: float = 0.0
    local: bool = False
    default: bool = False
    note: str | None = None


class ModelRegistry:
    def __init__(self, models_yaml_path: str | Path | None = None) -> None:
        self.path = Path(models_yaml_path) if models_yaml_path else DEFAULT_MODELS_YAML
        self._models: dict[str, ModelInfo] = {}
        self._provider_cache: dict[str, LLMProvider] = {}
        self._load()

    def _load(self) -> None:
        with open(self.path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        entries = raw.get("models", [])
        self._models = {}
        for entry in entries:
            info = ModelInfo(**entry)
            self._models[info.name] = info

    def reload(self) -> None:
        self._provider_cache.clear()
        self._load()

    def list_models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def get_model_info(self, model_name: str) -> ModelInfo:
        if model_name not in self._models:
            raise KeyError(f"Unknown model name: {model_name!r}")
        return self._models[model_name]

    def api_key_present(self, model_name: str) -> bool:
        info = self.get_model_info(model_name)
        if info.api_key_env is None:
            return True  # no key required (e.g. local Ollama)
        return bool(os.environ.get(info.api_key_env))

    def get_provider_for(self, model_name: str) -> LLMProvider:
        """Construct (or return the cached) provider instance for a model entry."""
        if model_name in self._provider_cache:
            return self._provider_cache[model_name]

        info = self.get_model_info(model_name)
        provider_cls = PROVIDER_CLASSES.get(info.provider)
        if provider_cls is None:
            raise KeyError(
                f"No provider class registered for provider={info.provider!r}. "
                f"Known providers: {sorted(PROVIDER_CLASSES)}"
            )

        api_key = os.environ.get(info.api_key_env) if info.api_key_env else None
        kwargs: dict = dict(
            api_key=api_key,
            cost_per_mtok_in=info.cost_per_mtok_in,
            cost_per_mtok_out=info.cost_per_mtok_out,
        )
        if info.base_url:
            kwargs["base_url"] = info.base_url

        provider = provider_cls(**kwargs)
        self._provider_cache[model_name] = provider
        return provider

    def default_model_name(self) -> str:
        for info in self._models.values():
            if info.default:
                return info.name
        # fall back to the first entry if nothing is flagged default
        return next(iter(self._models))


@lru_cache(maxsize=1)
def get_registry() -> ModelRegistry:
    return ModelRegistry()
