"""Phase 5: compliance checker.

Per spec.md constraint #4: "Format compliance is checked with CODE, not LLM
judgment." This module is a thin graph-facing adapter over Phase 2's
programmatic validator (`formats/validator.py`) -- there is deliberately no
LLM call anywhere in it. It maps a leaf `SectionBrief` to its `SectionSpec`
node in the `FormatSpec` tree and runs `validate_section` against the drafted
Markdown, returning the list of `Violation`s (empty == compliant).

The orchestrating `review_node` (`graph/review.py`) calls this per section as
each draft/redraft completes and, on any error-severity violation, routes the
section back to the drafter with the violations as feedback (bounded loops,
then flag for human).
"""

from __future__ import annotations

from draftforge.formats.schema import FormatSpec, SectionSpec
from draftforge.formats.validator import (
    Severity,
    ValidationContext,
    Violation,
    validate_section,
)


def context_for(spec: FormatSpec) -> ValidationContext:
    """Build the format-wide validation context once per run (numbering,
    citation style, caption rules) to pass to every per-section check."""
    return ValidationContext(
        heading_numbering=spec.heading_numbering,
        citation_style=spec.citation_style,
        captions=spec.captions,
    )


def check_section(
    markdown: str,
    section_spec: SectionSpec,
    context: ValidationContext,
) -> list[Violation]:
    """Programmatic compliance check for one section's drafted text.

    Pure delegation to `formats/validator.validate_section` -- kept as a named
    graph-layer function so `review_node` and the redraft loop have a single,
    obvious call site and so tests can assert 'compliance is code' by importing
    from here."""
    return validate_section(markdown, section_spec, context=context)


def blocking_violations(violations: list[Violation]) -> list[Violation]:
    """The error-severity subset that should trigger a redraft. Warnings are
    surfaced in the review UI but never force a redraft loop on their own."""
    return [v for v in violations if v.severity == Severity.ERROR]


def compliance_feedback(violations: list[Violation]) -> str:
    """Human/LLM-readable feedback string for a redraft, one line per
    violation (spec.md #4: violations 'route back to that section's drafter
    with feedback')."""
    return "\n".join(f"- {v.message}" for v in violations)
