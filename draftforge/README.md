# DraftForge

An agentic RAG pipeline that drafts long, format-compliant academic documents
(project reports, thesis chapters, paper drafts) from a student's own source
material. Sources are ingested (GROBID/Docling → chunks → BGE-M3 dense +
BM25 sparse → Qdrant), a LangGraph agent graph plans an outline, drafts each
leaf section with citation-key-only sourcing, verifies citation faithfulness,
checks format compliance programmatically, and renders the result to
docx/PDF via Pandoc. Single-user, local-first, no auth.

See `/home/user/SwiftDraft/spec.md` (project root) for the full specification
and phase plan. This directory (`draftforge/`) is the project root for the
actual application; it lives alongside (and does not touch) `ResearchAgent/`,
an earlier, separate prototype in this same repo.

## Requirements

- Python 3.11+ with [`uv`](https://docs.astral.sh/uv/)
- Node 20+ / npm
- Docker (for Qdrant + GROBID)

## Running it

```sh
cp .env.example .env   # fill in whichever provider API keys you have

make up      # start Qdrant (6333) + GROBID (8070) via docker compose
make dev     # run the API (uvicorn --reload, :8000) and the Vite dev
             # server (:5173) together; the frontend proxies /health,
             # /settings, /projects, /runs to the API
make test    # pytest (backend) + vitest (frontend)
```

Open http://localhost:5173 — the **Settings** screen is fully functional in
Phase 0: pick a model per agent role (planner, drafter, critic, citation
verifier, continuity editor), see API-key-present status and Ollama
reachability, and save. Every other screen is a Phase-0 placeholder that
later phases fill in.

## Project status

This repo is built phase by phase per `spec.md`. See `TESTING.md` for the
living checklist of what's automated-tested vs. manually verified per phase,
and `DECISIONS.md` for a log of ambiguous calls made along the way.
