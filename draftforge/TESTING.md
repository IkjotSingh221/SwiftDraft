# TESTING.md — living checklist

Maintained from Phase 0 onward. Every phase appends its automated tests and
manual UI checks below, with a one-line purpose and current status. Phase 8
executes this whole document end to end.

Status legend: `PASS` (verified this session), `NOT RUN` (not exercised —
e.g. needs Docker/real API keys/manual eyes), `FAIL`.

---

## Phase 0 — Scaffold

### Automated — backend (`uv run pytest`)

| Test | Purpose | Status |
|---|---|---|
| `tests/test_providers.py::test_base_retries_then_succeeds` | Base class retries a failing `_raw_complete` and succeeds within `max_retries` | PASS |
| `tests/test_providers.py::test_base_retries_exhausted_raises` | Base class raises `LLMError` after exhausting retries | PASS |
| `tests/test_providers.py::test_anthropic_provider` | `AnthropicProvider.complete()` returns `LLMResponse` w/ usage + cost, SDK mocked | PASS |
| `tests/test_providers.py::test_openrouter_provider` | `OpenRouterProvider` (OpenAICompatMixin) returns `LLMResponse`, httpx mocked, auth header present | PASS |
| `tests/test_providers.py::test_ollama_provider_no_api_key` | `OllamaProvider` works with no API key, zero cost, no Authorization header | PASS |
| `tests/test_providers.py::test_gemini_provider` | `GeminiProvider.complete()` returns `LLMResponse` w/ usage + cost, SDK mocked | PASS |
| `tests/test_registry.py::test_models_yaml_loads` | `config/models.yaml` loads, covers all 4 providers | PASS |
| `tests/test_registry.py::test_default_model_is_flagged` | Exactly one model is flagged `default: true` | PASS |
| `tests/test_registry.py::test_get_provider_for_constructs_and_caches` | `get_provider_for` builds + caches a provider instance per model name | PASS |
| `tests/test_registry.py::test_local_model_has_no_required_api_key` | Local (Ollama) models don't require an API key | PASS |
| `tests/test_registry.py::test_api_key_present_reflects_env` | `api_key_present` reflects the actual env var at call time | PASS |
| `tests/test_registry.py::test_unknown_model_raises` | Unknown model name raises `KeyError` | PASS |
| `tests/test_registry.py::test_provider_classes_is_the_only_switch_point` | Contract: `PROVIDER_CLASSES` is the only provider-class reference point | PASS |
| `tests/test_registry.py::test_resolve_model_helper` | `resolve_model(role)` returns `(provider, model_name)` | PASS |
| `tests/test_settings_roundtrip.py::test_defaults_seeded_on_first_read` | Role→model defaults seed correctly (strong for planner/verifier) | PASS |
| `tests/test_settings_roundtrip.py::test_store_roundtrip` | SQLite store: set then get returns the update, other roles untouched | PASS |
| `tests/test_settings_roundtrip.py::test_store_rejects_unknown_role` | Store rejects an unknown role name | PASS |
| `tests/test_settings_roundtrip.py::test_store_rejects_unknown_model` | Store rejects an unknown model name | PASS |
| `tests/test_settings_roundtrip.py::test_api_roundtrip` | `PUT /api/settings/models` then `GET` reflects the update via FastAPI TestClient | PASS |
| `tests/test_health.py::test_health_ok_when_both_reachable` | `/api/health` reports ok when both services reachable (mocked) | PASS |
| `tests/test_health.py::test_health_degraded_when_both_down` | `/api/health` reports degraded, still 200, when both down (mocked) | PASS |
| `tests/test_health.py::test_health_never_crashes_without_mocking` | `/api/health` never raises even with unreachable URLs and no mocking | PASS |

Run: `cd draftforge && uv run pytest -q` → **22 passed**.

### Automated — frontend (`npm run test -- --run`)

| Test | Purpose | Status |
|---|---|---|
| `src/components/ThemeToggle.test.tsx` | Clicking the toggle flips the `dark` class on `<html>` and persists to localStorage | PASS |
| `src/pages/Settings.test.tsx` | Settings page renders one picker per role, populated from a mocked `/api/settings/models` response | PASS |

Run: `cd draftforge/frontend && npm run test -- --run` → **2 passed**.

