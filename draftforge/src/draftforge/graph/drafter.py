"""Phase 4: the per-leaf corrective-RAG drafter subgraph, bounded fan-out over
all leaf briefs, and the DocumentState update step.

## Per-leaf pipeline (`draft_leaf`)

For one `SectionBrief`:

    query rewrite (LLM) -> filtered hybrid retrieval -> LLM chunk-relevance
    grading -> (poor grades? rewrite + retry, up to 2x) -> draft with
    [@bibkey] markers (LLM) -> enforce valid bibkeys (pure code) ->
    self-critique (LLM) -> at most one revision (LLM) -> re-enforce bibkeys
    -> write section file to disk.

Every LLM call resolves its model via `resolve_model(role)` at call time
(role "drafter" for query rewrite/grading/drafting/revision, "critic" for
critique) -- never a hardcoded model name. Every agent decision (query
rewrites, chunk grades, critique verdicts, bibkey stripping) is logged via
`graph.decisions.log_decision`.

A leaf's own pipeline NEVER raises: any exception (LLM/network/Qdrant
unavailable) is caught, logged as a `leaf_pipeline_error` decision, and the
leaf is marked "flagged" -- see DECISIONS.md ("fail fast on the first LLM
failure per leaf") for why a leaf aborts on its *first* failing LLM call
rather than retrying through the whole pipeline: it keeps a single dead
leaf cheap (bounded to one provider.complete() retry-and-fail sequence)
instead of compounding retries across every remaining LLM call in that
leaf's pipeline.

## Constraint #2 -- hallucinated citation keys are impossible by construction

The drafter is only ever given the bibkeys attached to its OWN retrieved
chunks (`SearchHit.bibkeys`) as the "valid keys" set for that leaf. After
every LLM call that can produce `[@key]` markers (draft, revision),
`enforce_valid_bibkeys` strips any key not in that set before the text is
written to disk or folded into DocumentState's citation registry -- there is
no code path by which an invented key can survive into a saved draft.

## Fan-out / bounded parallelism

A single synchronous LangGraph node (`drafter_fanout_node`) drives a
`concurrent.futures.ThreadPoolExecutor` with `max_workers` bounded by
`run_config["drafter_parallelism"]` (default `DEFAULT_PARALLELISM` = 3), one
task per leaf brief. This -- not LangGraph's `Send` API / nested
per-leaf subgraphs with their own checkpoints -- was chosen because (a) every
LLM/retrieval call in this codebase is a blocking synchronous call (no
`asyncio` client anywhere in `llm/`), so a thread pool is the natural bounded-
concurrency primitive without inventing an async wrapper layer, and (b) a
single top-level node keeps the checkpointer's unit of work exactly one
`RunState` update per `graph.invoke()` call, matching the existing
"reopen a fresh SqliteSaver connection per call" resumability story in
`build_graph.py` -- nested subgraph checkpoints would need their own thread_id
scheme with no corresponding benefit for a single-user local tool. See
DECISIONS.md.

## DocumentState update ("post-draft node")

`apply_document_state_update` is a small, independently-testable pure
function -- called once per completed leaf from the main thread of
`drafter_fanout_node`'s `as_completed` loop (never from a worker thread) --
that folds one `LeafDraftResult` into the running `DocumentState`: section
summary, newly-used citation keys, figure/table counters, and up to 3 short
global claims. It plays the role of spec.md's "a post-draft node [that]
updates it after each section completes", implemented as a function rather
than a second graph node so it composes cleanly with the thread-pool fan-out
above (see DECISIONS.md).

## Never store prose in RunState (constraints #1 / #6)

`draft_leaf` writes the (possibly revised) section Markdown to
`data/runs/{run_id}/sections/{leaf_id}.md` and returns only a `LeafDraftResult`
(status, file path, citation keys used, a short summary, word/figure/table
counts, tokens/cost) -- never the prose itself -- back to the fan-out node,
which in turn only ever writes `section_status` (status/tokens/cost) and
`document_state` (summaries/registries/counters) into `RunState`.

## Events (for the SSE dashboard)

`emit_event`/`read_events` maintain a small durable, append-only JSONL log at
`data/runs/{run_id}/events.jsonl` -- the same "reopen per call" style as
`graph/decisions.py`'s decisions log, and deliberately a SEPARATE file from
it: `decisions.jsonl` is the full agent-decision audit trail (query rewrites,
grades, critique verdicts, with full payloads); `events.jsonl` is a smaller,
UI-facing stream of section-status transitions and token/cost counters, sized
and shaped for the Run Dashboard and its SSE endpoint (see `api/sse.py`). A
client that (re)connects to the SSE endpoint always replays this file from
byte 0 first, then keeps tailing it -- the file itself is the "replay
buffer", so reattachment after a crash/resume needs no separate in-memory
pub/sub (see DECISIONS.md).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from draftforge.graph import decisions
from draftforge.graph.decisions import log_decision
from draftforge.graph.state import DocumentState, RunState, SectionBrief, SectionStatusEntry
from draftforge.ingest.store import SearchHit, get_qdrant_client, hybrid_search
from draftforge.llm.base import LLMMessage, LLMResponse
from draftforge.llm.settings_store import resolve_model

logger = logging.getLogger(__name__)

DEFAULT_PARALLELISM = 3
MAX_QUERY_REWRITES = 2  # + the initial rewrite = up to 3 retrieval attempts
RETRIEVAL_TOP_K = 6
GRADE_RELEVANT_RATIO_THRESHOLD = 0.5
DRAFT_MAX_TOKENS = 3000
QUERY_REWRITE_MAX_TOKENS = 100
GRADE_MAX_TOKENS = 600
CRITIQUE_MAX_TOKENS = 500

# Pandoc citation marker grammar (see formats/validator.py's own regex for the
# same grammar): `[@key1;@key2]`, each entry `@` + `[A-Za-z0-9][A-Za-z0-9_:.-]*`.
CITATION_MARKER_RE = re.compile(r"\[(@[^\]]+)\]")
CITATION_KEY_RE = re.compile(r"^@([A-Za-z0-9][A-Za-z0-9_:.\-]*)$")


# ---------------------------------------------------------------------------
# Events log (UI-facing; see module docstring for why this is separate from
# graph/decisions.py's decisions log)
# ---------------------------------------------------------------------------

_events_lock = threading.Lock()


def events_log_path(run_id: str) -> Path:
    return decisions.run_dir(run_id) / "events.jsonl"


def emit_event(
    run_id: str,
    event: str,
    *,
    events_path: str | Path | None = None,
    **fields: Any,
) -> None:
    """Append one JSON event record. `event` is the SSE event name
    ("section_status" | "token_usage" | "run_status"); `fields` are folded
    into the record (e.g. section_id, status, tokens_used, cost_usd)."""
    path = Path(events_path) if events_path else events_log_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "event": event,
        **fields,
    }
    with _events_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")


def read_events(run_id: str, *, events_path: str | Path | None = None) -> list[dict[str, Any]]:
    path = Path(events_path) if events_path else events_log_path(run_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Section draft files on disk (never stored in RunState -- constraints #1/#6)
# ---------------------------------------------------------------------------


def section_draft_path(run_id: str, leaf_id: str) -> Path:
    d = decisions.run_dir(run_id) / "sections"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{leaf_id}.md"


# ---------------------------------------------------------------------------
# Constraint #2: hallucinated bibkeys are impossible by construction
# ---------------------------------------------------------------------------


def extract_citation_keys(text: str) -> list[str]:
    """All well-formed `@key` entries appearing in `[@...]` markers, in
    order of first appearance (may contain duplicates -- callers dedupe)."""
    keys: list[str] = []
    for marker in CITATION_MARKER_RE.finditer(text):
        for part in marker.group(1).split(";"):
            m = CITATION_KEY_RE.match(part.strip())
            if m:
                keys.append(m.group(1))
    return keys


def enforce_valid_bibkeys(text: str, allowed_keys: set[str]) -> tuple[str, list[str]]:
    """Strip any citation key not in `allowed_keys` from every `[@k1;@k2]`
    marker in `text`. A marker left with zero valid keys is removed
    entirely; a marker with a mix keeps only the valid entries. Malformed
    entries (not matching the citation-key grammar) are dropped silently --
    that is `formats/validator.py`'s concern, not this function's.

    Returns `(clean_text, stripped_keys)` -- `stripped_keys` is every
    hallucinated key that was removed, for logging. This is the ONLY place
    citation markers are written into a saved draft, so it is the single
    enforcement point for constraint #2 ("hallucinated keys must be
    impossible").
    """
    stripped: list[str] = []

    def _clean_marker(m: re.Match[str]) -> str:
        parts = [p.strip() for p in m.group(1).split(";")]
        kept: list[str] = []
        for part in parts:
            key_match = CITATION_KEY_RE.match(part)
            if not key_match:
                continue  # malformed entry -- drop silently
            key = key_match.group(1)
            if key in allowed_keys:
                kept.append(part)
            else:
                stripped.append(key)
        if not kept:
            return ""
        return "[" + ";".join(kept) + "]"

    clean = CITATION_MARKER_RE.sub(_clean_marker, text)
    return clean, stripped


# ---------------------------------------------------------------------------
# Query rewrite
# ---------------------------------------------------------------------------

_QUERY_REWRITE_SYSTEM = (
    "You rewrite an academic document section's brief into a short, focused "
    "search query for retrieving the most relevant passages from the "
    "student's own source material. Respond with the query text ONLY -- no "
    "explanation, no quotes, a single line."
)


def rewrite_query(
    brief: SectionBrief,
    previous_query: str | None,
    feedback: str | None,
    *,
    provider: Any,
    model_name: str,
) -> tuple[str, LLMResponse]:
    prompt = f"Section title: {brief.title}\nBrief: {brief.brief or brief.title}\n"
    if previous_query:
        prompt += f"Previous query {previous_query!r} returned poor results.\n"
    if feedback:
        prompt += f"Grader feedback: {feedback}\n"
    prompt += "Rewrite this into one focused retrieval query."
    response = provider.complete(
        [
            LLMMessage(role="system", content=_QUERY_REWRITE_SYSTEM),
            LLMMessage(role="user", content=prompt),
        ],
        model=model_name,
        max_tokens=QUERY_REWRITE_MAX_TOKENS,
        temperature=0.3,
        json_mode=False,
    )
    query = response.text.strip().splitlines()[0].strip() if response.text.strip() else brief.title
    return query[:300], response


# ---------------------------------------------------------------------------
# Filtered hybrid retrieval
# ---------------------------------------------------------------------------


def retrieve_chunks(
    project_id: str,
    query: str,
    source_tags: list[str],
    *,
    top_k: int = RETRIEVAL_TOP_K,
    qdrant_client_factory: Callable[[], Any] | None = None,
    hybrid_search_fn: Callable[..., list[SearchHit]] | None = None,
) -> list[SearchHit]:
    """Hybrid search filtered by the planner-assigned source tags.

    `hybrid_search`'s convenience filter only matches one `source_id` at a
    time, so multiple source tags are queried individually and the results
    merged (best score per chunk_id wins), then truncated to `top_k`. No
    source tags means an unfiltered search across the whole project.
    Gracefully degrades to no results if Qdrant is unavailable (same
    precedent as `planner.sample_retrieval`) -- never raises.

    `qdrant_client_factory`/`hybrid_search_fn` default to `None` and are
    resolved to the module-level `get_qdrant_client`/`hybrid_search` names
    HERE, at call time, rather than as ordinary default-argument values --
    default arguments are bound once at function-definition time, which
    would make `monkeypatch.setattr(drafter, "hybrid_search", ...)` silently
    not take effect for any caller that didn't pass an explicit override.
    """
    client_factory = qdrant_client_factory or get_qdrant_client
    search_fn = hybrid_search_fn or hybrid_search
    try:
        client = client_factory()
    except Exception as exc:  # noqa: BLE001 - store unavailable is expected/handled
        logger.warning("drafter: qdrant client unavailable (%s); retrieval empty", exc)
        return []

    tags: list[str | None] = list(source_tags) if source_tags else [None]
    best: dict[str, SearchHit] = {}
    for tag in tags:
        try:
            hits = search_fn(client, project_id, query, top_k=top_k, source_id=tag)
        except Exception as exc:  # noqa: BLE001 - e.g. collection doesn't exist yet
            logger.warning("drafter: hybrid_search failed (tag=%r): %s", tag, exc)
            continue
        for hit in hits:
            existing = best.get(hit.chunk_id)
            if existing is None or hit.score > existing.score:
                best[hit.chunk_id] = hit

    ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# LLM chunk relevance grading
# ---------------------------------------------------------------------------

_GRADE_SYSTEM = (
    "You grade retrieved passages for relevance to a section brief. Respond "
    'JSON ONLY: {"grades": [{"chunk_id": str, "relevant": bool}, ...]} -- one '
    "entry per given chunk_id."
)


def grade_chunks(
    brief: SectionBrief,
    hits: list[SearchHit],
    *,
    provider: Any,
    model_name: str,
) -> tuple[list[SearchHit], list[dict[str, Any]], LLMResponse | None]:
    """Returns `(relevant_hits, raw_grades, llm_response)`. `llm_response` is
    None (no LLM call made) when there is nothing to grade."""
    if not hits:
        return [], [], None

    payload = {
        "section_title": brief.title,
        "brief": brief.brief,
        "chunks": [{"chunk_id": h.chunk_id, "text": h.text[:600]} for h in hits],
    }
    response = provider.complete(
        [
            LLMMessage(role="system", content=_GRADE_SYSTEM),
            LLMMessage(role="user", content=json.dumps(payload)),
        ],
        model=model_name,
        max_tokens=GRADE_MAX_TOKENS,
        temperature=0.0,
        json_mode=True,
    )
    grades: list[dict[str, Any]] = []
    try:
        data = json.loads(response.text)
        raw = data.get("grades", []) if isinstance(data, dict) else []
        grades = [g for g in raw if isinstance(g, dict)]
    except json.JSONDecodeError:
        grades = []

    relevant_ids = {g.get("chunk_id") for g in grades if g.get("relevant")}
    relevant_hits = [h for h in hits if h.chunk_id in relevant_ids]
    return relevant_hits, grades, response


# ---------------------------------------------------------------------------
# Corrective retrieval loop: rewrite -> retrieve -> grade -> retry up to 2x
# ---------------------------------------------------------------------------


def run_corrective_retrieval(
    brief: SectionBrief,
    project_id: str,
    *,
    run_id: str,
    drafter_provider: Any,
    drafter_model: str,
    qdrant_client_factory: Callable[[], Any] | None = None,
    hybrid_search_fn: Callable[..., list[SearchHit]] | None = None,
    log_path: str | Path | None = None,
) -> tuple[list[SearchHit], list[LLMResponse]]:
    node_name = f"drafter:{brief.id}"
    responses: list[LLMResponse] = []
    query: str | None = None
    feedback: str | None = None
    accepted: list[SearchHit] = []

    for attempt in range(MAX_QUERY_REWRITES + 1):
        query, rewrite_resp = rewrite_query(
            brief, query, feedback, provider=drafter_provider, model_name=drafter_model
        )
        responses.append(rewrite_resp)
        log_decision(
            run_id, node_name, "query_rewrite", {"attempt": attempt, "query": query}, log_path=log_path
        )

        hits = retrieve_chunks(
            project_id,
            query,
            brief.source_tags,
            qdrant_client_factory=qdrant_client_factory,
            hybrid_search_fn=hybrid_search_fn,
        )
        relevant, grades, grade_resp = grade_chunks(
            brief, hits, provider=drafter_provider, model_name=drafter_model
        )
        if grade_resp is not None:
            responses.append(grade_resp)

        poor = (not hits) or (len(relevant) / len(hits) < GRADE_RELEVANT_RATIO_THRESHOLD)
        log_decision(
            run_id,
            node_name,
            "chunk_grade",
            {
                "attempt": attempt,
                "query": query,
                "num_hits": len(hits),
                "num_relevant": len(relevant),
                "poor": poor,
                "grades": grades,
            },
            log_path=log_path,
        )
        accepted = relevant or hits
        if not poor:
            break
        feedback = f"only {len(relevant)}/{len(hits)} retrieved passages were relevant; broaden or refocus the query."

    return accepted, responses


# ---------------------------------------------------------------------------
# Draft / critique / revise
# ---------------------------------------------------------------------------

_DRAFT_SYSTEM = """\
You are the section-drafting agent for an academic document. Draft ONE leaf \
section's prose given a brief, a target word count, a compact ledger of what \
has already been written elsewhere in the document (do not repeat or \
contradict it), and retrieved source passages with their valid citation keys.

