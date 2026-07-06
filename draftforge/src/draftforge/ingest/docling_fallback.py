"""Docling fallback parser for docx / non-paper PDFs.

Used when the source isn't a scientific paper (so GROBID's citation-aware
parsing doesn't apply) or when GROBID is unreachable/errors. Produces the
same `ParsedDocument` shape as `grobid.py`, with an empty `bib_entries` list
(Docling doesn't extract a bibliography).

`docling` pulls in torch/transformers, so it is imported lazily inside
`parse_document` — importing this module (or running the hermetic test
suite, which never calls `parse_document`) never loads it.
"""

from __future__ import annotations

import re
from pathlib import Path

from draftforge.ingest.models import ParsedDocument, ParsedSection

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def parse_document(file_path: str | Path) -> ParsedDocument:
    """Parse a docx/PDF file via Docling into structured sections."""
    from docling.document_converter import DocumentConverter  # lazy import

    converter = DocumentConverter()
    result = converter.convert(str(file_path))
    markdown = result.document.export_to_markdown()
    return ParsedDocument(sections=_markdown_to_sections(markdown), bib_entries=[])


def _markdown_to_sections(markdown: str) -> list[ParsedSection]:
    """Split Docling's markdown export into sections on ATX headings.

    Pure function (no Docling import) so it's unit-testable without the
    heavy dependency installed/loaded. Tracks a heading-depth stack to build
    `section_path`, mirroring `grobid.py`'s hierarchy shape.
    """
    sections: list[ParsedSection] = []
    stack: list[str] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if heading or text:
            sections.append(
                ParsedSection(section_path=list(stack), heading=heading, text=text, page=None)
            )
        buffer.clear()

    for line in markdown.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            flush()
            depth = len(match.group(1))
            heading = match.group(2).strip()
            stack = [*stack[: depth - 1], heading]
        else:
            buffer.append(line)
    flush()

    return sections
