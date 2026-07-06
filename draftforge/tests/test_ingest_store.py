"""Hybrid (dense+sparse RRF) search against Qdrant's real in-process
`:memory:` engine — no Docker, no network, but the actual qdrant-client
query/fusion code path (not a hand-rolled fake).

Embeddings are hand-crafted fakes injected via `dense_embed_fn`/
`sparse_embed_fn` so the test is deterministic and needs no ML models.
"""

from __future__ import annotations

from qdrant_client import QdrantClient

from draftforge.ingest.chunker import Chunk
from draftforge.ingest.embedder import SparseVector
from draftforge.ingest.store import ensure_collection, hybrid_search, upsert_chunks


def _chunk(chunk_id: str, source_id: str, text: str, section_path: list[str]) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        source_id=source_id,
        section_path=section_path,
        page=1,
        text=text,
        bibkeys=["smith2020"],
        token_count=len(text.split()),
    )


def _client() -> QdrantClient:
    return QdrantClient(":memory:")


def test_known_relevant_chunk_lands_in_top5():
    client = _client()
    project_id = "proj1"

    chunks = [
        _chunk("c1", "s1", "Photosynthesis converts light into chemical energy in plants.", ["Intro"]),
        _chunk("c2", "s1", "The stock market closed higher today amid trading volume.", ["Finance"]),
        _chunk("c3", "s1", "A recipe for chocolate cake requires flour, sugar, and eggs.", ["Recipes"]),
        _chunk("c4", "s1", "Basketball teams compete in a league every season.", ["Sports"]),
        _chunk("c5", "s1", "Weather forecasts predict rain over the weekend.", ["Weather"]),
        _chunk("c6", "s1", "Chemical reactions in cells release energy for growth.", ["Biology"]),
    ]

    # Deterministic fake dense vectors: chunks about the query topic
    # ("photosynthesis energy") point in a similar direction; others don't.
    relevant_ids = {"c1", "c6"}
    dense = [
        [1.0, 0.9, 0.0] if c.chunk_id in relevant_ids else [0.0, 0.1, 1.0] for c in chunks
    ]
    # Fake sparse vectors: relevant chunks share token id 1 ("energy"); others don't.
    sparse = [
        SparseVector(indices=[1, 2], values=[1.0, 1.0])
        if c.chunk_id in relevant_ids
        else SparseVector(indices=[3, 4], values=[1.0, 1.0])
        for c in chunks
    ]

    upsert_chunks(client, project_id, chunks, dense, sparse)

    def fake_dense_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.9, 0.0] for _ in texts]

    def fake_sparse_embed(texts: list[str]) -> list[SparseVector]:
        return [SparseVector(indices=[1, 2], values=[1.0, 1.0]) for _ in texts]

    hits = hybrid_search(
        client,
        project_id,
        "how do plants produce energy",
        top_k=5,
        dense_embed_fn=fake_dense_embed,
        sparse_embed_fn=fake_sparse_embed,
    )

    top_ids = {hit.chunk_id for hit in hits}
    assert "c1" in top_ids
    assert len(hits) <= 5
    # metadata round-trips through the payload
    top_hit = next(h for h in hits if h.chunk_id == "c1")
    assert top_hit.source_id == "s1"
    assert top_hit.section_path == ["Intro"]
    assert top_hit.bibkeys == ["smith2020"]


def test_source_id_filter_scopes_results():
    client = _client()
    project_id = "proj-filter"
    chunks = [
        _chunk("c1", "sourceA", "shared topic text about energy", ["Intro"]),
        _chunk("c2", "sourceB", "shared topic text about energy", ["Intro"]),
    ]
    dense = [[1.0, 0.0], [1.0, 0.0]]
    sparse = [SparseVector(indices=[1], values=[1.0]), SparseVector(indices=[1], values=[1.0])]
    upsert_chunks(client, project_id, chunks, dense, sparse)

    def fake_dense_embed(texts):
        return [[1.0, 0.0] for _ in texts]

    def fake_sparse_embed(texts):
        return [SparseVector(indices=[1], values=[1.0]) for _ in texts]

    hits = hybrid_search(
        client,
        project_id,
        "energy",
        top_k=5,
        source_id="sourceA",
        dense_embed_fn=fake_dense_embed,
        sparse_embed_fn=fake_sparse_embed,
    )
    assert len(hits) == 1
    assert hits[0].source_id == "sourceA"


def test_ensure_collection_is_idempotent():
    client = _client()
    name1 = ensure_collection(client, "proj-idempotent", dense_dim=4)
    name2 = ensure_collection(client, "proj-idempotent", dense_dim=4)
    assert name1 == name2 == "draftforge_project_proj-idempotent"


def test_upsert_rejects_mismatched_lengths():
    client = _client()
    chunks = [_chunk("c1", "s1", "text", ["Intro"])]
    import pytest

    with pytest.raises(ValueError):
        upsert_chunks(client, "proj1", chunks, dense=[], sparse=[SparseVector(indices=[], values=[])])
