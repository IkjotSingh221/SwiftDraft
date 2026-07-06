"""models.yaml loads correctly; resolve_model resolves provider+model;
PROVIDER_CLASSES is the only switch point for adding a provider.
"""

from __future__ import annotations

import pytest

from draftforge.llm.anthropic import AnthropicProvider
from draftforge.llm.base import LLMProvider
from draftforge.llm.registry import PROVIDER_CLASSES, ModelRegistry


@pytest.fixture()
def registry() -> ModelRegistry:
    return ModelRegistry()


def test_models_yaml_loads(registry: ModelRegistry):
    models = registry.list_models()
    assert len(models) >= 4
    providers = {m.provider for m in models}
    assert providers == {"anthropic", "gemini", "openrouter", "ollama"}


def test_default_model_is_flagged(registry: ModelRegistry):
    default_name = registry.default_model_name()
    info = registry.get_model_info(default_name)
    assert info.default is True
    assert info.provider == "anthropic"


def test_get_provider_for_constructs_and_caches(registry: ModelRegistry):
    provider = registry.get_provider_for("claude-sonnet-4-6")
    assert isinstance(provider, AnthropicProvider)
    # cached: same instance on second call
    assert registry.get_provider_for("claude-sonnet-4-6") is provider


def test_local_model_has_no_required_api_key(registry: ModelRegistry):
    info = registry.get_model_info("ollama-llama3.1-8b")
    assert info.local is True
    assert info.api_key_env is None
    assert registry.api_key_present("ollama-llama3.1-8b") is True


def test_api_key_present_reflects_env(monkeypatch, registry: ModelRegistry):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert registry.api_key_present("claude-sonnet-4-6") is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    assert registry.api_key_present("claude-sonnet-4-6") is True


def test_unknown_model_raises(registry: ModelRegistry):
    with pytest.raises(KeyError):
        registry.get_model_info("does-not-exist")


def test_provider_classes_is_the_only_switch_point():
    """Contract: every provider entry point is reachable only via PROVIDER_CLASSES.

    Adding a new provider should require exactly one new file + one entry
    here; nothing else in the registry should need to change.
    """
    assert set(PROVIDER_CLASSES) == {"anthropic", "openrouter", "ollama", "gemini"}
    for cls in PROVIDER_CLASSES.values():
        assert issubclass(cls, LLMProvider)


def test_resolve_model_helper(monkeypatch, registry: ModelRegistry):
    from draftforge.llm.settings_store import resolve_model

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    provider, model_name = resolve_model("planner", db_path=":memory:", registry=registry)
    assert isinstance(provider, LLMProvider)
    assert model_name == registry.default_model_name()
