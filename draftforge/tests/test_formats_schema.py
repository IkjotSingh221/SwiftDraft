"""Format spec loader: JSON-Schema validation, pydantic round-trip, and the
two example specs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from draftforge.formats.schema import (
    FormatSpec,
    FormatSpecError,
    SPECS_DIR,
    list_available_specs,
    load_spec,
    load_spec_from_dict,
)


@pytest.mark.parametrize("spec_id", ["ieee_report", "university_thesis"])
def test_load_spec_loads_both_example_specs(spec_id: str):
    spec = load_spec(SPECS_DIR / f"{spec_id}.json")
    assert isinstance(spec, FormatSpec)
    assert spec.spec_id == spec_id
    assert len(spec.sections) > 0
    assert spec.citation_style.csl_file
    assert spec.heading_numbering.style in {"decimal", "none"}


def test_example_specs_round_trip_through_pydantic():
    spec = load_spec(SPECS_DIR / "ieee_report.json")
    # Round-trip: dump back to a dict and re-load through the dict path.
    dumped = spec.model_dump(mode="json")
    reloaded = load_spec_from_dict(dumped)
    assert reloaded == spec


def test_ieee_report_section_ids():
    spec = load_spec(SPECS_DIR / "ieee_report.json")
    ids = {s.id for s in spec.iter_all_sections()}
    assert {"introduction", "related_work", "method", "results", "conclusion", "references"} <= ids
    assert spec.find_section("method") is not None
    assert spec.find_section("method_data") is not None


def test_university_thesis_nested_sections():
    spec = load_spec(SPECS_DIR / "university_thesis.json")
    lit_review = spec.find_section("literature_review")
    assert lit_review is not None
    child_ids = {c.id for c in lit_review.subsections}
    assert {"related_work", "research_gap"} <= child_ids


def test_list_available_specs_finds_both():
    summaries = list_available_specs(SPECS_DIR)
    ids = {s.spec_id for s in summaries}
    assert {"ieee_report", "university_thesis"} <= ids
    # The JSON Schema file itself must not show up as a "spec".
    assert "format_spec" not in ids


def test_malformed_spec_missing_required_field_raises():
    bad = {"spec_id": "bad", "name": "Bad"}  # missing version/sections/etc.
    with pytest.raises(FormatSpecError, match="schema validation"):
        load_spec_from_dict(bad)


def test_malformed_spec_wrong_type_raises():
    bad = {
        "spec_id": "bad",
        "name": "Bad",
        "version": "1.0.0",
        "sections": "not-a-list",
        "citation_style": {"style_id": "x", "csl_file": "x.csl", "marker_style": "pandoc_at_key"},
        "heading_numbering": {"style": "none"},
        "front_matter": {"title_page_fields": []},
        "captions": {
            "figure": {"prefix": "Fig.", "numbering": "none"},
            "table": {"prefix": "Table", "numbering": "none"},
        },
    }
    with pytest.raises(FormatSpecError):
        load_spec_from_dict(bad)


def test_load_spec_missing_file_raises_clear_error(tmp_path: Path):
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FormatSpecError, match="could not read"):
        load_spec(missing)


def test_load_spec_invalid_json_raises_clear_error(tmp_path: Path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(FormatSpecError, match="not valid JSON"):
        load_spec(bad_file)


def test_word_range_min_greater_than_max_rejected():
    from draftforge.formats.schema import WordRange

    with pytest.raises(ValueError):
        WordRange(min=100, max=10)
