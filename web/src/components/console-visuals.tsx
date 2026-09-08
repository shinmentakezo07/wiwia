// Console visual primitives — the richer, motion-aware building blocks used by
// the Dashboard / Usage / Analytics pages: count-up values, a live per-second
// pulse meter, share bars, a token-mix ribbon, gauge rings, a latency
// histogram ribbon, a status mix bar, and a provider health grid.
//
// Everything here is presentational and derives from data the caller already
// has — no fetching, no page-specific logic. All motion is gated on the
// user's reduced-motion preference (OS media query or the console setting).

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Activity, AlertTriangle, Gauge, ShieldCheck, Timer } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, CardHeader, EmptyState } from "@/components/ui";
import { AnimatedNumber } from "@/components/animated";
import { fmtInt, fmtPct, fmtTokens } from "@/lib/format";

// -- live pulse meter --------------------------------------------------------

export interface PulseEvent {
  ts: number;
  failed?: boolean;
}

interface PulseSlot {
  start: number;
  total: number;
  failed: number;
}

/** Bucket events into fixed slots across the trailing `seconds` window. */
function pulseSlots(
  events: PulseEvent[],
  seconds: number,
  slotSeconds: number,
  nowSec: number,
): PulseSlot[] {
  const slots = Math.max(1, Math.round(seconds / slotSeconds));
  const out: PulseSlot[] = [];
  for (let i = slots - 1; i >= 0; i--) {
    out.push({ start: nowSec - i * slotSeconds, total: 0, failed: 0 });
  }
  const first = out[0].start;
  for (const e of events) {
    if (e.ts < first) continue;
    const idx = Math.min(slots - 1, Math.floor((e.ts - first) / slotSeconds));
    const slot = out[idx];
    slot.total += 1;
    if (e.failed) slot.failed += 1;
  }
  return out;
}

/**
 * Per-second (or per-slot) request meter: one vertical bar per slot, with the
 * failed share stacked in red and the newest slot glowing. Reads as a live
 * "heartbeat" for traffic that is too sparse for a 30-minute chart.
 */