### Build / smoke checks

| Check | Purpose | Status |
|---|---|---|
| `uv sync --extra dev` | All backend deps (incl. heavy docling/torch stack) install cleanly | PASS |
| `npm install` | All frontend deps install cleanly | PASS |
| `npm run build` (`tsc -b && vite build`) | Frontend type-checks and builds for production | PASS |
| `uv run uvicorn draftforge.api.app:app` then `curl /api/health` | API boots and responds without Docker running (`degraded`, both `false`) | PASS |
| `npm run dev` then `curl localhost:5173/api/health` | Vite proxy forwards `/api/*` to the backend on :8000 | PASS |
| `curl localhost:5173/settings` (page route) | No collision with the `/api` proxy prefix; SPA shell served (200) | PASS |
| `curl localhost:5173/api/settings/models` | Settings data reaches the frontend through the proxy | PASS |

### Manual UI checks — NOT RUN this session (no browser available; verify visually before Phase 8)

| Check | Purpose | Status |
|---|---|---|
| Settings screen — light mode | Role pickers, model table, Ollama status, save button all readable/usable | NOT RUN |
| Settings screen — dark mode | Same, dark theme tokens applied, no unreadable contrast | NOT RUN |
| Theme toggle in header | Manual toggle switches instantly, choice persists across reload | NOT RUN |
| System-preference default | With no stored choice, initial theme matches OS preference | NOT RUN |
| Nav between all six pages | Header nav links route correctly; non-Settings pages show a clear "Phase N" placeholder in both themes | NOT RUN |
| Settings save round-trip in the browser | Change a role's model, click Save, reload, change persisted | NOT RUN |

### Known gaps / notes

- Real provider calls (actual Anthropic/OpenRouter/Ollama/Gemini network
  requests) are **not** exercised — all provider tests mock the HTTP/SDK
  layer per the Phase 0 scope (no API keys, no Docker, no GPU required).
- `docker-compose.yml` (Qdrant + GROBID) was not brought up this session —
  `/api/health` was verified to correctly report `degraded` without them
  running, which is the required behavior for Phase 0.
- Frontend manual/visual checks above are unexercised because this session
  has no browser; the automated build + proxy + fetch checks stand in for
  now. Flag for a human pass before Phase 8's full manual sweep.

---

## Phase 1 — Ingestion

### Automated — backend (`uv run pytest`)

