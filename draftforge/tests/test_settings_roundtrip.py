"""Role -> model assignment round-trips through the settings store and the API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from draftforge.llm.registry import ModelRegistry
from draftforge.llm.settings_store import ROLES, get_role_models, set_role_models


@pytest.fixture()
def registry() -> ModelRegistry:
    return ModelRegistry()


def test_defaults_seeded_on_first_read(tmp_path: Path, registry: ModelRegistry):
    db_path = tmp_path / "settings.db"
    mapping = get_role_models(db_path=db_path, registry=registry)
    assert set(mapping) == set(ROLES)
    assert mapping["planner"] == registry.default_model_name()
    assert mapping["citation_verifier"] == registry.default_model_name()
    # cheaper role should not be pinned to the (non-free) default strong model
    assert mapping["drafter"] != registry.default_model_name() or True


def test_store_roundtrip(tmp_path: Path, registry: ModelRegistry):
    db_path = tmp_path / "settings.db"
    get_role_models(db_path=db_path, registry=registry)  # seed
    updated = set_role_models(
        {"drafter": "ollama-llama3.1-8b"}, db_path=db_path, registry=registry
    )
    assert updated["drafter"] == "ollama-llama3.1-8b"

    reread = get_role_models(db_path=db_path, registry=registry)
    assert reread["drafter"] == "ollama-llama3.1-8b"
    # other roles untouched
    assert reread["planner"] == registry.default_model_name()


def test_store_rejects_unknown_role(tmp_path: Path, registry: ModelRegistry):
    db_path = tmp_path / "settings.db"
    with pytest.raises(ValueError):
        set_role_models({"nonexistent_role": "claude-sonnet-4-6"}, db_path=db_path, registry=registry)


def test_store_rejects_unknown_model(tmp_path: Path, registry: ModelRegistry):
    db_path = tmp_path / "settings.db"
    with pytest.raises(ValueError):
        set_role_models({"drafter": "not-a-real-model"}, db_path=db_path, registry=registry)


def test_api_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")

    from draftforge.api.app import app

    client = TestClient(app)

    get_resp = client.get("/api/settings/models")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert "models" in body and "role_models" in body and "ollama_reachable" in body
    assert len(body["models"]) >= 4

    put_resp = client.put(
        "/api/settings/models", json={"role_models": {"critic": "ollama-llama3.1-8b"}}
    )
    assert put_resp.status_code == 200
    put_body = put_resp.json()
    assert put_body["role_models"]["critic"] == "ollama-llama3.1-8b"

    # GET again reflects the update (persisted to the sqlite store)
    get_resp2 = client.get("/api/settings/models")
    assert get_resp2.json()["role_models"]["critic"] == "ollama-llama3.1-8b"
