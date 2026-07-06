"""Project routes: create/list/get projects, upload sources, ingest them.

Project + source metadata is persisted as JSON under the data dir (see
DECISIONS.md for why JSON over SQLite here). Ingestion
(parse -> chunk -> embed -> upsert) runs as a FastAPI `BackgroundTask` per
uploaded file, updating that source's status as it progresses through
`queued -> parsing -> chunking -> embedding -> done` (or `error`).
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from qdrant_client import QdrantClient

from draftforge.api.models import Project, ProjectCreate, SourceStatus
from draftforge.config import get_settings
from draftforge.ingest import chunker, docling_fallback, grobid, store
from draftforge.ingest.embedder import embed_dense, embed_sparse
from draftforge.ingest.models import BibEntry

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger("draftforge.ingest")

_lock = threading.Lock()

_PDF_SUFFIXES = {".pdf"}


# ---------------------------------------------------------------------------
# JSON-backed project/source metadata store
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    settings = get_settings()
    settings.ensure_data_dir()
    return settings.data_dir / "projects.json"


def _load(db_path: Path | None = None) -> dict:
    path = db_path or _db_path()
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict, db_path: Path | None = None) -> None:
    path = db_path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def _project_dir(project_id: str) -> Path:
    settings = get_settings()
    d = settings.data_dir / "projects" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bibliography_path(project_id: str) -> Path:
    return _project_dir(project_id) / "bibliography.json"


# ---------------------------------------------------------------------------
# Qdrant client factory (overridden in tests to point at an in-memory client)
# ---------------------------------------------------------------------------


def _qdrant_client_factory() -> QdrantClient:
    return store.get_qdrant_client()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=Project)
def create_project(payload: ProjectCreate) -> Project:
    with _lock:
        data = _load()
        project_id = uuid.uuid4().hex
        record = {
            "id": project_id,
            "name": payload.name,
            "format_spec": payload.format_spec,
            "status": "created",
            "sources": {},
        }
        data[project_id] = record
        _save(data)
    return Project(id=project_id, name=payload.name, format_spec=payload.format_spec, status="created")


@router.get("", response_model=list[Project])
def list_projects() -> list[Project]:
    data = _load()
    return [
        Project(id=p["id"], name=p["name"], format_spec=p.get("format_spec"), status=p.get("status", "created"))
        for p in data.values()
    ]


@router.get("/{project_id}", response_model=Project)
def get_project(project_id: str) -> Project:
    data = _load()
    record = data.get(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown project_id: {project_id!r}")
    return Project(
        id=record["id"],
        name=record["name"],
        format_spec=record.get("format_spec"),
        status=record.get("status", "created"),
    )


@router.post("/{project_id}/sources", response_model=SourceStatus)
async def upload_source(project_id: str, file: UploadFile, background_tasks: BackgroundTasks) -> SourceStatus:
    data = _load()
    if project_id not in data:
        raise HTTPException(status_code=404, detail=f"Unknown project_id: {project_id!r}")

    source_id = uuid.uuid4().hex
    filename = file.filename or f"{source_id}.bin"
    contents = await file.read()

    sources_dir = _project_dir(project_id) / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    dest = sources_dir / f"{source_id}_{filename}"
    dest.write_bytes(contents)

    with _lock:
        data = _load()
        data[project_id]["sources"][source_id] = {
            "source_id": source_id,
            "filename": filename,
            "status": "queued",
            "error": None,
        }
        _save(data)

    background_tasks.add_task(_run_ingestion, project_id, source_id, dest, filename)

    return SourceStatus(source_id=source_id, filename=filename, status="queued")


@router.get("/{project_id}/sources", response_model=list[SourceStatus])
def list_sources(project_id: str) -> list[SourceStatus]:
    data = _load()
    record = data.get(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown project_id: {project_id!r}")
    return [
        SourceStatus(
            source_id=s["source_id"],
            filename=s["filename"],
            status=s["status"],
            error=s.get("error"),
        )
        for s in record["sources"].values()
    ]


# ---------------------------------------------------------------------------
# Ingestion pipeline: parse (GROBID -> Docling fallback) -> chunk -> embed -> upsert
# ---------------------------------------------------------------------------


def _set_source_status(project_id: str, source_id: str, status: str, error: str | None = None) -> None:
    with _lock:
        data = _load()
        source = data.get(project_id, {}).get("sources", {}).get(source_id)
        if source is None:
            return
        source["status"] = status
        source["error"] = error
        _save(data)


def _merge_bibliography(project_id: str, new_entries: list[BibEntry]) -> None:
    path = _bibliography_path(project_id)
    existing: dict = {}
    if path.exists():
        existing = {e["id"]: e for e in json.loads(path.read_text(encoding="utf-8"))}
    for entry in new_entries:
        csl = entry.to_csl_json()
        if csl["id"] in existing:
            logger.info("bibliography: bibkey %r already present, keeping first seen", csl["id"])
            continue
        existing[csl["id"]] = csl
    path.write_text(json.dumps(list(existing.values()), indent=2), encoding="utf-8")


def _run_ingestion(project_id: str, source_id: str, path: Path, filename: str) -> None:
    settings = get_settings()
    try:
        _set_source_status(project_id, source_id, "parsing")
        parsed = _parse_source(path, filename, grobid_url=settings.grobid_url)
        logger.info(
            "ingest: parsed source_id=%s sections=%d bib_entries=%d",
            source_id,
            len(parsed.sections),
            len(parsed.bib_entries),
        )

        _set_source_status(project_id, source_id, "chunking")
        chunks = chunker.chunk_document(parsed, source_id)
        logger.info("ingest: chunked source_id=%s chunks=%d", source_id, len(chunks))

        _set_source_status(project_id, source_id, "embedding")
        texts = [c.text for c in chunks]
        dense = embed_dense(texts) if texts else []
        sparse = embed_sparse(texts) if texts else []

        client = _qdrant_client_factory()
        store.upsert_chunks(client, project_id, chunks, dense, sparse)

        if parsed.bib_entries:
            _merge_bibliography(project_id, parsed.bib_entries)

        _set_source_status(project_id, source_id, "done")
        logger.info("ingest: done source_id=%s", source_id)
    except Exception as exc:  # noqa: BLE001 - surface any failure as source status
        logger.exception("ingest: failed source_id=%s", source_id)
        _set_source_status(project_id, source_id, "error", error=str(exc))


def _parse_source(path: Path, filename: str, *, grobid_url: str):
    suffix = Path(filename).suffix.lower()
    if suffix in _PDF_SUFFIXES:
        try:
            return grobid.parse_pdf(path.read_bytes(), grobid_url=grobid_url, filename=filename)
        except grobid.GrobidError as exc:
            logger.warning("ingest: GROBID unavailable (%s), falling back to Docling", exc)
    return docling_fallback.parse_document(path)
