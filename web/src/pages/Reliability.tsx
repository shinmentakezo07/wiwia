// Reliability — how the gateway behaves when a key, a deployment, or a whole
// provider fails. Everything on this page maps to code in wiwi/router/ and
// wiwi/core/recovery.py; no invented uptime numbers.
//
// The page is built as one argument in three moves: an attempt trace in the
// masthead (what a failure looks like), an escalation ladder (where failover
// happens), and a timeline (the order it happens in). Counts and strategy
// names are taken from providers/base.py, router/router.py and recovery.py —
// change the code and this copy has to follow.

import { Fragment, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowRight,
  Check,
  Clock,
  Gauge,
  HeartPulse,
  Layers,
  RefreshCw,
  RotateCw,
  ServerCrash,
  ShieldCheck,
  Timer,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useReducedMotion } from "@/components/animated";
import { Badge } from "@/components/ui";
import { QuickstartBand } from "@/components/landing/QuickstartBand";

// providers/base.py:RETRYABLE_STATUS — seven statuses, not eight.
const STATS = [
  { value: "7", label: "Retryable statuses" },
  { value: "3", label: "Failover layers" },
  { value: "1 token", label: "Health probes" },
  { value: "0", label: "Manual intervention" },
];

// One request, three attempts: two keys fail, the third provider serves it.
const ATTEMPTS = [
  {
    step: "01",
    target: "openai-main · key 2",
    status: "429",
    detail: "retry-after 12s honoured · key cooling",
    tone: "amber" as const,
  },
  {
    step: "02",
    target: "openai-main · key 3",
    status: "502",
    detail: "deployment cooled 60s · next candidate",
    tone: "red" as const,
  },
  {
    step: "03",
    target: "anthropic-main",
    status: "200",
    detail: "412 ms · served, no client retry",
    tone: "emerald" as const,
  },
];

const LAYERS: { icon: LucideIcon; scope: string; title: string; body: string }[] = [
  {
    icon: RefreshCw,
    scope: "per key",
    title: "Key rotation",
    body: "Each provider account holds a pool of keys. Smooth weighted round-robin spreads requests, and a failing key is cooled down while its siblings keep serving.",
  },
  {
    icon: Layers,
    scope: "per deployment",
    title: "Deployment fallback",
    body: "A model group can carry several deployments. When one provider is cooling or unavailable, the router picks the next healthy deployment for the same model.",
  },
  {
    icon: ServerCrash,
    scope: "per model group",
    title: "Model-group fallbacks",
    body: "Fallback chains move a request to another model group entirely when the primary group has no usable deployment left.",
  },
];

const FLOW: { step: string; title: string; chip: string; body: string }[] = [
  {
    step: "01",
    title: "The upstream returns a retryable error",
    chip: "408 · 429 · 5xx",
    body: "408, 429, 500, 502, 503, 504, and 529 are classified as retryable. The attempt is recorded with the upstream status and message.",
  },
  {
    step: "02",
    title: "Backoff, then another key or deployment",
    chip: "exponential",
    body: "The router sleeps with exponential backoff — honouring Retry-After when the upstream sends it — and retries against the next eligible key.",
  },
  {
    step: "03",
    title: "The failing key cools down",
    chip: "cooldown",
    body: "A key that keeps failing is marked cooling for a cooldown window, so later requests skip it instead of paying the failure again.",
  },
  {
    step: "04",
    title: "The healer restores it early",
    chip: "opt-in",
    body: "When enabled, a background sweeper probes cooling keys and deployments with a one-token completion and returns them to service before the cooldown expires.",
  },
];

const FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: Activity,
    title: "Per-key health state",
    body: "active, cooling, invalid, and disabled are tracked per key and shown live in the console, so you can see which credential is the problem.",
  },
  {
    icon: Gauge,
    title: "Latency-aware selection",
    body: "The router supports simple-shuffle, least-busy, and latency-based strategies; per-deployment weight is respected in every mode.",
  },
  {
    icon: Timer,
    title: "Retry-After aware",
    body: "When an upstream sends Retry-After, the cooldown uses it instead of guessing, so a throttled provider is not hammered.",
  },
  {
    icon: HeartPulse,
    title: "Opt-in health healer",
    body: "Probes spend real provider money, so the healer is off by default. Turn it on when faster recovery is worth the probe cost.",
  },
  {
    icon: ShieldCheck,
    title: "Probation, not instant trust",
    body: "A key restored by the healer re-enters at reduced weight until it proves itself, rather than taking a full share immediately.",
  },
  {
    icon: Clock,
    title: "Every attempt logged",
    body: "The request log records each attempt, its provider, status, latency, and error, so a failover is reconstructable after the fact.",
  },
];

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const STATUS_TONE = {
  amber: "border-amber-400/25 bg-amber-400/10 text-amber-300",
  red: "border-red-400/25 bg-red-400/10 text-red-300",
  emerald: "border-emerald-400/25 bg-emerald-400/10 text-emerald-300",
} as const;

