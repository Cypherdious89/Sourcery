"use client";

import { useEffect, useState } from "react";
import { getStats, type StatsResponse } from "@/lib/api";
import { BreakdownBars } from "@/components/charts/BreakdownBars";
import { LineChart } from "@/components/charts/LineChart";
import { StatusBar } from "@/components/charts/StatusBar";
import { IconBarChart } from "@/components/icons";
import { StatTile } from "@/components/StatTile";

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return `${months[Number(m) - 1]} ${Number(d)}`;
}

function formatCost(v: number): string {
  return v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

function formatLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

function Card({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-fg">{title}</h2>
      {subtitle && <p className="mt-0.5 text-xs text-subtle">{subtitle}</p>}
      <div className="mt-4">{children}</div>
    </div>
  );
}

function CardSkeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-xl border border-line bg-surface p-4 ${className}`}
    >
      <div className="h-4 w-1/3 rounded bg-inset" />
      <div className="mt-4 h-24 rounded bg-inset" />
    </div>
  );
}

export default function StatsPage() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStats().then(
      (s) => {
        if (!cancelled) setStats(s);
      },
      (e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-10 sm:px-6">
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-soft text-accent-text">
          <IconBarChart className="h-4.5 w-4.5" />
        </span>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-fg">
            Gateway stats
          </h1>
          <p className="text-sm text-muted">
            Every number reads straight from{" "}
            <code className="rounded bg-inset px-1 py-0.5 font-mono text-xs">
              llm_calls
            </code>{" "}
            — the same table the per-message transparency panel uses.
          </p>
        </div>
      </div>

      {error && (
        <p className="mt-6 rounded-lg bg-red-50 px-3.5 py-2.5 text-sm text-red-700 ring-1 ring-inset ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900">
          {error}
        </p>
      )}

      {!stats && !error && (
        <div className="mt-8 space-y-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <CardSkeleton key={i} className="h-24" />
            ))}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <CardSkeleton />
            <CardSkeleton />
          </div>
        </div>
      )}

      {stats && (
        <div className="mt-8 space-y-6">
          {stats.total_calls === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-line py-16 text-center">
              <span className="flex h-10 w-10 items-center justify-center rounded-full bg-inset text-subtle">
                <IconBarChart className="h-5 w-5" />
              </span>
              <p className="text-sm text-muted">
                No gateway calls yet. Ask a question in a notebook to see
                stats here.
              </p>
            </div>
          ) : (
            <>
              {/* KPI row — headline numbers, per dataviz "is it even a chart?": a
                  handful of current values is a stat-tile row, not a chart. */}
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatTile
                  label="Total calls"
                  value={stats.total_calls.toLocaleString()}
                />
                <StatTile
                  label="Total cost"
                  value={formatCost(stats.total_cost_usd)}
                />
                <StatTile
                  label="Cache hit rate"
                  value={`${Math.round(stats.cache_hit_rate * 100)}%`}
                  caption={`${stats.cache_hits} of ${stats.total_calls} calls`}
                />
                <StatTile
                  label="Avg latency"
                  value={formatLatency(stats.avg_latency_ms)}
                  caption={`p50 ${formatLatency(stats.p50_latency_ms)} · p95 ${formatLatency(stats.p95_latency_ms)}`}
                />
              </div>

              {/* Two single-series lines, never one dual-axis chart. */}
              <div className="grid gap-4 sm:grid-cols-2">
                <Card title="Calls per day" subtitle="Last 30 days">
                  <LineChart
                    data={stats.daily.map((d) => ({
                      label: shortDate(d.date),
                      value: d.calls,
                    }))}
                    color="var(--viz-series-1)"
                    formatValue={(v) => Math.round(v).toString()}
                  />
                </Card>
                <Card title="Cost per day" subtitle="Last 30 days">
                  <LineChart
                    data={stats.daily.map((d) => ({
                      label: shortDate(d.date),
                      value: d.cost_usd,
                    }))}
                    color="var(--viz-series-2)"
                    formatValue={formatCost}
                  />
                </Card>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Card
                  title="Calls by provider"
                  subtitle="Which providers actually answered"
                >
                  <BreakdownBars
                    items={stats.by_provider.map((p) => ({
                      label: p.provider,
                      value: p.calls,
                      secondary: formatCost(p.cost_usd),
                    }))}
                  />
                </Card>
                <Card title="Calls by model" subtitle="Primary vs. fallback model">
                  <BreakdownBars
                    items={stats.by_model.map((m) => ({
                      label: m.model,
                      value: m.calls,
                      secondary: formatCost(m.cost_usd),
                    }))}
                  />
                </Card>
              </div>

              <Card
                title="Call outcomes"
                subtitle="ok = primary succeeded · fallback = primary failed over · error = both providers failed"
              >
                <StatusBar counts={stats.by_status} />
              </Card>

              {stats.top_notebooks.length > 0 && (
                <Card title="Top notebooks by spend">
                  <div className="space-y-3">
                    {stats.top_notebooks.map((n) => {
                      const max = Math.max(
                        ...stats.top_notebooks.map((x) => x.cost_usd),
                        0.000001,
                      );
                      const pct = Math.max(4, (n.cost_usd / max) * 100);
                      return (
                        <div key={n.notebook_id}>
                          <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
                            <span className="truncate font-medium text-fg">
                              {n.title}
                            </span>
                            <span className="shrink-0 font-mono text-subtle">
                              {formatCost(n.cost_usd)} · {n.calls} calls
                            </span>
                          </div>
                          <div className="h-4 overflow-hidden rounded bg-inset">
                            <div
                              className="h-full rounded-r"
                              style={{
                                width: `${pct}%`,
                                backgroundColor: "var(--viz-series-1)",
                              }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </Card>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