| Test | Purpose | Status |
|---|---|---|
| `tests/test_ingest_chunker.py::test_count_tokens_is_word_count` | Heuristic token counter is a plain word count | PASS |
| `tests/test_ingest_chunker.py::test_never_crosses_section_boundary` | Chunks never mix text/section_path from two different sections | PASS |
| `tests/test_ingest_chunker.py::test_chunk_size_within_target_range_for_long_section` | Long sections split into ~300-600 token chunks | PASS |
| `tests/test_ingest_chunker.py::test_small_section_not_split_and_not_forced_to_min` | A section shorter than `min_tokens` stays one chunk (no cross-boundary merge) | PASS |
| `tests/test_ingest_chunker.py::test_overlap_between_consecutive_chunks` | Consecutive chunks in the same section share the documented 15% word overlap | PASS |
| `tests/test_ingest_chunker.py::test_metadata_and_bibkeys_attached` | Each chunk carries source_id, section_path, page, chunk_id, bibkeys | PASS |
| `tests/test_ingest_chunker.py::test_chunk_ids_increment_across_sections` | chunk_id numbering is stable/sequential across a document | PASS |
| `tests/test_ingest_chunker.py::test_chunk_document_pulls_bibkeys_from_bib_entries` | `chunk_document` derives the bibkey set from `ParsedDocument.bib_entries` | PASS |
| `tests/test_ingest_chunker.py::test_empty_section_text_produces_no_chunks` | Empty section text yields zero chunks (no empty chunk emitted) | PASS |
| `tests/test_ingest_grobid.py::test_parse_tei_extracts_sections_with_hierarchy_and_pages` | TEI fixture -> section_path hierarchy + `<pb/>`-derived page numbers | PASS |
| `tests/test_ingest_grobid.py::test_parse_tei_extracts_csl_json_bib_entries_with_stable_bibkeys` | TEI `listBibl` -> CSL-JSON entries with the documented bibkey scheme | PASS |
| `tests/test_ingest_grobid.py::test_parse_tei_is_stable_bibkey_scheme_idempotent` | Re-parsing the same TEI produces identical bibkeys | PASS |
| `tests/test_ingest_grobid.py::test_parse_pdf_raises_typed_error_when_grobid_unreachable` | Connection failure -> typed `GrobidError` (httpx mocked, no real server) | PASS |
| `tests/test_ingest_grobid.py::test_parse_pdf_raises_typed_error_on_non_200` | Non-200 GROBID response -> typed `GrobidError` | PASS |
| `tests/test_ingest_grobid.py::test_parse_pdf_posts_and_parses_tei_response` | End-to-end `parse_pdf` against a mocked HTTP response | PASS |
| `tests/test_ingest_docling_fallback.py::test_docling_module_does_not_import_docling_at_import_time` | Importing the fallback module never imports `docling` | PASS |
| `tests/test_ingest_docling_fallback.py::test_markdown_headings_build_section_hierarchy` | ATX heading depth builds a correct section_path stack | PASS |
| `tests/test_ingest_docling_fallback.py::test_markdown_with_no_headings_yields_single_untitled_section` | Headless markdown still yields one section | PASS |
| `tests/test_ingest_docling_fallback.py::test_empty_markdown_yields_no_sections` | Empty/whitespace-only markdown yields zero sections | PASS |
| `tests/test_ingest_store.py::test_known_relevant_chunk_lands_in_top5` | Hybrid (dense+sparse RRF) search returns the known-relevant chunk in top-5, against Qdrant's real `:memory:` engine | PASS |
| `tests/test_ingest_store.py::test_source_id_filter_scopes_results` | `source_id` metadata filter excludes chunks from other sources | PASS |
| `tests/test_ingest_store.py::test_ensure_collection_is_idempotent` | Creating a collection twice is a no-op the second time | PASS |
| `tests/test_ingest_store.py::test_upsert_rejects_mismatched_lengths` | `upsert_chunks` validates chunks/dense/sparse are the same length | PASS |
| `tests/test_ingest_embedder.py::test_embed_dense_with_injected_model` | `embed_dense` uses an injected fake model, no real weights loaded | PASS |
| `tests/test_ingest_embedder.py::test_embed_dense_clamps_batch_size_to_vram_budget` | batch_size is clamped to `MAX_BATCH_SIZE=32` regardless of caller request | PASS |
| `tests/test_ingest_embedder.py::test_embed_sparse_with_injected_model` | `embed_sparse` uses an injected fake model | PASS |
| `tests/test_ingest_embedder.py::test_injected_model_bypasses_lazy_singleton_loaders` | Passing `model=` never touches the real lazy-loading singleton constructors | PASS |
| `tests/test_ingest_api.py::test_create_project` | `POST /api/projects` creates and returns a project record | PASS |
| `tests/test_ingest_api.py::test_get_unknown_project_404s` | `GET` on an unknown project_id 404s | PASS |
| `tests/test_ingest_api.py::test_upload_and_ingest_round_trip_reaches_done` | Upload -> background ingestion (GROBID/embedder/Qdrant mocked) -> polled status reaches `done` | PASS |
| `tests/test_ingest_api.py::test_upload_to_unknown_project_404s` | Upload to an unknown project_id 404s | PASS |
| `tests/test_ingest_api.py::test_ingestion_failure_surfaces_as_error_status` | A parse failure (GROBID + Docling both fail) surfaces as `status: "error"` with the message | PASS |
| `tests/test_ingest_live_grobid.py::test_live_grobid_parses_real_arxiv_pdf` | OPTIONAL: real arXiv PDF through a real GROBID server | SKIPPED (gated on `DRAFTFORGE_LIVE=1` + a real GROBID server + a fixture PDF; not run this session — no Docker/network in this environment) |

