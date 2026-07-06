"""FastAPI application entrypoint."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from draftforge.api import routes_projects, routes_runs, routes_settings
from draftforge.api.models import HealthResponse
from draftforge.config import get_settings

app = FastAPI(title="DraftForge API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All API routes are mounted under /api (see DECISIONS.md): this keeps the
# backend's URL space disjoint from the frontend's client-side routes (e.g.
# both a "/settings" page and a "/settings/..." endpoint would otherwise
# collide under the Vite dev proxy on a full page load).
app.include_router(routes_settings.router, prefix="/api")
app.include_router(routes_projects.router, prefix="/api")
app.include_router(routes_runs.router, prefix="/api")


def _reachable(url: str, timeout: float = 1.5) -> bool:
    try:
        resp = httpx.get(url, timeout=timeout)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()

    qdrant_ok = _reachable(f"{settings.qdrant_url.rstrip('/')}/healthz")
    grobid_ok = _reachable(f"{settings.grobid_url.rstrip('/')}/api/isalive")

    status = "ok" if (qdrant_ok and grobid_ok) else "degraded"
    return HealthResponse(
        status=status, qdrant_reachable=qdrant_ok, grobid_reachable=grobid_ok
    )
