"""Each provider is mockable behind the shared LLMProvider interface.

We mock the HTTP/SDK layer for each provider and assert `complete()` returns
an `LLMResponse` with usage + a correctly computed cost. We also assert the
base class's retry behavior works, independent of any specific provider.
"""

from __future__ import annotations

import types

import pytest

from draftforge.llm.base import LLMError, LLMMessage, LLMProvider, TokenUsage


# ---------------------------------------------------------------------------
# Base class retry behavior (provider-agnostic)
# ---------------------------------------------------------------------------


class _FlakyProvider(LLMProvider):
    provider_name = "flaky"

    def __init__(self, *, fail_times: int, **kwargs):
        super().__init__(**kwargs)
        self.fail_times = fail_times
        self.calls = 0

    def _raw_complete(self, messages, *, model, max_tokens, temperature, json_mode):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("simulated transient failure")
        return "ok", TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)


def test_base_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("draftforge.llm.base.time.sleep", lambda _s: None)
    provider = _FlakyProvider(
        fail_times=2,
        max_retries=3,
        backoff_base=0.01,
        cost_per_mtok_in=1.0,
        cost_per_mtok_out=2.0,
    )
    resp = provider.complete(
        [LLMMessage(role="user", content="hi")], model="m", max_tokens=10
    )
    assert provider.calls == 3
    assert resp.text == "ok"
    assert resp.usage.total_tokens == 150
    assert resp.cost_usd == pytest.approx(100 / 1_000_000 * 1.0 + 50 / 1_000_000 * 2.0)


def test_base_retries_exhausted_raises(monkeypatch):
    monkeypatch.setattr("draftforge.llm.base.time.sleep", lambda _s: None)
    provider = _FlakyProvider(fail_times=10, max_retries=3, backoff_base=0.01)
    with pytest.raises(LLMError):
        provider.complete([LLMMessage(role="user", content="hi")], model="m", max_tokens=10)
    assert provider.calls == 3


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


def test_anthropic_provider(monkeypatch):
    from draftforge.llm import anthropic as anthropic_module

    class FakeBlock:
        type = "text"

        def __init__(self, text):
            self.text = text

    class FakeUsage:
        input_tokens = 20
        output_tokens = 10

    class FakeMessage:
        content = [FakeBlock("hello world")]
        usage = FakeUsage()

    class FakeMessages:
        def create(self, **kwargs):
            return FakeMessage()

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic_module.anthropic, "Anthropic", FakeClient)

    provider = anthropic_module.AnthropicProvider(
        api_key="fake", cost_per_mtok_in=3.0, cost_per_mtok_out=15.0
    )
    resp = provider.complete(
        [LLMMessage(role="user", content="hi")],
        model="claude-sonnet-4-6",
        max_tokens=100,
    )
    assert resp.text == "hello world"
    assert resp.usage.prompt_tokens == 20
    assert resp.usage.completion_tokens == 10
    assert resp.provider == "anthropic"
    assert resp.cost_usd == pytest.approx(20 / 1_000_000 * 3.0 + 10 / 1_000_000 * 15.0)


# ---------------------------------------------------------------------------
# OpenRouterProvider / OllamaProvider (shared OpenAICompatMixin)
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHTTPClient:
    last_request: dict = {}

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        _FakeHTTPClient.last_request = {"url": url, "headers": headers, "json": json}
        return _FakeHTTPResponse(
            {
                "choices": [{"message": {"content": "generated text"}}],
                "usage": {
                    "prompt_tokens": 30,
                    "completion_tokens": 15,
                    "total_tokens": 45,
                },
            }
        )


def test_openrouter_provider(monkeypatch):
    from draftforge.llm import openrouter as openrouter_module

    monkeypatch.setattr(
        "draftforge.llm.openai_compat.httpx.Client", _FakeHTTPClient
    )
    provider = openrouter_module.OpenRouterProvider(
        api_key="fake-key", cost_per_mtok_in=0.35, cost_per_mtok_out=0.4
    )
    resp = provider.complete(
        [LLMMessage(role="user", content="hi")],
        model="meta-llama/llama-3.1-70b-instruct",
        max_tokens=100,
    )
    assert resp.text == "generated text"
    assert resp.usage.total_tokens == 45
    assert resp.provider == "openrouter"
    assert "Authorization" in _FakeHTTPClient.last_request["headers"]


def test_ollama_provider_no_api_key(monkeypatch):
    from draftforge.llm import ollama as ollama_module

    monkeypatch.setattr(
        "draftforge.llm.openai_compat.httpx.Client", _FakeHTTPClient
    )
    provider = ollama_module.OllamaProvider()
    resp = provider.complete(
        [LLMMessage(role="user", content="hi")], model="llama3.1:8b", max_tokens=100
    )
    assert resp.text == "generated text"
    assert resp.cost_usd == 0.0
    assert "Authorization" not in _FakeHTTPClient.last_request["headers"]


# ---------------------------------------------------------------------------
# GeminiProvider
# ---------------------------------------------------------------------------


def test_gemini_provider(monkeypatch):
    from draftforge.llm import gemini as gemini_module

    class FakeUsageMeta:
        prompt_token_count = 12
        candidates_token_count = 8
        total_token_count = 20

    class FakeResponse:
        text = "gemini output"
        usage_metadata = FakeUsageMeta()

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    provider = gemini_module.GeminiProvider(
        api_key="fake", cost_per_mtok_in=1.25, cost_per_mtok_out=10.0
    )
    resp = provider.complete(
        [LLMMessage(role="user", content="hi")], model="gemini-2.5-pro", max_tokens=100
    )
    assert resp.text == "gemini output"
    assert resp.usage.total_tokens == 20
    assert resp.provider == "gemini"
    assert resp.cost_usd == pytest.approx(12 / 1_000_000 * 1.25 + 8 / 1_000_000 * 10.0)
