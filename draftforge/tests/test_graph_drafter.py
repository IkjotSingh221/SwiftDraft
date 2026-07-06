"""Phase 4 drafter subgraph: mocked-LLM per-leaf pipeline traverses the right
paths (good grades -> draft directly; poor grades -> up to 2 query-rewrite
retries), hallucinated bibkeys are impossible to survive into a saved draft,
the bounded thread-pool fan-out produces every leaf's draft with parallelism
respected, and the DocumentState update stays compact. Fully hermetic: no
Qdrant/LLM network calls anywhere -- `hybrid_search`/`resolve_model` are
always injected or monkeypatched."""

from __future__ import annotations

import json
import threading
import time

import pytest

from draftforge.graph import drafter as drafter_mod
from draftforge.graph.state import DocumentState, SectionBrief
from draftforge.ingest.store import SearchHit
from draftforge.llm.base import LLMResponse, TokenUsage


def _hit(chunk_id: str, bibkeys: list[str], text: str = "Some retrieved passage text.") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        source_id="src1",
        section_path=["Intro"],
        page=1,
        text=text,
        bibkeys=bibkeys,
        score=0.9,
    )


def _brief(leaf_id: str = "intro", target_words: int = 500) -> SectionBrief:
    return SectionBrief(
        id=leaf_id,
        title="Introduction",
        brief="Motivate the problem and summarize prior work.",
        source_tags=["src1"],
        target_words=target_words,
        parent_path=[],
        format_section_id=leaf_id,
    )


class ScriptedProvider:
    """A fake LLMProvider whose response depends on which system prompt it
    was called with (rewrite / grade / draft / critique), so a single fake
    instance can stand in for both the "drafter" and "critic" roles."""

    provider_name = "fake"

    def __init__(
        self,
        *,
        grade_response: str = '{"grades": []}',
        draft_response: str | None = None,
        critique_response: str = '{"verdict": "pass", "feedback": ""}',
        rewrite_response: str = "a focused retrieval query",
        on_call=None,
    ):
        self.grade_response = grade_response
        self.draft_response = draft_response or json.dumps(
            {"draft": "Body prose citing [@valid1].", "summary": "A short summary.", "claims": ["claim one"]}
        )
        self.critique_response = critique_response
        self.rewrite_response = rewrite_response
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0
        self.on_call = on_call

    def complete(self, messages, *, model, max_tokens, temperature, json_mode):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            system = messages[0].content
            with self._lock:
                self.calls.append({"system": system[:40], "json_mode": json_mode})
            if self.on_call:
                time.sleep(self.on_call)
            if not json_mode:
                text = self.rewrite_response
            elif "grade retrieved passages" in system:
                text = self.grade_response
            elif "critical reviewer" in system:
                text = self.critique_response
            else:  # drafting system prompt (draft or revision)
                text = self.draft_response
            return LLMResponse(
                text=text,
                usage=TokenUsage(prompt_tokens=50, completion_tokens=50, total_tokens=100),
                model=model,
                provider=self.provider_name,
                cost_usd=0.001,
            )
        finally:
            with self._lock:
                self._active -= 1

    def call_count(self, predicate) -> int:
        return len([c for c in self.calls if predicate(c)])


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))


def _resolve_model_stub(provider: ScriptedProvider):
    def _resolve(role: str):
        return provider, f"fake-{role}"

    return _resolve


# ---------------------------------------------------------------------------
# Good grades -> draft directly (no retries)
# ---------------------------------------------------------------------------


def test_good_grades_draft_path_no_retries(monkeypatch, tmp_path):
    provider = ScriptedProvider(
        grade_response=json.dumps({"grades": [{"chunk_id": "c1", "relevant": True}]}),
    )
    monkeypatch.setattr(drafter_mod, "resolve_model", _resolve_model_stub(provider))

    hits = [_hit("c1", ["valid1"])]
    result = drafter_mod.draft_leaf(
        _brief(),
        DocumentState(),
        run_id="run1",
        project_id="proj1",
        qdrant_client_factory=lambda: object(),
        hybrid_search_fn=lambda *a, **k: hits,
    )

    assert result.status == "done"
    # exactly one query rewrite + one grading call -- no retries needed.
    rewrite_calls = provider.call_count(lambda c: not c["json_mode"])
    grade_calls = provider.call_count(lambda c: c["json_mode"] and "grade retrieved" in _system_of(provider, c))
    assert rewrite_calls == 1


