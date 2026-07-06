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