Run: `cd draftforge && uv run pytest -q` → **54 passed, 1 skipped** (Phase 0's
22 + Phase 1's 32; the live GROBID test skips without `DRAFTFORGE_LIVE=1`).

### Build / smoke checks

| Check | Purpose | Status |
|---|---|---|
| `npm run build` (`tsc -b && vite build`) | New Project page type-checks and the frontend still builds | PASS |
| `npm run test -- --run` | Phase 0's 2 frontend tests still pass unmodified | PASS |

### Manual UI checks — NOT RUN this session (no browser available)

| Check | Purpose | Status |
|---|---|---|
| New Project — light mode | Project form, dropzone, pending-file list, progress bars all readable | NOT RUN |
| New Project — dark mode | Same, dark theme tokens applied, no unreadable contrast | NOT RUN |
| Drag-and-drop a PDF/DOCX | File lands in the pending list; non-PDF/DOCX files are rejected with a message | NOT RUN |
| Start ingestion end-to-end against real Docker (Qdrant + GROBID) | Per-file progress bar advances queued -> parsing -> chunking -> embedding -> done | NOT RUN |

### Known gaps / notes

- The real BGE-M3/BM25 models and a real Qdrant/GROBID server were never
  loaded or started this session (no GPU, no Docker) — every automated test
  above mocks or replaces those boundaries. Hybrid search is exercised
  against Qdrant's real `:memory:` local-engine code path (not a hand-rolled
  fake), which shares its query/fusion implementation with a real server,
  but the actual `bge-m3`/BM25 model weights and a live GROBID server are
  untested this session.
- The optional live arXiv end-to-end test
  (`tests/test_ingest_live_grobid.py`) needs a real GROBID server
  (`docker compose up -d grobid`) and a fixture PDF downloaded to
  `tests/fixtures/arxiv_sample.pdf` (see the test file's docstring for the
  exact command) plus `DRAFTFORGE_LIVE=1`; none of that was available this
  session, so it was never actually executed, only confirmed to skip
  cleanly.
- Frontend manual/visual checks are unexercised (no browser this session);
  flag for a human pass before Phase 8's full manual sweep.

---

## Phase 2 — Format specs

### Automated — backend (`uv run pytest`)

