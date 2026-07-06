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

// ---------------------------------------------------------------------------
// Projects / sources (Phase 1 — ingestion)
// ---------------------------------------------------------------------------

export interface Project {
  id: string;
  name: string;
  format_spec: string | null;
  status: string;
}

export interface ProjectCreate {
  name: string;
  format_spec?: string | null;
}

export type SourceIngestStatus =
  | "queued"
  | "parsing"
  | "chunking"
  | "embedding"
  | "done"
  | "error";

export interface SourceStatus {
  source_id: string;
  filename: string;
  status: SourceIngestStatus;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Runs / outline (Phase 3 — planner + human-in-the-loop)
// ---------------------------------------------------------------------------

export type RunStatus =
  | "queued"
  | "planning"
  | "awaiting_outline_approval"
  | "drafting"
  | "verifying"
  | "rendering"
  | "completed"
  | "error";

export interface Run {
  id: string;
  project_id: string;
  format_spec_id: string;
  status: RunStatus;
  error: string | null;
}

export interface RunCreate {
  project_id: string;
  format_spec_id: string;
  run_config?: Record<string, unknown>;
}

export interface OutlineNode {
  id: string;
  title: string;
  brief: string | null;
  target_words: number | null;
  source_tags: string[];
  children: OutlineNode[];
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

/** Like `request`, but for multipart/form-data uploads — never sets a JSON
 * Content-Type header, since the browser must set its own multipart
 * boundary. */
async function upload<T>(path: string, formData: FormData): Promise<T> {
  const resp = await fetch(path, { method: "POST", body: formData });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`POST ${path} failed: ${resp.status} ${body}`);
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
  createProject: (payload: ProjectCreate) =>
    request<Project>(`${API_BASE}/projects`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getProject: (projectId: string) =>
    request<Project>(`${API_BASE}/projects/${projectId}`),
  uploadSource: (projectId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return upload<SourceStatus>(`${API_BASE}/projects/${projectId}/sources`, formData);
  },
  listSources: (projectId: string) =>
    request<SourceStatus[]>(`${API_BASE}/projects/${projectId}/sources`),
  createRun: (payload: RunCreate) =>
    request<Run>(`${API_BASE}/runs`, { method: "POST", body: JSON.stringify(payload) }),
  getRun: (runId: string) => request<Run>(`${API_BASE}/runs/${runId}`),
  getOutline: (runId: string) => request<OutlineNode[]>(`${API_BASE}/runs/${runId}/outline`),
  patchOutline: (runId: string, nodes: OutlineNode[]) =>
    request<OutlineNode[]>(`${API_BASE}/runs/${runId}/outline`, {
      method: "PATCH",
      body: JSON.stringify(nodes),
    }),
  approveRun: (runId: string) =>
    request<Run>(`${API_BASE}/runs/${runId}/approve`, { method: "POST" }),
};

export const ROLES: Role[] = [
  "planner",
  "drafter",
  "critic",
  "citation_verifier",
  "continuity_editor",
];
