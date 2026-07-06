"""Phase 6 API contract (hermetic): POST /render, GET /artifacts, and
GET /artifacts/{name}, all through the FastAPI TestClient. Every LLM role is
mocked (same pattern as test_redraft_api.py) so a full run completes without
network, and the Pandoc subprocess itself is mocked (`shutil.which` +
`render.pandoc.run_pandoc`) so this suite never shells out to a real
`pandoc` binary."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from draftforge.config import get_settings
from draftforge.llm.base import LLMResponse, TokenUsage

WELL_FORMED_OUTLINE = json.dumps(
    [
        {"id": "introduction", "title": "Introduction", "brief": "Motivate.", "target_words": 600, "children": []},
        {"id": "conclusion", "title": "Conclusion", "target_words": 300, "children": []},
        {"id": "references", "title": "References", "target_words": 0, "children": []},
    ]
)


class _FakeProvider:
    provider_name = "fake"

    def complete(self, messages, *, model, max_tokens, temperature, json_mode):
        return LLMResponse(
            text=WELL_FORMED_OUTLINE,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            model=model, provider=self.provider_name, cost_usd=0.0,
        )


def _fake_run_pandoc(args, *, cwd=None, timeout=120):
    """Stands in for a real `pandoc` invocation: writes a small placeholder
    file at the `-o` target and reports success, so `render_run`'s own
    orchestration logic (assemble -> write -> copy bib -> docx -> pdf) is
    exercised for real, without ever shelling out."""
    out_path = Path(args[args.index("-o") + 1])
    out_path.write_bytes(b"PK\x03\x04FAKE-OFFICE-DOCUMENT-BYTES")
    return subprocess.CompletedProcess(args=["pandoc", *args], returncode=0, stdout="", stderr="")


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import draftforge.graph.planner as planner_mod
    import draftforge.graph.drafter as drafter_mod
    import draftforge.graph.verifier as verifier_mod
    import draftforge.graph.continuity as continuity_mod
    import draftforge.render.pandoc as pandoc_mod
    from draftforge.api.app import app

    fake = lambda role: (_FakeProvider(), "fake-model")  # noqa: E731
    monkeypatch.setattr(planner_mod, "sample_retrieval", lambda *a, **k: {})
    monkeypatch.setattr(planner_mod, "resolve_model", fake)
    monkeypatch.setattr(drafter_mod, "resolve_model", fake)
    monkeypatch.setattr(drafter_mod, "get_qdrant_client", lambda: object())
    monkeypatch.setattr(drafter_mod, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(verifier_mod, "resolve_model", fake)
    monkeypatch.setattr(continuity_mod, "resolve_model", fake)

    # Pandoc + a LaTeX engine both "installed" as far as pandoc.py can tell,
    # and every actual subprocess call is faked -- see _fake_run_pandoc.
    monkeypatch.setattr(pandoc_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pandoc_mod, "run_pandoc", _fake_run_pandoc)

    # A minimal but real CSL-JSON bibliography for project "proj1" (matches
    # routes_projects.py's on-disk shape: a bare JSON array of entries).
    bib_path = get_settings().data_dir / "projects" / "proj1" / "bibliography.json"
    bib_path.parent.mkdir(parents=True, exist_ok=True)
    bib_path.write_text(json.dumps([{"id": "smith2020", "type": "article", "title": "A paper"}]), encoding="utf-8")

    return TestClient(app)


def _reach(client: TestClient, run_id: str, targets: set[str], timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{run_id}").json()["status"]
        if status in targets:
            return status
        time.sleep(0.05)
    return status


def _start_run(client: TestClient) -> str:
    resp = client.post(
        "/api/runs", json={"project_id": "proj1", "format_spec_id": "ieee_report", "run_config": {}}
    )
    return resp.json()["id"]


def _completed_run(client: TestClient) -> str:
    run_id = _start_run(client)
    assert _reach(client, run_id, {"awaiting_outline_approval", "error"}) == "awaiting_outline_approval"
    approve = client.post(f"/api/runs/{run_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "completed"
    return run_id


# ---------------------------------------------------------------------------
# Unknown run / not-completed guards
# ---------------------------------------------------------------------------


def test_unknown_run_404s_on_every_render_endpoint(client: TestClient):
    assert client.get("/api/runs/nope/artifacts").status_code == 404
    assert client.get("/api/runs/nope/artifacts/draft.docx").status_code == 404
    assert client.post("/api/runs/nope/render").status_code == 404


def test_artifacts_before_completion_is_409(client: TestClient):
    run_id = _start_run(client)
    _reach(client, run_id, {"awaiting_outline_approval", "error"})
    assert client.get(f"/api/runs/{run_id}/artifacts").status_code == 409
    assert client.get(f"/api/runs/{run_id}/artifacts/draft.docx").status_code == 409


# ---------------------------------------------------------------------------
# Path traversal / unknown artifact name -- whitelist rejection
# ---------------------------------------------------------------------------


def test_unknown_artifact_name_404s(client: TestClient):
    run_id = _completed_run(client)
    resp = client.get(f"/api/runs/{run_id}/artifacts/secret.txt")
    assert resp.status_code == 404


def test_path_traversal_name_is_rejected(client: TestClient):
    run_id = _completed_run(client)
    # A bare ".." is normalized away by the HTTP client itself before the
    # request is even sent (it collapses to the parent /artifacts listing
    # URL, never reaching the server as a literal name) -- covered by
    # test_unknown_artifact_name_404s's whitelist-lookup logic instead. Here
    # we exercise names that DO survive to the server as a literal `name`
    # path parameter: `get_artifact` looks `name` up only in the
    # `ARTIFACT_SPECS` whitelist dict, never uses it to build a filesystem
    # path, so any non-whitelisted string 404s regardless of its shape.
    for hostile in ("etc-passwd", "..%2F..%2Fetc%2Fpasswd", "....%2f....%2fetc%2fpasswd"):
        resp = client.get(f"/api/runs/{run_id}/artifacts/{hostile}")
        assert resp.status_code == 404, hostile
        assert "root:" not in resp.text


# ---------------------------------------------------------------------------
# Full round trip: render -> list -> download
# ---------------------------------------------------------------------------


def test_full_run_render_list_and_download_round_trip(client: TestClient):
    run_id = _completed_run(client)

    render_resp = client.post(f"/api/runs/{run_id}/render")
    assert render_resp.status_code == 200
    body = render_resp.json()
    assert "draft.docx" in body["generated"]
    assert "draft.pdf" in body["generated"]
    assert "bibliography.json" in body["generated"]

    listing = client.get(f"/api/runs/{run_id}/artifacts")
    assert listing.status_code == 200
    by_name = {a["name"]: a for a in listing.json()}
    assert set(by_name) == {"draft.docx", "draft.pdf", "bibliography.json", "decisions.jsonl", "eval_report.md"}
    assert by_name["draft.docx"]["available"] is True
    assert by_name["draft.docx"]["size_bytes"] > 0
    assert by_name["draft.docx"]["content_type"].startswith("application/vnd.openxmlformats")
    assert by_name["draft.pdf"]["available"] is True
    assert by_name["bibliography.json"]["available"] is True
    assert by_name["decisions.jsonl"]["available"] is True  # written during drafting, not render-dependent
    assert by_name["eval_report.md"]["available"] is False
    assert by_name["eval_report.md"]["note"] == "coming in Phase 7"

    docx = client.get(f"/api/runs/{run_id}/artifacts/draft.docx")
    assert docx.status_code == 200
    assert docx.content == b"PK\x03\x04FAKE-OFFICE-DOCUMENT-BYTES"
    assert docx.headers["content-type"].startswith("application/vnd.openxmlformats")

    bib = client.get(f"/api/runs/{run_id}/artifacts/bibliography.json")
    assert bib.status_code == 200
    assert json.loads(bib.content)[0]["id"] == "smith2020"

    decisions = client.get(f"/api/runs/{run_id}/artifacts/decisions.jsonl")
    assert decisions.status_code == 200
    assert decisions.content  # at least one JSONL line was logged during the run

    missing_eval = client.get(f"/api/runs/{run_id}/artifacts/eval_report.md")
    assert missing_eval.status_code == 404


def test_get_artifacts_lazily_renders_on_first_call_without_an_explicit_post(client: TestClient):
    """No explicit POST /render this time -- GET /artifacts should trigger a
    best-effort render itself since the run is completed and nothing has
    been rendered yet."""
    run_id = _completed_run(client)

    listing = client.get(f"/api/runs/{run_id}/artifacts").json()
    by_name = {a["name"]: a for a in listing}
    assert by_name["draft.docx"]["available"] is True

    docx = client.get(f"/api/runs/{run_id}/artifacts/draft.docx")
    assert docx.status_code == 200


def test_render_is_idempotent_and_does_not_re_shell_out_via_get(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Once artifacts exist, a subsequent GET /artifacts must not trigger
    another pandoc invocation (the lazy trigger only fires when the
    artifacts directory is empty)."""
    run_id = _completed_run(client)
    client.post(f"/api/runs/{run_id}/render")

    import draftforge.render.pandoc as pandoc_mod

    calls = {"n": 0}
    original = pandoc_mod.run_pandoc

    def _counting(*a, **k):
        calls["n"] += 1
        return original(*a, **k)

    monkeypatch.setattr(pandoc_mod, "run_pandoc", _counting)
    client.get(f"/api/runs/{run_id}/artifacts")
    client.get(f"/api/runs/{run_id}/artifacts/draft.docx")
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# Pandoc-missing graceful degradation (typed RenderError -> 502; other
# artifacts remain listed/downloadable)
# ---------------------------------------------------------------------------


