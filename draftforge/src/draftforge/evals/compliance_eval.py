"""Phase 7: compliance eval — real, code-computed pass rate per format spec.

Per spec.md constraint #4 ("format compliance is checked with code, not LLM
judgment"), this eval needs no LLM/GPU at all: it runs the actual
`formats.validator.validate_document` (unedited, reused verbatim) over a set
of fixture "drafts" per shipped format spec and reports a genuine pass rate
plus a violation-code breakdown. Every number this module produces is real
(computed by the validator against real generated Markdown) -- unlike
retrieval_eval.py, there is nothing synthetic-labeled here.

## Where the fixture drafts come from

`formats/schema.py`'s two shipped specs (`ieee_report`, `university_thesis`)
have section word ranges from a few hundred to several thousand words, which
makes hand-typed Markdown fixtures impractical for the larger spec.
`_filler_body` deterministically generates prose (a fixed word pool, cycled)
sized to land exactly inside (or, for the "bad" variants, deliberately
outside) a section's `word_range` -- reproducible, inspectable, and "small"
in the sense that it's a few lines of code, not thousands of lines of
committed prose. See DECISIONS.md.

## `validate_document`'s two independent completeness checks (important!)

Discovered while building this eval, and worth documenting since it shapes
`_build_entries` below: `validate_document`'s tree-completeness pass
(`_check_missing_tree_sections`) requires EVERY required node in the WHOLE
spec tree -- container AND leaf, at every depth (e.g. `ieee_report`'s
`method` *and* its children `method_data`/`method_approach`) -- to have its
OWN `(SectionSpec, markdown)` entry in the `sections` list passed in.
Separately, `validate_section`'s own `_check_required_subsections` (run once
per provided entry) checks whether a container's *declared* subsections
appear as headings *within that container's own provided text*. These are
independent checks on independent data, so a "compliant" draft has to
satisfy both: a full separate entry for every required node PLUS a
numbered, title-matching stub heading for each required child inlined in
its parent's own entry. `_build_entries` does both. This is a real, observed
property of the unedited Phase 2 validator (`formats/validator.py`, which
Phase 7 may not edit) -- not a Phase 7 design choice -- documented here and
in DECISIONS.md/README.md as a known gap for Phase 8's attention, since
Phase 5's live per-section compliance check only ever calls
`validate_section` (never `validate_document`), so this interaction has
never been exercised end-to-end before this eval.

## The four fixture drafts (per spec)

- `compliant`: every required node (every depth) present, in range, numbered,
  with required subsections inlined as stub headings. Expected: zero
  violations.
- `over_word_limit`: `conclusion` regenerated well over its `word_range.max`.
  Expected: `WORD_COUNT_OVER`.
- `missing_required_section`: `conclusion`'s entry dropped entirely.
  Expected: `MISSING_REQUIRED_SUBSECTION`.
- `malformed_citation`: a malformed `[@bad key]` marker appended to the
  first entry. Expected: `MALFORMED_CITATION_MARKER`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from draftforge.formats.schema import FormatSpec, SectionSpec, list_available_specs, load_spec
from draftforge.formats.validator import Severity, Violation, validate_document

FIXTURE_BIBKEY = "fixture2024"

_FILLER_POOL = (
    "the harness evaluates retrieval quality across dense sparse and hybrid "
    "configurations using a small labeled fixture set of source passages "
    "students annotate queries with the chunk identifiers they consider "
    "relevant before running the pipeline against every configuration and "
    "the resulting draft is checked programmatically against the format "
    "specification before it is ever shown to a reviewer"
).split()


def _filler_body(n_words: int, *, cite_key: str | None = None, seed: int = 0) -> str:
    """Deterministic filler prose totaling exactly `n_words` word-tokens as
    counted by `formats.validator.count_words` (one citation marker, if
    given, contributes exactly one of those tokens -- its key)."""
    if n_words <= 0:
        return "Filler."
    reserve = 1 if cite_key else 0
    target = max(n_words - reserve, 0)
    words = [_FILLER_POOL[(seed + i) % len(_FILLER_POOL)] for i in range(target)]
    sentences: list[str] = []
    for i in range(0, len(words), 12):
        chunk = words[i : i + 12]
        if not chunk:
            continue
        s = " ".join(chunk)
        sentences.append(s[0].upper() + s[1:] + ".")
    if not sentences:
        sentences = ["Filler."]
    if cite_key:
        sentences[-1] = sentences[-1][:-1] + f" [@{cite_key}]."
    return " ".join(sentences)


def _heading(number: str, title: str, level: int) -> str:
    return f"{'#' * level} {number} {title}"


def _midpoint(section: SectionSpec) -> int:
    assert section.word_range is not None
    return (section.word_range.min + section.word_range.max) // 2


def _build_entries(spec: FormatSpec) -> list[tuple[SectionSpec, str]]:
    """One `(SectionSpec, markdown)` entry per REQUIRED node at EVERY depth
    (see module docstring for why both a container and its children each
    need their own entry). Each entry's own text also inlines a numbered,
    title-matching stub heading for each of its own required children, so
    both of `validate_document`'s independent completeness checks pass."""
    entries: list[tuple[SectionSpec, str]] = []

    def walk(node: SectionSpec, number: str, seed: int) -> None:
        if not node.required:
            return
        required_children = [c for c in node.subsections if c.required]
        parts = [_heading(number, node.title, 1)]
        if node.word_range is not None:
            cite_key = FIXTURE_BIBKEY if not required_children else None
            parts.append(_filler_body(_midpoint(node), cite_key=cite_key, seed=seed))
        elif not node.subsections:
            # e.g. "references": no word range, no subsections -- Pandoc/CSL
            # generates the real bibliography; a short visible placeholder
            # is enough here since word count isn't checked either way.
            parts.append("References are generated automatically by the citation processor.")
        for i, child in enumerate(required_children, start=1):
            # Stub heading only (title match for `_check_required_subsections`
            # on THIS node's own text) -- `child` gets its own full entry via
            # the recursive `walk` call below, satisfying the tree-completeness
            # check independently.
            parts.append(_heading(f"{number}.{i}", child.title, 2))
        entries.append((node, "\n\n".join(parts)))

        for i, child in enumerate(node.subsections, start=1):
            walk(child, f"{number}.{i}", seed=seed * 10 + i)

    for i, top in enumerate(spec.sections, start=1):
        walk(top, str(i), seed=i)
    return entries


