"""Build the LangGraph `StateGraph` and expose the run lifecycle helpers the
API uses: `create_run`, `get_state`, `resume` (plus supporting helpers
`run_planner_to_interrupt`, `apply_outline_edits`, `get_run_meta`).

## Graph shape (Phase 4)

    START -> planner -> drafter -> END
             ^^^^^^^
    interrupt_after=["planner"]

"drafter" is `graph/drafter.py::drafter_fanout_node` — see that module's
docstring for the corrective-RAG-per-leaf pipeline and its bounded
(`ThreadPoolExecutor`) fan-out. Phase 5 extends this further by inserting
more real nodes between "drafter" and END (citation verifier, continuity
editor, compliance checker) — see `get_graph()`'s own TODO for exactly what
changes. Nothing about the interrupt/checkpoint/resume machinery below needs
to change for that; only `get_graph()`'s node/edge wiring does.

## Interrupt mechanism

Static `interrupt_after=["planner"]` (LangGraph's built-in mechanism), not
the dynamic `interrupt()` function. Chosen because Phase 3's "human in the
loop" step is a plain resume-to-next-node action — the student's outline
edits are applied directly to the checkpointed state via `update_state`
(no value needs to be threaded back into the middle of a running node), so
the static form is simpler and avoids re-executing (re-billing) the
planner's LLM call on every edit/approve cycle. See DECISIONS.md.

## Checkpointer / thread_id scheme

One SqliteSaver-backed database at `{data_dir}/runs.sqlite`; thread_id ==
run_id. Every helper below opens its OWN short-lived `sqlite3.Connection`
(`check_same_thread=False`) for the duration of one call and closes it
afterward — the same "reopen per call" choice already made for the settings
store in Phase 0 (see DECISIONS.md), which incidentally is exactly what
"everything resumes" (spec.md non-negotiable #5) requires: a *new* graph
object built from the on-disk database recovers a run's state, so a crashed
process's next call already exercises the resume path.

## Run metadata (registry)

A separate, lightweight JSON file (`{data_dir}/runs.json`, same pattern as
`routes_projects.py`'s `projects.json` — see DECISIONS.md) tracks each run's
coarse status (`queued`/`planning`/`awaiting_outline_approval`/`completed`/
`error`) plus `project_id`/`format_spec_id`/`run_config`. This is what
`GET /api/runs/{id}` reads — it never has to open the (heavier) LangGraph
sqlite checkpoint DB just to answer "does this run exist / what's its
status". The checkpointed `RunState` (outline, leaf_briefs, document_state,
...) remains the single source of truth for everything outline-related.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from draftforge.config import get_settings
from draftforge.graph import decisions
from draftforge.graph.drafter import drafter_fanout_node
from draftforge.graph.planner import planner_node
from draftforge.graph.review import review_node
from draftforge.graph.state import DocumentState, RunState

_registry_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Graph construction + checkpointer
# ---------------------------------------------------------------------------


def _runs_db_path() -> Path:
    settings = get_settings()
    settings.ensure_data_dir()
    return settings.data_dir / "runs.sqlite"


@contextmanager
def _open_graph(db_path: str | Path | None = None) -> Iterator[CompiledStateGraph]:
    path = Path(db_path) if db_path else _runs_db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        yield get_graph(saver)
    finally:
        conn.close()


def get_graph(checkpointer: SqliteSaver) -> CompiledStateGraph:
    """Compile the graph against a given checkpointer.

    ## Graph shape (Phase 5)

        START -> planner -> drafter -> review -> END
                 ^^^^^^^
        interrupt_after=["planner"]

    "drafter" (`graph/drafter.py::drafter_fanout_node`) fans out over every
    leaf brief with bounded parallelism and folds each leaf's result into
    `document_state`/`section_status`, handing off with status "verifying".

    "review" (`graph/review.py::review_node`) then runs, per section, the
    programmatic compliance checker + the citation verifier with a bounded
    redraft loop (max 2, then flag for human), followed by the continuity
    editor over adjacent boundaries; it is the LAST node before END and sets
    the terminal "completed" status. A crash INSIDE review re-runs review from
    the drafter's checkpoint without re-drafting (spec.md #5; see
    `resume_run` below and `tests/test_kill_resume.py`).
    """
    builder = StateGraph(RunState)
    builder.add_node("planner", planner_node)
    builder.add_node("drafter", drafter_fanout_node)
    builder.add_node("review", review_node)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "drafter")
    builder.add_edge("drafter", "review")
    builder.add_edge("review", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=["planner"])


def _thread_config(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}}


# ---------------------------------------------------------------------------
# Run registry (lightweight JSON store, mirrors routes_projects.py's pattern)
# ---------------------------------------------------------------------------


def _registry_path() -> Path:
    settings = get_settings()
    settings.ensure_data_dir()
    return settings.data_dir / "runs.json"


def _registry_load() -> dict[str, dict[str, Any]]:
    path = _registry_path()
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _registry_save(data: dict[str, dict[str, Any]]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def _registry_set(run_id: str, **fields: Any) -> None:
    with _registry_lock:
        data = _registry_load()
        record = data.get(run_id, {"run_id": run_id})
        record.update(fields)
        data[run_id] = record
        _registry_save(data)


def get_run_meta(run_id: str) -> dict[str, Any] | None:
    """Lightweight run lookup (registry only — does not touch the graph
    checkpoint DB). Returns None if `run_id` is unknown."""
    return _registry_load().get(run_id)


# ---------------------------------------------------------------------------
# Public contract: create_run / get_state / resume
# ---------------------------------------------------------------------------


def create_run(
    project_id: str,
    format_spec_id: str,
    run_config: dict[str, Any] | None = None,
) -> str:
    """Register a new run and return its `run_id`.

    Does NOT invoke the graph — the planner's retrieval + LLM call can be
    slow, so the API schedules `run_planner_to_interrupt` as a background
    task and returns immediately with status "queued".
    """
    run_id = uuid.uuid4().hex
    _registry_set(
        run_id,
        project_id=project_id,
        format_spec_id=format_spec_id,
        run_config=run_config or {},
        status="queued",
        error=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return run_id


def run_planner_to_interrupt(run_id: str, *, db_path: str | Path | None = None) -> None:
    """Invoke the graph up to the planner interrupt. Intended to run as a
    background task (see `api/routes_runs.py`'s `POST /runs`).

    On success, the run registry's status mirrors whatever the planner node
    set in the checkpointed state (normally "awaiting_outline_approval"). On
    any exception, the registry is marked "error" with the message, and the
    exception is swallowed (a background task has no caller to propagate to;
    the registry IS the error-reporting channel here).
    """
    meta = get_run_meta(run_id)
    if meta is None:
        raise KeyError(f"Unknown run_id: {run_id!r}")

    _registry_set(run_id, status="planning")
    initial_state: RunState = {
        "run_id": run_id,
        "project_id": meta["project_id"],
        "format_spec_id": meta["format_spec_id"],
        "run_config": meta.get("run_config", {}),
        "status": "planning",
        "error": None,
        "outline": [],
        "leaf_briefs": [],
        "document_state": DocumentState().model_dump(),
        "decisions_log_path": str(decisions.decisions_log_path(run_id)),
        "section_status": {},
    }
    try:
        with _open_graph(db_path) as graph:
            graph.invoke(initial_state, _thread_config(run_id))
            snapshot = graph.get_state(_thread_config(run_id))
        final_status = (snapshot.values or {}).get("status", "awaiting_outline_approval")
        _registry_set(run_id, status=final_status)
    except Exception as exc:  # noqa: BLE001 - reported via the registry, not re-raised to a caller
        _registry_set(run_id, status="error", error=str(exc))


def get_state(run_id: str, *, db_path: str | Path | None = None) -> RunState | None:
    """Return the checkpointed graph state for `run_id`, or None if the run
    hasn't produced a checkpoint yet (still "queued"/"planning") or doesn't
    exist at all. This is the ONLY way outline/leaf_briefs/document_state are
    read back — always via a fresh checkpointer connection, so this also
    doubles as the "reload after a crash" path (spec.md non-negotiable #5).
    """
    with _open_graph(db_path) as graph:
        snapshot = graph.get_state(_thread_config(run_id))
    return snapshot.values or None  # type: ignore[return-value]


def apply_outline_edits(
    run_id: str,
    outline: list[dict[str, Any]],
    leaf_briefs: list[dict[str, Any]] | None = None,
    *,
    db_path: str | Path | None = None,
) -> RunState:
    """Persist student edits (titles/briefs/word targets) into the
    checkpointed state via `update_state` — this does NOT re-run the
    planner node; it patches the paused checkpoint directly.

    Raises `KeyError` if the run has no checkpoint yet, `ValueError` if the
    run isn't currently paused at the outline-approval interrupt.
    """
    current = get_state(run_id, db_path=db_path)
    if current is None:
        raise KeyError(f"run {run_id!r} has no checkpointed state yet")
    if current.get("status") != "awaiting_outline_approval":
        raise ValueError(
            f"run {run_id!r} is not awaiting outline approval (status={current.get('status')!r})"
        )

    values: dict[str, Any] = {"outline": outline}
    if leaf_briefs is not None:
        values["leaf_briefs"] = leaf_briefs
    with _open_graph(db_path) as graph:
        config = _thread_config(run_id)
        graph.update_state(config, values)
        snapshot = graph.get_state(config)
    return snapshot.values  # type: ignore[return-value]


def resume(run_id: str, *, db_path: str | Path | None = None) -> RunState:
    """Resume the graph past the planner interrupt (student approval).

    Phase 4: `planner -> drafter -> END` — resuming now actually runs the
    bounded-parallelism drafter fan-out (see `graph/drafter.py`), which can
    take a while for a real run. This call is still fully synchronous/
    blocking (matching Phase 3's `POST /runs/{id}/approve` contract exactly —
    see `api/routes_runs.py`), so the run registry is stamped "drafting"
    *before* the (possibly slow) `graph.invoke` below, purely so a concurrent
    `GET /runs/{id}` from another request can observe progress while this
    call is still in flight. Phase 5 inserts more nodes after "drafter" (see
    `get_graph`'s TODOs) — once that happens, this same
    `graph.invoke(None, config)` call executes those too.

    Raises `KeyError` if the run has no checkpoint yet, `ValueError` if the
    run isn't currently paused at the outline-approval interrupt.
    """
    current = get_state(run_id, db_path=db_path)
    if current is None:
        raise KeyError(f"run {run_id!r} has no checkpointed state yet")
    if current.get("status") != "awaiting_outline_approval":
        raise ValueError(
            f"run {run_id!r} is not awaiting outline approval (status={current.get('status')!r})"
        )

    _registry_set(run_id, status="drafting")
    with _open_graph(db_path) as graph:
        config = _thread_config(run_id)
        graph.invoke(None, config)
        snapshot = graph.get_state(config)
        # Phase 3 has no node after planner, so nothing sets a post-approval
        # status on its own; mark it explicitly. Phase 4/5's post-drafting
        # nodes are expected to set "completed"/"error" themselves instead,
        # at which point this line becomes a no-op overwrite of the same value.
        if (snapshot.values or {}).get("status") == "awaiting_outline_approval":
            graph.update_state(config, {"status": "completed"})
            snapshot = graph.get_state(config)

    final_status = (snapshot.values or {}).get("status", "completed")
    _registry_set(run_id, status=final_status)
    return snapshot.values  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Phase 5: crash recovery + single-section redraft
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = {"completed", "error"}


def resume_run(run_id: str, *, db_path: str | Path | None = None) -> RunState:
    """Resume a run that was interrupted *after* approval (a crash mid-draft or
    mid-review), continuing it to completion from the SqliteSaver checkpoint —
    the `POST /runs/{id}/resume` crash-recovery path (spec.md non-negotiable
    #5).

    Distinguished from `resume()` (which handles the ordinary planner-approval
    resume): this one is for a run already past approval. Because the drafter
    checkpoints before the review node, re-invoking here continues from the
    last completed node WITHOUT re-running it — a crash inside review re-runs
    only review, not the (expensive) drafter fan-out.

    Raises `KeyError` if the run is unknown/uncheckpointed, `ValueError` if the
    run hasn't been approved yet (use `POST /approve` first).
    """
    meta = get_run_meta(run_id)
    if meta is None:
        raise KeyError(f"unknown run_id: {run_id!r}")
    current = get_state(run_id, db_path=db_path)
    if current is None:
        raise KeyError(f"run {run_id!r} has no checkpointed state yet")

    checkpoint_status = current.get("status")
    if checkpoint_status in _TERMINAL_STATUSES:
        return current  # nothing to resume

    # A checkpoint still parked at the planner interrupt means the run was only
    # resumed if the registry already moved it past approval (resume() stamps
    # "drafting" before invoking). If neither is true, it genuinely hasn't been
    # approved yet.
    registry_status = meta.get("status")
    if checkpoint_status == "awaiting_outline_approval" and registry_status in {
        "queued",
        "planning",
        "awaiting_outline_approval",
    }:
        raise ValueError(f"run {run_id!r} has not been approved yet; POST /approve first")

    _registry_set(run_id, status=checkpoint_status or "drafting")
    with _open_graph(db_path) as graph:
        config = _thread_config(run_id)
        graph.invoke(None, config)
        snapshot = graph.get_state(config)

    final_status = (snapshot.values or {}).get("status", "completed")
    _registry_set(run_id, status=final_status, error=(snapshot.values or {}).get("error"))
    return snapshot.values  # type: ignore[return-value]


def redraft_section(
    run_id: str,
    section_id: str,
    feedback: str | None = None,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Human-triggered redraft of one section from the Review screen. Redrafts
    + re-verifies just that leaf, patches the checkpointed state via
    `update_state`, and returns the fresh `SectionReview` dict.

    Raises `KeyError` if the run/section is unknown.
    """
    from draftforge.graph import review as review_mod

    current = get_state(run_id, db_path=db_path)
    if current is None:
        raise KeyError(f"run {run_id!r} has no checkpointed state yet")

    update = review_mod.redraft_single_section(current, section_id, feedback)
    section_review = update.pop("section_review")
    with _open_graph(db_path) as graph:
        config = _thread_config(run_id)
        graph.update_state(config, update)
    return section_review
