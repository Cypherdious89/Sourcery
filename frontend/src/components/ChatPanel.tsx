"use client";

import { useEffect, useRef, useState } from "react";
import {
  listMessages,
  regenerateAnswer,
  streamChat,
  type ChatResponse,
} from "@/lib/api";
import { useTypewriter } from "@/lib/useTypewriter";
import { AnswerWithCitations } from "./AnswerWithCitations";
import { buttonClass, IconButton } from "./Button";
import {
  IconArrowUp,
  IconRefresh,
  IconSparkles,
  IconSquare,
  IconUser,
} from "./icons";
import { TransparencyPanel } from "./TransparencyPanel";

interface UserMessage {
  id: string;
  role: "user";
  content: string;
}

interface AssistantMessage extends ChatResponse {
  id: string;
  role: "assistant";
}

interface ErrorMessage {
  id: string;
  role: "error";
  content: string;
}

/** What's left of an answer when the user hits Stop mid-stream. No citations
 * or transparency data — generation never reached the `done` event. */
interface StoppedMessage {
  id: string;
  role: "stopped";
  content: string;
}

type Message = UserMessage | AssistantMessage | ErrorMessage | StoppedMessage;

function Avatar({ role }: { role: "user" | "assistant" }) {
  return (
    <span
      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
        role === "user"
          ? "bg-fg text-canvas"
          : "bg-accent-soft text-accent-text"
      }`}
    >
      {role === "user" ? (
        <IconUser className="h-3.5 w-3.5" />
      ) : (
        <IconSparkles className="h-3.5 w-3.5" />
      )}
    </span>
  );
}

/** The answer currently being streamed, revealed character by character. */
function StreamingAnswer({ text }: { text: string }) {
  const shown = useTypewriter(text, true);
  return (
    <div className="flex items-start gap-2.5">
      <Avatar role="assistant" />
      <div className="min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-inset px-3.5 py-2.5 ring-1 ring-inset ring-line">
        {shown === "" ? (
          <p className="flex items-center gap-2 text-xs text-muted">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-subtle" />
            Retrieving chunks and calling the gateway…
          </p>
        ) : (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">
            {shown}
            {/* Blinking caret while more text is still arriving. */}
            <span className="ml-0.5 inline-block h-3.5 w-1.5 translate-y-0.5 animate-pulse bg-subtle" />
          </p>
        )}
      </div>
    </div>
  );
}

export function ChatPanel({
  notebookId,
  readyCount,
}: {
  notebookId: string;
  readyCount: number;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [streamText, setStreamText] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  // Message id currently being regenerated, if any — disables its button and
  // shows a spinner in place of the refresh icon.
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Stop needs the latest streamed text without waiting on a state update —
  // the click handler reads this ref rather than the (possibly one-frame-
  // stale) `streamText` state.
  const streamTextRef = useRef("");

  // Rehydrate the transcript: messages persist in `chat_messages`, so a reload
  // shouldn't lose them. Assistant turns come back with their gateway metadata
  // joined from `llm_calls`.
  useEffect(() => {
    let cancelled = false;
    listMessages(notebookId).then(
      (stored) => {
        if (cancelled) return;
        setMessages(
          stored.map((m) =>
            m.role === "user"
              ? { id: m.id, role: "user", content: m.content }
              : {
                  id: m.id,
                  role: "assistant",
                  answer: m.content,
                  citations: m.citations,
                  provider: m.provider ?? "unknown",
                  model: m.model ?? "",
                  status: m.status ?? "ok",
                  latency_ms: m.latency_ms ?? 0,
                  cost_usd: m.cost_usd ?? 0,
                  cache_hit: m.cache_hit ?? false,
                  message_id: m.id,
                },
          ),
        );
        setHistoryLoaded(true);
      },
      () => {
        // A failure here shouldn't block chatting — start from an empty panel.
        if (!cancelled) setHistoryLoaded(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [notebookId]);

  // Keep the newest content in view as it streams in.
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, streamText]);

  // Abandon an in-flight stream if the component goes away.
  useEffect(() => () => abortRef.current?.abort(), []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const query = input.trim();
    if (!query || pending) return;

    const stamp = `${Date.now()}`;
    setMessages((cur) => [
      ...cur,
      { id: `u-${stamp}`, role: "user", content: query },
    ]);
    setInput("");
    setPending(true);
    setStreamText("");
    streamTextRef.current = "";

    const controller = new AbortController();
    abortRef.current = controller;
    let accumulated = "";

    const fail = (message: string) =>
      setMessages((cur) => [
        ...cur,
        { id: `e-${stamp}`, role: "error", content: message },
      ]);

    try {
      await streamChat(
        notebookId,
        query,
        {
          onToken: (text) => {
            accumulated += text;
            streamTextRef.current = accumulated;
            setStreamText(accumulated);
          },
          onDone: (result) => {
            // Swap the streaming bubble for the finished message, which also
            // carries citations and the transparency metadata. Prefer the
            // real persisted id (needed for regenerate) over the synthetic
            // stamp — only the "no sources yet" canned reply lacks one.
            setMessages((cur) => [
              ...cur,
              { ...result, id: result.message_id ?? `a-${stamp}`, role: "assistant" },
            ]);
            setStreamText(null);
          },
          onError: (message) => {
            fail(message);
            setStreamText(null);
          },
        },
        controller.signal,
      );
    } catch (err) {
      if (!controller.signal.aborted) {
        fail(err instanceof Error ? err.message : String(err));
      }
      setStreamText(null);
    } finally {
      setPending(false);
      abortRef.current = null;
    }
  }

  /** Cut the stream short, keeping whatever text has arrived so far. Backend
   * behavior: aborting the fetch does not retract cost already spent with the
   * provider — the in-flight call finishes server-side and is still logged to
   * llm_calls. Only the client stops rendering further tokens. */
  function handleStop() {
    abortRef.current?.abort();
    const partial = streamTextRef.current;
    if (partial) {
      setMessages((cur) => [
        ...cur,
        { id: `stopped-${Date.now()}`, role: "stopped", content: partial },
      ]);
    }
    setStreamText(null);
    setPending(false);
  }

  /** Re-run an assistant message's answer in place, bypassing the LLM cache
   * server-side — a cache hit would just hand back the identical answer. */
  async function handleRegenerate(messageId: string) {
    if (regeneratingId) return;
    setRegeneratingId(messageId);
    try {
      const result = await regenerateAnswer(notebookId, messageId);
      setMessages((cur) =>
        cur.map((m) =>
          m.role === "assistant" && m.id === messageId
            ? { ...result, id: messageId, role: "assistant" }
            : m,
        ),
      );
    } catch (err) {
      setMessages((cur) => [
        ...cur,
        {
          id: `e-${Date.now()}`,
          role: "error",
          content: err instanceof Error ? err.message : String(err),
        },
      ]);
    } finally {
      setRegeneratingId(null);
    }
  }

  return (
    <section className="flex min-h-0 flex-col rounded-xl border border-line bg-surface">
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-fg">Chat</h2>
        <span className="ml-auto text-xs text-subtle">
          {readyCount === 0
            ? "no ready sources"
            : `grounded in ${readyCount} source${readyCount === 1 ? "" : "s"}`}
        </span>
      </header>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4"
      >
        {historyLoaded && messages.length === 0 && streamText === null && (
          <div className="py-10 text-center">
            <p className="text-sm text-subtle">
              Ask a question about this notebook&apos;s sources.
            </p>
            {readyCount === 0 && (
              <p className="mt-1 text-xs text-subtle">
                Add a source first — answers come only from ingested sources.
              </p>
            )}
          </div>
        )}

        {messages.map((m) => {
          if (m.role === "user") {
            return (
              <div key={m.id} className="flex items-start justify-end gap-2.5">
                <p className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-accent px-3.5 py-2.5 text-sm text-accent-fg">
                  {m.content}
                </p>
                <Avatar role="user" />
              </div>
            );
          }
          if (m.role === "error") {
            return (
              <div
                key={m.id}
                className="ml-9 rounded-xl bg-red-50 p-3 ring-1 ring-inset ring-red-200 dark:bg-red-950/40 dark:ring-red-900"
              >
                <p className="text-xs font-medium text-red-800 dark:text-red-300">
                  Request failed
                </p>
                <p className="mt-1 text-xs text-red-700 dark:text-red-300">{m.content}</p>
              </div>
            );
          }
          if (m.role === "stopped") {
            return (
              <div key={m.id} className="flex items-start gap-2.5">
                <Avatar role="assistant" />
                <div className="min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-inset px-3.5 py-2.5 ring-1 ring-inset ring-line">
                  <AnswerWithCitations answer={m.content} citations={[]} />
                  <p className="mt-2 flex items-center gap-1.5 border-t border-line pt-2 text-[10px] font-medium uppercase tracking-wide text-subtle">
                    <IconSquare className="h-2.5 w-2.5" />
                    Stopped before finishing
                  </p>
                </div>
              </div>
            );
          }
          return (
            <div key={m.id} className="flex items-start gap-2.5">
              <Avatar role="assistant" />
              <div className="min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-inset px-3.5 py-2.5 ring-1 ring-inset ring-line">
                <AnswerWithCitations answer={m.answer} citations={m.citations} />
                <TransparencyPanel meta={m} />
                {m.message_id && (
                  <div className="mt-2 flex justify-end border-t border-line pt-2">
                    <IconButton
                      onClick={() => handleRegenerate(m.message_id!)}
                      disabled={regeneratingId !== null}
                      aria-label="Regenerate answer"
                      title="Regenerate answer"
                      className="!h-6 !w-6 hover:text-accent"
                    >
                      <IconRefresh
                        className={`h-3 w-3 ${regeneratingId === m.message_id ? "animate-spin" : ""}`}
                      />
                    </IconButton>
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {streamText !== null && <StreamingAnswer text={streamText} />}
      </div>

      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-2 border-t border-line p-3"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question…"
          disabled={pending}
          className="min-w-0 flex-1 rounded-full border border-line bg-surface px-4 py-2.5 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none disabled:opacity-50"
        />
        {pending ? (
          <button
            type="button"
            onClick={handleStop}
            title="Stop generating"
            aria-label="Stop generating"
            className={buttonClass(
              "danger",
              "md",
              "!h-10 !w-10 !rounded-full !p-0",
            )}
          >
            <IconSquare className="h-3.5 w-3.5" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim()}
            title="Send"
            aria-label="Send"
            className={buttonClass(
              "accent",
              "md",
              "!h-10 !w-10 !rounded-full !p-0",
            )}
          >
            <IconArrowUp className="h-4 w-4" />
          </button>
        )}
      </form>
    </section>
  );
}
