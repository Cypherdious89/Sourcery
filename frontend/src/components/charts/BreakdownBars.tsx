export interface BreakdownItem {
  label: string;
  value: number;
  /** Already-formatted secondary metric (e.g. "$0.05"), shown beneath the bar. */
  secondary?: string;
}

// Fixed categorical order — never reassigned by filtering or sort. A 4th
// distinct category folds into "Other" rather than seating a non-validated
// hue (see globals.css for the validation this order clears).
const SLOT_COLORS = ["var(--viz-series-1)", "var(--viz-series-2)", "var(--viz-series-3)"];
const OTHER_COLOR = "var(--subtle)";

/**
 * Horizontal bar breakdown (categorical — "tell distinct series apart").
 * Direct-labeled (count at the bar's tip, cost caption beneath) so every
 * value is on the page — nothing is gated behind hover.
 */
export function BreakdownBars({
  items,
  formatValue = (v: number) => String(v),
}: {
  items: BreakdownItem[];
  formatValue?: (v: number) => string;
}) {
  if (items.length === 0) {
    return <p className="py-6 text-center text-xs text-subtle">No data yet</p>;
  }

  const sorted = [...items].sort((a, b) => b.value - a.value);
  const top = sorted.slice(0, 3);
  const rest = sorted.slice(3);
  const otherValue = rest.reduce((sum, r) => sum + r.value, 0);
  const rows = otherValue > 0 ? [...top, { label: "Other", value: otherValue }] : top;
  const max = Math.max(...rows.map((r) => r.value), 1);

  return (
    <div className="space-y-3">
      {rows.map((row, i) => {
        const color = i < top.length ? SLOT_COLORS[i] : OTHER_COLOR;
        const widthPct = Math.max(4, (row.value / max) * 100);
        return (
          <div key={row.label}>
            <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
              <span className="truncate font-medium text-fg">{row.label}</span>
              <span className="shrink-0 font-mono text-subtle">
                {formatValue(row.value)}
                {row.secondary ? ` · ${row.secondary}` : ""}
              </span>
            </div>
            <div className="h-4 overflow-hidden rounded bg-inset">
              <div
                className="h-full rounded-r"
                style={{ width: `${widthPct}%`, backgroundColor: color }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
