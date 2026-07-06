"""Pydantic models for format specs, mirroring `specs/format_spec.schema.json`.

Loading always goes through `load_spec` / `load_spec_from_dict`, which validate
the raw JSON against the JSON Schema (draft 2020-12, via `jsonschema`) *before*
parsing into pydantic models. That way a malformed spec fails fast with a
`jsonschema` error message pointing at the exact offending path, rather than a
confusing pydantic error over data that was never structurally valid to begin
with.

These models are the shared DTOs for format specs across phases: Phase 3's
planner reads a `FormatSpec` to build an outline that conforms to it; Phase 6
renders with `citation_style.csl_file` and the matching `templates/*`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, model_validator

from draftforge.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

SPECS_DIR = PROJECT_ROOT / "specs"
SCHEMA_PATH = SPECS_DIR / "format_spec.schema.json"


class FormatSpecError(ValueError):
    """Raised when a format spec file fails JSON-Schema validation or parsing."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class WordRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: int = Field(ge=0)
    max: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> "WordRange":
        if self.max < self.min:
            raise ValueError(f"word_range.max ({self.max}) < word_range.min ({self.min})")
        return self


class SectionSpec(BaseModel):
    """One node in the recursive section tree (chapter -> section -> subsection)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    required: bool = True
    max_heading_depth: int = Field(default=1, ge=1, le=6)
    word_range: WordRange | None = None
    allow_extra_subsections: bool = False
    subsections: list["SectionSpec"] = Field(default_factory=list)

    def iter_tree(self) -> Iterator["SectionSpec"]:
        """Depth-first walk of this node and all its descendants."""
        yield self
        for child in self.subsections:
            yield from child.iter_tree()


SectionSpec.model_rebuild()


class CitationStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style_id: str
    csl_file: str
    marker_style: Literal["pandoc_at_key"] = "pandoc_at_key"


class HeadingNumbering(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: Literal["decimal", "none"]
    start_level: int = Field(default=1, ge=1)


class FrontMatterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_page_fields: list[str] = Field(default_factory=list)
    abstract_required: bool = True
    abstract_word_range: WordRange | None = None


class CaptionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str
    numbering: Literal["decimal", "chapter_decimal", "none"]
    required: bool = True
    min_words: int = Field(default=0, ge=0)


class CaptionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    figure: CaptionRule
    table: CaptionRule


class FormatSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_id: str
    name: str
    version: str
    description: str = ""
    sections: list[SectionSpec]
    citation_style: CitationStyle
    heading_numbering: HeadingNumbering
    front_matter: FrontMatterSpec
    captions: CaptionRules

    def iter_all_sections(self) -> Iterator[SectionSpec]:
        """Depth-first walk over every section/subsection in the whole tree."""
        for top in self.sections:
            yield from top.iter_tree()

    def find_section(self, section_id: str) -> SectionSpec | None:
        for section in self.iter_all_sections():
            if section.id == section_id:
                return section
        return None


class SpecSummary(BaseModel):
    """Lightweight listing entry for the UI spec picker / Phase 3 planner."""

    model_config = ConfigDict(extra="forbid")

    spec_id: str
    name: str
    version: str
    description: str
    path: str


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_json_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_spec_from_dict(data: dict[str, Any]) -> FormatSpec:
    """Validate a raw spec dict against the JSON Schema, then parse it.

    Raises `FormatSpecError` with a clear, pointed message if the dict fails
    JSON-Schema validation.
    """
    schema = _load_json_schema()
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise FormatSpecError(f"format spec failed schema validation at '{path}': {exc.message}") from exc
    return FormatSpec.model_validate(data)


def load_spec(path: str | Path) -> FormatSpec:
    """Read a spec JSON file from disk, schema-validate it, then parse it."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FormatSpecError(f"could not read format spec file '{path}': {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FormatSpecError(f"format spec file '{path}' is not valid JSON: {exc}") from exc
    try:
        return load_spec_from_dict(data)
    except FormatSpecError as exc:
        raise FormatSpecError(f"{path}: {exc}") from exc


def list_available_specs(specs_dir: str | Path = SPECS_DIR) -> list[SpecSummary]:
    """List every valid format spec JSON file in `specs_dir`.

    Used by the UI's spec picker (Phase 1 upload screen) and Phase 3's
    planner. Skips the JSON Schema file itself and any file that fails to
    load, logging a warning rather than raising -- one bad spec file should
    never take down the whole listing.
    """
    specs_dir = Path(specs_dir)
    summaries: list[SpecSummary] = []
    for candidate in sorted(specs_dir.glob("*.json")):
        if candidate.resolve() == SCHEMA_PATH.resolve():
            continue
        try:
            spec = load_spec(candidate)
        except FormatSpecError as exc:
            logger.warning("Skipping invalid format spec %s: %s", candidate, exc)
            continue
        summaries.append(
            SpecSummary(
                spec_id=spec.spec_id,
                name=spec.name,
                version=spec.version,
                description=spec.description,
                path=str(candidate),
            )
        )
    return summaries
