"use client";

import { useEffect, useState } from "react";
import { getSearchStatus, searchWeb, type SearchResult } from "@/lib/api";
import { buttonClass } from "./Button";
import { IconChevronRight, IconGlobe } from "./icons";

/**
 * Search the web and pick results to add as notebook sources.
 *
 * Picked results go through the ordinary URL ingestion path, so nothing about
 * retrieval or citations changes — these become normal `url` sources.
 *
 * The results list collapses into a disclosure once there's something to
 * summarize: right after a search returns, and again automatically once a
 * selection is added. This keeps a stale results list from permanently
 * occupying space in the Sources panel — the query input itself always stays
 * visible so a new search is one click away.
 */
export function WebSearchPanel({
  existingUrls,
  busy,
  onAdd,
}: {
  /** URLs already in the notebook, so results can be marked as added. */
  existingUrls: Set<string>;
  busy: boolean;
  onAdd: (urls: string[]) => Promise<void>;
}) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [query, setQuery] = useState("");
  const [lastQuery, setLastQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Whether the results dropdown is open. A fresh search opens it so the user
  // can act on it immediately; adding sources closes it again.
  const [resultsOpen, setResultsOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSearchStatus().then(
      (s) => {
        if (!cancelled) setConfigured(s.configured);
      },
      () => {
        if (!cancelled) setConfigured(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  // Feature is off (or the backend is unreachable) — render nothing.
  if (configured === null || configured === false) return null;

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q || searching) return;
    setSearching(true);
    setError(null);
    try {
      const res = await searchWeb(q);
      setResults(res.results);
      setLastQuery(q);
      setSelected(new Set());
      setResultsOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setResults(null);
    } finally {
      setSearching(false);
    }
  }

  function toggle(url: string) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  }

  async function handleAdd() {
    const urls = [...selected];
    if (urls.length === 0) return;
    await onAdd(urls);
    setSelected(new Set());
    // Job done — collapse back to the summary row rather than leaving a
    // fully-expanded, now mostly-"already added" list sitting open.
    setResultsOpen(false);
  }

  const addedCount = results?.filter((r) => existingUrls.has(r.url)).length ?? 0;

  return (
    <div className="border-t border-line px-4 py-3">
      <form onSubmit={handleSearch}>
        <label
          htmlFor="source-search"
          className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted"
        >
          <IconGlobe className="h-3.5 w-3.5" />
          Or search the web
        </label>
        <div className="flex gap-2">
          <input
            id="source-search"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="vector databases…"
            disabled={searching || busy}
            className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={searching || busy || !query.trim()}
            className={buttonClass("secondary", "sm")}
          >
            {searching ? "…" : "Search"}
          </button>
        </div>
      </form>

      {error && (
        <p className="mt-2 rounded-md bg-red-50 px-2.5 py-2 text-xs text-red-700 ring-1 ring-inset ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900">
          {error}
        </p>
      )}

      {results !== null && (
        <div className="mt-2 rounded-lg ring-1 ring-inset ring-line">
          <button
            type="button"
            onClick={() => setResultsOpen((v) => !v)}
            aria-expanded={resultsOpen}
            className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs text-muted transition-colors hover:text-fg"
          >
            <IconChevronRight
              className={`h-3 w-3 shrink-0 transition-transform ${resultsOpen ? "rotate-90" : ""}`}
            />
            <span className="truncate">
              {results.length === 0
                ? `No results for "${lastQuery}"`
                : `${results.length} result${results.length === 1 ? "" : "s"} for "${lastQuery}"`}
            </span>
            {addedCount > 0 && (
              <span className="ml-auto shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300">
                {addedCount} added
              </span>
            )}
          </button>

          {resultsOpen && results.length > 0 && (
            <div className="border-t border-line p-2">
              <ul className="max-h-64 space-y-1 overflow-y-auto">
                {results.map((r) => {
                  const added = existingUrls.has(r.url);
                  const checked = selected.has(r.url);
                  return (
                    <li key={r.url}>
                      <label
                        className={`flex cursor-pointer gap-2 rounded-md p-2 ring-1 ring-inset transition-colors ${
                          added
                            ? "cursor-not-allowed bg-inset ring-line opacity-60"
                            : checked
                              ? "bg-accent-soft ring-accent-line"
                              : "bg-surface ring-line hover:bg-inset"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={added || busy}
                          onChange={() => toggle(r.url)}
                          className="mt-0.5 shrink-0 accent-accent"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-xs font-medium text-fg">
                            {r.title}
                          </span>
                          <span className="block truncate text-[10px] text-subtle">
                            {r.url}
                          </span>
                          {r.snippet && (
                            <span className="mt-0.5 line-clamp-2 block text-[10px] leading-snug text-muted">
                              {r.snippet}
                            </span>
                          )}
                          {added && (
                            <span className="mt-0.5 block text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                              already added
                            </span>
                          )}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>

              <button
                type="button"
                onClick={handleAdd}
                disabled={selected.size === 0 || busy}
                className={buttonClass("accent", "sm", "mt-2 w-full")}
              >
                {busy
                  ? "Adding…"
                  : selected.size === 0
                    ? "Select results to add"
                    : `Add ${selected.size} selected source${selected.size === 1 ? "" : "s"}`}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
