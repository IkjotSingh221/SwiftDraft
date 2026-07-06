"""Planner node: outline conforms to the FormatSpec structure given a
MOCKED planner LLM; a malformed LLM response is repaired rather than
propagated. Fully hermetic — no LLM/network/Qdrant calls."""

from __future__ import annotations

import json

import pytest

from draftforge.formats.schema import SPECS_DIR, load_spec
from draftforge.graph import planner as planner_mod
from draftforge.graph.decisions import read_decisions
from draftforge.graph.state import DocumentState
from draftforge.llm.base import LLMResponse, TokenUsage

IEEE_SPEC_PATH = SPECS_DIR / "ieee_report.json"


class _FakeProvider:
    provider_name = "fake"

    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    def complete(self, messages, *, model, max_tokens, temperature, json_mode):
        self.calls.append(
            {"model": model, "max_tokens": max_tokens, "temperature": temperature, "json_mode": json_mode}
        )
        return LLMResponse(
            text=self.text,
            usage=TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
            model=model,
            provider=self.provider_name,
            cost_usd=0.01,
        )


WELL_FORMED_OUTLINE = json.dumps(
    [
        {"id": "introduction", "title": "Introduction", "brief": "Motivate the problem.", "target_words": 600, "source_tags": [], "children": []},
        {"id": "related_work", "title": "Related Work", "brief": "Survey prior approaches.", "target_words": 500, "source_tags": [], "children": []},
        {
            "id": "method",
            "title": "Method",
            "brief": None,
            "target_words": None,
            "source_tags": [],
            "children": [
                {"id": "method_data", "title": "Data", "brief": "Describe the dataset.", "target_words": 250, "source_tags": ["src-1"], "children": []},
                {"id": "method_approach", "title": "Approach", "brief": "Describe the modeling approach.", "target_words": 400, "source_tags": ["src-1"], "children": []},
            ],
        },
        {"id": "results", "title": "Results", "brief": "Report findings.", "target_words": 700, "source_tags": [], "children": []},
        {"id": "conclusion", "title": "Conclusion", "brief": "Summarize and note limitations.", "target_words": 300, "source_tags": [], "children": []},
        {"id": "references", "title": "References", "brief": "Bibliography.", "target_words": 0, "source_tags": [], "children": []},
    ]
)


def _state(run_id="run1", format_spec_id="ieee_report", tmp_path=None):
    return {
        "run_id": run_id,
        "project_id": "proj1",
        "format_spec_id": format_spec_id,
        "run_config": {},
        "status": "planning",
        "outline": [],
        "leaf_briefs": [],
        "document_state": DocumentState().model_dump(),
        "decisions_log_path": str((tmp_path or __import__("pathlib").Path("/tmp")) / "decisions.jsonl"),
        "section_status": {},
        "error": None,
    }


@pytest.fixture()
def no_retrieval(monkeypatch: pytest.MonkeyPatch):
    """`planner_node` tests don't exercise Qdrant themselves — that's covered
    separately by the `test_sample_retrieval_*` tests below — so
    `sample_retrieval` is stubbed to empty samples here."""
    monkeypatch.setattr(planner_mod, "sample_retrieval", lambda *a, **k: {})


def test_outline_conforms_to_spec_structure(tmp_path, monkeypatch: pytest.MonkeyPatch, no_retrieval):
    fake = _FakeProvider(WELL_FORMED_OUTLINE)
    monkeypatch.setattr(planner_mod, "resolve_model", lambda role: (fake, "claude-sonnet-4-6"))

    state = _state(tmp_path=tmp_path)
    result = planner_mod.planner_node(state)

    assert result["status"] == "awaiting_outline_approval"
    outline = result["outline"]
    top_ids = [n["id"] for n in outline]
    assert top_ids == ["introduction", "related_work", "method", "results", "conclusion", "references"]

    method = next(n for n in outline if n["id"] == "method")
    assert [c["id"] for c in method["children"]] == ["method_data", "method_approach"]
    assert method["target_words"] is None  # non-leaf: no word target of its own

    leaf_briefs = result["leaf_briefs"]
    leaf_ids = [b["id"] for b in leaf_briefs]
    assert leaf_ids == [
        "introduction",
        "related_work",
        "method_data",
        "method_approach",
        "results",
        "conclusion",
        "references",
    ]
    method_data_brief = next(b for b in leaf_briefs if b["id"] == "method_data")
    assert method_data_brief["parent_path"] == ["method"]
    assert method_data_brief["target_words"] == 250
    assert method_data_brief["source_tags"] == ["src-1"]

    # planner resolved its model via resolve_model, not a hardcoded name
    assert fake.calls[0]["model"] == "claude-sonnet-4-6"
    assert fake.calls[0]["json_mode"] is True

    # decisions were logged: retrieval sample, raw outline, validation
    decisions = read_decisions("run1", log_path=state["decisions_log_path"])
    kinds = [d["kind"] for d in decisions]
    assert kinds == ["retrieval_sample", "raw_outline", "validation"]
    assert decisions[-1]["payload"]["violations"] == []  # well-formed input, nothing to repair


