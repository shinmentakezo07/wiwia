// Reliability — automatic failover, health monitoring, and intelligent
// routing. A creative, visual-first page built around a live failover diagram,
// an animated uptime timeline, a before/after comparison, and a bento feature
// grid. Dark design system.

import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Cpu,
  Gauge,
  Globe,
  LineChart,
  Radio,
  Server,
  ShieldCheck,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// ── hero stats ────────────────────────────────────────────────────────────

const STATS = [
  { value: "99.9999%", label: "Effective uptime", accent: "text-emerald-400" },
  { value: "<32s", label: "Downtime / year", accent: "text-blue-400" },
  { value: "25+", label: "Providers", accent: "text-purple-400" },
  { value: "0ms", label: "Failover overhead", accent: "text-amber-400" },
];

// ── live failover diagram ──────────────────────────────────────────────────
// Your app (left) → gateway hub (center) → three providers (right). One
// provider is down; a failed packet peters out before reaching it while a
// second request is re-routed to a healthy provider and pings success.

type NodeState = "ok" | "down";

const PROVIDERS: {
  name: string;
  state: NodeState;
  latency: string;
}[] = [
  { name: "Anthropic", state: "ok", latency: "210ms" },
  { name: "OpenAI", state: "down", latency: "5xx" },
  { name: "Google", state: "ok", latency: "184ms" },
];

// ── uptime timeline (90 days, per-provider) ────────────────────────────────

const TIMELINE_PROVIDERS = [
  { name: "Anthropic", outages: [[8, 10], [42, 44], [78, 80]] as [number, number][] },
  { name: "OpenAI", outages: [[3, 5], [30, 32], [63, 65], [88, 90]] as [number, number][] },
  { name: "Google Vertex", outages: [[18, 20], [50, 52], [72, 74]] as [number, number][] },
  { name: "AWS Bedrock", outages: [[13, 15], [37, 39]] as [number, number][] },
  { name: "Azure OpenAI", outages: [[23, 25], [55, 57], [95, 97]] as [number, number][] },
];

function buildSegments(outages: [number, number][]) {
  const segments: { type: "up" | "down"; width: number }[] = [];
  let pos = 0;
  for (const [start, end] of outages) {
    if (start > pos) segments.push({ type: "up", width: start - pos });
    segments.push({ type: "down", width: end - start });
    pos = end;
  }
  if (pos < 100) segments.push({ type: "up", width: 100 - pos });
  return segments;
}

// ── bento features (asymmetric grid) ──────────────────────────────────────

type Feature = {
  icon: LucideIcon;
  title: string;
  body: string;
  accent: string;
  /** grid span hint; defaults to 1 col / 1 row. */
  span?: string;
  /** optional inline mini-visual rendered in the card body. */
  visual?: "pulse" | "gauge" | "regions" | "shield";
};

const FEATURES: Feature[] = [
  {
    icon: Activity,
    title: "Real-time health checks",
    body: "Every provider is continuously probed. Unhealthy endpoints leave the rotation within seconds — not on the next request.",
    accent: "text-emerald-400",
    span: "lg:col-span-2",
    visual: "pulse",
  },
  {
    icon: Gauge,
    title: "Latency-aware routing",
    body: "Requests go to the fastest responsive provider for your region. TTFT is tracked per provider, per model.",
    accent: "text-blue-400",
    visual: "gauge",
  },
  {
    icon: Globe,
    title: "Multi-region redundancy",
    body: "Route across providers in US, EU, and APAC so a regional outage never takes you down.",
    accent: "text-purple-400",
    visual: "regions",
  },
  {
    icon: ShieldCheck,
    title: "Rate-limit aware",
    body: "When a provider throttles you, traffic shifts automatically — you keep serving requests with no manual intervention.",
    accent: "text-amber-400",
    visual: "shield",
  },
  {
    icon: LineChart,
    title: "Observable by default",
    body: "Uptime, error rates, and latency tracked per provider in your dashboard. Export it for audits or share with stakeholders.",
    accent: "text-cyan-400",
    span: "lg:col-span-2",
  },
];

