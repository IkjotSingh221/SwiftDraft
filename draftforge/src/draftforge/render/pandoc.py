"""Phase 6: the Pandoc subprocess wrapper + artifact registry.

`assemble.py` produces a Markdown string with no Pandoc/LaTeX involved at
all; this module is the ONLY place in the codebase that shells out to an
external binary for rendering. It:

  1. Writes the assembled Markdown + a front-matter metadata YAML to
     `data/runs/{run_id}/artifacts/`.
  2. Copies the project's `bibliography.json` (the single CSL-JSON source of
     truth, see `graph/verifier.py::_bibliography_path`) into the same
     directory as a standalone, always-downloadable artifact.
  3. Invokes `pandoc` twice -- once for `draft.docx`, once for `draft.pdf` --
     against the format spec's `templates/{spec_id}.yaml` Pandoc defaults
     file and `specs/{csl_file}` CSL style.

## Typed errors / graceful degradation

- Unknown/uncheckpointed run: `KeyError` (mirrors `graph/build_graph.py`'s
  own convention for these two cases -- routes translate to 404).
- Run not yet `"completed"`: `ValueError` (routes translate to 409).
- Anything Pandoc-related that keeps the WHOLE render from producing any
  document at all -- most notably `pandoc` itself missing from `PATH` -- is a
  `RenderError` (routes translate to a 502; see DECISIONS.md for why 502 and
  not 500: the failure is in an external dependency, not this service's own
  code).
- Per-format failures (missing `pdf-engine`, or any other real Pandoc error
  for either format -- bad CSL, malformed template, ...) never raise and
  never abort the other format's attempt: each is caught individually and
  recorded in `RenderResult.skipped[name] = reason`, so one broken format
  can't take down the other or the already-written bibliography artifact.
  This is the concrete case spec.md calls out ("if no LaTeX engine, still
  produce docx"), generalized to both formats and to non-engine failures too
  -- a partially-successful render is more useful than an exception that
  discards a document that DID render. `render_run` itself only raises
  `RenderError` when `pandoc` is entirely missing (nothing whatsoever can be
  produced).
- `run_pandoc` always passes a bounded `timeout` to `subprocess.run` -- a
  render can never hang the request/background task that triggered it.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from draftforge.config import PROJECT_ROOT
from draftforge.formats.schema import FormatSpec
from draftforge.graph import build_graph
from draftforge.graph.decisions import decisions_log_path, run_dir
from draftforge.graph.planner import _load_spec_by_id
from draftforge.graph.verifier import _bibliography_path
from draftforge.render.assemble import AssembleError, assemble_run

logger = logging.getLogger(__name__)

PANDOC_TIMEOUT_SECONDS = 120
TEMPLATES_DIR = PROJECT_ROOT / "templates"


class RenderError(RuntimeError):
    """A Pandoc-related failure that keeps the whole render from producing a
    usable document (pandoc missing, a real Pandoc/LaTeX error). See module
    docstring for what is instead handled as a graceful per-format skip."""


# ---------------------------------------------------------------------------
# Binary detection
# ---------------------------------------------------------------------------


def pandoc_available() -> bool:
    return shutil.which("pandoc") is not None


def _engine_available(engine: str) -> bool:
    return shutil.which(engine) is not None


# ---------------------------------------------------------------------------
# Artifact registry (whitelist -- see api/routes_runs.py)
# ---------------------------------------------------------------------------


def artifacts_dir(run_id: str) -> Path:
    d = run_dir(run_id) / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(frozen=True)
class ArtifactSpec:
    content_type: str
    path_fn: Callable[[str], Path]
    note_if_missing: str | None = None


def _decisions_log_artifact_path(run_id: str) -> Path:
    # Served straight from its own canonical location (graph/decisions.py) --
    # no copy into artifacts/ needed, it's already a standalone file.
    return decisions_log_path(run_id)


def _eval_report_path(run_id: str) -> Path:
    # Phase 7 will write this file; the entry exists now so Downloads can
    # list it (as unavailable, with a "coming in Phase 7" note) without a
    # frontend/backend contract change once Phase 7 lands.
    return artifacts_dir(run_id) / "eval_report.md"


ARTIFACT_SPECS: dict[str, ArtifactSpec] = {
    "draft.docx": ArtifactSpec(
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        path_fn=lambda run_id: artifacts_dir(run_id) / "draft.docx",
    ),
    "draft.pdf": ArtifactSpec(
        content_type="application/pdf",
        path_fn=lambda run_id: artifacts_dir(run_id) / "draft.pdf",
    ),
    "bibliography.json": ArtifactSpec(
        content_type="application/json",
        path_fn=lambda run_id: artifacts_dir(run_id) / "bibliography.json",
    ),
    "decisions.jsonl": ArtifactSpec(
        content_type="application/x-ndjson",
        path_fn=_decisions_log_artifact_path,
    ),
    "eval_report.md": ArtifactSpec(
        content_type="text/markdown",
        path_fn=_eval_report_path,
        note_if_missing="coming in Phase 7",
    ),
}


# ---------------------------------------------------------------------------
# Subprocess wrapper
# ---------------------------------------------------------------------------


def run_pandoc(
    args: list[str], *, cwd: Path | None = None, timeout: int = PANDOC_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    """Thin wrapper around `subprocess.run(["pandoc", *args], ...)`. Never
    shells out to anything but the `pandoc` binary itself; always bounded by
    `timeout` so a render can never hang."""
    try:
        return subprocess.run(
            ["pandoc", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RenderError("pandoc is not installed / not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"pandoc timed out after {timeout}s") from exc


def _template_paths(format_spec_id: str) -> tuple[Path, Path]:
    """(defaults_yaml, tex_template) for a format_spec_id, following Phase 2's
    naming convention (`templates/{spec_id}.yaml` / `.tex`)."""
    return TEMPLATES_DIR / f"{format_spec_id}.yaml", TEMPLATES_DIR / f"{format_spec_id}.tex"


def _load_defaults(defaults_yaml: Path) -> dict[str, Any]:
    return yaml.safe_load(defaults_yaml.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Per-format render helpers
# ---------------------------------------------------------------------------


def _render_docx(
    assembled_path: Path,
    defaults: dict[str, Any],
    front_matter_path: Path,
    bib_path: Path,
    number_sections: bool,
    out_path: Path,
    *,
    timeout: int,
) -> None:
    """DOCX needs no LaTeX engine, so this is built from the SAME logical
    options as the defaults file, but WITHOUT `--defaults=...` itself -- the
    defaults file also sets `template`/`pdf-engine`, which Pandoc's docx
    writer cannot consume (see `templates/*.yaml`'s own inline comment: "For
    DOCX output: drop template/pdf-engine ... rely on Pandoc's default docx
    styling -- citeproc/csl/number-sections below still apply.")."""
    args: list[str] = [str(assembled_path)]
    if defaults.get("from"):
        args.append(f"--from={defaults['from']}")
    if defaults.get("standalone"):
        args.append("--standalone")
    if defaults.get("citeproc"):
        args.append("--citeproc")
    if defaults.get("csl"):
        args.append(f"--csl={PROJECT_ROOT / defaults['csl']}")
    args.append(f"--metadata-file={front_matter_path}")
    if bib_path.exists():
        args.append(f"--bibliography={bib_path}")
    args.append(f"--metadata=number-sections:{'true' if number_sections else 'false'}")
    if defaults.get("top-level-division"):
        args.append(f"--top-level-division={defaults['top-level-division']}")
    for key, value in (defaults.get("metadata") or {}).items():
        args.append(f"--metadata={key}:{value}")
    args += ["-o", str(out_path)]

    result = run_pandoc(args, cwd=PROJECT_ROOT, timeout=timeout)
    if result.returncode != 0:
        raise RenderError(f"pandoc docx render failed: {result.stderr.strip()[:2000]}")


def _render_pdf(
    assembled_path: Path,
    defaults_yaml: Path,
    defaults: dict[str, Any],
    front_matter_path: Path,
    bib_path: Path,
    number_sections: bool,
    out_path: Path,
    *,
    timeout: int,
) -> str | None:
    """Returns a skip-reason string if PDF rendering was skipped (missing
    LaTeX engine -- graceful, per spec.md), else None on success. Raises
    `RenderError` for any OTHER Pandoc failure."""
    engine = str(defaults.get("pdf-engine") or "pdflatex")
    if not _engine_available(engine):
        return f"pdf-engine '{engine}' is not installed"

    args = [
        str(assembled_path),
        f"--defaults={defaults_yaml}",
        f"--metadata-file={front_matter_path}",
        f"--metadata=number-sections:{'true' if number_sections else 'false'}",
    ]
    if bib_path.exists():
        args.append(f"--bibliography={bib_path}")
    args += ["-o", str(out_path)]

    result = run_pandoc(args, cwd=PROJECT_ROOT, timeout=timeout)
    if result.returncode != 0:
        raise RenderError(f"pandoc pdf render failed: {result.stderr.strip()[:2000]}")
    return None


# ---------------------------------------------------------------------------
# Result + orchestration
# ---------------------------------------------------------------------------


@dataclass
class RenderResult:
    generated: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


def render_run(
    run_id: str,
    *,
    db_path: str | Path | None = None,
    timeout: int = PANDOC_TIMEOUT_SECONDS,
) -> RenderResult:
    """Assemble + render one completed run's `draft.docx`/`draft.pdf` +
    standalone `bibliography.json`, writing everything to
    `data/runs/{run_id}/artifacts/`.

    Raises `KeyError` (unknown run), `ValueError` (run not completed yet),
    `AssembleError` (bubbled up unchanged -- assembly is pure and should not
    fail for a completed run, but a caller can distinguish it from a Pandoc
    problem), or `RenderError` (pandoc missing / a real Pandoc/LaTeX
    failure). The `assembled.md`/`front_matter.yaml`/`bibliography.json`
    artifacts are written BEFORE the pandoc-availability check, so a run
    whose Pandoc render fails outright still leaves its bibliography
    downloadable.
    """
    meta = build_graph.get_run_meta(run_id)
    if meta is None:
        raise KeyError(f"unknown run_id: {run_id!r}")
    state = build_graph.get_state(run_id, db_path=db_path)
    if state is None or state.get("status") != "completed":
        status = (state or {}).get("status") if state else meta.get("status")
        raise ValueError(f"run {run_id!r} is not completed yet (status={status!r})")

    project_id = state["project_id"]
    format_spec_id = state["format_spec_id"]
    spec: FormatSpec = _load_spec_by_id(format_spec_id)

    doc = assemble_run(run_id, state, spec)  # AssembleError bubbles up unchanged

    out_dir = artifacts_dir(run_id)
    assembled_path = out_dir / "assembled.md"
    assembled_path.write_text(doc.markdown, encoding="utf-8")
    front_matter_path = out_dir / "front_matter.yaml"
    front_matter_path.write_text(yaml.safe_dump(doc.front_matter, sort_keys=False), encoding="utf-8")

    result = RenderResult()

    bib_path = _bibliography_path(project_id)
    bib_artifact = out_dir / "bibliography.json"
    if bib_path.exists():
        bib_artifact.write_text(bib_path.read_text(encoding="utf-8"), encoding="utf-8")
        result.generated.append("bibliography.json")
    else:
        result.skipped["bibliography.json"] = "project has no bibliography.json yet"

    if not pandoc_available():
        result.skipped["draft.docx"] = result.skipped["draft.pdf"] = "pandoc is not installed"
        raise _pandoc_missing_error(result)

    defaults_yaml, _tex_template = _template_paths(format_spec_id)
    if not defaults_yaml.exists():
        raise RenderError(f"no Pandoc defaults file for format spec {format_spec_id!r} (expected {defaults_yaml})")
    defaults = _load_defaults(defaults_yaml)

    try:
        _render_docx(
            assembled_path, defaults, front_matter_path, bib_path, doc.number_sections,
            out_dir / "draft.docx", timeout=timeout,
        )
        result.generated.append("draft.docx")
    except RenderError as exc:
        # DOCX needs no LaTeX engine, so a failure here is unexpected (bad
        # CSL, malformed assembled Markdown, ...) rather than the documented
        # "no LaTeX engine" case -- still recorded as a skip (with the real
        # reason visible to the caller) rather than aborting the whole render
        # and losing the PDF attempt / bibliography artifact already written.
        result.skipped["draft.docx"] = str(exc)
        logger.warning("render_run(%r): docx render failed: %s", run_id, exc)

    try:
        pdf_skip_reason = _render_pdf(
            assembled_path, defaults_yaml, defaults, front_matter_path, bib_path, doc.number_sections,
            out_dir / "draft.pdf", timeout=timeout,
        )
        if pdf_skip_reason is not None:
            result.skipped["draft.pdf"] = pdf_skip_reason
        else:
            result.generated.append("draft.pdf")
    except RenderError as exc:
        result.skipped["draft.pdf"] = str(exc)

    return result


def _pandoc_missing_error(result: RenderResult) -> RenderError:
    return RenderError(
        "pandoc is not installed on this server; docx/pdf cannot be rendered "
        "(bibliography.json/decisions.jsonl remain downloadable if present)"
    )
