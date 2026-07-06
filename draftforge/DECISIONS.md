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

## Phase 5 — Verifier, continuity, compliance loop

- **One `review` node, not three graph nodes.** Compliance + citation
  verification (with a bounded per-section redraft loop) + continuity all run
  inside a single `graph/review.py::review_node`, mirroring Phase 4's
  single-node `drafter_fanout_node`. Rationale: the redraft loop is per-section
  and bounded (max 2, then flag), and continuity runs once over adjacent
  boundaries — expressing that as LangGraph conditional edges would need a
  per-section fan-out with its own checkpoint sub-scheme for no benefit on a
  single-user tool. Keeps the checkpointer's unit of work one `RunState`
  update per super-step. Graph is now `planner -> drafter -> review -> END`.
- **Review runs sequentially** (unlike the parallel drafter): it is cheaper,
  the continuity pass needs a stable left-to-right order, and a shared
  `DocumentState` updated after each redraft stays coherent without locking.
- **Supporting chunks are re-retrieved for verification.** Phase 4's drafter
  persists only prose + the citation keys used, not the retrieved chunks, so
  the verifier re-runs `drafter.retrieve_chunks` (filtered by the leaf's source
  tags) and indexes `SearchHit.text` by each hit's `bibkeys`. This leaves
  Phase 4 untouched and reuses one retrieval path. A cited claim whose key has
  no retrieved chunk fails traceability outright (no LLM call); the rest go to
  a single batched NLI call.
- **Redraft-with-feedback reuses `draft_leaf` unchanged**: the compliance/
  verifier failures are appended to the brief's `brief` text (which
  `draft_leaf` already threads into the draft prompt) rather than adding a
  `feedback` parameter to Phase 4's drafter — the whole corrective-RAG pipeline
  is reused verbatim.
- **Bad-key backstop needs the store present.** `load_valid_bibkeys` returns
  `None` (unknown) when a project's `bibliography.json` is missing, and the
  verifier then SKIPS the bad-key check rather than flagging every citation —
  `None` is deliberately distinct from an empty set.
