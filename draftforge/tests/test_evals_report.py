"""Phase 7 report aggregation (hermetic): `build_report` produces markdown
containing all four eval sections and `write_report` writes it to both
documented locations. No LLM/GPU/network -- retrieval + compliance always
run for real (synthetic-embedding / pure-code respectively); citation
faithfulness and cost/latency degrade to explicit "not run" text without a
`--run <run_id>`, exactly like `python -m draftforge.evals` would with no
`--run` flag."""

from __future__ import annotations

from draftforge.evals.report import build_report, write_report


def test_build_report_contains_all_four_sections_with_no_run_id():
    markdown = build_report(run_id=None, live=False)
    assert "# DraftForge Evaluation Report" in markdown
    assert "## 1. Retrieval" in markdown
    assert "## 2. Citation faithfulness" in markdown
    assert "## 3. Compliance" in markdown
    assert "## 4. Cost / latency" in markdown

    # Retrieval + compliance are real numbers even with no run_id.
    assert "SYNTHETIC" in markdown
    assert "ieee_report" in markdown
    assert "university_thesis" in markdown

    # Citation faithfulness + cost/latency honestly report "not run" rather
    # than fabricating anything, since no run_id was given.
    assert "Not run: no `--run <run_id>` given" in markdown


def test_build_report_never_fabricates_live_numbers_when_live_is_off():
    markdown = build_report(run_id=None, live=False)
    assert "Live mode (DRAFTFORGE_LIVE): off" in markdown
    assert "Live retrieval eval not attempted this run" in markdown


def test_build_report_degrades_gracefully_for_an_unknown_run_id():
    markdown = build_report(run_id="totally-not-a-real-run-id", live=False)
    # An unknown run_id must not crash the whole report -- it degrades to
    # "no run" behavior for the run-scoped sections.
    assert "# DraftForge Evaluation Report" in markdown
    assert "## 4. Cost / latency" in markdown


def test_build_report_with_a_real_run_includes_cost_latency_numbers(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from draftforge.graph import build_graph
    from draftforge.graph.drafter import emit_event, section_draft_path

    run_id = build_graph.create_run("proj1", "ieee_report", {})
    build_graph._registry_set(run_id, status="completed")
    section_draft_path(run_id, "introduction").write_text(
        "Some drafted prose citing a source [@doe2020].", encoding="utf-8"
    )
    emit_event(run_id, "token_usage", section_id="introduction", tokens_used=42, cost_usd=0.004, cumulative_tokens=42, cumulative_cost_usd=0.004)

    markdown = build_report(run_id=run_id, live=False)
    assert f"Run evaluated: `{run_id}`" in markdown
    assert "42 tokens" in markdown
    assert "introduction" in markdown
    # No LLM configured -- citation faithfulness still honestly "not run".
    assert "no LLM configured" in markdown


def test_write_report_writes_global_path_always(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    markdown = build_report(run_id=None, live=False)
    paths = write_report(markdown, run_id=None)
    assert len(paths) == 1
    global_path = tmp_path / "data" / "evals" / "eval_report.md"
    assert paths[0] == global_path
    assert global_path.read_text(encoding="utf-8") == markdown


def test_write_report_also_writes_run_artifacts_path_when_run_given(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from draftforge.graph import build_graph

    run_id = build_graph.create_run("proj1", "ieee_report", {})
    build_graph._registry_set(run_id, status="completed")

    markdown = build_report(run_id=run_id, live=False)
    paths = write_report(markdown, run_id=run_id)
    assert len(paths) == 2

    global_path = tmp_path / "data" / "evals" / "eval_report.md"
    run_path = tmp_path / "data" / "runs" / run_id / "artifacts" / "eval_report.md"
    assert global_path in paths
    assert run_path in paths
    assert run_path.read_text(encoding="utf-8") == markdown

    # This is exactly the path render.pandoc.ARTIFACT_SPECS["eval_report.md"]
    # serves -- confirm they agree without importing pandoc internals twice.
    from draftforge.render.pandoc import artifacts_dir

    assert run_path == artifacts_dir(run_id) / "eval_report.md"


def test_write_report_skips_run_artifacts_path_for_unknown_run(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    markdown = build_report(run_id=None, live=False)
    paths = write_report(markdown, run_id="not-a-real-run")
    assert len(paths) == 1  # only the global path -- unknown run_id is skipped, not an error
