// Shared hero backdrop — animated gradient beams drifting across a hero
// section. Extracted from the identical local copies in Landing.tsx and
// Pricing.tsx so every public hero uses the same motion language.

import { useId } from "react";

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
  { d: "M 0,80 Q 400,20 800,100 T 1200,60", dur: 11, delay: 2, w: 1, c0: "#a78bfa", c1: "#22d3ee" },
  { d: "M 0,160 Q 200,100 500,180 T 1200,140", dur: 15, delay: 1.2, w: 1, c0: "#f472b6", c1: "#a78bfa" },
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
  return (
    <svg
      className={`pointer-events-none absolute inset-0 h-full w-full ${props.className ?? "opacity-70"}`}
      viewBox="0 0 1200 400"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden
    >
      <defs>
        {beams.map((b, i) => (
          <linearGradient key={i} id={`hb-${id}-${i}`} gradientUnits="userSpaceOnUse">
            <stop stopColor={b.c0} stopOpacity="0">
              <animate attributeName="offset" values="-0.3;1" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
            <stop stopColor={b.c0}>
              <animate attributeName="offset" values="-0.1;1.1" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
            <stop offset="0.3" stopColor={b.c1}>
              <animate attributeName="offset" values="0;1.3" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
            <stop offset="1" stopColor={b.c1} stopOpacity="0">
              <animate attributeName="offset" values="0.3;1.6" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
          </linearGradient>
        ))}
      </defs>
      {beams.map((b, i) => (
        <g key={i}>
          <path d={b.d} fill="none" stroke="rgba(255,255,255,0.03)" strokeWidth={b.w} />
          <path d={b.d} fill="none" stroke={`url(#hb-${id}-${i})`} strokeWidth={b.w} strokeLinecap="round" />
        </g>
      ))}
    </svg>
  );
}
