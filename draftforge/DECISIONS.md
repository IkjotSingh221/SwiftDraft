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
