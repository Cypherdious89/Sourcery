const STATUS_META: Record<string, { label: string; color: string }> = {
  ok: { label: "OK", color: "var(--viz-good)" },
  fallback: { label: "Fallback", color: "var(--viz-warning)" },
  error: { label: "Error", color: "var(--viz-critical)" },
};
const ORDER = ["ok", "fallback", "error"];

/**
 * Part-to-whole call outcomes as a single stacked bar. Status color is
 * fixed/reserved (never a categorical slot) and never carries meaning alone —
 * every segment is paired with a legend row naming it and stating the count,
 * satisfying the relief rule for the sub-3:1 statuses on a light surface.
 */
export function StatusBar({
  counts,
}: {
  counts: { status: string; count: number }[];
}) {
  const total = counts.reduce((sum, c) => sum + c.count, 0);
  if (total === 0) {
    return <p className="py-6 text-center text-xs text-subtle">No calls yet</p>;
  }
  const byStatus = new Map(counts.map((c) => [c.status, c.count]));

  return (
    <div>
      <div className="flex h-6 gap-0.5 overflow-hidden rounded-md">
        {ORDER.filter((s) => (byStatus.get(s) ?? 0) > 0).map((s) => {
          const count = byStatus.get(s) ?? 0;
          const pct = (count / total) * 100;
          return (
            <div
              key={s}
              className="h-full first:rounded-l-md last:rounded-r-md"
              style={{ width: `${pct}%`, backgroundColor: STATUS_META[s].color }}
              title={`${STATUS_META[s].label}: ${count}`}
            />
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
        {ORDER.filter((s) => byStatus.has(s)).map((s) => (
          <div key={s} className="flex items-center gap-1.5 text-xs">
            <span
              aria-hidden
              className="h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: STATUS_META[s].color }}
            />
            <span className="text-muted">{STATUS_META[s].label}</span>
            <span className="font-mono font-medium text-fg">
              {byStatus.get(s)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
