"""Planner node: format spec + a retrieval sample per required section ->
hierarchical outline with per-leaf briefs.

Pipeline, per spec.md's Phase 3 description:
  1. Load the `FormatSpec` (formats/schema.py) for `state["format_spec_id"]`.
  2. Retrieval sample: for every LEAF section in the spec's tree, run
     `hybrid_search` (ingest/store.py) to pull a few representative chunks —
     used both as planner context and as the source of a leaf's default
     `source_tags`. Handles an empty/unreachable store gracefully (empty
     samples), since a project may have just been created.
  3. Resolve the planner's model via `resolve_model("planner")` AT CALL TIME
     (never hardcoded) and ask it for a JSON outline (`json_mode=True`).
  4. Validate + repair: the LLM's outline is merged onto a deterministic
     skeleton built directly from the `FormatSpec` tree, so the final outline
     ALWAYS has every required section present at the right nesting depth —
     a malformed/missing/empty LLM response degrades to the skeleton's
     defaults rather than failing the run.
  5. Flatten into `leaf_briefs` (one per leaf outline node, in document
     order) and log every decision (retrieval samples, raw LLM outline,
     validation/repair result) to the per-run JSONL decisions log.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from draftforge.formats.schema import FormatSpec, SectionSpec, list_available_specs, load_spec
from draftforge.graph.decisions import log_decision
from draftforge.graph.state import OutlineTreeNode, RunState, SectionBrief
from draftforge.ingest.store import SearchHit, get_qdrant_client, hybrid_search
from draftforge.llm.base import LLMMessage
from draftforge.llm.settings_store import resolve_model

logger = logging.getLogger(__name__)

DEFAULT_TARGET_WORDS = 500
RETRIEVAL_SAMPLE_TOP_K = 3
PLANNER_MAX_TOKENS = 4096

_SYSTEM_PROMPT = """\
You are the planning agent for an academic document drafting system. You are
given a format specification's required section structure (with word-count
ranges) and a small retrieval sample of the student's own source material for
each leaf section. Produce a hierarchical outline that exactly mirrors the
given section structure (same ids, same nesting) and, for every leaf section,
a one-to-two-sentence brief describing what that section should cover, drawn
from the retrieval sample and the section's purpose.

