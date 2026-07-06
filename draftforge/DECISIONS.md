# Design decisions log

One-line-rationale log of ambiguous calls, in chronological order. Later
phases: add to the bottom, don't rewrite history.

## Phase 0

- **Project root is `draftforge/` subdir.** Keeps this app fully disjoint
  from `ResearchAgent/` and the existing repo-root README, per instructions
  not to touch either.

- **All backend routes mounted under `/api`.** Initially tried proxying the
  API's bare route prefixes (`/settings`, `/projects`, `/runs`, `/health`)
  directly, matching the routers' own prefixes. That collides with the
  frontend's client-side route of the same name — a full page load/refresh
  of the Settings screen (path `/settings`) and a fetch to
  `/settings/models` both matched the Vite proxy's `/settings` rule, so the
  page load 404'd against the backend instead of falling through to the SPA
  shell. Fixed by mounting the whole API under one `/api` prefix
  (`/api/health`, `/api/settings/models`, `/api/projects`, `/api/runs/...`)
  and proxying only that single prefix in `vite.config.ts`. No collision is
  possible by construction, and it scales cleanly as more page routes are
  added in later phases.

- **`ModelInfo` exists in two places on purpose.** `llm/registry.py:ModelInfo`
  is the internal config representation (mirrors `models.yaml`).
  `api/models.py:ModelInfo` is the API response DTO — a superset that adds
  `api_key_present` (computed from env at request time). Kept distinct so the
  registry stays a pure config loader with no API/env-reading concerns.

- **Provider construction is per-model-entry, not per-provider-class.**
  `ModelRegistry.get_provider_for(model_name)` builds and caches one provider
  instance per `models.yaml` entry (keyed by entry name), passing that
  entry's `cost_per_mtok_in/out` into the constructor. This keeps
  `LLMProvider.complete()`'s signature exactly as specified
  (`complete(messages, *, model, max_tokens, temperature, json_mode)`) with
  no extra cost-rate parameter — the instance already knows its own cost.

- **Anthropic JSON mode is instruction + `{` prefill.** The Anthropic
  Messages API has no native `response_format`; `json_mode=True` appends a
  "respond with JSON only" instruction to the system prompt and prefills the
  assistant turn with `"{"` to bias the model toward valid JSON, then
  re-prepends the prefill to the returned text if the model's own completion
  didn't already start with it.

- **Cheapest-model default for non-strong roles.** `settings_store`'s seed
  defaults pick the model with the lowest `cost_per_mtok_in + cost_per_mtok_out`
  (ties broken toward `local: true`) for `drafter`, `critic`, and
  `continuity_editor`; the registry's `default: true` model (Claude Sonnet)
  is used for `planner` and `citation_verifier`. This matches the spec's
  "strong model for planner+verifier, cheaper/local permitted elsewhere."

- **`resolve_model` lives in `llm/settings_store.py`, not `llm/registry.py`.**
  It needs both the role→model mapping (settings_store) and model→provider
  construction (registry); putting it in settings_store avoids a circular
  import (registry never needs to know about settings_store).

- **`docling` is a hard dependency in `pyproject.toml` per the task
  instructions**, even though it pulls in torch/transformers and is not
  imported anywhere in Phase 0 code. Verified `uv sync --extra dev` installs
  cleanly with it included and Phase 0 tests do not import it.

