"""Phase 7: aggregate all four evals into one markdown report.

`build_report` never raises for a missing GPU/LLM/run -- every section that
can't run for real says so explicitly ("not run" / "SYNTHETIC FIXTURE"),
per spec.md's working-style rule: "Never fabricate benchmark numbers or eval
results. If an eval can't run, say so."

Two write locations (see DECISIONS.md):
  - always: `{data_dir}/evals/eval_report.md` (a standing, global report).
  - when a `run_id` is given: ALSO `data/runs/{run_id}/artifacts/eval_report.md`
    (the exact path Phase 6's `render/pandoc.py::ARTIFACT_SPECS["eval_report.md"]`
    already serves via `GET /runs/{id}/artifacts/eval_report.md`), so the
    Downloads screen's existing "coming in Phase 7" row becomes available
    automatically -- no frontend/backend contract change needed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from draftforge.config import get_settings
from draftforge.evals.citation_faithfulness import (
    CitationFaithfulnessResult,
    run_citation_faithfulness_eval_for_run,
    spot_check_markdown_table,
)
from draftforge.evals.compliance_eval import ComplianceEvalResult, run_compliance_eval
from draftforge.evals.cost_latency import RunCostLatency, cost_latency_for_run
from draftforge.evals.retrieval_eval import RetrievalEvalResult, run_retrieval_eval
from draftforge.graph.build_graph import get_run_meta
from draftforge.render.pandoc import artifacts_dir


def _live_enabled(live: bool | None) -> bool:
    if live is not None:
        return live
    return os.getenv("DRAFTFORGE_LIVE") == "1"


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _retrieval_section(live: bool) -> str:
    lines = ["## 1. Retrieval (recall@5 / recall@10 / MRR ablation)", ""]
    synthetic = run_retrieval_eval(live=False)
    lines += _retrieval_table(synthetic, heading="Synthetic fixture (default, no GPU/LLM required)")

    if live:
        try:
            real = run_retrieval_eval(live=True)
        except Exception as exc:  # noqa: BLE001 - never crash the harness on a missing GPU/model
            lines += [
                "",
                "### Live (DRAFTFORGE_LIVE=1) — NOT AVAILABLE",
                "",
                f"Attempted to run with real `embed_dense`/`embed_sparse` (BGE-M3 + BM25) and failed: `{exc}`",
            ]
        else:
            lines += _retrieval_table(real, heading="Live (real BGE-M3 + BM25 embeddings)")
    else:
        lines += [
            "",
            "_Live retrieval eval not attempted this run (set `DRAFTFORGE_LIVE=1` and run on a GPU "
            "machine with BGE-M3/BM25 weights available for real numbers)._",
        ]
    return "\n".join(lines)


def _retrieval_table(result: RetrievalEvalResult, *, heading: str) -> list[str]:
    label = "SYNTHETIC" if result.synthetic else "LIVE"
    lines = [
        f"### {heading}",
        "",
        f"**{label}** — {result.note}",
        f"Fixture: {result.fixture_num_chunks} chunks, {result.fixture_num_queries} labeled queries "
        f"(`tests/fixtures/evals/retrieval_fixture.json`).",
        "",
        "| Config | recall@5 | recall@10 | MRR | # queries |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in result.rows:
        lines.append(
            f"| {row.config} | {row.recall_at_5:.3f} | {row.recall_at_10:.3f} | {row.mrr:.3f} | {row.num_queries} |"
        )
    return lines


def _citation_faithfulness_section(run_id: str | None, live: bool) -> str:
    lines = ["## 2. Citation faithfulness", ""]
    if run_id is None:
        lines.append(
            "_Not run: no `--run <run_id>` given. Pass a completed run id to sample its citations and "
            "(with `DRAFTFORGE_LIVE=1`) run the real LLM-as-judge NLI check._"
        )
        return "\n".join(lines)

    result: CitationFaithfulnessResult = run_citation_faithfulness_eval_for_run(run_id, live=live)
    if result.status != "ok":
        lines.append(f"_Not run: {result.reason}_")
        if result.total_claims:
            lines.append(f"\n({result.total_claims} cited claim(s) found in the draft, but not checked.)")
        return "\n".join(lines)

    rate = result.faithfulness_rate if result.faithfulness_rate is not None else float("nan")
    label = "SYNTHETIC JUDGE (test-injected)" if result.synthetic_judge else "LIVE (real citation_verifier model)"
    lines += [
        f"**{label}**",
        "",
        f"- Total cited claims in draft: {result.total_claims}",
        f"- Sampled for judging: {result.sampled_claims}",
        f"- Supported: {result.supported_claims}",
        f"- **Faithfulness rate: {rate:.1%}**",
        "",
        "### Human spot-check table",
        "",
        spot_check_markdown_table(result.rows),
    ]
    return "\n".join(lines)


def _compliance_section() -> str:
    lines = ["## 3. Compliance (real, code-computed — no LLM/GPU)", ""]
    result: ComplianceEvalResult = run_compliance_eval()
    for spec in result.specs:
        lines += [
            f"### {spec.spec_id}",
            "",
            f"Pass rate: **{spec.pass_rate:.0%}** ({spec.passed_drafts}/{spec.total_drafts} fixture drafts, "
            "zero error-severity violations)",
            "",
            "| Draft | Passed | Violations | Codes |",
            "| --- | --- | --- | --- |",
        ]
        for draft in result.drafts:
            if draft.spec_id != spec.spec_id:
                continue
            codes = ", ".join(sorted(set(draft.violation_codes))) or "—"
            lines.append(f"| {draft.draft_name} | {'yes' if draft.passed else 'no'} | {draft.violation_count} | {codes} |")
        if spec.violation_code_counts:
            lines += ["", "Violation code counts across all drafts for this spec:", ""]
            for code, count in sorted(spec.violation_code_counts.items()):
                lines.append(f"- `{code}`: {count}")
        lines.append("")
    return "\n".join(lines)


def _cost_latency_section(run_id: str | None) -> str:
    lines = ["## 4. Cost / latency", ""]
    if run_id is None:
        lines.append("_Not run: no `--run <run_id>` given. Pass a run id to aggregate its `events.jsonl`._")
        return "\n".join(lines)

    result: RunCostLatency = cost_latency_for_run(run_id)
    if result.event_count == 0:
        lines.append(f"_Run `{run_id}` has no events.jsonl yet (not started, or no events emitted)._")
        return "\n".join(lines)

    lines += [
        f"Run `{run_id}`: {result.total_tokens} tokens, ${result.total_cost_usd:.4f}, "
        f"{result.wall_clock_seconds:.1f}s wall-clock ({result.event_count} events).",
        "",
        "| Section | Tokens | Cost (USD) | Wall-clock (s) | Events |",
        "| --- | --- | --- | --- | --- |",
    ]
    for sec in result.sections:
        lines.append(
            f"| {sec.section_id} | {sec.tokens_used} | {sec.cost_usd:.4f} | {sec.wall_clock_seconds:.1f} | {sec.event_count} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def build_report(*, run_id: str | None = None, live: bool | None = None) -> str:
    """Build the full markdown report. `run_id`, if given, must be a known
    run (used for citation-faithfulness + cost/latency); an unknown run_id
    degrades to "not run" sections rather than raising, so the harness never
    crashes on a typo."""
    live_enabled = _live_enabled(live)
    if run_id is not None and get_run_meta(run_id) is None:
        run_id = None  # unknown run -- degrade gracefully, don't crash the whole report

    generated_at = datetime.now(timezone.utc).isoformat()
    header = [
        "# DraftForge Evaluation Report",
        "",
        f"Generated: {generated_at}",
        f"Run evaluated: `{run_id}`" if run_id else "Run evaluated: (none — pass `--run <run_id>` for citation-faithfulness and cost/latency)",
        f"Live mode (DRAFTFORGE_LIVE): {'ON' if live_enabled else 'off'}",
        "",
        "This report clearly separates what was actually measured in this environment from what requires a "
        "GPU and/or a configured LLM to measure for real. Sections 1-2 are gated behind real "
        "BGE-M3/BM25/LLM access; section 3 is always real (pure code); section 4 is real whenever a run is given.",
        "",
    ]

    parts = [
        "\n".join(header),
        _retrieval_section(live_enabled),
        _citation_faithfulness_section(run_id, live_enabled),
        _compliance_section(),
        _cost_latency_section(run_id),
    ]
    return "\n\n".join(parts) + "\n"


def write_report(markdown: str, *, run_id: str | None = None) -> list[Path]:
    """Write the report to its documented locations. Always writes the
    global `{data_dir}/evals/eval_report.md`; additionally writes
    `data/runs/{run_id}/artifacts/eval_report.md` (reusing
    `render.pandoc.artifacts_dir`, the exact path Phase 6's artifact
    whitelist already serves) when `run_id` is given."""
    settings = get_settings()
    written: list[Path] = []

    global_dir = settings.data_dir / "evals"
    global_dir.mkdir(parents=True, exist_ok=True)
    global_path = global_dir / "eval_report.md"
    global_path.write_text(markdown, encoding="utf-8")
    written.append(global_path)

    if run_id is not None and get_run_meta(run_id) is not None:
        run_path = artifacts_dir(run_id) / "eval_report.md"
        run_path.write_text(markdown, encoding="utf-8")
        written.append(run_path)

    return written