def _system_of(provider, call):
    return call["system"]


# ---------------------------------------------------------------------------
# Poor grades -> retry with a rewritten query up to 2x (3 attempts total)
# ---------------------------------------------------------------------------


def test_poor_grades_retries_up_to_two_times(monkeypatch):
    provider = ScriptedProvider(
        grade_response=json.dumps({"grades": [{"chunk_id": "c1", "relevant": False}]}),
    )
    monkeypatch.setattr(drafter_mod, "resolve_model", _resolve_model_stub(provider))

    hits = [_hit("c1", ["valid1"])]
    result = drafter_mod.draft_leaf(
        _brief(),
        DocumentState(),
        run_id="run2",
        project_id="proj1",
        qdrant_client_factory=lambda: object(),
        hybrid_search_fn=lambda *a, **k: hits,
    )

    # Still completes (best-effort with whatever was retrieved) rather than failing.
    assert result.status == "done"

    rewrite_calls = provider.call_count(lambda c: not c["json_mode"])
    assert rewrite_calls == drafter_mod.MAX_QUERY_REWRITES + 1  # initial + 2 retries, never more

    decisions = _read_decisions("run2")
    grade_decisions = [d for d in decisions if d["kind"] == "chunk_grade"]
    assert [d["payload"]["attempt"] for d in grade_decisions] == [0, 1, 2]
    assert all(d["payload"]["poor"] for d in grade_decisions)
    rewrite_decisions = [d for d in decisions if d["kind"] == "query_rewrite"]
    assert [d["payload"]["attempt"] for d in rewrite_decisions] == [0, 1, 2]


def _read_decisions(run_id: str):
    from draftforge.graph.decisions import read_decisions

    return read_decisions(run_id)


# ---------------------------------------------------------------------------
# Constraint #2: hallucinated bibkeys are impossible by construction
# ---------------------------------------------------------------------------


def test_enforce_valid_bibkeys_strips_hallucinated_keys():
    text = "Claim A [@valid1]. Claim B [@notreal]. Claim C [@valid1;@notreal]."
    clean, stripped = drafter_mod.enforce_valid_bibkeys(text, {"valid1"})

    assert "notreal" not in clean
    assert "[@valid1]" in clean
    assert "[@notreal]" not in clean
    assert stripped == ["notreal", "notreal"]
    # the mixed marker keeps only the valid entry
    assert "[@valid1;@notreal]" not in clean


def test_hallucinated_bibkey_never_survives_into_saved_draft(monkeypatch):
    bogus_draft = json.dumps(
        {
            "draft": "Evidence shows X [@valid1] and also Y [@notreal].",
            "summary": "Covers X and Y.",
            "claims": [],
        }
    )
    provider = ScriptedProvider(
        grade_response=json.dumps({"grades": [{"chunk_id": "c1", "relevant": True}]}),
        draft_response=bogus_draft,
    )
    monkeypatch.setattr(drafter_mod, "resolve_model", _resolve_model_stub(provider))

    hits = [_hit("c1", ["valid1"])]  # "notreal" is NOT a valid key for this leaf's chunks
    result = drafter_mod.draft_leaf(
        _brief("intro2"),
        DocumentState(),
        run_id="run3",
        project_id="proj1",
        qdrant_client_factory=lambda: object(),
        hybrid_search_fn=lambda *a, **k: hits,
    )

    assert result.status == "done"
    assert "notreal" not in result.citation_keys
    assert result.citation_keys == ["valid1"]

    saved_text = drafter_mod.section_draft_path("run3", "intro2").read_text()
    assert "notreal" not in saved_text
    assert "[@valid1]" in saved_text

    decisions = _read_decisions("run3")
    enforcement = [d for d in decisions if d["kind"] == "bibkey_enforcement"]
    assert enforcement and enforcement[0]["payload"]["stripped_keys"] == ["notreal"]


def test_critique_revise_triggers_exactly_one_revision_call(monkeypatch):
    provider = ScriptedProvider(
        grade_response=json.dumps({"grades": [{"chunk_id": "c1", "relevant": True}]}),
        critique_response=json.dumps({"verdict": "revise", "feedback": "add more detail"}),
    )
    monkeypatch.setattr(drafter_mod, "resolve_model", _resolve_model_stub(provider))

    hits = [_hit("c1", ["valid1"])]
    result = drafter_mod.draft_leaf(
        _brief("intro3"),
        DocumentState(),
        run_id="run6",
        project_id="proj1",
        qdrant_client_factory=lambda: object(),
        hybrid_search_fn=lambda *a, **k: hits,
    )

    assert result.status == "done"
    draft_system_calls = provider.call_count(
        lambda c: c["json_mode"] and "grade retrieved" not in c["system"] and "critical reviewer" not in c["system"]
    )
    assert draft_system_calls == 2  # initial draft + exactly one revision, never a loop

    decisions = _read_decisions("run6")
    critique_decisions = [d for d in decisions if d["kind"] == "critique"]
    assert len(critique_decisions) == 1  # self-critique runs once


