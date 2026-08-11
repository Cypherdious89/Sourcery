"use client";

import { useSyncExternalStore } from "react";

type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "theme";

/**
 * localStorage is an external store, so it's read via `useSyncExternalStore`
 * rather than an effect + setState. That gives a correct SSR snapshot and
 * avoids the cascading-render problem of syncing external state into React
 * state on mount.
 */
const listeners = new Set<() => void>();

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  // `storage` fires for changes made in *other* tabs; `listeners` covers this one.
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function getSnapshot(): Theme {
  try {
    return (localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "system";
  } catch {
    return "system";
  }
}

/** The server has no localStorage; "system" matches what ThemeScript assumes. */
function getServerSnapshot(): Theme {
  return "system";
}

function resolve(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function apply(theme: Theme): void {
  document.documentElement.classList.toggle("dark", resolve(theme) === "dark");
}

function store(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Private mode / storage disabled — the class still applies for this page.
  }
  apply(theme);
  listeners.forEach((l) => l());
}

/**
 * Inline script that sets the theme class before first paint.
 *
 * Without it the page renders light and snaps to dark once React hydrates —
 * a visible flash. Must run synchronously in <head>.
 */
export function ThemeScript() {
  const js = `(function(){try{var t=localStorage.getItem("${STORAGE_KEY}")||"system";var d=t==="dark"||(t==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d)}catch(e){}})()`;
  return <script dangerouslySetInnerHTML={{ __html: js }} />;
}

const NEXT: Record<Theme, Theme> = {
  light: "dark",
  dark: "system",
  system: "light",
};
const LABEL: Record<Theme, string> = {
  light: "Light",
  dark: "Dark",
  system: "System",
};
const ICON: Record<Theme, string> = { light: "☀", dark: "☾", system: "◐" };

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  return (
    <button
      type="button"
      onClick={() => store(NEXT[theme])}
      title={`Theme: ${LABEL[theme]} (click to change)`}
      aria-label={`Theme: ${LABEL[theme]}`}
      className="flex h-7 shrink-0 cursor-pointer items-center gap-1.5 rounded-md border border-line px-2 text-xs text-muted transition-colors hover:text-fg"
    >
      <span aria-hidden>{ICON[theme]}</span>
      <span className="hidden sm:inline">{LABEL[theme]}</span>
    </button>
  );
}
