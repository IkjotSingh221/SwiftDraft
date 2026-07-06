"""Graph state — the contract Phases 4/5 extend. Read this file before
touching `build_graph.py`, `planner.py`, or any later-phase node.

Two families of types live here:

1. Pydantic models (`SectionBrief`, `OutlineTreeNode`, `DocumentState`,
   `SectionStatusEntry`) — the *shapes* of data that flow through the graph.
   These are what nodes construct/validate against; they get serialized to
   plain dicts (`model_dump()`) before being written into `RunState`, because
   the LangGraph checkpointer (SqliteSaver) persists whatever is in the
   TypedDict via its own serializer, and plain JSON-able dicts/lists are the
   safest, most portable thing to hand it (see DECISIONS.md).

2. `RunState` — the actual LangGraph state TypedDict, checkpointed per run
   (thread_id == run_id). Every node function takes a `RunState` (or a
   subset via `.get`) and returns a partial-update dict merged into it.

Non-negotiable constraint this file exists to enforce (spec.md #1, #6): no
node ever holds full section prose in this state, and cross-section
consistency comes only from the compact `DocumentState` ledger, not from
passing completed section text between agents.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Run status
# ---------------------------------------------------------------------------

# The lifecycle a run moves through. Phase 3 only ever reaches
# "awaiting_outline_approval" or "completed"/"error" — "drafting", "verifying"
# and "rendering" are reserved for Phases 4-6's nodes to set.
RunStatus = Literal[
    "queued",
    "planning",
    "awaiting_outline_approval",
    "drafting",
    "verifying",
    "rendering",
    "completed",
    "error",
]

SectionRunStatus = Literal["queued", "drafting", "critiquing", "verifying", "done", "flagged"]


# ---------------------------------------------------------------------------
# Outline / leaf brief shapes
# ---------------------------------------------------------------------------


class SectionBrief(BaseModel):
    """One leaf section of the outline — the unit of work Phase 4's drafter
    subgraph fans out over.

    A "leaf" is any outline node with no children, which by construction
    corresponds 1:1 with a leaf `SectionSpec` in the `FormatSpec` tree (see
    `formats/schema.py`) — per spec.md non-negotiable #1, drafting always
    happens per leaf section (500-1500 words), never above.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    brief: str
    source_tags: list[str] = Field(default_factory=list)
    target_words: int
    parent_path: list[str] = Field(default_factory=list)
    format_section_id: str


