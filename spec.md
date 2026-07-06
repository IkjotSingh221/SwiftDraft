# Project: DraftForge — Agentic RAG Document Drafter for STEM Students

Build a full-stack web application that drafts long (50+ page), format-compliant academic documents (project reports, thesis chapters, paper drafts) from a student's own source material, using an agentic RAG pipeline. The student uploads (a) source documents (papers, lab notes, results) and (b) selects/uploads an evaluator format specification through a web UI; the system produces a fully cited, format-compliant draft they can review and download. It is a single-user local tool — no auth, no multi-tenancy.

Work through the phases below **in order**. Do not skip ahead. At the end of each phase, run the tests for that phase and show me a short summary before continuing.

---

## Tech stack (fixed — do not substitute)

- **Python 3.11+**, managed with `uv`
- **LangGraph** for agent orchestration (with `SqliteSaver` checkpointer)
- **Qdrant** (Docker) as vector store, hybrid search: dense + sparse (BM25 via fastembed)
- **Embeddings**: `BAAI/bge-m3` via `sentence-transformers`, running locally on GPU (RTX 4060, so batch sizes must fit in 8 GB VRAM — use fp16 and batch_size ≤ 32)
- **GROBID** (Docker) for scientific PDF parsing → TEI XML; **Docling** as fallback for non-paper documents (docx, generic PDFs)
- **Pandoc** for rendering markdown → docx/PDF, with CSL styles for citations
- **FastAPI** + uvicorn for the backend API; **SSE** (`sse-starlette`) streaming LangGraph node events as run progress
- **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS; TanStack Query for data fetching. Keep it dependency-light — no heavy component kits, no state library beyond Query + local state
- **pytest** for backend tests; **Vitest + React Testing Library** for frontend unit tests (a handful of critical ones, not exhaustive)
- **LLM providers**: Anthropic (default `claude-sonnet-4-6`), **OpenRouter**, **Ollama** (local models on the 4060), and **Gemini API** — all behind a common provider interface (see "LLM provider layer" below). Implement the thin abstraction ourselves rather than pulling in litellm, so adding a provider is one file
- Provide a `docker-compose.yml` that brings up Qdrant and GROBID; the API and frontend run locally via `make dev` (uvicorn --reload + vite dev server, with a Vite proxy to the API)

## Non-negotiable design constraints

1. **No agent ever sees the full document.** Drafting happens per leaf section (500–1500 words). Assembly is deterministic (file concatenation + Pandoc), never an LLM call.
2. **Citations are keys, never free text.** Drafters emit `[@bibkey]` markers only. A bibliography store (CSL-JSON) is the single source of truth. Hallucinated keys must be impossible: the drafter is given the list of valid keys for its retrieved chunks, and the citation verifier rejects any key not in the store.
3. **Every factual claim must be traceable to a retrieved chunk.** The citation verifier checks claim→chunk support; unsupported claims are flagged and the section is redrafted with the violation as feedback.
4. **Format compliance is checked with code, not LLM judgment.** Word counts, required sections, heading depth, citation style — all validated programmatically against the format spec JSON schema, per section, as each completes.
5. **Everything resumes.** Any crash at section N must resume at section N via the LangGraph checkpointer. Test this explicitly (kill mid-run, resume).
6. **Bounded shared state.** Cross-section consistency comes from a `DocumentState` object (defined below) that grows with the outline, not the prose. Never pass completed section text between agents.

## LLM provider layer (modular, UI-switchable)

- `llm/base.py`: an `LLMProvider` ABC with a single method — `complete(messages, *, model, max_tokens, temperature, json_mode) -> LLMResponse` (text + token usage). Retries, timeouts, and cost accounting live in the base class, never per provider.
- One file per provider: `anthropic.py`, `openrouter.py`, `ollama.py`, `gemini.py`. OpenRouter and Ollama both speak OpenAI-compatible chat endpoints, so they share a small mixin. **Adding a new provider must require exactly one new file plus one registry entry — nothing else changes.**
- `llm/registry.py`: reads `config/models.yaml` (provider, model id, base URL, API-key env var, cost per Mtok, `local: true/false`) and exposes the available-model list to the API.
- **Per-role model assignment**: each agent role (planner, drafter, critic, citation verifier, continuity editor) maps to a provider+model in a persisted settings store (SQLite), editable at runtime via `GET/PUT /settings/models` from the Settings screen. Graph nodes resolve their model through the registry **at call time** — a model name must never be hardcoded in a node.
- Sensible defaults: strong model for planner and verifier; cheaper/local models permitted for drafter and critic. Models flagged `local` should surface a quality/VRAM note in the UI.

