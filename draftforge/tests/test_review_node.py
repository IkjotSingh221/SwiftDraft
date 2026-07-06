"""Phase 5 review orchestration (hermetic): per-section verify+compliance with
a BOUNDED redraft loop (max 2, then flag for human), compliance violations
routing back to the drafter, and the end-to-end review node producing
review.json + section_status + continuity diffs. Verifier/redraft/continuity
are all injected -- no network."""

from __future__ import annotations

import pytest

from draftforge.formats.validator import ValidationContext
from draftforge.formats.schema import SectionSpec, WordRange
from draftforge.graph.continuity import ContinuityDiff
from draftforge.graph.drafter import LeafDraftResult, section_draft_path
from draftforge.graph.review import (
    MAX_REDRAFT_LOOPS,
    load_review,
    review_node,
    review_section,
)
from draftforge.graph.state import DocumentState, SectionBrief
from draftforge.graph.verifier import CitationCheck, SectionVerification


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))


def _brief(leaf_id: str = "intro") -> SectionBrief:
    return SectionBrief(
        id=leaf_id, title=leaf_id.title(), brief="cover it",
        source_tags=["s1"], target_words=400, parent_path=[], format_section_id=leaf_id,
    )


def _write_draft(run_id: str, leaf_id: str, text: str) -> None:
    section_draft_path(run_id, leaf_id).write_text(text, encoding="utf-8")


def _ok_verify(*a, **k) -> SectionVerification:
    return SectionVerification(section_id=a[1] if len(a) > 1 else "?", checks=[], invalid_keys=[])


def _bad_verify(*a, **k) -> SectionVerification:
    section_id = a[1] if len(a) > 1 else "?"
    return SectionVerification(
        section_id=section_id,
        checks=[CitationCheck(claim="c", cited_keys=["k"], supported=False, reason="no")],
        invalid_keys=[],
    )


def test_supported_section_completes_without_redraft():
    _write_draft("r1", "intro", "Body prose with a citation [@k].")
    calls = {"n": 0}

    def _redraft(*a, **k):
        calls["n"] += 1
        return LeafDraftResult(leaf_id="intro", status="done")

    review, _ds, _t, _c = review_section(
        _brief(), None, None, DocumentState(), None,
        run_id="r1", project_id="p1", log_path=None,
        verify_fn=_ok_verify, redraft_fn=_redraft,
    )
    assert review.status == "done"
    assert review.attempts == 0
    assert calls["n"] == 0


def test_unsupported_section_redrafts_then_flags_for_human():
    _write_draft("r2", "intro", "Body prose [@k].")
    calls = {"n": 0}

    def _redraft(*a, **k):
        calls["n"] += 1
        return LeafDraftResult(leaf_id="intro", status="done")

    review, _ds, _t, _c = review_section(
        _brief(), None, None, DocumentState(), None,
        run_id="r2", project_id="p1", log_path=None,
        verify_fn=_bad_verify, redraft_fn=_redraft,
    )
    # Bounded: exactly MAX_REDRAFT_LOOPS redrafts, then flagged (never a loop).
    assert calls["n"] == MAX_REDRAFT_LOOPS
    assert review.attempts == MAX_REDRAFT_LOOPS
    assert review.status == "flagged"


def test_compliance_violation_routes_back_to_drafter_then_passes():
    spec = SectionSpec(id="intro", title="Introduction", word_range=WordRange(min=1, max=4))
    _write_draft("r3", "intro", "one two three four five six seven eight nine ten")  # over limit

    def _redraft(brief, document_state, feedback, *, run_id, project_id, **k):
        # The drafter rewrites a compliant (short) section on redraft.
        _write_draft(run_id, brief.id, "two words")
        return LeafDraftResult(leaf_id=brief.id, status="done")

    review, _ds, _t, _c = review_section(
        _brief(), spec, ValidationContext(), DocumentState(), None,
        run_id="r3", project_id="p1", log_path=None,
        verify_fn=_ok_verify, redraft_fn=_redraft,
    )
    assert review.attempts == 1
    assert review.status == "done"
    assert review.compliance_violations == []


def test_missing_draft_file_is_flagged():
    review, _ds, _t, _c = review_section(
        _brief("ghost"), None, None, DocumentState(), None,
        run_id="r4", project_id="p1", log_path=None,
        verify_fn=_ok_verify, redraft_fn=lambda *a, **k: None,
    )
    assert review.status == "flagged"


def test_review_node_end_to_end_writes_review_and_continuity():
    run_id = "rEnd"
    _write_draft(run_id, "intro", "Intro body.\n\nIntro tail para.")
    _write_draft(run_id, "method", "Method head para.\n\nMethod body.")

    state = {
        "run_id": run_id,
        "project_id": "p1",
        "format_spec_id": "ieee_report",
        "leaf_briefs": [_brief("intro").model_dump(), _brief("method").model_dump()],
        "document_state": DocumentState().model_dump(),
        "section_status": {},
        "decisions_log_path": None,
    }

    def _fake_edit(a_id, b_id, a_tail, b_head, compact, **k):
        return ContinuityDiff(a_id=a_id, b_id=b_id, changed=False, note="fine")

    result = review_node(
        state,
        spec_loader=lambda _id: None,  # skip compliance; exercise verify + continuity
        verify_fn=_ok_verify,
        edit_boundary_fn=_fake_edit,
    )

    assert result["status"] == "completed"
    statuses = {sid: e["status"] for sid, e in result["section_status"].items()}
    assert statuses == {"intro": "done", "method": "done"}

    review = load_review(run_id)
    assert review is not None
    assert {s["section_id"] for s in review["sections"]} == {"intro", "method"}
    # two sections in document order -> exactly one adjacent boundary
    assert len(review["continuity"]) == 1
    assert review["continuity"][0]["a_id"] == "intro"
    assert review["continuity"][0]["b_id"] == "method"
