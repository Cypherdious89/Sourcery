import type { SourceStatus } from "@/lib/api";

const STYLES: Record<SourceStatus, string> = {
  pending:
    "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-950/50 dark:text-amber-300 dark:ring-amber-900",
  processing:
    "bg-blue-50 text-blue-700 ring-blue-200 dark:bg-blue-950/50 dark:text-blue-300 dark:ring-blue-900",
  ready:
    "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/50 dark:text-emerald-300 dark:ring-emerald-900",
  failed:
    "bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/50 dark:text-red-300 dark:ring-red-900",
};

/** Ingestion status pill. `pending`/`processing` animate so polling is visible. */
export function StatusBadge({ status }: { status: SourceStatus }) {
  const inFlight = status === "pending" || status === "processing";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STYLES[status]}`}
    >
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full bg-current ${inFlight ? "animate-pulse" : ""}`}
      />
      {status}
    </span>
  );
}
