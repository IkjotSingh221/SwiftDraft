"""Qdrant hybrid (dense + sparse) vector store.

Collection strategy (see DECISIONS.md): **one Qdrant collection per
project** (`draftforge_project_{project_id}`), rather than one shared
collection with a `project_id` payload filter. A single-user local tool
never has more than a handful of projects, per-project deletion is a plain
`delete_collection`, and every query is naturally scoped without having to
remember a filter — simpler and harder to get wrong than a shared collection.

Hybrid search uses Qdrant's Query API: dense and sparse are each prefetched
as ranked candidate lists, then fused with Reciprocal Rank Fusion (RRF) via
`FusionQuery`. This runs identically against a real Qdrant server or
`QdrantClient(":memory:")` (Qdrant's local, in-process engine) — tests use
the latter so hybrid search is exercised against the real client library,
not a hand-rolled mock.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from draftforge.config import get_settings
from draftforge.ingest.chunker import Chunk
from draftforge.ingest.embedder import SparseVector, embed_dense, embed_sparse

COLLECTION_PREFIX = "draftforge_project_"
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
DENSE_DIM = 1024  # BAAI/bge-m3 dense output dimension


class SearchHit(BaseModel):
    chunk_id: str
    source_id: str
    section_path: list[str]
    page: int | None
    text: str
    bibkeys: list[str]
    score: float


def get_qdrant_client(url: str | None = None) -> QdrantClient:
    """Build a client against `QDRANT_URL` (or an explicit `url`, e.g. ':memory:')."""
    settings = get_settings()
    return QdrantClient(url=url or settings.qdrant_url)


def collection_name(project_id: str) -> str:
    return f"{COLLECTION_PREFIX}{project_id}"


def ensure_collection(client: QdrantClient, project_id: str, *, dense_dim: int = DENSE_DIM) -> str:
    """Create the project's collection if it doesn't exist yet. Idempotent."""
    name = collection_name(project_id)
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config={
                DENSE_VECTOR_NAME: qm.VectorParams(size=dense_dim, distance=qm.Distance.COSINE)
            },
            sparse_vectors_config={SPARSE_VECTOR_NAME: qm.SparseVectorParams()},
        )
    return name


def upsert_chunks(
    client: QdrantClient,
    project_id: str,
    chunks: list[Chunk],
    dense: list[list[float]],
    sparse: list[SparseVector],
) -> None:
    """Embed-and-store step: one dense + one sparse vector per chunk."""
    if not (len(chunks) == len(dense) == len(sparse)):
        raise ValueError("chunks, dense, and sparse must have the same length")
    if not chunks:
        return

    name = ensure_collection(client, project_id, dense_dim=len(dense[0]))
    points = [
        qm.PointStruct(
            id=_point_id(chunk.chunk_id),
            vector={
                DENSE_VECTOR_NAME: dvec,
                SPARSE_VECTOR_NAME: qm.SparseVector(indices=svec.indices, values=svec.values),
            },
            payload={
                "chunk_id": chunk.chunk_id,
                "source_id": chunk.source_id,
                "section_path": chunk.section_path,
                "page": chunk.page,
                "text": chunk.text,
                "bibkeys": chunk.bibkeys,
                "project_id": project_id,
            },
        )
        for chunk, dvec, svec in zip(chunks, dense, sparse)
    ]
    client.upsert(collection_name=name, points=points)


def _point_id(chunk_id: str) -> str:
    import uuid

    # Qdrant point IDs must be an int or a UUID; chunk_id is a human-readable
    # string ("source_id:0001"), so derive a stable UUID5 from it.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def hybrid_search(
    client: QdrantClient,
    project_id: str,
    query: str,
    *,
    top_k: int = 5,
    source_id: str | None = None,
    section_path: str | None = None,
    filters: dict[str, Any] | None = None,
    dense_embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    sparse_embed_fn: Callable[[list[str]], list[SparseVector]] | None = None,
) -> list[SearchHit]:
    """Embed `query` and run a fused dense+sparse (RRF) search, scoped to the
    project's collection.

    `source_id` / `section_path` are convenience filters (payload field
    equality/containment); `filters` accepts arbitrary extra
    payload-field: value equality filters (e.g. tags added by the planner).

    `dense_embed_fn`/`sparse_embed_fn` default to `embedder.embed_dense` /
    `embed_sparse`, but tests (and any caller wanting to reuse a
    already-computed query embedding) can inject alternatives.
    """
    dense_fn = dense_embed_fn or embed_dense
    sparse_fn = sparse_embed_fn or embed_sparse
    query_dense = dense_fn([query])[0]
    query_sparse = sparse_fn([query])[0]

    name = collection_name(project_id)
    query_filter = _build_filter(source_id=source_id, section_path=section_path, filters=filters)
    fetch_limit = max(top_k * 4, 20)

    result = client.query_points(
        collection_name=name,
        prefetch=[
            qm.Prefetch(
                query=query_dense,
                using=DENSE_VECTOR_NAME,
                filter=query_filter,
                limit=fetch_limit,
            ),
            qm.Prefetch(
                query=qm.SparseVector(indices=query_sparse.indices, values=query_sparse.values),
                using=SPARSE_VECTOR_NAME,
                filter=query_filter,
                limit=fetch_limit,
            ),
        ],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF),
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )

    hits: list[SearchHit] = []
    for point in result.points:
        payload = point.payload or {}
        hits.append(
            SearchHit(
                chunk_id=payload.get("chunk_id", str(point.id)),
                source_id=payload.get("source_id", ""),
                section_path=payload.get("section_path", []),
                page=payload.get("page"),
                text=payload.get("text", ""),
                bibkeys=payload.get("bibkeys", []),
                score=point.score,
            )
        )
    return hits


def _build_filter(
    *,
    source_id: str | None,
    section_path: str | None,
    filters: dict[str, Any] | None,
) -> qm.Filter | None:
    must: list[qm.FieldCondition] = []
    if source_id:
        must.append(qm.FieldCondition(key="source_id", match=qm.MatchValue(value=source_id)))
    if section_path:
        # `section_path` is stored as an array; MatchValue on an array field
        # checks containment (does any element equal this value).
        must.append(qm.FieldCondition(key="section_path", match=qm.MatchValue(value=section_path)))
    if filters:
        for key, value in filters.items():
            must.append(qm.FieldCondition(key=key, match=qm.MatchValue(value=value)))
    return qm.Filter(must=must) if must else None
