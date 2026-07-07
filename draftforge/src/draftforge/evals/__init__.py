"""Phase 7: the eval harness.

Four evals, aggregated by `report.build_report` into one markdown report:

  - `retrieval_eval` — recall@5/@10 + MRR, dense/sparse/hybrid ablation,
    against a small labeled fixture with deterministic synthetic embeddings
    (no GPU required; see its module docstring for the honesty rule).
  - `citation_faithfulness` — LLM-as-judge NLI check on a sampled subset of a
    finished draft's citations, reusing `graph.verifier` (needs a real LLM;
    reports "not run" otherwise, never a fabricated rate).
  - `compliance_eval` — real, code-computed pass rate per format spec over
    fixture drafts, reusing `formats.validator.validate_document` verbatim.
  - `cost_latency` — tokens/wall-clock per section and per run, from a run's
    `events.jsonl`.

Run the whole harness with `python -m draftforge.evals` (see `__main__.py`),
which is exactly what `make eval` invokes.
"""
