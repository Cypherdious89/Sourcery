"use client";

import { useCallback, useEffect, useState } from "react";
import {
  addFileSource,
  addUrlSource,
  deleteSource,
  listSources,
  type Source,
} from "./api";

const POLL_INTERVAL_MS = 1500;

function isInFlight(s: Source): boolean {
  return s.status === "pending" || s.status === "processing";
}

export interface UseSources {
  sources: Source[];
  loaded: boolean;
  error: string | null;
  busy: boolean;
  /** True while any source is still pending/processing. */
  polling: boolean;
  readyCount: number;
  addFile: (file: File) => Promise<void>;
  addFiles: (files: File[]) => Promise<void>;
  addUrl: (url: string) => Promise<void>;
  addUrls: (urls: string[]) => Promise<void>;
  removeSource: (sourceId: string) => Promise<void>;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/**
 * Owns a notebook's source list: initial load, plus re-polling while any
 * source is still ingesting (backend does parse/chunk/embed in a background
 * task, so status transitions pending → processing → ready|failed).
 *
 * Polling self-schedules and stops once everything settles; `reloadKey` bumps
 * restart it after a new source is added.
 */
export function useSources(notebookId: string): UseSources {
  const [sources, setSources] = useState<Source[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = () => {
      listSources(notebookId).then(
        (next) => {
          if (cancelled) return;
          setSources(next);
          setError(null);
          setLoaded(true);
          // Keep polling only while something is still ingesting.
          if (next.some(isInFlight)) {
            timer = setTimeout(tick, POLL_INTERVAL_MS);
          }
        },
        (e: unknown) => {
          if (cancelled) return;
          setError(message(e));
          setLoaded(true);
        },
      );
    };
    tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [notebookId, reloadKey]);

  const addFile = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      try {
        const created = await addFileSource(notebookId, file);
        // Show the pending row immediately, then let polling take over.
        setSources((cur) => [created, ...cur]);
        setReloadKey((k) => k + 1);
      } catch (e) {
        setError(message(e));
      } finally {
        setBusy(false);
      }
    },
    [notebookId],
  );

  /** Upload several files at once — each is its own request, run in parallel,
   * mirroring how a search-result batch is added via `addUrls`. */
  const addFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      setBusy(true);
      setError(null);
      const outcomes = await Promise.allSettled(
        files.map((f) => addFileSource(notebookId, f)),
      );
      const created = outcomes
        .filter((o) => o.status === "fulfilled")
        .map((o) => o.value);
      if (created.length > 0) setSources((cur) => [...created, ...cur]);

      const failed = outcomes.filter((o) => o.status === "rejected");
      if (failed.length > 0) {
        setError(
          `${failed.length} of ${files.length} file${files.length === 1 ? "" : "s"} failed to upload: ${message(failed[0].reason)}`,
        );
      }
      setReloadKey((k) => k + 1);
      setBusy(false);
    },
    [notebookId],
  );

  const addUrl = useCallback(
    async (url: string) => {
      setBusy(true);
      setError(null);
      try {
        const created = await addUrlSource(notebookId, url);
        setSources((cur) => [created, ...cur]);
        setReloadKey((k) => k + 1);
      } catch (e) {
        setError(message(e));
      } finally {
        setBusy(false);
      }
    },
    [notebookId],
  );

  /** Add several URLs at once (used by web-search results). */
  const addUrls = useCallback(
    async (urls: string[]) => {
      if (urls.length === 0) return;
      setBusy(true);
      setError(null);
      const outcomes = await Promise.allSettled(
        urls.map((u) => addUrlSource(notebookId, u)),
      );
      const created = outcomes
        .filter((o) => o.status === "fulfilled")
        .map((o) => o.value);
      if (created.length > 0) setSources((cur) => [...created, ...cur]);

      // Report failures without discarding the ones that worked.
      const failed = outcomes.filter((o) => o.status === "rejected");
      if (failed.length > 0) {
        setError(
          `${failed.length} of ${urls.length} source${urls.length === 1 ? "" : "s"} failed to add: ${message(failed[0].reason)}`,
        );
      }
      setReloadKey((k) => k + 1);
      setBusy(false);
    },
    [notebookId],
  );

  const removeSource = useCallback(
    async (sourceId: string) => {
      setBusy(true);
      setError(null);
      try {
        await deleteSource(notebookId, sourceId);
        setSources((cur) => cur.filter((s) => s.id !== sourceId));
      } catch (e) {
        setError(message(e));
      } finally {
        setBusy(false);
      }
    },
    [notebookId],
  );

  return {
    sources,
    loaded,
    error,
    busy,
    polling: sources.some(isInFlight),
    readyCount: sources.filter((s) => s.status === "ready").length,
    addFile,
    addFiles,
    addUrl,
    addUrls,
    removeSource,
  };
}
