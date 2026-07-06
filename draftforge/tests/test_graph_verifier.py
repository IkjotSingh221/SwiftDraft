"""Phase 5 citation verifier (hermetic): claim extraction, the NLI support
check (supported passes / unsupported flags), the constraint-#2 bad-key
backstop, the #3 no-chunk-is-unsupported rule, and graceful degradation when
the verifier model is unavailable. No network anywhere -- the NLI provider and
retrieval are always injected."""

from __future__ import annotations

import json

import pytest

from draftforge.graph.verifier import (
    extract_claims,
    load_valid_bibkeys,
    verify_section,
)
from draftforge.ingest.store import SearchHit
from draftforge.llm.base import LLMResponse, TokenUsage


def _hit(chunk_id: str, bibkeys: list[str], text: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id, source_id="s1", section_path=["x"], page=1,
        text=text, bibkeys=bibkeys, score=0.9,
    )


class NLIProvider:
    provider_name = "fake"

    def __init__(self, verdicts: list[dict] | None = None, raises: bool = False):
        self.verdicts = verdicts if verdicts is not None else []
        self.raises = raises
        self.calls = 0

    def complete(self, messages, *, model, max_tokens, temperature, json_mode):
        self.calls += 1
        if self.raises:
            raise RuntimeError("verifier model unreachable")
        return LLMResponse(
            text=json.dumps({"verdicts": self.verdicts}),
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model=model, provider=self.provider_name, cost_usd=0.002,
        )


def _verify(markdown, hits, provider, *, valid_bibkeys=None):
    return verify_section(
        markdown, "intro", "proj1", ["s1"], "query text",
        valid_bibkeys=valid_bibkeys,
        provider=provider, model_name="fake-model",
        retrieve_fn=lambda *a, **k: hits,
    )


def test_extract_claims_only_returns_cited_sentences():
    md = "This sentence has no citation. But this one does [@doe2020]. Another plain one."
    claims = extract_claims(md)
    assert len(claims) == 1
    assert claims[0].cited_keys == ["doe2020"]
    assert "this one does" in claims[0].text


def test_supported_claim_passes():
    provider = NLIProvider(verdicts=[{"supported": True, "reason": "entailed"}])
    ver = _verify(
        "The model reaches 90% accuracy [@doe2020].",
        [_hit("c1", ["doe2020"], "Our model attains 90% accuracy on the benchmark.")],
        provider,
        valid_bibkeys={"doe2020"},
    )
    assert provider.calls == 1
    assert ver.ok
    assert ver.unsupported == []
    assert ver.invalid_keys == []
    assert ver.tokens_used == 15


def test_unsupported_claim_is_flagged_with_feedback():
    provider = NLIProvider(verdicts=[{"supported": False, "reason": "not entailed by passage"}])
    ver = _verify(
        "The method cures cancer [@doe2020].",
        [_hit("c1", ["doe2020"], "The method improves image contrast.")],
        provider,
        valid_bibkeys={"doe2020"},
    )
    assert not ver.ok
    assert len(ver.unsupported) == 1
    fb = ver.feedback()
    assert "not supported" in fb
    assert "cures cancer" in fb


def test_bad_key_backstop_rejects_key_not_in_store():
    """Constraint #2 backstop: a key not in the bibliography store is flagged
    even though Phase 4 makes it impossible by construction."""
    provider = NLIProvider(verdicts=[])
    ver = _verify(
        "A claim citing a ghost key [@ghost].",
        [],  # no chunks -> also unsupported-by-absence, but we assert the key check
        provider,
        valid_bibkeys={"real1", "real2"},
    )
    assert ver.invalid_keys == ["ghost"]
    assert not ver.ok


def test_claim_with_no_supporting_chunk_is_unsupported_without_an_llm_call():
    """Constraint #3: a cited claim whose key has no retrieved chunk fails
    traceability outright -- no NLI call is even made for it."""
    provider = NLIProvider(verdicts=[])
    ver = _verify(
        "An orphan claim [@doe2020].",
        [_hit("c1", ["other_key"], "Unrelated passage.")],  # no chunk carries doe2020
        provider,
        valid_bibkeys={"doe2020"},
    )
    assert provider.calls == 0  # nothing had evidence -> no NLI call
    assert len(ver.unsupported) == 1
    assert "no retrieved chunk" in ver.unsupported[0].reason


def test_section_with_no_citations_is_ok_and_makes_no_llm_call():
    provider = NLIProvider(verdicts=[])
    ver = _verify("Plain prose with no citations at all.", [], provider)
    assert provider.calls == 0
    assert ver.ok


def test_verifier_degrades_gracefully_when_model_raises():
    """An unreachable verifier model must not crash the run: claims that had
    evidence keep their optimistic default rather than raising."""
    provider = NLIProvider(raises=True)
    ver = _verify(
        "A cited claim [@doe2020].",
        [_hit("c1", ["doe2020"], "Supporting passage text.")],
        provider,
        valid_bibkeys={"doe2020"},
    )
    # No exception; the evidence-backed claim stays supported (not fabricated
    # as a violation), so the run proceeds.
    assert ver.ok


def test_load_valid_bibkeys_reads_store_or_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # Missing store -> None (unknown), so the bad-key check is skipped.
    assert load_valid_bibkeys("projX") is None

    bib_dir = tmp_path / "data" / "projects" / "projX"
    bib_dir.mkdir(parents=True)
    (bib_dir / "bibliography.json").write_text(
        json.dumps([{"id": "doe2020", "title": "A"}, {"id": "smith2019", "title": "B"}])
    )
    keys = load_valid_bibkeys("projX")
    assert keys == {"doe2020", "smith2019"}