Rules:
- Write academic prose in Markdown, aiming for the target word count.
- Cite using [@key] markers ONLY for keys explicitly listed as "valid
  citation keys" below. NEVER invent a key or cite one not in that list.
  Cite every non-trivial factual claim drawn from the source passages.
- Write only the section's own body prose (no restated heading).

Respond with JSON ONLY:
{"draft": "<markdown prose>",
 "summary": "<1-2 sentence summary of this section, for other sections' context>",
 "claims": ["<short bullet claim>", ...]}
("claims": 0-3 short bullet strings, the most important document-wide facts
asserted in this section.)
"""

_CRITIQUE_SYSTEM = (
    "You are a critical reviewer checking one drafted section against its "
    "brief before it is finalized. Respond JSON ONLY: "
    '{"verdict": "pass"|"revise", "feedback": "<specific, actionable feedback '
    'if revise, else empty string>"}'
)


def _valid_keys_for(hits: list[SearchHit]) -> set[str]:
    return {k for h in hits for k in h.bibkeys}


def _build_draft_payload(
    brief: SectionBrief,
    hits: list[SearchHit],
    document_state: DocumentState,
    valid_keys: set[str],
) -> str:
    chunks = [
        {"chunk_id": h.chunk_id, "valid_keys": h.bibkeys, "text": h.text[:1200]} for h in hits
    ]
    payload = {
        "section_title": brief.title,
        "brief": brief.brief,
        "target_words": brief.target_words,
        "valid_citation_keys": sorted(valid_keys),
        "document_state": document_state.render_compact(),
        "retrieved_passages": chunks,
    }
    return json.dumps(payload, indent=2)


def _parse_draft_json(text: str, fallback_summary: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("draft"), str):
            return {
                "draft": data["draft"],
                "summary": data.get("summary") if isinstance(data.get("summary"), str) else fallback_summary,
                "claims": [c for c in (data.get("claims") or []) if isinstance(c, str)],
            }
    except json.JSONDecodeError:
        pass
    # Best-effort fallback: treat the raw text as the draft itself.
    return {"draft": text, "summary": fallback_summary, "claims": []}


def draft_section(
    brief: SectionBrief,
    hits: list[SearchHit],
    document_state: DocumentState,
    *,
    provider: Any,
    model_name: str,
) -> tuple[dict[str, Any], LLMResponse, set[str]]:
    valid_keys = _valid_keys_for(hits)
    response = provider.complete(
        [
            LLMMessage(role="system", content=_DRAFT_SYSTEM),
            LLMMessage(role="user", content=_build_draft_payload(brief, hits, document_state, valid_keys)),
        ],
        model=model_name,
        max_tokens=DRAFT_MAX_TOKENS,
        temperature=0.4,
        json_mode=True,
    )
    parsed = _parse_draft_json(response.text, fallback_summary=brief.brief or brief.title)
    return parsed, response, valid_keys


def critique_section(
    brief: SectionBrief,
    draft_text: str,
    *,
    provider: Any,
    model_name: str,
) -> tuple[dict[str, Any], LLMResponse]:
    payload = {
        "section_title": brief.title,
        "brief": brief.brief,
        "target_words": brief.target_words,
        "draft": draft_text,
    }
    response = provider.complete(
        [
            LLMMessage(role="system", content=_CRITIQUE_SYSTEM),
            LLMMessage(role="user", content=json.dumps(payload)),
        ],
        model=model_name,
        max_tokens=CRITIQUE_MAX_TOKENS,
        temperature=0.0,
        json_mode=True,
    )
    try:
        verdict = json.loads(response.text)
        if not isinstance(verdict, dict) or "verdict" not in verdict:
            verdict = {"verdict": "pass", "feedback": ""}
    except json.JSONDecodeError:
        verdict = {"verdict": "pass", "feedback": ""}
    return verdict, response


def revise_section(
    brief: SectionBrief,
    hits: list[SearchHit],
    document_state: DocumentState,
    previous_draft: str,
    feedback: str,
    *,
    provider: Any,
    model_name: str,
) -> tuple[dict[str, Any], LLMResponse, set[str]]:
    valid_keys = _valid_keys_for(hits)
    prompt = (
        _build_draft_payload(brief, hits, document_state, valid_keys)
        + "\n\nPrevious draft:\n"
        + previous_draft
        + "\n\nCritique feedback to address:\n"
        + feedback
        + "\n\nRewrite the section addressing the feedback, respecting the same JSON response shape."
    )
    response = provider.complete(
        [
            LLMMessage(role="system", content=_DRAFT_SYSTEM),
            LLMMessage(role="user", content=prompt),
        ],
        model=model_name,
        max_tokens=DRAFT_MAX_TOKENS,
        temperature=0.4,
        json_mode=True,
    )
    parsed = _parse_draft_json(response.text, fallback_summary=brief.brief or brief.title)
    return parsed, response, valid_keys


# ---------------------------------------------------------------------------
# Per-leaf pipeline result + entrypoint
# ---------------------------------------------------------------------------


class LeafDraftResult(BaseModel):
    """Everything the fan-out node needs back from one leaf's pipeline --
    deliberately NEVER the prose itself (constraints #1/#6)."""

    leaf_id: str
    status: str  # "done" | "flagged"
    draft_path: str = ""
    citation_keys: list[str] = Field(default_factory=list)
    summary: str = ""
    claims: list[str] = Field(default_factory=list)
    figure_count: int = 0
    table_count: int = 0
    tokens_used: int = 0
    cost_usd: float = 0.0
    error: str | None = None


_TABLE_HEADER_RE = re.compile(r"^\|.*\|[ \t]*\n\|[ \t:|-]+\|[ \t]*$", re.MULTILINE)


def draft_leaf(
    brief: SectionBrief,
    document_state: DocumentState,
    *,
    run_id: str,
    project_id: str,
    log_path: str | Path | None = None,
    qdrant_client_factory: Callable[[], Any] | None = None,
    hybrid_search_fn: Callable[..., list[SearchHit]] | None = None,
    on_status: Callable[[str, str], None] | None = None,
) -> LeafDraftResult:
    """Run the full corrective-RAG pipeline for one leaf brief. Never raises
    -- any failure is caught, logged, and returned as a "flagged" result so
    one bad leaf can never sink the whole fan-out (see module docstring)."""
    node_name = f"drafter:{brief.id}"
    tokens_total = 0
    cost_total = 0.0

    def _track(resp: LLMResponse) -> None:
        nonlocal tokens_total, cost_total
        tokens_total += resp.usage.total_tokens
        cost_total += resp.cost_usd

    try:
        if on_status:
            on_status(brief.id, "drafting")
        drafter_provider, drafter_model = resolve_model("drafter")

        accepted_hits, retrieval_responses = run_corrective_retrieval(
            brief,
            project_id,
            run_id=run_id,
            drafter_provider=drafter_provider,
            drafter_model=drafter_model,
            qdrant_client_factory=qdrant_client_factory,
            hybrid_search_fn=hybrid_search_fn,
            log_path=log_path,
        )
        for resp in retrieval_responses:
            _track(resp)

        draft_json, draft_resp, valid_keys = draft_section(
            brief, accepted_hits, document_state, provider=drafter_provider, model_name=drafter_model
        )
        _track(draft_resp)
        clean_text, stripped = enforce_valid_bibkeys(draft_json["draft"], valid_keys)
        if stripped:
            log_decision(
                run_id,
                node_name,
                "bibkey_enforcement",
                {"stage": "draft", "stripped_keys": stripped},
                log_path=log_path,
            )
        summary = draft_json["summary"]
        claims = draft_json["claims"]

        if on_status:
            on_status(brief.id, "critiquing")
        critic_provider, critic_model = resolve_model("critic")
        verdict, critique_resp = critique_section(
            brief, clean_text, provider=critic_provider, model_name=critic_model
        )
        _track(critique_resp)
        log_decision(run_id, node_name, "critique", {"verdict": verdict}, log_path=log_path)

        if str(verdict.get("verdict", "pass")).lower() == "revise":
            revised_json, revise_resp, revise_keys = revise_section(
                brief,
                accepted_hits,
                document_state,
                clean_text,
                str(verdict.get("feedback") or ""),
                provider=drafter_provider,
                model_name=drafter_model,
            )
            _track(revise_resp)
            clean_text, stripped2 = enforce_valid_bibkeys(revised_json["draft"], revise_keys)
            if stripped2:
                log_decision(
                    run_id,
                    node_name,
                    "bibkey_enforcement",
                    {"stage": "revision", "stripped_keys": stripped2},
                    log_path=log_path,
                )
            summary = revised_json["summary"]
            claims = revised_json["claims"]

        used_keys = sorted(dict.fromkeys(extract_citation_keys(clean_text)))
        figure_count = clean_text.count("![")
        table_count = len(_TABLE_HEADER_RE.findall(clean_text))

        path = section_draft_path(run_id, brief.id)
        path.write_text(clean_text, encoding="utf-8")

        if on_status:
            on_status(brief.id, "done")

        return LeafDraftResult(
            leaf_id=brief.id,
            status="done",
            draft_path=str(path),
            citation_keys=used_keys,
            summary=summary,
            claims=claims[:3],
            figure_count=figure_count,
            table_count=table_count,
            tokens_used=tokens_total,
            cost_usd=cost_total,
        )
    except Exception as exc:  # noqa: BLE001 - a per-leaf failure must never sink the whole run
        logger.warning("drafter: leaf %r failed (%s)", brief.id, exc)
        log_decision(run_id, node_name, "leaf_pipeline_error", {"error": str(exc)}, log_path=log_path)
        if on_status:
            on_status(brief.id, "flagged")
        return LeafDraftResult(
            leaf_id=brief.id,
            status="flagged",
            tokens_used=tokens_total,
            cost_usd=cost_total,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# DocumentState update ("post-draft node", see module docstring)
# ---------------------------------------------------------------------------


def apply_document_state_update(state: DocumentState, result: LeafDraftResult) -> DocumentState:
    """Fold one completed leaf's result into the running DocumentState.
    A "flagged" (failed) leaf contributes nothing -- there is no summary/
    citations/claims to trust from a pipeline that never produced a draft."""
    if result.status != "done":
        return state

    section_summaries = dict(state.section_summaries)
    section_summaries[result.leaf_id] = result.summary

    citation_keys = list(state.citation_keys)
    for key in result.citation_keys:
        if key not in citation_keys:
            citation_keys.append(key)

    global_claims = list(state.global_claims) + list(result.claims)

    return state.model_copy(
        update={
            "section_summaries": section_summaries,
            "citation_keys": citation_keys,
            "figure_counter": state.figure_counter + result.figure_count,
            "table_counter": state.table_counter + result.table_count,
            "global_claims": global_claims,
        }
    )


class _DocumentStateBox:
    """Thread-safe box around one `DocumentState`, used by the fan-out node
    so concurrent leaves each see a consistent snapshot at the moment they
    START drafting, and updates are serialized as leaves complete."""

    def __init__(self, initial: DocumentState) -> None:
        self._lock = threading.Lock()
        self._state = initial

    def snapshot(self) -> DocumentState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def apply(self, result: LeafDraftResult) -> None:
        with self._lock:
            self._state = apply_document_state_update(self._state, result)

    def dump(self) -> dict[str, Any]:
        with self._lock:
            return self._state.model_dump()


# ---------------------------------------------------------------------------
# Fan-out node: bounded parallelism over all leaf briefs
# ---------------------------------------------------------------------------


def drafter_fanout_node(
    state: RunState,
    *,
    qdrant_client_factory: Callable[[], Any] | None = None,
    hybrid_search_fn: Callable[..., list[SearchHit]] | None = None,
) -> dict[str, Any]:
    """LangGraph node: `planner -> drafter -> END` (Phase 4; Phase 5 inserts
    verifier/continuity/compliance nodes after this one -- see
    build_graph.py). Drafts every leaf brief with bounded parallelism and
    returns the RunState update: `section_status`, `document_state`, and
    (for now, since nothing follows) the final `status`.

    `qdrant_client_factory`/`hybrid_search_fn` are forwarded to every leaf's
    `draft_leaf` call -- keyword-only injection points for tests (mirrors
    `planner.sample_retrieval`'s own testability pattern), never used in
    production (LangGraph calls this node with just `state`).
    """
    run_id = state["run_id"]
    project_id = state["project_id"]
    log_path = state.get("decisions_log_path")
    leaf_briefs = [SectionBrief.model_validate(b) for b in state.get("leaf_briefs", [])]
    run_config = state.get("run_config") or {}
    parallelism = max(1, int(run_config.get("drafter_parallelism") or DEFAULT_PARALLELISM))

    doc_box = _DocumentStateBox(DocumentState.model_validate(state.get("document_state") or {}))
    section_status: dict[str, dict[str, Any]] = dict(state.get("section_status") or {})

    for brief in leaf_briefs:
        section_status[brief.id] = SectionStatusEntry(status="queued").model_dump()
        emit_event(run_id, "section_status", section_id=brief.id, title=brief.title, status="queued")

    emit_event(run_id, "run_status", status="drafting")

    def on_status(leaf_id: str, status: str) -> None:
        emit_event(run_id, "section_status", section_id=leaf_id, status=status)

    def run_one(brief: SectionBrief) -> LeafDraftResult:
        snapshot = doc_box.snapshot()
        return draft_leaf(
            brief,
            snapshot,
            run_id=run_id,
            project_id=project_id,
            log_path=log_path,
            qdrant_client_factory=qdrant_client_factory,
            hybrid_search_fn=hybrid_search_fn,
            on_status=on_status,
        )

    cumulative_tokens = 0
    cumulative_cost = 0.0

    if leaf_briefs:
        with ThreadPoolExecutor(max_workers=min(parallelism, len(leaf_briefs))) as pool:
            future_to_brief = {pool.submit(run_one, brief): brief for brief in leaf_briefs}
            for future in as_completed(future_to_brief):
                brief = future_to_brief[future]
                result = future.result()  # draft_leaf never raises
                doc_box.apply(result)
                cumulative_tokens += result.tokens_used
                cumulative_cost += result.cost_usd
                section_status[brief.id] = SectionStatusEntry(
                    status=result.status,
                    tokens_used=result.tokens_used,
                    cost_usd=result.cost_usd,
                ).model_dump()
                emit_event(
                    run_id,
                    "token_usage",
                    section_id=brief.id,
                    tokens_used=result.tokens_used,
                    cost_usd=result.cost_usd,
                    cumulative_tokens=cumulative_tokens,
                    cumulative_cost_usd=cumulative_cost,
                )

    # Phase 5: the review node (citation verifier / compliance / continuity)
    # runs after this node and sets the terminal "completed" status itself, so
    # the drafter hands off with an intermediate "verifying" status.
    emit_event(run_id, "run_status", status="verifying")

    return {
        "status": "verifying",
        "document_state": doc_box.dump(),
        "section_status": section_status,
    }
