"""OPTIONAL live end-to-end test: parse a real arXiv PDF against a real
GROBID server. Not part of the hermetic suite — requires Docker (GROBID
running at GROBID_URL) and network access to fetch the fixture PDF once.

Run with:
    curl -L https://arxiv.org/pdf/2301.00234 -o tests/fixtures/arxiv_sample.pdf
    docker compose up -d grobid
    DRAFTFORGE_LIVE=1 uv run pytest tests/test_ingest_live_grobid.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from draftforge.config import get_settings
from draftforge.ingest.grobid import parse_pdf

LIVE_PDF_PATH = Path(__file__).parent / "fixtures" / "arxiv_sample.pdf"


@pytest.mark.skipif(
    not os.getenv("DRAFTFORGE_LIVE"),
    reason="set DRAFTFORGE_LIVE=1 to run the live GROBID/arXiv end-to-end test",
)
def test_live_grobid_parses_real_arxiv_pdf():
    if not LIVE_PDF_PATH.exists():
        pytest.skip(
            f"no PDF fixture at {LIVE_PDF_PATH}. Download a real arXiv paper there, e.g.:\n"
            "  curl -L https://arxiv.org/pdf/2301.00234 -o tests/fixtures/arxiv_sample.pdf\n"
            "and ensure a real GROBID server is reachable at GROBID_URL "
            "(docker compose up -d grobid) before running this test."
        )
    settings = get_settings()
    doc = parse_pdf(
        LIVE_PDF_PATH.read_bytes(), grobid_url=settings.grobid_url, filename="arxiv_sample.pdf"
    )
    assert len(doc.sections) > 0
    assert any(section.text.strip() for section in doc.sections)
    # Real papers almost always have a non-empty reference list.
    assert len(doc.bib_entries) > 0
    assert all(entry.id for entry in doc.bib_entries)
