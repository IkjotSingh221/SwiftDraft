"""Phase 7 compliance eval (hermetic, no LLM/GPU): real `validate_document`
runs over >=3 fixture drafts per shipped format spec. Every number here is
genuinely computed by the unedited Phase 2 validator -- see
`evals/compliance_eval.py`'s module docstring for how the fixture drafts are
built and the two independent completeness checks they have to satisfy."""

from __future__ import annotations

from draftforge.evals.compliance_eval import (
    build_compliant_draft,
    run_compliance_eval,
)
from draftforge.formats.schema import list_available_specs, load_spec
from draftforge.formats.validator import Severity, validate_document


def test_compliant_draft_has_zero_violations_for_both_shipped_specs():
    for summary in list_available_specs():
        spec = load_spec(summary.path)
        draft = build_compliant_draft(spec)
        violations = validate_document(draft, spec)
        assert violations == [], f"{spec.spec_id}: expected zero violations, got {violations}"


def test_run_compliance_eval_ships_at_least_three_drafts_per_spec():
    result = run_compliance_eval()
    spec_ids = {s.spec_id for s in result.specs}
    assert spec_ids == {"ieee_report", "university_thesis"}
    for spec in result.specs:
        assert spec.total_drafts >= 3


def test_run_compliance_eval_pass_rate_reflects_some_pass_some_fail():
    result = run_compliance_eval()
    for spec in result.specs:
        # At least one compliant draft passes, and at least one deliberately
        # broken draft fails -- a real (not trivially 0% or 100%) pass rate.
        assert 0.0 < spec.pass_rate < 1.0
        assert spec.passed_drafts >= 1
        assert spec.passed_drafts < spec.total_drafts


def test_each_expected_violation_code_is_produced_by_its_own_variant():
    result = run_compliance_eval()
    by_key = {(d.spec_id, d.draft_name): d for d in result.drafts}

    for spec_id in ("ieee_report", "university_thesis"):
        compliant = by_key[(spec_id, "compliant")]
        assert compliant.passed is True
        assert compliant.violation_count == 0

        over_limit = by_key[(spec_id, "over_word_limit")]
        assert over_limit.passed is False
        assert "word_count_over" in over_limit.violation_codes

        missing = by_key[(spec_id, "missing_required_section")]
        assert missing.passed is False
        assert "missing_required_subsection" in missing.violation_codes

        malformed = by_key[(spec_id, "malformed_citation")]
        assert malformed.passed is False
        assert "malformed_citation_marker" in malformed.violation_codes


def test_violation_code_counts_sum_matches_total_violations_across_drafts():
    result = run_compliance_eval()
    for spec in result.specs:
        drafts_for_spec = [d for d in result.drafts if d.spec_id == spec.spec_id]
        assert sum(d.violation_count for d in drafts_for_spec) == sum(spec.violation_code_counts.values())


def test_only_error_severity_violations_count_against_pass_rate():
    # Every ViolationCode this validator currently emits defaults to
    # Severity.ERROR (see compliance_eval.py's module docstring) -- this
    # test pins that assumption so a future change to validator.py that
    # introduces warnings is caught here rather than silently changing what
    # "pass" means for this eval.
    result = run_compliance_eval()
    for draft in result.drafts:
        if draft.violation_count > 0:
            assert draft.passed is False
