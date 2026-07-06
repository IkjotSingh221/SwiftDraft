"""GROBID client: TEI parsing (hermetic, fixture-based) + the HTTP call boundary
(mocked with pytest-httpx — never hits a real GROBID server)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from draftforge.ingest.grobid import GrobidError, parse_pdf, parse_tei

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_tei.xml"


def _load_fixture_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def test_parse_tei_extracts_sections_with_hierarchy_and_pages():
    doc = parse_tei(_load_fixture_bytes())

    headings = [s.heading for s in doc.sections]
    assert "1 Introduction" in headings
    assert "1.1 Background" in headings
    assert "2 Methods" in headings
    assert "2.1 Data collection" in headings

    intro = next(s for s in doc.sections if s.heading == "1 Introduction")
    assert intro.section_path == ["1 Introduction"]
    assert "Photosynthesis" in intro.text
    assert intro.page == 1

    background = next(s for s in doc.sections if s.heading == "1.1 Background")
    assert background.section_path == ["1 Introduction", "1.1 Background"]
    # nested section inherits the page of the last <pb/> seen in its ancestor
    assert background.page == 1

    methods = next(s for s in doc.sections if s.heading == "2 Methods")
    assert methods.page == 2


def test_parse_tei_extracts_csl_json_bib_entries_with_stable_bibkeys():
    doc = parse_tei(_load_fixture_bytes())
    assert len(doc.bib_entries) == 3

    ids = [b.id for b in doc.bib_entries]
    # two "Smith" 2020 entries must disambiguate deterministically
    assert ids == ["smith2020", "smith2020a", "doe2018"]

    smith1 = doc.bib_entries[0]
    assert smith1.title == "A Great Paper on Plant Biology"
    assert smith1.author == [{"family": "Smith", "given": "Jane"}]
    assert smith1.issued == {"date-parts": [[2020]]}

    # CSL-JSON export drops None fields
    csl = smith1.to_csl_json()
    assert csl["id"] == "smith2020"
    assert "title" in csl


def test_parse_tei_is_stable_bibkey_scheme_idempotent():
    doc1 = parse_tei(_load_fixture_bytes())
    doc2 = parse_tei(_load_fixture_bytes())
    assert [b.id for b in doc1.bib_entries] == [b.id for b in doc2.bib_entries]


def test_parse_pdf_raises_typed_error_when_grobid_unreachable(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    with pytest.raises(GrobidError):
        parse_pdf(b"%PDF-1.4 fake", grobid_url="http://localhost:8070")


def test_parse_pdf_raises_typed_error_on_non_200(httpx_mock):
    httpx_mock.add_response(status_code=500, text="internal error")
    with pytest.raises(GrobidError):
        parse_pdf(b"%PDF-1.4 fake", grobid_url="http://localhost:8070")


def test_parse_pdf_posts_and_parses_tei_response(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:8070/api/processFulltextDocument",
        content=_load_fixture_bytes(),
        status_code=200,
    )
    doc = parse_pdf(b"%PDF-1.4 fake", grobid_url="http://localhost:8070", filename="paper.pdf")
    assert len(doc.sections) == 4
    assert len(doc.bib_entries) == 3
