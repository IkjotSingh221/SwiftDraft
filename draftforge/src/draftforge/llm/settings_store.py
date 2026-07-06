"""SQLite-backed per-role model assignment.

Each agent role maps to a `models.yaml` entry name. Assignments persist
across restarts so the Settings screen's choices stick. Defaults are seeded
the first time the store is touched: a strong model for `planner` and
`citation_verifier`, the cheapest available model (local preferred) for the
rest.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from draftforge.config import get_settings
from draftforge.llm.base import LLMProvider
from draftforge.llm.registry import ModelRegistry, get_registry

ROLES: list[str] = [
    "planner",
    "drafter",
    "critic",
    "citation_verifier",
    "continuity_editor",
]

STRONG_ROLES = {"planner", "citation_verifier"}


def _default_db_path() -> Path:
    settings = get_settings()
    settings.ensure_data_dir()
    return settings.data_dir / "settings.db"


def _default_assignment(registry: ModelRegistry) -> dict[str, str]:
    models = registry.list_models()
    if not models:
        raise RuntimeError("Model registry is empty; cannot seed defaults")

    strong = registry.default_model_name()

    cheap_sorted = sorted(
        models,
        key=lambda m: (m.cost_per_mtok_in + m.cost_per_mtok_out, not m.local),
    )
    cheap = cheap_sorted[0].name

    return {role: (strong if role in STRONG_ROLES else cheap) for role in ROLES}


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db_path()
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS role_models (
            role TEXT PRIMARY KEY,
            model_name TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _seed_if_empty(conn: sqlite3.Connection, registry: ModelRegistry) -> None:
    count = conn.execute("SELECT COUNT(*) FROM role_models").fetchone()[0]
    if count > 0:
        return
    defaults = _default_assignment(registry)
    conn.executemany(
        "INSERT OR REPLACE INTO role_models (role, model_name) VALUES (?, ?)",
        list(defaults.items()),
    )
    conn.commit()


def get_role_models(
    db_path: str | Path | None = None, registry: ModelRegistry | None = None
) -> dict[str, str]:
    """Return the current role -> model_name assignment, seeding defaults on first use."""
    registry = registry or get_registry()
    conn = _connect(db_path)
    try:
        _seed_if_empty(conn, registry)
        rows = conn.execute("SELECT role, model_name FROM role_models").fetchall()
        return {role: model_name for role, model_name in rows}
    finally:
        conn.close()


def set_role_models(
    mapping: dict[str, str],
    db_path: str | Path | None = None,
    registry: ModelRegistry | None = None,
) -> dict[str, str]:
    """Update role -> model_name assignments for the given roles and return the full set."""
    registry = registry or get_registry()
    known_models = {m.name for m in registry.list_models()}
    for role, model_name in mapping.items():
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role!r}. Valid roles: {ROLES}")
        if model_name not in known_models:
            raise ValueError(f"Unknown model name: {model_name!r}")

    conn = _connect(db_path)
    try:
        _seed_if_empty(conn, registry)
        conn.executemany(
            "INSERT OR REPLACE INTO role_models (role, model_name) VALUES (?, ?)",
            list(mapping.items()),
        )
        conn.commit()
        rows = conn.execute("SELECT role, model_name FROM role_models").fetchall()
        return {role: model_name for role, model_name in rows}
    finally:
        conn.close()


def resolve_model(
    role: str,
    db_path: str | Path | None = None,
    registry: ModelRegistry | None = None,
) -> tuple[LLMProvider, str]:
    """Resolve a role to its currently-assigned (provider instance, model name).

    Graph nodes call this AT CALL TIME instead of hardcoding a model name, so
    changes made on the Settings screen take effect on the next node
    invocation without restarting a run.
    """
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role!r}. Valid roles: {ROLES}")
    registry = registry or get_registry()
    role_models = get_role_models(db_path=db_path, registry=registry)
    model_name = role_models[role]
    provider = registry.get_provider_for(model_name)
    return provider, model_name
