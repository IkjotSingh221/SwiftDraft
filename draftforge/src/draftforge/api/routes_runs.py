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
Phase 5: verifier/continuity/compliance loop + redraft + resume are
implemented (`get_review`, `redraft_section`, `resume_run`).

Phase 6: `POST /runs/{id}/render` explicitly (re)renders a completed run's
artifacts via `render/pandoc.py::render_run`. `GET /runs/{id}/artifacts`
lists the whitelisted artifact set (name/size/content-type; unrendered/Phase
7 entries show `available=False`), lazily triggering a best-effort render on
first call if the run is completed and nothing has been rendered yet (see
`_ensure_rendered_best_effort` — failures there are logged, never raised, so
a listing call never 5xx's just because Pandoc/LaTeX aren't installed; use
the explicit POST to see the real error). `GET /runs/{id}/artifacts/{name}`
streams one artifact by NAME, looked up only in the `ARTIFACT_SPECS`
whitelist (never used to construct a filesystem path directly) — this is
the path-traversal guard: an unknown/hostile `name` simply isn't in the
dict and 404s.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from draftforge.api import sse
from draftforge.api.models import Artifact, OutlineNode, RenderResponse, Run, RunCreate, SectionStatus
from draftforge.graph import build_graph, decisions
from draftforge.graph import review as review_mod
from draftforge.render import pandoc as pandoc_render

logger = logging.getLogger(__name__)


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


def _ensure_rendered_best_effort(run_id: str) -> None:
    """Best-effort lazy render: if the run's artifacts directory is empty,
    render once. Failures are logged and swallowed here — `GET /artifacts`
    and `GET /artifacts/{name}` should still return whatever IS available
    (e.g. an already-copied bibliography.json) rather than 5xx just because
    Pandoc/LaTeX aren't installed; `POST /render` is the endpoint that
    surfaces a `RenderError` to the caller."""
    out_dir = pandoc_render.artifacts_dir(run_id)
    if any(out_dir.iterdir()):
        return
    try:
        pandoc_render.render_run(run_id)
    except (ValueError, pandoc_render.RenderError) as exc:
        logger.warning("lazy render for run %r skipped/failed: %s", run_id, exc)


@router.post("/{run_id}/render", response_model=RenderResponse)
def render_run(run_id: str) -> RenderResponse:
    """Explicitly (re)render a completed run's artifacts. Unlike the lazy
    trigger on `GET /artifacts`, a real rendering failure here IS surfaced to
    the caller (502) — this is the endpoint to call to see why a document
    didn't render, or to refresh artifacts after a section redraft (Phase 6
    does not auto-invalidate previously rendered artifacts; call this again
    to pick up changes)."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    try:
        result = pandoc_render.render_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except pandoc_render.RenderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RenderResponse(generated=result.generated, skipped=result.skipped)


@router.get("/{run_id}/artifacts", response_model=list[Artifact])
def list_artifacts(run_id: str) -> list[Artifact]:
    """List the whitelisted artifact set (docx, pdf, bibliography, decisions
    log, and the Phase 7 eval-report slot) with name/size/content-type.
    Lazily renders once (best-effort) if nothing has been rendered yet for a
    completed run — see `_ensure_rendered_best_effort`."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    state = build_graph.get_state(run_id)
    if state is None or state.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} is not completed yet (status={meta.get('status')!r})",
        )
    _ensure_rendered_best_effort(run_id)

    out: list[Artifact] = []
    for name, spec in pandoc_render.ARTIFACT_SPECS.items():
        path = spec.path_fn(run_id)
        if path.exists():
            out.append(
                Artifact(
                    name=name,
                    available=True,
                    size_bytes=path.stat().st_size,
                    content_type=spec.content_type,
                    note=None,
                )
            )
        else:
            out.append(
                Artifact(
                    name=name,
                    available=False,
                    size_bytes=None,
                    content_type=spec.content_type,
                    note=spec.note_if_missing,
                )
            )
    return out


@router.get("/{run_id}/artifacts/{name}")
def get_artifact(run_id: str, name: str):
    """Stream one artifact by name. `name` is looked up ONLY in the
    `ARTIFACT_SPECS` whitelist (never used to build a filesystem path
    directly) — an unknown or path-traversal-shaped name simply isn't a key
    in that dict and 404s, same as a known name whose file doesn't exist
    yet."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id!r}")
    spec = pandoc_render.ARTIFACT_SPECS.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown artifact name: {name!r}")
    state = build_graph.get_state(run_id)
    if state is None or state.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} is not completed yet (status={meta.get('status')!r})",
        )
    _ensure_rendered_best_effort(run_id)

    path = spec.path_fn(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"artifact {name!r} is not available for run {run_id!r}")
    return FileResponse(path=str(path), media_type=spec.content_type, filename=name)