// ── small inline visualizations ────────────────────────────────────────────

function PulseVisual() {
  // three heartbeat-style blips across a baseline
  return (
    <div className="mt-4 flex items-end gap-1.5" aria-hidden>
      {[3, 7, 4, 9, 5, 8, 4, 6, 3].map((h, i) => (
        <div
          key={i}
          className="w-1.5 rounded-full bg-emerald-400/40"
          style={{
            height: `${h * 3}px`,
            animation: `rel-incident-blink 1.6s ease-in-out ${i * 0.12}s infinite`,
          }}
        />
      ))}
      <span className="ml-2 font-mono text-[10px] uppercase tracking-wider text-emerald-400/70">
        probing · 4s
      </span>
    </div>
  );
}

function GaugeVisual() {
  return (
    <div className="mt-4 flex items-center gap-3" aria-hidden>
      <div className="relative h-10 w-10">
        <svg viewBox="0 0 40 40" className="h-10 w-10 -rotate-90">
          <circle cx="20" cy="20" r="16" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="3" />
          <circle
            cx="20"
            cy="20"
            r="16"
            fill="none"
            stroke="var(--admin-accent)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={`${0.78 * 100.5} ${100.5}`}
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center font-mono text-[11px] font-bold text-blue-300">
          184
        </span>
      </div>
      <div className="text-[11px] leading-tight text-[var(--admin-text-dim)]">
        <div className="font-mono text-blue-300">TTFT · ms</div>
        <div>p50 across regions</div>
      </div>
    </div>
  );
}

function RegionsVisual() {
  const regions = ["US", "EU", "AP"];
  return (
    <div className="mt-4 flex gap-2" aria-hidden>
      {regions.map((r, i) => (
        <div
          key={r}
          className="relative flex h-9 w-9 items-center justify-center rounded-lg border border-purple-500/20 bg-purple-500/[0.04]"
        >
          <span className="font-mono text-[10px] font-bold text-purple-300">{r}</span>
          <span
            className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-purple-400"
            style={{ animation: `rel-hub-glow 3s ease-in-out ${i * 0.5}s infinite` }}
          />
        </div>
      ))}
    </div>
  );
}

function ShieldVisual() {
  return (
    <div className="mt-4 flex items-center gap-2" aria-hidden>
      <div className="flex items-center gap-1.5 rounded-md border border-amber-500/20 bg-amber-500/[0.04] px-2.5 py-1">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-400" style={{ animation: "rel-down-flare 1.4s ease-in-out infinite" }} />
        <span className="font-mono text-[10px] text-amber-300">429 throttle</span>
      </div>
      <ArrowRight size={12} className="text-[var(--admin-text-dim)]" />
      <div className="flex items-center gap-1.5 rounded-md border border-emerald-500/20 bg-emerald-500/[0.04] px-2.5 py-1">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
        <span className="font-mono text-[10px] text-emerald-300">re-routed</span>
      </div>
    </div>
  );
}

const FEATURE_VISUALS: Record<NonNullable<Feature["visual"]>, () => React.ReactNode> = {
  pulse: PulseVisual,
  gauge: GaugeVisual,
  regions: RegionsVisual,
  shield: ShieldVisual,
};

// ── the live failover diagram ──────────────────────────────────────────────

