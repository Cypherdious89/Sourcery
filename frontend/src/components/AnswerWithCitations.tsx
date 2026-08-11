"use client";

import { Fragment, type ReactNode, useRef, useState } from "react";
import type { Citation } from "@/lib/api";

/** Inline tokens we format: `**bold**` and `[S1]` citation markers.
 *
 * The `S` prefix matters: retrieved chunks (Wikipedia especially) carry their
 * own `[1]`-style footnotes, which a bare-digit pattern would render as
 * clickable citations. */
const INLINE_SPLIT = /(\*\*[^*]+\*\*|\[S\d+\])/g;
const BULLET = /^\s*[-*]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;

const TOOLTIP_WIDTH = 288; // w-72

function CitationChip({
  citation,
  active,
  onClick,
}: {
  citation: Citation;
  active: boolean;
  onClick: () => void;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [alignRight, setAlignRight] = useState(false);

  // Flip the tooltip when a left-aligned one would run off the viewport.
  function handleEnter() {
    const rect = ref.current?.getBoundingClientRect();
    if (rect) setAlignRight(rect.left + TOOLTIP_WIDTH > window.innerWidth - 16);
  }

  return (
    <span
      ref={ref}
      onMouseEnter={handleEnter}
      className="group relative inline-block align-baseline"
    >
      <button
        type="button"
        onClick={onClick}
        aria-label={`Citation ${citation.marker}: ${citation.snippet}`}
        className={`mx-0.5 inline-flex h-4 min-w-4 cursor-pointer items-center justify-center rounded px-1 align-baseline font-mono text-[10px] font-semibold ring-1 ring-inset transition-colors ${
          active
            ? "bg-accent text-accent-fg ring-accent"
            : "bg-accent-soft text-accent-text ring-accent-line hover:bg-accent-soft"
        }`}
      >
        {citation.marker}
      </button>
      {/* Hover preview, opening downward to avoid clipping at the top of the
          scrolling chat container. */}
      <span
        className={`pointer-events-none absolute top-full z-20 mt-1 hidden w-72 rounded-md bg-fg p-2 text-xs font-normal leading-snug text-canvas shadow-lg group-hover:block ${
          alignRight ? "right-0" : "left-0"
        }`}
      >
        {citation.snippet}
      </span>
    </span>
  );
}

/**
 * Renders one line of answer text: `**bold**` becomes <strong>, and `[S1]`
 * markers become clickable chips when they map to a real citation.
 *
 * Unmatched markers stay literal.
 */
function renderInline(
  line: string,
  byMarker: Map<number, Citation>,
  activeMarker: number | null,
  onToggle: (marker: number) => void,
): ReactNode[] {
  return line.split(INLINE_SPLIT).map((token, i) => {
    const bold = /^\*\*([^*]+)\*\*$/.exec(token);
    if (bold) return <strong key={i}>{bold[1]}</strong>;

    const marker = /^\[S(\d+)\]$/.exec(token);
    const citation = marker ? byMarker.get(Number(marker[1])) : undefined;
    if (citation) {
      return (
        <CitationChip
          key={i}
          citation={citation}
          active={activeMarker === citation.marker}
          onClick={() => onToggle(citation.marker)}
        />
      );
    }
    return <Fragment key={i}>{token}</Fragment>;
  });
}

/** Groups raw answer lines into paragraphs, bullet lists, and headings. */
function renderBlocks(
  answer: string,
  byMarker: Map<number, Citation>,
  activeMarker: number | null,
  onToggle: (marker: number) => void,
): ReactNode[] {
  const inline = (s: string) => renderInline(s, byMarker, activeMarker, onToggle);
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];

  const flushBullets = () => {
    if (bullets.length === 0) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="ml-4 list-disc space-y-1">
        {bullets.map((b, i) => (
          <li key={i}>{inline(b)}</li>
        ))}
      </ul>,
    );
    bullets = [];
  };

  for (const line of answer.split("\n")) {
    const bullet = BULLET.exec(line);
    if (bullet) {
      bullets.push(bullet[1]);
      continue;
    }
    flushBullets();

    if (line.trim() === "") continue;

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push(
        <p key={`h-${blocks.length}`} className="font-semibold">
          {inline(heading[1])}
        </p>,
      );
      continue;
    }
    blocks.push(<p key={`p-${blocks.length}`}>{inline(line)}</p>);
  }
  flushBullets();
  return blocks;
}

/**
 * An assistant answer with inline citation chips and the resolved source
 * snippets underneath. Clicking a chip or a snippet pins that citation.
 */
export function AnswerWithCitations({
  answer,
  citations,
}: {
  answer: string;
  citations: Citation[];
}) {
  const [activeMarker, setActiveMarker] = useState<number | null>(null);
  const byMarker = new Map(citations.map((c) => [c.marker, c]));
  const toggle = (marker: number) =>
    setActiveMarker((cur) => (cur === marker ? null : marker));

  return (
    <div>
      <div className="space-y-2 text-sm leading-relaxed text-fg">
        {renderBlocks(answer, byMarker, activeMarker, toggle)}
      </div>

      {citations.length > 0 && (
        <div className="mt-3 space-y-1.5">
          <p className="text-[10px] font-medium uppercase tracking-wide text-subtle">
            {citations.length} cited source{citations.length === 1 ? "" : "s"}
          </p>
          {citations.map((c) => {
            const active = activeMarker === c.marker;
            return (
              <button
                key={c.chunk_id}
                type="button"
                onClick={() => toggle(c.marker)}
                className={`flex w-full cursor-pointer gap-2 rounded-md p-2 text-left ring-1 ring-inset transition-colors ${
                  active
                    ? "bg-accent-soft ring-accent-line"
                    : "bg-inset ring-line hover:bg-inset"
                }`}
              >
                <span className="mt-px inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded bg-accent px-1 font-mono text-[10px] font-semibold text-accent-fg">
                  {c.marker}
                </span>
                <span className="min-w-0 flex-1">
                  <span
                    className={`block text-xs leading-snug text-muted ${active ? "" : "line-clamp-2"}`}
                  >
                    {c.snippet}
                  </span>
                  {active && (
                    <span className="mt-1.5 block font-mono text-[10px] text-subtle">
                      chunk {c.chunk_id.slice(0, 8)} · source{" "}
                      {c.source_id.slice(0, 8)}
                    </span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
