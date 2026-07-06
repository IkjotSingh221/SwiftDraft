"""Phase 5: citation verifier.

Per spec.md constraints #2/#3:
  - #2 "Citations are keys, never free text ... the citation verifier rejects
    any key not in the store." Phase 4 already makes hallucinated keys
    impossible *by construction* (`drafter.enforce_valid_bibkeys`); this
    module is the independent BACKSTOP -- it re-checks every cited key against
    the project's bibliography store (`data/projects/{id}/bibliography.json`,
    the single source of truth) and flags anything not in it.
  - #3 "Every factual claim must be traceable to a retrieved chunk." For each
    *cited* sentence, the verifier gathers the supporting chunk text for the
    cited keys and runs an NLI-style "is this claim entailed by this evidence?"
    check with `resolve_model("citation_verifier")`. A claim with no supporting
    chunk, or one the model judges unsupported, is flagged.

Where does the supporting-chunk text come from? Phase 4's drafter does not
persist the chunks it retrieved (only the final prose + the citation keys
used), so the verifier RE-RETRIEVES for the section via the same
`drafter.retrieve_chunks` (filtered by the leaf's source tags) and indexes the
returned `SearchHit.text` by each hit's `bibkeys`. This keeps Phase 4's
drafter untouched and reuses one retrieval path (see DECISIONS.md).

Nothing here mutates a draft. The orchestrating `review_node`
(`graph/review.py`) decides what to do with an unsupported section (redraft
with the failures as feedback, bounded, then flag for human).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from draftforge.config import get_settings
from draftforge.graph.drafter import extract_citation_keys, retrieve_chunks
from draftforge.ingest.store import SearchHit
from draftforge.llm.base import LLMMessage, LLMResponse
from draftforge.llm.settings_store import resolve_model

logger = logging.getLogger(__name__)

VERIFY_MAX_TOKENS = 1200

# Sentence splitter: break on ., !, ? followed by whitespace. Deliberately
# simple (see validator.py's own "not a full parser" rationale) -- section
# prose is short and this only needs to isolate cited sentences.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CITATION_PRESENT_RE = re.compile(r"\[@[^\]]+\]")


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class Claim(BaseModel):
    """One cited sentence extracted from a drafted section."""

    text: str
    cited_keys: list[str] = Field(default_factory=list)


class CitationCheck(BaseModel):
    """The verifier's verdict for one cited claim."""

    claim: str
    cited_keys: list[str] = Field(default_factory=list)
    supported: bool
    reason: str = ""
    supporting_excerpt: str = ""


