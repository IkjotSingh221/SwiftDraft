"""`/health` returns 200 with the expected shape, even when Qdrant/GROBID are down."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from draftforge.api.app import app


def test_health_ok_when_both_reachable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("draftforge.api.app._reachable", lambda url, timeout=1.5: True)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "qdrant_reachable": True, "grobid_reachable": True}


def test_health_degraded_when_both_down(monkeypatch: pytest.MonkeyPatch):
    def fake_reachable(url, timeout=1.5):
        raise httpx.HTTPError("boom")

    # _reachable itself swallows httpx errors and returns False; simulate that
    # behavior directly instead of monkeypatching the internals of httpx.get.
    monkeypatch.setattr("draftforge.api.app._reachable", lambda url, timeout=1.5: False)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["qdrant_reachable"] is False
    assert body["grobid_reachable"] is False


def test_health_never_crashes_without_mocking(monkeypatch: pytest.MonkeyPatch):
    """With no docker services running at all, /health must still return 200."""
    monkeypatch.setenv("QDRANT_URL", "http://localhost:1")
    monkeypatch.setenv("GROBID_URL", "http://localhost:1")
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
