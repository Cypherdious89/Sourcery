"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { createNotebook, listNotebooks, type Notebook } from "@/lib/api";
import { Button } from "@/components/Button";
import { IconChevronRight, IconNotebook, IconPlus } from "@/components/icons";

function relativeDate(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function NotebookCardSkeleton() {
  return (
    <div className="animate-pulse rounded-xl border border-line bg-surface p-4">
      <div className="h-8 w-8 rounded-lg bg-inset" />
      <div className="mt-3 h-4 w-3/4 rounded bg-inset" />
      <div className="mt-2 h-3 w-1/3 rounded bg-inset" />
    </div>
  );
}

export default function NotebooksPage() {
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listNotebooks()
      .then(setNotebooks)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : String(e)),
      )
      .finally(() => setLoaded(true));
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createNotebook(trimmed);
      setNotebooks((cur) => [created, ...cur]);
      setTitle("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-fg">
            Notebooks
          </h1>
          <p className="mt-1 text-sm text-muted">
            Create a notebook, add sources, then chat grounded in those
            sources.
          </p>
        </div>
      </div>

      <form onSubmit={handleCreate} className="mt-6 flex gap-2">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="New notebook title…"
          disabled={busy}
          className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3.5 py-2.5 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none disabled:opacity-50"
        />
        <Button
          type="submit"
          variant="primary"
          disabled={busy || !title.trim()}
        >
          <IconPlus className="h-4 w-4" />
          {busy ? "Creating…" : "Create"}
        </Button>
      </form>

      {error && (
        <p className="mt-4 rounded-lg bg-red-50 px-3.5 py-2.5 text-sm text-red-700 ring-1 ring-inset ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900">
          {error}
        </p>
      )}

      <div className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {!loaded &&
          Array.from({ length: 4 }).map((_, i) => (
            <NotebookCardSkeleton key={i} />
          ))}

        {loaded && notebooks.length === 0 && !error && (
          <div className="col-span-full flex flex-col items-center gap-2 rounded-xl border border-dashed border-line py-14 text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-inset text-subtle">
              <IconNotebook className="h-5 w-5" />
            </span>
            <p className="text-sm text-muted">
              No notebooks yet. Create one above.
            </p>
          </div>
        )}

        {notebooks.map((n) => (
          <Link
            key={n.id}
            href={`/notebooks/${n.id}`}
            className="group flex items-start gap-3 rounded-xl border border-line bg-surface p-4 transition-all hover:-translate-y-0.5 hover:border-accent-line hover:shadow-md"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-text">
              <IconNotebook className="h-4.5 w-4.5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-fg">
                {n.title}
              </span>
              <span className="mt-0.5 block text-xs text-subtle">
                {relativeDate(n.created_at)}
              </span>
            </span>
            <IconChevronRight className="mt-1.5 h-4 w-4 shrink-0 text-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
          </Link>
        ))}
      </div>
    </div>
  );
}