const STEP_TONE = {
  amber: "text-amber-300/80",
  red: "text-red-300/80",
  emerald: "text-emerald-300/90",
} as const;

// Rows land in sequence; each status chip follows its row.
const ROW_DELAY = [0.05, 0.3, 0.55];

function SectionHead(props: { index: string; label: string; title: string }) {
  return (
    <div className="mb-5">
      <p
        className="font-mono text-[10.5px] uppercase tracking-[0.16em] text-[var(--admin-text-dim)]"
        style={{ fontFamily: MONO }}
      >
        <span className="text-emerald-400/80">{props.index}</span>
        <span aria-hidden> · </span>
        {props.label}
      </p>
      <h2 className="mt-2 text-[21px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
        {props.title}
      </h2>
    </div>
  );
}

function AttemptTrace() {
  const reduced = useReducedMotion();
  const [run, setRun] = useState(0);

  return (
    <div className="relative min-w-0 overflow-hidden rounded-xl border border-white/[0.08] bg-black/40">
      <div
        className="pointer-events-none absolute -bottom-14 -right-10 h-36 w-44 rounded-full bg-emerald-500/[0.05] blur-2xl"
        aria-hidden
      />
      <div className="relative flex h-11 items-center gap-2 border-b border-white/[0.06] px-4">
        <span className="h-1.5 w-1.5 rounded-full bg-white/20" aria-hidden />
        <span
          className="font-mono text-[11px] text-[var(--admin-text-dim)]"
          style={{ fontFamily: MONO }}
        >
          attempt-trace
        </span>
        <span
          className="ml-auto inline-flex items-center gap-1 rounded-full border border-emerald-400/25 bg-emerald-400/10 px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.1em] text-emerald-300"
          style={{ fontFamily: MONO }}
        >
          recovered
        </span>
        {!reduced && (
          <button
            type="button"
            onClick={() => setRun((n) => n + 1)}
            aria-label="Replay attempt trace"
            className="-mr-2 flex size-11 shrink-0 items-center justify-center rounded-md text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/50"
          >
            <RotateCw size={13} aria-hidden />
          </button>
        )}
      </div>

      <ol
        key={run}
        aria-label="Attempt trace for one request"
        className="relative m-0 list-none p-4"
      >
        {ATTEMPTS.map((attempt, i) => (
          <li
            key={attempt.step}
            style={{ animationDelay: `${ROW_DELAY[i]}s` }}
            className="rel-trace-row relative grid grid-cols-[1.4rem_minmax(0,1fr)_auto] items-start gap-x-2.5 pb-3.5 last:pb-0"
          >
            {i < ATTEMPTS.length - 1 && (
              <span
                aria-hidden
                style={{ animationDelay: `${ROW_DELAY[i] + 0.12}s` }}
                className="rel-trace-line absolute left-[10px] top-6 bottom-0 w-px bg-white/[0.08]"
              />
            )}
            <span
              className={`mt-0.5 font-mono text-[10px] tabular-nums ${STEP_TONE[attempt.tone]}`}
              style={{ fontFamily: MONO }}
            >
              {attempt.step}
            </span>
            <div className="min-w-0">
              <p className="break-words text-[11.5px] font-medium text-[var(--admin-text)]">
                {attempt.target}
              </p>
              <p className="mt-0.5 text-[10.5px] leading-snug text-[var(--admin-text-dim)]">
                {attempt.detail}
              </p>
            </div>
            <span
              style={{ fontFamily: MONO, animationDelay: `${ROW_DELAY[i] + 0.16}s` }}
              className={`rel-trace-status mt-0.5 rounded border px-1.5 py-0.5 font-mono text-[10px] tabular-nums ${STATUS_TONE[attempt.tone]}`}
            >
              {attempt.status}
            </span>
          </li>
        ))}
      </ol>

      <div className="relative flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-white/[0.06] px-4 py-2">
        <span
          className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--admin-text-dim)]"
          style={{ fontFamily: MONO }}
        >
          2 failovers · 1 request
        </span>
        <span
          className="ml-auto inline-flex items-center gap-1 font-mono text-[10px] text-emerald-300/90"
          style={{ fontFamily: MONO }}
        >
          <Check size={11} aria-hidden /> caller saw 200
        </span>
      </div>
    </div>
  );
}

