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

---

## Phase 3 — Planner + human-in-the-loop

### Automated — backend (`uv run pytest`)

| Test | Purpose | Status |
|---|---|---|
| `tests/test_graph_state.py::test_document_state_round_trips_through_dump_and_validate` | `DocumentState.model_dump()` -> `model_validate()` round-trips to an equal model | PASS |
| `tests/test_graph_state.py::test_document_state_defaults_are_empty_and_compact` | An empty `DocumentState` renders to `""` / 0 tokens | PASS |
| `tests/test_graph_state.py::test_document_state_stays_compact_for_a_document_sized_ledger` | A ~40-leaf-section, 60-citation, 30-claim ledger (simulating a 50+ page doc) still serializes well under the spec.md ~4k token budget | PASS |
| `tests/test_graph_state.py::test_outline_tree_node_is_leaf_and_iter_tree` | `OutlineTreeNode.is_leaf()`/`iter_tree()` behave correctly on a 2-node tree | PASS |
| `tests/test_graph_state.py::test_section_brief_round_trips` | `SectionBrief` round-trips through dump/validate | PASS |
| `tests/test_graph_decisions.py::test_log_decision_appends_one_json_line_per_call` | `log_decision` appends one well-formed JSON record per call | PASS |
| `tests/test_graph_decisions.py::test_read_decisions_returns_records_in_order` | `read_decisions` returns records in append order | PASS |
| `tests/test_graph_decisions.py::test_read_decisions_on_missing_file_returns_empty_list` | Reading a run with no decisions log yet returns `[]`, not an error | PASS |
| `tests/test_graph_planner.py::test_outline_conforms_to_spec_structure` | A MOCKED planner LLM's well-formed JSON outline produces an outline/leaf_briefs whose ids/nesting exactly match `ieee_report.json`'s required-section tree; `resolve_model("planner")` is used (never a hardcoded model name); decisions (retrieval_sample/raw_outline/validation) are logged | PASS |
| `tests/test_graph_planner.py::test_malformed_llm_outline_is_rejected_and_repaired` | A non-JSON LLM response degrades to the deterministic spec skeleton — required structure still present, `unparseable_or_empty_llm_outline` violation logged | PASS |
| `tests/test_graph_planner.py::test_partial_llm_outline_missing_a_required_section_is_repaired` | An LLM outline missing a required section and with an out-of-range `target_words` is repaired (section filled in from the skeleton, word count clamped into the spec's range), both logged as violations | PASS |
| `tests/test_graph_planner.py::test_sample_retrieval_handles_unavailable_store_gracefully` | An unreachable Qdrant client factory yields empty per-leaf samples instead of raising | PASS |
| `tests/test_graph_planner.py::test_sample_retrieval_handles_per_leaf_search_failure` | A `hybrid_search` failure for one leaf doesn't affect others / doesn't raise | PASS |
| `tests/test_graph_build.py::test_graph_pauses_after_planner_then_resumes` | `create_run` + `run_planner_to_interrupt` reaches `awaiting_outline_approval` (registry + checkpoint agree); `resume` reaches `completed` | PASS |
| `tests/test_graph_build.py::test_resume_before_approval_state_is_rejected` | `resume()` on a run with no checkpoint yet raises `KeyError` | PASS |
| `tests/test_graph_build.py::test_apply_outline_edits_persists_through_the_checkpointer` | An edit applied via `apply_outline_edits` is visible on a **separate** subsequent `get_state` call (fresh checkpointer connection each time) | PASS |
| `tests/test_graph_build.py::test_double_approve_is_rejected_after_completion` | Calling `resume()` twice raises `ValueError` the second time (not paused at the interrupt anymore) | PASS |
| `tests/test_graph_build.py::test_checkpoint_survives_a_brand_new_graph_object_against_the_same_db_file` | A brand-new `SqliteSaver`/connection/`CompiledStateGraph` built against the same on-disk sqlite file (simulating a process restart) recovers the run's outline purely by `run_id` (spec.md non-negotiable #5, "everything resumes") | PASS |
| `tests/test_runs_api.py::test_create_run_returns_queued_immediately` | `POST /api/runs` returns 202 with a queued/planning/awaiting-approval status immediately (planner runs as a background task) | PASS |
| `tests/test_runs_api.py::test_full_interrupt_edit_approve_round_trip` | Full round trip through the FastAPI TestClient: create -> poll to `awaiting_outline_approval` -> `GET outline` -> edit title/word target -> `PATCH outline` -> re-`GET` confirms persistence -> `POST approve` -> `completed` | PASS |
| `tests/test_runs_api.py::test_get_outline_before_ready_is_409` | `GET outline` for an unknown run_id is 404 | PASS |
| `tests/test_runs_api.py::test_approve_before_outline_ready_is_409` | A second `POST approve` after the run already completed is 409 | PASS |
| `tests/test_runs_api.py::test_unknown_format_spec_id_surfaces_as_run_error` | An unknown `format_spec_id` surfaces as run status `"error"` with a message, not an unhandled 500 or a silently stuck run | PASS |
| `tests/test_runs_api.py::test_get_unknown_run_404s` | `GET`/`PATCH`/`POST approve` on an unknown run_id all 404 | PASS |

Run: `cd draftforge && uv run pytest -q -k graph_state or graph_decisions or graph_planner or graph_build or runs_api` -> **24 passed**.
Full suite: `cd draftforge && uv run pytest -q` -> **104 passed, 1 skipped** (Phase 0's
22 + Phase 1's 32 + Phase 2's 26 + Phase 3's 24; the one skip is Phase 1's
optional live-GROBID test, unrelated to Phase 3).

### Build / smoke checks

| Check | Purpose | Status |
|---|---|---|
| `npx tsc -b` | Frontend (incl. new `OutlineReview.tsx` + `api/client.ts` run/outline types) type-checks with no errors | PASS |
| `npm run build` | Frontend still builds for production | PASS |
| `npm run test -- --run` | Phase 0's 2 frontend tests still pass unmodified (no Phase 3 frontend unit tests were added — Outline Review's UI logic is exercised end-to-end through the backend API tests instead) | PASS |

