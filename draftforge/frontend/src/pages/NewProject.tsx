import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, type Project, type SourceIngestStatus, type SourceStatus } from "../api/client";

// TODO(Phase 2): replace this static placeholder with specs loaded from the
// format-spec registry (`GET /api/specs` or similar). The two example specs
// named here (`specs/ieee_report.json`, `specs/university_thesis.json`) are
// the ones the spec document ships; Phase 2 owns making this list real.
const FORMAT_SPEC_OPTIONS = [
  { value: "", label: "None selected yet" },
  { value: "ieee_report", label: "IEEE Report" },
  { value: "university_thesis", label: "University Thesis" },
];

const ACCEPTED_EXTENSIONS = [".pdf", ".docx"];

const STATUS_PROGRESS: Record<SourceIngestStatus, number> = {
  queued: 5,
  parsing: 30,
  chunking: 55,
  embedding: 80,
  done: 100,
  error: 100,
};

const STATUS_LABEL: Record<SourceIngestStatus, string> = {
  queued: "Queued",
  parsing: "Parsing (GROBID/Docling)",
  chunking: "Chunking",
  embedding: "Embedding",
  done: "Done",
  error: "Error",
};

function hasAcceptedExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function isInFlight(status: SourceIngestStatus): boolean {
  return status !== "done" && status !== "error";
}

export default function NewProject() {
  const queryClient = useQueryClient();

  const [projectName, setProjectName] = useState("");
  const [formatSpec, setFormatSpec] = useState("");
  const [project, setProject] = useState<Project | null>(null);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [rejectedNames, setRejectedNames] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const createProjectMutation = useMutation({
    mutationFn: () => api.createProject({ name: projectName.trim(), format_spec: formatSpec || null }),
    onSuccess: (created) => setProject(created),
  });

  const sourcesQuery = useQuery({
    queryKey: ["sources", project?.id],
    queryFn: () => api.listSources(project!.id),
    enabled: !!project,
    refetchInterval: (query) => {
      const data = query.state.data as SourceStatus[] | undefined;
      if (!data || data.length === 0) return 1500;
      return data.some((s) => isInFlight(s.status)) ? 1000 : false;
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadSource(project!.id, file),
  });

  const sources = sourcesQuery.data ?? [];

  function addFiles(files: FileList | File[]) {
    const accepted: File[] = [];
    const rejected: string[] = [];
    for (const file of Array.from(files)) {
      if (hasAcceptedExtension(file.name)) {
        accepted.push(file);
      } else {
        rejected.push(file.name);
      }
    }
    if (accepted.length > 0) {
      setPendingFiles((prev) => [...prev, ...accepted]);
    }
    setRejectedNames(rejected);
  }

  function removePendingFile(index: number) {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function startIngestion() {
    if (!project || pendingFiles.length === 0) return;
    const filesToUpload = pendingFiles;
    setPendingFiles([]);
    await Promise.all(filesToUpload.map((file) => uploadMutation.mutateAsync(file)));
    queryClient.invalidateQueries({ queryKey: ["sources", project.id] });
  }

  return (
    <div className="max-w-2xl space-y-8">
      <div>
        <h1 className="text-xl font-semibold">New Project</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Upload your source material, pick a format spec, and start ingestion. Each file is
          parsed, chunked, and embedded independently so you can watch progress per file.
        </p>
      </div>

      <section className="rounded border border-border-subtle bg-surface-raised p-4 space-y-4">
        <h2 className="text-sm font-medium text-text-secondary">Project</h2>

        {!project ? (
          <div className="space-y-3">
            <div>
              <label htmlFor="project-name" className="text-sm font-medium">
                Project name
              </label>
              <input
                id="project-name"
                type="text"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder="e.g. Photosynthesis Lab Report"
                className="mt-1 w-full rounded border border-border-subtle bg-surface px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label htmlFor="format-spec" className="text-sm font-medium">
                Format spec
              </label>
              <select
                id="format-spec"
                value={formatSpec}
                onChange={(e) => setFormatSpec(e.target.value)}
                className="mt-1 w-full rounded border border-border-subtle bg-surface px-3 py-2 text-sm"
              >
                {FORMAT_SPEC_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-text-secondary">
                Placeholder list — Phase 2 wires this up to real format spec files.
              </p>
            </div>
            <button
              type="button"
              disabled={!projectName.trim() || createProjectMutation.isPending}
              onClick={() => createProjectMutation.mutate()}
              className="rounded bg-accent px-4 py-2 text-sm font-medium text-accent-contrast disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {createProjectMutation.isPending ? "Creating…" : "Create project"}
            </button>
            {createProjectMutation.isError && (
              <p className="text-sm text-danger">
                {createProjectMutation.error instanceof Error
                  ? createProjectMutation.error.message
                  : "Failed to create project"}
              </p>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">{project.name}</p>
              <p className="text-xs text-text-secondary">
                id: {project.id}
                {project.format_spec ? ` · spec: ${project.format_spec}` : ""}
              </p>
            </div>
            <span className="rounded border border-border-subtle px-2 py-1 text-xs text-text-secondary">
              {project.status}
            </span>
          </div>
        )}
      </section>

      {project && (
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-text-secondary">Upload sources</h2>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragging(false);
              addFiles(e.dataTransfer.files);
            }}
            onClick={() => fileInputRef.current?.click()}
            className={`rounded border-2 border-dashed p-8 text-center cursor-pointer transition-colors ${
              isDragging
                ? "border-accent bg-accent/5"
                : "border-border-subtle hover:border-accent-strong"
            }`}
          >
            <p className="text-sm text-text-primary">
              Drag and drop PDF or DOCX files here, or click to browse
            </p>
            <p className="mt-1 text-xs text-text-secondary">Accepted: .pdf, .docx</p>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.docx"
              className="hidden"
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </div>

          {rejectedNames.length > 0 && (
            <p className="text-sm text-warning">
              Skipped unsupported file(s): {rejectedNames.join(", ")}
            </p>
          )}

          {pendingFiles.length > 0 && (
            <div className="rounded border border-border-subtle bg-surface-raised p-4 space-y-2">
              <p className="text-sm font-medium">Ready to upload</p>
              <ul className="space-y-1">
                {pendingFiles.map((file, i) => (
                  <li
                    key={`${file.name}-${i}`}
                    className="flex items-center justify-between text-sm text-text-secondary"
                  >
                    <span>{file.name}</span>
                    <button
                      type="button"
                      onClick={() => removePendingFile(i)}
                      className="text-xs text-danger hover:underline"
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                onClick={startIngestion}
                disabled={uploadMutation.isPending}
                className="rounded bg-accent px-4 py-2 text-sm font-medium text-accent-contrast disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {uploadMutation.isPending ? "Starting…" : "Start ingestion"}
              </button>
            </div>
          )}

          {sources.length > 0 && (
            <div className="rounded border border-border-subtle bg-surface-raised p-4 space-y-3">
              <p className="text-sm font-medium">Ingestion progress</p>
              {sources.map((source) => (
                <div key={source.source_id} className="space-y-1">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-text-primary">{source.filename}</span>
                    <span
                      className={source.status === "error" ? "text-danger" : "text-text-secondary"}
                    >
                      {STATUS_LABEL[source.status]}
                    </span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded bg-surface">
                    <div
                      className={`h-full rounded transition-all ${
                        source.status === "error" ? "bg-danger" : "bg-accent"
                      }`}
                      style={{ width: `${STATUS_PROGRESS[source.status]}%` }}
                    />
                  </div>
                  {source.error && <p className="text-xs text-danger">{source.error}</p>}
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
