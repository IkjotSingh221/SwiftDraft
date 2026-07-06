"""Shared pydantic types produced by the parsing layer (`grobid.py` /
`docling_fallback.py`) and consumed by the chunker.

Both parsers (GROBID for scientific PDFs, Docling for everything else)
normalize into the same `ParsedDocument` shape so `chunker.py` never has to
know which parser produced its input.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ParsedSection(BaseModel):
    """One heading + its body text, at a single level of the document's
    heading hierarchy.

    `section_path` is the full heading breadcrumb, e.g.
    `["2 Methods", "2.1 Data collection"]` — the leaf heading is
    `section_path[-1]` and also duplicated in `heading` for convenience.
    """

    section_path: list[str] = []
    heading: str = ""
    text: str = ""
    page: int | None = None


class BibEntry(BaseModel):
    """A single bibliography entry in (a permissive superset of) CSL-JSON.

    `id` is the stable bibkey (see `grobid.py`'s bibkey scheme, documented in
    DECISIONS.md). Extra CSL-JSON fields (container-title, volume, DOI, ...)
    are preserved via `extra="allow"` so we don't need to enumerate the full
    CSL-JSON schema here.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    type: str = "article-journal"
    title: str | None = None
    author: list[dict] = []
    issued: dict | None = None

    def to_csl_json(self) -> dict:
        return self.model_dump(exclude_none=True)


class ParsedDocument(BaseModel):
    """Normalized output of either parser: structured sections + bibliography."""

    sections: list[ParsedSection] = []
    bib_entries: list[BibEntry] = []