def test_render_without_pandoc_is_a_502_but_bibliography_still_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    import draftforge.render.pandoc as pandoc_mod

    monkeypatch.setattr(pandoc_mod.shutil, "which", lambda name: None)  # nothing is installed

    run_id = _completed_run(client)
    render_resp = client.post(f"/api/runs/{run_id}/render")
    assert render_resp.status_code == 502

    listing = {a["name"]: a for a in client.get(f"/api/runs/{run_id}/artifacts").json()}
    assert listing["bibliography.json"]["available"] is True
    assert listing["draft.docx"]["available"] is False
    assert listing["draft.pdf"]["available"] is False


# ---------------------------------------------------------------------------
# OPTIONAL live test: a real `pandoc` binary actually renders a tiny doc.
# Skips cleanly on any machine (like this sandbox) without pandoc installed;
# proves the real subprocess path on a machine that has it.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed on this machine")
def test_live_pandoc_renders_a_real_minimal_docx(tmp_path):
    from draftforge.render import pandoc as pandoc_mod

    md_path = tmp_path / "doc.md"
    md_path.write_text("# Title\n\nHello world with a citation [@smith2020].\n", encoding="utf-8")
    out_path = tmp_path / "draft.docx"

    result = pandoc_mod.run_pandoc([str(md_path), "-o", str(out_path)], timeout=30)

    assert result.returncode == 0, result.stderr
    data = out_path.read_bytes()
    assert len(data) > 0
    assert data[:2] == b"PK"  # a .docx file is a zip archive (local file header magic)
