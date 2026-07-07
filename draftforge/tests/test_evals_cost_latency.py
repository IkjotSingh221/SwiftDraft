"""Phase 7 cost/latency eval (hermetic): aggregation over a fixture
`events.jsonl`, both the pure `compute_cost_latency` function and the
file-reading `cost_latency_for_run` wrapper (which reuses
`graph.drafter.read_events` verbatim)."""

from __future__ import annotations

import json

from draftforge.evals.cost_latency import compute_cost_latency, cost_latency_for_run


def _events() -> list[dict]:
    # A tiny two-section run: intro drafted 10:00:00-10:00:05 (2 token_usage
    # events), conclusion drafted 10:00:05-10:00:08 (1 token_usage event).
    # Wall clock for the whole run: 10:00:00 -> 10:00:08 = 8s.
    return [
        {"ts": "2026-01-01T10:00:00+00:00", "event": "run_status", "status": "drafting"},
        {"ts": "2026-01-01T10:00:00+00:00", "event": "section_status", "section_id": "intro", "status": "drafting"},
        {
            "ts": "2026-01-01T10:00:02+00:00",
            "event": "token_usage",
            "section_id": "intro",
            "tokens_used": 100,
            "cost_usd": 0.01,
            "cumulative_tokens": 100,
            "cumulative_cost_usd": 0.01,
        },
        {
            "ts": "2026-01-01T10:00:05+00:00",
            "event": "token_usage",
            "section_id": "intro",
            "tokens_used": 50,
            "cost_usd": 0.005,
            "cumulative_tokens": 150,
            "cumulative_cost_usd": 0.015,
        },
        {"ts": "2026-01-01T10:00:05+00:00", "event": "section_status", "section_id": "intro", "status": "done"},
        {"ts": "2026-01-01T10:00:05+00:00", "event": "section_status", "section_id": "conclusion", "status": "drafting"},
        {
            "ts": "2026-01-01T10:00:08+00:00",
            "event": "token_usage",
            "section_id": "conclusion",
            "tokens_used": 80,
            "cost_usd": 0.008,
            "cumulative_tokens": 230,
            "cumulative_cost_usd": 0.023,
        },
        {"ts": "2026-01-01T10:00:08+00:00", "event": "section_status", "section_id": "conclusion", "status": "done"},
        {"ts": "2026-01-01T10:00:08+00:00", "event": "run_status", "status": "completed"},
    ]


def test_compute_cost_latency_per_run_totals():
    result = compute_cost_latency("run1", _events())
    assert result.total_tokens == 230  # last cumulative_tokens value
    assert result.total_cost_usd == 0.023
    assert result.wall_clock_seconds == 8.0
    assert result.event_count == len(_events())


def test_compute_cost_latency_per_section_breakdown():
    result = compute_cost_latency("run1", _events())
    by_id = {s.section_id: s for s in result.sections}
    assert set(by_id) == {"intro", "conclusion"}

    intro = by_id["intro"]
    assert intro.tokens_used == 150  # 100 + 50
    assert round(intro.cost_usd, 3) == 0.015
    assert intro.wall_clock_seconds == 5.0  # 10:00:00 -> 10:00:05

    conclusion = by_id["conclusion"]
    assert conclusion.tokens_used == 80
    assert conclusion.wall_clock_seconds == 3.0  # 10:00:05 -> 10:00:08


def test_compute_cost_latency_empty_events_is_all_zero_not_an_error():
    result = compute_cost_latency("run-empty", [])
    assert result.total_tokens == 0
    assert result.total_cost_usd == 0.0
    assert result.wall_clock_seconds == 0.0
    assert result.sections == []


def test_compute_cost_latency_falls_back_to_summing_when_no_cumulative_field():
    events = [
        {"ts": "2026-01-01T10:00:00+00:00", "event": "token_usage", "section_id": "a", "tokens_used": 10, "cost_usd": 0.001},
        {"ts": "2026-01-01T10:00:01+00:00", "event": "token_usage", "section_id": "a", "tokens_used": 20, "cost_usd": 0.002},
    ]
    result = compute_cost_latency("run2", events)
    assert result.total_tokens == 30
    assert round(result.total_cost_usd, 3) == 0.003


def test_cost_latency_for_run_reads_a_real_events_jsonl_file(tmp_path):
    events_path = tmp_path / "events.jsonl"
    with open(events_path, "w", encoding="utf-8") as f:
        for event in _events():
            f.write(json.dumps({"run_id": "run1", **event}) + "\n")

    result = cost_latency_for_run("run1", events_path=events_path)
    assert result.total_tokens == 230
    assert result.wall_clock_seconds == 8.0
    assert len(result.sections) == 2


def test_cost_latency_for_run_missing_file_is_all_zero_not_an_error(tmp_path):
    result = cost_latency_for_run("nonexistent-run", events_path=tmp_path / "no_such_file.jsonl")
    assert result.event_count == 0
    assert result.total_tokens == 0
