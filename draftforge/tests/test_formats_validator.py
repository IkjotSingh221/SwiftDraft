"""Programmatic format-compliance validator: crafted violations are all
caught, and a compliant fixture passes with zero violations.

Per spec.md constraint #4, none of this touches an LLM -- it's all
regex/line-based Markdown parsing, exercised here directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from draftforge.formats.schema import (
    CaptionRule,
    CaptionRules,
    CitationStyle,
    FormatSpec,
    FrontMatterSpec,
    HeadingNumbering,
    SectionSpec,
    WordRange,
)
from draftforge.formats.validator import (
    ValidationContext,
    ViolationCode,
    validate_document,
    validate_front_matter,
    validate_section,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Crafted violations -- one assert per violation type
# ---------------------------------------------------------------------------


def test_word_count_over_limit_is_caught():
    section = SectionSpec(
        id="conclusion",
        title="Conclusion",
        max_heading_depth=1,
        word_range=WordRange(min=10, max=60),
    )
    violations = validate_section(_read("over_limit_section.md"), section)
    assert any(v.code == ViolationCode.WORD_COUNT_OVER for v in violations)


def test_word_count_under_limit_is_caught():
    section = SectionSpec(
        id="short",
        title="Short",
        max_heading_depth=1,
        word_range=WordRange(min=500, max=1000),
    )
    violations = validate_section("A very short section.", section)
    assert any(v.code == ViolationCode.WORD_COUNT_UNDER for v in violations)


def test_missing_required_subsection_is_caught():
    section = SectionSpec(
        id="method",
        title="Method",
        max_heading_depth=2,
        subsections=[
            SectionSpec(id="data", title="Data", required=True),
            SectionSpec(id="approach", title="Approach", required=True),
        ],
    )
    violations = validate_section(_read("missing_subsection_section.md"), section)
    missing = [v for v in violations if v.code == ViolationCode.MISSING_REQUIRED_SUBSECTION]
    assert len(missing) == 1
    assert missing[0].details["missing_subsection_id"] == "data"


def test_heading_too_deep_is_caught():
    section = SectionSpec(id="method", title="Method", max_heading_depth=2)
    violations = validate_section(_read("heading_too_deep_section.md"), section)
    assert any(v.code == ViolationCode.HEADING_TOO_DEEP for v in violations)


def test_malformed_citation_marker_is_caught():
    section = SectionSpec(id="related_work", title="Related Work", max_heading_depth=1)
    violations = validate_section(_read("malformed_citation_section.md"), section)
    malformed = [v for v in violations if v.code == ViolationCode.MALFORMED_CITATION_MARKER]
    # Fixture has two malformed markers: "[@bad key]" and "[@]".
    assert len(malformed) == 2
    # The well-formed "[@smith2019]" marker must NOT be flagged.
    assert all("smith2019" not in v.details.get("raw", "") for v in malformed)


def test_heading_numbering_mismatch_is_caught():
    section = SectionSpec(id="method", title="Method", max_heading_depth=1)
    context = ValidationContext(heading_numbering=HeadingNumbering(style="decimal"))
    violations = validate_section("# Method\n\nSome unnumbered prose here.", section, context=context)
    assert any(v.code == ViolationCode.HEADING_NUMBERING_MISMATCH for v in violations)


def test_heading_numbering_forbidden_when_present_is_caught():
    section = SectionSpec(id="method", title="Method", max_heading_depth=1)
    context = ValidationContext(heading_numbering=HeadingNumbering(style="none"))
    violations = validate_section("# 1 Method\n\nSome prose here.", section, context=context)
    assert any(v.code == ViolationCode.HEADING_NUMBERING_MISMATCH for v in violations)


def test_caption_missing_is_caught():
    section = SectionSpec(id="results", title="Results", max_heading_depth=1)
    context = ValidationContext(
        captions=CaptionRules(
            figure=CaptionRule(prefix="Fig.", numbering="decimal", required=True),
            table=CaptionRule(prefix="Table", numbering="decimal", required=True),
        )
    )
    # Image is the last thing in the section -- no caption line follows it
    # at all (as opposed to a caption line being present but malformed).
    markdown = "# Results\n\n![a chart](chart.png)\n"
    violations = validate_section(markdown, section, context=context)
    assert any(v.code == ViolationCode.CAPTION_MISSING for v in violations)


def test_caption_present_but_malformed_is_caught():
    section = SectionSpec(id="results", title="Results", max_heading_depth=1)
    context = ValidationContext(
        captions=CaptionRules(
            figure=CaptionRule(prefix="Fig.", numbering="decimal", required=True),
            table=CaptionRule(prefix="Table", numbering="decimal", required=True),
        )
    )
    markdown = "# Results\n\n![a chart](chart.png)\n\nSome prose with no caption line at all.\n"
    violations = validate_section(markdown, section, context=context)
    assert any(v.code == ViolationCode.CAPTION_MALFORMED for v in violations)


def test_abstract_over_limit_is_caught():
    front_matter = FrontMatterSpec(
        title_page_fields=["title", "authors", "affiliation", "contact_email"],
        abstract_required=True,
        abstract_word_range=WordRange(min=150, max=250),
    )
    fields = {"title": "T", "authors": "A", "affiliation": "Aff", "contact_email": "a@b.com"}
    violations = validate_front_matter(fields, _read("abstract_over_limit.md"), front_matter)
    assert any(v.code == ViolationCode.ABSTRACT_WORD_COUNT_OVER for v in violations)


def test_front_matter_field_missing_is_caught():
    front_matter = FrontMatterSpec(title_page_fields=["title", "authors"], abstract_required=False)
    violations = validate_front_matter({"title": "Only title present"}, None, front_matter)
    missing = [v for v in violations if v.code == ViolationCode.FRONT_MATTER_FIELD_MISSING]
    assert any(v.details.get("field") == "authors" for v in missing)


# ---------------------------------------------------------------------------
# Compliant fixture -> zero violations
# ---------------------------------------------------------------------------


def _compliant_context() -> ValidationContext:
    return ValidationContext(
        heading_numbering=HeadingNumbering(style="decimal", start_level=1),
        citation_style=CitationStyle(style_id="ieee", csl_file="csl/ieee.csl", marker_style="pandoc_at_key"),
        captions=CaptionRules(
            figure=CaptionRule(prefix="Fig.", numbering="decimal", required=True, min_words=3),
            table=CaptionRule(prefix="Table", numbering="decimal", required=True, min_words=3),
        ),
    )


def test_compliant_fixture_has_zero_violations():
    section = SectionSpec(
        id="results",
        title="Results",
        max_heading_depth=2,
        word_range=WordRange(min=60, max=220),
        subsections=[SectionSpec(id="metrics", title="Metrics", required=True, max_heading_depth=1)],
    )
    violations = validate_section(_read("compliant_section.md"), section, context=_compliant_context())
    assert violations == []


# ---------------------------------------------------------------------------
# Document-level validation
# ---------------------------------------------------------------------------


def _tiny_spec() -> FormatSpec:
    return FormatSpec(
        spec_id="tiny",
        name="Tiny",
        version="1.0.0",
        sections=[
            SectionSpec(
                id="method",
                title="Method",
                subsections=[
                    SectionSpec(id="data", title="Data", required=True),
                    SectionSpec(id="approach", title="Approach", required=True),
                ],
            ),
            SectionSpec(id="intro", title="Introduction", required=True),
        ],
        citation_style=CitationStyle(style_id="x", csl_file="csl/x.csl", marker_style="pandoc_at_key"),
        heading_numbering=HeadingNumbering(style="none"),
        front_matter=FrontMatterSpec(title_page_fields=[]),
        captions=CaptionRules(
            figure=CaptionRule(prefix="Fig.", numbering="none"),
            table=CaptionRule(prefix="Table", numbering="none"),
        ),
    )


def test_validate_document_flags_missing_top_level_section_without_cascading():
    spec = _tiny_spec()
    intro_spec = spec.sections[1]
    violations = validate_document([(intro_spec, "Some intro text here that is fine.")], spec)
    # "method" (and its two required subsections) are entirely absent from
    # the provided sections -- exactly one violation for the missing
    # parent, no cascading duplicate violations for its children.
    method_violations = [v for v in violations if v.section_id == "method"]
    assert len(method_violations) == 1
    assert method_violations[0].code == ViolationCode.MISSING_REQUIRED_SUBSECTION
    assert not any(v.section_id in {"data", "approach"} for v in violations)


def test_validate_document_runs_validate_section_for_each_provided_section():
    spec = _tiny_spec()
    intro_spec = spec.sections[1]
    method_spec = spec.sections[0]
    # Provide both top-level sections; "intro" is way too short relative to
    # a word_range we attach just for this test via a fresh SectionSpec.
    short_intro = intro_spec.model_copy(update={"word_range": WordRange(min=100, max=200)})
    violations = validate_document(
        [(short_intro, "Too short."), (method_spec, "## Data\n\nok\n\n## Approach\n\nok")],
        spec,
    )
    assert any(v.section_id == "intro" and v.code == ViolationCode.WORD_COUNT_UNDER for v in violations)


def test_validate_document_with_front_matter_and_abstract():
    spec = _tiny_spec()
    spec = spec.model_copy(
        update={
            "front_matter": FrontMatterSpec(
                title_page_fields=["title"],
                abstract_required=True,
                abstract_word_range=WordRange(min=150, max=250),
            )
        }
    )
    intro_spec = spec.sections[1]
    method_spec = spec.sections[0]
    violations = validate_document(
        [
            (intro_spec, "Intro text."),
            (method_spec, "## Data\n\nok\n\n## Approach\n\nok"),
        ],
        spec,
        front_matter_fields={},
        abstract_markdown=_read("abstract_over_limit.md"),
    )
    assert any(v.section_id == "__front_matter__" and v.code == ViolationCode.FRONT_MATTER_FIELD_MISSING for v in violations)
    assert any(v.code == ViolationCode.ABSTRACT_WORD_COUNT_OVER for v in violations)
