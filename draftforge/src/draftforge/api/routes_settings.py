"""Settings API: model registry + per-role assignment.

This is the one API surface Phase 0 must make fully functional end to end.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from draftforge.api.models import ModelInfo, RoleModelsResponse, RoleModelsUpdate
from draftforge.config import get_settings
from draftforge.llm.registry import get_registry
from draftforge.llm.settings_store import get_role_models, set_role_models

router = APIRouter(prefix="/settings", tags=["settings"])


def _check_ollama_reachable() -> bool:
    settings = get_settings()
    base_url = settings.ollama_base_url.rstrip("/")
    # /v1/models is the OpenAI-compatible listing endpoint Ollama serves.
    url = f"{base_url}/models"
    try:
        resp = httpx.get(url, timeout=2.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@router.get("/models", response_model=RoleModelsResponse)
def get_settings_models() -> RoleModelsResponse:
    registry = get_registry()
    infos = [
        ModelInfo(
            name=m.name,
            provider=m.provider,
            model=m.model,
            local=m.local,
            cost_per_mtok_in=m.cost_per_mtok_in,
            cost_per_mtok_out=m.cost_per_mtok_out,
            note=m.note,
            api_key_present=registry.api_key_present(m.name),
        )
        for m in registry.list_models()
    ]
    role_models = get_role_models(registry=registry)
    return RoleModelsResponse(
        models=infos,
        role_models=role_models,
        ollama_reachable=_check_ollama_reachable(),
    )


@router.put("/models", response_model=RoleModelsResponse)
def put_settings_models(update: RoleModelsUpdate) -> RoleModelsResponse:
    registry = get_registry()
    try:
        set_role_models(update.role_models, registry=registry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_settings_models()
