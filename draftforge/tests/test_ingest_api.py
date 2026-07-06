"""API round-trip: create project -> upload a source -> poll status -> "done".

GROBID, the embedder, and Qdrant are all mocked/replaced at their
boundaries so this runs hermetically and fast:
- `grobid.parse_pdf` is monkeypatched to return a fixed `ParsedDocument`
  (TEI parsing itself has its own dedicated test).
- `embed_dense`/`embed_sparse` are monkeypatched to cheap deterministic fakes.
- the Qdrant client factory is monkeypatched to Qdrant's real in-process
  `:memory:` engine (no Docker needed).
"""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from draftforge.ingest.embedder import SparseVector
from draftforge.ingest.models import BibEntry, ParsedDocument, ParsedSection


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    from draftforge.api.app import app
    import draftforge.api.routes_projects as routes_projects
    import draftforge.ingest.grobid as grobid

    def fake_parse_pdf(pdf_bytes, *, grobid_url, filename="document.pdf", timeout=120.0):
        return ParsedDocument(
            sections=[
                ParsedSection(section_path=["Intro"], heading="Intro", text="hello world " * 60, page=1)
            ],
            bib_entries=[BibEntry(id="smith2020", title="A paper")],
        )

    def fake_embed_dense(texts, **kwargs):
        return [[1.0, 0.0] for _ in texts]

    def fake_embed_sparse(texts, **kwargs):
        return [SparseVector(indices=[1], values=[1.0]) for _ in texts]

    monkeypatch.setattr(grobid, "parse_pdf", fake_parse_pdf)
    monkeypatch.setattr(routes_projects, "embed_dense", fake_embed_dense)
    monkeypatch.setattr(routes_projects, "embed_sparse", fake_embed_sparse)
    monkeypatch.setattr(routes_projects, "_qdrant_client_factory", lambda: QdrantClient(":memory:"))

    return TestClient(app)


def test_create_project(client: TestClient):
    resp = client.post("/api/projects", json={"name": "My Report"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "My Report"
    assert body["status"] == "created"
    assert body["id"]


def test_get_unknown_project_404s(client: TestClient):
    resp = client.get("/api/projects/does-not-exist")
    assert resp.status_code == 404


def test_upload_and_ingest_round_trip_reaches_done(client: TestClient):
    create_resp = client.post("/api/projects", json={"name": "Thesis"})
    project_id = create_resp.json()["id"]

    files = {"file": ("paper.pdf", io.BytesIO(b"%PDF-1.4 fake pdf bytes"), "application/pdf")}
    upload_resp = client.post(f"/api/projects/{project_id}/sources", files=files)
    assert upload_resp.status_code == 200
    source = upload_resp.json()
    assert source["filename"] == "paper.pdf"
    assert source["status"] in {"queued", "parsing", "chunking", "embedding", "done"}

    # Background task runs synchronously within TestClient's request lifecycle,
    # but poll with a short retry loop to be robust regardless of timing.
    deadline = time.monotonic() + 5
    status = None
    while time.monotonic() < deadline:
        list_resp = client.get(f"/api/projects/{project_id}/sources")
        assert list_resp.status_code == 200
        sources = list_resp.json()
        assert len(sources) == 1
        status = sources[0]["status"]
        if status in {"done", "error"}:
            break
        time.sleep(0.05)

    assert status == "done", f"expected 'done', got {status!r}"
    assert sources[0]["error"] is None


def test_upload_to_unknown_project_404s(client: TestClient):
    files = {"file": ("paper.pdf", io.BytesIO(b"fake"), "application/pdf")}
    resp = client.post("/api/projects/nonexistent/sources", files=files)
    assert resp.status_code == 404


def test_ingestion_failure_surfaces_as_error_status(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import draftforge.api.routes_projects as routes_projects
    import draftforge.ingest.grobid as grobid

    def boom(*args, **kwargs):
        raise grobid.GrobidError("simulated failure")

    def fake_docling_parse_document(path):
        raise RuntimeError("docling also fails in this test")

    monkeypatch.setattr(grobid, "parse_pdf", boom)
    monkeypatch.setattr(routes_projects.docling_fallback, "parse_document", fake_docling_parse_document)

    create_resp = client.post("/api/projects", json={"name": "Failing"})
    project_id = create_resp.json()["id"]

    files = {"file": ("paper.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")}
    client.post(f"/api/projects/{project_id}/sources", files=files)

    deadline = time.monotonic() + 5
    status = None
    error = None
    while time.monotonic() < deadline:
        sources = client.get(f"/api/projects/{project_id}/sources").json()
        status = sources[0]["status"]
        error = sources[0]["error"]
        if status in {"done", "error"}:
            break
        time.sleep(0.05)

    assert status == "error"
    assert "docling also fails" in error
