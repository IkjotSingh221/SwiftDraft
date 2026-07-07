"""Phase 7: cost/latency eval — tokens and wall-clock per section and per run,
computed from a run's own `events.jsonl` (`graph.drafter.emit_event`/
`read_events`, reused verbatim — see DECISIONS.md).

No LLM/GPU needed: this is pure aggregation over an already-recorded event
log. The hermetic test feeds a small fixture `events.jsonl`; the harness
(`report.py`) feeds a real run's log when `--run <run_id>` is given.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from draftforge.graph.drafter import read_events


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SectionCostLatency(BaseModel):
    section_id: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    wall_clock_seconds: float = 0.0
    event_count: int = 0


class RunCostLatency(BaseModel):
    run_id: str
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    wall_clock_seconds: float = 0.0
    event_count: int = 0
    sections: list[SectionCostLatency] = Field(default_factory=list)


def compute_cost_latency(run_id: str, events: list[dict[str, Any]]) -> RunCostLatency:
    """Pure aggregation function (no I/O) -- the seam the hermetic test
    exercises directly with a small hand-built event list."""
    if not events:
        return RunCostLatency(run_id=run_id)

    all_ts = [_parse_ts(e["ts"]) for e in events if e.get("ts")]
    wall_clock = (max(all_ts) - min(all_ts)).total_seconds() if all_ts else 0.0

    token_events = [e for e in events if e.get("event") == "token_usage"]
    if token_events:
        last = token_events[-1]
        total_tokens = (
            int(last["cumulative_tokens"])
            if "cumulative_tokens" in last
            else sum(int(e.get("tokens_used", 0)) for e in token_events)
        )
        total_cost = (
            float(last["cumulative_cost_usd"])
            if "cumulative_cost_usd" in last
            else sum(float(e.get("cost_usd", 0.0)) for e in token_events)
        )
    else:
        total_tokens = 0
        total_cost = 0.0

    by_section: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        sid = e.get("section_id")
        if sid:
            by_section.setdefault(sid, []).append(e)

    sections: list[SectionCostLatency] = []
    for sid in sorted(by_section):
        sec_events = by_section[sid]
        ts_list = [_parse_ts(e["ts"]) for e in sec_events if e.get("ts")]
        sec_wall = (max(ts_list) - min(ts_list)).total_seconds() if ts_list else 0.0
        sec_token_events = [e for e in sec_events if e.get("event") == "token_usage"]
        sections.append(
            SectionCostLatency(
                section_id=sid,
                tokens_used=sum(int(e.get("tokens_used", 0)) for e in sec_token_events),
                cost_usd=sum(float(e.get("cost_usd", 0.0)) for e in sec_token_events),
                wall_clock_seconds=sec_wall,
                event_count=len(sec_events),
            )
        )

    return RunCostLatency(
        run_id=run_id,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        wall_clock_seconds=wall_clock,
        event_count=len(events),
        sections=sections,
    )


def cost_latency_for_run(run_id: str, *, events_path: str | Path | None = None) -> RunCostLatency:
    """Reads `data/runs/{run_id}/events.jsonl` via `graph.drafter.read_events`
    (reused, not reimplemented) and aggregates it. Returns an all-zero result
    (not an error) for a run with no events yet."""
    events = read_events(run_id, events_path=events_path)
    return compute_cost_latency(run_id, events)
