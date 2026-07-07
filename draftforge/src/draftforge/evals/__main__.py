"""`python -m draftforge.evals` — the Makefile's `make eval` target.

Runs the whole eval harness (retrieval, citation faithfulness, compliance,
cost/latency), writes the aggregated markdown report to
`{data_dir}/evals/eval_report.md`, and — when `--run <run_id>` is given —
also to `data/runs/{run_id}/artifacts/eval_report.md` so the Downloads
screen's eval-report row becomes available.
"""

from __future__ import annotations

import argparse
import sys

from draftforge.evals.report import build_report, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m draftforge.evals",
        description="Run the DraftForge Phase 7 eval harness and emit a single markdown report.",
    )
    parser.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="A run id to evaluate citation faithfulness and cost/latency against, and to also write "
        "the report into that run's artifacts/ directory for the Downloads screen.",
    )
    args = parser.parse_args(argv)

    markdown = build_report(run_id=args.run_id)
    paths = write_report(markdown, run_id=args.run_id)

    for path in paths:
        print(f"wrote {path}", file=sys.stderr)
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
