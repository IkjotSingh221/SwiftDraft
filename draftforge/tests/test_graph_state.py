"""DocumentState (the consistency ledger): round-trips through
model_dump()/model_validate(), and stays compact (spec.md: "serialized,
<=4k tokens") even for a document-sized ledger."""

from __future__ import annotations

from draftforge.graph.state import DocumentState, OutlineTreeNode, SectionBrief


def test_document_state_round_trips_through_dump_and_validate():
    original = DocumentState(
        section_summaries={"introduction": "Introduces the problem and motivates the approach."},
        terminology={"RAG": "retrieval-augmented generation", "BGE-M3": "the dense embedding model used"},
        citation_keys=["smith2020", "doe2019a"],
        figure_counter=2,
        table_counter=1,
        global_claims=["The proposed method reduces latency by 30% over the baseline."],
    )
    dumped = original.model_dump()
    restored = DocumentState.model_validate(dumped)
    assert restored == original


def test_document_state_defaults_are_empty_and_compact():
    empty = DocumentState()
    assert empty.render_compact() == ""
    assert empty.approx_token_count() == 0


def test_document_state_stays_compact_for_a_document_sized_ledger():
    # Simulate a ~50-page document: ~40 completed leaf sections, a healthy
    # terminology registry, and a few dozen citation keys/claims.
    state = DocumentState(
        section_summaries={
            f"section_{i}": f"This section covers topic {i} and its relationship to the overall argument."
            for i in range(40)
        },
        terminology={f"TERM{i}": f"definition of term {i}" for i in range(20)},
        citation_keys=[f"author{i}2020" for i in range(60)],
        figure_counter=12,
        table_counter=8,
        global_claims=[f"Claim number {i} established earlier in the document." for i in range(30)],
    )
    # ~4k tokens is the spec.md budget; the word-count heuristic (see
    # DECISIONS.md) is a mild undercount vs. a real tokenizer, so assert
    # comfortably under budget rather than right at the line.
    assert state.approx_token_count() < 3000


def test_outline_tree_node_is_leaf_and_iter_tree():
    leaf = OutlineTreeNode(id="intro", title="Introduction", target_words=500)
    parent = OutlineTreeNode(id="method", title="Method", children=[leaf])
    assert leaf.is_leaf()
    assert not parent.is_leaf()
    assert [n.id for n in parent.iter_tree()] == ["method", "intro"]


def test_section_brief_round_trips():
    brief = SectionBrief(
        id="method_data",
        title="Data",
        brief="Describe the dataset and preprocessing.",
        source_tags=["source-1"],
        target_words=250,
        parent_path=["method"],
        format_section_id="method_data",
    )
    assert SectionBrief.model_validate(brief.model_dump()) == brief