- **Health check probes `{qdrant_url}/healthz` and `{grobid_url}/api/isalive`**
  — Qdrant's REST API exposes `/healthz`; GROBID exposes `/api/isalive`
  (returns "true"/"false" as plain text, but any non-5xx response is treated
  as reachable here — Phase 0 doesn't need to parse the body).

- **SQLite settings store re-opens a connection per call** rather than
  holding one open across the process lifetime. Simpler and safe for a
  single-user local tool with infrequent settings writes; can revisit if
  profiling ever shows it matters.

## Phase 1

- **Token counting is a whitespace word-count heuristic, not a real BPE
  tokenizer.** `chunker.count_tokens` returns `len(text.split())`. Real BPE
  tokenizers (tiktoken, etc.) produce ~1.2-1.3x more tokens than words for
  English text, so `token_count` is an undercount in absolute terms — but
  the chunker only needs it for *relative* sizing decisions (is this piece
  too big/small), so a documented word-count proxy is good enough and avoids
  adding `tiktoken` as a new dependency. Swap in a real tokenizer later if a
  phase needs exact context-window accounting.

- **Chunker absorbs small trailing pieces rather than emitting tiny
  fragments.** Within a section, if the remaining tail after a full-size
  window is smaller than `min_tokens`, it's folded into the current chunk
  (which may then run a bit over `max_tokens`) instead of becoming its own
  sub-`min_tokens` chunk. Respecting section boundaries takes priority over
  the size range, and a slightly-oversized chunk retrieves better than a
  near-empty one.

- **Bibkey scheme: `{first-author-surname-lowercased}{year}`, disambiguated
  with a trailing `a`/`b`/`c`... on collision** (e.g. two 2020 Smith papers
  become `smith2020` and `smith2020a`). No year -> `nd`. No author -> `anon`.
  Chosen for human-readability in citation markers (`[@smith2020]`) over a
  hash-based key; collisions are rare enough per-source that a simple
  counter suffices.

- **GROBID TEI section pages come from `<pb n="...">` elements encountered
  during the depth-first walk of `<body>`**, not from GROBID's `@coords`
  attribute (which requires `teiCoordinates` params we don't request). A
  nested `<div>` inherits the page most recently seen among its ancestors'
  `<pb/>` siblings. This is an approximation (a section's *first* paragraph
  page, not necessarily where every paragraph in it falls) but is exactly
  what GROBID's default `processFulltextDocument` output gives us for free.

- **Docling fallback goes through Docling's markdown export, not its
  internal document-item tree.** `docling_fallback.py` calls
  `result.document.export_to_markdown()` and then splits on ATX headings
  (`_markdown_to_sections`, a pure function). Docling's internal item API
  has changed across versions; markdown export is the most stable surface
  and lets the heading-splitting logic be unit-tested without Docling
  installed/imported at all.

- **Qdrant collection-per-project, not a shared collection with a
  `project_id` payload filter.** `store.collection_name` returns
  `draftforge_project_{project_id}`. A single-user local tool never has more
  than a handful of projects; per-project deletion is a plain
  `delete_collection`, and every query is scoped by construction (no risk of
  a missing filter leaking another project's chunks into results).
  `project_id` is still duplicated into each point's payload for debugging.

- **Hybrid search fuses via Qdrant's Query API with RRF
  (`FusionQuery(fusion=Fusion.RRF)`)**, prefetching top candidates from the
  dense (`bge-m3`) and sparse (BM25) named vectors separately, each filtered
  identically, then reranking the union. Chosen over a hand-rolled weighted
  score combination because RRF needs no score-scale calibration between
  dense cosine similarity and BM25 scores. Tests run this against
  `QdrantClient(":memory:")` — Qdrant's real in-process engine — instead of
  a hand-rolled fake, so the fusion code path under test is the same one
  that runs against a real server.

- **Project/source metadata persisted as a single JSON file**
  (`data/projects.json`), not SQLite. Phase 1's access pattern is
  read-modify-write the whole small dict on every status transition from a
  single process; JSON keeps `routes_projects.py` dependency-free and
  trivially inspectable, at the cost of no concurrent-writer safety (fine
  for a single-user local tool with a threading lock around read-modify-write).
  Per-project CSL-JSON bibliographies live alongside at
  `data/projects/{project_id}/bibliography.json`, merged across sources
  (first-seen bibkey wins on collision, logged).

- **Embedder model loads are lazy module-level singletons, injectable via a
  `model=` kwarg on `embed_dense`/`embed_sparse`.** This is the seam tests
  use to avoid ever loading BGE-M3/BM25 for real; `reset_models()` exists to
  drop the cached singletons if a process needs to reload (e.g. after a
  config change), though nothing calls it yet.

- **`GET/POST /api/projects/{id}/sources` DTOs are unchanged from the
  Phase 0 placeholders** (`Project`, `ProjectCreate`, `SourceStatus` in
  `api/models.py`) — no new fields were needed. `SourceStatus.status`'s
  existing literal (`queued/parsing/chunking/embedding/done/error`) already
  models per-file progress as discrete pipeline stages, which the frontend
  maps to a percentage bar client-side rather than the API tracking a
  numeric progress field.

## Phase 2

- **Abstract lives in `front_matter`, not in the `sections` tree.** spec.md's
  own "Format spec schema" description puts "abstract word limit" under
  front-matter requirements, so `FrontMatterSpec.abstract_required` /
  `abstract_word_range` own it. The English description of `ieee_report.json`
  ("abstract, intro, related work, ...") is read as describing the
  conceptual document flow, not a literal instruction to duplicate abstract
  as a numbered section.

- **`validate_section` is per-leaf, text-only; tree completeness lives in
  `validate_document`.** Per spec.md's non-negotiable #1 ("no agent ever
  sees the full document... assembly is deterministic"), only true leaf
  sections get drafted text; parent/container nodes in the section tree
  (e.g. "Method" with subsections "Data"/"Approach") typically have no text
  of their own. So `validate_section(markdown, section_spec, context)`
  checks only what a single leaf's own Markdown can tell you: word count,
  any headings *it chose to inline*, heading depth/numbering, citation
  marker form, and captions. Required-subsection-of-the-whole-tree
  presence is a document-level concern (which completed sections exist at
  all), so it's checked once in `validate_document` by walking
  `FormatSpec.sections` against the set of provided section ids -- a
  missing parent short-circuits (no cascading duplicate violations for
  each of its still-absent children).

- **`ValidationContext` carries format-wide rules into per-section calls.**
  `validate_section`'s signature only takes one `SectionSpec`, but heading
  numbering style, citation style, and caption rules are properties of the
  whole `FormatSpec`, not of one node. `validate_document` builds one
  `ValidationContext` per document and reuses it across every section, so
  Phase 4/5 (drafting one leaf at a time) can build the same context once
  per run and pass it to every `validate_section` call as each leaf
  completes.

- **Word count = a simple alnum-token regex (`[A-Za-z0-9][A-Za-z0-9'-]*`),
  excluding heading lines.** Deliberately not a tokenizer/NLP library --
  boring and exactly reproducible is more valuable here than perfect
  linguistic accuracy, and it's what the drafter's own word-target
  reasoning will approximate too. A `[@bibkey]` marker contributes at most
  one token (the key), a small, accepted overcount vs. true prose word
  count.

- **Heading numbering representation: a leading `\d+(\.\d+)*` prefix,
  parsed off the heading text and stored on `Heading.number`/`.title`
  separately.** Two spec-wide styles are supported -- `"decimal"` (prefix
  required) and `"none"` (prefix forbidden) -- matching spec.md's own
  example ("decimal / none"). Roman-numeral/lettered IEEE-style numbering
  was considered and dropped for Phase 2 scope; both example specs use
  `"decimal"` and differ instead in caption numbering (`ieee_report` uses
  flat `"decimal"` Fig./Table numbers, `university_thesis` uses
  `"chapter_decimal"`, e.g. "Figure 2.3").

- **Citation marker validation checks FORM only, via a hand-rolled
  regex, not a citeproc/Pandoc parse.** Any `[...]` bracket containing an
  `@` is treated as a citation attempt; it's valid iff every `;`-separated
  entry matches `@[A-Za-z0-9][A-Za-z0-9_:.\-]*` (Pandoc's own citation key
  grammar, permissively). Whether the key actually exists in the
  bibliography store is explicitly out of scope here (that's the Phase 5
  citation verifier's job) -- constraint #2 in spec.md draws exactly this
  line.

- **Caption rules use a line-based heuristic, not a Markdown AST:** a
  figure caption is expected on the next non-blank line after a
  `![...](...)` image; a table caption is expected on the nearest
  non-blank line *before* the table's header row (common academic
  convention: figure captions below, table captions above). Multi-line
  caption paragraphs are not supported -- only the single adjacent line is
  checked -- documented as a known simplification; a real drafter is
  expected to keep captions to one line anyway.

- **CSL handling: real CSL files are not vendored.** `specs/csl/ieee.csl`
  and `specs/csl/apa.csl` are minimal, clearly-labeled placeholder styles
  (enough to exercise `--csl` wiring end-to-end) with a comment pointing at
  the real upstream style in the `citation-style-language/styles` repo to
  drop in at the same path before a real render. No spec/template changes
  are needed when the placeholder is swapped for the real file.

- **Pandoc templates are text-based (LaTeX `.tex` + a pandoc "defaults"
  `.yaml`), not a binary `--reference-doc` `.docx`.** The task explicitly
  asked for diffable, checked-in templates. `templates/ieee_report.tex` /
  `templates/university_thesis.tex` are minimal hand-written Pandoc
  templates (not the full vendored `IEEEtran.cls`, which is a third-party,
  binary-adjacent distribution file) -- comments in each `.tex`/`.yaml`
  explain how to swap in the real IEEE class file later. DOCX rendering in
  Phase 6 is expected to fall back to Pandoc's own default docx styling
  (still gets `citeproc`/`csl`/`number-sections` from the defaults file);
  a binary reference-doc can be added later as a gitignored, regenerable
  asset if visual fidelity beyond Pandoc's defaults turns out to matter.

- **`university_thesis.yaml` requires `top-level-division: chapter`.**
  Without it, Pandoc maps level-1 Markdown headings (the spec's chapters)
  to LaTeX `\section`, which doesn't match the thesis template's
  `\chapter`-based title page/TOC flow. Documented inline in both the
  `.yaml` and `.tex` files so Phase 6 doesn't have to rediscover it.

## Phase 3

- **Interrupt mechanism: static `interrupt_after=["planner"]`, not the
  dynamic `interrupt()` function.** Phase 3's human-in-the-loop step is a
  plain "pause after planning, resume on approval" action; the student's
  outline edits are applied straight to the checkpointed state via
  `graph.update_state(...)`, so nothing needs to be threaded back *into* a
  running node. The static form is simpler, and critically means an
  edit/approve cycle never re-executes (and re-bills) the planner's LLM
  call, which `interrupt()`'s node-rerun-on-resume semantics would risk if
  used naively here. See `graph/build_graph.py`'s module docstring.

- **Checkpointer: one `SqliteSaver`-backed DB at
  `{data_dir}/runs.sqlite`, thread_id == run_id, a fresh
  `sqlite3.Connection` opened per call and closed afterward.** Same
  "reopen per call" choice already made for the Phase 0 settings store
  (see that entry above) — and it turns out to double as the resumability
  mechanism for free: every `get_state`/`apply_outline_edits`/`resume` call
  already builds a brand-new `CompiledStateGraph` from the on-disk file, so
  there is no in-memory graph object whose loss a crash could expose.

- **Run metadata lives in a separate lightweight JSON registry
  (`{data_dir}/runs.json`), not folded into the LangGraph checkpoint.**
  Mirrors `routes_projects.py`'s `projects.json` pattern. The registry
  tracks only coarse status (`queued`/`planning`/`awaiting_outline_approval`/
  `completed`/`error`) + `project_id`/`format_spec_id`/`run_config`/`error`,
  so `GET /api/runs/{id}` never has to open the (heavier) graph sqlite DB
  just to answer "does this run exist / what's its status". The
  checkpointed `RunState` remains the single source of truth for
  everything outline-related (`outline`, `leaf_briefs`, `document_state`).

- **Outline edits apply via `update_state`, never by replaying the planner
  node.** `PATCH /runs/{id}/outline` calls
  `build_graph.apply_outline_edits(run_id, outline)`, which patches the
  paused checkpoint's `outline`/`leaf_briefs` fields directly. This is also
  why `resolve_model("planner")` is only ever called once per run (inside
  the node) rather than once per edit.

- **Planner output is always structurally repaired against the
  `FormatSpec`, never rejected outright.** A deterministic "skeleton"
  outline is built directly from `FormatSpec.sections` (title/word-range
  midpoint per leaf); the LLM's candidate outline is merged onto it node by
  node — required sections missing from the candidate, or leaf
  `target_words` outside the section's `word_range`, are filled/clamped
  from the skeleton and logged as violations rather than failing the run.
  This guarantees `state["outline"]` always has the exact shape of the
  spec's required-section tree, which the API/frontend and (later) Phase
  4's drafter fan-out can all rely on unconditionally.

- **A leaf outline node is exactly a `SectionSpec` with no `subsections`**
  (no extra planner-introduced nesting beyond the format spec's own tree,
  for Phase 3 scope). `graph/planner.py::flatten_leaf_briefs` walks the
  merged outline depth-first collecting these into `SectionBrief`s in
  document order — the exact list Phase 4's drafter fans out over.
  `format_section_id` on each brief is therefore always equal to its own
  `id` today; the field exists as a separate name in case a later phase
  wants the planner to introduce sub-leaf structure that no longer maps
  1:1 to a `FormatSpec` node.

- **A leaf whose `word_range` is `None` (e.g. `references`) gets
  `target_words = 0`**, not a generic default — a bibliography is
  auto-generated by Pandoc/CSL in Phase 6, not drafted prose, so a
  nonzero default word target would be actively misleading in the
  Outline Review UI.

- **Retrieval-sample failures are swallowed per-leaf and at the
  client-construction level**, both logged as warnings, never raised —
  a project may not have finished ingestion (or Qdrant may be down) by the
  time a run starts, and the planner must still produce a valid,
  fully-structured outline (with empty `source_tags`) rather than fail the
  whole run.

## Phase 4

- **Fan-out mechanism: a single LangGraph node (`drafter_fanout_node`)
  driving a `concurrent.futures.ThreadPoolExecutor`**, not nested LangGraph
  subgraphs / the `Send` API. Every LLM/retrieval call in this codebase is a
  blocking synchronous call (no async client anywhere in `llm/`), so a
  bounded thread pool is the natural concurrency primitive without inventing
  an async wrapper layer just for this. It also keeps the checkpointer's
  unit of work exactly one `RunState` update per `graph.invoke()` call,
  matching the existing "reopen a fresh SqliteSaver connection per call"
  resumability story — nested per-leaf subgraph checkpoints would need their
  own `thread_id` scheme for no corresponding benefit in a single-user local
  tool. Parallelism is `run_config["drafter_parallelism"]`, default 3
  (`drafter.DEFAULT_PARALLELISM`).

- **DocumentState update is a plain function (`apply_document_state_update`),
  called from the fan-out node's main thread as each future completes**, not
  a second LangGraph node. It plays the role of spec.md's "post-draft node"
  exactly, just implemented as a step in the same node's `as_completed` loop
  (serialized by construction — only the main thread ever calls it) so it
  composes cleanly with the thread-pool fan-out above, via a small
  `_DocumentStateBox` that gives each leaf a consistent snapshot at the
  moment it STARTS (not at submission time), so leaves that start later
  (once a worker slot frees up) see the latest state, not a stale one taken
  when all tasks were originally submitted.

- **Hallucinated bibkeys are impossible by construction via one enforcement
  function, `drafter.enforce_valid_bibkeys`.** The drafter is only ever given
  the bibkeys attached to ITS OWN retrieved chunks (`SearchHit.bibkeys`) as
  the "valid citation keys" set in its prompt. After every LLM call that can
  emit `[@key]` markers (initial draft AND the one revision), this function
  strips any key not in that set before the text is written to disk or
  folded into `DocumentState.citation_keys` — a marker left with zero valid
  keys is removed entirely, a mixed marker keeps only its valid entries.
  There is no code path from an LLM response to a saved draft that skips
  this step, so an invented key literally cannot survive. Stripped keys are
  logged as a `bibkey_enforcement` decision for visibility.

- **A leaf's pipeline aborts on its FIRST failing LLM call, rather than
  retrying through the whole pipeline.** `draft_leaf` catches any exception
  broadly and marks the leaf "flagged" immediately — it does not attempt to
  keep going through retrieval/draft/critique after resolve_model/`complete()`
  has already failed once. This matters in practice: `llm/base.py`'s own
  retry-with-backoff (not something Phase 4 may edit) means every unreachable
  provider call already costs ~3s (two sleeps) before raising; without a
  fail-fast leaf, a leaf with N possible LLM calls could cost N×3s instead of
  a bounded 3s. This is also why Phase 3's `test_graph_build.py`/
  `test_runs_api.py` tests (which mock only the planner's model/retrieval,
  not the drafter/critic roles or `hybrid_search`) still pass unmodified once
  the drafter node is wired in after planner — each of their `resume()` calls
  now genuinely runs the drafter fan-out against an unreachable local Ollama
  endpoint (the seeded default for `drafter`/`critic`) and Qdrant, but every
  leaf fails fast on its first query-rewrite call and is marked "flagged", so
  the run still reaches `status="completed"` (with all sections flagged)
  in bounded, small added wall-clock time instead of hanging or failing the
  test. Known gap: this does add real (if bounded) latency to those two
  pre-existing tests since they run unmocked against local network calls that
  fail; see TESTING.md.

- **Filtered hybrid retrieval issues one `hybrid_search` call per source tag
  and merges results (best score per chunk_id wins), rather than passing
  `brief.source_tags` straight through as a single filter.**
  `ingest/store.hybrid_search`'s convenience filters only match ONE
  `source_id` (or `section_path`) value per call (an AND of single-value
  equality filters, no "any of these values" OR support), and Phase 4 may not
  edit `ingest/store.py` to add one. Since the planner already populates a
  leaf's `source_tags` from the `source_id`s of its own retrieval sample
  (see Phase 3's `_skeleton_node`), tags ARE source ids in practice, so
  querying once per tag and merging is both correct and requires no changes
  to `ingest/store.py`.

- **Query-rewrite/grading/drafting/revision all resolve the "drafter" role
  model; only self-critique resolves "critic".** `llm/settings_store.ROLES`
  has no separate "query rewriter" or "chunk grader" role, and spec.md
  explicitly allows "LLM (role drafter or a dedicated small prompt)" for
  query rewrite — reusing the existing "drafter" role for every drafter-side
  call keeps the Settings screen's five roles unchanged (no new role, no
  settings-store schema change) while still resolving every model at call
  time via `resolve_model`, never hardcoded.

- **Self-critique triggers AT MOST one revision, never a loop.** spec.md says
  "self-critique against the brief -> one revision" (singular). `draft_leaf`
  calls critique exactly once; if the verdict is `"revise"`, exactly one
  revision call is made and its (re-enforced) output is final — there is no
  second critique pass on the revision. Iterating critique/revise further is
  explicitly out of scope for Phase 4 (Phase 5's citation verifier is the
  mechanism for further redraft loops, with its own 2-loop-then-flag-for-human
  bound per spec.md).

- **Section drafts are written to
  `data/runs/{run_id}/sections/{leaf_id}.md`**, one file per leaf, via
  `drafter.section_draft_path` — reusing `graph/decisions.run_dir(run_id)` as
  the run's data directory (so all of a run's artifacts —
  `decisions.jsonl`, `events.jsonl`, `sections/*.md` — live under one
  `data/runs/{run_id}/` tree). Only the file path (plus status/citation
  keys/word-adjacent counts) is ever written back into `RunState` — never the
  prose itself (constraints #1/#6).

- **A separate `events.jsonl` per run (distinct from `decisions.jsonl`)
  backs the SSE dashboard**, written by `drafter.emit_event`/read by
  `drafter.read_events`. `decisions.jsonl` (Phase 3) is the full agent-
  decision audit trail with complete payloads (query rewrites, grades,
  critique verdicts, bibkey enforcement) — potentially large and detailed.
  `events.jsonl` is a small, UI-shaped stream: `section_status`
  (queued/drafting/critiquing/done/flagged transitions),
  `token_usage` (per-call and cumulative tokens/cost), and `run_status`
  (drafting/completed/error). Keeping them separate means the SSE tailer
  never has to filter/re-shape the (bigger, less frequent, more detailed)
  decisions log, and the dashboard's decisions-log viewer hits its own small
  `GET /runs/{id}/decisions` endpoint instead.

- **SSE reattachment strategy: the durable `events.jsonl` file IS the replay
  buffer — no separate in-memory pub/sub.** `api.sse.tail_run_events` always
  opens the file from byte 0 on a new connection, yields every existing line,
  then keeps polling (every 0.2s, with a 15s heartbeat when idle) for new
  ones, terminating once the run registry reports a terminal status AND a
  poll iteration reads zero new lines. Since drafting happens in worker
  threads (not the async event loop FastAPI runs on), a real in-memory
  `asyncio.Queue`-per-run broadcaster would need cross-thread signaling
  machinery for no benefit over "just tail the file" in a single-user local
  tool — and file-tailing gets reattachment after a crash/resume for free
  (the dashboard's next `GET /runs/{id}/events` call just replays the whole
  history again).

- **`POST /runs/{id}/approve` stays fully synchronous** (unchanged from
  Phase 3 — still one direct `build_graph.resume(run_id)` call in the request
  handler), even though `resume()` now actually runs the (potentially slow)
  drafter fan-out. This was a deliberate choice to avoid touching
  `tests/test_runs_api.py`'s existing contract, which asserts the response
  body's `status` is already `"completed"` immediately after the POST
  returns (no polling). For a real multi-minute drafting run this means the
  `/approve` HTTP request blocks for the whole run — acceptable for a
  single-user local tool (FastAPI still serves other concurrent requests,
  e.g. `GET /runs/{id}` or the SSE stream, from other worker threads while
  one thread blocks in `resume()`), and `build_graph.resume` stamps the run
  registry `"drafting"` right before invoking the graph specifically so a
  concurrent poller can observe progress. Phase 5 may want to revisit this
  once the verifier/continuity/compliance loop adds even more per-run
  latency.