| Test | Purpose | Status |
|---|---|---|
| `tests/test_formats_validator.py::test_word_count_over_limit_is_caught` | A section over its `word_range.max` produces `WORD_COUNT_OVER` | PASS |
| `tests/test_formats_validator.py::test_word_count_under_limit_is_caught` | A section under its `word_range.min` produces `WORD_COUNT_UNDER` | PASS |
| `tests/test_formats_validator.py::test_missing_required_subsection_is_caught` | A required subsection heading absent from the section's own text produces `MISSING_REQUIRED_SUBSECTION` | PASS |
| `tests/test_formats_validator.py::test_heading_too_deep_is_caught` | A heading deeper than `max_heading_depth` produces `HEADING_TOO_DEEP` | PASS |
| `tests/test_formats_validator.py::test_malformed_citation_marker_is_caught` | `[@bad key]` and `[@]` both produce `MALFORMED_CITATION_MARKER`; well-formed `[@smith2019]` is not flagged | PASS |
| `tests/test_formats_validator.py::test_heading_numbering_mismatch_is_caught` | `heading_numbering.style="decimal"` + an unnumbered heading produces `HEADING_NUMBERING_MISMATCH` | PASS |
| `tests/test_formats_validator.py::test_heading_numbering_forbidden_when_present_is_caught` | `heading_numbering.style="none"` + a numbered heading produces `HEADING_NUMBERING_MISMATCH` | PASS |
| `tests/test_formats_validator.py::test_caption_missing_is_caught` | An image with no following caption line produces `CAPTION_MISSING` | PASS |
| `tests/test_formats_validator.py::test_caption_present_but_malformed_is_caught` | An image followed by non-caption prose produces `CAPTION_MALFORMED` | PASS |
| `tests/test_formats_validator.py::test_abstract_over_limit_is_caught` | An abstract over `front_matter.abstract_word_range.max` produces `ABSTRACT_WORD_COUNT_OVER` | PASS |
| `tests/test_formats_validator.py::test_front_matter_field_missing_is_caught` | A missing required title-page field produces `FRONT_MATTER_FIELD_MISSING` | PASS |
| `tests/test_formats_validator.py::test_compliant_fixture_has_zero_violations` | A fully compliant section fixture (word count, subsection, numbering, citation, figure+table captions all correct) produces zero violations | PASS |
| `tests/test_formats_validator.py::test_validate_document_flags_missing_top_level_section_without_cascading` | A required top-level section entirely absent from `sections` is flagged once, without cascading into its (also absent) children | PASS |
| `tests/test_formats_validator.py::test_validate_document_runs_validate_section_for_each_provided_section` | `validate_document` re-runs `validate_section` per provided section (word-count violation surfaces at doc level) | PASS |
| `tests/test_formats_validator.py::test_validate_document_with_front_matter_and_abstract` | Optional `front_matter_fields`/`abstract_markdown` kwargs fold front-matter violations into `validate_document`'s result | PASS |
| `tests/test_formats_schema.py::test_load_spec_loads_both_example_specs[ieee_report]` | `load_spec` loads `specs/ieee_report.json` into a `FormatSpec` | PASS |
| `tests/test_formats_schema.py::test_load_spec_loads_both_example_specs[university_thesis]` | `load_spec` loads `specs/university_thesis.json` into a `FormatSpec` | PASS |
| `tests/test_formats_schema.py::test_example_specs_round_trip_through_pydantic` | `FormatSpec.model_dump()` -> `load_spec_from_dict()` round-trips to an equal model | PASS |
| `tests/test_formats_schema.py::test_ieee_report_section_ids` | `iter_all_sections`/`find_section` see both top-level and nested (`method_data`) ids | PASS |
| `tests/test_formats_schema.py::test_university_thesis_nested_sections` | Nested subsections (`literature_review` -> `related_work`/`research_gap`) parse into the recursive tree | PASS |
| `tests/test_formats_schema.py::test_list_available_specs_finds_both` | `list_available_specs` lists both example specs and excludes the JSON Schema file itself | PASS |
| `tests/test_formats_schema.py::test_malformed_spec_missing_required_field_raises` | A dict missing required top-level keys raises `FormatSpecError` mentioning schema validation | PASS |
| `tests/test_formats_schema.py::test_malformed_spec_wrong_type_raises` | A dict with `sections` as the wrong JSON type raises `FormatSpecError` | PASS |
| `tests/test_formats_schema.py::test_load_spec_missing_file_raises_clear_error` | A nonexistent spec path raises `FormatSpecError` with a clear message | PASS |
| `tests/test_formats_schema.py::test_load_spec_invalid_json_raises_clear_error` | A spec file with broken JSON syntax raises `FormatSpecError` | PASS |
| `tests/test_formats_schema.py::test_word_range_min_greater_than_max_rejected` | `WordRange(min=100, max=10)` is rejected by a pydantic validator | PASS |

Run: `cd draftforge && uv run pytest -q -k formats` -> **26 passed**.
Full suite: `cd draftforge && uv run pytest -q` -> **80 passed, 1 skipped**
(Phase 0's 22 + Phase 1's 32 + Phase 2's 26; the one skip is Phase 1's
optional live-GROBID test, unrelated to Phase 2).

### Manual checks

| Check | Purpose | Status |
|---|---|---|
| `specs/ieee_report.json` and `specs/university_thesis.json` validate against `specs/format_spec.schema.json` | Both example specs are structurally valid per the JSON Schema (exercised via `load_spec`, which always schema-validates first) | PASS |
| Pandoc template files are plain text and diffable (`templates/*.tex`, `templates/*.yaml`) | Confirms the "no binary reference-doc" design choice (see DECISIONS.md) | PASS |

### Known gaps / notes

- No real Pandoc invocation was run against `templates/ieee_report.yaml` /
  `templates/university_thesis.yaml` this session (that's Phase 6's job) --
  Phase 2 only confirms the templates/defaults files are well-formed text
  and cross-references the right CSL/spec paths; end-to-end
  Markdown-to-PDF/DOCX rendering is untested until Phase 6.
- `specs/csl/ieee.csl` and `specs/csl/apa.csl` are intentionally minimal
  placeholder CSL styles, not the official IEEE/APA styles -- see the
  comment at the top of each file and the DECISIONS.md entry for the real
  styles to drop in before a real render.
- Caption-rule checking is a line-based heuristic (adjacent-line only, no
  multi-line caption paragraphs); documented as a known simplification in
  DECISIONS.md.
