import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, artifactUrl, type Artifact } from "../api/client";

const ARTIFACT_LABELS: Record<string, string> = {
  "draft.docx": "Draft (.docx)",
  "draft.pdf": "Draft (.pdf)",
  "bibliography.json": "Bibliography (CSL-JSON)",
  "decisions.jsonl": "Decisions log (JSONL)",
  "eval_report.md": "Evaluation report",
};

function formatBytes(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ArtifactRow({ runId, artifact }: { runId: string; artifact: Artifact }) {
  const label = ARTIFACT_LABELS[artifact.name] ?? artifact.name;
  return (
    <li className="flex items-center justify-between gap-3 rounded border border-border-subtle bg-surface-raised p-3">
      <div>
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-text-secondary">
          {artifact.available ? formatBytes(artifact.size_bytes) : artifact.note ?? "Not yet available"}
        </p>
      </div>
      {artifact.available ? (
        <a
          href={artifactUrl(runId, artifact.name)}
          download={artifact.name}
          className="rounded bg-accent px-3 py-1.5 text-xs font-medium text-accent-contrast hover:opacity-90"
        >
          Download
        </a>
      ) : (
        <span className="rounded border border-border-subtle px-3 py-1.5 text-xs font-medium text-text-secondary opacity-60">
          Unavailable
        </span>
      )}
    </li>
  );
}

export default function Downloads() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run");
  const [runIdInput, setRunIdInput] = useState("");
  const queryClient = useQueryClient();

  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId as string),
    enabled: !!runId,
  });

  const artifactsQuery = useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => api.listArtifacts(runId as string),
    enabled: !!runId && runQuery.data?.status === "completed",
  });

  const reviewQuery = useQuery({
    queryKey: ["review", runId],
    queryFn: () => api.getReview(runId as string),
    enabled: !!runId && runQuery.data?.status === "completed",
  });

  const render = useMutation({
    mutationFn: () => api.renderRun(runId as string),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["artifacts", runId] });
    },
  });

  const flaggedSections = (reviewQuery.data?.sections ?? []).filter((s) => s.status === "flagged");
  const notCompleted = !!runId && runQuery.data && runQuery.data.status !== "completed";

  return (
    <div className="max-w-2xl space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Downloads</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Rendered docx/PDF, the project bibliography, and the run's decisions log. The
          evaluation report arrives in Phase 7.
        </p>
      </div>

      {!runId && (
        <section className="rounded border border-border-subtle bg-surface-raised p-4 space-y-3">
          <h2 className="text-sm font-medium text-text-secondary">View a run's downloads</h2>
          <div className="flex items-center gap-2">
            <input
              value={runIdInput}
              onChange={(e) => setRunIdInput(e.target.value)}
              placeholder="Run id"
              className="flex-1 rounded border border-border-subtle bg-surface px-3 py-2 text-sm"
            />
            <button
              type="button"
              disabled={!runIdInput.trim()}
              onClick={() => setSearchParams({ run: runIdInput.trim() })}
              className="rounded bg-accent px-4 py-2 text-sm font-medium text-accent-contrast disabled:opacity-50"
            >
              View
            </button>
          </div>
        </section>
      )}

      {runId && runQuery.isLoading && <p className="text-sm text-text-secondary">Loading run…</p>}

      {notCompleted && (
        <p className="text-sm text-text-secondary">
          This run isn't finished yet (status: {runQuery.data?.status}). Downloads become available
          once it reaches "completed".
        </p>
      )}

      {runId && runQuery.data?.status === "completed" && (
        <>
          {flaggedSections.length > 0 && (
            <section className="rounded border border-warning/30 bg-surface-raised p-4">
              <p className="text-sm text-warning">
                {flaggedSections.length} section{flaggedSections.length > 1 ? "s" : ""} flagged for
                human review ({flaggedSections.map((s) => s.title).join(", ")}) — the rendered draft
                includes them as-is. Visit the Review screen to accept or redraft before sharing the
                final document.
              </p>
            </section>
          )}

          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-text-secondary">Artifacts</h2>
              <button
                type="button"
                disabled={render.isPending}
                onClick={() => render.mutate()}
                className="rounded border border-border-subtle px-3 py-1.5 text-xs font-medium text-text-secondary hover:text-text-primary disabled:opacity-50"
              >
                {render.isPending ? "Rendering…" : "Render"}
              </button>
            </div>

            {artifactsQuery.isLoading && <p className="text-sm text-text-secondary">Loading artifacts…</p>}

            {artifactsQuery.data && (
              <ul className="space-y-2">
                {artifactsQuery.data.map((a) => (
                  <ArtifactRow key={a.name} runId={runId} artifact={a} />
                ))}
              </ul>
            )}

            {render.isError && (
              <p className="text-xs text-danger">{(render.error as Error).message}</p>
            )}
            {render.data && Object.keys(render.data.skipped).length > 0 && (
              <div className="text-xs text-text-secondary">
                <p className="uppercase tracking-wide text-[11px]">Skipped this render</p>
                <ul className="mt-1 space-y-0.5">
                  {Object.entries(render.data.skipped).map(([name, reason]) => (
                    <li key={name}>
                      {ARTIFACT_LABELS[name] ?? name}: {reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