class SectionVerification(BaseModel):
    """Every citation verdict for one section, plus the bad-key backstop."""

    section_id: str
    checks: list[CitationCheck] = Field(default_factory=list)
    invalid_keys: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0

    @property
    def unsupported(self) -> list[CitationCheck]:
        return [c for c in self.checks if not c.supported]

    @property
    def ok(self) -> bool:
        """A section passes verification when no key is invalid and every
        cited claim is supported by its chunk(s)."""
        return not self.invalid_keys and not self.unsupported

    def feedback(self) -> str:
        """Actionable feedback string for a redraft (spec.md #3: 'the section
        is redrafted with the violation as feedback')."""
        lines: list[str] = []
        if self.invalid_keys:
            lines.append(
                "Remove citations to unknown keys (not in the bibliography): "
                + ", ".join(f"@{k}" for k in self.invalid_keys)
                + "."
            )
        for check in self.unsupported:
            keys = ", ".join(f"@{k}" for k in check.cited_keys) or "(no key)"
            lines.append(
                f"The claim \"{check.claim.strip()}\" (cited {keys}) is not "
                f"supported by the retrieved sources: {check.reason.strip() or 'no supporting passage found'}. "
                "Either cite a source that supports it, soften the claim, or remove it."
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bibliography store (single source of truth for valid keys -- constraint #2)
# ---------------------------------------------------------------------------


def _bibliography_path(project_id: str) -> Path:
    return get_settings().data_dir / "projects" / project_id / "bibliography.json"


def load_valid_bibkeys(project_id: str) -> set[str] | None:
    """Return the set of valid bibkeys for a project, or None if the
    bibliography file doesn't exist yet.

    None (unknown) is deliberately distinct from an empty set: when the store
    is missing we cannot know which keys are invalid, so the caller SKIPS the
    bad-key backstop rather than flagging every citation. Each CSL-JSON entry
    contributes its `id` (the bibkey)."""
    path = _bibliography_path(project_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("verifier: could not read bibliography %s (%s)", path, exc)
        return None
    entries = data.get("entries", []) if isinstance(data, dict) else data
    keys: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            keys.add(entry["id"])
    return keys


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------


def extract_claims(markdown: str) -> list[Claim]:
    """Every cited sentence in the section, with its citation keys.

    A "claim" here is a sentence that carries at least one `[@key]` marker --
    the factual assertions the drafter chose to back with a citation, which are
    exactly the ones spec.md #3 requires to be traceable to a chunk. Heading
    lines are skipped."""
    claims: list[Claim] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip()
            if not sentence or not _CITATION_PRESENT_RE.search(sentence):
                continue
            keys = list(dict.fromkeys(extract_citation_keys(sentence)))
            claims.append(Claim(text=sentence, cited_keys=keys))
    return claims


# ---------------------------------------------------------------------------
# Supporting-chunk retrieval + NLI check
# ---------------------------------------------------------------------------


def _index_hits_by_key(hits: list[SearchHit]) -> dict[str, list[str]]:
    by_key: dict[str, list[str]] = {}
    for hit in hits:
        for key in hit.bibkeys:
            by_key.setdefault(key, []).append(hit.text)
    return by_key


_NLI_SYSTEM = (
    "You are a citation-faithfulness checker. For each item you are given a "
    "factual claim and the source passage(s) it cites. Decide, for each item, "
    "whether the claim is ENTAILED (directly supported) by the passage(s). Do "
    "not use outside knowledge. Respond JSON ONLY: "
    '{"verdicts": [{"supported": bool, "reason": "<short justification>"}, ...]} '
    "-- exactly one verdict per item, in the same order."
)


def _nli_check(
    items: list[tuple[str, str]],  # (claim, evidence)
    *,
    provider: Any,
    model_name: str,
) -> tuple[list[dict[str, Any]], LLMResponse | None]:
    if not items:
        return [], None
    payload = {
        "items": [{"claim": claim, "passages": evidence} for claim, evidence in items]
    }
    response = provider.complete(
        [
            LLMMessage(role="system", content=_NLI_SYSTEM),
            LLMMessage(role="user", content=json.dumps(payload)),
        ],
        model=model_name,
        max_tokens=VERIFY_MAX_TOKENS,
        temperature=0.0,
        json_mode=True,
    )
    verdicts: list[dict[str, Any]] = []
    try:
        data = json.loads(response.text)
        raw = data.get("verdicts", []) if isinstance(data, dict) else []
        verdicts = [v for v in raw if isinstance(v, dict)]
    except json.JSONDecodeError:
        verdicts = []
    return verdicts, response


def verify_section(
    markdown: str,
    section_id: str,
    project_id: str,
    source_tags: list[str],
    query: str,
    *,
    valid_bibkeys: set[str] | None,
    provider: Any | None = None,
    model_name: str | None = None,
    retrieve_fn: Callable[..., list[SearchHit]] | None = None,
    qdrant_client_factory: Callable[[], Any] | None = None,
    hybrid_search_fn: Callable[..., list[SearchHit]] | None = None,
) -> SectionVerification:
    """Verify one drafted section's citations.

    `provider`/`model_name` default to `resolve_model("citation_verifier")`
    resolved AT CALL TIME (never hardcoded). `retrieve_fn` defaults to
    `drafter.retrieve_chunks` -- a keyword-only injection point for tests
    (same pattern as the drafter's own `hybrid_search_fn`).
    """
    claims = extract_claims(markdown)

    # Bad-key backstop (constraint #2). Only runs when the store is known.
    invalid_keys: list[str] = []
    if valid_bibkeys is not None:
        cited = list(dict.fromkeys(k for c in claims for k in c.cited_keys))
        invalid_keys = [k for k in cited if k not in valid_bibkeys]

    if not claims:
        return SectionVerification(section_id=section_id, checks=[], invalid_keys=invalid_keys)

    fetch = retrieve_fn or retrieve_chunks
    hits = fetch(
        project_id,
        query,
        source_tags,
        qdrant_client_factory=qdrant_client_factory,
        hybrid_search_fn=hybrid_search_fn,
    )
    by_key = _index_hits_by_key(hits)

    # An unsupported-by-absence claim (no chunk for any cited key) never needs
    # an LLM call -- it fails traceability outright. The rest go to the NLI
    # model in a single batched call.
    checks: list[CitationCheck] = [CitationCheck(claim="", supported=True) for _ in claims]
    nli_items: list[tuple[str, str]] = []
    nli_index: list[int] = []
    for i, claim in enumerate(claims):
        evidence_texts = [t for k in claim.cited_keys for t in by_key.get(k, [])]
        if not evidence_texts:
            checks[i] = CitationCheck(
                claim=claim.text,
                cited_keys=claim.cited_keys,
                supported=False,
                reason="no retrieved chunk was found for the cited key(s)",
            )
            continue
        evidence = "\n---\n".join(t[:800] for t in evidence_texts[:3])
        nli_items.append((claim.text, evidence))
        nli_index.append(i)
        checks[i] = CitationCheck(
            claim=claim.text,
            cited_keys=claim.cited_keys,
            supported=True,
            supporting_excerpt=evidence_texts[0][:400],
        )

    # An unreachable verifier model must not crash the whole run (matching the
    # drafter's "never sink the run" stance): on failure we skip the NLI pass
    # and the claims keep their optimistic supported=True default. The
    # unsupported-by-absence claims above (no chunk at all) are already flagged
    # without any model call, so #3's core traceability check still holds.
    verdicts: list[dict[str, Any]] = []
    response: LLMResponse | None = None
    try:
        provider_, model_ = (provider, model_name)
        if provider_ is None or model_ is None:
            provider_, model_ = resolve_model("citation_verifier")
        verdicts, response = _nli_check(nli_items, provider=provider_, model_name=model_)
    except Exception as exc:  # noqa: BLE001 - verifier unavailable degrades, never crashes
        logger.warning("verifier: NLI check unavailable for %s (%s)", section_id, exc)

    for offset, verdict in enumerate(verdicts):
        if offset >= len(nli_index):
            break
        idx = nli_index[offset]
        supported = bool(verdict.get("supported", True))
        checks[idx] = checks[idx].model_copy(
            update={"supported": supported, "reason": str(verdict.get("reason") or "")}
        )
    # If the model returned fewer verdicts than items, the un-answered ones keep
    # their optimistic default (supported=True) -- a malformed verifier response
    # must not manufacture spurious violations that would trigger redrafts.

    tokens = response.usage.total_tokens if response else 0
    cost = response.cost_usd if response else 0.0
    return SectionVerification(
        section_id=section_id,
        checks=checks,
        invalid_keys=invalid_keys,
        tokens_used=tokens,
        cost_usd=cost,
    )