---

## Architecture

```
Student sources ─► Ingestion (GROBID/Docling → structure-aware chunks
                   → BGE-M3 dense + BM25 sparse → Qdrant)
Format spec (JSON) ─────────────┐
                                ▼
                     LangGraph agent graph
                     ├─ Planner: hierarchical outline (chapters→sections→
                     │   leaf subsections) + per-leaf section briefs
                     │   [interrupt: student approves/edits outline in web UI]
                     ├─ Section drafters (fan-out over leaves, parallel):
                     │   corrective-RAG subgraph per leaf:
                     │   rewrite query → hybrid retrieve (filtered by
                     │   planner-assigned source tags) → grade chunks →
                     │   draft with [@bibkeys] → self-critique → revise
                     ├─ Citation verifier: NLI-style check that each cited
                     │   claim is supported by its chunk; reject bad keys
                     ├─ Continuity editor: reviews adjacent-section
                     │   boundary paragraphs + DocumentState; patches
                     │   transitions only
                     └─ Compliance checker (code, not LLM): per-section
                        schema validation; violations route back to that
                        section's drafter with feedback
                                ▼
                     Renderer: Pandoc + evaluator template + CSL style
                     → draft.docx / draft.pdf + bibliography,
                       downloadable from the web UI
```

### Web UI (six screens, keep them minimal)

1. **New project** — upload sources (drag-and-drop PDFs/docx), pick a format spec, kick off ingestion with per-file progress.
2. **Outline review** — tree view of the planner's outline at the interrupt; editable section titles, briefs, and word targets; approve → resume the graph.
3. **Run dashboard** — live per-section status via SSE (queued / drafting / critiquing / verifying / done / flagged), running token + cost counter, per-section decisions log viewer.
4. **Review** — flagged citations (claim, chunk, verifier verdict) with accept / redraft-section actions; continuity-editor diffs shown per boundary.
5. **Downloads** — rendered docx/PDF, bibliography, eval report, decisions log.
6. **Settings** — per-role provider/model pickers populated from the registry, API-key status indicators (env var detected or not), Ollama reachability check, and cost-so-far summary.

### Design language (binding, not a suggestion)

Formal and understated — this is an academic tool, not an AI showcase. Neutral slate/gray palette with **one** restrained accent (muted blue or deep green). Explicitly forbidden: purple/violet gradients, gold/amber accents, glassmorphism, glowing borders — none of the stock AI-generated look. **Light and dark mode are both required**, implemented with CSS variable theme tokens + Tailwind's `class` dark-mode strategy, defaulting to system preference with a manual toggle in the header; every screen must be checked in both themes. Typography: one clean sans (Inter or the system stack), generous whitespace, clear information hierarchy — a stressed student at 2 a.m. should understand every screen without a tutorial.

### DocumentState (the consistency ledger)

A pydantic model persisted in graph state, passed (serialized, ≤4k tokens) to every drafter:

- outline tree, with 1–2 sentence summaries of each **completed** section
- terminology/notation registry (acronyms defined, symbol meanings, preferred phrasings)
- citation key registry
- figure/table numbering counters
- registry of global claims already made (short bullet strings)

Drafters read it; a post-draft node updates it after each section completes.

### Format spec schema

`format_spec.schema.json` defining: ordered required sections (with allowed nesting depth), per-section word ranges, citation style (CSL file reference), heading numbering style, front-matter requirements (title page fields, abstract limits), and figure/table caption rules. Ship two example specs: `specs/ieee_report.json` and `specs/university_thesis.json`, plus matching Pandoc reference templates in `templates/`.

