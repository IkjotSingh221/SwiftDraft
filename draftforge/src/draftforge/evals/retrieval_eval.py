"""Phase 7: retrieval eval — recall@5 / recall@10 / MRR ablation over
dense-only, sparse-only, and hybrid retrieval.

## Honesty rule (spec.md: "never fabricate benchmark numbers")

This sandbox has no GPU and no downloaded `BAAI/bge-m3`/BM25 model weights.
By default (`live=False`), this module embeds the fixture chunks/queries
with `synthetic_dense_embed`/`synthetic_sparse_embed` -- small, deterministic,
clearly-labeled fake embedders (a curated topic-keyword bag for "dense", a
literal word-hash bag-of-words for "sparse"). They exercise the REAL
`ingest.store` hybrid-search/RRF-fusion code path against Qdrant's real
`:memory:` engine, so the retrieval *plumbing* under test is genuine -- but
the resulting recall/MRR numbers say nothing about real BGE-M3/BM25 quality.
Every result this module returns carries `synthetic=True` in that mode, and
`report.py` prints that flag prominently rather than silently presenting
synthetic numbers as if they were real ones.

Passing `live=True` (wired to `DRAFTFORGE_LIVE=1` in `report.py`) swaps in
the real `ingest.embedder.embed_dense`/`embed_sparse` -- i.e. real BGE-M3 +
BM25 -- which requires a GPU (or at least the model weights) to actually run;
untested in this sandbox.

## Labeled fixture

`tests/fixtures/evals/retrieval_fixture.json`: 8 hand-authored chunks across
4 topics, 4 queries each with 2 known-relevant chunk ids (paraphrased so a
literal-keyword sparse search and a topic-level dense search are not
trivially identical). See DECISIONS.md for why the fixture lives under
`tests/fixtures/` even though this is production code -- the harness runs
from a repo checkout, not a pip-installed wheel, so referencing it directly
is simpler than duplicating the data into `src/`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from draftforge.config import PROJECT_ROOT
from draftforge.ingest.chunker import Chunk
from draftforge.ingest.embedder import SparseVector, embed_dense, embed_sparse
from draftforge.ingest.store import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    collection_name,
    hybrid_search,
    upsert_chunks,
)

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "evals" / "retrieval_fixture.json"

EVAL_PROJECT_ID = "eval-retrieval-fixture"
CONFIGS = ("dense_only", "sparse_only", "hybrid")

DenseEmbedFn = Callable[[list[str]], list[list[float]]]
SparseEmbedFn = Callable[[list[str]], list[SparseVector]]


# ---------------------------------------------------------------------------
# Fixture models + loading
# ---------------------------------------------------------------------------


class LabeledQuery(BaseModel):
    query: str
    relevant_chunk_ids: list[str]


class RetrievalFixture(BaseModel):
    chunks: list[Chunk]
    queries: list[LabeledQuery]


def load_fixture(path: str | Path = FIXTURE_PATH) -> RetrievalFixture:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    chunks = [
        Chunk(
            chunk_id=c["chunk_id"],
            source_id=c["source_id"],
            section_path=c["section_path"],
            page=c.get("page"),
            text=c["text"],
            bibkeys=c.get("bibkeys", []),
            token_count=len(c["text"].split()),
        )
        for c in data["chunks"]
    ]
    queries = [LabeledQuery(query=q["query"], relevant_chunk_ids=q["relevant_chunk_ids"]) for q in data["queries"]]
    return RetrievalFixture(chunks=chunks, queries=queries)


# ---------------------------------------------------------------------------
# Synthetic (deterministic, clearly-fake) embedders -- see module docstring
# ---------------------------------------------------------------------------

# A small curated topic -> keyword-bag table. This is NOT a semantic model;
# it is a deterministic stand-in that groups the fixture's 4 topics together
# so "dense" search behaves like a coarse topic matcher, distinct from the
# literal-overlap "sparse" search below. Extending the fixture with a new
# topic means adding a bucket here too.
_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "energy_biology": [
        "photosynthesis", "energy", "chlorophyll", "sunlight", "light", "plant",
        "plants", "cell", "cells", "sugar", "sugars", "glucose", "chemical",
        "vegetation", "harvest", "food",
    ],
    "finance": [
        "market", "stock", "trading", "investors", "investor", "earnings",
        "exchange", "volume", "interest", "rate", "bank", "financial", "reaction",
    ],
    "sports": [
        "basketball", "league", "team", "teams", "championship", "playoffs",
        "season", "players", "court", "compete", "title", "professional",
    ],
    "astronomy": [
        "planet", "orbit", "orbiting", "orbital", "telescope", "star",
        "astronomer", "astronomers", "gravitational", "observed",
    ],
}
_TOPIC_ORDER = tuple(_TOPIC_KEYWORDS)
_WORD_RE = re.compile(r"[a-z0-9]+")


def synthetic_dense_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic "dense" fake: one dimension per curated topic, value =
    count of that topic's keywords present in the text, L2-normalized."""
    vectors: list[list[float]] = []
    for text in texts:
        words = set(_WORD_RE.findall(text.lower()))
        raw = [float(sum(1 for kw in _TOPIC_KEYWORDS[topic] if kw in words)) for topic in _TOPIC_ORDER]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        vectors.append([v / norm for v in raw])
    return vectors


