import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useSSE } from "../api/sse";
import {
  api,
  runEventsUrl,
  type Run,
  type RunStatus,
  type SectionRunStatus,
  type SectionStatus,
} from "../api/client";

const RUN_EVENT_NAMES = ["section_status", "token_usage", "run_status", "heartbeat"];
const IN_FLIGHT_RUN_STATUSES = new Set<RunStatus>(["queued", "planning", "drafting", "verifying", "rendering"]);
const IN_FLIGHT_SECTION_STATUSES = new Set<SectionRunStatus>(["queued", "drafting", "critiquing", "verifying"]);

interface SectionStatusEventData {
  section_id: string;
  status: SectionRunStatus;
  title?: string;
}

interface TokenUsageEventData {
  section_id: string;
  tokens_used: number;
  cost_usd: number;
  cumulative_tokens: number;
  cumulative_cost_usd: number;
}

const STATUS_LABEL: Record<SectionRunStatus, string> = {
  queued: "Queued",
  drafting: "Drafting",
  critiquing: "Critiquing",
  verifying: "Verifying",
  done: "Done",
  flagged: "Flagged",
};

function chipClass(status: SectionRunStatus): string {
  const base = "rounded border px-2 py-0.5 text-xs font-medium";
  switch (status) {
    case "done":
      return `${base} border-success/30 text-success`;
    case "flagged":
      return `${base} border-danger/30 text-danger`;
    case "drafting":
    case "critiquing":
    case "verifying":
      return `${base} border-accent/30 text-accent`;
    default:
      return `${base} border-border-subtle text-text-secondary`;
  }
}

export default function RunDashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run");
  const [runIdInput, setRunIdInput] = useState("");
  const [expandedSection, setExpandedSection] = useState<string | null>(null);

  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId as string),
    enabled: !!runId,
    refetchInterval: (query) => {
      const data = query.state.data as Run | undefined;
      return data && IN_FLIGHT_RUN_STATUSES.has(data.status) ? 2000 : false;
    },
  });
  const run = runQuery.data;

  const sectionsQuery = useQuery({
    queryKey: ["sections", runId],
    queryFn: () => api.listSections(runId as string),
    enabled: !!runId,
    refetchInterval: (query) => {
      const data = query.state.data as SectionStatus[] | undefined;
      const stillRunning = !data || data.some((s) => IN_FLIGHT_SECTION_STATUSES.has(s.status));
      return stillRunning ? 2000 : false;
    },
  });

  const events = useSSE<SectionStatusEventData | TokenUsageEventData | { status: RunStatus }>(
    runId ? runEventsUrl(runId) : null,
    RUN_EVENT_NAMES,
    !!runId,
  );

  // Live section statuses: base list (titles + last polled status) with any
  // newer SSE section_status events layered on top, keyed by section_id.
  const sections = useMemo(() => {
    const base = sectionsQuery.data ?? [];
    const liveStatus = new Map<string, SectionRunStatus>();
    for (const evt of events) {
      if (evt.event === "section_status") {
        const data = evt.data as SectionStatusEventData;
        liveStatus.set(data.section_id, data.status);
      }
    }
    return base.map((s) => ({ ...s, status: liveStatus.get(s.section_id) ?? s.status }));
  }, [sectionsQuery.data, events]);

  const latestTokenUsage = useMemo(() => {
    const usageEvents = events.filter((e) => e.event === "token_usage") as {
      event: string;
      data: TokenUsageEventData;
    }[];
    return usageEvents.length ? usageEvents[usageEvents.length - 1].data : null;
  }, [events]);

  const decisionsQuery = useQuery({
    queryKey: ["decisions", runId, expandedSection],
    queryFn: () => api.listDecisions(runId as string, expandedSection ?? undefined),
    enabled: !!runId && !!expandedSection,
  });

  return (
    <div className="max-w-3xl space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Run Dashboard</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Live per-section drafting status, a running token/cost counter, and each section's
          decisions log.
        </p>
      </div>

      {!runId && (
        <section className="rounded border border-border-subtle bg-surface-raised p-4 space-y-3">
          <h2 className="text-sm font-medium text-text-secondary">View a run</h2>
          <div className="flex items-center gap-2">
            <input
              value={runIdInput}
              onChange={(e) => setRunIdInput(e.target.value)}
              placeholder="Run id from the Outline Review screen"
              className="flex-1 rounded border border-border-subtle bg-surface px-3 py-2 text-sm"
            />
            <button
              type="button"
              disabled={!runIdInput.trim()}
              onClick={() => setSearchParams({ run: runIdInput.trim() })}
              className="rounded bg-accent px-4 py-2 text-sm font-medium text-accent-contrast disabled:opacity-50 disabled:cursor-not-allowed"
            >
              View
            </button>
          </div>
        </section>
      )}

      {runId && (
        <section className="rounded border border-border-subtle bg-surface-raised p-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Run {runId}</p>
            <p className="text-xs text-text-secondary">status: {run?.status ?? "…"}</p>
          </div>
          <div className="text-right text-xs text-text-secondary">
            <p>
              tokens:{" "}
              <span className="font-medium text-text-primary">
                {latestTokenUsage?.cumulative_tokens ?? 0}
              </span>
            </p>
            <p>
              cost:{" "}
              <span className="font-medium text-text-primary">
                ${(latestTokenUsage?.cumulative_cost_usd ?? 0).toFixed(4)}
              </span>
            </p>
          </div>
        </section>
      )}

      {runId && sections.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium text-text-secondary">Sections</h2>
          <ul className="space-y-2">
            {sections.map((s) => {
              const expanded = expandedSection === s.section_id;
              return (
                <li
                  key={s.section_id}
                  className="rounded border border-border-subtle bg-surface-raised p-3"
                >
                  <button
                    type="button"
                    onClick={() => setExpandedSection(expanded ? null : s.section_id)}
                    className="flex w-full items-center justify-between text-left"
                  >
                    <span className="text-sm font-medium">{s.title}</span>
                    <span className="flex items-center gap-3">
                      {s.tokens_used > 0 && (
                        <span className="text-xs text-text-secondary">
                          {s.tokens_used} tok · ${s.cost_usd.toFixed(4)}
                        </span>
                      )}
                      <span className={chipClass(s.status)}>{STATUS_LABEL[s.status]}</span>
                      <span className="text-xs text-text-secondary">{expanded ? "▾" : "▸"}</span>
                    </span>
                  </button>

                  {expanded && (
                    <div className="mt-3 border-t border-border-subtle pt-3">
                      {decisionsQuery.isLoading && (
                        <p className="text-xs text-text-secondary">Loading decisions…</p>
                      )}
                      {decisionsQuery.data && decisionsQuery.data.length === 0 && (
                        <p className="text-xs text-text-secondary">No decisions logged yet.</p>
                      )}
                      <ul className="space-y-2">
                        {decisionsQuery.data?.map((d, i) => (
                          <li key={i} className="text-xs">
                            <span className="font-medium">{d.kind}</span>{" "}
                            <span className="text-text-secondary">({d.ts})</span>
                            <pre className="mt-1 overflow-x-auto rounded bg-surface p-2 text-[11px] text-text-secondary">
                              {JSON.stringify(d.payload, null, 2)}
                            </pre>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {runId && sectionsQuery.data && sectionsQuery.data.length === 0 && (
        <p className="text-sm text-text-secondary">
          No sections yet — the outline may not be approved, or drafting hasn't started.
        </p>
      )}
    </div>
  );
}
