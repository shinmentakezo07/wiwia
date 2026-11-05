// Shared hero backdrop — animated gradient beams drifting across a hero
// section. Extracted from the identical local copies in Landing.tsx and
// Pricing.tsx so every public hero uses the same motion language.

import { useId } from "react";
import { useReducedMotion } from "@/components/animated";

export interface HeroBeam {
  d: string;
  dur: number;
  delay: number;
  w: number;
  c0: string;
  c1: string;
}

/** Full set — Landing hero. */
export const HERO_BEAMS_FULL: readonly HeroBeam[] = [
  { d: "M 0,120 Q 300,60 600,140 T 1200,120", dur: 10, delay: 0, w: 1.5, c0: "#3b82f6", c1: "#8b5cf6" },
  { d: "M 0,220 Q 250,160 500,240 T 1200,200", dur: 12, delay: 1.5, w: 1, c0: "#8b5cf6", c1: "#ec4899" },
  { d: "M 0,320 Q 350,260 700,340 T 1200,300", dur: 14, delay: 0.8, w: 1.5, c0: "#22d3ee", c1: "#3b82f6" },
  { d: "M 0,80 Q 400,20 800,100 T 1200,60", dur: 11, delay: 2, w: 2, c0: "#d8b4fe", c1: "#67e8f9" },
  { d: "M 0,160 Q 200,100 500,180 T 1200,140", dur: 15, delay: 1.2, w: 2, c0: "#f9a8d4", c1: "#c4b5fd" },
  { d: "M 0,280 Q 450,220 800,300 T 1200,260", dur: 13, delay: 0.5, w: 1, c0: "#60a5fa", c1: "#22c55e" },
];

/** Compact set — Pricing hero. */
export const HERO_BEAMS_COMPACT: readonly HeroBeam[] = [
  { d: "M 0,120 Q 300,60 600,140 T 1200,120", dur: 10, delay: 0, w: 1.5, c0: "#3b82f6", c1: "#8b5cf6" },
  { d: "M 0,220 Q 250,160 500,240 T 1200,200", dur: 12, delay: 1.5, w: 1, c0: "#8b5cf6", c1: "#ec4899" },
  { d: "M 0,320 Q 350,260 700,340 T 1200,300", dur: 14, delay: 0.8, w: 1.5, c0: "#22d3ee", c1: "#3b82f6" },
  { d: "M 0,80 Q 400,20 800,100 T 1200,60", dur: 11, delay: 2, w: 1, c0: "#a78bfa", c1: "#22d3ee" },
];

export function HeroBeamBackdrop(props: {
  beams?: readonly HeroBeam[];
  className?: string;
}) {
  const id = useId().replace(/[:]/g, "");
  const beams = props.beams ?? HERO_BEAMS_FULL;
  const reducedMotion = useReducedMotion();
  return (
    <svg
      className={`pointer-events-none absolute inset-0 h-full w-full ${props.className ?? "opacity-70"}`}
      viewBox="0 0 1200 400"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden
    >
      <defs>
        {beams.map((b, i) => (
          <linearGradient
            key={i}
            id={`hb-${id}-${i}`}
            gradientUnits="userSpaceOnUse"
            x1="0"
            y1="0"
            x2="1200"
            y2="0"
            gradientTransform={
              reducedMotion ? undefined : `translate(${-120 - (960 * b.delay) / b.dur} 0)`
            }
          >
            <stop offset="0.3" stopColor={b.c0} stopOpacity="0" />
            <stop offset="0.4" stopColor={b.c0} stopOpacity={reducedMotion ? 0.65 : 1} />
            <stop offset="0.6" stopColor={b.c1} />
            <stop offset="1" stopColor={b.c1} stopOpacity="0" />
            {/* Translate the complete gradient rather than each stop, so SVG
                never reorders or clamps animated offsets. */}
            {!reducedMotion && (
              <animateTransform
                attributeName="gradientTransform"
                type="translate"
                values="0 0;1680 0"
                dur={`${b.dur}s`}
                begin={`${b.delay}s`}
                repeatCount="indefinite"
              />
            )}
          </linearGradient>
        ))}
      </defs>
      {beams.map((b, i) => (
        <g key={i}>
          <path d={b.d} fill="none" stroke="rgba(255,255,255,0.03)" strokeWidth={b.w} />
          <path
            d={b.d}
            fill="none"
            stroke={`url(#hb-${id}-${i})`}
            strokeWidth={b.w}
            strokeLinecap="round"
            strokeDasharray={reducedMotion ? "110 2000" : undefined}
          />
        </g>
      ))}
    </svg>
  );
}
