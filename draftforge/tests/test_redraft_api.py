"""Phase 5 API contract (hermetic): GET /review, the human-triggered
POST /sections/{id}/redraft, and the crash-recovery POST /resume, all through
the FastAPI TestClient. Every LLM role is mocked to a fast fake so a full run
completes without network."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from draftforge.llm.base import LLMResponse, TokenUsage

WELL_FORMED_OUTLINE = json.dumps(
    [
        {"id": "introduction", "title": "Introduction", "brief": "Motivate.", "target_words": 600, "children": []},
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
            model=model, provider=self.provider_name, cost_usd=0.0,
        )


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import draftforge.graph.planner as planner_mod
    import draftforge.graph.drafter as drafter_mod
    import draftforge.graph.verifier as verifier_mod
    import draftforge.graph.continuity as continuity_mod
    from draftforge.api.app import app

    fake = lambda role: (_FakeProvider(), "fake-model")  # noqa: E731
    monkeypatch.setattr(planner_mod, "sample_retrieval", lambda *a, **k: {})
    monkeypatch.setattr(planner_mod, "resolve_model", fake)
    monkeypatch.setattr(drafter_mod, "resolve_model", fake)
    monkeypatch.setattr(drafter_mod, "get_qdrant_client", lambda: object())
    monkeypatch.setattr(drafter_mod, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(verifier_mod, "resolve_model", fake)
    monkeypatch.setattr(continuity_mod, "resolve_model", fake)
    return TestClient(app)


def _reach(client: TestClient, run_id: str, targets: set[str], timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{run_id}").json()["status"]
        if status in targets:
            return status
        time.sleep(0.05)
    return status


def _start_run(client: TestClient) -> str:
    resp = client.post(
        "/api/runs", json={"project_id": "proj1", "format_spec_id": "ieee_report", "run_config": {}}
    )
    return resp.json()["id"]


def test_review_empty_before_verification(client: TestClient):
    run_id = _start_run(client)
    _reach(client, run_id, {"awaiting_outline_approval", "error"})
    resp = client.get(f"/api/runs/{run_id}/review")
    assert resp.status_code == 200
    assert resp.json() == {"sections": [], "continuity": []}


def test_redraft_and_resume_unknown_run_are_404(client: TestClient):
    assert client.post("/api/runs/nope/sections/introduction/redraft").status_code == 404
    assert client.post("/api/runs/nope/resume").status_code == 404


def test_resume_before_approval_is_409(client: TestClient):
    run_id = _start_run(client)
    _reach(client, run_id, {"awaiting_outline_approval", "error"})
    resp = client.post(f"/api/runs/{run_id}/resume")
    assert resp.status_code == 409


def test_full_run_then_review_and_redraft_round_trip(client: TestClient):
    run_id = _start_run(client)
    assert _reach(client, run_id, {"awaiting_outline_approval", "error"}) == "awaiting_outline_approval"

    approve = client.post(f"/api/runs/{run_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "completed"

    # Review is populated once the review node has run.
    review = client.get(f"/api/runs/{run_id}/review").json()
    assert len(review["sections"]) >= 1

    # Pick a real leaf id from the run's own sections list, then redraft it.
    sections = client.get(f"/api/runs/{run_id}/sections").json()
    assert sections
    section_id = sections[0]["section_id"]

    redraft = client.post(f"/api/runs/{run_id}/sections/{section_id}/redraft", json={"feedback": "tighten it"})
    assert redraft.status_code == 200
    body = redraft.json()
    assert body["section_id"] == section_id
    assert body["status"] in {"done", "flagged"}

    # The refreshed section review is persisted (single entry per section).
    review2 = client.get(f"/api/runs/{run_id}/review").json()
    matching = [s for s in review2["sections"] if s["section_id"] == section_id]
    assert len(matching) == 1
