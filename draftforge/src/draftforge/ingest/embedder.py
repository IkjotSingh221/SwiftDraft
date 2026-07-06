"""Embeddings: BGE-M3 dense (sentence-transformers) + BM25 sparse (fastembed).

Both models are lazy-loaded module-level singletons — constructed on first
use, not at import time — so importing this module (or running the
hermetic test suite, which always injects a fake `model`) never pulls in
torch/onnxruntime or downloads model weights.

fp16 + batch_size <= 32 (see spec: RTX 4060, 8GB VRAM) are enforced here so
callers don't have to remember the VRAM budget.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel

MAX_BATCH_SIZE = 32
DENSE_MODEL_NAME = "BAAI/bge-m3"
SPARSE_MODEL_NAME = "Qdrant/bm25"


class SparseVector(BaseModel):
    indices: list[int]
    values: list[float]


class _DenseModel(Protocol):
    def encode(self, texts: Sequence[str], **kwargs: Any) -> Any: ...


class _SparseModel(Protocol):
    def embed(self, texts: Sequence[str], **kwargs: Any) -> Any: ...


_dense_model: _DenseModel | None = None
_sparse_model: _SparseModel | None = None


def _get_dense_model() -> _DenseModel:
    global _dense_model
    if _dense_model is None:
        from sentence_transformers import SentenceTransformer

        _dense_model = SentenceTransformer(
            DENSE_MODEL_NAME, model_kwargs={"torch_dtype": "float16"}
        )
    return _dense_model


def _get_sparse_model() -> _SparseModel:
    global _sparse_model
    if _sparse_model is None:
        from fastembed import SparseTextEmbedding

        _sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL_NAME)
    return _sparse_model


def reset_models() -> None:
    """Drop cached model singletons (mainly useful for tests/reload)."""
    global _dense_model, _sparse_model
    _dense_model = None
    _sparse_model = None


def embed_dense(
    texts: list[str],
    *,
    model: _DenseModel | None = None,
    batch_size: int = MAX_BATCH_SIZE,
) -> list[list[float]]:
    """Dense embeddings via BGE-M3. Pass `model` to inject a fake in tests."""
    m = model or _get_dense_model()
    batch_size = min(batch_size, MAX_BATCH_SIZE)
    vectors = m.encode(list(texts), batch_size=batch_size, show_progress_bar=False)
    return [[float(x) for x in vec] for vec in vectors]


def embed_sparse(
    texts: list[str],
    *,
    model: _SparseModel | None = None,
) -> list[SparseVector]:
    """Sparse BM25 embeddings via fastembed. Pass `model` to inject a fake in tests."""
    m = model or _get_sparse_model()
    results = list(m.embed(list(texts)))
    return [
        SparseVector(indices=[int(i) for i in r.indices], values=[float(v) for v in r.values])
        for r in results
    ]
