import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  api,
  type CitationCheck,
  type ContinuityDiff,
  type SectionReview,
} from "../api/client";

function VerdictChip({ supported }: { supported: boolean }) {
  const base = "rounded border px-2 py-0.5 text-xs font-medium";
  return supported ? (
    <span className={`${base} border-success/30 text-success`}>Supported</span>
  ) : (
    <span className={`${base} border-danger/30 text-danger`}>Unsupported</span>
  );
}

function CitationRow({ check }: { check: CitationCheck }) {
  return (
    <li className="rounded border border-border-subtle bg-surface p-3 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm">{check.claim}</p>
        <VerdictChip supported={check.supported} />
      </div>
      <p className="text-xs text-text-secondary">
        Cited: {check.cited_keys.length ? check.cited_keys.map((k) => `@${k}`).join(", ") : "—"}
      </p>
      {check.supporting_excerpt && (
        <div>
          <p className="text-[11px] uppercase tracking-wide text-text-secondary">Source chunk</p>
          <p className="mt-1 rounded bg-surface-raised p-2 text-xs text-text-secondary">
            {check.supporting_excerpt}
          </p>
        </div>
      )}
      {!check.supported && check.reason && (
        <p className="text-xs text-danger">Verifier: {check.reason}</p>
      )}
    </li>
  );
}

function BoundaryDiff({ diff }: { diff: ContinuityDiff }) {
  return (
    <li className="rounded border border-border-subtle bg-surface-raised p-3 space-y-2">
      <p className="text-sm font-medium">
        {diff.a_id} → {diff.b_id}{" "}
        {diff.changed ? (
          <span className="text-xs font-normal text-accent">(transition patched)</span>
        ) : (
          <span className="text-xs font-normal text-text-secondary">(unchanged)</span>
        )}
      </p>
      {diff.note && <p className="text-xs text-text-secondary">{diff.note}</p>}
      {diff.changed && (
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <p className="text-[11px] uppercase tracking-wide text-text-secondary">Before</p>
            <pre className="mt-1 overflow-x-auto rounded bg-surface p-2 text-[11px] text-text-secondary whitespace-pre-wrap">
              {diff.a_before}
              {"\n— — —\n"}
              {diff.b_before}
            </pre>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-text-secondary">After</p>
            <pre className="mt-1 overflow-x-auto rounded bg-surface p-2 text-[11px] text-text-primary whitespace-pre-wrap">
              {diff.a_after}
              {"\n— — —\n"}
              {diff.b_after}
            </pre>
          </div>
        </div>
      )}
    </li>
  );
}

function SectionCard({
  runId,
  section,
  accepted,
  onAccept,
}: {
  runId: string;
  section: SectionReview;
  accepted: boolean;
  onAccept: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const redraft = useMutation({
    mutationFn: () => api.redraftSection(runId, section.section_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["review", runId] });
      queryClient.invalidateQueries({ queryKey: ["sections", runId] });
    },
  });

  const flagged = section.status === "flagged";
  const unsupported = section.citations.filter((c) => !c.supported);
  const hasIssues = flagged || unsupported.length > 0 || section.compliance_violations.length > 0;

  return (
    <li className="rounded border border-border-subtle bg-surface-raised p-4 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium">{section.title}</p>
          <p className="text-xs text-text-secondary">
            {section.section_id} · {section.attempts} redraft attempt(s)
            {accepted && <span className="ml-2 text-success">accepted</span>}
          </p>
        </div>
        <span
          className={`rounded border px-2 py-0.5 text-xs font-medium ${
            flagged ? "border-danger/30 text-danger" : "border-success/30 text-success"
          }`}
        >
          {flagged ? "Flagged" : "Done"}
        </span>
      </div>

      {section.invalid_keys.length > 0 && (
        <p className="text-xs text-danger">
          Rejected citation keys (not in bibliography):{" "}
          {section.invalid_keys.map((k) => `@${k}`).join(", ")}
        </p>
      )}

      {section.compliance_violations.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-wide text-text-secondary">
            Compliance violations
          </p>
          <ul className="mt-1 space-y-1">
            {section.compliance_violations.map((v, i) => (
              <li key={i} className="text-xs text-warning">
                {v.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {section.citations.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-wide text-text-secondary">
            Cited claims ({unsupported.length} flagged)
          </p>
          <ul className="mt-1 space-y-2">
            {section.citations.map((c, i) => (
              <CitationRow key={i} check={c} />
            ))}
          </ul>
        </div>
      )}

      {hasIssues && !accepted && (
        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={() => onAccept(section.section_id)}
            className="rounded border border-border-subtle px-3 py-1.5 text-xs font-medium text-text-secondary hover:text-text-primary"
          >
            Accept as-is
          </button>
          <button
            type="button"
            disabled={redraft.isPending}
            onClick={() => redraft.mutate()}
            className="rounded bg-accent px-3 py-1.5 text-xs font-medium text-accent-contrast disabled:opacity-50"
          >
            {redraft.isPending ? "Redrafting…" : "Redraft section"}
          </button>
        </div>
      )}
      {redraft.isError && (
        <p className="text-xs text-danger">{(redraft.error as Error).message}</p>
      )}
    </li>
  );
}

export default function Review() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run");
  const [runIdInput, setRunIdInput] = useState("");
  const [accepted, setAccepted] = useState<Set<string>>(new Set());

  const reviewQuery = useQuery({
    queryKey: ["review", runId],
    queryFn: () => api.getReview(runId as string),
    enabled: !!runId,
  });

  const review = reviewQuery.data;

  return (
    <div className="max-w-3xl space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Review</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Flagged citations (claim, source chunk, verifier verdict) with accept / redraft
          actions, plus the continuity editor's per-boundary transition diffs.
        </p>
      </div>

      {!runId && (
        <section className="rounded border border-border-subtle bg-surface-raised p-4 space-y-3">
          <h2 className="text-sm font-medium text-text-secondary">Review a run</h2>
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

      {runId && reviewQuery.isLoading && (
        <p className="text-sm text-text-secondary">Loading review…</p>
      )}

      {runId && review && review.sections.length === 0 && (
        <p className="text-sm text-text-secondary">
          No review yet — the run hasn't reached the verification stage.
        </p>
      )}

      {review && review.sections.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium text-text-secondary">Sections</h2>
          <ul className="space-y-3">
            {review.sections.map((s) => (
              <SectionCard
                key={s.section_id}
                runId={runId as string}
                section={s}
                accepted={accepted.has(s.section_id)}
                onAccept={(id) => setAccepted((prev) => new Set(prev).add(id))}
              />
            ))}
          </ul>
        </section>
      )}

      {review && review.continuity.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium text-text-secondary">Continuity boundaries</h2>
          <ul className="space-y-3">
            {review.continuity.map((d, i) => (
              <BoundaryDiff key={i} diff={d} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
