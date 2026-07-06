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
