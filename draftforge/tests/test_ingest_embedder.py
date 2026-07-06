"""Embedder: lazy-loading contract + injected fake models (no real
sentence-transformers/fastembed models are loaded)."""

from __future__ import annotations

import pytest

from draftforge.ingest import embedder
from draftforge.ingest.embedder import MAX_BATCH_SIZE, SparseVector, embed_dense, embed_sparse


class _FakeDenseModel:
    def __init__(self):
        self.calls: list[dict] = []

    def encode(self, texts, **kwargs):
        self.calls.append({"texts": list(texts), "kwargs": kwargs})
        return [[float(len(t)), 0.0] for t in texts]


class _FakeSparseEmbedding:
    def __init__(self, indices, values):
        self.indices = indices
        self.values = values


class _FakeSparseModel:
    def embed(self, texts, **kwargs):
        return [_FakeSparseEmbedding([1, 2], [0.5, 0.5]) for _ in texts]


def test_injected_model_bypasses_lazy_singleton_loaders(monkeypatch: pytest.MonkeyPatch):
    """Passing `model=` must never touch `_get_dense_model`/`_get_sparse_model`
    (the functions that actually import sentence-transformers/fastembed and
    load real weights) — this is the lazy-loading contract that keeps
    hermetic tests fast and dependency-free.
    """

    def _boom():
        raise AssertionError("should not construct a real model when one is injected")

    monkeypatch.setattr(embedder, "_get_dense_model", _boom)
    monkeypatch.setattr(embedder, "_get_sparse_model", _boom)

    embed_dense(["hi"], model=_FakeDenseModel())
    embed_sparse(["hi"], model=_FakeSparseModel())


def test_embed_dense_with_injected_model():
    model = _FakeDenseModel()
    vectors = embed_dense(["hi", "hello"], model=model)
    assert vectors == [[2.0, 0.0], [5.0, 0.0]]
    assert model.calls[0]["kwargs"]["batch_size"] == MAX_BATCH_SIZE


def test_embed_dense_clamps_batch_size_to_vram_budget():
    model = _FakeDenseModel()
    embed_dense(["a"], model=model, batch_size=999)
    assert model.calls[0]["kwargs"]["batch_size"] == MAX_BATCH_SIZE


def test_embed_sparse_with_injected_model():
    model = _FakeSparseModel()
    vectors = embed_sparse(["a", "b"], model=model)
    assert vectors == [
        SparseVector(indices=[1, 2], values=[0.5, 0.5]),
        SparseVector(indices=[1, 2], values=[0.5, 0.5]),
    ]