---

## Repository layout

```
draftforge/
  pyproject.toml, docker-compose.yml, Makefile, README.md
  src/draftforge/
    api/                   # FastAPI: app.py, routes_projects.py (upload/ingest),
                           # routes_runs.py (start/outline/approve/resume/downloads),
                           # routes_settings.py (model registry + per-role assignment),
                           # sse.py (LangGraph event stream), models.py (pydantic DTOs)
    llm/                   # provider layer: base.py, registry.py, anthropic.py,
                           # openrouter.py, ollama.py, gemini.py
    ingest/ (grobid.py, docling_fallback.py, chunker.py, embedder.py, store.py)
    graph/ (state.py, planner.py, drafter.py, verifier.py,
            continuity.py, compliance.py, build_graph.py)
    formats/ (schema.py, validator.py)
    render/ (assemble.py, pandoc.py)
    evals/ (retrieval_eval.py, citation_faithfulness.py, compliance_eval.py, report.py)
  frontend/                # Vite + React + TS + Tailwind
    src/
      api/                 # typed client + SSE hook
      pages/ (NewProject.tsx, OutlineReview.tsx, RunDashboard.tsx,
              Review.tsx, Downloads.tsx, Settings.tsx)
      components/          # small shared pieces only (incl. theme toggle)
  config/ (models.yaml)  specs/  templates/  tests/  TESTING.md
```

---

## Phases

### Phase 0 — Scaffold
Repo layout, `pyproject.toml` (uv), docker-compose for Qdrant + GROBID. The **full LLM provider layer**: base interface, registry from `config/models.yaml`, all four providers, per-role settings persistence, and `GET/PUT /settings/models`. FastAPI skeleton with `/health` and CORS; Vite + React + TS + Tailwind scaffold with routing between the six pages — the **Settings screen fully functional now**, so every later phase can be exercised against any provider (Ollama for free local iteration, API models for quality). Theme tokens + dark/light mode wired in this phase, not retrofitted. `make dev` runs API + frontend together; `make test` runs pytest + vitest. Smoke tests: containers reachable, each provider mockable behind the shared interface, role→model switch round-trips through the API, frontend builds, `/health` reachable through the Vite proxy.

### Phase 1 — Ingestion
GROBID client (PDF → TEI XML → structured sections + reference list → CSL-JSON bib entries), Docling fallback, structure-aware chunker (respect section boundaries; 300–600 token chunks, 15% overlap; chunk metadata: source_id, section_path, page). Embed (dense fp16 batched + BM25 sparse) and upsert to Qdrant with a hybrid-search query function supporting metadata filters. API: `POST /projects`, `POST /projects/{id}/sources` (multipart upload), ingestion runs as a background task with per-file status polled by the UI. Frontend: **New project** screen (drag-and-drop upload, spec picker, ingestion progress). Tests: parse a real arXiv PDF fixture end-to-end; hybrid query returns the known-relevant chunk in top-5; upload → ingested round-trip via the API test client.

### Phase 2 — Format specs
JSON schema, pydantic loader, programmatic validator that takes (markdown section, spec node) → list of violations. The two example specs + Pandoc reference templates. Tests: crafted violations (over word limit, missing subsection, wrong heading depth) are all caught; a compliant fixture passes.

### Phase 3 — Planner + human-in-the-loop
LangGraph graph with SqliteSaver. Planner node: reads format spec + a retrieval sample per required section → hierarchical outline with leaf briefs (what to cover, which source tags, target words). Interrupt for human approval, surfaced through the API: `POST /runs` starts a run, `GET /runs/{id}/outline` returns the outline at the interrupt, `PATCH /runs/{id}/outline` applies edits, `POST /runs/{id}/approve` resumes the graph. Frontend: **Outline review** screen — collapsible tree, inline-editable titles/briefs/word targets, approve button. Tests: outline conforms to spec structure; interrupt → edit → resume works through the API test client.

