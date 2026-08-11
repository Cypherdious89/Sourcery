"use client";

import { useRef, useState } from "react";
import type { Source } from "@/lib/api";
import type { UseSources } from "@/lib/useSources";
import { buttonClass, IconButton } from "./Button";
import { IconFileText, IconLoader, IconTrash, IconUpload } from "./icons";
import { StatusBadge } from "./StatusBadge";
import { WebSearchPanel } from "./WebSearchPanel";

const TYPE_LABEL: Record<Source["type"], string> = {
  pdf: "PDF",
  docx: "DOCX",
  url: "URL",
};

/** Add-a-source form plus the live ingestion-status list. */
export function SourcesPanel({ state }: { state: UseSources }) {
  const {
    sources,
    loaded,
    error,
    busy,
    polling,
    addFiles,
    addUrl,
    addUrls,
    removeSource,
  } = state;
  // URL-type sources store the URL in original_name_or_url, so search results
  // can be marked as already added.
  const existingUrls = new Set(
    sources.filter((s) => s.type === "url").map((s) => s.original_name_or_url),
  );
  const [url, setUrl] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    await addFiles(Array.from(files));
    // Reset so re-picking the same file(s) fires onChange again.
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleUrl(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed || busy) return;
    await addUrl(trimmed);
    setUrl("");
  }

  return (
    <section className="flex min-h-0 flex-col rounded-xl border border-line bg-surface">
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-fg">
          <IconFileText className="h-4 w-4 text-subtle" />
          Sources
        </h2>
        {polling && (
          <span className="flex items-center gap-1.5 text-xs text-blue-600 dark:text-blue-400">
            <IconLoader className="h-3 w-3" />
            ingesting…
          </span>
        )}
        <span className="ml-auto text-xs text-subtle">
          {sources.length} total
        </span>
      </header>

      <div className="space-y-3 border-b border-line px-4 py-3">
        <div>
          <label
            htmlFor="source-file"
            className="mb-1.5 block text-xs font-medium text-muted"
          >
            Upload PDF or DOCX — select multiple to batch-upload
          </label>
          <label
            htmlFor="source-file"
            className={buttonClass(
              "secondary",
              "sm",
              `w-full justify-center ${busy ? "pointer-events-none opacity-40" : "cursor-pointer"}`,
            )}
          >
            <IconUpload className="h-3.5 w-3.5" />
            {busy ? "Working…" : "Choose files"}
          </label>
          <input
            id="source-file"
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx"
            multiple
            disabled={busy}
            onChange={handleFiles}
            className="sr-only"
          />
        </div>

        <form onSubmit={handleUrl}>
          <label
            htmlFor="source-url"
            className="mb-1.5 block text-xs font-medium text-muted"
          >
            Or paste a webpage URL
          </label>
          <div className="flex gap-2">
            <input
              id="source-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://en.wikipedia.org/wiki/…"
              disabled={busy}
              className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={busy || !url.trim()}
              className={buttonClass("primary", "sm")}
            >
              Add
            </button>
          </div>
        </form>

        {error && (
          <p className="rounded-md bg-red-50 px-2.5 py-2 text-xs text-red-700 ring-1 ring-inset ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900">
            {error}
          </p>
        )}
      </div>

      <WebSearchPanel
        existingUrls={existingUrls}
        busy={busy}
        onAdd={addUrls}
      />

      <ul className="min-h-0 flex-1 divide-y divide-line overflow-y-auto">
        {!loaded && (
          <li className="px-4 py-3 text-xs text-subtle">Loading sources…</li>
        )}
        {loaded && sources.length === 0 && (
          <li className="px-4 py-6 text-center text-xs text-subtle">
            No sources yet. Add one above to start chatting.
          </li>
        )}
        {sources.map((s) => (
          <li key={s.id} className="flex items-start gap-2 px-4 py-2.5">
            <span className="mt-0.5 shrink-0 rounded bg-inset px-1.5 py-0.5 font-mono text-[10px] font-medium text-muted">
              {TYPE_LABEL[s.type]}
            </span>
            <span className="min-w-0 flex-1">
              <span
                className="block truncate text-xs text-fg"
                title={s.original_name_or_url}
              >
                {s.original_name_or_url}
              </span>
              {s.status === "failed" && (
                <span className="mt-0.5 block text-[10px] text-red-600 dark:text-red-400">
                  Ingestion failed — check the backend logs.
                </span>
              )}
            </span>
            <StatusBadge status={s.status} />
            <IconButton
              onClick={() => removeSource(s.id)}
              disabled={busy}
              aria-label={`Delete ${s.original_name_or_url}`}
              title="Delete source"
              className="hover:text-red-600 dark:hover:text-red-400"
            >
              <IconTrash className="h-3.5 w-3.5" />
            </IconButton>
          </li>
        ))}
      </ul>
    </section>
  );
}