function FailoverDiagram() {
  return (
    <div
      className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-6 sm:p-10"
      role="img"
      aria-label="Live failover: a request from your app routes through the gateway hub to a healthy provider after the primary returns an error"
    >
      {/* diagram grid: app · hub · providers */}
      <div className="relative grid grid-cols-[1fr_auto_1fr] items-center gap-6 sm:gap-12">
        {/* ── your app ── */}
        <div className="flex flex-col items-center gap-2">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-white/[0.08] bg-white/[0.02]">
            <Cpu className="h-7 w-7 text-[var(--admin-text-muted)]" />
          </div>
          <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">Your app</span>
        </div>

        {/* ── gateway hub ── */}
        <div className="flex flex-col items-center gap-2">
          <div className="relative">
            {/* breathing glow */}
            <div
              className="rel-hub-glow absolute inset-0 -z-10 rounded-full"
              style={{ background: "radial-gradient(circle, rgba(59,130,246,0.25) 0%, transparent 70%)" }}
            />
            {/* rotating shield sweep */}
            <div
              className="rel-shield-sweep absolute -inset-2 -z-10 rounded-full opacity-30"
              style={{
                background: "conic-gradient(from 0deg, transparent, rgba(124,58,237,0.4), transparent 60%)",
              }}
            />
            <div className="flex h-20 w-20 items-center justify-center rounded-full border border-blue-500/30 bg-[var(--admin-surface-elevated)]">
              <Radio className="h-8 w-8 text-blue-400" />
            </div>
          </div>
          <span className="font-mono text-[10px] uppercase tracking-wider text-blue-300">wiwi gateway</span>
        </div>

        {/* ── providers (stacked) ── */}
        <div className="flex flex-col gap-3">
          {PROVIDERS.map((p) => {
            const isDown = p.state === "down";
            return (
              <div key={p.name} className="flex items-center gap-3">
                <div
                  className={`relative flex h-12 w-44 items-center gap-2.5 rounded-xl border px-3 ${
                    isDown
                      ? "border-red-500/25 bg-red-500/[0.04]"
                      : "border-emerald-500/20 bg-emerald-500/[0.03]"
                  }`}
                >
                  <Server className={`h-4 w-4 shrink-0 ${isDown ? "text-red-400" : "text-emerald-400"}`} />
                  <div className="min-w-0">
                    <div className="truncate text-[12px] font-medium text-[var(--admin-text)]">{p.name}</div>
                    <div className={`font-mono text-[10px] ${isDown ? "text-red-400" : "text-emerald-400"}`}>
                      {p.latency}
                    </div>
                  </div>
                  {isDown ? (
                    <span className="rel-down-flare ml-auto h-2 w-2 shrink-0 rounded-full bg-red-400" />
                  ) : (
                    <span className="relative ml-auto h-2 w-2 shrink-0">
                      <span
                        className="rel-success-ring absolute inset-0 rounded-full bg-emerald-400/40"
                        style={{ animationDelay: `${Math.random() * 0.6}s` }}
                      />
                      <span className="relative block h-2 w-2 rounded-full bg-emerald-400" />
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── animated connector paths + packets ── */}
      <svg
        className="pointer-events-none absolute inset-0 h-full w-full"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden
      >
        <defs>
          <linearGradient id="rel-path-ok" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="rgba(59,130,246,0.5)" />
            <stop offset="100%" stopColor="rgba(52,211,153,0.6)" />
          </linearGradient>
        </defs>
        {/* three straight horizontal-ish connectors from hub area to each provider */}
        {/* We draw them as near-horizontal paths at the provider y-positions. */}
        {/* The packets use offset-path with the same d strings. */}
        {PROVIDERS.map((p, i) => {
          // approximate vertical positions of the three provider cards (in %)
          const y = 30 + i * 22;
          const isDown = p.state === "down";
          const path = `M 62 ${50} Q 75 ${50} 88 ${y}`;
          return (
            <g key={p.name}>
              <path
                d={path}
                fill="none"
                stroke={isDown ? "rgba(248,113,113,0.35)" : "url(#rel-path-ok)"}
                strokeWidth={isDown ? 1 : 1.5}
                strokeDasharray={isDown ? "3 3" : "none"}
                opacity={0.7}
              />
            </g>
          );
        })}
        {/* app → hub connector (always healthy) */}
        <path
          d="M 16 50 Q 33 50 44 50"
          fill="none"
          stroke="url(#rel-path-ok)"
          strokeWidth={1.5}
          opacity={0.7}
        />
      </svg>

      {/* packets: small dots animated along the paths via offset-path */}
      <div className="pointer-events-none absolute inset-0" aria-hidden>
        {/* app → hub */}
        <span
          className="rel-packet-ok absolute h-2 w-2 rounded-full bg-blue-400"
          style={{ offsetPath: "path('M 16 50 Q 33 50 44 50')", offsetRotate: "0deg" }}
        />
        {/* hub → healthy provider (index 0) */}
        <span
          className="rel-packet-ok absolute h-2 w-2 rounded-full bg-emerald-400"
          style={{
            offsetPath: "path('M 62 50 Q 75 50 88 30')",
            animationDelay: "0.8s",
          }}
        />
        {/* hub → down provider (fails partway) */}
        <span
          className="rel-packet-fail absolute h-2 w-2 rounded-full bg-red-400"
          style={{ offsetPath: "path('M 62 50 Q 75 50 88 52')" }}
        />
        {/* hub → healthy provider (index 2) */}
        <span
          className="rel-packet-ok absolute h-2 w-2 rounded-full bg-emerald-400"
          style={{
            offsetPath: "path('M 62 50 Q 75 50 88 74')",
            animationDelay: "1.6s",
          }}
        />
      </div>

      {/* ── caption strip ── */}
      <div className="mt-8 flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--admin-border)] bg-white/[0.02] p-3.5 text-center sm:flex-row sm:gap-3">
        <span className="flex items-center gap-2 text-[12px] font-medium text-red-400">
          <AlertTriangle size={13} /> OpenAI returns 5xx
        </span>
        <ArrowRight size={13} className="hidden text-[var(--admin-text-dim)] sm:block" />
        <span className="flex items-center gap-2 text-[12px] font-medium text-blue-300">
          <Zap size={13} /> Gateway retries on the same request
        </span>
        <ArrowRight size={13} className="hidden text-[var(--admin-text-dim)] sm:block" />
        <span className="flex items-center gap-2 text-[12px] font-medium text-emerald-400">
          <CheckCircle2 size={13} /> Delivered via Anthropic · {`{`}user sees nothing{`}`}
        </span>
      </div>
    </div>
  );
}

// ── uptime timeline ────────────────────────────────────────────────────────

function UptimeTimeline() {
  return (
    <div
      className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 sm:p-8"
      role="img"
      aria-label="Per-provider uptime over the last 90 days, with the gateway's combined uptime at 99.9999%"
    >
      {/* day scale */}
      <div className="mb-3 flex items-center justify-between px-24">
        <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">90 days ago</span>
        <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">today</span>
      </div>

      <div className="space-y-2.5 sm:space-y-3">
        {TIMELINE_PROVIDERS.map((provider) => {
          const segments = buildSegments(provider.outages);
          const totalDown = provider.outages.reduce((sum, [s, e]) => sum + (e - s), 0);
          return (
            <div key={provider.name} className="flex items-center gap-2 sm:gap-4">
              <div className="w-20 shrink-0 text-right sm:w-28">
                <span className="text-xs font-medium text-[var(--admin-text-muted)] sm:text-sm">{provider.name}</span>
              </div>
              <div className="relative h-5 flex-1 overflow-hidden rounded sm:h-7">
                <div className="absolute inset-0 bg-white/[0.04]" />
                <div className="rel-uptime-reveal flex h-full">
                  {segments.map((seg, i) =>
                    seg.type === "up" ? (
                      <div key={i} className="h-full bg-emerald-500/30" style={{ width: `${seg.width}%` }} />
                    ) : (
                      <div
                        key={i}
                        className="rel-incident-blink h-full bg-red-500/60"
                        style={{ width: `${seg.width}%` }}
                      />
                    ),
                  )}
                </div>
              </div>
              <div className="w-12 shrink-0 text-right sm:w-20">
                <span className="font-mono text-xs text-[var(--admin-text-muted)] sm:text-sm">{100 - totalDown}%</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* failover divider */}
      <div className="my-4 flex items-center gap-2 sm:my-6 sm:gap-4">
        <div className="w-20 shrink-0 sm:w-28" />
        <div className="relative flex-1 border-t border-dashed border-[var(--admin-border)]">
          <div className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 whitespace-nowrap bg-[var(--admin-surface)] px-3 py-0.5 font-mono text-xs text-[var(--admin-text-muted)]">
            <ArrowRight size={12} className="text-blue-500" />
            failover combines uptime
          </div>
        </div>
        <div className="w-12 shrink-0 sm:w-20" />
      </div>

      {/* gateway combined bar */}
      <div className="flex items-center gap-2 sm:gap-4">
        <div className="w-20 shrink-0 text-right sm:w-28">
          <span className="text-xs font-bold text-[var(--admin-text)] sm:text-sm">wiwi gateway</span>
        </div>
        <div className="relative h-5 flex-1 overflow-hidden rounded sm:h-7">
          <div className="absolute inset-0 bg-white/[0.04]" />
          <div className="rel-uptime-reveal absolute inset-0 rounded bg-emerald-500" style={{ animationDelay: "0.4s" }} />
          <div
            className="pointer-events-none absolute inset-0 rounded"
            style={{ boxShadow: "0 0 20px rgba(16,185,129,0.3), inset 0 1px 0 rgba(52,211,153,0.2)" }}
          />
        </div>
        <div className="w-12 shrink-0 text-right sm:w-20">
          <span className="font-mono text-xs font-bold text-emerald-400 sm:text-sm">99.9999%</span>
        </div>
      </div>

      {/* legend */}
      <div className="mt-5 flex flex-wrap items-center gap-4 px-24">
        <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">
          <span className="h-2.5 w-2.5 rounded-sm bg-emerald-500/30" /> operational
        </span>
        <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">
          <span className="rel-incident-blink h-2.5 w-2.5 rounded-sm bg-red-500/60" /> incident
        </span>
      </div>
    </div>
  );
}

// ── before / after ──────────────────────────────────────────────────────────

function BeforeAfter() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 sm:gap-6">
      <div className="rounded-xl border border-red-500/20 bg-red-500/[0.03] p-5 sm:p-6">
        <div className="mb-3 font-mono text-xs tracking-wider text-red-400">WITHOUT A GATEWAY</div>
        <div className="font-mono text-3xl font-bold text-[var(--admin-text)] sm:text-4xl">94%</div>
        <div className="mt-1 text-sm text-[var(--admin-text-muted)]">uptime per provider</div>
        <div className="mt-4 border-t border-red-500/10 pt-4">
          <div className="font-mono text-lg font-bold text-red-400">~22 days</div>
          <div className="text-sm text-[var(--admin-text-muted)]">of downtime per year</div>
        </div>
      </div>
      <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/[0.03] p-5 sm:p-6">
        <div className="mb-3 font-mono text-xs tracking-wider text-emerald-400">WITH WIWI</div>
        <div className="font-mono text-3xl font-bold text-emerald-400 sm:text-4xl">99.9999%</div>
        <div className="mt-1 text-sm text-[var(--admin-text-muted)]">combined uptime across providers</div>
        <div className="mt-4 border-t border-emerald-500/10 pt-4">
          <div className="font-mono text-lg font-bold text-emerald-400">&lt;32s</div>
          <div className="text-sm text-[var(--admin-text-muted)]">of downtime per year</div>
        </div>
      </div>
    </div>
  );
}

// ── page ────────────────────────────────────────────────────────────────────

export function ReliabilityPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-16 pb-16">
      {/* ══ hero ══ */}
      <section className="pt-6 text-center">
        {/* eyebrow badge */}
        <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-4 py-1.5">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
          </span>
          <span className="font-mono text-[11px] text-emerald-400">RELIABILITY</span>
          <span className="text-[11px] text-[var(--admin-text-muted)]">99.9999% effective uptime</span>
        </div>

        <h1 className="text-4xl font-bold tracking-tight text-balance sm:text-5xl lg:text-6xl">
          Your AI app can&apos;t afford to{" "}
          <span className="bg-gradient-to-r from-emerald-400 via-teal-400 to-blue-400 bg-clip-text text-transparent">
            go down
          </span>
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-lg leading-relaxed text-[var(--admin-text-muted)]">
          The gateway automatically routes requests to healthy providers in real time.
          When one goes down, your traffic seamlessly fails over — your users never
          notice.
        </p>

        <div className="mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to="/signup"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            Start routing in minutes
            <ArrowRight size={16} />
          </Link>
          <Link
            to="/enterprise"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            <ShieldCheck size={16} />
            Talk to sales
          </Link>
        </div>

        {/* hero stats row */}
        <div className="mx-auto mt-12 grid max-w-3xl grid-cols-2 gap-4 sm:grid-cols-4">
          {STATS.map((s) => (
            <div key={s.label} className="admin-card p-5 text-center">
              <div className={`font-mono text-[22px] font-bold tabular-nums ${s.accent}`} style={{ fontFamily: MONO }}>
                {s.value}
              </div>
              <div className="mt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--admin-text-muted)]">
                {s.label}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ══ live failover diagram ══ */}
      <section>
        <div className="mb-8 text-center">
          <span className="admin-label">How it works</span>
          <h2 className="mt-2 text-[28px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Watch a request fail over — live
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Every request is health-checked in real time. The moment a provider starts
            failing, the gateway retries the same prompt against the next healthy one —
            on the same request, with zero SDK changes.
          </p>
        </div>
        <FailoverDiagram />
      </section>

      {/* ══ uptime timeline ══ */}
      <section>
        <div className="mb-8 text-center">
          <span className="admin-label">90-day history</span>
          <h2 className="mt-2 text-[28px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            One provider&apos;s bad day is just a blip
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Individual providers dip. The gateway's combined uptime stays pinned at the top
            because traffic flows around the outage — not into it.
          </p>
        </div>
        <UptimeTimeline />
      </section>

      {/* ══ before / after ══ */}
      <section>
        <BeforeAfter />
      </section>

      {/* ══ bento features ══ */}
      <section>
        <div className="mb-8 text-center">
          <span className="admin-label">What&apos;s included</span>
          <h2 className="mt-2 text-[28px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Built for production traffic
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Reliability is the default — not an add-on. Every account gets the full
            routing engine.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((feature) => {
            const Icon = feature.icon;
            const Visual = feature.visual ? FEATURE_VISUALS[feature.visual] : null;
            return (
              <div
                key={feature.title}
                className={`admin-card p-5 transition-colors hover:border-[var(--admin-border-hover)] ${feature.span ?? ""}`}
              >
                <div className={`mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-white/[0.03]`}>
                  <Icon className={`h-5 w-5 ${feature.accent}`} />
                </div>
                <h3 className="mb-2 text-[15px] font-semibold text-[var(--admin-text)]">{feature.title}</h3>
                <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{feature.body}</p>
                {Visual && <Visual />}
              </div>
            );
          })}
        </div>
      </section>

      {/* ══ CTA ══ */}
      <section>
        <div
          className="overflow-hidden rounded-2xl border border-emerald-500/20 p-8 text-center sm:p-12"
          style={{ background: "linear-gradient(to bottom right, rgba(16,185,129,0.04), transparent)" }}
        >
          <h2 className="text-[28px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Stop babysitting provider dashboards
          </h2>
          <p className="mx-auto mt-3 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            Switch your base URL to the gateway and get automatic failover, real-time
            health monitoring, and uptime reporting across 25+ providers — in one line
            of code.
          </p>
          <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link
              to="/signup"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
            >
              Get started free
              <ArrowRight size={16} />
            </Link>
            <Link
              to="/enterprise"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              Talk to sales
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
