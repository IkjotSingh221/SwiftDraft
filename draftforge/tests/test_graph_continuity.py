"""Phase 5 continuity editor (hermetic): the editor sees and patches ONLY the
two boundary paragraphs (A's tail, B's head) -- every other paragraph of both
sections is left byte-for-byte unchanged (constraints #1/#6) -- and a
no-op/failed edit changes nothing. The LLM is always injected."""

from __future__ import annotations

import json

from draftforge.graph.continuity import (
    ContinuityDiff,
    apply_boundary_patch,
    edit_boundary,
    split_paragraphs,
)
from draftforge.llm.base import LLMResponse, TokenUsage


class ContinuityProvider:
    provider_name = "fake"

    def __init__(self, payload: dict | None = None, raises: bool = False):
        self.payload = payload
        self.raises = raises

    def complete(self, messages, *, model, max_tokens, temperature, json_mode):
        if self.raises:
            raise RuntimeError("continuity model down")
        return LLMResponse(
            text=json.dumps(self.payload or {}),
            usage=TokenUsage(prompt_tokens=8, completion_tokens=8, total_tokens=16),
            model=model, provider=self.provider_name, cost_usd=0.001,
        )


def test_apply_boundary_patch_touches_only_boundary_paragraphs():
    a_md = "A first para.\n\nA MIDDLE para that must not change.\n\nA last para."
    b_md = "B first para.\n\nB middle para.\n\nB last para must not change."
    diff = ContinuityDiff(
        a_id="a", b_id="b",
        a_before="A last para.", a_after="A last para, leading into the next section.",
        b_before="B first para.", b_after="Building on the previous section, B first para.",
        changed=True,
    )
    new_a, new_b = apply_boundary_patch(a_md, b_md, diff)

    # Only A's last paragraph and B's first paragraph changed.
    assert "A MIDDLE para that must not change." in new_a
    assert "A first para." in new_a
    assert "leading into the next section" in new_a
    assert "B last para must not change." in new_b
    assert "B middle para." in new_b
    assert "Building on the previous section" in new_b


def test_noop_diff_leaves_both_bodies_unchanged():
    a_md = "P1.\n\nP2."
    b_md = "Q1.\n\nQ2."
    diff = ContinuityDiff(a_id="a", b_id="b", changed=False)
    new_a, new_b = apply_boundary_patch(a_md, b_md, diff)
    assert new_a == a_md
    assert new_b == b_md


def test_edit_boundary_reports_a_change():
    provider = ContinuityProvider(
        payload={
            "a_tail": "A tail, now transitioning.",
            "b_head": "Following on, B head.",
            "changed": True,
            "note": "smoothed the transition",
        }
    )
    diff = edit_boundary(
        "a", "b", "A tail.", "B head.", "ledger",
        provider=provider, model_name="fake",
    )
    assert diff.changed
    assert diff.a_after == "A tail, now transitioning."
    assert diff.b_after == "Following on, B head."
    assert diff.tokens_used == 16


def test_edit_boundary_changed_false_is_a_noop():
    provider = ContinuityProvider(
        payload={"a_tail": "A tail.", "b_head": "B head.", "changed": False, "note": "fine"}
    )
    diff = edit_boundary("a", "b", "A tail.", "B head.", "ledger", provider=provider, model_name="f")
    assert not diff.changed
    assert diff.a_after == "A tail."


def test_edit_boundary_never_raises_on_model_failure():
    provider = ContinuityProvider(raises=True)
    diff = edit_boundary("a", "b", "A tail.", "B head.", "ledger", provider=provider, model_name="f")
    assert not diff.changed
    assert diff.a_after == "A tail."
    assert "error" in diff.note


def test_split_paragraphs_drops_blanks():
    assert split_paragraphs("P1.\n\n\n\nP2.\n\n") == ["P1.", "P2."]