### Manual UI checks — NOT RUN this session (no browser available)

| Check | Purpose | Status |
|---|---|---|
| Outline Review — light mode | Start-run form, run status badge, collapsible outline tree, inline edit fields, Save/Approve buttons all readable/usable | NOT RUN |
| Outline Review — dark mode | Same, dark theme tokens applied, no unreadable contrast | NOT RUN |
| Start a run end-to-end against a real backend + real LLM | Paste a real project id, pick a spec, confirm the outline appears once planning finishes, edit a section, approve, confirm status flips to "completed" | NOT RUN |
| Collapse/expand a multi-child section (e.g. IEEE's "Method") | Toggle hides/shows its subsections without losing in-progress edits | NOT RUN |

### Known gaps / notes

- The planner's retrieval sample and LLM call are both mocked in every
  automated test this session (no Qdrant, no GROBID-ingested project, no
  real provider/API key) — the graceful-degradation path (unavailable
  store) is exercised directly (`test_sample_retrieval_handles_*`), but a
  real end-to-end "ingest a project, then plan against its real chunks"
  run has not been performed. Flag for Phase 8's real-LLM end-to-end pass.
- The planner currently produces outline nesting that is exactly the
  `FormatSpec`'s own section tree (no additional planner-introduced
  subsections beyond it) — see DECISIONS.md. A more elaborate planner that
  subdivides a leaf section into finer sub-leaves is explicitly out of
  scope for Phase 3.
- No new frontend Vitest tests were added for `OutlineReview.tsx` — its
  logic (create/poll/edit/approve) is a thin wrapper over the same API
  contract already covered end-to-end by `tests/test_runs_api.py`; a
  human/browser pass is the primary way to validate the tree UI itself
  (see the manual checks above).
- `run_planner_to_interrupt` swallows exceptions and reports them via the
  run registry's `error` field rather than propagating — this is
  deliberate (it runs as a `BackgroundTasks` callback with no caller to
  raise to) but means a bug inside the planner node surfaces only through
  polling `GET /api/runs/{id}`, never as a stack trace in the request that
  started the run.

---

## Phase 4 — Drafter subgraph

### Automated — backend (`uv run pytest`)