### Phase 4 — Drafter subgraph (the core)
Per-leaf corrective-RAG subgraph: query rewrite → filtered hybrid retrieval → LLM chunk relevance grading (retry with rewritten query up to 2× if grades are poor) → draft with `[@bibkey]` markers and DocumentState in context → self-critique against the brief → one revision. Fan-out over leaves with bounded parallelism (config, default 3). DocumentState update node after each section. Stream LangGraph node events over `GET /runs/{id}/events` (SSE). Frontend: **Run dashboard** — per-section status chips (queued / drafting / critiquing / done), live token + cost counter, expandable per-section decisions log. Tests: mocked-LLM subgraph traverses correct paths; invalid bibkeys impossible by construction; parallel run produces all sections; SSE stream delivers events for a mocked run.

### Phase 5 — Verifier, continuity, compliance loop
Citation verifier (claim extraction → chunk support check; unsupported → redraft that section with the specific failures as feedback, max 2 loops, then flag for human). Continuity editor over adjacent boundaries. Compliance checker wired so violations route back to the owning drafter. Frontend: **Review** screen — flagged citations shown as claim / source chunk / verifier verdict, with accept and redraft-section actions (`POST /runs/{id}/sections/{sid}/redraft`); continuity diffs per boundary. Kill-and-resume test: SIGKILL the API process mid-draft, restart, `POST /runs/{id}/resume` completes the run and the dashboard reattaches to the SSE stream.

### Phase 6 — Rendering
Assemble section files in outline order, resolve cross-references, run Pandoc with reference template + CSL → docx and PDF + standalone bibliography, exposed via `GET /runs/{id}/artifacts/{name}`. Frontend: **Downloads** screen listing docx, PDF, bibliography, decisions log, and (after Phase 7) the eval report. Test: rendered docx opens, headings/numbering/citations match spec; artifact download round-trips through the API.

### Phase 7 — Eval harness (do not skimp; this is the differentiator)
- **Retrieval**: build a small labeled set (query → relevant chunk ids) from fixtures; report recall@5/@10 and MRR for dense-only vs sparse-only vs hybrid (ablation table).
- **Citation faithfulness**: for every citation in a finished draft, LLM-as-judge NLI check (claim entailed by chunk?) on a sampled subset with a human-spot-check protocol; report faithfulness rate.
- **Compliance**: automatic pass rate per spec across ≥3 generated drafts.
- **Cost/latency**: tokens and wall-clock per section and per run.
`make eval` (running `python -m draftforge.evals`) emits a single markdown report with all tables, also served on the Downloads screen. README documents the results honestly, including failure modes observed.

### Phase 8 — Consolidated verification
`TESTING.md` is maintained from Phase 0 onward: every phase appends its test cases (automated + manual UI checks) with a one-line purpose and current status. In this phase, execute it end to end: full automated suite; the manual UI checklist on all six screens **in both light and dark mode**; one complete real-LLM draft against a small (~5-page) spec on two different providers (one API, one Ollama) to prove the provider layer; one kill-and-resume drill. Record results and known gaps honestly in `TESTING.md`.

---

## Working style

- Write minimal tests alongside each phase — invariants and happy paths only, not exhaustive coverage. Mock LLM calls in unit tests; keep one optional live integration test per phase behind an env flag. Register every test (and every manual UI check) in `TESTING.md` as you go — it is the living checklist that Phase 8 executes in full.
- Prefer boring, explicit code over clever abstractions. Type-hint everything; pydantic models at every boundary.
- Log every agent decision (query rewrites, chunk grades, critique verdicts, compliance violations) to a per-run JSONL decisions log — I use these for failure analysis.
- If a design decision is ambiguous, make the simple choice, note it in `DECISIONS.md` with a one-line rationale, and continue — don't stall.
- Frontend restraint: the UI exists to operate the pipeline, not to impress. Follow the design language section exactly (neutral palette, both themes, no AI-cliché purple/gold); beyond that, no animations, no component library, no premature componentization. Backend correctness always takes priority over UI polish.
- The API is the only interface to the graph — no code path may depend on being run interactively from a terminal, so headless/scripted runs against the API stay possible for evals.
- Never fabricate benchmark numbers or eval results. If an eval can't run, say so.