def synthetic_sparse_embed(texts: list[str]) -> list[SparseVector]:
    """Deterministic "sparse" fake: a literal bag-of-words, each word hashed
    into a fixed-size index space with raw term counts as values (a crude
    stand-in for BM25's literal-term matching, not real BM25 scoring)."""
    vectors: list[SparseVector] = []
    for text in texts:
        counts: dict[int, float] = {}
        for word in _WORD_RE.findall(text.lower()):
            idx = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16) % 4096
            counts[idx] = counts.get(idx, 0.0) + 1.0
        indices = sorted(counts)
        vectors.append(SparseVector(indices=indices, values=[counts[i] for i in indices]))
    return vectors


# ---------------------------------------------------------------------------
# Metrics (pure functions -- hand-verifiable)
# ---------------------------------------------------------------------------


def recall_at_k(returned_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Fraction of `relevant_ids` present in the first `k` of `returned_ids`.
    0.0 if `relevant_ids` is empty (nothing to recall)."""
    if not relevant_ids:
        return 0.0
    top_k = set(returned_ids[:k])
    return len(top_k & relevant_ids) / len(relevant_ids)


def reciprocal_rank(returned_ids: list[str], relevant_ids: set[str]) -> float:
    """1 / (rank of the first relevant id, 1-indexed), 0.0 if none found."""
    for i, cid in enumerate(returned_ids, start=1):
        if cid in relevant_ids:
            return 1.0 / i
    return 0.0


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class AblationRow(BaseModel):
    config: str  # "dense_only" | "sparse_only" | "hybrid"
    num_queries: int
    recall_at_5: float
    recall_at_10: float
    mrr: float


class RetrievalEvalResult(BaseModel):
    rows: list[AblationRow] = Field(default_factory=list)
    synthetic: bool = True
    fixture_num_chunks: int = 0
    fixture_num_queries: int = 0
    note: str = ""


# ---------------------------------------------------------------------------
# Single-vector (dense-only / sparse-only) search -- hybrid_search always
# fuses both, so an ablation needs its own single-vector query path. This
# mirrors ingest.store.hybrid_search's own hit-building code (kept in sync
# manually; ingest/store.py is off-limits to edit in this phase).
# ---------------------------------------------------------------------------


def _single_vector_search(
    client: QdrantClient,
    project_id: str,
    query_vector: Any,
    *,
    vector_name: str,
    top_k: int,
) -> list[dict[str, Any]]:
    result = client.query_points(
        collection_name=collection_name(project_id),
        query=query_vector,
        using=vector_name,
        limit=top_k,
        with_payload=True,
    )
    hits: list[dict[str, Any]] = []
    for point in result.points:
        payload = point.payload or {}
        hits.append({"chunk_id": payload.get("chunk_id", str(point.id)), "score": point.score})
    return hits


def _search_ids(
    client: QdrantClient,
    project_id: str,
    config: str,
    query: str,
    *,
    dense_fn: DenseEmbedFn,
    sparse_fn: SparseEmbedFn,
    top_k: int,
) -> list[str]:
    if config == "dense_only":
        qvec = dense_fn([query])[0]
        hits = _single_vector_search(client, project_id, qvec, vector_name=DENSE_VECTOR_NAME, top_k=top_k)
        return [h["chunk_id"] for h in hits]
    if config == "sparse_only":
        svec = sparse_fn([query])[0]
        qvec = qm.SparseVector(indices=svec.indices, values=svec.values)
        hits = _single_vector_search(client, project_id, qvec, vector_name=SPARSE_VECTOR_NAME, top_k=top_k)
        return [h["chunk_id"] for h in hits]
    if config == "hybrid":
        hits = hybrid_search(
            client, project_id, query, top_k=top_k, dense_embed_fn=dense_fn, sparse_embed_fn=sparse_fn
        )
        return [h.chunk_id for h in hits]
    raise ValueError(f"unknown retrieval config: {config!r}")


# ---------------------------------------------------------------------------
# Ablation driver
# ---------------------------------------------------------------------------


def run_ablation(
    fixture: RetrievalFixture,
    dense_fn: DenseEmbedFn,
    sparse_fn: SparseEmbedFn,
    *,
    top_k: int = 10,
    client_factory: Callable[[], QdrantClient] = lambda: QdrantClient(":memory:"),
    project_id: str = EVAL_PROJECT_ID,
) -> list[AblationRow]:
    """Upsert `fixture.chunks` into a fresh collection and compute
    recall@5/@10 + MRR for each of dense-only/sparse-only/hybrid.

    Injectable `client_factory`/embedders so tests can run this against a
    tiny hand-verifiable fixture with hand-crafted vectors (same pattern as
    `tests/test_ingest_store.py`), no ML models involved.
    """
    client = client_factory()
    dense_vectors = dense_fn([c.text for c in fixture.chunks])
    sparse_vectors = sparse_fn([c.text for c in fixture.chunks])
    upsert_chunks(client, project_id, fixture.chunks, dense_vectors, sparse_vectors)

    rows: list[AblationRow] = []
    for config in CONFIGS:
        recalls_5: list[float] = []
        recalls_10: list[float] = []
        mrrs: list[float] = []
        for labeled in fixture.queries:
            relevant = set(labeled.relevant_chunk_ids)
            returned = _search_ids(
                client, project_id, config, labeled.query, dense_fn=dense_fn, sparse_fn=sparse_fn, top_k=top_k
            )
            recalls_5.append(recall_at_k(returned, relevant, 5))
            recalls_10.append(recall_at_k(returned, relevant, 10))
            mrrs.append(reciprocal_rank(returned, relevant))
        n = len(fixture.queries) or 1
        rows.append(
            AblationRow(
                config=config,
                num_queries=len(fixture.queries),
                recall_at_5=sum(recalls_5) / n,
                recall_at_10=sum(recalls_10) / n,
                mrr=sum(mrrs) / n,
            )
        )
    return rows


def run_retrieval_eval(*, live: bool = False, fixture_path: str | Path = FIXTURE_PATH) -> RetrievalEvalResult:
    """The harness entrypoint. `live=False` (default, safe everywhere): uses
    the deterministic synthetic embedders. `live=True`: uses the real
    `embed_dense`/`embed_sparse` (BGE-M3 + BM25) -- requires a GPU/model
    weights and will raise if they are unavailable; callers (`report.py`)
    are expected to catch that and report it honestly rather than crash."""
    fixture = load_fixture(fixture_path)
    dense_fn: DenseEmbedFn = embed_dense if live else synthetic_dense_embed
    sparse_fn: SparseEmbedFn = embed_sparse if live else synthetic_sparse_embed
    rows = run_ablation(fixture, dense_fn, sparse_fn)
    return RetrievalEvalResult(
        rows=rows,
        synthetic=not live,
        fixture_num_chunks=len(fixture.chunks),
        fixture_num_queries=len(fixture.queries),
        note=(
            "SYNTHETIC FIXTURE: deterministic keyword-bag embeddings, not real "
            "BGE-M3/BM25 -- exercises the hybrid_search/RRF fusion code path "
            "only, not real retrieval quality. Run with DRAFTFORGE_LIVE=1 on a "
            "GPU machine for real numbers."
            if not live
            else "LIVE: real embed_dense/embed_sparse (BGE-M3 + BM25) against the same labeled fixture."
        ),
    )
