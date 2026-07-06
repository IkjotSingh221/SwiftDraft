interface PhasePlaceholderProps {
  title: string;
  phase: string;
  description: string;
}

export default function PhasePlaceholder({ title, phase, description }: PhasePlaceholderProps) {
  return (
    <div className="rounded border border-border-subtle bg-surface-raised p-8">
      <h1 className="text-xl font-semibold text-text-primary">{title}</h1>
      <p className="mt-2 text-sm text-text-secondary">{description}</p>
      <span className="mt-4 inline-block rounded border border-border-subtle px-2 py-1 text-xs text-text-secondary">
        {phase}
      </span>
    </div>
  );
}
