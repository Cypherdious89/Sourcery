/**
 * Stat tile: label (sentence case) + value (semibold, auto-compact) +
 * optional caption. Per the dataviz skill's figures spec: a single current
 * value doesn't need a chart, it needs a number.
 */
export function StatTile({
  label,
  value,
  caption,
}: {
  label: string;
  value: string;
  caption?: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1.5 text-2xl font-semibold tracking-tight text-fg">
        {value}
      </p>
      {caption && <p className="mt-1 font-mono text-[11px] text-subtle">{caption}</p>}
    </div>
  );
}
