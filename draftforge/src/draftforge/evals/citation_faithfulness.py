"""Phase 7: citation-faithfulness eval — LLM-as-judge NLI check on a sampled
subset of a finished draft's citations, reusing `graph.verifier` verbatim
(`extract_claims`, `verify_section`, `load_valid_bibkeys`) rather than
reimplementing any of it.

## Honesty rule

This eval needs a real LLM judge (`resolve_model("citation_verifier")`) to
produce a real faithfulness rate. There is no LLM configured in this
sandbox, so by default `run_citation_faithfulness_eval_for_run` returns
`status="not_run"` with an explicit reason -- it NEVER fabricates a rate.
The hermetic unit test proves the math is correct by injecting a fake judge
(same pattern as `tests/test_graph_verifier.py`'s `NLIProvider`); the real
path only runs when a caller explicitly passes `live=True` (wired to
`DRAFTFORGE_LIVE=1` in `report.py`), which resolves the real
`citation_verifier` model and does real retrieval -- untested in this
sandbox (no API key / GPU / ingested project available here).
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from draftforge.graph import build_graph, decisions
from draftforge.graph.verifier import Claim, extract_claims, load_valid_bibkeys, verify_section
from draftforge.ingest.store import SearchHit

DEFAULT_SAMPLE_SIZE = 20
DEFAULT_SEED = 42


class SpotCheckRow(BaseModel):
    """One sampled claim's judge verdict -- the human-spot-check protocol's
    unit of audit."""

    section_id: str
    claim: str
    cited_keys: list[str] = Field(default_factory=list)
    supported: bool
    reason: str = ""
    supporting_excerpt: str = ""


class CitationFaithfulnessResult(BaseModel):
    status: str  # "ok" | "not_run"
    reason: str = ""
    total_claims: int = 0
    sampled_claims: int = 0
    supported_claims: int = 0
    faithfulness_rate: float | None = None
    synthetic_judge: bool = False
    rows: list[SpotCheckRow] = Field(default_factory=list)


def _sample_claims(
    all_claims: list[tuple[str, Claim]], sample_size: int, seed: int
) -> list[tuple[str, Claim]]:
    if len(all_claims) <= sample_size:
        return all_claims
    return random.Random(seed).sample(all_claims, sample_size)


def run_citation_faithfulness_eval(
    sections: dict[str, str],
    *,
    project_id: str,
    source_tags_by_section: dict[str, list[str]] | None = None,
    query_by_section: dict[str, str] | None = None,
    valid_bibkeys: set[str] | None = None,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
    provider: Any | None = None,
    model_name: str | None = None,
    retrieve_fn: Callable[..., list[SearchHit]] | None = None,
    live: bool = False,
) -> CitationFaithfulnessResult:
    """Low-level, fully injectable entrypoint: given a draft's section
    markdown (leaf id -> text) and enough context to re-retrieve supporting
    chunks, sample its cited claims and run `verifier.verify_section`'s NLI
    check on them.

    Returns `status="not_run"` (never a fabricated rate) unless a judge is
    actually available: either `provider` is injected (tests) or `live=True`
    (resolves the real `citation_verifier` model at call time).
    """
    all_claims: list[tuple[str, Claim]] = [
        (section_id, claim) for section_id, md in sections.items() for claim in extract_claims(md)
    ]
    if not all_claims:
        return CitationFaithfulnessResult(status="not_run", reason="draft has no cited claims to check", total_claims=0)

    if provider is None and not live:
        return CitationFaithfulnessResult(
            status="not_run",
            reason="no LLM configured (inject `provider` for a test judge, or pass live=True / set "
            "DRAFTFORGE_LIVE=1 to use the real citation_verifier model)",
            total_claims=len(all_claims),
        )

    sampled = _sample_claims(all_claims, sample_size, seed)
    by_section: dict[str, list[Claim]] = defaultdict(list)
    for section_id, claim in sampled:
        by_section[section_id].append(claim)

    rows: list[SpotCheckRow] = []
    supported_count = 0
    for section_id, claims in by_section.items():
        source_tags = (source_tags_by_section or {}).get(section_id, [])
        query = (query_by_section or {}).get(section_id, section_id)
        verification = verify_section(
            sections[section_id],
            section_id,
            project_id,
            source_tags,
            query,
            valid_bibkeys=valid_bibkeys,
            provider=provider,
            model_name=model_name,
            retrieve_fn=retrieve_fn,
        )
        wanted = {c.text for c in claims}
        for check in verification.checks:
            if check.claim not in wanted:
                continue
            rows.append(
                SpotCheckRow(
                    section_id=section_id,
                    claim=check.claim,
                    cited_keys=check.cited_keys,
                    supported=check.supported,
                    reason=check.reason,
                    supporting_excerpt=check.supporting_excerpt,
                )
            )
            if check.supported:
                supported_count += 1

    faithfulness_rate = supported_count / len(rows) if rows else None
    return CitationFaithfulnessResult(
        status="ok",
        total_claims=len(all_claims),
        sampled_claims=len(rows),
        supported_claims=supported_count,
        faithfulness_rate=faithfulness_rate,
        synthetic_judge=not live,
        rows=rows,
    )


# ---------------------------------------------------------------------------
# High-level: run against a real run's on-disk sections/bibliography
# ---------------------------------------------------------------------------


def _load_run_sections(run_id: str) -> dict[str, str]:
    sections_dir = decisions.run_dir(run_id) / "sections"
    if not sections_dir.exists():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(sections_dir.glob("*.md"))}


def _brief_context(run_id: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Best-effort: pull each leaf's `source_tags` and drafting query
    (title + brief, matching `graph.review._query_for`'s own convention)
    from the checkpointed run state, for a more faithful re-retrieval than
    guessing. Returns ({}, {}) if the state isn't available."""
    try:
        state = build_graph.get_state(run_id)
    except Exception:  # noqa: BLE001 - best-effort context only
        state = None
    source_tags: dict[str, list[str]] = {}
    queries: dict[str, str] = {}
    for brief in (state or {}).get("leaf_briefs", []) if state else []:
        sid = brief.get("id")
        if not sid:
            continue
        source_tags[sid] = brief.get("source_tags", [])
        queries[sid] = f"{brief.get('title', sid)}. {brief.get('brief', '')}".strip()
    return source_tags, queries


