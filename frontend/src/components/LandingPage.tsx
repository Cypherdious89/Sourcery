import { Button } from "./Button";
import {
  IconBarChart,
  IconCheck,
  IconFileText,
  IconGlobe,
  IconGoogle,
  IconRefresh,
  IconSparkles,
} from "./icons";

const FEATURES = [
  {
    icon: IconCheck,
    title: "Grounded, cited answers",
    body: "Every answer traces back to your sources — click a [S1] marker to jump straight to the passage it came from.",
  },
  {
    icon: IconRefresh,
    title: "Resilient LLM gateway",
    body: "A rate-limit-aware fallback chain across Gemini and Groq means one provider having a bad day doesn't take your notebook down with it.",
  },
  {
    icon: IconBarChart,
    title: "Full cost & latency transparency",
    body: "Every call is logged — provider, model, cost, cache hit, latency — visible per message and rolled up on the stats page.",
  },
  {
    icon: IconGlobe,
    title: "PDFs, docs, and the open web",
    body: "Upload files, paste a URL, or search the web for sources — everything gets chunked, embedded, and made citable the same way.",
  },
];

/**
 * The signed-out landing page. Rendered by AuthGate in place of the app when
 * there's no session — this is most first-time visitors' only look at the
 * product before deciding whether to sign in, so it earns being more than a
 * bare "Continue with Google" box.
 */
export function LandingPage({ onSignIn }: { onSignIn: () => void }) {
  return (
    <div className="flex flex-1 items-center justify-center overflow-y-auto p-6">
      <div className="w-full max-w-3xl py-10">
        <div className="flex flex-col items-center text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent text-accent-fg shadow-sm">
            <IconSparkles className="h-6 w-6" />
          </span>
          <h1 className="mt-5 text-3xl font-semibold tracking-tight text-fg sm:text-4xl">
            Sourcery
          </h1>
          <p className="mt-3 max-w-lg text-balance text-base text-muted">
            Turn your PDFs, docs, and web pages into a notebook you can chat
            with — every answer cited, every call transparent.
          </p>

          <Button
            variant="primary"
            className="mt-8 !h-11 !px-6 !text-base"
            onClick={onSignIn}
          >
            <IconGoogle className="h-4 w-4" />
            Continue with Google
          </Button>
          <p className="mt-2.5 text-xs text-subtle">
            Free to use — your notebooks are private to your account.
          </p>
        </div>

        <div className="mt-14 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <div
              key={title}
              className="rounded-2xl border border-line bg-surface p-5 text-left"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-soft text-accent-text">
                <Icon className="h-4.5 w-4.5" />
              </span>
              <h2 className="mt-3.5 text-sm font-semibold text-fg">{title}</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">
                {body}
              </p>
            </div>
          ))}
        </div>

        <div className="mt-10 flex items-center justify-center gap-2.5 text-xs text-subtle">
          <IconFileText className="h-3.5 w-3.5" />
          <span>PDF · DOCX · URL · web search — pick any combination</span>
        </div>
      </div>
    </div>
  );
}
