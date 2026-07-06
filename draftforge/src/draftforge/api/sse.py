"""SSE helpers for streaming LangGraph run events.

`GET /runs/{id}/events` (see `routes_runs.py`) tails the durable, append-only
`data/runs/{run_id}/events.jsonl` file written by
`graph/drafter.py::emit_event` — section-status transitions
(queued/drafting/critiquing/done/flagged), per-section token/cost counters,
and coarse run-status changes. A brand-new connection always reads the file
from byte 0 first, then keeps polling for new lines: the file itself IS the
replay buffer, so a dashboard that (re)connects mid-run or after a
crash/resume sees the full history before it starts seeing live events — no
separate in-memory pub/sub is needed for reattachment (see DECISIONS.md).

The tailer stops once the run registry reports a terminal status
("completed"/"error") AND no new lines were read in that same poll — this
lets a completed run's stream terminate cleanly (important for tests using
a synchronous TestClient) while an in-progress run keeps streaming.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sse_starlette.sse import EventSourceResponse

_TERMINAL_STATUSES = {"completed", "error"}
_HEARTBEAT_INTERVAL_S = 15.0


async def event_generator(
    events: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, str]]:
    """Adapt an async iterator of event dicts into sse-starlette's expected
    shape. Each source event dict may include an "event" key (SSE event name)
    and a "data" key (JSON-serializable payload)."""
    async for event in events:
        name = event.get("event", "message")
        data = event.get("data", {})
        yield {"event": name, "data": json.dumps(data, default=str)}


async def heartbeat_stream(interval_s: float = 15.0) -> AsyncIterator[dict[str, Any]]:
    """A placeholder source generator that just heartbeats; useful for smoke tests."""
    while True:
        yield {"event": "heartbeat", "data": {}}
        await asyncio.sleep(interval_s)


def sse_response(events: AsyncIterator[dict[str, Any]]) -> EventSourceResponse:
    return EventSourceResponse(event_generator(events))


# ---------------------------------------------------------------------------
# Run-events tailer
# ---------------------------------------------------------------------------


def _default_events_path(run_id: str) -> Path:
    # Imported lazily to avoid a hard import-time dependency from this module
    # on graph/drafter.py's own (heavier) import chain.
    from draftforge.graph.drafter import events_log_path

    return events_log_path(run_id)


def _default_run_status(run_id: str) -> str | None:
    from draftforge.graph import build_graph

    meta = build_graph.get_run_meta(run_id)
    return (meta or {}).get("status")


async def tail_run_events(
    run_id: str,
    *,
    poll_interval: float = 0.2,
    events_path: str | Path | None = None,
    run_status_fn: Any = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every event already logged for `run_id`, then keep tailing the
    file for new ones. Terminates once the run's registry status is terminal
    and a poll iteration reads zero new lines (see module docstring)."""
    path = Path(events_path) if events_path else _default_events_path(run_id)
    status_fn = run_status_fn or _default_run_status

    pos = 0
    last_heartbeat = time.monotonic()
    while True:
        new_lines: list[str] = []
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                f.seek(pos)
                new_lines = f.readlines()
                pos = f.tell()

        for line in new_lines:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            yield {"event": record.get("event", "message"), "data": record}

        status = status_fn(run_id)
        if status in _TERMINAL_STATUSES and not new_lines:
            break

        now = time.monotonic()
        if not new_lines and (now - last_heartbeat) >= _HEARTBEAT_INTERVAL_S:
            last_heartbeat = now
            yield {"event": "heartbeat", "data": {"run_id": run_id}}

        await asyncio.sleep(poll_interval)
