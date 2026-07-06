"""GROBID client: PDF -> TEI XML -> structured sections + CSL-JSON bibliography.

POSTs to GROBID's `/api/processFulltextDocument` and parses the returned TEI
XML with lxml. GROBID being unreachable (or erroring) raises `GrobidError`, a
typed exception the caller (the ingestion pipeline in `routes_projects.py`)
catches to fall back to Docling.
"""

from __future__ import annotations

import re

import httpx
from lxml import etree

from draftforge.ingest.models import BibEntry, ParsedDocument, ParsedSection

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


class GrobidError(Exception):
    """GROBID is unreachable, timed out, or returned a non-200 response."""


def parse_pdf(
    pdf_bytes: bytes,
    *,
    grobid_url: str,
    filename: str = "document.pdf",
    timeout: float = 120.0,
) -> ParsedDocument:
    """Send `pdf_bytes` to a GROBID server and parse the resulting TEI XML."""
    tei_xml = _call_grobid(pdf_bytes, grobid_url=grobid_url, filename=filename, timeout=timeout)
    return parse_tei(tei_xml)


def _call_grobid(pdf_bytes: bytes, *, grobid_url: str, filename: str, timeout: float) -> bytes:
    url = f"{grobid_url.rstrip('/')}/api/processFulltextDocument"
    try:
        resp = httpx.post(
            url,
            files={"input": (filename, pdf_bytes, "application/pdf")},
            data={"consolidateCitations": "0", "includeRawCitations": "1"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise GrobidError(f"GROBID unreachable at {url}: {exc}") from exc
    if resp.status_code != 200:
        raise GrobidError(
            f"GROBID returned {resp.status_code} for {url}: {resp.text[:200]!r}"
        )
    return resp.content


def parse_tei(tei_xml: bytes | str) -> ParsedDocument:
    """Parse TEI XML (as produced by GROBID) into a `ParsedDocument`."""
    if isinstance(tei_xml, str):
        tei_xml = tei_xml.encode("utf-8")
    root = etree.fromstring(tei_xml)
    sections = _extract_sections(root)
    bib_entries = _extract_bibliography(root)
    return ParsedDocument(sections=sections, bib_entries=bib_entries)


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------


def _extract_sections(root: etree._Element) -> list[ParsedSection]:
    body = root.find(".//tei:text/tei:body", TEI_NS)
    if body is None:
        return []
    sections: list[ParsedSection] = []
    _walk_divs(body, path=[], sections=sections, page=None)
    return sections


def _walk_divs(
    elem: etree._Element,
    path: list[str],
    sections: list[ParsedSection],
    page: int | None,
) -> None:
    for div in elem.findall("tei:div", TEI_NS):
        head = div.find("tei:head", TEI_NS)
        heading = "".join(head.itertext()).strip() if head is not None else ""
        new_path = [*path, heading] if heading else path

        paragraphs: list[str] = []
        local_page = page
        for child in div:
            tag = etree.QName(child).localname
            if tag == "pb":
                n = child.get("n")
                if n:
                    try:
                        local_page = int(n)
                    except ValueError:
                        pass
            elif tag == "p":
                text = "".join(child.itertext()).strip()
                if text:
                    paragraphs.append(text)

        if heading or paragraphs:
            sections.append(
                ParsedSection(
                    section_path=list(new_path),
                    heading=heading,
                    text="\n\n".join(paragraphs),
                    page=local_page,
                )
            )

        _walk_divs(div, new_path, sections, local_page)


# ---------------------------------------------------------------------------
# Bibliography extraction -> CSL-JSON
# ---------------------------------------------------------------------------


def _extract_bibliography(root: etree._Element) -> list[BibEntry]:
    entries: list[BibEntry] = []
    used_keys: dict[str, int] = {}
    for bibl in root.findall(".//tei:listBibl/tei:biblStruct", TEI_NS):
        entry = _biblstruct_to_csl(bibl, used_keys)
        if entry is not None:
            entries.append(entry)
    return entries


def _biblstruct_to_csl(bibl: etree._Element, used_keys: dict[str, int]) -> BibEntry | None:
    authors: list[dict] = []
    for pers in bibl.findall(".//tei:author/tei:persName", TEI_NS):
        surname = (pers.findtext("tei:surname", default="", namespaces=TEI_NS) or "").strip()
        forename = (pers.findtext("tei:forename", default="", namespaces=TEI_NS) or "").strip()
        if surname:
            authors.append({"family": surname, "given": forename})

    title_el = bibl.find(".//tei:analytic/tei:title", TEI_NS)
    if title_el is None:
        title_el = bibl.find(".//tei:monogr/tei:title", TEI_NS)
    title = "".join(title_el.itertext()).strip() if title_el is not None else None

    year = None
    date_el = bibl.find(".//tei:imprint/tei:date", TEI_NS)
    if date_el is not None:
        when = date_el.get("when") or (date_el.text or "")
        match = re.match(r"(\d{4})", when.strip())
        if match:
            year = int(match.group(1))

    if not authors and not title:
        return None

    bibkey = _make_bibkey(authors, year, used_keys)
    return BibEntry(
        id=bibkey,
        type="article-journal",
        title=title,
        author=authors,
        issued={"date-parts": [[year]]} if year else None,
    )


def _make_bibkey(authors: list[dict], year: int | None, used_keys: dict[str, int]) -> str:
    """first-author-surname + year, disambiguated with a/b/c on collision.

    e.g. two 2020 papers by "Smith" become "smith2020" and "smith2020a".
    See DECISIONS.md for the rationale (simple, stable, human-readable keys).
    """
    surname = _slug(authors[0]["family"]) if authors else "anon"
    year_part = str(year) if year else "nd"
    base = f"{surname}{year_part}"

    count = used_keys.get(base, 0)
    used_keys[base] = count + 1
    if count == 0:
        return base
    suffix = chr(ord("a") + count - 1)
    return f"{base}{suffix}"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())
