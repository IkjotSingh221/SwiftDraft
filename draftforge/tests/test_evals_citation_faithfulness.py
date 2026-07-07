"""Phase 7 citation-faithfulness eval (hermetic): the faithfulness-rate math
with an INJECTED fake NLI judge (same pattern as
`tests/test_graph_verifier.py::NLIProvider`), and the "no judge configured"
path reports `status="not_run"` rather than crashing or fabricating a rate."""

from __future__ import annotations

import json

from draftforge.evals.citation_faithfulness import (
    run_citation_faithfulness_eval,
    run_citation_faithfulness_eval_for_run,
    spot_check_markdown_table,
    write_spot_check_csv,
)
from draftforge.graph.drafter import section_draft_path
from draftforge.graph.decisions import run_dir
from draftforge.ingest.store import SearchHit
from draftforge.llm.base import LLMResponse, TokenUsage


class _FakeJudge:
    provider_name = "fake"

    def __init__(self, verdicts: list[dict]):
        self.verdicts = verdicts
        self.calls = 0

    def complete(self, messages, *, model, max_tokens, temperature, json_mode):
        self.calls += 1
        return LLMResponse(
            text=json.dumps({"verdicts": self.verdicts}),
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model=model,
            provider=self.provider_name,
            cost_usd=0.001,
        )


def _hit(bibkeys: list[str], text: str) -> SearchHit:
    return SearchHit(chunk_id="c1", source_id="s1", section_path=["x"], page=1, text=text, bibkeys=bibkeys, score=0.9)


SECTIONS = {
    "intro": (
        "The model reaches 90% accuracy on the benchmark [@doe2020]. "
        "The method also cures cancer [@doe2020]. "
        "This sentence has no citation at all."
    ),
    "conclusion": "We conclude the approach generalizes well [@lee2019].",
}


def test_faithfulness_rate_computed_correctly_with_injected_judge():
    # 3 claims total across both sections (2 in intro, 1 in conclusion);
    # sample_size=3 means every claim is sampled (no randomness to control for).
    judge = _FakeJudge(
        verdicts=[
            {"supported": True, "reason": "entailed"},
            {"supported": False, "reason": "not entailed"},
        ]
    )
    hits = {
        "intro": [_hit(["doe2020"], "Our model attains 90% accuracy on the benchmark; it improves contrast.")],
        "conclusion": [_hit(["lee2019"], "The approach generalizes well across five held-out benchmarks.")],
    }

    result = run_citation_faithfulness_eval(
        SECTIONS,
        project_id="proj1",
        valid_bibkeys={"doe2020", "lee2019"},
        sample_size=10,
        provider=judge,
        model_name="fake-model",
        retrieve_fn=lambda project_id, query, tags, **kw: hits.get(_section_for_query(query), []),
    )

    assert result.status == "ok"
    assert result.total_claims == 3
    assert result.sampled_claims == 3
    # intro's two claims share the same evidence chunk -> one NLI call for
    # intro (2 items), one for conclusion (1 item) = 2 provider.complete() calls.
    assert judge.calls == 2
    # Hand-verified: intro's call gets both canned verdicts [True, False]
    # (claim 1 supported, claim 2 not); conclusion's call only has 1 item, so
    # only the first canned verdict (True) applies to it -- 2 of 3 supported.
    assert result.supported_claims == 2
    assert result.faithfulness_rate == 2 / 3
    assert result.supported_claims == sum(1 for r in result.rows if r.supported)


def _section_for_query(query: str) -> str:
    # In this test, query_by_section defaults to the section_id itself.
    return query


def test_sampling_respects_sample_size_and_is_deterministic_for_a_fixed_seed():
    many_sections = {f"s{i}": f"Claim number {i} is cited here [@doe2020]." for i in range(10)}
    hits = [_hit(["doe2020"], "Supporting passage.")]

    result_a = run_citation_faithfulness_eval(
        many_sections,
        project_id="proj1",
        valid_bibkeys={"doe2020"},
        sample_size=4,
        seed=7,
        provider=_FakeJudge(verdicts=[{"supported": True, "reason": "ok"}] * 4),
        model_name="fake-model",
        retrieve_fn=lambda *a, **k: hits,
    )
    result_b = run_citation_faithfulness_eval(
        many_sections,
        project_id="proj1",
        valid_bibkeys={"doe2020"},
        sample_size=4,
        seed=7,
        provider=_FakeJudge(verdicts=[{"supported": True, "reason": "ok"}] * 4),
        model_name="fake-model",
        retrieve_fn=lambda *a, **k: hits,
    )
    assert result_a.total_claims == 10
    assert result_a.sampled_claims == 4
    assert {r.claim for r in result_a.rows} == {r.claim for r in result_b.rows}  # same seed -> same sample


def test_no_judge_configured_reports_not_run_never_a_fabricated_rate():
    result = run_citation_faithfulness_eval(SECTIONS, project_id="proj1")
    assert result.status == "not_run"
    assert "no LLM configured" in result.reason
    assert result.faithfulness_rate is None
    assert result.total_claims == 3  # claims were still counted, just not judged


def test_draft_with_no_citations_reports_not_run():
    result = run_citation_faithfulness_eval({"s1": "Plain prose with no citations at all."}, project_id="proj1")
    assert result.status == "not_run"
    assert result.total_claims == 0


def test_for_run_with_no_llm_configured_reports_not_run(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from draftforge.graph import build_graph

    run_id = build_graph.create_run("proj1", "ieee_report", {})
    path = section_draft_path(run_id, "introduction")
    path.write_text("Some drafted prose citing a source [@doe2020].", encoding="utf-8")

    result = run_citation_faithfulness_eval_for_run(run_id, live=False)
    assert result.status == "not_run"
    assert "no LLM configured" in result.reason
    assert result.total_claims == 1


def test_for_run_unknown_run_id_reports_not_run(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    result = run_citation_faithfulness_eval_for_run("does-not-exist", live=False)
    assert result.status == "not_run"
    assert "unknown run_id" in result.reason


def test_for_run_with_no_drafted_sections_reports_not_run(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from draftforge.graph import build_graph

    run_id = build_graph.create_run("proj1", "ieee_report", {})
    run_dir(run_id)  # ensure the run dir exists but has no sections/ subdir

    result = run_citation_faithfulness_eval_for_run(run_id, live=False)
    assert result.status == "not_run"
    assert "no drafted section files" in result.reason


def test_spot_check_markdown_table_and_csv_export(tmp_path):
    judge = _FakeJudge(verdicts=[{"supported": True, "reason": "entailed"}])
    hits = {"conclusion": [_hit(["lee2019"], "The approach generalizes well.")]}
    result = run_citation_faithfulness_eval(
        {"conclusion": SECTIONS["conclusion"]},
        project_id="proj1",
        valid_bibkeys={"lee2019"},
        sample_size=5,
        provider=judge,
        model_name="fake-model",
        retrieve_fn=lambda *a, **k: hits["conclusion"],
    )
    table = spot_check_markdown_table(result.rows)
    assert "SUPPORTED" in table
    assert "conclusion" in table

    csv_path = write_spot_check_csv(result.rows, tmp_path / "spot_check.csv")
    content = csv_path.read_text(encoding="utf-8")
    assert "section_id" in content.splitlines()[0]
    assert "conclusion" in content