| Test | Purpose | Status |
|---|---|---|
| `tests/test_graph_drafter.py::test_good_grades_draft_path_no_retries` | Good chunk grades -> exactly one query rewrite, no retries, leaf reaches "done" | PASS |
| `tests/test_graph_drafter.py::test_poor_grades_retries_up_to_two_times` | Poor grades on every attempt -> exactly `MAX_QUERY_REWRITES + 1` (3) query-rewrite/retrieval/grading attempts, never more, leaf still completes best-effort; decisions logged with `attempt` 0,1,2 | PASS |
| `tests/test_graph_drafter.py::test_enforce_valid_bibkeys_strips_hallucinated_keys` | Unit test of the constraint-#2 enforcement function: invalid keys stripped, valid keys kept, a mixed marker keeps only its valid entries | PASS |
| `tests/test_graph_drafter.py::test_hallucinated_bibkey_never_survives_into_saved_draft` | End-to-end: an LLM draft response containing a bogus `[@notreal]` key never survives into `result.citation_keys` or the file written to disk; stripping is logged as a `bibkey_enforcement` decision | PASS |
| `tests/test_graph_drafter.py::test_critique_revise_triggers_exactly_one_revision_call` | A "revise" critique verdict triggers exactly one additional draft-system LLM call (the revision), never a loop; critique itself runs exactly once | PASS |
| `tests/test_graph_drafter.py::test_leaf_pipeline_failure_never_raises_and_flags_the_section` | Any exception inside a leaf's pipeline (e.g. `resolve_model` failing) is caught, the leaf is marked "flagged" with no draft file, and the fan-out is never sunk by one bad leaf | PASS |
| `tests/test_graph_drafter.py::test_fanout_produces_all_sections_with_bounded_parallelism` | A 5-leaf outline with `drafter_parallelism=2` produces a draft file + "done" status for every leaf, and the max concurrently-in-flight LLM calls observed is `<= 2` (and `> 1`, proving real parallelism, not serial execution) | PASS |
| `tests/test_graph_drafter.py::test_apply_document_state_update_updates_registries_and_stays_compact` | The post-draft DocumentState update folds in section summary, citation keys, figure/table counters, and claims correctly, is a pure function (original untouched), and stays well under the ~4k token budget | PASS |
| `tests/test_graph_drafter.py::test_apply_document_state_update_ignores_flagged_leaves` | A "flagged" (failed) leaf contributes nothing to DocumentState | PASS |
| `tests/test_runs_sse.py::test_events_endpoint_delivers_section_status_and_token_usage` | A full mocked run (create -> approve, through the FastAPI TestClient) produces an SSE stream with `section_status` events in order queued -> drafting -> critiquing -> done per section, non-decreasing cumulative `token_usage`, and `run_status` drafting -> completed | PASS |
| `tests/test_runs_sse.py::test_events_endpoint_404s_for_unknown_run` | `GET /runs/{id}/events` 404s for an unknown run_id instead of hanging/500ing | PASS |
| `tests/test_runs_sse.py::test_tail_run_events_replays_full_history_on_reconnect` | Two independent calls to `api.sse.tail_run_events` against the same `events.jsonl` return the identical event list — proves the reattach-by-replay mechanism directly, without going through HTTP | PASS |

Run: `cd draftforge && uv run pytest -q -k graph_drafter or runs_sse` -> **12 passed**.
Full suite: `cd draftforge && uv run pytest -q` -> **116 passed, 1 skipped** (Phase 0-3's
104 + Phase 4's 12; the one skip is Phase 1's optional live-GROBID test).

### Build / smoke checks

| Check | Purpose | Status |
|---|---|---|
| `npx tsc -b` | Frontend (incl. new `RunDashboard.tsx`, `api/sse.ts`, and `api/client.ts` section-status/decisions/SSE-url additions) type-checks with no errors | PASS |
| `npm run build` | Frontend still builds for production | PASS |
| `npm run test -- --run` | Phase 0's 2 frontend tests still pass unmodified (no new Vitest tests added — the dashboard's data-fetching/reduction logic is a thin wrapper over the same API/SSE contract already covered end-to-end by `tests/test_runs_sse.py` and `tests/test_graph_drafter.py`; a human/browser pass is the primary way to validate the dashboard UI itself, same precedent as Phase 3's Outline Review) | PASS |

### Manual UI checks — NOT RUN this session (no browser available)

| Check | Purpose | Status |
|---|---|---|
| Run Dashboard — light mode | Run-id input, status/token/cost summary, per-section chips, expandable decisions log all readable/usable | NOT RUN |
| Run Dashboard — dark mode | Same, dark theme tokens applied, no unreadable contrast | NOT RUN |
| Approve an outline and watch the dashboard live | Section chips progress queued -> drafting -> critiquing -> done in real time via SSE; token/cost counter increases | NOT RUN |
| Expand a section's decisions log | Query rewrites/chunk grades/critique verdicts/bibkey enforcement appear, most-recent-first-or-in-order as logged | NOT RUN |
| Reload the dashboard mid-run / after a resume | SSE reconnects and the full section-status history replays instead of starting blank | NOT RUN |

### Known gaps / notes

- **Two pre-existing Phase 3 tests now incur real (bounded) added latency.**
  `tests/test_graph_build.py::test_graph_pauses_after_planner_then_resumes`
  and `tests/test_runs_api.py`'s approve-round-trip tests only mock the
  planner's model/retrieval, not the drafter/critic roles or
  `ingest.store.hybrid_search`. Now that `resume()` actually runs the
  drafter fan-out, each of their `resume()`/`approve` calls attempts a real
  (unreachable, in this sandboxed environment) local Ollama call per leaf,
  which costs ~3s (two retry backoff sleeps in `llm/base.py`, which Phase 4
  may not edit) before failing fast and marking that leaf "flagged" — see
  DECISIONS.md's "fail fast on the first LLM failure per leaf" entry for why
  it's bounded to ~3s per leaf rather than compounding across every LLM call
  a leaf's pipeline could make. Net effect: the full suite runs in ~50s
  instead of a few seconds, but every test still passes deterministically
  and no existing test file needed to change. If this becomes a real
  problem, the fix would be adding a fast local-provider reachability probe
  before the first `.complete()` call per leaf (mirroring `api/app.py`'s
  `_reachable()` health-check helper) — deliberately not done here since it
  would add a code path not exercised by anything in Phase 4's own scope.
