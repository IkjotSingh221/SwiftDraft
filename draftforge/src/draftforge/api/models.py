"""Pydantic DTOs shared across the API.

Phase 0 defines the full set now — including placeholders for later phases —
so route modules can import stable names without editing this file again.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

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
    """TODO(Phase 3): fields for starting a run (project id, options, ...)."""

    project_id: str


class Run(BaseModel):
    """TODO(Phase 3): full run record."""

    id: str
    project_id: str
    status: str = "created"


class OutlineNode(BaseModel):
    """TODO(Phase 3): a single node in the planner's outline tree."""

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
