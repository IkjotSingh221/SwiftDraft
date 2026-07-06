"""Run routes: start/outline/approve/resume/events/downloads.

Phase 0: routes are registered (paths reserved) with stub 501 responses so
later phases only need to fill in bodies here — no route wiring changes.
Phase 3: planner + human-in-the-loop outline approval (create/get/outline
GET+PATCH/approve) are implemented against `graph/build_graph.py`.
TODO(Phase 4): drafter subgraph + SSE event stream.
TODO(Phase 5): verifier/continuity/compliance loop + redraft + resume.
TODO(Phase 6): rendering + artifact downloads.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from draftforge.api.models import OutlineNode, Run, RunCreate, SectionStatus
from draftforge.graph import build_graph

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


@router.get("/{run_id}/events", status_code=501)
def stream_events(run_id: str):
    # TODO(Phase 4): SSE stream of LangGraph node events via draftforge.api.sse.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/{run_id}/sections", response_model=list[SectionStatus])
def list_sections(run_id: str) -> list[SectionStatus]:
    # TODO(Phase 4): live per-section status for the run dashboard.
    return []


@router.post("/{run_id}/sections/{section_id}/redraft", status_code=501)
def redraft_section(run_id: str, section_id: str) -> dict:
    # TODO(Phase 5): re-run the drafter subgraph for one flagged section.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.post("/{run_id}/resume", status_code=501)
def resume_run(run_id: str) -> dict:
    # TODO(Phase 5): resume from the SqliteSaver checkpoint after a crash.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/{run_id}/artifacts/{name}", status_code=501)
def get_artifact(run_id: str, name: str):
    # TODO(Phase 6): serve rendered docx/PDF/bibliography/decisions log.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)
