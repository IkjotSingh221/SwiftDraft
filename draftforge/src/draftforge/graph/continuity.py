"""Phase 5: continuity editor.

Per spec.md: "Continuity editor: reviews adjacent-section boundary paragraphs +
DocumentState; patches transitions only." Critically it obeys non-negotiables
#1/#6 -- it NEVER sees or rewrites a full section body. For an adjacent pair
(A, B) it is given only:
  - the LAST paragraph of A (A's tail),
  - the FIRST paragraph of B (B's head),
  - the compact `DocumentState` ledger,
and it may rewrite ONLY those two boundary paragraphs to smooth the transition.
Every other paragraph of both sections is left byte-for-byte unchanged (this is
enforced in code here, not trusted to the model, and asserted in the tests).

The model resolves via `resolve_model("continuity_editor")` at call time. Each
boundary edit is recorded as a `ContinuityDiff` (before/after of just the two
paragraphs) for the Review screen's per-boundary diff view.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from draftforge.llm.base import LLMMessage, LLMResponse
from draftforge.llm.settings_store import resolve_model

logger = logging.getLogger(__name__)

CONTINUITY_MAX_TOKENS = 1200


class ContinuityDiff(BaseModel):
    """A single adjacent-boundary edit, for the Review UI's diff view."""

    a_id: str
    b_id: str
    a_before: str = ""
    a_after: str = ""
    b_before: str = ""
    b_after: str = ""
    changed: bool = False
    note: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0


def split_paragraphs(markdown: str) -> list[str]:
    """Split section Markdown into paragraph blocks on blank lines, preserving
    each block's internal text. Empty blocks are dropped."""
    blocks = [b.strip("\n") for b in markdown.split("\n\n")]
    return [b for b in blocks if b.strip()]


def _rejoin(paragraphs: list[str]) -> str:
    return "\n\n".join(paragraphs) + "\n"


_CONTINUITY_SYSTEM = (
    "You are a continuity editor for an academic document. You are given the "
    "LAST paragraph of one section and the FIRST paragraph of the very next "
    "section, plus a ledger of what the document has covered so far. Improve "
    "ONLY the transition between them: you may lightly revise these two "
    "paragraphs so they connect smoothly, but you MUST preserve their facts, "
    "any [@citation] markers, and their length. Do not add new claims or "
    "citations. If the transition is already fine, return them unchanged. "
    'Respond JSON ONLY: {"a_tail": "<revised last paragraph of A>", '
    '"b_head": "<revised first paragraph of B>", "changed": bool, '
    '"note": "<one short line on what you changed, or why nothing changed>"}'
)


def edit_boundary(
    a_id: str,
    b_id: str,
    a_tail: str,
    b_head: str,
    document_state_compact: str,
    *,
    provider: Any | None = None,
    model_name: str | None = None,
) -> ContinuityDiff:
    """Ask the continuity editor to smooth one A->B boundary.

    Never raises: any failure yields an unchanged (no-op) diff so one bad
    boundary can't sink the review pass."""
    provider_, model_ = (provider, model_name)
    if provider_ is None or model_ is None:
        provider_, model_ = resolve_model("continuity_editor")

    payload = {
        "section_a_id": a_id,
        "section_b_id": b_id,
        "a_last_paragraph": a_tail,
        "b_first_paragraph": b_head,
        "document_state": document_state_compact,
    }
    try:
        response: LLMResponse = provider_.complete(
            [
                LLMMessage(role="system", content=_CONTINUITY_SYSTEM),
                LLMMessage(role="user", content=json.dumps(payload)),
            ],
            model=model_,
            max_tokens=CONTINUITY_MAX_TOKENS,
            temperature=0.2,
            json_mode=True,
        )
        data = json.loads(response.text)
        if not isinstance(data, dict):
            raise ValueError("continuity response was not a JSON object")
        new_a = data.get("a_tail")
        new_b = data.get("b_head")
        note = str(data.get("note") or "")
        new_a = new_a if isinstance(new_a, str) and new_a.strip() else a_tail
        new_b = new_b if isinstance(new_b, str) and new_b.strip() else b_head
        changed = bool(data.get("changed")) and (new_a != a_tail or new_b != b_head)
        return ContinuityDiff(
            a_id=a_id,
            b_id=b_id,
            a_before=a_tail,
            a_after=new_a if changed else a_tail,
            b_before=b_head,
            b_after=new_b if changed else b_head,
            changed=changed,
            note=note,
            tokens_used=response.usage.total_tokens,
            cost_usd=response.cost_usd,
        )
    except Exception as exc:  # noqa: BLE001 - a bad boundary must never sink review
        logger.warning("continuity: boundary %s->%s failed (%s)", a_id, b_id, exc)
        return ContinuityDiff(
            a_id=a_id, b_id=b_id, a_before=a_tail, a_after=a_tail,
            b_before=b_head, b_after=b_head, changed=False, note=f"error: {exc}",
        )


def apply_boundary_patch(a_markdown: str, b_markdown: str, diff: ContinuityDiff) -> tuple[str, str]:
    """Apply a boundary diff to the two section bodies, touching ONLY A's last
    paragraph and B's first paragraph. Returns (new_a_markdown, new_b_markdown).

    If the diff is a no-op, the inputs are returned unchanged. This function is
    the single enforcement point for "patches transitions only": it swaps
    exactly one paragraph in each body and rejoins the rest verbatim."""
    if not diff.changed:
        return a_markdown, b_markdown

    a_paras = split_paragraphs(a_markdown)
    b_paras = split_paragraphs(b_markdown)
    if a_paras and diff.a_after and diff.a_after != a_paras[-1]:
        a_paras[-1] = diff.a_after
    if b_paras and diff.b_after and diff.b_after != b_paras[0]:
        b_paras[0] = diff.b_after
    return _rejoin(a_paras), _rejoin(b_paras)
