"use client";

import { useState } from "react";

export interface LinePoint {
  label: string;
  value: number;
}

const WIDTH = 480;
const HEIGHT = 160;
const PAD_LEFT = 34;
const PAD_RIGHT = 8;
const PAD_TOP = 10;
const PAD_BOTTOM = 22;

/** Round a max value up to a "clean" tick per the marks spec (0 / 1,000 / …). */
function niceMax(max: number): number {
  if (max <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  const steps = [1, 2, 2.5, 5, 10];
  for (const step of steps) {
    const candidate = step * magnitude;
    if (candidate >= max) return candidate;
  }
  return magnitude * 10;
}

/**
 * A single-series line chart — no legend (one series names itself via the
 * card title), hairline gridlines, and a hover crosshair that snaps to the
 * nearest point. Built in plain SVG rather than a charting dependency.
 */
export function LineChart({
  data,
  color = "var(--viz-series-1)",
  formatValue = (v: number) => String(v),
}: {
  data: LinePoint[];
  color?: string;
  formatValue?: (v: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);

  if (data.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-xs text-subtle">
        No data yet
      </div>
    );
  }

  const max = niceMax(Math.max(...data.map((d) => d.value), 1));
  const innerW = WIDTH - PAD_LEFT - PAD_RIGHT;
  const innerH = HEIGHT - PAD_TOP - PAD_BOTTOM;
  const stepX = data.length > 1 ? innerW / (data.length - 1) : 0;

  const x = (i: number) => PAD_LEFT + i * stepX;
  const y = (v: number) => PAD_TOP + innerH * (1 - v / max);

  const path = data
    .map((d, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(d.value).toFixed(1)}`)
    .join(" ");

  const ticks = [0, max / 2, max];
  const active = hover !== null ? data[hover] : null;

  function handleMove(e: React.PointerEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * WIDTH;
    const idx = Math.round((px - PAD_LEFT) / (stepX || 1));
    setHover(Math.max(0, Math.min(data.length - 1, idx)));
  }

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full touch-none"
        role="img"
        aria-label="Line chart"
        onPointerMove={handleMove}
        onPointerLeave={() => setHover(null)}
      >
        {/* Gridlines — hairline, recessive, one step off the surface. */}
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={PAD_LEFT}
              x2={WIDTH - PAD_RIGHT}
              y1={y(t)}
              y2={y(t)}
              stroke="var(--line)"
              strokeWidth={1}
            />
            <text
              x={PAD_LEFT - 6}
              y={y(t)}
              textAnchor="end"
              dominantBaseline="middle"
              className="fill-subtle"
              fontSize={9}
            >
              {formatValue(t)}
            </text>
          </g>
        ))}

        {/* First/last x labels — enough to orient without crowding. */}
        <text
          x={x(0)}
          y={HEIGHT - 6}
          textAnchor="start"
          className="fill-subtle"
          fontSize={9}
        >
          {data[0].label}
        </text>
        <text
          x={x(data.length - 1)}
          y={HEIGHT - 6}
          textAnchor="end"
          className="fill-subtle"
          fontSize={9}
        >
          {data[data.length - 1].label}
        </text>

        {/* The line itself: 2px, round joins, a soft area wash beneath. */}
        <path
          d={`${path} L ${x(data.length - 1).toFixed(1)} ${y(0)} L ${x(0)} ${y(0)} Z`}
          fill={color}
          opacity={0.08}
        />
        <path
          d={path}
          fill="none"
          stroke={color}
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Crosshair + hovered point. */}
        {active && (
          <>
            <line
              x1={x(hover!)}
              x2={x(hover!)}
              y1={PAD_TOP}
              y2={HEIGHT - PAD_BOTTOM}
              stroke="var(--line)"
              strokeWidth={1}
            />
            <circle
              cx={x(hover!)}
              cy={y(active.value)}
              r={4}
              fill={color}
              stroke="var(--surface)"
              strokeWidth={2}
            />
          </>
        )}
      </svg>

      {active && (
        <div
          className="pointer-events-none absolute top-0 rounded-md border border-line bg-surface px-2 py-1 text-[10px] shadow-md"
          style={{
            left: `${(x(hover!) / WIDTH) * 100}%`,
            transform:
              hover! > data.length / 2 ? "translateX(-100%)" : "translateX(0)",
          }}
        >
          <div className="font-mono font-semibold text-fg">
            {formatValue(active.value)}
          </div>
          <div className="text-subtle">{active.label}</div>
        </div>
      )}
    </div>
  );
}
