"""Phase 7 retrieval eval (hermetic): metric math on hand-verifiable toy
cases, plus the full ablation (dense-only/sparse-only/hybrid) against the
shipped labeled fixture with deterministic SYNTHETIC embeddings, run
against Qdrant's real `:memory:` engine (same pattern as
`tests/test_ingest_store.py`). No GPU/network anywhere -- see
`evals/retrieval_eval.py`'s module docstring for the synthetic-embedding
honesty rule this file also asserts (`synthetic=True` by default)."""

from __future__ import annotations

from draftforge.evals.retrieval_eval import (
    AblationRow,
    CONFIGS,
    RetrievalFixture,
    load_fixture,
    reciprocal_rank,
    recall_at_k,
    run_ablation,
    run_retrieval_eval,
    synthetic_dense_embed,
    synthetic_sparse_embed,
)


# ---------------------------------------------------------------------------
# Pure metric math -- hand-verifiable, no Qdrant involved
# ---------------------------------------------------------------------------


def test_recall_at_k_hand_computed():
    returned = ["a", "b", "c", "d", "e", "f"]
    relevant = {"b", "c"}
    # both relevant ids land within the first 3 -> recall@3 == 1.0
    assert recall_at_k(returned, relevant, 3) == 1.0
    # only "b" is within the first 2 -> recall@2 == 1/2
    assert recall_at_k(returned, relevant, 2) == 0.5
    # none within the first 1 -> recall@1 == 0.0
    assert recall_at_k(returned, relevant, 1) == 0.0


def test_recall_at_k_empty_relevant_set_is_zero():
    assert recall_at_k(["a", "b"], set(), 5) == 0.0


def test_reciprocal_rank_hand_computed():
    # first relevant id ("c") is at rank 3 -> MRR contribution == 1/3
    assert reciprocal_rank(["a", "b", "c", "d"], {"c", "d"}) == 1 / 3
    # first relevant id at rank 1 -> MRR == 1.0
    assert reciprocal_rank(["x", "y"], {"x"}) == 1.0
    # no relevant id present at all -> MRR == 0.0
    assert reciprocal_rank(["a", "b"], {"z"}) == 0.0


def test_known_relevant_chunk_in_top5_known_mrr_toy_case():
    """A tiny 2-query toy case computed by hand:
    query 1: returned = [c1, c2, c3], relevant = {c1}  -> recall@5=1.0, MRR=1.0
    query 2: returned = [c1, c2, c3], relevant = {c3}  -> recall@5=1.0, MRR=1/3
    Averaged MRR over the two queries = (1.0 + 1/3) / 2 = 2/3.
    """
    q1_returned = ["c1", "c2", "c3"]
    q2_returned = ["c1", "c2", "c3"]
    assert recall_at_k(q1_returned, {"c1"}, 5) == 1.0
    assert reciprocal_rank(q1_returned, {"c1"}) == 1.0
    assert recall_at_k(q2_returned, {"c3"}, 5) == 1.0
    assert reciprocal_rank(q2_returned, {"c3"}) == 1 / 3
    avg_mrr = (reciprocal_rank(q1_returned, {"c1"}) + reciprocal_rank(q2_returned, {"c3"})) / 2
    assert avg_mrr == 2 / 3


# ---------------------------------------------------------------------------
# End-to-end ablation against the shipped labeled fixture (real Qdrant
# :memory: engine, real hybrid_search/RRF fusion, deterministic synthetic
# embeddings)
# ---------------------------------------------------------------------------


def test_load_fixture_reads_the_shipped_labeled_set():
    fixture = load_fixture()
    assert isinstance(fixture, RetrievalFixture)
    assert len(fixture.chunks) >= 6
    assert len(fixture.queries) >= 3
    for q in fixture.queries:
        assert q.relevant_chunk_ids  # every labeled query has at least one known-relevant chunk


def test_all_three_ablation_configs_produce_a_row():
    fixture = load_fixture()
    rows = run_ablation(fixture, synthetic_dense_embed, synthetic_sparse_embed, top_k=10)
    assert len(rows) == 3
    assert {row.config for row in rows} == set(CONFIGS)
    for row in rows:
        assert isinstance(row, AblationRow)
        assert row.num_queries == len(fixture.queries)
        assert 0.0 <= row.recall_at_5 <= 1.0
        assert 0.0 <= row.recall_at_10 <= 1.0
        assert 0.0 <= row.mrr <= 1.0
        # recall@10 can never be lower than recall@5 for the same run (top-5 is a prefix of top-10)
        assert row.recall_at_10 >= row.recall_at_5


def test_known_relevant_chunk_lands_in_top5_for_hybrid():
    """Every labeled query's relevant chunks are drawn from a distinct topic
    with clear keyword separation from the others (see the fixture's own
    comment) -- hybrid (dense+sparse RRF) should always surface at least one
    of them in the top 5."""
    fixture = load_fixture()
    rows = run_ablation(fixture, synthetic_dense_embed, synthetic_sparse_embed, top_k=10)
    hybrid = next(r for r in rows if r.config == "hybrid")
    assert hybrid.recall_at_5 == 1.0
    assert hybrid.mrr == 1.0


def test_run_retrieval_eval_default_is_clearly_labeled_synthetic():
    result = run_retrieval_eval(live=False)
    assert result.synthetic is True
    assert "SYNTHETIC" in result.note
    assert len(result.rows) == 3
    assert result.fixture_num_chunks == len(load_fixture().chunks)
