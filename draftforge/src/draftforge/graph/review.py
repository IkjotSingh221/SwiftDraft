"""Phase 5: the review node -- orchestrates compliance + citation verification
(with bounded redraft loops) and the continuity editor, then finalizes the run.

Graph shape after Phase 5 (see build_graph.py):

    START -> planner -> drafter -> review -> END
             ^^^^^^^
    interrupt_after=["planner"]

Why a single node (like `drafter_fanout_node`) rather than three graph nodes
with conditional edges? The redraft loop is *per section* and bounded ("max 2
loops, then flag for human" -- spec.md Phase 5), and continuity runs over
adjacent boundaries once all sections are settled. Expressing that as
LangGraph conditional edges would need a per-section fan-out with its own
checkpoint sub-scheme; the established pattern in this codebase (Phase 4) is to
do the heavy per-section work inside ONE node so the checkpointer's unit of
work stays exactly one `RunState` update per super-step (see DECISIONS.md).
Resumability (#5) still holds: the drafter's checkpoint precedes this node, so
a crash inside `review` re-runs review from the drafter checkpoint WITHOUT
re-drafting -- exercised by `tests/test_kill_resume.py`.

Unlike drafting, review runs SEQUENTIALLY over `leaf_briefs` in document order:
it is cheaper than drafting, the continuity pass needs a stable left-to-right
order, and a shared `DocumentState` updated after each redraft stays coherent
without locking.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from draftforge.formats.schema import FormatSpec, SectionSpec
from draftforge.graph import continuity as continuity_mod
from draftforge.graph import decisions
from draftforge.graph import verifier as verifier_mod
from draftforge.graph.compliance import (
    blocking_violations,
    check_section,
    compliance_feedback,
    context_for,
)
from draftforge.graph.continuity import ContinuityDiff, edit_boundary, split_paragraphs
from draftforge.graph.decisions import log_decision
from draftforge.graph.drafter import (
    apply_document_state_update,
    draft_leaf,
    emit_event,
    section_draft_path,
)
from draftforge.graph.planner import _load_spec_by_id
from draftforge.graph.state import DocumentState, RunState, SectionBrief, SectionStatusEntry
from draftforge.graph.verifier import SectionVerification, load_valid_bibkeys, verify_section

logger = logging.getLogger(__name__)

MAX_REDRAFT_LOOPS = 2  # spec.md Phase 5: "max 2 loops, then flag for human"


# ---------------------------------------------------------------------------
# Persisted review result (backs the Review screen)
# ---------------------------------------------------------------------------


class SectionReview(BaseModel):
    section_id: str
    title: str
    status: str  # "done" | "flagged"
    attempts: int = 0
    compliance_violations: list[dict[str, Any]] = Field(default_factory=list)
    invalid_keys: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class ReviewResult(BaseModel):
    sections: list[SectionReview] = Field(default_factory=list)
    continuity: list[ContinuityDiff] = Field(default_factory=list)


def review_path(run_id: str) -> Path:
    return decisions.run_dir(run_id) / "review.json"


def load_review(run_id: str) -> dict[str, Any] | None:
    path = review_path(run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_review(run_id: str, result: ReviewResult) -> None:
    review_path(run_id).write_text(result.model_dump_json(indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Redraft-with-feedback (routes compliance/verifier failures back to the drafter)
# ---------------------------------------------------------------------------


def redraft_with_feedback(
    brief: SectionBrief,
    document_state: DocumentState,
    feedback: str,
    *,
    run_id: str,
    project_id: str,
    log_path: str | Path | None = None,
    on_status: Callable[[str, str], None] | None = None,
    qdrant_client_factory: Callable[[], Any] | None = None,
    hybrid_search_fn: Callable[..., Any] | None = None,
) -> Any:
    """Redraft one leaf, injecting the compliance/verifier failures as feedback.

    The feedback is appended to the brief's `brief` text (which `draft_leaf`
    already threads into the draft prompt) rather than adding a new parameter
    to Phase 4's `draft_leaf` -- this reuses the whole corrective-RAG pipeline
    untouched, per the Phase 4 handoff note (see DECISIONS.md)."""
    augmented = brief.model_copy(
        update={
            "brief": (brief.brief or brief.title)
            + "\n\nREVISION REQUIRED -- address these issues from the previous draft:\n"
            + feedback
        }
    )
    log_decision(
        run_id, f"review:{brief.id}", "redraft_feedback", {"feedback": feedback}, log_path=log_path
    )
    return draft_leaf(
        augmented,
        document_state,
        run_id=run_id,
        project_id=project_id,
        log_path=log_path,
        qdrant_client_factory=qdrant_client_factory,
        hybrid_search_fn=hybrid_search_fn,
        on_status=on_status,
    )


# ---------------------------------------------------------------------------
# Per-section verify + compliance + bounded redraft
# ---------------------------------------------------------------------------


def _read_draft(run_id: str, leaf_id: str) -> str | None:
    path = section_draft_path(run_id, leaf_id)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _query_for(brief: SectionBrief) -> str:
    return f"{brief.title}. {brief.brief}".strip()


def review_section(
    brief: SectionBrief,
    section_spec: SectionSpec | None,
    context,
    document_state: DocumentState,
    valid_bibkeys: set[str] | None,
    *,
    run_id: str,
    project_id: str,
    log_path: str | Path | None,
    on_status: Callable[[str, str], None] | None = None,
    verify_fn: Callable[..., SectionVerification] | None = None,
    redraft_fn: Callable[..., Any] | None = None,
    qdrant_client_factory: Callable[[], Any] | None = None,
    hybrid_search_fn: Callable[..., Any] | None = None,
) -> tuple[SectionReview, DocumentState, int, float]:
    """Verify + compliance-check one section, redrafting up to
    `MAX_REDRAFT_LOOPS` times on failure, then flag. Returns
    (review, updated_document_state, extra_tokens, extra_cost)."""
    node = f"review:{brief.id}"
    verify = verify_fn or verify_section
    redraft = redraft_fn or redraft_with_feedback
    extra_tokens = 0
    extra_cost = 0.0

    if on_status:
        on_status(brief.id, "verifying")

    attempts = 0
    last_comp: list = []
    last_ver: SectionVerification | None = None

    while True:
        markdown = _read_draft(run_id, brief.id)
        if markdown is None:
            # Drafter never produced a file for this leaf (it was flagged in
            # Phase 4). Nothing to verify -- surface as flagged.
            return (
                SectionReview(section_id=brief.id, title=brief.title, status="flagged", attempts=attempts),
                document_state,
                extra_tokens,
                extra_cost,
            )

        comp = check_section(markdown, section_spec, context) if section_spec else []
        blocking = blocking_violations(comp)
        ver = verify(
            markdown,
            brief.id,
            project_id,
            brief.source_tags,
            _query_for(brief),
            valid_bibkeys=valid_bibkeys,
            qdrant_client_factory=qdrant_client_factory,
            hybrid_search_fn=hybrid_search_fn,
        )
        extra_tokens += ver.tokens_used
        extra_cost += ver.cost_usd
        last_comp, last_ver = comp, ver

        log_decision(
            run_id,
            node,
            "compliance_violation",
            {"attempt": attempts, "violations": [v.model_dump(mode="json") for v in comp]},
            log_path=log_path,
        )
        log_decision(
            run_id,
            node,
            "citation_verdict",
            {
                "attempt": attempts,
                "invalid_keys": ver.invalid_keys,
                "unsupported": [c.model_dump() for c in ver.unsupported],
            },
            log_path=log_path,
        )

        if not blocking and ver.ok:
            status = "done"
            break
        if attempts >= MAX_REDRAFT_LOOPS:
            status = "flagged"
            break

        feedback = "\n".join(filter(None, [compliance_feedback(blocking), ver.feedback()]))
        result = redraft(
            brief,
            document_state,
            feedback,
            run_id=run_id,
            project_id=project_id,
            log_path=log_path,
            on_status=on_status,
            qdrant_client_factory=qdrant_client_factory,
            hybrid_search_fn=hybrid_search_fn,
        )
        extra_tokens += result.tokens_used
        extra_cost += result.cost_usd
        document_state = apply_document_state_update(document_state, result)
        attempts += 1

    review = SectionReview(
        section_id=brief.id,
        title=brief.title,
        status=status,
        attempts=attempts,
        compliance_violations=[v.model_dump(mode="json") for v in last_comp],
        invalid_keys=last_ver.invalid_keys if last_ver else [],
        citations=[c.model_dump() for c in last_ver.checks] if last_ver else [],
    )
    if on_status:
        on_status(brief.id, status)
    return review, document_state, extra_tokens, extra_cost


# ---------------------------------------------------------------------------
# Continuity pass over adjacent boundaries
# ---------------------------------------------------------------------------


def run_continuity(
    ordered_leaf_ids: list[tuple[str, str]],  # (leaf_id, title)
    document_state: DocumentState,
    *,
    run_id: str,
    log_path: str | Path | None,
    edit_boundary_fn: Callable[..., ContinuityDiff] | None = None,
) -> tuple[list[ContinuityDiff], int, float]:
    """Smooth every adjacent A->B boundary among sections that have a draft on
    disk. Patches only the two boundary paragraphs (see continuity.py)."""
    edit = edit_boundary_fn or edit_boundary
    diffs: list[ContinuityDiff] = []
    tokens = 0
    cost = 0.0
    compact = document_state.render_compact()

    present = [(lid, title) for lid, title in ordered_leaf_ids if section_draft_path(run_id, lid).exists()]
    for (a_id, _a_title), (b_id, _b_title) in zip(present, present[1:]):
        a_md = _read_draft(run_id, a_id)
        b_md = _read_draft(run_id, b_id)
        if a_md is None or b_md is None:
            continue
        a_paras = split_paragraphs(a_md)
        b_paras = split_paragraphs(b_md)
        if not a_paras or not b_paras:
            continue
        diff = edit(a_id, b_id, a_paras[-1], b_paras[0], compact)
        tokens += diff.tokens_used
        cost += diff.cost_usd
        if diff.changed:
            new_a, new_b = continuity_mod.apply_boundary_patch(a_md, b_md, diff)
            section_draft_path(run_id, a_id).write_text(new_a, encoding="utf-8")
            section_draft_path(run_id, b_id).write_text(new_b, encoding="utf-8")
        log_decision(
            run_id,
            f"continuity:{a_id}->{b_id}",
            "boundary_edit",
            {"changed": diff.changed, "note": diff.note},
            log_path=log_path,
        )
        diffs.append(diff)
    return diffs, tokens, cost


# ---------------------------------------------------------------------------
# The review node
# ---------------------------------------------------------------------------


def review_node(
    state: RunState,
    *,
    spec_loader: Callable[[str], FormatSpec] | None = None,
    verify_fn: Callable[..., SectionVerification] | None = None,
    redraft_fn: Callable[..., Any] | None = None,
    edit_boundary_fn: Callable[..., ContinuityDiff] | None = None,
    qdrant_client_factory: Callable[[], Any] | None = None,
    hybrid_search_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """LangGraph node: `drafter -> review -> END`. Verifies citations +
    compliance per section (bounded redraft loop), then smooths continuity,
    persists the review for the UI, and sets the terminal run status.

    The keyword-only `*_fn` args are test injection points (same pattern as
    `drafter_fanout_node`); production calls this with just `state`."""
    run_id = state["run_id"]
    project_id = state["project_id"]
    log_path = state.get("decisions_log_path")
    load_spec = spec_loader or _load_spec_by_id

    leaf_briefs = [SectionBrief.model_validate(b) for b in state.get("leaf_briefs", [])]
    document_state = DocumentState.model_validate(state.get("document_state") or {})
    section_status: dict[str, dict[str, Any]] = dict(state.get("section_status") or {})

    emit_event(run_id, "run_status", status="verifying")

    try:
        spec: FormatSpec | None = load_spec(state["format_spec_id"])
    except Exception as exc:  # noqa: BLE001 - a missing/broken spec must not crash the run
        logger.warning("review: could not load spec %r (%s)", state.get("format_spec_id"), exc)
        spec = None
    context = context_for(spec) if spec else None
    valid_bibkeys = load_valid_bibkeys(project_id)

    def on_status(leaf_id: str, status: str) -> None:
        emit_event(run_id, "section_status", section_id=leaf_id, status=status)

    reviews: list[SectionReview] = []
    for brief in leaf_briefs:
        section_spec = spec.find_section(brief.format_section_id) if spec else None
        review, document_state, extra_tokens, extra_cost = review_section(
            brief,
            section_spec,
            context,
            document_state,
            valid_bibkeys,
            run_id=run_id,
            project_id=project_id,
            log_path=log_path,
            on_status=on_status,
            verify_fn=verify_fn,
            redraft_fn=redraft_fn,
            qdrant_client_factory=qdrant_client_factory,
            hybrid_search_fn=hybrid_search_fn,
        )
        reviews.append(review)
        prior = section_status.get(brief.id, {})
        section_status[brief.id] = SectionStatusEntry(
            status=review.status,  # "done" | "flagged"
            tokens_used=int(prior.get("tokens_used", 0)) + extra_tokens,
            cost_usd=float(prior.get("cost_usd", 0.0)) + extra_cost,
        ).model_dump()

    ordered = [(b.id, b.title) for b in leaf_briefs]
    diffs, cont_tokens, cont_cost = run_continuity(
        ordered, document_state, run_id=run_id, log_path=log_path, edit_boundary_fn=edit_boundary_fn
    )

    save_review(run_id, ReviewResult(sections=reviews, continuity=diffs))
    emit_event(run_id, "run_status", status="completed")

    return {
        "status": "completed",
        "document_state": document_state.model_dump(),
        "section_status": section_status,
    }


# ---------------------------------------------------------------------------
# Single-section redraft (human-triggered, POST /runs/{id}/sections/{id}/redraft)
# ---------------------------------------------------------------------------


def redraft_single_section(
    state: RunState,
    section_id: str,
    feedback: str | None,
    *,
    verify_fn: Callable[..., SectionVerification] | None = None,
    redraft_fn: Callable[..., Any] | None = None,
    spec_loader: Callable[[str], FormatSpec] | None = None,
    qdrant_client_factory: Callable[[], Any] | None = None,
    hybrid_search_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Redraft ONE section on demand (from the Review screen), then re-verify +
    re-check compliance. Returns the update to merge into `RunState`
    (`section_status`, `document_state`) plus the fresh `SectionReview` under
    key `"section_review"` (the API strips that before checkpointing)."""
    run_id = state["run_id"]
    project_id = state["project_id"]
    log_path = state.get("decisions_log_path")
    load_spec = spec_loader or _load_spec_by_id

    briefs = {b["id"]: SectionBrief.model_validate(b) for b in state.get("leaf_briefs", [])}
    if section_id not in briefs:
        raise KeyError(f"unknown section_id {section_id!r} for run {run_id!r}")
    brief = briefs[section_id]
    document_state = DocumentState.model_validate(state.get("document_state") or {})

    try:
        spec: FormatSpec | None = load_spec(state["format_spec_id"])
    except Exception:  # noqa: BLE001
        spec = None
    context = context_for(spec) if spec else None
    section_spec = spec.find_section(brief.format_section_id) if spec else None
    valid_bibkeys = load_valid_bibkeys(project_id)

    def on_status(leaf_id: str, status: str) -> None:
        emit_event(run_id, "section_status", section_id=leaf_id, status=status)

    redraft = redraft_fn or redraft_with_feedback
    # A human-triggered redraft always drafts at least once (with any provided
    # feedback), then verifies. We reuse the drafter, then run one verify pass.
    result = redraft(
        brief,
        document_state,
        feedback or "Redraft this section for improved quality and citation support.",
        run_id=run_id,
        project_id=project_id,
        log_path=log_path,
        on_status=on_status,
        qdrant_client_factory=qdrant_client_factory,
        hybrid_search_fn=hybrid_search_fn,
    )
    document_state = apply_document_state_update(document_state, result)

    review, document_state, extra_tokens, extra_cost = review_section(
        brief,
        section_spec,
        context,
        document_state,
        valid_bibkeys,
        run_id=run_id,
        project_id=project_id,
        log_path=log_path,
        on_status=on_status,
        verify_fn=verify_fn,
        redraft_fn=redraft_fn,
        qdrant_client_factory=qdrant_client_factory,
        hybrid_search_fn=hybrid_search_fn,
    )

    section_status = dict(state.get("section_status") or {})
    prior = section_status.get(section_id, {})
    section_status[section_id] = SectionStatusEntry(
        status=review.status,
        tokens_used=int(prior.get("tokens_used", 0)) + result.tokens_used + extra_tokens,
        cost_usd=float(prior.get("cost_usd", 0.0)) + result.cost_usd + extra_cost,
    ).model_dump()

    # Merge the refreshed SectionReview into the persisted review.json.
    existing = load_review(run_id) or {"sections": [], "continuity": []}
    sections = [s for s in existing.get("sections", []) if s.get("section_id") != section_id]
    sections.append(review.model_dump())
    save_review(run_id, ReviewResult(
        sections=[SectionReview.model_validate(s) for s in sections],
        continuity=[ContinuityDiff.model_validate(c) for c in existing.get("continuity", [])],
    ))

    return {
        "document_state": document_state.model_dump(),
        "section_status": section_status,
        "section_review": review.model_dump(),
    }
