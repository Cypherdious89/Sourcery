"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Reveals `target` character by character.
 *
 * Providers don't stream one letter at a time — Gemini delivers a handful of
 * large chunks (often 100+ characters each), so painting deltas directly makes
 * the answer appear in visible jumps. This drains the backlog on every animation
 * frame instead, which reads like ChatGPT regardless of how the provider
 * actually chunks its output.
 *
 * The drain rate scales with how far behind we are, so a big burst catches up
 * quickly rather than falling further behind the stream.
 */
export function useTypewriter(target: string, active: boolean): string {
  const [shown, setShown] = useState("");
  const targetRef = useRef(target);

  // Kept in an effect rather than assigned during render: the animation loop
  // reads this outside of render, so it must not be mutated mid-render.
  useEffect(() => {
    targetRef.current = target;
  }, [target]);

  useEffect(() => {
    if (!active) {
      // Finished (or never started): show everything immediately.
      setShown(targetRef.current);
      return;
    }

    let frame = 0;
    const tick = () => {
      setShown((cur) => {
        const full = targetRef.current;
        // Identical reference lets React bail out of the re-render.
        if (cur.length >= full.length) return cur;
        const backlog = full.length - cur.length;
        const step = Math.max(1, Math.ceil(backlog / 8));
        return full.slice(0, cur.length + step);
      });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [active]);

  return active ? shown : target;
}
