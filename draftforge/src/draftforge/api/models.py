"""Pydantic DTOs shared across the API.

Phase 0 defines the full set now — including placeholders for later phases —
so route modules can import stable names without editing this file again.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Settings / model registry (Phase 0 — fully functional)
# ---------------------------------------------------------------------------


class ModelInfo(BaseModel):
    """API-facing view of a registry entry, enriched with runtime status."""

    name: str
    provider: str
    model: str
    local: bool = False
    cost_per_mtok_in: float = 0.0
    cost_per_mtok_out: float = 0.0
    note: str | None = None
    api_key_present: bool = True


class RoleModelsResponse(BaseModel):
    models: list[ModelInfo]
    role_models: dict[str, str]
    ollama_reachable: bool


class RoleModelsUpdate(BaseModel):
    role_models: dict[str, str]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    qdrant_reachable: bool
    grobid_reachable: bool


# ---------------------------------------------------------------------------
# Placeholder DTOs for later phases — present now so imports never break.
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    """TODO(Phase 1): fields for creating a project (name, format spec id, ...)."""

    name: str
    format_spec: str | None = None


class Project(BaseModel):
    """TODO(Phase 1): full project record."""

    id: str
    name: str
    format_spec: str | None = None
    status: str = "created"


class SourceStatus(BaseModel):
    """TODO(Phase 1): per-uploaded-source ingestion status."""

    source_id: str
    filename: str
    status: Literal["queued", "parsing", "chunking", "embedding", "done", "error"] = (
        "queued"
    )
    error: str | None = None


class RunCreate(BaseModel):
    """Fields for starting a run: project + format spec + free-form run config
    (e.g. topic/notes/retrieval sample size) passed through to the planner."""

    project_id: str
    format_spec_id: str
    run_config: dict[str, Any] = Field(default_factory=dict)


class Run(BaseModel):
    """A run's coarse status, as tracked by the run registry (see
    `graph/build_graph.py`'s DECISIONS.md entry) — not the full checkpointed
    graph state (that's `GET /runs/{id}/outline` etc.)."""

    id: str
    project_id: str
    format_spec_id: str
    status: str = "queued"
    error: str | None = None


class OutlineNode(BaseModel):
    """A single node in the planner's outline tree. Field-for-field mirror of
    `graph.state.OutlineTreeNode` (see that module's docstring for why they're
    kept as two separate types)."""

    id: str
    title: str
    brief: str | None = None
    target_words: int | None = None
    source_tags: list[str] = []
    children: list["OutlineNode"] = []


OutlineNode.model_rebuild()


class SectionStatus(BaseModel):
    """TODO(Phase 4/5): live status of a single leaf section during a run."""

    section_id: str
    title: str
    status: Literal[
        "queued", "drafting", "critiquing", "verifying", "done", "flagged"
    ] = "queued"
    tokens_used: int = 0
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Rendering / artifacts (Phase 6)
# ---------------------------------------------------------------------------


class Artifact(BaseModel):
    """One entry in `GET /runs/{id}/artifacts`'s whitelist-driven listing
    (see `render/pandoc.py::ARTIFACT_SPECS`) -- `available=False` entries
    (not yet rendered, or the Phase 7 eval report slot) are still listed so
    the Downloads screen can show a disabled/greyed-out row with `note`
    instead of the item simply not existing."""

    name: str
    available: bool
    size_bytes: int | None = None
    content_type: str
    note: str | None = None


class RenderResponse(BaseModel):
    """Result of `POST /runs/{id}/render`: which artifacts were (re)generated
    vs. skipped this call, with a short human-readable reason per skip (e.g.
    "pdf-engine 'xelatex' is not installed")."""

    generated: list[str] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)