- **Graceful degradation.** An unreachable verifier or continuity model never
  crashes a run (matching the drafter's stance): the NLI call and each boundary
  edit are wrapped, degrading to "no violation manufactured" / "no-op patch".
- **Continuity patches only boundary paragraphs.** `apply_boundary_patch` swaps
  exactly A's last paragraph and B's first paragraph and rejoins the rest
  verbatim — the single code-enforced guarantee that bodies never change (#1/#6).
- **Crash recovery (`resume_run`)** distinguishes an approved-but-crashed run
  from an unapproved one using the run registry: if the checkpoint is still at
  the planner interrupt but the registry has moved past approval (`resume`
  stamps `"drafting"` before invoking), the run is continued; otherwise it is
  refused with "approve first". A crash inside `review` re-runs only `review`
  from the drafter's checkpoint — proven by `test_kill_resume.py` asserting the
  drafter call count stays at 1 across the crash.
- **`POST /runs/{id}/resume` vs `resume()`**: the existing `resume()` helper is
  the ordinary planner-approval resume (`POST /approve`); the new
  `resume_run()` (`POST /resume`) is the crash-recovery path for a run already
  past approval. Kept as two functions so approval can never be bypassed by the
  crash-recovery endpoint.
- **Redraft persistence**: the human-triggered `POST /sections/{id}/redraft`
  patches the checkpointed `section_status`/`document_state` via `update_state`
  and rewrites the single section's entry in `data/runs/{id}/review.json`; the
  Review screen re-fetches `GET /review`.
- **"Accept as-is" is client-side.** The Review screen's Accept action dismisses
  a flagged section locally (no backend state) — the draft on disk is already
  the accepted artifact; a flag is advisory. Kept simple deliberately.

## Phase 6 — Rendering

- **Assembly never hand-writes numeric heading prefixes; Pandoc's
  `number-sections` does the numbering.** `render/assemble.py` emits plain,
  unnumbered ATX headings at depth `len(parent_path)+1` and relies entirely
  on the `number-sections: true` already set in both `templates/*.yaml`
  defaults files (Phase 2) for `heading_numbering.style == "decimal"`. Having
  assembly *also* print "2.1 Data" style prefixes would create two competing
  numbering authorities that could drift out of sync (e.g. an optional
  subsection present in one draft but not another shifts one but not the
  other). `AssembledDocument.number_sections` still surfaces
  `spec.heading_numbering.style == "decimal"` as an explicit boolean so
  `render/pandoc.py` can pass `--metadata=number-sections:...` per-render
  (overriding the template's own hardcoded default) — no spec ships `"none"`
  today, but the override is free and keeps the two representations from
  silently drifting apart later.

- **The "references" leaf is skipped entirely during assembly — no heading,
  no spliced body.** Reusing Phase 3's own documented convention (a leaf
  whose `FormatSpec` section has `word_range: null`, e.g. "references" in
  both example specs, gets `target_words = 0` because "a bibliography is
  auto-generated by Pandoc/CSL in Phase 6, not drafted prose" — see Phase 3's
  entry above), `assemble_markdown` looks up `spec.find_section(leaf_id)` and
  skips any leaf whose section has `word_range is None`. Emitting our own
  empty "# References" heading here would leave a stray, empty heading right
  before Pandoc's own citeproc-generated bibliography section
  (`reference-section-title: "References"`, already wired in both defaults
  files) — skipping the node entirely avoids the duplicate.

- **Cross-reference resolution is scoped to caption NUMBERS only, via a
  deterministic regex rewrite pass, not a general reference resolver.**
  spec.md asks Phase 6 to "resolve cross-references if present (at minimum:
  assemble figure/table numbering coherently across sections)". Because
  Phase 4's fan-out drafts leaves in parallel, each leaf's `DocumentState`
  figure/table counters are a snapshot taken when that leaf STARTED, so two
  concurrently-drafted leaves can independently (and wrongly) guess the same
  number. `assemble_markdown` walks the final, in-order text and rewrites
  each figure/table caption's leading `<prefix> <number>` substring to a
  running, globally coherent sequence — flat for `"decimal"` numbering, reset
  per top-level outline node (chapter) for `"chapter_decimal"` (matching
  `university_thesis.json`'s captions). Only the caption LINE'S own number is
  touched; in-prose references to a figure by number ("as shown in Figure 2")
  are NOT tracked or rewritten, since Phase 4/5 defines no cross-reference
  marker syntax at all (only `[@bibkey]` citation markers exist) — there is
  nothing structured to resolve for those. This is a documented, bounded
  simplification, not a full LaTeX-`\ref`-style system.

- **Front-matter field-name mismatches between `specs/*.json` and
  `templates/*.tex` are bridged in `assemble.py`, not by editing the
  Phase 2 templates/specs.** Two exist today: `ieee_report.json`'s
  `"authors"` (plural) vs. the template's `$for(author)$` (Pandoc's singular
  convention), and `university_thesis.json`'s `"submission_date"` (underscore,
  required by the schema's `^[a-z0-9_]+$` id pattern) vs. the template's
  `$submission-date$` (hyphen, Pandoc's own metadata convention).
  `build_front_matter` emits BOTH spellings for every underscored field name,
  and aliases `"authors"` to `"author"` too — cheaper and safer than editing
  two other phases' checked-in artifacts for a one-line naming quirk.

- **No abstract is ever drafted by Phases 0-5** (Phase 2's own DECISIONS.md
  entry: "Abstract lives in front_matter, not in the sections tree" — and no
  leaf id named "abstract" exists in either example spec). `build_front_matter`
  therefore always sources `abstract` from `run_config.front_matter.abstract`
  (or `run_config.abstract`) if the caller supplies one, else a clearly
  bracketed placeholder — rendering must never fail for a document with no
  abstract content anywhere in the pipeline yet. Flagged as a known gap: a
  real abstract-drafting step is out of scope for this phase.

- **Render trigger: an explicit `POST /runs/{id}/render`, PLUS a best-effort
  lazy render on the first `GET /runs/{id}/artifacts` or
  `.../artifacts/{name}` call for a completed run whose artifacts directory
  is still empty.** The explicit POST is the only place a real render
  failure (`RenderError`) is surfaced to the caller (502) — the lazy GET path
  swallows and logs the same exception, so a listing call never 5xx's just
  because Pandoc/LaTeX aren't installed on this machine; it just shows
  `available: false` rows. "Stale" is NOT detected — a section redraft after
  a render does not invalidate the cached `artifacts/` directory; call
  `POST /render` again to refresh. This mirrors the existing "boring,
  explicit code" precedent over adding a content-hash/mtime staleness check
  for a single-user local tool.

- **Per-format failures are recorded, never raised, from `render_run`** —
  `RenderResult.skipped[name] = reason` for BOTH docx and pdf, whether the
  cause is the documented "no LaTeX engine" case or any other real Pandoc
  error (bad CSL, malformed assembled Markdown, ...). A partially-successful
  render (e.g. docx renders, pdf doesn't) is strictly more useful than an
  exception that discards a document that DID render and the
  already-copied bibliography artifact. `render_run` itself only raises
  `RenderError` when `pandoc` is entirely missing from `PATH` — in that case
  literally nothing (docx or pdf) can be produced, so there is truly nothing
  to fall back to.

- **DOCX is rendered WITHOUT the `--defaults=...yaml` file** — instead,
  `_render_docx` reads the same YAML and passes its `citeproc`/`csl`/
  `metadata-file`/`bibliography`/`number-sections`/`top-level-division`/
  `metadata.*` keys through explicitly, dropping `template`/`pdf-engine`
  entirely. Both `templates/*.yaml` files already document this exact split
  inline ("For DOCX output: drop template/pdf-engine ... rely on Pandoc's
  default docx styling") — Pandoc's docx writer cannot consume a LaTeX
  `--template`, so reusing the defaults file wholesale for docx would error.
  PDF rendering DOES use `--defaults=...yaml` wholesale (template +
  pdf-engine both apply to the LaTeX-mediated PDF path).

- **Artifact whitelist lives in `render/pandoc.py::ARTIFACT_SPECS`, a plain
  `dict[str, ArtifactSpec]`** (name -> content-type + a `path_fn(run_id)` +
  an optional "why unavailable" note) — `draft.docx`, `draft.pdf`,
  `bibliography.json`, `decisions.jsonl` (served straight from its own
  canonical `graph/decisions.py` path, not copied into `artifacts/`), and
  `eval_report.md` (Phase 7's slot, `note_if_missing="coming in Phase 7"`).
  `GET /runs/{id}/artifacts/{name}` looks `name` up ONLY in this dict and
  never uses the string to build a filesystem path directly — this IS the
  path-traversal guard (an unknown/hostile name simply isn't a key and
  404s), not a separate sanitization step.

- **`RenderError` maps to 502, not 500, in the API.** The failure is Pandoc
  (an external dependency) being unavailable or erroring, not a bug in this
  service's own request-handling code; `KeyError`/`ValueError` from
  `render_run` still map to 404/409 exactly like every other
  `graph/build_graph.py` function's convention.
