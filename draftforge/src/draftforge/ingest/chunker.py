"""Structure-aware chunker: `ParsedSection`s -> 300-600 "token" chunks.

Pure functions, no I/O — fully unit-testable without any models loaded.

Token counting (see DECISIONS.md): we approximate token count as whitespace
word count rather than adding `tiktoken` as a dependency. This is a
documented ~20-30% undercount vs. a real BPE tokenizer for English text, but
it's only used for chunk-sizing decisions, so the approximation is fine and
keeps this module dependency-light.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from draftforge.ingest.models import ParsedDocument, ParsedSection

DEFAULT_MIN_TOKENS = 300
DEFAULT_MAX_TOKENS = 600
DEFAULT_OVERLAP_RATIO = 0.15


class Chunk(BaseModel):
    """A single retrievable unit, carrying everything the retriever/verifier
    need: which source/section/page it came from, and the full set of
    bibkeys valid for citations drawn from this chunk (per spec: the drafter
    is only ever given valid keys for its retrieved chunks).
    """

    chunk_id: str
    source_id: str
    section_path: list[str]
    page: int | None
    text: str
    bibkeys: list[str]
    token_count: int


def count_tokens(text: str) -> int:
    """Heuristic token counter: whitespace word count (see module docstring)."""
    return len(text.split())


def chunk_document(
    parsed: ParsedDocument,
    source_id: str,
    *,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    token_counter: Callable[[str], int] = count_tokens,
) -> list[Chunk]:
    """Convenience wrapper: chunk every section of a `ParsedDocument`, tagging
    each chunk with the full bibkey set from the document's bibliography.
    """
    bibkeys = [entry.id for entry in parsed.bib_entries]
    return chunk_sections(
        source_id,
        parsed.sections,
        bibkeys,
        min_tokens=min_tokens,
        max_tokens=max_tokens,
        overlap_ratio=overlap_ratio,
        token_counter=token_counter,
    )


def chunk_sections(
    source_id: str,
    sections: list[ParsedSection],
    bibkeys: list[str],
    *,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    token_counter: Callable[[str], int] = count_tokens,
) -> list[Chunk]:
    """Split sections into chunks, never crossing a section boundary.

    Within a section, words are windowed into `max_tokens`-sized pieces with
    `overlap_ratio` overlap between consecutive pieces. If the tail remaining
    after a window is smaller than `min_tokens`, it's absorbed into the
    current chunk instead of becoming its own tiny fragment (a documented
    heuristic — see DECISIONS.md) — so a chunk may occasionally run a bit
    over `max_tokens`, but boundary-respecting takes priority.
    """
    overlap_words = round(max_tokens * overlap_ratio)
    chunks: list[Chunk] = []
    counter = 0

    for section in sections:
        words = section.text.split()
        if not words:
            continue
        n = len(words)
        start = 0
        while start < n:
            end = min(start + max_tokens, n)
            remaining_after = n - end
            if 0 < remaining_after < min_tokens:
                end = n  # absorb small tail rather than emit a tiny chunk

            piece_words = words[start:end]
            text = " ".join(piece_words)
            counter += 1
            chunks.append(
                Chunk(
                    chunk_id=f"{source_id}:{counter:04d}",
                    source_id=source_id,
                    section_path=list(section.section_path),
                    page=section.page,
                    text=text,
                    bibkeys=list(bibkeys),
                    token_count=token_counter(text),
                )
            )

            if end >= n:
                break
            next_start = end - overlap_words if overlap_words > 0 else end
            # guard against a zero/negative-progress loop for pathological inputs
            start = next_start if next_start > start else end

    return chunks
