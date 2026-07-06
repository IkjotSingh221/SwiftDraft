"""API round-trip: POST /runs -> GET outline (paused at interrupt) -> PATCH
an edit -> POST approve -> run reaches the post-approval state, all through
the FastAPI TestClient. LLM and the retrieval store are both mocked; no
Docker/GPU/keys involved."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from draftforge.llm.base import LLMResponse, TokenUsage

WELL_FORMED_OUTLINE = json.dumps(
    [
        {"id": "introduction", "title": "Introduction", "brief": "Motivate the work.", "target_words": 600, "children": []},
        {"id": "related_work", "title": "Related Work", "target_words": 500, "children": []},
        {
            "id": "method",
            "title": "Method",
            "children": [
                {"id": "method_data", "title": "Data", "target_words": 250, "children": []},
                {"id": "method_approach", "title": "Approach", "target_words": 400, "children": []},
            ],
        },
        {"id": "results", "title": "Results", "target_words": 700, "children": []},
        {"id": "conclusion", "title": "Conclusion", "target_words": 300, "children": []},
        {"id": "references", "title": "References", "target_words": 0, "children": []},
    ]
)


class _FakeProvider:
    provider_name = "fake"

    def complete(self, messages, *, model, max_tokens, temperature, json_mode):
        return LLMResponse(
            text=WELL_FORMED_OUTLINE,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            model=model,
            provider=self.provider_name,
            cost_usd=0.0,
        )


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import draftforge.graph.continuity as continuity_mod
    import draftforge.graph.drafter as drafter_mod
    import draftforge.graph.planner as planner_mod
    import draftforge.graph.verifier as verifier_mod
    from draftforge.api.app import app

    fake = lambda role: (_FakeProvider(), "fake-model")  # noqa: E731
    monkeypatch.setattr(planner_mod, "sample_retrieval", lambda *a, **k: {})
    monkeypatch.setattr(planner_mod, "resolve_model", fake)
    # Phase 5: approving now runs the drafter + review (verifier/continuity)
    # nodes too; mock every role + the store so the whole run stays hermetic
    # and fast (no real Ollama/Qdrant, no retry backoff).
    monkeypatch.setattr(drafter_mod, "resolve_model", fake)
    monkeypatch.setattr(drafter_mod, "get_qdrant_client", lambda: object())
    monkeypatch.setattr(drafter_mod, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(verifier_mod, "resolve_model", fake)
    monkeypatch.setattr(continuity_mod, "resolve_model", fake)

    return TestClient(app)


def _wait_for_status(client: TestClient, run_id: str, targets: set[str], timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in targets:
            return status
        time.sleep(0.05)
    return status


def test_create_run_returns_queued_immediately(client: TestClient):
    resp = client.post(
        "/api/runs", json={"project_id": "proj1", "format_spec_id": "ieee_report", "run_config": {}}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] in {"queued", "planning", "awaiting_outline_approval"}
    assert body["project_id"] == "proj1"
    assert body["format_spec_id"] == "ieee_report"


def test_full_interrupt_edit_approve_round_trip(client: TestClient):
    create_resp = client.post(
        "/api/runs", json={"project_id": "proj1", "format_spec_id": "ieee_report", "run_config": {}}
    )
    run_id = create_resp.json()["id"]

    status = _wait_for_status(client, run_id, {"awaiting_outline_approval", "error"})
    assert status == "awaiting_outline_approval", f"planner did not reach the interrupt (status={status!r})"

    outline_resp = client.get(f"/api/runs/{run_id}/outline")
    assert outline_resp.status_code == 200
    outline = outline_resp.json()
    top_ids = [n["id"] for n in outline]
    assert top_ids == ["introduction", "related_work", "method", "results", "conclusion", "references"]

    method = next(n for n in outline if n["id"] == "method")
    assert [c["id"] for c in method["children"]] == ["method_data", "method_approach"]

    # Human edit: change the introduction's title and word target.
    outline[0]["title"] = "Rewritten Introduction"
    outline[0]["target_words"] = 750

    patch_resp = client.patch(f"/api/runs/{run_id}/outline", json=outline)
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched[0]["title"] == "Rewritten Introduction"
    assert patched[0]["target_words"] == 750

    # The edit must be persisted through the checkpointer, not just echoed
    # back once -- re-fetch independently to confirm.
    reget_resp = client.get(f"/api/runs/{run_id}/outline")
    reget = reget_resp.json()
    assert reget[0]["title"] == "Rewritten Introduction"
    assert reget[0]["target_words"] == 750

    approve_resp = client.post(f"/api/runs/{run_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "completed"

    final_resp = client.get(f"/api/runs/{run_id}")
    assert final_resp.json()["status"] == "completed"


def test_get_outline_before_ready_is_409(client: TestClient):
    # Unknown run entirely -> 404, not 409.
    resp = client.get("/api/runs/does-not-exist/outline")
    assert resp.status_code == 404


def test_approve_before_outline_ready_is_409(client: TestClient):
    create_resp = client.post(
        "/api/runs", json={"project_id": "proj1", "format_spec_id": "ieee_report", "run_config": {}}
    )
    run_id = create_resp.json()["id"]
    _wait_for_status(client, run_id, {"awaiting_outline_approval", "error"})

    # First approve succeeds; a second is rejected (already completed).
    first = client.post(f"/api/runs/{run_id}/approve")
    assert first.status_code == 200
    second = client.post(f"/api/runs/{run_id}/approve")
    assert second.status_code == 409


def test_unknown_format_spec_id_surfaces_as_run_error(client: TestClient):
    create_resp = client.post(
        "/api/runs", json={"project_id": "proj1", "format_spec_id": "not-a-real-spec", "run_config": {}}
    )
    run_id = create_resp.json()["id"]
    status = _wait_for_status(client, run_id, {"awaiting_outline_approval", "error"})
    assert status == "error"
    body = client.get(f"/api/runs/{run_id}").json()
    assert "not-a-real-spec" in (body["error"] or "")


def test_get_unknown_run_404s(client: TestClient):
    assert client.get("/api/runs/nonexistent").status_code == 404
    assert client.patch("/api/runs/nonexistent/outline", json=[]).status_code == 404
    assert client.post("/api/runs/nonexistent/approve").status_code == 404