export function ReliabilityPage() {
  return (
    <div className="pb-16">
      <div className="mx-auto max-w-4xl space-y-14">
      {/* ── masthead: the claim next to the trace that proves it ── */}
      <section className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[#070707]">
        <div
          className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-80"
          aria-hidden
        />
        <div className="hero-grid pointer-events-none absolute inset-0 opacity-30" aria-hidden />

        <div className="relative grid items-center gap-8 px-6 py-9 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.92fr)] lg:gap-10 lg:py-11">
          <div className="min-w-0">
            <Badge tone="green">Routing &amp; recovery</Badge>
            <h1 className="mt-4 text-[clamp(1.75rem,4vw,2.35rem)] font-semibold leading-[1.08] tracking-[-0.03em] text-[var(--admin-text)]">
              A provider fails.
              <br />
              <span className="bg-gradient-to-r from-emerald-400 to-blue-400 bg-clip-text text-transparent">
                The request does not.
              </span>
            </h1>
            <p className="mt-3 max-w-[46ch] text-[14.5px] leading-relaxed text-[var(--admin-text-muted)]">
              Retries, per-key cooldowns, deployment fallbacks, and an optional health healer
              keep traffic moving without a manual failover. The client sees one response and
              never learns which key died.
            </p>
            <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12.5px] text-[var(--admin-text-muted)]">
              <span className="inline-flex items-center gap-1.5">
                <Check size={13} className="text-emerald-400" aria-hidden /> Retry before the
                client notices
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Check size={13} className="text-emerald-400" aria-hidden /> Every attempt logged
              </span>
            </div>
          </div>

          <AttemptTrace />
        </div>

        {/* Counts strip: part of the masthead, not another card. */}
        <dl className="relative m-0 grid grid-cols-2 border-t border-white/[0.06] sm:grid-cols-4">
          {STATS.map((stat) => (
            <div
              key={stat.label}
              className="flex flex-col-reverse border-t border-white/[0.06] px-4 py-3 even:border-l sm:border-l sm:border-t-0 sm:first:border-l-0"
            >
              <dt className="mt-1.5 text-[10.5px] uppercase leading-tight tracking-[0.1em] text-[var(--admin-text-muted)]">
                {stat.label}
              </dt>
              <dd
                className="m-0 font-mono text-[20px] font-semibold tabular-nums leading-none text-emerald-400"
                style={{ fontFamily: MONO }}
              >
                {stat.value}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      {/* ── where failover happens, in escalation order ── */}
      <section className="scroll-reveal">
        <SectionHead index="02" label="escalation" title="Failover escalates outward" />
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)] md:items-stretch">
          {LAYERS.map((layer, i) => {
            const Icon = layer.icon;
            return (
              <Fragment key={layer.title}>
                {i > 0 && (
                  <div
                    className="flex items-center justify-center text-[var(--admin-text-dim)]"
                    aria-hidden
                  >
                    <ArrowDown size={15} className="rel-arrow-nudge-y md:hidden" />
                    <ArrowRight size={15} className="rel-arrow-nudge hidden md:block" />
                  </div>
                )}
                <div className="min-w-0 rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 transition-colors hover:border-[var(--admin-border-hover)] motion-safe:transition-transform motion-safe:duration-200 motion-safe:hover:-translate-y-0.5">
                  <div className="flex items-center justify-between gap-2">
                    <span
                      className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-[var(--admin-text-dim)]"
                      style={{ fontFamily: MONO }}
                    >
                      {layer.scope}
                    </span>
                    <Icon size={15} className="shrink-0 text-emerald-400" aria-hidden />
                  </div>
                  <h3 className="mt-2.5 text-[14px] font-semibold text-[var(--admin-text)]">
                    {layer.title}
                  </h3>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--admin-text-muted)]">
                    {layer.body}
                  </p>
                </div>
              </Fragment>
            );
          })}
        </div>
      </section>

      {/* ── the order of events, as a timeline ── */}
      <section className="scroll-reveal">
        <SectionHead index="03" label="sequence" title="What happens on a failure" />
        <ol className="relative m-0 list-none p-0">
          <span
            aria-hidden
            className="absolute bottom-3 left-[11px] top-2 w-px bg-gradient-to-b from-white/[0.14] via-white/[0.08] to-transparent"
          />
          <span aria-hidden className="absolute inset-y-0 left-[10px] w-[3px] overflow-hidden">
            <span className="rel-spine-dot absolute left-0 h-9 w-full rounded-full bg-gradient-to-b from-transparent via-emerald-400/60 to-transparent" />
          </span>
          {FLOW.map((item) => (
            <li key={item.step} className="relative pb-6 pl-9 last:pb-0">
              <span
                className="absolute left-0 top-0 flex size-[23px] items-center justify-center rounded-full border border-[var(--admin-border)] bg-[#0a0a0a] font-mono text-[9.5px] tabular-nums text-emerald-300/90"
                style={{ fontFamily: MONO }}
                aria-hidden
              >
                {item.step}
              </span>
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">{item.title}</h3>
                <span
                  className="rounded border border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.08em] text-[var(--admin-text-dim)]"
                  style={{ fontFamily: MONO }}
                >
                  {item.chip}
                </span>
              </div>
              <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {item.body}
              </p>
            </li>
          ))}
          <li className="relative pl-9">
            <span
              className="absolute left-0 top-0 flex size-[23px] items-center justify-center rounded-full border border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
              aria-hidden
            >
              <Check size={12} />
            </span>
            <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">
              The caller gets a normal response
            </h3>
            <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
              The failover lives in the request log, not in the client. If every candidate is
              exhausted, the last upstream error is returned in the caller&rsquo;s own dialect.
            </p>
          </li>
        </ol>
        <p className="mt-5 flex items-start gap-2 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
          <Check size={14} className="mt-0.5 shrink-0 text-emerald-400" aria-hidden />
          The exact retry policy is configured under router_settings; the numbers above describe
          the defaults, not a service-level guarantee.
        </p>
      </section>

      {/* ── what the operator can actually see ── */}
      <section className="scroll-reveal">
        <SectionHead index="04" label="observability" title="Recovery you can observe" />
        <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
          {FEATURES.map((feature) => {
            const Icon = feature.icon;
            return (
              <div key={feature.title} className="border-t border-[var(--admin-border)] pt-4">
                <div className="flex items-center gap-2">
                  <Icon size={15} className="shrink-0 text-blue-400" aria-hidden />
                  <h3 className="text-[13.5px] font-semibold text-[var(--admin-text)]">
                    {feature.title}
                  </h3>
                </div>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--admin-text-muted)]">
                  {feature.body}
                </p>
              </div>
            );
          })}
        </div>
      </section>

      </div>

      <QuickstartBand
        id="reliability-quickstart"
        index="05"
        eyebrow="quickstart"
        title={
          <>
            Watch it fail.
            <br />
            <span className="text-[var(--admin-text-muted)]">Watch it </span>
            <span className="text-brand-400">heal.</span>
          </>
        }
        body="Cooldowns, probation, and the healer only matter when a credential dies. Start the gateway locally, pull a key, and watch the pool keep answering — no manual failover, no client retry."
        primary={{ label: "Read about routing", to: "/docs" }}
        secondary={{ label: "Open the console", to: "/console/providers" }}
        tertiary={{ label: "GitHub", href: "https://github.com/shinmentakezo07/wiwia" }}
        steps={[
          {
            title: "Start the gateway",
            detail: "Binds 0.0.0.0:4000 by default.",
            command: "wiwi --config wiwi.yaml",
          },
          {
            title: "Watch key health",
            detail: "active, cooling, invalid, and disabled, live.",
            command:
              'curl localhost:4000/admin/providers -H "Authorization: Bearer $WIWI_MASTER_KEY"',
          },
          {
            title: "Turn on the healer",
            detail: "One-token probes restore cooling keys early.",
            command: "printf 'healer:\\n  enabled: true\\n' >> wiwi.yaml",
          },
        ]}
        motto={
          <>
            Cooldowns, probation, and probes.{" "}
            <span className="text-[var(--admin-text-muted)]">No manual failover.</span>
          </>
        }
      />
    </div>
  );
}
