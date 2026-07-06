"""GET /api/runs/{id}/events (SSE): a mocked, fully-drafted run's event
stream delivers per-section status transitions (queued -> drafting ->
critiquing -> done) plus a running token/cost counter, and the underlying
generator (`api.sse.tail_run_events`) replays a run's full event history from
the start -- the mechanism the dashboard relies on to reattach after a
resume. Fully hermetic: planner + drafter/critic LLM calls and hybrid_search
are all mocked; no Docker/GPU/keys."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from draftforge.api import sse
from draftforge.llm.base import LLMResponse, TokenUsage

WELL_FORMED_OUTLINE = json.dumps(
    [
        {"id": "introduction", "title": "Introduction", "brief": "Motivate.", "target_words": 600, "children": []},
        {"id": "conclusion", "title": "Conclusion", "brief": "Wrap up.", "target_words": 300, "children": []},
    ]
)


class _FakePlannerProvider:
    provider_name = "fake"

    def complete(self, messages, *, model, max_tokens, temperature, json_mode):
        return LLMResponse(
            text=WELL_FORMED_OUTLINE,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            model=model,
            provider=self.provider_name,
            cost_usd=0.0,
        )


class _FakeDrafterProvider:
    provider_name = "fake"

    def complete(self, messages, *, model, max_tokens, temperature, json_mode):
        system = messages[0].content
        if not json_mode:
            text = "a query"
        elif "grade retrieved" in system:
            text = json.dumps({"grades": []})  # no hits anyway -- nothing to grade
        elif "critical reviewer" in system:
            text = json.dumps({"verdict": "pass", "feedback": ""})
        else:
            text = json.dumps({"draft": "Body prose, no citations.", "summary": "A summary.", "claims": []})
        return LLMResponse(
            text=text,
            usage=TokenUsage(prompt_tokens=20, completion_tokens=30, total_tokens=50),
            model=model,
            provider=self.provider_name,
            cost_usd=0.002,
        )


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import draftforge.graph.drafter as drafter_mod
    import draftforge.graph.planner as planner_mod
    from draftforge.api.app import app

    monkeypatch.setattr(planner_mod, "sample_retrieval", lambda *a, **k: {})
    monkeypatch.setattr(planner_mod, "resolve_model", lambda role: (_FakePlannerProvider(), "fake-planner"))
    monkeypatch.setattr(drafter_mod, "resolve_model", lambda role: (_FakeDrafterProvider(), "fake-drafter"))
    monkeypatch.setattr(drafter_mod, "get_qdrant_client", lambda: object())
    monkeypatch.setattr(drafter_mod, "hybrid_search", lambda *a, **k: [])

    return TestClient(app)


def _run_to_completion(client: TestClient) -> str:
    create_resp = client.post(
        "/api/runs", json={"project_id": "proj1", "format_spec_id": "ieee_report", "run_config": {}}
    )
    run_id = create_resp.json()["id"]

    import time

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if client.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_outline_approval":
            break
        time.sleep(0.02)

    approve_resp = client.post(f"/api/runs/{run_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "completed"
    return run_id


def _parse_sse(body: str) -> list[dict]:
    """Minimal SSE frame parser: splits on blank lines, extracts event/data."""
    frames: list[dict] = []
    body = body.replace("\r\n", "\n")
    for block in body.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        event_name = "message"
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if data_lines:
            frames.append({"event": event_name, "data": json.loads("".join(data_lines))})
    return frames


def test_events_endpoint_delivers_section_status_and_token_usage(client: TestClient):
    run_id = _run_to_completion(client)

    resp = client.get(f"/api/runs/{run_id}/events")
    assert resp.status_code == 200
    frames = _parse_sse(resp.text)

    section_events = [f for f in frames if f["event"] == "section_status"]
    statuses_for_intro = [f["data"]["status"] for f in section_events if f["data"].get("section_id") == "introduction"]
    # queued -> drafting -> critiquing -> done, in that order.
    assert statuses_for_intro == ["queued", "drafting", "critiquing", "done"]

    token_events = [f for f in frames if f["event"] == "token_usage"]
    assert token_events, "expected at least one token_usage event"
    assert token_events[-1]["data"]["cumulative_tokens"] > 0
    assert token_events[-1]["data"]["cumulative_cost_usd"] > 0
    # cumulative counters are non-decreasing across the stream.
    cumulative = [f["data"]["cumulative_tokens"] for f in token_events]
    assert cumulative == sorted(cumulative)

    run_status_events = [f["data"]["status"] for f in frames if f["event"] == "run_status"]
    assert run_status_events[0] == "drafting"
    assert run_status_events[-1] == "completed"


def test_events_endpoint_404s_for_unknown_run(client: TestClient):
    resp = client.get("/api/runs/does-not-exist/events")
    assert resp.status_code == 404


def test_tail_run_events_replays_full_history_on_reconnect(tmp_path):
    """A second, independent tailer call (simulating the dashboard
    reconnecting after a resume) sees the entire event history again from
    the start -- the events.jsonl file itself is the replay buffer."""
    from draftforge.graph.drafter import emit_event

    events_path = tmp_path / "events.jsonl"
    emit_event("runX", "section_status", events_path=events_path, section_id="a", status="queued")
    emit_event("runX", "section_status", events_path=events_path, section_id="a", status="done")

    async def _collect():
        return [
            e
            async for e in sse.tail_run_events(
                "runX", events_path=events_path, run_status_fn=lambda _rid: "completed"
            )
        ]

    first = asyncio.run(_collect())
    second = asyncio.run(_collect())

    assert [e["data"]["status"] for e in first] == ["queued", "done"]
    assert first == second  # reconnecting replays the identical history
