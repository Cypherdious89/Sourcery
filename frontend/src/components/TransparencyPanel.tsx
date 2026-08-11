"use client";

import { useState } from "react";
import type { ChatResponse } from "@/lib/api";
import { IconChevronRight } from "./icons";

type Meta = Pick<
  ChatResponse,
  "provider" | "model" | "status" | "latency_ms" | "cost_usd" | "cache_hit"
>;

function formatCost(cost: number): string {
  if (cost === 0) return "$0.00";
  // Gateway costs are fractions of a cent; show enough digits to be meaningful.
  return `$${cost.toFixed(6)}`;
}

function formatLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[10px] font-medium uppercase tracking-wide text-subtle">
        {label}
      </dt>
      <dd className="font-mono text-xs text-muted">{value}</dd>
    </div>
  );
}

/**
 * Expandable per-message row surfacing gateway metadata: which provider
 * answered, how long it took, what it cost, and whether it was a cache hit.
 */
export function TransparencyPanel({ meta }: { meta: Meta }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-2 border-t border-line pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left text-xs text-muted transition-colors hover:text-fg"
      >
        <IconChevronRight
          className={`h-3 w-3 shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
        />
        <span className="font-medium">Transparency</span>
        {/* Collapsed summary so the key facts are visible without expanding. */}
        <span className="ml-auto flex items-center gap-2 font-mono">
          {/* Model, not just provider — otherwise a fallback is invisible. */}
          <span>{meta.model || meta.provider}</span>
          {meta.status === "fallback" && (
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">
              fallback
            </span>
          )}
          <span className="text-subtle">·</span>
          <span>{formatLatency(meta.latency_ms)}</span>
          <span className="text-subtle">·</span>
          <span
            className={
              meta.cache_hit
                ? "rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300"
                : "rounded bg-inset px-1.5 py-0.5 text-muted"
            }
          >
            {meta.cache_hit ? "cache hit" : "live call"}
          </span>
        </span>
      </button>

      {open && (
        <dl className="mt-3 grid grid-cols-2 gap-3 rounded-md bg-inset p-3 ring-1 ring-inset ring-line sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Provider" value={meta.provider} />
          <Stat label="Model" value={meta.model || "—"} />
          <Stat label="Status" value={meta.status} />
          <Stat label="Latency" value={formatLatency(meta.latency_ms)} />
          <Stat label="Cost" value={formatCost(meta.cost_usd)} />
          <Stat label="Cache" value={meta.cache_hit ? "hit" : "miss"} />
        </dl>
      )}
    </div>
  );
}
