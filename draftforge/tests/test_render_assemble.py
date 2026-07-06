"""Phase 6 assembly (hermetic, no Pandoc): given a small fake run (a couple of
`sections/{id}.md` files + an outline/leaf_briefs-shaped `RunState`), assert
the assembled Markdown puts sections in outline order, at the right heading
depth, splices each leaf's body verbatim (citation markers preserved), skips
the auto-generated "references" leaf, renumbers figure/table captions
coherently, and front matter is populated from run_config with sane
placeholders otherwise. Pure -- no subprocess, no pandoc needed."""

from __future__ import annotations

import pytest

from draftforge.formats.schema import load_spec
from draftforge.graph.drafter import section_draft_path
from draftforge.render.assemble import (
    AssembleError,
    assemble_markdown,
    assemble_run,
    build_front_matter,
)

IEEE_SPEC = load_spec("specs/ieee_report.json")
THESIS_SPEC = load_spec("specs/university_thesis.json")


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))


def _write(run_id: str, leaf_id: str, text: str) -> None:
    section_draft_path(run_id, leaf_id).write_text(text, encoding="utf-8")


IEEE_OUTLINE = [
    {"id": "introduction", "title": "Introduction", "children": []},
    {"id": "related_work", "title": "Related Work", "children": []},
    {
        "id": "method",
        "title": "Method",
        "children": [
            {"id": "method_data", "title": "Data", "children": []},
            {"id": "method_approach", "title": "Approach", "children": []},
        ],
    },
    {"id": "results", "title": "Results", "children": []},
    {"id": "conclusion", "title": "Conclusion", "children": []},
    {"id": "references", "title": "References", "children": []},
]


def test_sections_appear_in_outline_order_with_correct_heading_depth():
    run_id = "r1"
    for leaf in ("introduction", "related_work", "method_data", "method_approach", "results", "conclusion"):
        _write(run_id, leaf, f"Body of {leaf}.")

    markdown, sections = assemble_markdown(run_id, IEEE_OUTLINE, IEEE_SPEC)

    # Document order matches the outline's own order exactly.
    ids_in_order = [s.id for s in sections]
    assert ids_in_order == [
        "introduction", "related_work", "method", "method_data", "method_approach",
        "results", "conclusion", "references",
    ]

    # Heading depth: top-level nodes are level 1, "method"'s children level 2.
    by_id = {s.id: s for s in sections}
    assert by_id["introduction"].level == 1
    assert by_id["method"].level == 1
    assert by_id["method_data"].level == 2
    assert by_id["method_approach"].level == 2

    # Each ATX heading appears with the right number of "#"s, in order, in the
    # actual Markdown text too (not just the returned metadata).
    assert markdown.index("# Introduction") < markdown.index("# Related Work")
    assert markdown.index("# Related Work") < markdown.index("# Method")
    assert markdown.index("## Data") < markdown.index("## Approach")
    assert markdown.index("# Method") < markdown.index("## Data")
    assert markdown.index("# Results") < markdown.index("# Conclusion")


def test_leaf_body_is_spliced_verbatim_and_citation_markers_preserved():
    run_id = "r2"
    for leaf in ("introduction", "related_work", "method_data", "method_approach", "results", "conclusion"):
        _write(run_id, leaf, f"Body of {leaf}.")
    _write(run_id, "introduction", "Intro text citing [@smith2020] and [@doe2019;@lee2021].")

    markdown, _ = assemble_markdown(run_id, IEEE_OUTLINE, IEEE_SPEC)

    assert "Intro text citing [@smith2020] and [@doe2019;@lee2021]." in markdown
    assert "Body of method_data." in markdown
    assert "Body of conclusion." in markdown


def test_missing_draft_file_gets_a_visible_placeholder_not_a_crash():
    run_id = "r3"
    # Only some leaves have a draft on disk -- "results" is flagged/missing.
    for leaf in ("introduction", "related_work", "method_data", "method_approach", "conclusion"):
        _write(run_id, leaf, f"Body of {leaf}.")

    markdown, sections = assemble_markdown(run_id, IEEE_OUTLINE, IEEE_SPEC)

    assert "not drafted" in markdown
    by_id = {s.id: s for s in sections}
    assert by_id["results"].spliced is True  # heading still emitted, body is a placeholder