def test_leaf_pipeline_failure_never_raises_and_flags_the_section(monkeypatch):
    def _boom(role):
        raise RuntimeError("no model configured")

    monkeypatch.setattr(drafter_mod, "resolve_model", _boom)

    result = drafter_mod.draft_leaf(
        _brief("broken"),
        DocumentState(),
        run_id="run4",
        project_id="proj1",
        qdrant_client_factory=lambda: object(),
        hybrid_search_fn=lambda *a, **k: [],
    )

    assert result.status == "flagged"
    assert result.error is not None
    assert result.draft_path == ""


# ---------------------------------------------------------------------------
# Bounded parallel fan-out produces every leaf's draft
# ---------------------------------------------------------------------------


def test_fanout_produces_all_sections_with_bounded_parallelism(monkeypatch):
    provider = ScriptedProvider(
        grade_response=json.dumps({"grades": [{"chunk_id": "c1", "relevant": True}]}),
        on_call=0.05,  # force overlap so bounded-parallelism is actually exercised
    )
    monkeypatch.setattr(drafter_mod, "resolve_model", _resolve_model_stub(provider))

    leaf_ids = ["intro", "related_work", "method", "results", "conclusion"]
    leaf_briefs = [_brief(lid).model_dump() for lid in leaf_ids]

    state = {
        "run_id": "run5",
        "project_id": "proj1",
        "run_config": {"drafter_parallelism": 2},
        "leaf_briefs": leaf_briefs,
        "document_state": DocumentState().model_dump(),
        "decisions_log_path": None,
        "section_status": {},
    }

    result = drafter_mod.drafter_fanout_node(
        state,
        qdrant_client_factory=lambda: object(),
        hybrid_search_fn=lambda *a, **k: [_hit("c1", ["valid1"])],
    )

    assert result["status"] == "completed"
    section_status = result["section_status"]
    assert set(section_status.keys()) == set(leaf_ids)
    assert all(entry["status"] == "done" for entry in section_status.values())
    for leaf_id in leaf_ids:
        assert drafter_mod.section_draft_path("run5", leaf_id).exists()

    # Bounded parallelism: never more than 2 (the configured limit) LLM calls
    # in flight at once, but more than 1 (i.e. actually parallel, not serial).
    assert provider.max_active <= 2
    assert provider.max_active > 1

    doc_state = DocumentState.model_validate(result["document_state"])
    assert set(doc_state.section_summaries.keys()) == set(leaf_ids)
    assert "valid1" in doc_state.citation_keys


# ---------------------------------------------------------------------------
# DocumentState update ("post-draft node") -- stays compact
# ---------------------------------------------------------------------------


def test_apply_document_state_update_updates_registries_and_stays_compact():
    state = DocumentState()
    result = drafter_mod.LeafDraftResult(
        leaf_id="intro",
        status="done",
        draft_path="/tmp/intro.md",
        citation_keys=["smith2020", "doe2019"],
        summary="Introduces the problem and motivates the approach.",
        claims=["The dataset has 10k samples."],
        figure_count=1,
        table_count=0,
        tokens_used=500,
        cost_usd=0.01,
    )

    updated = drafter_mod.apply_document_state_update(state, result)

    assert updated.section_summaries["intro"] == result.summary
    assert updated.citation_keys == ["smith2020", "doe2019"]
    assert updated.figure_counter == 1
    assert updated.table_counter == 0
    assert updated.global_claims == ["The dataset has 10k samples."]
    # original untouched (pure function)
    assert state.section_summaries == {}

    assert updated.approx_token_count() < 4000


def test_apply_document_state_update_ignores_flagged_leaves():
    state = DocumentState()
    result = drafter_mod.LeafDraftResult(leaf_id="broken", status="flagged", error="boom")

    updated = drafter_mod.apply_document_state_update(state, result)

    assert updated.section_summaries == {}
    assert updated.citation_keys == []