def test_malformed_llm_outline_is_rejected_and_repaired(tmp_path, monkeypatch: pytest.MonkeyPatch, no_retrieval):
    garbage = "this is not json at all, sorry"
    fake = _FakeProvider(garbage)
    monkeypatch.setattr(planner_mod, "resolve_model", lambda role: (fake, "claude-sonnet-4-6"))

    state = _state(run_id="run2", tmp_path=tmp_path)
    result = planner_mod.planner_node(state)

    # Required structure is still present -- the skeleton fallback wins entirely.
    outline = result["outline"]
    top_ids = [n["id"] for n in outline]
    assert top_ids == ["introduction", "related_work", "method", "results", "conclusion", "references"]
    method = next(n for n in outline if n["id"] == "method")
    assert [c["id"] for c in method["children"]] == ["method_data", "method_approach"]

    leaf_ids = [b["id"] for b in result["leaf_briefs"]]
    assert set(leaf_ids) == {
        "introduction",
        "related_work",
        "method_data",
        "method_approach",
        "results",
        "conclusion",
        "references",
    }

    decisions = read_decisions("run2", log_path=state["decisions_log_path"])
    validation = next(d for d in decisions if d["kind"] == "validation")
    assert "unparseable_or_empty_llm_outline" in validation["payload"]["violations"]


def test_partial_llm_outline_missing_a_required_section_is_repaired(tmp_path, monkeypatch: pytest.MonkeyPatch, no_retrieval):
    partial = json.dumps(
        [
            {"id": "introduction", "title": "Intro", "brief": "b", "target_words": 600, "children": []},
            # "related_work" missing entirely
            {"id": "method", "title": "Method", "children": [
                {"id": "method_data", "title": "Data", "target_words": 250, "children": []},
                {"id": "method_approach", "title": "Approach", "target_words": 5000, "children": []},  # out of range
            ]},
            {"id": "results", "title": "Results", "target_words": 700, "children": []},
            {"id": "conclusion", "title": "Conclusion", "target_words": 300, "children": []},
            {"id": "references", "title": "References", "target_words": 0, "children": []},
        ]
    )
    fake = _FakeProvider(partial)
    monkeypatch.setattr(planner_mod, "resolve_model", lambda role: (fake, "m"))

    state = _state(run_id="run3", tmp_path=tmp_path)
    result = planner_mod.planner_node(state)

    top_ids = [n["id"] for n in result["outline"]]
    assert "related_work" in top_ids  # repaired in from the skeleton

    method_approach = next(b for b in result["leaf_briefs"] if b["id"] == "method_approach")
    spec = load_spec(IEEE_SPEC_PATH)
    word_range = spec.find_section("method_approach").word_range
    assert word_range.min <= method_approach["target_words"] <= word_range.max

    decisions = read_decisions("run3", log_path=state["decisions_log_path"])
    validation = next(d for d in decisions if d["kind"] == "validation")
    violations = validation["payload"]["violations"]
    assert "missing_required_section:related_work" in violations
    assert "target_words_out_of_range:method_approach" in violations


def test_sample_retrieval_handles_unavailable_store_gracefully():
    spec = load_spec(IEEE_SPEC_PATH)

    def _boom():
        raise ConnectionError("qdrant unreachable")

    samples = planner_mod.sample_retrieval(
        "proj1", spec, qdrant_client_factory=_boom
    )
    leaves = [s.id for s in spec.iter_all_sections() if not s.subsections]
    assert set(samples) == set(leaves)
    assert all(hits == [] for hits in samples.values())


def test_sample_retrieval_handles_per_leaf_search_failure():
    spec = load_spec(IEEE_SPEC_PATH)

    def _fake_hybrid_search(client, project_id, query, *, top_k=3):
        raise RuntimeError("collection does not exist")

    samples = planner_mod.sample_retrieval(
        "proj1",
        spec,
        qdrant_client_factory=lambda: object(),
        hybrid_search_fn=_fake_hybrid_search,
    )
    assert all(hits == [] for hits in samples.values())
