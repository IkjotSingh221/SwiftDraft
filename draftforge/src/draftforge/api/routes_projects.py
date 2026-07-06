"""Project routes.

Phase 0: routes are registered (paths reserved) with stub 501 responses so
later phases only need to fill in bodies here — no route wiring changes.
TODO(Phase 1): implement ingestion (GROBID/Docling -> chunk -> embed -> Qdrant).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile

from draftforge.api.models import Project, ProjectCreate, SourceStatus

router = APIRouter(prefix="/projects", tags=["projects"])

_NOT_IMPLEMENTED = "TODO(Phase 1): not implemented yet"


@router.post("", response_model=Project, status_code=501)
def create_project(payload: ProjectCreate) -> Project:
    # TODO(Phase 1): persist project, return created record.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("", response_model=list[Project])
def list_projects() -> list[Project]:
    # TODO(Phase 1): list persisted projects.
    return []


@router.get("/{project_id}", response_model=Project, status_code=501)
def get_project(project_id: str) -> Project:
    # TODO(Phase 1): fetch a single project.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.post("/{project_id}/sources", response_model=SourceStatus, status_code=501)
async def upload_source(project_id: str, file: UploadFile) -> SourceStatus:
    # TODO(Phase 1): multipart upload -> background ingestion task.
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/{project_id}/sources", response_model=list[SourceStatus])
def list_sources(project_id: str) -> list[SourceStatus]:
    # TODO(Phase 1): poll per-file ingestion status.
    return []
