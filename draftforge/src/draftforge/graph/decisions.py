"""Per-run JSONL decisions log.

spec.md: "Log every agent decision (query rewrites, chunk grades, critique
verdicts, compliance violations) to a per-run JSONL decisions log." Phase 3's
planner is the first writer; Phases 4/5 (query rewrites, chunk grades,
critique/verifier verdicts, compliance violations) append to the same file
via the same `log_decision` helper, one JSON object per line, so the whole
run's decision history is a single append-only, streamable file per run.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from draftforge.config import get_settings

_lock = threading.Lock()


def run_dir(run_id: str) -> Path:
    settings = get_settings()
    d = settings.data_dir / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def decisions_log_path(run_id: str) -> Path:
    return run_dir(run_id) / "decisions.jsonl"


def log_decision(
    run_id: str,
    node: str,
    kind: str,
    payload: dict[str, Any],
    *,
    log_path: str | Path | None = None,
) -> None:
    """Append one JSON decision record.

    `node` is the graph node name (e.g. "planner", "drafter:leaf_id"), `kind`
    is a short category (e.g. "retrieval_sample", "raw_outline",
    "validation", "query_rewrite", "chunk_grade", "critique",
    "compliance_violation").
    """
    path = Path(log_path) if log_path else decisions_log_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "node": node,
        "kind": kind,
        "payload": payload,
    }
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")


def read_decisions(run_id: str, *, log_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Read back every decision logged for a run, in order. Used by tests and
    (Phase 3's dashboard / Phase 5's decisions log viewer)."""
    path = Path(log_path) if log_path else decisions_log_path(run_id)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records
