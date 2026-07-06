"""Application configuration via pydantic-settings.

Values are read from the environment / a `.env` file at the project root.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    qdrant_url: str = "http://localhost:6333"
    grobid_url: str = "http://localhost:8070"
    ollama_base_url: str = "http://localhost:11434/v1"

    data_dir: Path = PROJECT_ROOT / "data"
    models_yaml_path: Path = PROJECT_ROOT / "config" / "models.yaml"

    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    gemini_api_key: str | None = None

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


def get_settings() -> Settings:
    return Settings()
