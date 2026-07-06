"""Ingestion: parse (GROBID/Docling) -> chunk -> embed -> Qdrant.

See DECISIONS.md for the Phase 1 design choices (token counting heuristic,
bibkey scheme, collection-per-project strategy).
"""
