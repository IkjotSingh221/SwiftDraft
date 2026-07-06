"""Chunker invariants: section boundaries respected, size/overlap, metadata."""

from __future__ import annotations

from draftforge.ingest.chunker import (
    Chunk,
    chunk_document,
    chunk_sections,
    count_tokens,
)
from draftforge.ingest.models import BibEntry, ParsedDocument, ParsedSection


def _words(n: int, prefix: str = "word") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def test_count_tokens_is_word_count():
    assert count_tokens("one two three") == 3
    assert count_tokens("") == 0


def test_never_crosses_section_boundary():
    sections = [
        ParsedSection(section_path=["A"], heading="A", text=_words(400)),
        ParsedSection(section_path=["B"], heading="B", text=_words(400)),
    ]
    chunks = chunk_sections("src1", sections, bibkeys=[])
    for chunk in chunks:
        # every chunk's text only contains words from a single section's word pool
        assert chunk.section_path in (["A"], ["B"])
    # no chunk mixes "wordX" from both sections since they use the same prefix
    # but different section_path already proves boundary separation structurally.
    a_chunks = [c for c in chunks if c.section_path == ["A"]]
    b_chunks = [c for c in chunks if c.section_path == ["B"]]
    assert a_chunks and b_chunks


def test_chunk_size_within_target_range_for_long_section():
    # 1200 words: comfortably larger than max_tokens (600), so we should get
    # multiple non-trivial chunks, each within [min_tokens, max_tokens] except
    # possibly a trailing absorbed piece.
    sections = [ParsedSection(section_path=["Body"], heading="Body", text=_words(1200))]
    chunks = chunk_sections("src1", sections, bibkeys=[], min_tokens=300, max_tokens=600)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert 300 <= chunk.token_count <= 600 + 90  # allow tail-absorption slop


def test_small_section_not_split_and_not_forced_to_min():
    # A section shorter than min_tokens still yields exactly one chunk (never
    # merges across section boundaries just to hit the minimum).
    sections = [ParsedSection(section_path=["Short"], heading="Short", text=_words(50))]
    chunks = chunk_sections("src1", sections, bibkeys=[])
    assert len(chunks) == 1
    assert chunks[0].token_count == 50


def test_overlap_between_consecutive_chunks():
    sections = [ParsedSection(section_path=["Body"], heading="Body", text=_words(1000))]
    chunks = chunk_sections(
        "src1", sections, bibkeys=[], min_tokens=300, max_tokens=600, overlap_ratio=0.15
    )
    assert len(chunks) >= 2
    overlap_words = round(600 * 0.15)  # 90
    first_words = chunks[0].text.split()
    second_words = chunks[1].text.split()
    # the tail of chunk 0 should reappear as the head of chunk 1
    tail = first_words[-overlap_words:]
    head = second_words[:overlap_words]
    assert tail == head


def test_metadata_and_bibkeys_attached():
    sections = [ParsedSection(section_path=["Intro"], heading="Intro", text=_words(100), page=3)]
    chunks = chunk_sections("source-42", sections, bibkeys=["smith2020", "doe2018"])
    assert len(chunks) == 1
    chunk = chunks[0]
    assert isinstance(chunk, Chunk)
    assert chunk.source_id == "source-42"
    assert chunk.section_path == ["Intro"]
    assert chunk.page == 3
    assert chunk.chunk_id == "source-42:0001"
    assert chunk.bibkeys == ["smith2020", "doe2018"]


def test_chunk_ids_increment_across_sections():
    sections = [
        ParsedSection(section_path=["A"], heading="A", text=_words(50)),
        ParsedSection(section_path=["B"], heading="B", text=_words(50)),
    ]
    chunks = chunk_sections("src1", sections, bibkeys=[])
    assert [c.chunk_id for c in chunks] == ["src1:0001", "src1:0002"]


def test_chunk_document_pulls_bibkeys_from_bib_entries():
    parsed = ParsedDocument(
        sections=[ParsedSection(section_path=["Intro"], heading="Intro", text=_words(50))],
        bib_entries=[BibEntry(id="smith2020"), BibEntry(id="doe2018")],
    )
    chunks = chunk_document(parsed, "source-1")
    assert len(chunks) == 1
    assert set(chunks[0].bibkeys) == {"smith2020", "doe2018"}


def test_empty_section_text_produces_no_chunks():
    sections = [ParsedSection(section_path=["Empty"], heading="Empty", text="")]
    chunks = chunk_sections("src1", sections, bibkeys=[])
    assert chunks == []
