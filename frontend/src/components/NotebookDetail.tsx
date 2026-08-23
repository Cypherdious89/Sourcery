"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  deleteNotebook,
  exportNotebook,
  getNotebook,
  renameNotebook,
  type Notebook,
} from "@/lib/api";
import { useSources } from "@/lib/useSources";
import { Button } from "./Button";
import { ChatPanel } from "./ChatPanel";
import { IconArrowLeft, IconDownload, IconPencil, IconTrash } from "./icons";
import { SourcesPanel } from "./SourcesPanel";

export function NotebookDetail({ notebookId }: { notebookId: string }) {
  const [notebook, setNotebook] = useState<Notebook | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Source state lives here so the chat panel can see how many are ready.
  const sources = useSources(notebookId);
  const router = useRouter();
  const [deleting, setDeleting] = useState(false);
  const [exporting, setExporting] = useState(false);

  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    getNotebook(notebookId).then(
      (nb) => {
        if (!cancelled) setNotebook(nb);
      },
      (e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [notebookId]);

  useEffect(() => {
    if (editing) titleInputRef.current?.select();
  }, [editing]);

  function startEditingTitle() {
    setDraftTitle(notebook?.title ?? "");
    setEditing(true);
  }

  async function commitTitle() {
    setEditing(false);
    const trimmed = draftTitle.trim();
    if (!trimmed || !notebook || trimmed === notebook.title) return;
    // Optimistic: the input already shows the new value.
    setNotebook({ ...notebook, title: trimmed });
    try {
      const updated = await renameNotebook(notebookId, trimmed);
      setNotebook(updated);
    } catch (e) {
      setNotebook(notebook); // revert on failure
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleExport() {
    setExporting(true);
    try {
      const { blob, filename } = await exportNotebook(notebookId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }

  async function handleDelete() {
    if (
      !window.confirm(
        "Delete this notebook? Its sources, chunks, and chat history are removed too.",
      )
    )
      return;
    setDeleting(true);
    try {
      await deleteNotebook(notebookId);
      router.push("/");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDeleting(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-4 sm:p-6">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link
          href="/"
          className="flex items-center gap-1 text-xs text-muted transition-colors hover:text-fg"
        >
          <IconArrowLeft className="h-3.5 w-3.5" />
          Notebooks
        </Link>
        <span className="text-line">/</span>

        {editing ? (
          <input
            ref={titleInputRef}
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onBlur={commitTitle}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitTitle();
              } else if (e.key === "Escape") {
                setEditing(false);
              }
            }}
            className="rounded-md border border-accent bg-surface px-1.5 py-0.5 text-lg font-semibold text-fg focus:outline-none"
          />
        ) : (
          <button
            type="button"
            onClick={startEditingTitle}
            title="Rename notebook"
            className="group -mx-1.5 flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-left transition-colors hover:bg-inset"
          >
            <h1 className="text-lg font-semibold text-fg">
              {notebook?.title ?? (error ? "Notebook" : "Loading…")}
            </h1>
            <IconPencil className="h-3.5 w-3.5 text-subtle opacity-0 transition-opacity group-hover:opacity-100" />
          </button>
        )}

        <span className="font-mono text-[10px] text-subtle">{notebookId}</span>

        <Button
          variant="secondary"
          size="sm"
          disabled={exporting}
          onClick={handleExport}
          className="ml-auto"
        >
          <IconDownload className="h-3.5 w-3.5" />
          {exporting ? "Exporting…" : "Export"}
        </Button>

        <Button
          variant="danger"
          size="sm"
          disabled={deleting}
          onClick={handleDelete}
        >
          <IconTrash className="h-3.5 w-3.5" />
          {deleting ? "Deleting…" : "Delete"}
        </Button>
      </header>

      {error && (
        <p className="rounded-lg bg-red-50 px-3.5 py-2.5 text-sm text-red-700 ring-1 ring-inset ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900">
          {error}
        </p>
      )}

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
        <SourcesPanel state={sources} />
        <ChatPanel notebookId={notebookId} readyCount={sources.readyCount} />
      </div>
    </div>
  );
}
