"""SSE helpers for streaming LangGraph run events.

Phase 0 only stubs the interface; Phase 4 wires an actual LangGraph event
stream into `event_generator`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from sse_starlette.sse import EventSourceResponse


async def event_generator(
    events: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, str]]:
    """Adapt an async iterator of event dicts into sse-starlette's expected shape.

    Each source event dict may include an "event" key (SSE event name) and a
    "data" key (JSON-serializable payload). TODO(Phase 4): feed this from the
    LangGraph node-event stream for a running graph.
    """
    async for event in events:
        name = event.get("event", "message")
        data = event.get("data", {})
        yield {"event": name, "data": json.dumps(data)}


async def heartbeat_stream(interval_s: float = 15.0) -> AsyncIterator[dict[str, Any]]:
    """A placeholder source generator that just heartbeats; useful for smoke tests."""
    while True:
        yield {"event": "heartbeat", "data": {}}
        await asyncio.sleep(interval_s)


def sse_response(events: AsyncIterator[dict[str, Any]]) -> EventSourceResponse:
    return EventSourceResponse(event_generator(events))