def run_citation_faithfulness_eval_for_run(
    run_id: str,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
    live: bool = False,
) -> CitationFaithfulnessResult:
    """The harness entrypoint: resolves a run's drafted sections + project
    bibliography from disk, then delegates to `run_citation_faithfulness_eval`.
    Always `status="not_run"` unless `live=True` (there is no other way to
    get a real judge in this codebase, by design -- see module docstring)."""
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        return CitationFaithfulnessResult(status="not_run", reason=f"unknown run_id {run_id!r}")

    project_id = meta["project_id"]
    sections = _load_run_sections(run_id)
    if not sections:
        return CitationFaithfulnessResult(status="not_run", reason="run has no drafted section files yet")

    if not live:
        total = sum(len(extract_claims(md)) for md in sections.values())
        return CitationFaithfulnessResult(
            status="not_run",
            reason="no LLM configured (set live=True / DRAFTFORGE_LIVE=1 to run the real citation_verifier judge)",
            total_claims=total,
        )

    source_tags_by_section, query_by_section = _brief_context(run_id)
    valid_bibkeys = load_valid_bibkeys(project_id)
    try:
        return run_citation_faithfulness_eval(
            sections,
            project_id=project_id,
            source_tags_by_section=source_tags_by_section,
            query_by_section=query_by_section,
            valid_bibkeys=valid_bibkeys,
            sample_size=sample_size,
            seed=seed,
            live=True,
        )
    except Exception as exc:  # noqa: BLE001 - live path is best-effort, never crash the harness
        return CitationFaithfulnessResult(status="not_run", reason=f"live citation faithfulness eval failed: {exc}")


# ---------------------------------------------------------------------------
# Human-spot-check export
# ---------------------------------------------------------------------------


def spot_check_markdown_table(rows: list[SpotCheckRow]) -> str:
    """A markdown table a human can read top-to-bottom to audit the judge's
    verdicts (the "human-spot-check protocol" spec.md asks for)."""
    if not rows:
        return "_(no sampled claims)_"
    lines = [
        "| Section | Claim | Cited keys | Verdict | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        claim = row.claim.replace("|", "\\|")
        reason = (row.reason or "").replace("|", "\\|")
        keys = ", ".join(f"@{k}" for k in row.cited_keys)
        verdict = "SUPPORTED" if row.supported else "UNSUPPORTED"
        lines.append(f"| {row.section_id} | {claim} | {keys} | {verdict} | {reason} |")
    return "\n".join(lines)


def write_spot_check_csv(rows: list[SpotCheckRow], path: str | Path) -> Path:
    """Write the same spot-check rows as CSV for a human auditor to open in
    a spreadsheet."""
    import csv

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["section_id", "claim", "cited_keys", "supported", "reason", "supporting_excerpt"])
        for row in rows:
            writer.writerow(
                [row.section_id, row.claim, ";".join(row.cited_keys), row.supported, row.reason, row.supporting_excerpt]
            )
    return path
