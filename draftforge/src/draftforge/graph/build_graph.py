"""Build the LangGraph `StateGraph` and expose the run lifecycle helpers the
API uses: `create_run`, `get_state`, `resume` (plus supporting helpers
`run_planner_to_interrupt`, `apply_outline_edits`, `get_run_meta`).

## Graph shape (Phase 3)

    START -> planner -> END
             ^^^^^^^
    interrupt_after=["planner"]

Phase 3 has exactly one real node. Phases 4/5 extend this by inserting real
nodes between "planner" and END (drafter fan-out subgraph, citation
verifier, continuity editor, compliance checker) — see the module docstring
in `state.py` for the state fields those nodes read/write. Nothing about the
interrupt/checkpoint/resume machinery below needs to change for that; only
`get_graph()`'s node/edge wiring does.

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
from draftforge.graph.planner import planner_node
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

    TODO(Phase 4): add the drafter fan-out subgraph as node(s) between
    "planner" and END (read `state["leaf_briefs"]` + `state["document_state"]`
    per leaf; write per-leaf drafts to disk, never back into this state —
    see state.py). TODO(Phase 5): insert the citation verifier / continuity
    editor / compliance checker after drafting, routing violations back to
    the owning leaf's drafter.
    """
    builder = StateGraph(RunState)
    builder.add_node("planner", planner_node)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", END)
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

    Phase 3: nothing follows the planner node yet (`planner -> END`), so
    resuming just finalizes the run as "completed". Phase 4/5 insert real
    nodes between "planner" and END (see `get_graph`'s TODOs) — once that
    happens, this same `graph.invoke(None, config)` call actually executes
    them instead of immediately completing.

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