def test_references_leaf_is_skipped_entirely():
    """word_range is null for "references" -- Pandoc/citeproc auto-generates
    the bibliography instead of splicing a drafted file (see DECISIONS.md)."""
    run_id = "r4"
    for leaf in ("introduction", "related_work", "method_data", "method_approach", "results", "conclusion"):
        _write(run_id, leaf, f"Body of {leaf}.")
    _write(run_id, "references", "This should never appear in the output.")

    markdown, sections = assemble_markdown(run_id, IEEE_OUTLINE, IEEE_SPEC)

    assert "# References" not in markdown
    assert "This should never appear in the output." not in markdown
    by_id = {s.id: s for s in sections}
    assert by_id["references"].spliced is False


def test_figure_captions_renumbered_coherently_across_sections():
    """Each leaf's DocumentState snapshot at draft time can only guess a
    figure number (parallel fan-out) -- assembly deterministically fixes
    numbering up in final document order."""
    run_id = "r5"
    _write(run_id, "introduction", "Intro.\n\n![x](a.png)\nFig. 7: first figure, guessed wrong.")
    _write(run_id, "related_work", "No figures here.")
    _write(run_id, "method_data", "Data.\n\n![y](b.png)\nFig. 3: second figure, also guessed wrong.")
    for leaf in ("method_approach", "results", "conclusion"):
        _write(run_id, leaf, f"Body of {leaf}.")

    markdown, _ = assemble_markdown(run_id, IEEE_OUTLINE, IEEE_SPEC)

    assert "Fig. 1: first figure, guessed wrong." in markdown
    assert "Fig. 2: second figure, also guessed wrong." in markdown
    assert "Fig. 7" not in markdown
    assert "Fig. 3: second" not in markdown


def test_chapter_scoped_caption_numbering_resets_per_chapter():
    """university_thesis.json uses "chapter_decimal" table numbering --
    counters reset at each top-level outline node."""
    run_id = "r6"
    outline = [
        {"id": "introduction", "title": "Introduction", "children": [
            {"id": "background", "title": "Background", "children": []},
        ]},
        {"id": "methodology", "title": "Methodology", "children": []},
        {"id": "references", "title": "References", "children": []},
    ]
    _write(run_id, "background", "BG.\n\nTable 1: bg table.\n| a |\n|---|\n| 1 |")
    _write(run_id, "methodology", "Method.\n\nTable 1: method table.\n| a |\n|---|\n| 1 |")

    markdown, _ = assemble_markdown(run_id, outline, THESIS_SPEC)

    assert "Table 1.1: bg table." in markdown
    assert "Table 2.1: method table." in markdown


def test_assemble_run_raises_on_empty_outline():
    with pytest.raises(AssembleError):
        assemble_run("empty-run", {"outline": [], "run_config": {}}, IEEE_SPEC)


def test_assemble_run_end_to_end_sets_front_matter_and_number_sections():
    run_id = "r7"
    for leaf in ("introduction", "related_work", "method_data", "method_approach", "results", "conclusion"):
        _write(run_id, leaf, f"Body of {leaf}.")
    run_state = {
        "outline": IEEE_OUTLINE,
        "run_config": {"front_matter": {"title": "My Report", "authors": "A. Student"}},
    }

    doc = assemble_run(run_id, run_state, IEEE_SPEC)

    assert doc.number_sections is True  # ieee_report.json's heading_numbering.style == "decimal"
    assert doc.front_matter["title"] == "My Report"
    assert doc.front_matter["author"] == "A. Student"  # aliased from "authors"
    assert "abstract" in doc.front_matter  # abstract_required -> always present (placeholder if missing)
    assert "Body of introduction." in doc.markdown


# ---------------------------------------------------------------------------
# Front matter, in isolation
# ---------------------------------------------------------------------------


def test_front_matter_fields_default_to_bracketed_placeholders():
    fm = build_front_matter(IEEE_SPEC, run_config=None)
    for field_name in IEEE_SPEC.front_matter.title_page_fields:
        assert fm[field_name] == f"[{field_name.upper()}]"
    assert fm["abstract"].startswith("[Abstract not yet drafted")


def test_front_matter_underscore_fields_get_a_hyphenated_alias():
    fm = build_front_matter(
        THESIS_SPEC, run_config={"front_matter": {"submission_date": "2026-05-01"}}
    )
    assert fm["submission_date"] == "2026-05-01"
    assert fm["submission-date"] == "2026-05-01"


def test_front_matter_supplied_abstract_is_used_verbatim():
    fm = build_front_matter(IEEE_SPEC, run_config={"front_matter": {"abstract": "A short abstract."}})
    assert fm["abstract"] == "A short abstract."
