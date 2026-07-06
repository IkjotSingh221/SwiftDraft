/**
 * Typed fetch client. Every request path is prefixed with /api ("/api/health",
 * "/api/settings/models", ...) — the Vite dev server proxies that single
 * prefix to the FastAPI backend (see vite.config.ts / DECISIONS.md), and a
 * production reverse proxy is expected to do the same, so the client needs
 * no base URL beyond that prefix.
 */

const API_BASE = "/api";

export interface ModelInfo {
  name: string;
  provider: string;
  model: string;
  local: boolean;
  cost_per_mtok_in: number;
  cost_per_mtok_out: number;
  note: string | null;
  api_key_present: boolean;
}

export type Role =
  | "planner"
  | "drafter"
  | "critic"
  | "citation_verifier"
  | "continuity_editor";

export interface RoleModelsResponse {
  models: ModelInfo[];
  role_models: Record<string, string>;
  ollama_reachable: boolean;
}

export interface RoleModelsUpdate {
  role_models: Record<string, string>;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  qdrant_reachable: boolean;
  grobid_reachable: boolean;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`${init?.method ?? "GET"} ${path} failed: ${resp.status} ${body}`);
  }
  return (await resp.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>(`${API_BASE}/health`),
  getRoleModels: () => request<RoleModelsResponse>(`${API_BASE}/settings/models`),
  putRoleModels: (update: RoleModelsUpdate) =>
    request<RoleModelsResponse>(`${API_BASE}/settings/models`, {
      method: "PUT",
      body: JSON.stringify(update),
    }),
};

export const ROLES: Role[] = [
  "planner",
  "drafter",
  "critic",
  "citation_verifier",
  "continuity_editor",
];
