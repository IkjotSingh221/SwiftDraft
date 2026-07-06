"""Phase 5 compliance checker (hermetic, NO LLM): it delegates to Phase 2's
programmatic validator, catches crafted violations, passes compliant text, and
exposes the error-severity subset that triggers a redraft. Constraint #4:
compliance is checked with code, not LLM judgment -- there is no provider here
at all."""

from __future__ import annotations

from draftforge.formats.schema import SectionSpec, WordRange
from draftforge.formats.validator import Severity, ValidationContext, ViolationCode
from draftforge.graph.compliance import (
    blocking_violations,
    check_section,
    compliance_feedback,
)


def _spec(**kw) -> SectionSpec:
    return SectionSpec(id="intro", title="Introduction", **kw)


def test_compliant_section_has_no_violations():
    spec = _spec(word_range=WordRange(min=3, max=50))
    md = "This introduction motivates the problem clearly and concisely for the reader."
    violations = check_section(md, spec, ValidationContext())
    assert violations == []


def test_over_word_limit_is_caught_and_is_blocking():
    spec = _spec(word_range=WordRange(min=1, max=5))
    md = "one two three four five six seven eight nine ten eleven twelve"
    violations = check_section(md, spec, ValidationContext())
    codes = {v.code for v in violations}
    assert ViolationCode.WORD_COUNT_OVER in codes
    blocking = blocking_violations(violations)
    assert blocking and all(v.severity == Severity.ERROR for v in blocking)


def test_heading_too_deep_is_caught():
    spec = _spec(max_heading_depth=2, word_range=WordRange(min=0, max=1000))
    md = "Intro prose.\n\n### Too deep heading\n\nmore prose"
    violations = check_section(md, spec, ValidationContext())
    assert any(v.code == ViolationCode.HEADING_TOO_DEEP for v in violations)


def test_compliance_feedback_lists_each_violation():
    spec = _spec(word_range=WordRange(min=1, max=2))
    md = "one two three four five"
    violations = check_section(md, spec, ValidationContext())
    fb = compliance_feedback(blocking_violations(violations))
    assert "above the maximum" in fb
    assert fb.startswith("- ")
