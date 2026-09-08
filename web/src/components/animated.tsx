// Motion primitives for the console: a reduced-motion probe and a number that
// eases towards its target instead of snapping. Kept in its own module because
// both `ui.tsx` (stat tiles) and `console-visuals.tsx` (gauges, meters) depend
// on it — a shared leaf avoids an import cycle between them.

import { useEffect, useRef, useState } from "react";

/** True when the OS asks for reduced motion OR the console setting is on. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined") return false;
    return (
      window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
      document.documentElement.classList.contains("reduce-motion")
    );
  });
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () =>
      setReduced(
        mq.matches || document.documentElement.classList.contains("reduce-motion"),
      );
    const obs = new MutationObserver(update);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    mq.addEventListener("change", update);
    return () => {
      obs.disconnect();
      mq.removeEventListener("change", update);
    };
  }, []);
  return reduced;
}

/** Ease `value` towards its new target so counters tick instead of jumping. */
export function useAnimatedNumber(value: number, ms = 650): number {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(value);
  const shownRef = useRef(value);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    shownRef.current = shown;
  }, [shown]);

  useEffect(() => {
    const from = shownRef.current;
    if (from === value) return;
    if (reduced || ms <= 0) {
      setShown(value);
      return;
    }
    const t0 = performance.now();
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / ms);
      const eased = 1 - Math.pow(1 - p, 3);
      setShown(from + (value - from) * eased);
      if (p < 1) rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [value, ms, reduced]);

  return shown;
}

/** Mono, tabular value that counts up to `value` and formats on the fly. */
export function AnimatedNumber(props: {
  value: number;
  format: (n: number) => string;
  className?: string;
}) {
  const shown = useAnimatedNumber(props.value);
  return (
    <span className={`font-mono tabular-nums ${props.className ?? ""}`}>
      {props.format(shown)}
    </span>
  );
}