Respond with JSON ONLY: a list of section node objects, each shaped as
{"id": str, "title": str, "brief": str | null, "target_words": int | null,
"source_tags": [str], "children": [<same shape>]}. `target_words` is only
meaningful for leaf nodes (empty "children"); set it to null for non-leaf
nodes. Do not add, remove, or reorder top-level sections or subsections.
"""


# ---------------------------------------------------------------------------
# Spec lookup
# ---------------------------------------------------------------------------


def _load_spec_by_id(format_spec_id: str) -> FormatSpec:
    for summary in list_available_specs():
        if summary.spec_id == format_spec_id:
            return load_spec(summary.path)
    raise ValueError(f"Unknown format_spec_id: {format_spec_id!r}")


# ---------------------------------------------------------------------------
# Retrieval sample
# ---------------------------------------------------------------------------


def sample_retrieval(
    project_id: str,
    spec: FormatSpec,
    *,
    top_k: int = RETRIEVAL_SAMPLE_TOP_K,
    qdrant_client_factory=get_qdrant_client,
    hybrid_search_fn=hybrid_search,
) -> dict[str, list[SearchHit]]:
    """One `hybrid_search` sample per leaf section, keyed by section id.

    Gracefully degrades to empty samples (per leaf) if the store is
    unavailable or empty — a brand-new project may not have finished
    ingestion yet, and that must never crash planning.
    """
    leaves = [s for s in spec.iter_all_sections() if not s.subsections]
    try:
        client = qdrant_client_factory()
    except Exception as exc:  # noqa: BLE001 - store unavailable is expected/handled
        logger.warning("planner: qdrant client unavailable (%s); retrieval samples empty", exc)
        return {leaf.id: [] for leaf in leaves}

    samples: dict[str, list[SearchHit]] = {}
    for leaf in leaves:
        try:
            samples[leaf.id] = hybrid_search_fn(client, project_id, leaf.title, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - e.g. collection doesn't exist yet
            logger.warning("planner: hybrid_search failed for section %r (%s)", leaf.id, exc)
            samples[leaf.id] = []
    return samples


# ---------------------------------------------------------------------------
# Deterministic skeleton (guarantees required-structure conformance)
# ---------------------------------------------------------------------------


def _default_target_words(section: SectionSpec) -> int:
    if section.word_range is None:
        return 0  # e.g. "references": no prose word-count target (see DECISIONS.md)
    return (section.word_range.min + section.word_range.max) // 2


def _skeleton_node(section: SectionSpec, retrieval_samples: dict[str, list[SearchHit]]) -> OutlineTreeNode:
    hits = retrieval_samples.get(section.id, [])
    source_tags = sorted({hit.source_id for hit in hits if hit.source_id})
    is_leaf = not section.subsections
    return OutlineTreeNode(
        id=section.id,
        title=section.title,
        brief=f"Cover the required content of '{section.title}' per the format spec."
        if is_leaf
        else None,
        target_words=_default_target_words(section) if is_leaf else None,
        source_tags=source_tags,
        children=[_skeleton_node(child, retrieval_samples) for child in section.subsections],
    )


def build_skeleton(spec: FormatSpec, retrieval_samples: dict[str, list[SearchHit]]) -> list[OutlineTreeNode]:
    return [_skeleton_node(section, retrieval_samples) for section in spec.sections]


# ---------------------------------------------------------------------------
# LLM outline parsing
# ---------------------------------------------------------------------------


def _node_from_dict(item: dict[str, Any]) -> OutlineTreeNode:
    children_raw = item.get("children") or item.get("subsections") or []
    children = [_node_from_dict(c) for c in children_raw if isinstance(c, dict)]
    target_words = item.get("target_words")
    if not isinstance(target_words, int):
        target_words = None
    return OutlineTreeNode(
        id=str(item["id"]),
        title=str(item.get("title") or item["id"]),
        brief=item.get("brief") if isinstance(item.get("brief"), str) else None,
        target_words=target_words,
        source_tags=[str(t) for t in item.get("source_tags", []) if isinstance(t, (str, int))],
        children=children,
    )


def parse_llm_outline(text: str) -> list[OutlineTreeNode]:
    """Best-effort parse of the planner LLM's JSON response into outline
    nodes. Returns an empty list (never raises) on anything unparseable —
    the caller then falls back entirely to the deterministic skeleton.
    """
    data: Any = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                data = None

    if isinstance(data, dict):
        data = data.get("outline") or data.get("sections") or []
    if not isinstance(data, list):
        return []

    nodes: list[OutlineTreeNode] = []
    for item in data:
        if not isinstance(item, dict) or "id" not in item:
            continue
        try:
            nodes.append(_node_from_dict(item))
        except Exception as exc:  # noqa: BLE001 - one bad node shouldn't drop the rest
            logger.warning("planner: skipping unparseable outline node (%s)", exc)
    return nodes


def _flatten_by_id(nodes: list[OutlineTreeNode]) -> dict[str, OutlineTreeNode]:
    out: dict[str, OutlineTreeNode] = {}
    for node in nodes:
        out.setdefault(node.id, node)
        out.update(_flatten_by_id(node.children))
    return out


# ---------------------------------------------------------------------------
# Validation + repair against the FormatSpec structure
# ---------------------------------------------------------------------------


def _merge_node(
    skeleton_node: OutlineTreeNode,
    spec_section: SectionSpec,
    candidate_by_id: dict[str, OutlineTreeNode],
    violations: list[str],
) -> OutlineTreeNode:
    candidate = candidate_by_id.get(skeleton_node.id)

    if candidate is None:
        if spec_section.required:
            violations.append(f"missing_required_section:{skeleton_node.id}")
        title = skeleton_node.title
        brief = skeleton_node.brief
        target_words = skeleton_node.target_words
        source_tags = skeleton_node.source_tags
    else:
        title = candidate.title or skeleton_node.title
        brief = candidate.brief or skeleton_node.brief
        source_tags = candidate.source_tags or skeleton_node.source_tags
        is_leaf = not spec_section.subsections
        if is_leaf:
            target_words = candidate.target_words if candidate.target_words else skeleton_node.target_words
            if spec_section.word_range is not None and target_words is not None:
                lo, hi = spec_section.word_range.min, spec_section.word_range.max
                if not (lo <= target_words <= hi):
                    violations.append(f"target_words_out_of_range:{skeleton_node.id}")
                    target_words = max(lo, min(hi, target_words))
        else:
            target_words = None

    children = [
        _merge_node(sk_child, spec_child, candidate_by_id, violations)
        for sk_child, spec_child in zip(skeleton_node.children, spec_section.subsections)
    ]
    return OutlineTreeNode(
        id=skeleton_node.id,
        title=title,
        brief=brief,
        target_words=target_words,
        source_tags=source_tags,
        children=children,
    )


def validate_and_repair(
    candidate: list[OutlineTreeNode],
    skeleton: list[OutlineTreeNode],
    spec: FormatSpec,
) -> tuple[list[OutlineTreeNode], list[str]]:
    """Merge the LLM's candidate outline onto the deterministic skeleton.

    The result ALWAYS has the exact structure of `spec.sections` (every
    required section present, nesting matching the spec tree) — the skeleton
    wins wherever the candidate is missing, malformed, or out of range.
    Returns (repaired_outline, violation_strings) for the decisions log.
    """
    violations: list[str] = []
    if not candidate:
        violations.append("unparseable_or_empty_llm_outline")
    candidate_by_id = _flatten_by_id(candidate)
    merged = [
        _merge_node(sk, sp, candidate_by_id, violations)
        for sk, sp in zip(skeleton, spec.sections)
    ]
    return merged, violations


# ---------------------------------------------------------------------------
# Leaf brief flattening
# ---------------------------------------------------------------------------


def flatten_leaf_briefs(outline: list[OutlineTreeNode]) -> list[SectionBrief]:
    """Depth-first flatten of the outline into leaf briefs, in document
    order — the exact unit list Phase 4's drafter fans out over."""
    briefs: list[SectionBrief] = []

    def walk(node: OutlineTreeNode, parents: list[str]) -> None:
        if node.is_leaf():
            briefs.append(
                SectionBrief(
                    id=node.id,
                    title=node.title,
                    brief=node.brief or "",
                    source_tags=node.source_tags,
                    target_words=node.target_words or DEFAULT_TARGET_WORDS,
                    parent_path=parents,
                    format_section_id=node.id,
                )
            )
            return
        for child in node.children:
            walk(child, parents + [node.id])

    for top in outline:
        walk(top, [])
    return briefs


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _section_spec_summary(section: SectionSpec) -> dict[str, Any]:
    return {
        "id": section.id,
        "title": section.title,
        "required": section.required,
        "word_range": (
            {"min": section.word_range.min, "max": section.word_range.max}
            if section.word_range
            else None
        ),
        "subsections": [_section_spec_summary(c) for c in section.subsections],
    }


