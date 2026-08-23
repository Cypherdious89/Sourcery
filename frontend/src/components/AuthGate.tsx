"use client";

import { SessionProvider, signIn, signOut, useSession } from "next-auth/react";
import { useEffect, useState } from "react";
import { getAuthConfig, setAuthTokenGetter } from "@/lib/api";
import { Button } from "./Button";
import { Header } from "./Header";
import { IconGoogle, IconSparkles } from "./icons";

/**
 * Gates the app on Google sign-in — but only when the backend says so.
 *
 * The backend is the source of truth (`GET /auth/config`): with no
 * GOOGLE_CLIENT_ID configured it runs in local-dev mode and the app renders
 * straight through, so everything still works without an OAuth client.
 *
 * `SessionProvider` is mounted *only* in that auth-on branch. Mounting it
 * unconditionally makes it poll `/api/auth/session`, which 500s when Auth.js
 * has no AUTH_SECRET — filling the console with errors on a deployment that
 * deliberately runs without auth.
 */

function Loading() {
  return (
    <div className="flex flex-1 items-center justify-center">
      <p className="text-sm text-subtle">Loading…</p>
    </div>
  );
}

function SignedIn({ children }: { children: React.ReactNode }) {
  const { data: session, status } = useSession();

  // Hand the API client a way to read the current token. Set synchronously
  // during render, not in a useEffect — child effects (e.g. the notebooks
  // page's initial fetch) run before a parent's own effect in the same
  // commit, so an effect here would fire too late and every first request
  // after sign-in would go out with no bearer token.
  setAuthTokenGetter(() => session?.idToken);
  useEffect(() => {
    return () => setAuthTokenGetter(() => undefined);
  }, []);

  if (status === "loading") return <Loading />;

  if (!session) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-sm rounded-2xl border border-line bg-surface p-8 text-center shadow-sm">
          <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-accent-fg">
            <IconSparkles className="h-5 w-5" />
          </span>
          <h1 className="mt-4 text-lg font-semibold text-fg">
            NotebookLM RAG Gateway
          </h1>
          <p className="mt-1.5 text-sm text-muted">
            Sign in to create notebooks and chat with your sources.
          </p>
          <Button
            variant="primary"
            className="mt-6 w-full"
            onClick={() => signIn("google")}
          >
            <IconGoogle className="h-4 w-4" />
            Continue with Google
          </Button>
        </div>
      </div>
    );
  }

  return (
    <>
      <Header user={session.user} onSignOut={() => signOut()} />
      {children}
    </>
  );
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAuthConfig().then(
      (c) => {
        if (!cancelled) setAuthRequired(c.auth_required);
      },
      // Backend unreachable: don't trap the user behind a login screen — let
      // the app render and surface the real connection error instead.
      () => {
        if (!cancelled) setAuthRequired(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  if (authRequired === null) return <Loading />;
  if (!authRequired) {
    return (
      <>
        <Header />
        {children}
      </>
    );
  }

  return (
    <SessionProvider>
      <SignedIn>{children}</SignedIn>
    </SessionProvider>
  );
}
