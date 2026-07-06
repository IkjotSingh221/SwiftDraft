import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type OutlineNode, type Run, type RunStatus } from "../api/client";

// TODO(Phase 1/2 follow-up): once New Project can hand off a project id and
// chosen spec directly, replace this manual entry with real navigation state.
// For now Phase 3 stands alone: paste a project id from New Project, pick a
// spec, and start a run from here.
const FORMAT_SPEC_OPTIONS = [
  { value: "ieee_report", label: "IEEE Report" },
  { value: "university_thesis", label: "University Thesis" },
];

const IN_FLIGHT_STATUSES = new Set<RunStatus>(["queued", "planning"]);

function cloneOutline(nodes: OutlineNode[]): OutlineNode[] {
  return nodes.map((n) => ({
    ...n,
    source_tags: [...n.source_tags],
    children: cloneOutline(n.children),
  }));
}

function updateAtPath(nodes: OutlineNode[], path: number[], patch: Partial<OutlineNode>): OutlineNode[] {
  const [head, ...rest] = path;
  return nodes.map((node, i) => {
    if (i !== head) return node;
    if (rest.length === 0) return { ...node, ...patch };
    return { ...node, children: updateAtPath(node.children, rest, patch) };
  });
}

export default function OutlineReview() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run");
  const queryClient = useQueryClient();

  const [projectId, setProjectId] = useState("");
  const [formatSpecId, setFormatSpecId] = useState(FORMAT_SPEC_OPTIONS[0].value);

  const createRunMutation = useMutation({
    mutationFn: () => api.createRun({ project_id: projectId.trim(), format_spec_id: formatSpecId }),
    onSuccess: (run) => setSearchParams({ run: run.id }),
  });

  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId as string),
    enabled: !!runId,
    refetchInterval: (query) => {
      const data = query.state.data as Run | undefined;
      return data && IN_FLIGHT_STATUSES.has(data.status) ? 1000 : false;
    },
  });

  const run = runQuery.data;
  const outlineReady = run?.status === "awaiting_outline_approval" || run?.status === "completed";

  const outlineQuery = useQuery({
    queryKey: ["outline", runId],
    queryFn: () => api.getOutline(runId as string),
    enabled: !!runId && outlineReady,
  });

  const [draft, setDraft] = useState<OutlineNode[] | null>(null);
  useEffect(() => {
    if (outlineQuery.data) setDraft(cloneOutline(outlineQuery.data));
  }, [outlineQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (nodes: OutlineNode[]) => api.patchOutline(runId as string, nodes),
    onSuccess: (nodes) => {
      queryClient.setQueryData(["outline", runId], nodes);
      setDraft(cloneOutline(nodes));
    },
  });

  const approveMutation = useMutation({
    mutationFn: () => api.approveRun(runId as string),
    onSuccess: (updatedRun) => queryClient.setQueryData(["run", runId], updatedRun),
  });

  function handleChange(path: number[], patch: Partial<OutlineNode>) {
    setDraft((prev) => (prev ? updateAtPath(prev, path, patch) : prev));
  }

  const dirty =
    !!draft && !!outlineQuery.data && JSON.stringify(draft) !== JSON.stringify(outlineQuery.data);

  return (
    <div className="max-w-3xl space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Outline Review</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Review the planner's outline, edit titles, briefs, and word targets inline, then
          approve to continue.
        </p>
      </div>

      {!runId && (
        <section className="rounded border border-border-subtle bg-surface-raised p-4 space-y-4">
          <h2 className="text-sm font-medium text-text-secondary">Start a run</h2>
          <div>
            <label className="text-sm font-medium" htmlFor="run-project-id">
              Project ID
            </label>
            <input
              id="run-project-id"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              placeholder="Project id from the New Project screen"
              className="mt-1 w-full rounded border border-border-subtle bg-surface px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="text-sm font-medium" htmlFor="run-format-spec">
              Format spec
            </label>
            <select
              id="run-format-spec"
              value={formatSpecId}
              onChange={(e) => setFormatSpecId(e.target.value)}
              className="mt-1 w-full rounded border border-border-subtle bg-surface px-3 py-2 text-sm"
            >
              {FORMAT_SPEC_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            disabled={!projectId.trim() || createRunMutation.isPending}
            onClick={() => createRunMutation.mutate()}
            className="rounded bg-accent px-4 py-2 text-sm font-medium text-accent-contrast disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {createRunMutation.isPending ? "Starting…" : "Start run"}
          </button>
          {createRunMutation.isError && (
            <p className="text-sm text-danger">
              {createRunMutation.error instanceof Error
                ? createRunMutation.error.message
                : "Failed to start run"}
            </p>
          )}
        </section>
      )}

      {runId && (
        <section className="rounded border border-border-subtle bg-surface-raised p-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Run {runId}</p>
            <p className="text-xs text-text-secondary">
              project: {run?.project_id ?? "…"} · spec: {run?.format_spec_id ?? "…"}
            </p>
          </div>
          <StatusBadge status={run?.status} />
        </section>
      )}

      {runId && run?.status === "error" && (
        <p className="text-sm text-danger">Planning failed: {run.error}</p>
      )}

      {runId && (run?.status === "queued" || run?.status === "planning") && (
        <p className="text-sm text-text-secondary">Planning outline… this polls automatically.</p>
      )}

      {runId && draft && (
        <section className="space-y-4">
          <ul className="space-y-2">
            {draft.map((node, i) => (
              <OutlineNodeEditor key={node.id} node={node} path={[i]} depth={0} onChange={handleChange} />
            ))}
          </ul>

          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={!dirty || saveMutation.isPending}
              onClick={() => draft && saveMutation.mutate(draft)}
              className="rounded border border-border-subtle px-4 py-2 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saveMutation.isPending ? "Saving…" : "Save edits"}
            </button>
            <button
              type="button"
              disabled={dirty || run?.status !== "awaiting_outline_approval" || approveMutation.isPending}
              onClick={() => approveMutation.mutate()}
              className="rounded bg-accent px-4 py-2 text-sm font-medium text-accent-contrast disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {approveMutation.isPending ? "Approving…" : "Approve"}
            </button>
            {saveMutation.isError && <span className="text-sm text-danger">Failed to save edits</span>}
            {approveMutation.isError && (
              <span className="text-sm text-danger">Failed to approve</span>
            )}
          </div>

          {run?.status === "completed" && (
            <p className="text-sm text-success">
              Outline approved — run completed for Phase 3. Drafting arrives in Phase 4.
            </p>
          )}
        </section>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status?: RunStatus }) {
  if (!status) return null;
  const color =
    status === "error"
      ? "text-danger"
      : status === "completed"
        ? "text-success"
        : "text-text-secondary";
  return (
    <span className={`rounded border border-border-subtle px-2 py-1 text-xs ${color}`}>{status}</span>
  );
}

function OutlineNodeEditor({
  node,
  path,
  depth,
  onChange,
}: {
  node: OutlineNode;
  path: number[];
  depth: number;
  onChange: (path: number[], patch: Partial<OutlineNode>) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const hasChildren = node.children.length > 0;

  return (
    <li
      className="rounded border border-border-subtle bg-surface-raised p-3"
      style={{ marginLeft: depth * 16 }}
    >
      <div className="flex items-start gap-2">
        {hasChildren && (
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            className="mt-1 text-xs text-text-secondary hover:text-text-primary"
            aria-label={collapsed ? "Expand" : "Collapse"}
          >
            {collapsed ? "▸" : "▾"}
          </button>
        )}
        <div className="flex-1 space-y-2">
          <input
            value={node.title}
            onChange={(e) => onChange(path, { title: e.target.value })}
            className="w-full rounded border border-border-subtle bg-surface px-2 py-1 text-sm font-medium"
          />
          {!hasChildren && (
            <>
              <textarea
                value={node.brief ?? ""}
                onChange={(e) => onChange(path, { brief: e.target.value })}
                placeholder="What should this section cover?"
                rows={2}
                className="w-full rounded border border-border-subtle bg-surface px-2 py-1 text-sm"
              />
              <div className="flex items-center gap-2">
                <label className="text-xs text-text-secondary" htmlFor={`words-${node.id}`}>
                  Target words
                </label>
                <input
                  id={`words-${node.id}`}
                  type="number"
                  min={0}
                  value={node.target_words ?? 0}
                  onChange={(e) => onChange(path, { target_words: Number(e.target.value) })}
                  className="w-24 rounded border border-border-subtle bg-surface px-2 py-1 text-sm"
                />
                {node.source_tags.length > 0 && (
                  <span className="text-xs text-text-secondary">
                    sources: {node.source_tags.join(", ")}
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {hasChildren && !collapsed && (
        <ul className="mt-3 space-y-2">
          {node.children.map((child, i) => (
            <OutlineNodeEditor
              key={child.id}
              node={child}
              path={[...path, i]}
              depth={depth + 1}
              onChange={onChange}
            />
          ))}
        </ul>
      )}
    </li>
  );
}