class OutlineTreeNode(BaseModel):
    """One node in the planner's hierarchical outline (chapter -> section ->
    leaf subsection). Mirrors `api.models.OutlineNode` field-for-field so the
    API layer can convert between them with a plain `model_dump()`/
    `model_validate()` — kept as two separate types (API DTO vs. internal
    graph type) per the Phase 0 precedent of not conflating API/registry
    representations (see DECISIONS.md's `ModelInfo` entry).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    brief: str | None = None
    target_words: int | None = None
    source_tags: list[str] = Field(default_factory=list)
    children: list["OutlineTreeNode"] = Field(default_factory=list)

    def iter_tree(self):
        yield self
        for child in self.children:
            yield from child.iter_tree()

    def is_leaf(self) -> bool:
        return not self.children


OutlineTreeNode.model_rebuild()


# ---------------------------------------------------------------------------
# DocumentState — the consistency ledger (spec.md "DocumentState")
# ---------------------------------------------------------------------------


class DocumentState(BaseModel):
    """The cross-section consistency ledger, passed (serialized) to every
    drafter. Deliberately compact — it stores SUMMARIES, not prose, so it
    stays well under ~4k tokens even for a 50+ page document with a deep
    outline (spec.md non-negotiable #6: "grows with the outline, not the
    prose"; never pass completed section text between agents).

    Fields, one-to-one with spec.md's "DocumentState (the consistency
    ledger)" bullet list:
      - `section_summaries`: outline tree summaries — 1-2 sentences per
        COMPLETED section, keyed by leaf section id. Populated by a
        post-draft node (Phase 4), never by the planner.
      - `terminology`: acronym/notation registry — term -> preferred
        definition/phrasing, so later sections reuse it verbatim.
      - `citation_keys`: every bibkey cited so far (registry, not per-section
        detail) — lets a drafter know what's already been cited.
      - `figure_counter` / `table_counter`: running numbering counters.
      - `global_claims`: short bullet strings of claims already made
        document-wide, so later sections don't repeat/contradict them.
    """

    model_config = ConfigDict(extra="forbid")

    section_summaries: dict[str, str] = Field(default_factory=dict)
    terminology: dict[str, str] = Field(default_factory=dict)
    citation_keys: list[str] = Field(default_factory=list)
    figure_counter: int = 0
    table_counter: int = 0
    global_claims: list[str] = Field(default_factory=list)

    def render_compact(self) -> str:
        """Serialize to a compact plain-text block for drafter prompts.

        Plain text, not JSON — cheaper in tokens and just as parseable by an
        LLM reading it as context. Empty sub-registries are omitted entirely
        rather than rendered as "(none)" to save tokens.
        """
        lines: list[str] = []
        if self.section_summaries:
            lines.append("Completed sections so far:")
            for section_id, summary in self.section_summaries.items():
                lines.append(f"- {section_id}: {summary}")
        if self.terminology:
            lines.append("Terminology/notation already established:")
            for term, definition in self.terminology.items():
                lines.append(f"- {term}: {definition}")
        if self.citation_keys:
            lines.append("Citation keys already used: " + ", ".join(self.citation_keys))
        if self.figure_counter or self.table_counter:
            lines.append(
                f"Numbering so far: {self.figure_counter} figure(s), {self.table_counter} table(s)."
            )
        if self.global_claims:
            lines.append("Claims already made document-wide:")
            for claim in self.global_claims:
                lines.append(f"- {claim}")
        return "\n".join(lines)

    def approx_token_count(self) -> int:
        """Whitespace word-count heuristic (see DECISIONS.md Phase 1 entry on
        `chunker.count_tokens` — same documented proxy, reused here rather
        than adding a real tokenizer dependency)."""
        return len(self.render_compact().split())


# ---------------------------------------------------------------------------
# Per-section live status (Phase 4/5 populate this; Phase 3 only declares it)
# ---------------------------------------------------------------------------


class SectionStatusEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SectionRunStatus = "queued"
    tokens_used: int = 0
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Top-level LangGraph state
# ---------------------------------------------------------------------------


class RunState(TypedDict, total=False):
    """The full LangGraph checkpointed state for one run. `thread_id` in the
    checkpointer config is always the run_id (see build_graph.py).

    Every value below is a plain JSON-able dict/list/str/int/float, not a
    pydantic instance — nodes construct/validate with the pydantic models
    above, then call `.model_dump()` before returning a state update, so the
    checkpointer never has to pickle anything but built-in types.

    Phase 4/5 read `leaf_briefs` (what to draft, in document order) and
    `document_state` (what to stay consistent with) — they must NOT read
    `outline` for prose content (it carries no completed text, only
    structure/briefs) and must NOT introduce a field carrying full section
    text into this TypedDict (spec.md non-negotiable #1/#6).
    """

    run_id: str
    project_id: str
    format_spec_id: str
    run_config: dict[str, Any]

    status: RunStatus
    error: str | None

    # Planner output (Phase 3)
    outline: list[dict[str, Any]]  # list[OutlineTreeNode.model_dump()], top-level nodes
    leaf_briefs: list[dict[str, Any]]  # list[SectionBrief.model_dump()], in document order

    # Consistency ledger (Phase 3 initializes empty; Phase 4 updates per section)
    document_state: dict[str, Any]  # DocumentState.model_dump()

    # Bookkeeping
    decisions_log_path: str
    section_status: dict[str, dict[str, Any]]  # leaf id -> SectionStatusEntry.model_dump()
