"""Per-run JSONL decisions logger — the append/read contract Phase 4/5 reuse."""

from __future__ import annotations

import json

from draftforge.graph.decisions import log_decision, read_decisions


def test_log_decision_appends_one_json_line_per_call(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    log_decision("run1", "planner", "retrieval_sample", {"a": 1}, log_path=log_path)
    log_decision("run1", "planner", "raw_outline", {"text": "..."}, log_path=log_path)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["run_id"] == "run1"
    assert first["node"] == "planner"
    assert first["kind"] == "retrieval_sample"
    assert first["payload"] == {"a": 1}
    assert "ts" in first


def test_read_decisions_returns_records_in_order(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    log_decision("run1", "planner", "retrieval_sample", {"n": 1}, log_path=log_path)
    log_decision("run1", "planner", "validation", {"n": 2}, log_path=log_path)

    records = read_decisions("run1", log_path=log_path)
    assert [r["payload"]["n"] for r in records] == [1, 2]


def test_read_decisions_on_missing_file_returns_empty_list(tmp_path):
    assert read_decisions("nonexistent", log_path=tmp_path / "missing.jsonl") == []