def build_compliant_draft(spec: FormatSpec) -> list[tuple[SectionSpec, str]]:
    """A genuinely compliant draft: every required node, at every depth,
    present, in range, numbered, with required subsections inlined."""
    return _build_entries(spec)


def _draft_variants(spec: FormatSpec) -> dict[str, list[tuple[SectionSpec, str]]]:
    compliant = build_compliant_draft(spec)
    variants: dict[str, list[tuple[SectionSpec, str]]] = {"compliant": compliant}

    conclusion = spec.find_section("conclusion")
    if conclusion is not None and conclusion.word_range is not None and not conclusion.subsections:
        idx = next(i for i, (s, _) in enumerate(compliant) if s.id == "conclusion")

        over_limit = list(compliant)
        bad_words = conclusion.word_range.max + 150
        over_limit[idx] = (
            conclusion,
            _heading(str(idx + 1), conclusion.title, 1)
            + "\n\n"
            + _filler_body(bad_words, cite_key=FIXTURE_BIBKEY, seed=999),
        )
        variants["over_word_limit"] = over_limit

        variants["missing_required_section"] = [pair for pair in compliant if pair[0].id != "conclusion"]

    if compliant:
        first_spec, first_md = compliant[0]
        malformed = list(compliant)
        malformed[0] = (first_spec, first_md + "\n\nSee also [@bad key] for further discussion.")
        variants["malformed_citation"] = malformed

    return variants


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class DraftFixtureResult(BaseModel):
    spec_id: str
    draft_name: str
    passed: bool
    violation_count: int
    violation_codes: list[str] = Field(default_factory=list)


class SpecComplianceSummary(BaseModel):
    spec_id: str
    total_drafts: int
    passed_drafts: int
    pass_rate: float
    violation_code_counts: dict[str, int] = Field(default_factory=dict)


class ComplianceEvalResult(BaseModel):
    specs: list[SpecComplianceSummary] = Field(default_factory=list)
    drafts: list[DraftFixtureResult] = Field(default_factory=list)


def run_compliance_eval(spec_ids: list[str] | None = None) -> ComplianceEvalResult:
    """Run `validate_document` over >=3 fixture drafts for each of
    `spec_ids` (default: every spec `list_available_specs` finds, i.e. both
    shipped example specs) and report a real pass rate + violation-code
    breakdown per spec. No LLM/GPU; fully hermetic."""
    if spec_ids is None:
        spec_ids = [s.spec_id for s in list_available_specs()]

    by_path = {s.spec_id: s.path for s in list_available_specs()}
    specs_out: list[SpecComplianceSummary] = []
    drafts_out: list[DraftFixtureResult] = []

    for spec_id in spec_ids:
        path = by_path.get(spec_id)
        if path is None:
            continue
        spec = load_spec(path)
        variants = _draft_variants(spec)
        code_counts: dict[str, int] = {}
        passed = 0
        for name, sections in variants.items():
            violations: list[Violation] = validate_document(sections, spec)
            errors = [v for v in violations if v.severity == Severity.ERROR]
            ok = len(errors) == 0
            if ok:
                passed += 1
            for v in violations:
                code_counts[v.code.value] = code_counts.get(v.code.value, 0) + 1
            drafts_out.append(
                DraftFixtureResult(
                    spec_id=spec_id,
                    draft_name=name,
                    passed=ok,
                    violation_count=len(violations),
                    violation_codes=[v.code.value for v in violations],
                )
            )
        specs_out.append(
            SpecComplianceSummary(
                spec_id=spec_id,
                total_drafts=len(variants),
                passed_drafts=passed,
                pass_rate=passed / len(variants) if variants else 0.0,
                violation_code_counts=code_counts,
            )
        )

    return ComplianceEvalResult(specs=specs_out, drafts=drafts_out)
