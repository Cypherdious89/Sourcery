"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { IconBarChart, IconLogOut, IconSparkles } from "./icons";
import { IconButton } from "./Button";
import { ThemeToggle } from "./ThemeToggle";

interface HeaderUser {
  email?: string | null;
  image?: string | null;
}

/**
 * Persistent app chrome: brand mark, primary nav, theme toggle, and — when
 * signed in — the account menu. Rendered once by AuthGate so every page
 * (notebook list, notebook detail, stats) shares one header instead of each
 * page inventing its own top bar.
 */
export function Header({
  user,
  onSignOut,
}: {
  user?: HeaderUser | null;
  onSignOut?: () => void;
}) {
  const pathname = usePathname();

  return (
    <header className="flex items-center gap-1 border-b border-line bg-surface px-4 py-2.5 sm:px-6">
      <Link href="/" className="flex items-center gap-2 pr-2">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-fg">
          <IconSparkles className="h-4 w-4" />
        </span>
        <span className="hidden text-sm font-semibold tracking-tight text-fg sm:inline">
          RAG Gateway
        </span>
      </Link>

      <nav className="flex items-center gap-0.5">
        <Link
          href="/stats"
          className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
            pathname === "/stats"
              ? "bg-inset text-fg"
              : "text-muted hover:bg-inset hover:text-fg"
          }`}
        >
          <IconBarChart className="h-3.5 w-3.5" />
          Stats
        </Link>
      </nav>

      <div className="ml-auto flex items-center gap-2.5">
        <ThemeToggle />
        {user && (
          <>
            <span className="h-4 w-px bg-line" aria-hidden />
            {user.image ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={user.image}
                alt=""
                className="h-6 w-6 rounded-full ring-1 ring-line"
              />
            ) : null}
            <span className="hidden max-w-[14ch] truncate text-xs text-muted sm:inline">
              {user.email}
            </span>
            <IconButton
              onClick={onSignOut}
              title="Sign out"
              aria-label="Sign out"
            >
              <IconLogOut className="h-4 w-4" />
            </IconButton>
          </>
        )}
      </div>
    </header>
  );
}
