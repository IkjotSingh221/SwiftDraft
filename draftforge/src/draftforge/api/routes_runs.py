"""Run routes: start/outline/approve/resume/events/downloads.

Phase 0: routes are registered (paths reserved) with stub 501 responses so
later phases only need to fill in bodies here — no route wiring changes.
TODO(Phase 3): planner + human-in-the-loop outline approval.
TODO(Phase 4): drafter subgraph + SSE event stream.
TODO(Phase 5): verifier/continuity/compliance loop + redraft + resume.
TODO(Phase 6): rendering + artifact downloads.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from draftforge.api.models import OutlineNode, Run, RunCreate, SectionStatus

router = APIRouter(prefix="/runs", tags=["runs"])

_NOT_IMPLEMENTED = "TODO: not implemented yet"


@router.post("", response_model=Run, status_code=501)
def create_run(payload: RunCreate) -> Run:
    # TODO(Phase 3): start a LangGraph run, persist via SqliteSaver.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/{run_id}/outline", response_model=list[OutlineNode], status_code=501)
def get_outline(run_id: str) -> list[OutlineNode]:
    # TODO(Phase 3): return the outline captured at the planner interrupt.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.patch("/{run_id}/outline", response_model=list[OutlineNode], status_code=501)
def patch_outline(run_id: str, nodes: list[OutlineNode]) -> list[OutlineNode]:
    # TODO(Phase 3): apply student edits to the outline before approval.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.post("/{run_id}/approve", status_code=501)
def approve_outline(run_id: str) -> dict:
    # TODO(Phase 3): resume the graph past the planner interrupt.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


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
