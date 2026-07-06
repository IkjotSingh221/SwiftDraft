"""Programmatic (no-LLM) format-compliance validator.

Per spec.md constraint #4: "Format compliance is checked with code, not LLM
judgment." Everything here is regex/line-based parsing over Markdown text --
deliberately not a full Markdown parser/AST, since section text is a small,
constrained, mostly-flat subset of Markdown (headings, prose, citation
markers, images, tables) and a hand-rolled parser is easier to reason about
and to write exhaustive tests against.

Two entry points, matching how the rest of the pipeline will call this:

- `validate_section(markdown, section_spec, context=...)` -- checks a single
  *already-drafted* leaf section's own text in isolation: word count, depth
  of any headings inside its own prose, heading numbering style, citation
  marker form, and figure/table caption rules. Phase 4/5 call this once per
  section as soon as a draft/redraft completes.
- `validate_document(sections, spec)` -- document-level checks that need the
  whole tree: which required sections/subsections are missing from the set
  of completed sections. It also re-runs `validate_section` for every
  provided section, so it is the one function Phase 5's compliance-checker
  node needs to call to get every violation across a document.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from draftforge.formats.schema import (
    CaptionRule,
    CaptionRules,
    CitationStyle,
    FormatSpec,
    FrontMatterSpec,
    HeadingNumbering,
    SectionSpec,
)

# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------


class ViolationCode(str, Enum):
    WORD_COUNT_UNDER = "word_count_under"
    WORD_COUNT_OVER = "word_count_over"
    ABSTRACT_WORD_COUNT_UNDER = "abstract_word_count_under"
    ABSTRACT_WORD_COUNT_OVER = "abstract_word_count_over"
    MISSING_REQUIRED_SUBSECTION = "missing_required_subsection"
    UNEXPECTED_SUBSECTION = "unexpected_subsection"
    HEADING_TOO_DEEP = "heading_too_deep"
    HEADING_NUMBERING_MISMATCH = "heading_numbering_mismatch"
    MALFORMED_CITATION_MARKER = "malformed_citation_marker"
    FRONT_MATTER_FIELD_MISSING = "front_matter_field_missing"
    CAPTION_MISSING = "caption_missing"
    CAPTION_MALFORMED = "caption_malformed"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    code: ViolationCode
    severity: Severity = Severity.ERROR
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationContext(BaseModel):
    """Format-level info a single section needs but doesn't carry itself.

    Built once per `FormatSpec` (see `validate_document`) and passed down to
    every `validate_section` call so each section is checked against the
    same citation/numbering/caption rules.
    """

    model_config = ConfigDict(extra="forbid")

    heading_numbering: HeadingNumbering | None = None
    citation_style: CitationStyle | None = None
    captions: CaptionRules | None = None


# ---------------------------------------------------------------------------
# Small explicit Markdown parsing helpers (regex/line-based, not a full parser)
# ---------------------------------------------------------------------------

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_NUMBERING_PREFIX_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
_CITATION_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")
_VALID_CITE_KEY_RE = re.compile(r"^@[A-Za-z0-9][A-Za-z0-9_:.\-]*$")
_IMAGE_RE = re.compile(r"^!\[[^\]]*\]\([^)]*\)\s*$")
_TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


class Heading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int
    number: str | None
    title: str
    line_no: int


def parse_headings(markdown: str) -> list[Heading]:
    """Parse ATX (`#`) headings, splitting off any leading numeric prefix."""
    headings: list[Heading] = []
    for line_no, line in enumerate(markdown.splitlines(), start=1):
        match = _ATX_HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        text = match.group(2).strip()
        num_match = _NUMBERING_PREFIX_RE.match(text)
        if num_match:
            number, title = num_match.group(1), num_match.group(2).strip()
        else:
            number, title = None, text
        headings.append(Heading(level=level, number=number, title=title, line_no=line_no))
    return headings


def count_words(markdown: str, *, exclude_headings: bool = True) -> int:
    """Word count via a simple alnum-token regex, excluding heading lines.

    Deliberately simple: counts `[A-Za-z0-9][A-Za-z0-9'-]*` tokens. Citation
    markers like `[@doe2020]` contribute at most one token (the key itself)
    since brackets/`@` are not word characters -- acceptable minor
    overcounting, documented in DECISIONS.md.
    """
    lines = markdown.splitlines()
    if exclude_headings:
        lines = [line for line in lines if not _ATX_HEADING_RE.match(line)]
    return len(_WORD_RE.findall("\n".join(lines)))


def find_citation_candidates(markdown: str) -> list[tuple[str, int]]:
    """Find every `[...]` bracket group that looks like a citation attempt.

    A bracket group is a "candidate" if it contains an `@` anywhere -- this
    catches both well-formed `[@key]` / `[@key1; @key2]` markers and
    malformed attempts (`[@]`, `[cite @key]`, `[@bad key]`) alike, so callers
    can validate FORM without needing to know real bibkeys.
    """
    candidates: list[tuple[str, int]] = []
    for line_no, line in enumerate(markdown.splitlines(), start=1):
        for match in _CITATION_BRACKET_RE.finditer(line):
            inner = match.group(1)
            if "@" in inner:
                candidates.append((inner, line_no))
    return candidates


def is_valid_citation_marker(inner: str) -> bool:
    """Form-only validity check for one `[...]` bracket's inner text.

    Valid: one or more `@bibkey` tokens separated by `;` (Pandoc's
    multi-citation syntax), each key starting with an alnum char and
    otherwise alnum/`_`/`:`/`.`/`-`. This checks *shape*, never whether the
    key actually exists in the bibliography store (that's the citation
    verifier's job in Phase 5).
    """
    keys = [k.strip() for k in inner.split(";")]
    return bool(keys) and all(_VALID_CITE_KEY_RE.match(k) for k in keys)


# ---------------------------------------------------------------------------
# Section-level validation
# ---------------------------------------------------------------------------


def _check_word_range(
    markdown: str, section_spec: SectionSpec
) -> list[Violation]:
    if section_spec.word_range is None:
        return []
    wc = count_words(markdown)
    wr = section_spec.word_range
    if wc < wr.min:
        return [
            Violation(
                section_id=section_spec.id,
                code=ViolationCode.WORD_COUNT_UNDER,
                message=f"Section '{section_spec.title}' has {wc} words, below the minimum of {wr.min}.",
                details={"word_count": wc, "min": wr.min, "max": wr.max},
            )
        ]
    if wc > wr.max:
        return [
            Violation(
                section_id=section_spec.id,
                code=ViolationCode.WORD_COUNT_OVER,
                message=f"Section '{section_spec.title}' has {wc} words, above the maximum of {wr.max}.",
                details={"word_count": wc, "min": wr.min, "max": wr.max},
            )
        ]
    return []


def _check_required_subsections(
    markdown: str, section_spec: SectionSpec
) -> list[Violation]:
    """Only meaningful when a leaf inlines its declared subsections as
    headings within its own prose (see module docstring); most tree
    completeness checking happens in `validate_document` instead."""
    if not section_spec.subsections:
        return []
    headings = parse_headings(markdown)
    present_titles = {h.title.strip().lower() for h in headings}
    violations: list[Violation] = []
    for child in section_spec.subsections:
        if child.required and child.title.strip().lower() not in present_titles:
            violations.append(
                Violation(
                    section_id=section_spec.id,
                    code=ViolationCode.MISSING_REQUIRED_SUBSECTION,
                    message=(
                        f"Required subsection '{child.title}' not found as a heading "
                        f"inside section '{section_spec.title}'."
                    ),
                    details={"missing_subsection_id": child.id, "missing_subsection_title": child.title},
                )
            )
    return violations


def _check_heading_depth(markdown: str, section_spec: SectionSpec) -> list[Violation]:
    violations: list[Violation] = []
    for heading in parse_headings(markdown):
        if heading.level > section_spec.max_heading_depth:
            violations.append(
                Violation(
                    section_id=section_spec.id,
                    code=ViolationCode.HEADING_TOO_DEEP,
                    message=(
                        f"Heading '{heading.title}' at line {heading.line_no} uses depth "
                        f"{heading.level}, exceeding the allowed max of {section_spec.max_heading_depth}."
                    ),
                    details={"line": heading.line_no, "level": heading.level, "max_heading_depth": section_spec.max_heading_depth},
                )
            )
    return violations


def _check_heading_numbering(
    markdown: str, section_spec: SectionSpec, numbering: HeadingNumbering | None
) -> list[Violation]:
    if numbering is None:
        return []
    violations: list[Violation] = []
    for heading in parse_headings(markdown):
        if heading.level < numbering.start_level:
            continue
        has_number = heading.number is not None
        if numbering.style == "decimal" and not has_number:
            violations.append(
                Violation(
                    section_id=section_spec.id,
                    code=ViolationCode.HEADING_NUMBERING_MISMATCH,
                    message=(
                        f"Heading '{heading.title}' at line {heading.line_no} is missing the "
                        f"required decimal numbering prefix."
                    ),
                    details={"line": heading.line_no},
                )
            )
        elif numbering.style == "none" and has_number:
            violations.append(
                Violation(
                    section_id=section_spec.id,
                    code=ViolationCode.HEADING_NUMBERING_MISMATCH,
                    message=(
                        f"Heading '{heading.title}' at line {heading.line_no} has a numbering "
                        f"prefix ('{heading.number}') but this spec requires unnumbered headings."
                    ),
                    details={"line": heading.line_no, "number": heading.number},
                )
            )
    return violations


def _check_citations(markdown: str, section_spec: SectionSpec, citation_style: CitationStyle | None) -> list[Violation]:
    violations: list[Violation] = []
    for inner, line_no in find_citation_candidates(markdown):
        if not is_valid_citation_marker(inner):
            violations.append(
                Violation(
                    section_id=section_spec.id,
                    code=ViolationCode.MALFORMED_CITATION_MARKER,
                    message=f"Malformed citation marker '[{inner}]' at line {line_no}.",
                    details={"raw": inner, "line": line_no},
                )
            )
    return violations


def _caption_pattern(rule: CaptionRule) -> re.Pattern[str]:
    prefix = re.escape(rule.prefix)
    if rule.numbering == "none":
        return re.compile(rf"^\*{{0,2}}{prefix}\*{{0,2}}\s*[:.]\s*(?P<text>.+)$", re.IGNORECASE)
    # decimal and chapter_decimal both look like "<prefix> <number>[.:]<text>"
    return re.compile(rf"^\*{{0,2}}{prefix}\s+(?P<num>\d+(?:\.\d+)*)\*{{0,2}}\s*[:.]?\s*(?P<text>.+)$", re.IGNORECASE)


def _caption_violation(
    section_id: str, kind: str, rule: CaptionRule, line_no: int, caption_line: str, matched: re.Match[str] | None
) -> Violation | None:
    if not caption_line:
        if rule.required:
            return Violation(
                section_id=section_id,
                code=ViolationCode.CAPTION_MISSING,
                message=f"{kind.capitalize()} near line {line_no} has no caption (expected prefix '{rule.prefix}').",
                details={"line": line_no, "kind": kind},
            )
        return None
    if matched is None:
        return Violation(
            section_id=section_id,
            code=ViolationCode.CAPTION_MALFORMED,
            message=(
                f"{kind.capitalize()} caption '{caption_line}' near line {line_no} does not match the required "
                f"format (prefix '{rule.prefix}', numbering '{rule.numbering}')."
            ),
            details={"line": line_no, "kind": kind, "text": caption_line},
        )
    caption_text = matched.group("text")
    word_count = len(_WORD_RE.findall(caption_text))
    if word_count < rule.min_words:
        return Violation(
            section_id=section_id,
            code=ViolationCode.CAPTION_MALFORMED,
            message=(
                f"{kind.capitalize()} caption near line {line_no} has {word_count} descriptive words, "
                f"below the minimum of {rule.min_words}."
            ),
            details={"line": line_no, "kind": kind, "word_count": word_count, "min_words": rule.min_words},
        )
    return None


def _next_nonblank(lines: list[str], start: int, step: int) -> tuple[int, str]:
    """Scan from `start` in direction `step` (+1/-1), skipping blank lines.

    Returns (index, stripped_text); index is -1 with empty text if the edge
    of the document is reached without finding a non-blank line.
    """
    i = start
    while 0 <= i < len(lines):
        stripped = lines[i].strip()
        if stripped:
            return i, stripped
        i += step
    return -1, ""


def _check_captions(markdown: str, section_spec: SectionSpec, captions: CaptionRules | None) -> list[Violation]:
    if captions is None:
        return []
    violations: list[Violation] = []
    lines = markdown.splitlines()
    fig_pattern = _caption_pattern(captions.figure)
    table_pattern = _caption_pattern(captions.table)

    for i, line in enumerate(lines):
        if _IMAGE_RE.match(line.strip()):
            cap_idx, caption_line = _next_nonblank(lines, i + 1, 1)
            matched = fig_pattern.match(caption_line) if caption_line else None
            report_line = (cap_idx + 1) if cap_idx >= 0 else (i + 1)
            v = _caption_violation(section_spec.id, "figure", captions.figure, report_line, caption_line, matched)
            if v:
                violations.append(v)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if _TABLE_SEP_RE.match(stripped):
            header_idx, header_line = _next_nonblank(lines, i - 1, -1)
            if header_idx < 0 or not _TABLE_ROW_RE.match(header_line):
                continue  # not actually a table (separator with no header row)
            cap_idx, caption_line = _next_nonblank(lines, header_idx - 1, -1)
            if cap_idx >= 0 and _TABLE_ROW_RE.match(caption_line):
                # The nearest non-blank line above the header is itself a
                # table row (or another table's caption) -- no real caption.
                cap_idx, caption_line = -1, ""
            matched = table_pattern.match(caption_line) if caption_line else None
            report_line = (cap_idx + 1) if cap_idx >= 0 else (header_idx + 1)
            v = _caption_violation(section_spec.id, "table", captions.table, report_line, caption_line, matched)
            if v:
                violations.append(v)

    return violations


def validate_section(
    markdown: str,
    section_spec: SectionSpec,
    *,
    context: ValidationContext | None = None,
) -> list[Violation]:
    """Validate one section's own drafted Markdown text against its spec node.

    Checks (all programmatic, no LLM): word count range, any subsection
    titles the spec declares that this section chose to inline as headings,
    heading nesting depth, heading numbering style, citation marker form,
    and figure/table caption rules. `context` supplies the format-wide
    numbering/citation/caption rules; omit it to skip those specific checks
    (useful for unit-testing word-count/heading-depth in isolation).
    """
    context = context or ValidationContext()
    violations: list[Violation] = []
    violations += _check_word_range(markdown, section_spec)
    violations += _check_required_subsections(markdown, section_spec)
    violations += _check_heading_depth(markdown, section_spec)
    violations += _check_heading_numbering(markdown, section_spec, context.heading_numbering)
    violations += _check_citations(markdown, section_spec, context.citation_style)
    violations += _check_captions(markdown, section_spec, context.captions)
    return violations


# ---------------------------------------------------------------------------
# Front matter / abstract
# ---------------------------------------------------------------------------


def validate_front_matter(
    front_matter_fields: dict[str, str],
    abstract_markdown: str | None,
    front_matter_spec: FrontMatterSpec,
) -> list[Violation]:
    """Check title-page fields are present and the abstract is within range.

    Uses the synthetic `section_id="__front_matter__"` since front matter
    isn't part of the section tree.
    """
    violations: list[Violation] = []
    for field_name in front_matter_spec.title_page_fields:
        if not front_matter_fields.get(field_name, "").strip():
            violations.append(
                Violation(
                    section_id="__front_matter__",
                    code=ViolationCode.FRONT_MATTER_FIELD_MISSING,
                    message=f"Required title-page field '{field_name}' is missing or empty.",
                    details={"field": field_name},
                )
            )

    if front_matter_spec.abstract_required and not (abstract_markdown or "").strip():
        violations.append(
            Violation(
                section_id="__front_matter__",
                code=ViolationCode.FRONT_MATTER_FIELD_MISSING,
                message="Abstract is required but missing.",
                details={"field": "abstract"},
            )
        )
    elif abstract_markdown and front_matter_spec.abstract_word_range is not None:
        wc = count_words(abstract_markdown)
        wr = front_matter_spec.abstract_word_range
        if wc < wr.min:
            violations.append(
                Violation(
                    section_id="__front_matter__",
                    code=ViolationCode.ABSTRACT_WORD_COUNT_UNDER,
                    message=f"Abstract has {wc} words, below the minimum of {wr.min}.",
                    details={"word_count": wc, "min": wr.min, "max": wr.max},
                )
            )
        elif wc > wr.max:
            violations.append(
                Violation(
                    section_id="__front_matter__",
                    code=ViolationCode.ABSTRACT_WORD_COUNT_OVER,
                    message=f"Abstract has {wc} words, above the maximum of {wr.max}.",
                    details={"word_count": wc, "min": wr.min, "max": wr.max},
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Document-level validation
# ---------------------------------------------------------------------------


def _check_missing_tree_sections(present_ids: set[str], nodes: list[SectionSpec]) -> list[Violation]:
    violations: list[Violation] = []
    for node in nodes:
        if node.required and node.id not in present_ids:
            violations.append(
                Violation(
                    section_id=node.id,
                    code=ViolationCode.MISSING_REQUIRED_SUBSECTION,
                    message=f"Required section '{node.title}' ({node.id}) is missing from the document.",
                    details={"missing_section_id": node.id, "missing_section_title": node.title},
                )
            )
            # Parent absent implies its whole subtree is absent too; one
            # violation for the parent is enough, no need to cascade.
            continue
        if node.subsections:
            violations += _check_missing_tree_sections(present_ids, node.subsections)
    return violations


def validate_document(
    sections: list[tuple[SectionSpec, str]],
    spec: FormatSpec,
    *,
    front_matter_fields: dict[str, str] | None = None,
    abstract_markdown: str | None = None,
) -> list[Violation]:
    """Validate a whole document: tree completeness + every section's own text.

    `sections` is the set of completed (section_spec, markdown) pairs, e.g.
    one leaf per fan-out drafter in Phase 4/5. `front_matter_fields` /
    `abstract_markdown` are optional extras beyond the core two-argument
    signature -- pass them when front-matter/abstract compliance should be
    checked as part of the same call.
    """
    present_ids = {section_spec.id for section_spec, _ in sections}
    violations: list[Violation] = list(_check_missing_tree_sections(present_ids, spec.sections))

    context = ValidationContext(
        heading_numbering=spec.heading_numbering,
        citation_style=spec.citation_style,
        captions=spec.captions,
    )
    for section_spec, markdown in sections:
        violations += validate_section(markdown, section_spec, context=context)

    if front_matter_fields is not None or abstract_markdown is not None:
        violations += validate_front_matter(front_matter_fields or {}, abstract_markdown, spec.front_matter)

    return violations