export function LivePulseMeter(props: {
  events: PulseEvent[];
  connected: boolean;
  /** Width of the trailing window, default 60s. */
  seconds?: number;
  /** Bucket width, default 2s (30 bars at 60s). */
  slotSeconds?: number;
  height?: number;
}) {
  const seconds = props.seconds ?? 60;
  const slotSeconds = props.slotSeconds ?? 2;
  // Re-bucket on a 1s heartbeat so the window keeps sliding even when idle.
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const id = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(id);
  }, []);

  const slots = pulseSlots(props.events, seconds, slotSeconds, nowSec);
  const peak = Math.max(1, ...slots.map((s) => s.total));
  const total = slots.reduce((a, s) => a + s.total, 0);
  const failed = slots.reduce((a, s) => a + s.failed, 0);
  const perSec = total / seconds;

  return (
    <div className="px-4 pb-4 pt-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <AnimatedNumber
            value={perSec}
            format={(v) => v.toFixed(2)}
            className="text-[22px] font-bold leading-none tracking-[-0.02em] text-[var(--admin-text)]"
          />
          <span className="admin-label">req / s</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
          <span>{fmtInt(total)} in {seconds}s</span>
          <span className={failed > 0 ? "text-red-400" : undefined}>
            {fmtInt(failed)} failed
          </span>
          <span>peak {fmtInt(peak)}/{slotSeconds}s</span>
        </div>
      </div>

      <div
        className="flex items-end gap-[2px]"
        style={{ height: props.height ?? 96 }}
        role="img"
        aria-label={`${fmtInt(total)} requests in the last ${seconds} seconds, ${fmtInt(failed)} failed`}
      >
        {slots.map((s, i) => {
          const isLast = i === slots.length - 1;
          const h = s.total === 0 ? 2 : Math.max(6, (s.total / peak) * 100);
          const errH = s.total > 0 ? (s.failed / s.total) * h : 0;
          return (
            <div
              key={s.start}
              className="relative flex-1 overflow-hidden rounded-[2px] transition-[height] duration-500 ease-out"
              style={{
                height: `${h}%`,
                background: "rgba(255,255,255,0.045)",
              }}
              title={`${s.total} request${s.total === 1 ? "" : "s"}${s.failed ? ` · ${s.failed} failed` : ""}`}
            >
              <span
                className="absolute inset-x-0 bottom-0 block bg-gradient-to-t from-blue-500/25 to-blue-400/70"
                style={{
                  height: `${Math.max(0, 100 - (s.total ? (s.failed / s.total) * 100 : 0))}%`,
                }}
              />
              {errH > 0 && (
                <span
                  className="absolute inset-x-0 top-0 block bg-gradient-to-b from-red-400/80 to-red-500/40"
                  style={{ height: `${(s.failed / s.total) * 100}%` }}
                />
              )}
              {isLast && (
                <span
                  className={`absolute inset-0 rounded-[2px] ring-1 ring-inset ${
                    props.connected ? "ring-blue-400/50" : "ring-white/10"
                  }`}
                  style={
                    props.connected
                      ? { boxShadow: "0 0 12px -2px rgba(59,130,246,0.55)" }
                      : undefined
                  }
                />
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-2 flex justify-between font-mono text-[10px] tracking-wider text-[var(--admin-text-dim)]">
        <span>-{seconds}s</span>
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-3 rounded bg-blue-400/70" /> ok
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-3 rounded bg-red-400/70" /> failed
          </span>
        </span>
        <span>now</span>
      </div>
    </div>
  );
}

// -- share bars --------------------------------------------------------------

export interface ShareRow {
  name: string;
  value: number;
  /** Rendered on the right; caller formats. */
  display: string;
  /** Optional secondary line under the name. */
  sub?: string;
}

/**
 * Horizontal ranked bars with an inline share fill — the compact way to show
 * "who is consuming what" without another donut.
 */
export function ShareBars(props: {
  rows: ShareRow[];
  /** Bars beyond this count are folded into an "other" note. */
  limit?: number;
  emptyLabel?: string;
  barColor?: string;
  onSelect?: (name: string) => void;
  activeName?: string | null;
  showRank?: boolean;
}) {
  const limit = props.limit ?? 6;
  const barColor = props.barColor ?? "rgba(59,130,246,0.55)";
  const rows = props.rows.slice(0, limit);
  const max = Math.max(1, ...props.rows.map((r) => r.value));
  const hidden = Math.max(0, props.rows.length - rows.length);

  if (rows.length === 0) {
    return <EmptyState>{props.emptyLabel ?? "No data in this window."}</EmptyState>;
  }

  return (
    <div className="space-y-2.5 p-4">
      {rows.map((r, i) => {
        const active = props.activeName === r.name;
        const Wrapper = props.onSelect ? "button" : "div";
        return (
          <Wrapper
            key={r.name}
            type={props.onSelect ? "button" : undefined}
            onClick={props.onSelect ? () => props.onSelect?.(r.name) : undefined}
            className={`block w-full text-left ${props.onSelect ? "cursor-pointer" : ""}`}
            title={r.name}
          >
            <div className="mb-1 flex items-baseline justify-between gap-3">
              <span className="flex min-w-0 items-baseline gap-2">
                {props.showRank && (
                  <span className="font-mono text-[10px] text-[var(--admin-text-dim)]">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                )}
                <span
                  className={`truncate text-[12.5px] ${
                    active ? "text-violet-200" : "text-[var(--admin-text)]"
                  }`}
                >
                  {r.name}
                </span>
                {r.sub && (
                  <span className="shrink-0 text-[10px] text-[var(--admin-text-dim)]">
                    {r.sub}
                  </span>
                )}
              </span>
              <span className="shrink-0 font-mono text-[12px] tabular-nums text-[var(--admin-text-muted)]">
                {r.display}
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.05]">
              <div
                className="h-full rounded-full transition-[width] duration-500 ease-out"
                style={{
                  width: `${(r.value / max) * 100}%`,
                  background: active
                    ? "linear-gradient(90deg, rgba(139,92,246,0.75), rgba(217,70,239,0.6))"
                    : `linear-gradient(90deg, ${barColor}, rgba(124,58,237,0.35))`,
                }}
              />
            </div>
          </Wrapper>
        );
      })}
      {hidden > 0 && (
        <p className="pt-1 font-mono text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">
          +{fmtInt(hidden)} more
        </p>
      )}
    </div>
  );
}

// -- token mix ribbon --------------------------------------------------------

export interface RibbonPart {
  label: string;
  value: number;
  color: string;
}

/** Single stacked strip showing how total tokens split across kinds. */
export function TokenRibbon(props: { parts: RibbonPart[]; total?: number }) {
  const total = props.total ?? props.parts.reduce((a, p) => a + p.value, 0);
  if (total <= 0) return <EmptyState>No tokens recorded in this window.</EmptyState>;
  return (
    <div className="p-4">
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-white/[0.04]">
        {props.parts
          .filter((p) => p.value > 0)
          .map((p) => (
            <span
              key={p.label}
              className="h-full transition-[width] duration-500 ease-out first:rounded-l-full last:rounded-r-full"
              style={{ width: `${(p.value / total) * 100}%`, backgroundColor: p.color }}
              title={`${p.label}: ${fmtTokens(p.value)}`}
            />
          ))}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {props.parts.map((p) => (
          <div key={p.label} className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: p.color }}
              />
              <span className="admin-label truncate">{p.label}</span>
            </div>
            <p className="mt-0.5 font-mono text-[13px] tabular-nums text-[var(--admin-text)]">
              {fmtTokens(p.value)}
            </p>
            <p className="font-mono text-[10px] tabular-nums text-[var(--admin-text-dim)]">
              {fmtPct(p.value / total)}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

// -- gauge ring --------------------------------------------------------------

/** Radial gauge: arc fills clockwise from 12 o'clock, value printed centered. */
export function GaugeRing(props: {
  /** 0..1 fraction of the arc to fill. */
  value: number;
  label: string;
  center: string;
  sub?: string;
  color?: string;
  size?: number;
  icon?: LucideIcon;
}) {
  const size = props.size ?? 132;
  const stroke = 9;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const frac = Math.min(1, Math.max(0, props.value));
  const color = props.color ?? "var(--admin-accent)";
  const Icon = props.icon;

  return (
    <div className="flex flex-col items-center px-4 py-5">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke="rgba(255,255,255,0.05)"
            strokeWidth={stroke}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={c * (1 - frac)}
            style={{
              transition: "stroke-dashoffset 600ms cubic-bezier(0.22,1,0.36,1)",
              filter: `drop-shadow(0 0 6px ${color}55)`,
            }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {Icon && <Icon className="mb-1 h-3.5 w-3.5" style={{ color, opacity: 0.7 }} />}
          <span className="font-mono text-[20px] font-bold leading-none tabular-nums tracking-[-0.02em] text-[var(--admin-text)]">
            {props.center}
          </span>
          <span className="admin-label mt-1.5">{props.label}</span>
        </div>
      </div>
      {props.sub && (
        <p className="mt-2.5 font-mono text-[11px] text-[var(--admin-text-dim)]">{props.sub}</p>
      )}
    </div>
  );
}

// -- latency ribbon ----------------------------------------------------------

export interface LatencyProfile {
  buckets: { label: string; count: number }[];
  p50: number;
  p95: number;
  max: number;
  samples: number;
}

const DEFAULT_LATENCY_EDGES = [50, 100, 250, 500, 1000, 2000, 4000];

/** Histogram + percentile markers for a set of ms measurements. */
export function LatencyRibbon(props: {
  profile: LatencyProfile;
  color?: string;
  unitLabel?: string;
}) {
  const color = props.color ?? "#3b82f6";
  const max = Math.max(1, ...props.profile.buckets.map((b) => b.count));
  const samples = props.profile.samples;

  if (samples === 0) {
    return <EmptyState>No latency samples in this window.</EmptyState>;
  }

  return (
    <div className="p-4">
      <div className="flex items-end gap-1.5" style={{ height: 92 }}>
        {props.profile.buckets.map((b) => (
          <div key={b.label} className="flex flex-1 flex-col items-center justify-end gap-1.5">
            <span className="font-mono text-[9px] tabular-nums text-[var(--admin-text-dim)]">
              {b.count > 0 ? b.count : ""}
            </span>
            <div
              className="w-full rounded-t-[3px] transition-[height] duration-500 ease-out"
              style={{
                height: `${Math.max(2, (b.count / max) * 100)}%`,
                background: `linear-gradient(180deg, ${color}, ${color}22)`,
                opacity: b.count > 0 ? 0.9 : 0.25,
              }}
              title={`${b.label} ms · ${fmtInt(b.count)} requests`}
            />
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex gap-1.5">
        {props.profile.buckets.map((b) => (
          <span
            key={b.label}
            className="flex-1 truncate text-center font-mono text-[9px] text-[var(--admin-text-dim)]"
          >
            {b.label}
          </span>
        ))}
      </div>
      <div className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-[8px] border border-[var(--admin-border)] bg-[var(--admin-border)]">
        {[
          { label: "p50", value: props.profile.p50 },
          { label: "p95", value: props.profile.p95 },
          { label: "max", value: props.profile.max },
        ].map((m) => (
          <div key={m.label} className="bg-[var(--admin-surface)] px-3 py-2.5">
            <span className="admin-label">{m.label}</span>
            <p className="mt-0.5 font-mono text-[14px] tabular-nums text-[var(--admin-text)]">
              {m.value >= 1000 ? `${(m.value / 1000).toFixed(2)}s` : `${Math.round(m.value)}ms`}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Build a LatencyProfile from raw millisecond samples. */
export function latencyProfile(
  values: number[],
  edges: number[] = DEFAULT_LATENCY_EDGES,
): LatencyProfile {
  const clean = values.filter((v) => v > 0).sort((a, b) => a - b);
  const labels: string[] = [];
  const bounds: Array<[number, number]> = [];
  let lo = 0;
  for (const hi of edges) {
    labels.push(hi >= 1000 ? `${hi / 1000}k` : String(hi));
    bounds.push([lo, hi]);
    lo = hi;
  }
  labels.push(`${edges[edges.length - 1] / 1000}k+`);
  bounds.push([lo, Number.POSITIVE_INFINITY]);

  const buckets = labels.map((label, i) => ({
    label,
    count: clean.filter((v) => v >= bounds[i][0] && v < bounds[i][1]).length,
  }));
  const at = (q: number) =>
    clean.length ? clean[Math.min(clean.length - 1, Math.floor(clean.length * q))] : 0;
  return {
    buckets,
    p50: at(0.5),
    p95: at(0.95),
    max: clean.length ? clean[clean.length - 1] : 0,
    samples: clean.length,
  };
}

// -- status mix --------------------------------------------------------------

export interface StatusSlice {
  label: string;
  count: number;
  color: string;
}

/** 2xx / 4xx / 5xx split as a single stacked strip + legend. */
export function StatusMix(props: { slices: StatusSlice[]; total?: number }) {
  const total = props.total ?? props.slices.reduce((a, s) => a + s.count, 0);
  if (total === 0) return <EmptyState>No requests in this window.</EmptyState>;
  return (
    <div className="p-4">
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-white/[0.04]">
        {props.slices
          .filter((s) => s.count > 0)
          .map((s) => (
            <span
              key={s.label}
              className="h-full transition-[width] duration-500 ease-out first:rounded-l-full last:rounded-r-full"
              style={{
                width: `${(s.count / total) * 100}%`,
                backgroundColor: s.color,
              }}
              title={`${s.label}: ${fmtInt(s.count)}`}
            />
          ))}
      </div>
      <div className="mt-3.5 space-y-2">
        {props.slices.map((s) => (
          <div key={s.label} className="flex items-center justify-between gap-3">
            <span className="flex items-center gap-2">
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: s.color }}
              />
              <span className="text-[12px] text-[var(--admin-text-muted)]">{s.label}</span>
            </span>
            <span className="flex items-baseline gap-2">
              <span className="font-mono text-[13px] tabular-nums text-[var(--admin-text)]">
                {fmtInt(s.count)}
              </span>
              <span className="font-mono text-[10px] tabular-nums text-[var(--admin-text-dim)]">
                {fmtPct(s.count / total)}
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Standard 2xx/4xx/5xx grouping for a list of HTTP statuses. */
export function statusSlices(statuses: number[]): StatusSlice[] {
  const buckets: StatusSlice[] = [
    { label: "2xx success", count: 0, color: "#34d399" },
    { label: "4xx client error", count: 0, color: "#fbbf24" },
    { label: "5xx upstream error", count: 0, color: "#f87171" },
  ];
  for (const s of statuses) {
    if (s >= 500) buckets[2].count += 1;
    else if (s >= 400) buckets[1].count += 1;
    else buckets[0].count += 1;
  }
  return buckets;
}

// -- health grid -------------------------------------------------------------

export interface HealthItem {
  name: string;
  ok: boolean;
  /** Degraded but serving (e.g. a key in cooldown). */
  warn?: boolean;
  sub?: string;
  meta?: string;
}

/** Compact provider/deployment health tiles: status dot + counters. */
export function HealthGrid(props: { items: HealthItem[]; emptyLabel?: string }) {
  if (props.items.length === 0) {
    return <EmptyState>{props.emptyLabel ?? "Nothing configured yet."}</EmptyState>;
  }
  const okCount = props.items.filter((i) => i.ok && !i.warn).length;
  return (
    <div className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="admin-label">providers</span>
        <span className="font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
          {okCount}/{props.items.length} healthy
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {props.items.map((it) => {
          const tone = it.ok && !it.warn ? "#34d399" : it.warn ? "#fbbf24" : "#f87171";
          return (
            <div
              key={it.name}
              className="rounded-[10px] border border-[var(--admin-border)] bg-white/[0.015] px-3 py-2.5 transition-colors hover:border-white/[0.09]"
            >
              <div className="flex items-center gap-2">
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: tone, boxShadow: `0 0 6px ${tone}66` }}
                />
                <span className="truncate text-[12.5px] text-[var(--admin-text)]" title={it.name}>
                  {it.name}
                </span>
              </div>
              <div className="mt-1.5 flex items-center justify-between gap-2">
                <span className="truncate font-mono text-[10px] text-[var(--admin-text-dim)]">
                  {it.meta ?? (it.ok ? "healthy" : "unhealthy")}
                </span>
                {it.sub && (
                  <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--admin-text-muted)]">
                    {it.sub}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// -- card wrappers -----------------------------------------------------------

/** Titled card shell for the visuals above, with an optional header control. */
export function VisualCard(props: {
  title: string;
  subtitle?: string;
  icon?: LucideIcon;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const Icon = props.icon;
  return (
    <Card className={props.className}>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            {Icon && (
              <Icon
                className="h-3.5 w-3.5"
                style={{ color: "var(--admin-accent)", opacity: 0.65 }}
              />
            )}
            {props.title}
          </span>
        }
        subtitle={props.subtitle}
        right={props.right}
      />
      {props.children}
    </Card>
  );
}

/** Consistency helper: the icon set used by the console's visual cards. */
export const VISUAL_ICONS = {
  pulse: Activity,
  gauge: Gauge,
  latency: Timer,
  health: ShieldCheck,
  errors: AlertTriangle,
};
