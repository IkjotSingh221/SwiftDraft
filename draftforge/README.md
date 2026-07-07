# DraftForge

An agentic RAG pipeline that drafts long, format-compliant academic documents
(project reports, thesis chapters, paper drafts) from a student's own source
material. Sources are ingested (GROBID/Docling → chunks → BGE-M3 dense +
BM25 sparse → Qdrant), a LangGraph agent graph plans an outline, drafts each
leaf section with citation-key-only sourcing, verifies citation faithfulness,
checks format compliance programmatically, and renders the result to
docx/PDF via Pandoc. Single-user, local-first, no auth.

See `/home/user/SwiftDraft/spec.md` (project root) for the full specification
and phase plan. This directory (`draftforge/`) is the project root for the
actual application; it lives alongside (and does not touch) `ResearchAgent/`,
an earlier, separate prototype in this same repo.

## Requirements

- Python 3.11+ with [`uv`](https://docs.astral.sh/uv/)
- Node 20+ / npm
- Docker (for Qdrant + GROBID)

## Running it

```sh
cp .env.example .env   # fill in whichever provider API keys you have

make up      # start Qdrant (6333) + GROBID (8070) via docker compose
make dev     # run the API (uvicorn --reload, :8000) and the Vite dev
             # server (:5173) together; the frontend proxies /health,
             # /settings, /projects, /runs to the API
make test    # pytest (backend) + vitest (frontend)
```

Open http://localhost:5173 — the **Settings** screen is fully functional in
Phase 0: pick a model per agent role (planner, drafter, critic, citation
verifier, continuity editor), see API-key-present status and Ollama
reachability, and save. Every other screen is a Phase-0 placeholder that
later phases fill in.

## Evaluation

`src/draftforge/evals/` is the Phase 7 eval harness. Run it with:

```sh
make eval                          # == uv run python -m draftforge.evals
uv run python -m draftforge.evals --run <run_id>   # also scores a specific run
```

It always writes `data/evals/eval_report.md`; with `--run <run_id>` it also
writes `data/runs/{run_id}/artifacts/eval_report.md`, which is exactly the
path `render/pandoc.py`'s artifact whitelist serves at
`GET /runs/{id}/artifacts/eval_report.md` — the file becomes available on the
**Downloads** screen purely by existing on disk, no other code change
required.

The report has four sections. Read this table before trusting any number in
it:

| # | Eval | What it measures | Status in this sandbox |
|---|---|---|---|
| 1 | Retrieval ablation | recall@5, recall@10, MRR for dense-only / sparse-only / hybrid, over a small hand-labeled fixture (`tests/fixtures/evals/retrieval_fixture.json`, 8 chunks / 4 queries) | **SYNTHETIC by default.** This sandbox has no GPU and no downloaded BGE-M3/BM25 weights, so the default run embeds the fixture with small, deterministic, clearly-labeled fake embedders (`synthetic_dense_embed`/`synthetic_sparse_embed` in `evals/retrieval_eval.py`) — a curated topic-keyword bag standing in for "dense," a literal word-hash bag-of-words standing in for "sparse." These numbers exercise the real `ingest.store.hybrid_search`/RRF-fusion code path against Qdrant's real `:memory:` engine, and prove the ablation/metric machinery is correct — **they are not a measurement of real retrieval quality.** Pass `--run` with `DRAFTFORGE_LIVE=1` on a machine with a GPU and the real model weights to get real numbers through the same code path (untested here). |
| 2 | Citation faithfulness | LLM-as-judge NLI check (claim entailed by its cited chunk?) on a sampled subset of a finished draft's citations, reusing `graph.verifier` verbatim; reports a faithfulness rate plus a human-spot-check table (claim / cited keys / verdict / reason) for manual audit | **NOT RUN.** No LLM provider is configured/reachable in this sandbox. The harness reports this explicitly (`status: "not_run"`, with a reason) rather than inventing a rate. The math is proven correct in `tests/test_evals_citation_faithfulness.py` with an injected fake judge (hand-computed faithfulness rate == 2/3 on a small fixture). Pass `--run <run_id>` with `DRAFTFORGE_LIVE=1` and a configured `citation_verifier` model against a real completed run for real numbers. |
| 3 | Compliance | Real, code-only pass rate per shipped format spec (`ieee_report`, `university_thesis`) across 4 fixture drafts each (compliant / over word limit / missing required section / malformed citation), via the unedited Phase 2 `formats.validator.validate_document` | **REAL — no LLM/GPU needed.** Measured every run: **1/4 (25%) pass rate for both specs**, because 3 of the 4 fixture drafts per spec are deliberately non-compliant (to exercise `WORD_COUNT_OVER`, `MISSING_REQUIRED_SUBSECTION`, `MALFORMED_CITATION_MARKER`). See `evals/compliance_eval.py`'s module docstring for a documented gap this eval surfaced: `validate_document`'s tree-completeness check requires a separate `(SectionSpec, markdown)` entry for every required node at *every* depth (containers and their children independently), which Phase 5's live per-section compliance check never exercises (it only ever calls `validate_section`, never `validate_document`) — flagged for Phase 8. |
| 4 | Cost / latency | Tokens and wall-clock per section and per run, aggregated from a run's `events.jsonl` (`graph.drafter.read_events`, reused verbatim) | **REAL whenever `--run <run_id>` is given** — pure aggregation over an already-recorded event log, no LLM/GPU involved. Without `--run`, reports "not run" (nothing to aggregate). |

Also found while building this harness: `ingest.store.get_qdrant_client(":memory:")`
does **not** actually produce Qdrant's in-process engine — the real
`QdrantClient` only special-cases `":memory:"` via its `location` parameter,
and `get_qdrant_client` only ever forwards its argument as `url`. The
existing hermetic tests (`tests/test_ingest_store.py`) sidestep this by
constructing `QdrantClient(":memory:")` directly, and `evals/retrieval_eval.py`
does the same rather than editing `ingest/store.py` (out of scope for this
phase). Flagged here since it's a real, reproducible finding, not a Phase 7
design choice.

Tests: `tests/test_evals_retrieval.py`, `test_evals_compliance.py`,
`test_evals_citation_faithfulness.py`, `test_evals_cost_latency.py`,
`test_evals_report.py` — all hermetic (no Docker/GPU/API keys). See
`TESTING.md`'s Phase 7 section for the full list with one-line purposes.

## Project status

This repo is built phase by phase per `spec.md`. See `TESTING.md` for the
living checklist of what's automated-tested vs. manually verified per phase,
and `DECISIONS.md` for a log of ambiguous calls made along the way.