def build_planner_prompt(
    spec: FormatSpec,
    retrieval_samples: dict[str, list[SearchHit]],
    run_config: dict[str, Any],
) -> str:
    spec_tree = [_section_spec_summary(s) for s in spec.sections]
    samples_summary = {
        section_id: [
            {"source_id": h.source_id, "section_path": h.section_path, "text": h.text[:500]}
            for h in hits
        ]
        for section_id, hits in retrieval_samples.items()
    }
    payload = {
        "format_spec": {
            "spec_id": spec.spec_id,
            "name": spec.name,
            "required_sections": spec_tree,
        },
        "retrieval_samples": samples_summary,
        "run_config": run_config,
    }
    return (
        "Build the outline for this document. Required section structure, "
        "retrieval samples per leaf section, and any run configuration are "
        "below as JSON:\n\n" + json.dumps(payload, indent=2)
    )


# ---------------------------------------------------------------------------
# Node entrypoint
# ---------------------------------------------------------------------------


def planner_node(state: RunState) -> dict[str, Any]:
    """LangGraph node: `START -> planner`. Returns a partial `RunState`
    update; the graph pauses right after this node (`interrupt_after`) so the
    student can review/edit the outline before the run continues.
    """
    run_id = state["run_id"]
    project_id = state["project_id"]
    format_spec_id = state["format_spec_id"]
    run_config = state.get("run_config") or {}
    log_path = state.get("decisions_log_path")

    spec = _load_spec_by_id(format_spec_id)

    retrieval_samples = sample_retrieval(project_id, spec)
    log_decision(
        run_id,
        "planner",
        "retrieval_sample",
        {
            section_id: [hit.model_dump() for hit in hits]
            for section_id, hits in retrieval_samples.items()
        },
        log_path=log_path,
    )

    provider, model_name = resolve_model("planner")
    prompt = build_planner_prompt(spec, retrieval_samples, run_config)
    response = provider.complete(
        [
            LLMMessage(role="system", content=_SYSTEM_PROMPT),
            LLMMessage(role="user", content=prompt),
        ],
        model=model_name,
        max_tokens=PLANNER_MAX_TOKENS,
        temperature=0.2,
        json_mode=True,
    )
    log_decision(
        run_id,
        "planner",
        "raw_outline",
        {"model": model_name, "provider": provider.provider_name, "text": response.text},
        log_path=log_path,
    )

    candidate = parse_llm_outline(response.text)
    skeleton = build_skeleton(spec, retrieval_samples)
    outline, violations = validate_and_repair(candidate, skeleton, spec)
    log_decision(run_id, "planner", "validation", {"violations": violations}, log_path=log_path)

    leaf_briefs = flatten_leaf_briefs(outline)

    return {
        "outline": [n.model_dump() for n in outline],
        "leaf_briefs": [b.model_dump() for b in leaf_briefs],
        "status": "awaiting_outline_approval",
    }
