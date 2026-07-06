import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ROLES, type Role, type RoleModelsUpdate } from "../api/client";

const ROLE_LABELS: Record<Role, string> = {
  planner: "Planner",
  drafter: "Drafter",
  critic: "Critic",
  citation_verifier: "Citation Verifier",
  continuity_editor: "Continuity Editor",
};

export default function Settings() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["settings", "models"],
    queryFn: api.getRoleModels,
  });

  const [draft, setDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    if (data) setDraft(data.role_models);
  }, [data]);

  const mutation = useMutation({
    mutationFn: (update: RoleModelsUpdate) => api.putRoleModels(update),
    onSuccess: (resp) => {
      queryClient.setQueryData(["settings", "models"], resp);
    },
  });

  if (isLoading) {
    return <p className="text-text-secondary">Loading settings…</p>;
  }

  if (isError || !data) {
    return (
      <p className="text-danger">
        Failed to load settings: {error instanceof Error ? error.message : "unknown error"}
      </p>
    );
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(data.role_models);

  return (
    <div className="max-w-2xl space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Assign a model to each agent role. Changes are resolved by graph nodes at call
          time, so this takes effect on the next run without a restart.
        </p>
      </div>

      <section className="rounded border border-border-subtle bg-surface-raised p-4">
        <h2 className="text-sm font-medium text-text-secondary">Environment</h2>
        <dl className="mt-2 text-sm">
          <div className="flex items-center justify-between py-1">
            <dt>Ollama reachable</dt>
            <dd>
              <StatusBadge ok={data.ollama_reachable} okLabel="reachable" badLabel="unreachable" />
            </dd>
          </div>
        </dl>
      </section>

      <section className="space-y-4">
        {ROLES.map((role) => (
          <div
            key={role}
            className="flex items-center justify-between gap-4 rounded border border-border-subtle bg-surface-raised p-4"
          >
            <div>
              <label htmlFor={`role-${role}`} className="text-sm font-medium">
                {ROLE_LABELS[role]}
              </label>
              <p className="text-xs text-text-secondary">{role}</p>
            </div>
            <select
              id={`role-${role}`}
              className="rounded border border-border-subtle bg-surface px-2 py-1.5 text-sm min-w-[16rem]"
              value={draft[role] ?? ""}
              onChange={(e) => setDraft((prev) => ({ ...prev, [role]: e.target.value }))}
            >
              {data.models.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name} ({m.provider}){m.local ? " · local" : ""}
                  {!m.api_key_present && !m.local ? " · missing key" : ""}
                </option>
              ))}
            </select>
          </div>
        ))}
      </section>

      <section>
        <h2 className="text-sm font-medium text-text-secondary">Available models</h2>
        <div className="mt-2 overflow-x-auto rounded border border-border-subtle">
          <table className="w-full text-sm">
            <thead className="bg-surface-raised text-left text-text-secondary">
              <tr>
                <th className="px-3 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium">Provider</th>
                <th className="px-3 py-2 font-medium">Cost in / out ($/Mtok)</th>
                <th className="px-3 py-2 font-medium">API key</th>
                <th className="px-3 py-2 font-medium">Note</th>
              </tr>
            </thead>
            <tbody>
              {data.models.map((m) => (
                <tr key={m.name} className="border-t border-border-subtle">
                  <td className="px-3 py-2">
                    {m.name}
                    {m.local && (
                      <span className="ml-2 rounded border border-border-subtle px-1.5 py-0.5 text-xs text-text-secondary">
                        local
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">{m.provider}</td>
                  <td className="px-3 py-2 text-text-secondary">
                    {m.cost_per_mtok_in.toFixed(2)} / {m.cost_per_mtok_out.toFixed(2)}
                  </td>
                  <td className="px-3 py-2">
                    {m.local ? (
                      <span className="text-text-secondary">n/a</span>
                    ) : (
                      <StatusBadge
                        ok={m.api_key_present}
                        okLabel="present"
                        badLabel="missing"
                      />
                    )}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">{m.note ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={!dirty || mutation.isPending}
          onClick={() => mutation.mutate({ role_models: draft })}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-accent-contrast disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {mutation.isPending ? "Saving…" : "Save changes"}
        </button>
        {mutation.isSuccess && !dirty && (
          <span className="text-sm text-success">Saved.</span>
        )}
        {mutation.isError && (
          <span className="text-sm text-danger">
            {mutation.error instanceof Error ? mutation.error.message : "Save failed"}
          </span>
        )}
      </div>
    </div>
  );
}

function StatusBadge({
  ok,
  okLabel,
  badLabel,
}: {
  ok: boolean;
  okLabel: string;
  badLabel: string;
}) {
  return (
    <span className={`text-sm ${ok ? "text-success" : "text-danger"}`}>
      {ok ? okLabel : badLabel}
    </span>
  );
}