- The drafter's LLM chunk-relevance grading and query-rewrite calls both
  resolve the "drafter" role model (there is no separate role for them in
  `llm/settings_store.ROLES`) — see DECISIONS.md.
- No live-LLM/live-Qdrant Phase 4 test was run this session (no Docker, no
  GPU, no API keys) — every test above mocks `resolve_model` and
  `hybrid_search`/`get_qdrant_client` at the boundary. A real end-to-end
  draft (real provider, real ingested project) is flagged for Phase 8's
  real-LLM pass.
- Frontend manual/visual checks are unexercised (no browser this session);
  flag for a human pass before Phase 8's full manual sweep.

## Phase 5 — Verifier, continuity, compliance loop

Run: `cd draftforge && uv run pytest -q`. All Phase 5 tests are hermetic
(LLM roles, `hybrid_search`/`retrieve_chunks`, and the store are injected/
mocked; no Docker, GPU, or API keys).

| Test | Purpose | Status |
| --- | --- | --- |
| `test_graph_verifier.py::test_extract_claims_only_returns_cited_sentences` | Only cited sentences become claims | PASS |
| `test_graph_verifier.py::test_supported_claim_passes` | NLI "supported" verdict → section ok | PASS |
| `test_graph_verifier.py::test_unsupported_claim_is_flagged_with_feedback` | Unsupported claim flagged + feedback string (#3) | PASS |
| `test_graph_verifier.py::test_bad_key_backstop_rejects_key_not_in_store` | Key not in bibliography rejected (#2 backstop) | PASS |
| `test_graph_verifier.py::test_claim_with_no_supporting_chunk_is_unsupported_without_an_llm_call` | No chunk → unsupported, no LLM call (#3) | PASS |
| `test_graph_verifier.py::test_section_with_no_citations_is_ok_and_makes_no_llm_call` | No citations → ok, no call | PASS |
| `test_graph_verifier.py::test_verifier_degrades_gracefully_when_model_raises` | Verifier model failure never crashes the run | PASS |
| `test_graph_verifier.py::test_load_valid_bibkeys_reads_store_or_returns_none` | Bibliography store load / None when missing | PASS |
| `test_graph_compliance.py` (4) | Code-based compliance: compliant passes, over-limit/heading-depth caught, blocking subset + feedback (#4) | PASS |
| `test_graph_continuity.py` (6) | Boundary-only patch (bodies unchanged, #1/#6), change/no-op/failure handling | PASS |
| `test_review_node.py::test_supported_section_completes_without_redraft` | Clean section → done, 0 redrafts | PASS |
| `test_review_node.py::test_unsupported_section_redrafts_then_flags_for_human` | Bounded redraft (max 2) then flag | PASS |
| `test_review_node.py::test_compliance_violation_routes_back_to_drafter_then_passes` | Compliance violation → redraft → passes | PASS |
| `test_review_node.py::test_missing_draft_file_is_flagged` | No draft file → flagged | PASS |
| `test_review_node.py::test_review_node_end_to_end_writes_review_and_continuity` | review.json + section_status + continuity produced | PASS |
| `test_kill_resume.py::test_crash_in_review_resumes_without_redrafting` | Kill-and-resume drill: resume from checkpoint, drafter NOT re-run (#5) | PASS |
| `test_kill_resume.py::test_resume_run_before_approval_is_rejected` | Crash-recovery refuses an unapproved run | PASS |
| `test_kill_resume.py::test_resume_run_on_completed_run_is_a_noop` | Resuming a completed run is a no-op | PASS |
| `test_redraft_api.py` (4) | GET /review, POST redraft, POST resume (404/409/round-trip) via TestClient | PASS |

- Manual UI check (deferred to Phase 8, no browser this session): Review screen
  in BOTH light and dark — flagged citations (claim / source chunk / verdict),
  Accept + Redraft actions, continuity boundary diffs.
- The kill-and-resume drill here simulates the crash by raising inside the
  review node (drafter already checkpointed) rather than a real SIGKILL; the
  checkpoint/resume machinery exercised is identical. A real process-kill drill
  is flagged for Phase 8.

---

## Phase 6 — Rendering

Run: `cd draftforge && uv run pytest -q`. All hermetic tests mock the LLM
roles/hybrid-search (same fixture pattern as `test_redraft_api.py`) and the
Pandoc subprocess itself (`shutil.which` + `render.pandoc.run_pandoc`) — no
Docker, GPU, API keys, or real `pandoc`/LaTeX install required. One test is
genuinely optional and skips cleanly on a machine without `pandoc` on `PATH`.

### Automated — backend (`uv run pytest`)

| Test | Purpose | Status |
| --- | --- | --- |
| `test_render_assemble.py::test_sections_appear_in_outline_order_with_correct_heading_depth` | Outline walk order == document order; heading depth == `len(parent_path)+1` | PASS |
| `test_render_assemble.py::test_leaf_body_is_spliced_verbatim_and_citation_markers_preserved` | Each leaf's on-disk body is spliced unmodified; `[@key]`/`[@k1;@k2]` markers survive | PASS |
| `test_render_assemble.py::test_missing_draft_file_gets_a_visible_placeholder_not_a_crash` | A leaf with no draft file (flagged/incomplete) gets a visible placeholder, assembly doesn't raise | PASS |
| `test_render_assemble.py::test_references_leaf_is_skipped_entirely` | A leaf whose spec section has `word_range: null` ("references") is skipped entirely -- no heading, no body -- Pandoc/citeproc generates the bibliography instead | PASS |
| `test_render_assemble.py::test_figure_captions_renumbered_coherently_across_sections` | Figure captions guessed wrong by parallel per-leaf drafting are renumbered into one coherent document-wide sequence | PASS |
| `test_render_assemble.py::test_chapter_scoped_caption_numbering_resets_per_chapter` | `"chapter_decimal"` table numbering (university_thesis) resets at each top-level outline node | PASS |
| `test_render_assemble.py::test_assemble_run_raises_on_empty_outline` | `assemble_run` raises `AssembleError` (not a silent empty doc) when there's no outline yet | PASS |
| `test_render_assemble.py::test_assemble_run_end_to_end_sets_front_matter_and_number_sections` | `assemble_run`'s `AssembledDocument` sets `number_sections` from the spec and threads `run_config.front_matter` through | PASS |
| `test_render_assemble.py::test_front_matter_fields_default_to_bracketed_placeholders` | Missing title-page fields get a visible `[FIELD]` placeholder, never silently blank | PASS |
| `test_render_assemble.py::test_front_matter_underscore_fields_get_a_hyphenated_alias` | `submission_date` is aliased to `submission-date` too (bridges the spec/template naming mismatch) | PASS |
| `test_render_assemble.py::test_front_matter_supplied_abstract_is_used_verbatim` | A supplied abstract passes through unchanged (no placeholder override) | PASS |
| `test_render_api.py::test_unknown_run_404s_on_every_render_endpoint` | `GET artifacts`, `GET artifacts/{name}`, `POST render` on an unknown run all 404 | PASS |
| `test_render_api.py::test_artifacts_before_completion_is_409` | Both artifact endpoints 409 before the run reaches "completed" | PASS |
| `test_render_api.py::test_unknown_artifact_name_404s` | A name outside the `ARTIFACT_SPECS` whitelist 404s | PASS |
| `test_render_api.py::test_path_traversal_name_is_rejected` | Hostile-shaped names (`../../../etc/passwd`, percent-encoded traversal) 404 via whitelist-only lookup, never leak file content | PASS |
| `test_render_api.py::test_full_run_render_list_and_download_round_trip` | End-to-end: approve -> `POST render` -> `GET artifacts` lists all 5 whitelisted names with correct availability/size/content-type -> `GET artifacts/{name}` round-trips docx/bibliography/decisions bytes; the Phase 7 eval-report slot 404s (not yet written) | PASS |
| `test_render_api.py::test_get_artifacts_lazily_renders_on_first_call_without_an_explicit_post` | `GET artifacts` alone (no prior `POST render`) triggers the best-effort lazy render for a completed run | PASS |
| `test_render_api.py::test_render_is_idempotent_and_does_not_re_shell_out_via_get` | Once artifacts exist, further `GET` calls make ZERO additional `run_pandoc` calls (lazy trigger only fires on an empty artifacts dir) | PASS |
| `test_render_api.py::test_render_without_pandoc_is_a_502_but_bibliography_still_available` | `pandoc` entirely missing -> `POST render` is a 502 `RenderError`, but the already-copied bibliography artifact remains listed/available | PASS |
| `test_render_api.py::test_live_pandoc_renders_a_real_minimal_docx` | OPTIONAL: a real `pandoc` binary renders a trivial doc to a genuine (non-empty, zip-magic `PK`) `.docx` | SKIPPED (no `pandoc` on `PATH` in this sandbox; verified to skip cleanly via `@pytest.mark.skipif`) |

Run: `cd draftforge && uv run pytest -q` -> **165 passed, 2 skipped** (Phase
0-5's 146 + Phase 6's 19, one of which skips; the other skip is Phase 1's
optional live-GROBID test).

### Build / smoke checks

| Check | Purpose | Status |
|---|---|---|
| `npx tsc -b` | Frontend (incl. new `Downloads.tsx` + `api/client.ts` Artifact/RenderResponse additions) type-checks with no errors | PASS |
| `npm run build` | Frontend still builds for production | PASS |
| `npm run test -- --run` | Phase 0's 2 frontend tests still pass unmodified (no new Vitest tests added — Downloads' data-fetching/render-trigger logic is a thin wrapper over the same API contract already covered end-to-end by `tests/test_render_api.py`, same precedent as Outline Review/Run Dashboard/Review) | PASS |

### Manual UI checks — NOT RUN this session (no browser available)

| Check | Purpose | Status |
|---|---|---|
| Downloads — light mode | Run-id input, artifact rows (docx/pdf/bibliography/decisions/eval-report-slot), Render button, flagged-sections caveat all readable/usable | NOT RUN |
| Downloads — dark mode | Same, dark theme tokens applied, no unreadable contrast | NOT RUN |
| Render a real run end-to-end with real Pandoc + LaTeX installed | Confirm `draft.docx` opens in Word/LibreOffice with correct headings/numbering/citations, `draft.pdf` opens and matches the spec's template | NOT RUN |
| Click "Render" on a run with a flagged section, then download | Confirm the flagged-sections caveat banner appears and the rendered document still includes that section's text as-is | NOT RUN |

### Known gaps / notes

- No real `pandoc`/LaTeX install was available this session (confirmed via
  `shutil.which`) — every render test mocks the subprocess boundary; the one
  optional live test (`test_live_pandoc_renders_a_real_minimal_docx`) is
  written and gated correctly but was never actually exercised against a
  real binary. Flag for Phase 8 (or any machine with `pandoc`+`texlive`
  installed) to run `uv run pytest tests/test_render_api.py -k live -q` and
  confirm the real path.
- No real 50+ page draft has been rendered end-to-end; the hermetic tests use
  a handful of short synthetic leaves. A real multi-section render (docx
  opens cleanly, headings/numbering/citations match the spec visually) is
  flagged for Phase 8's full manual sweep, per spec.md Phase 6's own
  acceptance test ("rendered docx opens, headings/numbering/citations match
  spec").
- Cross-reference resolution is limited to figure/table CAPTION numbers (see
  DECISIONS.md) — in-prose references to a figure/table by number are not
  tracked or rewritten, since no cross-reference marker syntax exists
  anywhere in Phases 3-5's output.
- No abstract-drafting step exists anywhere in Phases 0-5; front matter's
  `abstract` field is always either a caller-supplied `run_config` value or a
  visible placeholder. See DECISIONS.md.
- Real `IEEEtran.cls`/official CSL styles are still the Phase 2 placeholders
  (`specs/csl/*.csl`, `templates/*.tex`) — Phase 6 renders against whatever
  Phase 2 shipped; swapping in the real files needs no Phase 6 code changes
  per Phase 2's own DECISIONS.md note.
- Frontend manual/visual checks are unexercised (no browser this session);
  flag for a human pass before Phase 8's full manual sweep.

---

## Phase 7 — Eval harness

Run: `cd draftforge && uv run pytest -q`. All Phase 7 tests are hermetic
(no Docker, GPU, or API keys) — retrieval uses deterministic synthetic
embeddings against Qdrant's real `:memory:` engine; citation faithfulness
injects a fake NLI judge; compliance and cost/latency need no LLM at all.
See README.md's Evaluation section for what's real vs. synthetic-fixture vs.
LLM/GPU-gated in the actual generated report.

### Automated — backend (`uv run pytest`)

| Test | Purpose | Status |
| --- | --- | --- |
| `test_evals_retrieval.py::test_recall_at_k_hand_computed` | `recall_at_k` matches a hand-computed value on a toy ranked list | PASS |
| `test_evals_retrieval.py::test_recall_at_k_empty_relevant_set_is_zero` | Empty relevant set -> recall 0.0, not a division error | PASS |
| `test_evals_retrieval.py::test_reciprocal_rank_hand_computed` | `reciprocal_rank` matches hand-computed values (rank 1, rank 3, not found) | PASS |
| `test_evals_retrieval.py::test_known_relevant_chunk_in_top5_known_mrr_toy_case` | A 2-query toy case's averaged MRR (2/3) matches hand computation | PASS |
| `test_evals_retrieval.py::test_load_fixture_reads_the_shipped_labeled_set` | The shipped labeled fixture (`tests/fixtures/evals/retrieval_fixture.json`) loads with every query carrying known-relevant chunk ids | PASS |
| `test_evals_retrieval.py::test_all_three_ablation_configs_produce_a_row` | `run_ablation` returns exactly one row each for dense_only/sparse_only/hybrid, all metrics in [0,1], recall@10 >= recall@5 | PASS |
| `test_evals_retrieval.py::test_known_relevant_chunk_lands_in_top5_for_hybrid` | Hybrid config: a known-relevant chunk lands in the top 5 for every labeled query (recall@5 == MRR == 1.0), via real `hybrid_search`/RRF against Qdrant `:memory:` | PASS |
| `test_evals_retrieval.py::test_run_retrieval_eval_default_is_clearly_labeled_synthetic` | The default (no `live=True`) result is labeled `synthetic=True` with a note explaining why | PASS |
| `test_evals_compliance.py::test_compliant_draft_has_zero_violations_for_both_shipped_specs` | `build_compliant_draft` + `validate_document` produce zero violations for both `ieee_report` and `university_thesis` | PASS |
| `test_evals_compliance.py::test_run_compliance_eval_ships_at_least_three_drafts_per_spec` | >=3 fixture drafts (actually 4) per shipped spec, per spec.md's requirement | PASS |
| `test_evals_compliance.py::test_run_compliance_eval_pass_rate_reflects_some_pass_some_fail` | Pass rate is strictly between 0% and 100% for both specs (not trivially all-pass or all-fail) | PASS |
| `test_evals_compliance.py::test_each_expected_violation_code_is_produced_by_its_own_variant` | `over_word_limit`/`missing_required_section`/`malformed_citation` each trigger exactly their named violation code | PASS |
| `test_evals_compliance.py::test_violation_code_counts_sum_matches_total_violations_across_drafts` | Per-spec violation-code-count totals reconcile with the sum of per-draft violation counts | PASS |
| `test_evals_compliance.py::test_only_error_severity_violations_count_against_pass_rate` | Pins the current "every violation is ERROR severity" assumption the pass-rate calculation relies on | PASS |
| `test_evals_citation_faithfulness.py::test_faithfulness_rate_computed_correctly_with_injected_judge` | Faithfulness rate (2/3) matches a hand-computed value against an injected fake NLI judge; exact NLI call count verified (one batched call per section) | PASS |
| `test_evals_citation_faithfulness.py::test_sampling_respects_sample_size_and_is_deterministic_for_a_fixed_seed` | Sampling honors `sample_size` and is reproducible for a fixed seed | PASS |
| `test_evals_citation_faithfulness.py::test_no_judge_configured_reports_not_run_never_a_fabricated_rate` | No `provider`/`live=True` -> `status="not_run"`, `faithfulness_rate is None` (never fabricated) | PASS |
| `test_evals_citation_faithfulness.py::test_draft_with_no_citations_reports_not_run` | A draft with zero cited claims -> `status="not_run"`, not a crash or a vacuous 100% | PASS |
| `test_evals_citation_faithfulness.py::test_for_run_with_no_llm_configured_reports_not_run` | The run-level entrypoint against a real (fixture) run dir -> "not run", still counts claims | PASS |
| `test_evals_citation_faithfulness.py::test_for_run_unknown_run_id_reports_not_run` | Unknown run_id -> "not run" with a clear reason, not a crash | PASS |
| `test_evals_citation_faithfulness.py::test_for_run_with_no_drafted_sections_reports_not_run` | A run with no `sections/` dir yet -> "not run" | PASS |
| `test_evals_citation_faithfulness.py::test_spot_check_markdown_table_and_csv_export` | The human-spot-check markdown table and CSV export both round-trip the sampled verdicts | PASS |
| `test_evals_cost_latency.py::test_compute_cost_latency_per_run_totals` | Run-level tokens/cost (from the last `cumulative_*` event) and wall-clock (max ts - min ts) match a hand-built fixture | PASS |
| `test_evals_cost_latency.py::test_compute_cost_latency_per_section_breakdown` | Per-section tokens and wall-clock match the hand-built fixture for two overlapping sections | PASS |
| `test_evals_cost_latency.py::test_compute_cost_latency_empty_events_is_all_zero_not_an_error` | No events -> an all-zero result, not an exception | PASS |
| `test_evals_cost_latency.py::test_compute_cost_latency_falls_back_to_summing_when_no_cumulative_field` | Missing `cumulative_tokens`/`cumulative_cost_usd` -> falls back to summing `tokens_used`/`cost_usd` | PASS |
| `test_evals_cost_latency.py::test_cost_latency_for_run_reads_a_real_events_jsonl_file` | `cost_latency_for_run` reads a real on-disk `events.jsonl` via `graph.drafter.read_events`, reused unmodified | PASS |
| `test_evals_cost_latency.py::test_cost_latency_for_run_missing_file_is_all_zero_not_an_error` | Missing `events.jsonl` -> all-zero result, not an error | PASS |
| `test_evals_report.py::test_build_report_contains_all_four_sections_with_no_run_id` | The rendered markdown contains all four `## N.` section headers, real retrieval+compliance numbers, and honest "not run" text for the run-scoped sections | PASS |
| `test_evals_report.py::test_build_report_never_fabricates_live_numbers_when_live_is_off` | `live=False` -> the report says so explicitly and never claims a live retrieval number | PASS |
| `test_evals_report.py::test_build_report_degrades_gracefully_for_an_unknown_run_id` | An unknown `--run` id doesn't crash `build_report` | PASS |
| `test_evals_report.py::test_build_report_with_a_real_run_includes_cost_latency_numbers` | A real fixture run's token count appears verbatim in the rendered report | PASS |
| `test_evals_report.py::test_write_report_writes_global_path_always` | `write_report` always writes `{data_dir}/evals/eval_report.md` | PASS |
| `test_evals_report.py::test_write_report_also_writes_run_artifacts_path_when_run_given` | With a known `run_id`, `write_report` ALSO writes `data/runs/{run_id}/artifacts/eval_report.md`, confirmed to equal `render.pandoc.artifacts_dir(run_id)/"eval_report.md"` (the exact path the Phase 6 artifact whitelist serves) | PASS |
| `test_evals_report.py::test_write_report_skips_run_artifacts_path_for_unknown_run` | An unknown `run_id` -> only the global path is written, not an error | PASS |

Run: `cd draftforge && uv run pytest -q -k evals` -> **35 passed**.
Full suite: `cd draftforge && uv run pytest -q` -> **200 passed, 2 skipped**
(Phase 0-6's 165 + Phase 7's 35; the two skips are Phase 1's optional live-GROBID
test and Phase 6's optional live-Pandoc test, both unrelated to Phase 7).

### Manual / smoke checks

| Check | Purpose | Status |
| --- | --- | --- |
| `uv run python -m draftforge.evals` | Runs the full harness standalone, writes `data/evals/eval_report.md`, prints the report to stdout | PASS |
| `uv run python -m draftforge.evals --run <run_id>` | Also writes `data/runs/{run_id}/artifacts/eval_report.md`; confirmed via a hand-fabricated run (fake `events.jsonl` + one section file) that the report's cost/latency numbers match | PASS |
| `GET /runs/{id}/artifacts` reflects the eval report once written | Confirmed by construction (`list_artifacts` marks `available` purely via `path.exists()` on the exact path `write_report` writes to) — the existing Phase 6 `test_render_api.py` suite already asserts this artifact 404s before the file exists; no Phase 7 code touches `routes_runs.py` | PASS (via existing Phase 6 test + this phase's `test_write_report_also_writes_run_artifacts_path_when_run_given`) |

### Known gaps / notes

- **Retrieval numbers in the default report are SYNTHETIC**, not a
  measurement of real BGE-M3/BM25 quality — no GPU or model weights in this
  sandbox. See README.md's Evaluation section and DECISIONS.md for the full
  rationale. The `DRAFTFORGE_LIVE=1` real-embedding path exists and is
  wired up but has never actually been executed.
- **Citation faithfulness has never produced a real rate this session** — no
  LLM provider is configured/reachable. The harness reports this honestly
  (`status="not_run"`) every time it's run here; only the hermetic
  fake-judge test proves the math.
- **Compliance numbers ARE real** (no LLM/GPU involved) and were actually
  computed this session: both shipped specs show a 1/4 (25%) pass rate
  across their 4 fixture drafts (1 deliberately compliant, 3 deliberately
  broken). This eval also surfaced a real, previously-unexercised gap in
  `validate_document`'s tree-completeness logic — see DECISIONS.md.
- **Cost/latency is real whenever a run is given** — pure log aggregation,
  no LLM/GPU. Verified against both a hand-built fixture and a real
  (fabricated-for-testing) `events.jsonl` file this session; never verified
  against a genuine multi-section LLM-drafted run (no reachable provider in
  this sandbox), which is the same gap Phase 4/5's own TESTING.md entries
  already flag for Phase 8's real-LLM pass.
- No frontend changes were needed or made this phase — `Downloads.tsx`
  already lists the `eval_report.md` row (Phase 6); it flips from
  unavailable to available purely because the file now exists on disk once
  the harness has been run for a given run_id.
