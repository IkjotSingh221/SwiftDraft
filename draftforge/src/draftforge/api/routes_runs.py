"""Run routes: start/outline/approve/resume/events/downloads.

Phase 0: routes are registered (paths reserved) with stub 501 responses so
later phases only need to fill in bodies here — no route wiring changes.
Phase 3: planner + human-in-the-loop outline approval (create/get/outline
GET+PATCH/approve) are implemented against `graph/build_graph.py`.
Phase 4: `GET /runs/{id}/events` (SSE, tails `graph/drafter.py`'s events
log — see `api/sse.py`), `GET /runs/{id}/sections` (live per-section status
from the checkpointed `section_status`), and `GET /runs/{id}/decisions` (the
full per-run agent-decision log, optionally filtered to one section) are all
implemented. `approve_outline` is UNCHANGED from Phase 3 (still a single
synchronous `build_graph.resume(run_id)` call) — see DECISIONS.md for why
that stays synchronous even though it now runs the drafter fan-out too.
TODO(Phase 5): verifier/continuity/compliance loop + redraft + resume.
TODO(Phase 6): rendering + artifact downloads.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from draftforge.api import sse
from draftforge.api.models import OutlineNode, Run, RunCreate, SectionStatus
from draftforge.graph import build_graph, decisions
from draftforge.graph import review as review_mod


class RedraftRequest(BaseModel):
    """Optional feedback for a human-triggered section redraft."""

    feedback: str | None = None

router = APIRouter(prefix="/runs", tags=["runs"])

_NOT_IMPLEMENTED = "TODO: not implemented yet"


def _run_from_meta(meta: dict) -> Run:
    return Run(
        id=meta["run_id"],
        project_id=meta["project_id"],
        format_spec_id=meta["format_spec_id"],
        status=meta.get("status", "queued"),
        error=meta.get("error"),
    )


def _outline_nodes(outline: list[dict]) -> list[OutlineNode]:
    return [OutlineNode.model_validate(node) for node in outline]


@router.post("", response_model=Run, status_code=202)
def create_run(payload: RunCreate, background_tasks: BackgroundTasks) -> Run:
    """Register a run and schedule the planner as a background task.

    Returns immediately with status "queued" — the planner's retrieval +
    LLM call can be slow, so it never runs inline on the request. Poll
    `GET /runs/{id}` for "awaiting_outline_approval" (or "error").
    """
    run_id = build_graph.create_run(payload.project_id, payload.format_spec_id, payload.run_config)
    background_tasks.add_task(build_graph.run_planner_to_interrupt, run_id)
    meta = build_graph.get_run_meta(run_id)
    assert meta is not None  # just written by create_run
    return _run_from_meta(meta)


@router.get("/{run_id}", response_model=Run)
def get_run(run_id: str) -> Run:
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    return _run_from_meta(meta)


@router.get("/{run_id}/outline", response_model=list[OutlineNode])
def get_outline(run_id: str) -> list[OutlineNode]:
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    state = build_graph.get_state(run_id)
    if state is None or not state.get("outline"):
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} outline is not ready yet (status={meta.get('status')!r})",
        )
    return _outline_nodes(state["outline"])


@router.patch("/{run_id}/outline", response_model=list[OutlineNode])
def patch_outline(run_id: str, nodes: list[OutlineNode]) -> list[OutlineNode]:
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    outline_dicts = [node.model_dump() for node in nodes]
    try:
        state = build_graph.apply_outline_edits(run_id, outline_dicts)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _outline_nodes(state["outline"])


@router.post("/{run_id}/approve", response_model=Run)
def approve_outline(run_id: str) -> Run:
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    try:
        state = build_graph.resume(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Run(
        id=run_id,
        project_id=meta["project_id"],
        format_spec_id=meta["format_spec_id"],
        status=state.get("status", "completed"),
        error=state.get("error"),
    )


@router.get("/{run_id}/events")
def stream_events(run_id: str):
    """SSE stream of run/section-status/token-cost events (see
    `graph/drafter.py::emit_event` and `api/sse.py`). A fresh connection
    always replays the run's full event history from the start before
    following new events live, so the dashboard reattaching after a
    crash/resume sees everything it missed."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    return sse.sse_response(sse.tail_run_events(run_id))


@router.get("/{run_id}/sections", response_model=list[SectionStatus])
def list_sections(run_id: str) -> list[SectionStatus]:
    """Live per-section status, in outline document order — a polling
    fallback/complement to the SSE stream above."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    state = build_graph.get_state(run_id)
    if state is None:
        return []
    statuses = state.get("section_status") or {}
    out: list[SectionStatus] = []
    for brief in state.get("leaf_briefs", []):
        leaf_id = brief["id"]
        entry = statuses.get(leaf_id, {})
        out.append(
            SectionStatus(
                section_id=leaf_id,
                title=brief.get("title", leaf_id),
                status=entry.get("status", "queued"),
                tokens_used=entry.get("tokens_used", 0),
                cost_usd=entry.get("cost_usd", 0.0),
            )
        )
    return out


@router.get("/{run_id}/decisions")
def list_decisions(run_id: str, section_id: str | None = None) -> list[dict]:
    """The full per-run agent-decision log (query rewrites, chunk grades,
    critique verdicts, bibkey enforcement, ...), optionally filtered to one
    leaf section's own decisions — backs the dashboard's expandable
    per-section decisions log viewer."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    records = decisions.read_decisions(run_id)
    if section_id:
        records = [r for r in records if r.get("node") == f"drafter:{section_id}"]
    return records


@router.get("/{run_id}/review")
def get_review(run_id: str) -> dict:
    """The Review screen's data: per-section compliance violations + citation
    verdicts (flagged claim / source excerpt / verifier reason) and the
    continuity editor's per-boundary diffs. Empty until the review node runs."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    return review_mod.load_review(run_id) or {"sections": [], "continuity": []}


@router.post("/{run_id}/sections/{section_id}/redraft")
def redraft_section(run_id: str, section_id: str, payload: RedraftRequest | None = None) -> dict:
    """Human-triggered redraft of one section (Review screen). Re-runs the
    drafter for just this leaf with any provided feedback, re-verifies +
    re-checks compliance, and returns the refreshed section review."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    feedback = payload.feedback if payload else None
    try:
        return build_graph.redraft_section(run_id, section_id, feedback)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{run_id}/resume", response_model=Run)
def resume_run(run_id: str) -> Run:
    """Resume a run interrupted after approval (crash mid-draft/mid-review),
    continuing from the SqliteSaver checkpoint to completion (spec.md #5). The
    dashboard reattaches to the SSE stream, which replays from byte 0."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    try:
        state = build_graph.resume_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Run(
        id=run_id,
        project_id=meta["project_id"],
        format_spec_id=meta["format_spec_id"],
        status=state.get("status", "completed"),
        error=state.get("error"),
    )


@router.get("/{run_id}/artifacts/{name}", status_code=501)
def get_artifact(run_id: str, name: str):
    # TODO(Phase 6): serve rendered docx/PDF/bibliography/decisions log.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)
