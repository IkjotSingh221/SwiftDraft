"""build_graph.py: the graph pauses at the planner interrupt, edits apply to
the checkpointed state, resume completes the run, and a run is recoverable
from a brand-new graph/connection object built against the same on-disk
SqliteSaver database (spec.md non-negotiable #5: "everything resumes")."""

from __future__ import annotations

import json

import pytest

from draftforge.graph import build_graph, planner as planner_mod
from draftforge.llm.base import LLMResponse, TokenUsage

WELL_FORMED_OUTLINE = json.dumps(
    [
        {"id": "introduction", "title": "Introduction", "target_words": 600, "children": []},
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


@pytest.fixture(autouse=True)
def _mock_llm_and_retrieval(monkeypatch: pytest.MonkeyPatch):
    import draftforge.graph.continuity as continuity_mod
    import draftforge.graph.drafter as drafter_mod
    import draftforge.graph.verifier as verifier_mod

    fake = lambda role: (_FakeProvider(), "fake-model")  # noqa: E731
    monkeypatch.setattr(planner_mod, "sample_retrieval", lambda *a, **k: {})
    monkeypatch.setattr(planner_mod, "resolve_model", fake)
    # Phase 5: resume() now runs drafter + review; mock those roles + the store
    # so the resume tests stay hermetic and fast.
    monkeypatch.setattr(drafter_mod, "resolve_model", fake)
    monkeypatch.setattr(drafter_mod, "get_qdrant_client", lambda: object())
    monkeypatch.setattr(drafter_mod, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(verifier_mod, "resolve_model", fake)
    monkeypatch.setattr(continuity_mod, "resolve_model", fake)


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "runs.sqlite"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))


def test_graph_pauses_after_planner_then_resumes(db_path):
    run_id = build_graph.create_run("proj1", "ieee_report", {})
    build_graph.run_planner_to_interrupt(run_id, db_path=db_path)

    meta = build_graph.get_run_meta(run_id)
    assert meta["status"] == "awaiting_outline_approval"

    state = build_graph.get_state(run_id, db_path=db_path)
    assert state is not None
    assert state["status"] == "awaiting_outline_approval"
    assert [n["id"] for n in state["outline"]][:2] == ["introduction", "related_work"]

    resumed = build_graph.resume(run_id, db_path=db_path)
    assert resumed["status"] == "completed"
    assert build_graph.get_run_meta(run_id)["status"] == "completed"


def test_resume_before_approval_state_is_rejected(db_path):
    run_id = build_graph.create_run("proj1", "ieee_report", {})
    # Not planned yet -- get_state returns None, resume must not silently succeed.
    with pytest.raises(KeyError):
        build_graph.resume(run_id, db_path=db_path)


def test_apply_outline_edits_persists_through_the_checkpointer(db_path):
    run_id = build_graph.create_run("proj1", "ieee_report", {})
    build_graph.run_planner_to_interrupt(run_id, db_path=db_path)

    state = build_graph.get_state(run_id, db_path=db_path)
    outline = state["outline"]
    outline[0]["title"] = "Edited Introduction Title"
    outline[0]["target_words"] = 999

    build_graph.apply_outline_edits(run_id, outline, db_path=db_path)

    # Read back via a completely separate call (fresh checkpointer connection).
    reloaded = build_graph.get_state(run_id, db_path=db_path)
    assert reloaded["outline"][0]["title"] == "Edited Introduction Title"
    assert reloaded["outline"][0]["target_words"] == 999


def test_double_approve_is_rejected_after_completion(db_path):
    run_id = build_graph.create_run("proj1", "ieee_report", {})
    build_graph.run_planner_to_interrupt(run_id, db_path=db_path)
    build_graph.resume(run_id, db_path=db_path)

    with pytest.raises(ValueError):
        build_graph.resume(run_id, db_path=db_path)


def test_checkpoint_survives_a_brand_new_graph_object_against_the_same_db_file(db_path):
    """Simulates a process restart: build an entirely separate SqliteSaver +
    connection + compiled graph pointed at the same on-disk file, and confirm
    the run's outline is recoverable purely by run_id."""
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    run_id = build_graph.create_run("proj1", "ieee_report", {})
    build_graph.run_planner_to_interrupt(run_id, db_path=db_path)

    # "Reload": independent connection/graph object, not the one used above.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        fresh_graph = build_graph.get_graph(saver)
        snapshot = fresh_graph.get_state(build_graph._thread_config(run_id))
        assert snapshot.values["status"] == "awaiting_outline_approval"
        assert snapshot.values["outline"][0]["id"] == "introduction"
    finally:
        conn.close()
