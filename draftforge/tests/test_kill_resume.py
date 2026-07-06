"""Phase 5 kill-and-resume drill (spec.md non-negotiable #5: "everything
resumes").

We simulate a crash INSIDE the review node: the drafter node has already run
and been checkpointed by SqliteSaver, then review raises. The recovery path
`build_graph.resume_run` re-invokes from the on-disk checkpoint and completes
the run by running ONLY the review node -- the (expensive) drafter fan-out is
NOT re-executed. Asserting the drafter call count stays at 1 across the crash
is the concrete proof that work already checkpointed at section N is not
redone on resume.

A true SIGKILL of a subprocess is neither hermetic nor fast; crashing a node
mid-`graph.invoke` and recovering from the same SqliteSaver DB exercises the
exact same checkpoint/resume machinery (see DECISIONS.md)."""

from __future__ import annotations

import json

import pytest

from draftforge.graph import build_graph, planner as planner_mod
from draftforge.llm.base import LLMResponse, TokenUsage

WELL_FORMED_OUTLINE = json.dumps(
    [
        {"id": "introduction", "title": "Introduction", "target_words": 600, "children": []},
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


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def _mock_planner(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(planner_mod, "sample_retrieval", lambda *a, **k: {})
    monkeypatch.setattr(planner_mod, "resolve_model", lambda role: (_FakeProvider(), "fake-model"))


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "runs.sqlite"


def test_crash_in_review_resumes_without_redrafting(db_path, monkeypatch):
    draft_calls = {"n": 0}
    review_state = {"fail_once": True}

    def fake_drafter(state, **_kw):
        draft_calls["n"] += 1
        return {
            "status": "verifying",
            "section_status": {"introduction": {"status": "done", "tokens_used": 0, "cost_usd": 0.0}},
            "document_state": state.get("document_state"),
        }

    def flaky_review(state, **_kw):
        if review_state["fail_once"]:
            review_state["fail_once"] = False
            raise RuntimeError("simulated crash mid-review")
        return {
            "status": "completed",
            "section_status": state.get("section_status", {}),
            "document_state": state.get("document_state"),
        }

    monkeypatch.setattr(build_graph, "drafter_fanout_node", fake_drafter)
    monkeypatch.setattr(build_graph, "review_node", flaky_review)

    run_id = build_graph.create_run("proj1", "ieee_report", {})
    build_graph.run_planner_to_interrupt(run_id, db_path=db_path)

    # Approve -> the drafter runs and is checkpointed, then review crashes.
    with pytest.raises(RuntimeError):
        build_graph.resume(run_id, db_path=db_path)

    assert draft_calls["n"] == 1
    crashed = build_graph.get_state(run_id, db_path=db_path)
    assert crashed["status"] == "verifying"  # drafter's checkpoint survived the crash

    # Recover from the checkpoint: only review re-runs, the drafter does not.
    resumed = build_graph.resume_run(run_id, db_path=db_path)
    assert resumed["status"] == "completed"
    assert draft_calls["n"] == 1  # NOT re-drafted -- resumed from section N's checkpoint
    assert build_graph.get_run_meta(run_id)["status"] == "completed"


def test_resume_run_before_approval_is_rejected(db_path):
    run_id = build_graph.create_run("proj1", "ieee_report", {})
    build_graph.run_planner_to_interrupt(run_id, db_path=db_path)
    # Never approved: the checkpoint is parked at the interrupt and the
    # registry hasn't moved past approval -> resume_run must refuse.
    with pytest.raises(ValueError):
        build_graph.resume_run(run_id, db_path=db_path)


def test_resume_run_on_completed_run_is_a_noop(db_path, monkeypatch):
    monkeypatch.setattr(build_graph, "drafter_fanout_node",
                        lambda state, **k: {"status": "verifying", "section_status": {}, "document_state": state.get("document_state")})
    monkeypatch.setattr(build_graph, "review_node",
                        lambda state, **k: {"status": "completed", "section_status": {}, "document_state": state.get("document_state")})
    run_id = build_graph.create_run("proj1", "ieee_report", {})
    build_graph.run_planner_to_interrupt(run_id, db_path=db_path)
    build_graph.resume(run_id, db_path=db_path)

    state = build_graph.resume_run(run_id, db_path=db_path)  # already completed
    assert state["status"] == "completed"
