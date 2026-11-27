// Reliability — how the gateway behaves when a key, a deployment, or a whole
// provider fails. Everything on this page maps to code in wiwi/router/ and
// wiwi/core/recovery.py; no invented uptime numbers.
//
// The page is one argument in three moves: an attempt trace you can switch
// between failure scenarios (what a failure looks like), an escalation ladder
// (where failover happens), and a stepper (the order it happens in). Counts and
// strategy names are taken from providers/base.py, router/router.py and
// recovery.py — change the code and this copy has to follow.
//
// Design notes: flat hairline surfaces, colour only on status codes, no blur,
// no backdrop-filter, no looping animation. The only motion is a one-shot row
// reveal in the trace, gated on reduced motion.

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  Check,
  ChevronRight,
  Clock,
  Copy,
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

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// providers/base.py:RETRYABLE_STATUS — seven statuses, not eight.
const STATS = [
  { value: "7", label: "Retryable statuses" },
  { value: "3", label: "Failover layers" },
  { value: "1 token", label: "Health probes" },
  { value: "0", label: "Manual intervention" },
];

type Tone = "warn" | "fail" | "ok";

interface Attempt {
  target: string;
  status: string;
  detail: string;
  tone: Tone;
}

interface Scenario {
  id: string;
  label: string;
  summary: string;
  attempts: Attempt[];
}

// Three failure shapes, one per failover layer. Each ends in a 200.
const SCENARIOS: Scenario[] = [
  {
    id: "key",
    label: "Key throttled",
    summary: "1 failover · 1 request",
    attempts: [
      {
        target: "openai-main · key 2",
        status: "429",
        detail: "retry-after 12s honoured · key cooling",
        tone: "warn",
      },
      {
        target: "openai-main · key 3",
        status: "200",
        detail: "380 ms · sibling key served, no client retry",
        tone: "ok",
      },
    ],
  },
  {
    id: "deployment",
    label: "Provider down",
    summary: "2 failovers · 1 request",
    attempts: [
      {
        target: "openai-main · key 2",
        status: "429",
        detail: "retry-after 12s honoured · key cooling",
        tone: "warn",
      },
      {
        target: "openai-main · key 3",
        status: "502",
        detail: "deployment cooled 60s · next candidate",
        tone: "fail",
      },
      {
        target: "anthropic-main",
        status: "200",
        detail: "412 ms · served, no client retry",
        tone: "ok",
      },
    ],
  },
  {
    id: "group",
    label: "Group exhausted",
    summary: "3 failovers · 1 request",
    attempts: [
      {
        target: "gpt-4o · openai-main",
        status: "503",
        detail: "deployment cooled 60s",
        tone: "fail",
      },
      {
        target: "gpt-4o · azure-eu",
        status: "529",
        detail: "deployment cooled 60s · group has no usable deployment",
        tone: "fail",
      },
      {
        target: "fallback → claude-sonnet",
        status: "200",
        detail: "506 ms · model-group fallback served",
        tone: "ok",
      },
    ],
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

const FLOW: { title: string; chip: string; body: string }[] = [
  {
    title: "The upstream returns a retryable error",
    chip: "408 · 429 · 5xx",
    body: "408, 429, 500, 502, 503, 504, and 529 are classified as retryable. The attempt is recorded with the upstream status and message.",
  },
  {
    title: "Backoff, then another key or deployment",
    chip: "exponential",
    body: "The router sleeps with exponential backoff — honouring Retry-After when the upstream sends it — and retries against the next eligible key.",
  },
  {
    title: "The failing key cools down",
    chip: "cooldown",
    body: "A key that keeps failing is marked cooling for a cooldown window, so later requests skip it instead of paying the failure again.",
  },
  {
    title: "The healer restores it early",
    chip: "opt-in",
    body: "When enabled, a background sweeper probes cooling keys and deployments with a one-token completion and returns them to service before the cooldown expires.",
  },
  {
    title: "The caller gets a normal response",
    chip: "200",
    body: "The failover lives in the request log, not in the client. If every candidate is exhausted, the last upstream error is returned in the caller\u2019s own dialect.",
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

const NAV = [
  { id: "trace", label: "Trace" },
  { id: "escalation", label: "Escalation" },
  { id: "sequence", label: "Sequence" },
  { id: "observability", label: "Observability" },
];

const STATUS_TEXT: Record<Tone, string> = {
  warn: "text-amber-300",
  fail: "text-red-300",
  ok: "text-emerald-300",
};

const STATUS_DOT: Record<Tone, string> = {
  warn: "bg-amber-400",
  fail: "bg-red-400",
  ok: "bg-emerald-400",
};

const FOCUS =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--admin-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--admin-bg)]";

function SectionHead(props: { id: string; index: string; label: string; title: string }) {
  return (
    <div className="mb-6">
      <p
        className="text-[11px] uppercase tracking-[0.14em] text-[var(--admin-text-dim)]"
        style={{ fontFamily: MONO }}
      >
        {props.index} · {props.label}
      </p>
      <h2
        id={props.id}
        className="mt-2 max-w-[24ch] text-[clamp(1.6rem,3.6vw,2.15rem)] font-semibold leading-[1.1] tracking-[-0.03em] text-[var(--admin-text)]"
      >
        {props.title}
      </h2>
    </div>
  );
}

// ── attempt trace: pick a failure, watch the request still succeed ───────────

function AttemptTrace() {
  const reduced = useReducedMotion();
  const [scenarioId, setScenarioId] = useState(SCENARIOS[1].id);
  const [run, setRun] = useState(0);
  const scenario = SCENARIOS.find((s) => s.id === scenarioId) ?? SCENARIOS[0];

  return (
    <div className="min-w-0 overflow-hidden rounded-2xl border border-[var(--admin-border-hover)] bg-[var(--admin-surface)] shadow-[0_1px_0_0_rgba(255,255,255,0.04)_inset,0_24px_48px_-24px_rgba(0,0,0,0.6)]">
      {/* Request line: anchors the trace to one real call, so the rows read as its history. */}
      <div
        className="flex items-center justify-between gap-3 border-b border-[var(--admin-border)] px-4 py-2.5 text-[11.5px] text-[var(--admin-text-dim)]"
        style={{ fontFamily: MONO }}
      >
        <span className="min-w-0 truncate">
          <span className="text-[var(--admin-text-muted)]">POST</span> /v1/chat/completions
        </span>
        <span className="shrink-0 tabular-nums">{scenario.attempts.length} attempts</span>
      </div>
      <div className="flex items-center gap-1 border-b border-[var(--admin-border)] p-1.5">
        <div role="tablist" aria-label="Failure scenario" className="flex min-w-0 flex-1 gap-1 overflow-x-auto">
          {SCENARIOS.map((s) => {
            const active = s.id === scenarioId;
            return (
              <button
                key={s.id}
                type="button"
                role="tab"
                id={`trace-tab-${s.id}`}
                aria-selected={active}
                aria-controls="trace-panel"
                onClick={() => {
                  setScenarioId(s.id);
                  setRun((n) => n + 1);
                }}
                className={`min-h-11 shrink-0 rounded-lg px-3 text-[12.5px] font-medium transition-colors ${FOCUS} ${
                  active
                    ? "bg-white/[0.08] text-[var(--admin-text)]"
                    : "text-[var(--admin-text-muted)] hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
                }`}
              >
                {s.label}
              </button>
            );
          })}
        </div>
        {!reduced && (
          <button
            type="button"
            onClick={() => setRun((n) => n + 1)}
            aria-label="Replay attempt trace"
            className={`flex size-11 shrink-0 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] ${FOCUS}`}
          >
            <RotateCw size={14} aria-hidden />
          </button>
        )}
      </div>

      <ol
        key={`${scenarioId}-${run}`}
        id="trace-panel"
        role="tabpanel"
        aria-labelledby={`trace-tab-${scenario.id}`}
        className="m-0 list-none divide-y divide-[var(--admin-border)] p-0"
      >
        {scenario.attempts.map((attempt, i) => (
          <li
            key={`${attempt.target}-${i}`}
            style={{ animationDelay: `${i * 0.12}s` }}
            className="rel-trace-row relative grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-x-3 px-4 py-3.5"
          >
            {/* Rail: joins this attempt's dot to the next, so the trace reads as one request. */}
            {i < scenario.attempts.length - 1 && (
              <span
                aria-hidden
                className="absolute bottom-[-0.875rem] left-[calc(1rem+3.5px)] top-[1.6rem] w-px bg-[var(--admin-border-hover)]"
              />
            )}
            <span
              aria-hidden
              className={`relative mt-1.5 size-2 rounded-full ring-4 ring-[var(--admin-surface)] ${STATUS_DOT[attempt.tone]}`}
            />
            <div className="min-w-0">
              <p
                className="mb-1 text-[10.5px] uppercase tracking-[0.12em] text-[var(--admin-text-dim)]"
                style={{ fontFamily: MONO }}
              >
                attempt {i + 1}
                {i === scenario.attempts.length - 1 ? " · served" : ""}
              </p>
              <p className="break-words text-[13px] font-medium text-[var(--admin-text)]">
                {attempt.target}
              </p>
              <p className="mt-0.5 text-[12px] leading-snug text-[var(--admin-text-dim)]">
                {attempt.detail}
              </p>
            </div>
            <span
              style={{ fontFamily: MONO }}
              className={`text-[13px] font-semibold tabular-nums ${STATUS_TEXT[attempt.tone]}`}
            >
              {attempt.status}
            </span>
          </li>
        ))}
      </ol>

      <div
        className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-[var(--admin-border)] px-4 py-2.5 text-[11.5px] text-[var(--admin-text-dim)]"
        style={{ fontFamily: MONO }}
        aria-live="polite"
      >
        <span>{scenario.summary}</span>
        <span className="inline-flex items-center gap-1 text-emerald-300">
          <Check size={12} aria-hidden /> caller saw 200
        </span>
      </div>
    </div>
  );
}

// ── sequence: a stepper instead of a long scroll ─────────────────────────────

function Sequence() {
  const [active, setActive] = useState(0);
  const step = FLOW[active];
  const last = active === FLOW.length - 1;

  return (
    <div className="grid gap-4 md:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]">
      <div
        role="tablist"
        aria-label="Failure sequence"
        aria-orientation="vertical"
        className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible"
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowRight") {
            e.preventDefault();
            setActive((i) => Math.min(FLOW.length - 1, i + 1));
          } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
            e.preventDefault();
            setActive((i) => Math.max(0, i - 1));
          }
        }}
      >
        {FLOW.map((item, i) => {
          const selected = i === active;
          return (
            <button
              key={item.title}
              type="button"
              role="tab"
              id={`seq-tab-${i}`}
              aria-selected={selected}
              aria-controls="seq-panel"
              tabIndex={selected ? 0 : -1}
              onClick={() => setActive(i)}
              className={`flex min-h-11 shrink-0 items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-[13px] transition-colors md:shrink ${FOCUS} ${
                selected
                  ? "border-[var(--admin-border-hover)] bg-white/[0.06] text-[var(--admin-text)]"
                  : "border-transparent text-[var(--admin-text-muted)] hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
              }`}
            >
              <span
                aria-hidden
                className={`w-5 shrink-0 text-[11px] tabular-nums ${
                  selected ? "text-[var(--admin-accent)]" : "text-[var(--admin-text-dim)]"
                }`}
                style={{ fontFamily: MONO }}
              >
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="whitespace-nowrap md:whitespace-normal">{item.title}</span>
            </button>
          );
        })}
      </div>

      <div
        id="seq-panel"
        role="tabpanel"
        aria-labelledby={`seq-tab-${active}`}
        className="relative flex min-w-0 flex-col overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5"
      >
        {/* Progress through the sequence; transform-only so it never triggers layout. */}
        <span aria-hidden className="absolute inset-x-0 top-0 h-px bg-white/[0.06]">
          <span
            className="block h-full origin-left bg-[var(--admin-accent)] transition-transform duration-300 motion-reduce:transition-none"
            style={{ transform: `scaleX(${(active + 1) / FLOW.length})` }}
          />
        </span>
        <span
          className="w-fit rounded-md bg-white/[0.06] px-2 py-1 text-[11px] uppercase tracking-[0.08em] text-[var(--admin-text-muted)]"
          style={{ fontFamily: MONO }}
        >
          {step.chip}
        </span>
        <h3 className="mt-3 text-[17px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {step.title}
        </h3>
        <p className="mt-2 max-w-[60ch] text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          {step.body}
        </p>
        <div className="mt-5 flex items-center justify-between gap-3 pt-1">
          <span className="text-[12px] text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
            {active + 1} / {FLOW.length}
          </span>
          <button
            type="button"
            onClick={() => setActive(last ? 0 : active + 1)}
            className={`inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-[var(--admin-border-hover)] px-4 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.05] ${FOCUS}`}
          >
            {last ? "Start over" : "Next step"}
            {last ? <RotateCw size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── closing: the failover drill, three commands, no decoration ───────────────

const DRILL = [
  {
    title: "Start the gateway",
    detail: "Binds 0.0.0.0:4000 by default.",
    command: "wiwi --config wiwi.yaml",
  },
  {
    title: "Watch key health",
    detail: "active, cooling, invalid, and disabled, live.",
    command: 'curl localhost:4000/admin/providers -H "Authorization: Bearer $WIWI_MASTER_KEY"',
  },
  {
    title: "Turn on the healer",
    detail: "One-token probes restore cooling keys early.",
    command: "printf 'healer:\\n  enabled: true\\n' >> wiwi.yaml",
  },
];

function CopyCommand(props: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(props.text);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be denied in an insecure context; the command stays
      // selectable rather than claiming a copy that did not happen.
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={`Copy ${props.label.toLowerCase()} command`}
      className={`inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg px-3 text-[12px] text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.05] hover:text-[var(--admin-text)] ${FOCUS}`}
    >
      {copied ? (
        <Check size={13} className="text-emerald-300" aria-hidden />
      ) : (
        <Copy size={13} aria-hidden />
      )}
      <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
    </button>
  );
}

function Quickstart() {
  return (
    <section aria-labelledby="reliability-quickstart" className="mx-auto mt-16 max-w-4xl scroll-mt-24">
      <SectionHead
        id="reliability-quickstart"
        index="05"
        label="quickstart"
        title="Watch it fail. Watch it heal."
      />
      <p className="-mt-3 mb-6 max-w-[60ch] text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
        Cooldowns, probation, and the healer only matter when a credential dies. Start the gateway
        locally, pull a key, and watch the pool keep answering — no manual failover, no client
        retry.
      </p>

      <ol className="m-0 list-none divide-y divide-[var(--admin-border)] overflow-hidden rounded-xl border border-[var(--admin-border-hover)] bg-[var(--admin-surface)] p-0">
        {DRILL.map((step, i) => (
          <li key={step.title} className="px-4 py-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">
                  <span
                    className="mr-2 text-[11px] font-normal tabular-nums text-[var(--admin-text-dim)]"
                    style={{ fontFamily: MONO }}
                    aria-hidden
                  >
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  {step.title}
                </h3>
                <p className="mt-0.5 text-[12.5px] text-[var(--admin-text-dim)]">{step.detail}</p>
              </div>
              <CopyCommand label={step.title} text={step.command} />
            </div>
            <pre
              className="m-0 mt-3 overflow-x-auto rounded-lg bg-black/50 px-3 py-2.5 text-[12px] leading-snug text-[var(--admin-text-muted)]"
              style={{ fontFamily: MONO }}
              tabIndex={0}
            >
              <code>
                <span aria-hidden className="select-none text-[var(--admin-text-dim)]">
                  ${" "}
                </span>
                {step.command}
              </code>
            </pre>
          </li>
        ))}
      </ol>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <Link
          to="/docs"
          className={`inline-flex min-h-11 items-center rounded-lg bg-white px-4 text-[13.5px] font-medium text-black transition-colors hover:bg-white/90 ${FOCUS}`}
        >
          Read about routing
        </Link>
        <Link
          to="/console/providers"
          className={`inline-flex min-h-11 items-center rounded-lg border border-[var(--admin-border-hover)] px-4 text-[13.5px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.05] ${FOCUS}`}
        >
          Open the console
        </Link>
        <a
          href="https://github.com/shinmentakezo07/wiwia"
          target="_blank"
          rel="noopener noreferrer"
          className={`inline-flex min-h-11 items-center rounded-lg px-3 text-[13.5px] text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)] ${FOCUS}`}
        >
          GitHub
        </a>
      </div>
    </section>
  );
}

export function ReliabilityPage() {
  return (
    <div className="pb-16">
      <div className="mx-auto max-w-4xl space-y-16">
        {/* ── masthead: the claim, the trace that proves it, the counts ── */}
        <section aria-labelledby="rel-title" className="pt-2">
          <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-12">
            <div className="min-w-0 lg:pt-3">
              <p
                className="text-[11px] uppercase tracking-[0.14em] text-[var(--admin-text-dim)]"
                style={{ fontFamily: MONO }}
              >
                Routing &amp; recovery
              </p>
              <h1
                id="rel-title"
                className="mt-3 text-[clamp(2.25rem,6vw,3.5rem)] font-semibold leading-[1.02] tracking-[-0.045em] text-[var(--admin-text)]"
              >
                A provider fails.
                <br />
                <span className="text-[var(--admin-text-muted)]">The request does not.</span>
              </h1>
              <p className="mt-4 max-w-[46ch] text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
                Retries, per-key cooldowns, deployment fallbacks, and an optional health healer
                keep traffic moving without a manual failover. The client sees one response and
                never learns which key died.
              </p>
              <p className="mt-4 text-[12.5px] text-[var(--admin-text-dim)]">
                Pick a failure on the right to see how the router recovers.
              </p>
            </div>

            <div id="trace" className="min-w-0 scroll-mt-24">
              <AttemptTrace />
            </div>
          </div>

          <dl className="m-0 mt-10 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-border)] sm:grid-cols-4">
            {STATS.map((stat) => (
              <div key={stat.label} className="flex flex-col-reverse bg-[var(--admin-surface)] px-5 py-5">
                <dt className="mt-2.5 text-[11px] uppercase leading-tight tracking-[0.1em] text-[var(--admin-text-dim)]">
                  {stat.label}
                </dt>
                <dd
                  className="m-0 text-[clamp(1.75rem,4vw,2.25rem)] font-semibold tabular-nums leading-none tracking-[-0.03em] text-[var(--admin-text)]"
                  style={{ fontFamily: MONO }}
                >
                  {stat.value}
                </dd>
              </div>
            ))}
          </dl>

          <nav aria-label="On this page" className="mt-6 flex flex-wrap gap-1">
            {NAV.map((item) => (
              <a
                key={item.id}
                href={`#${item.id}`}
                className={`inline-flex min-h-11 items-center rounded-lg px-3 text-[13px] text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] ${FOCUS}`}
              >
                {item.label}
              </a>
            ))}
          </nav>
        </section>

        {/* ── where failover happens, in escalation order ── */}
        <section aria-labelledby="escalation" className="scroll-mt-24">
          <SectionHead
            id="escalation"
            index="02"
            label="escalation"
            title="Failover escalates outward"
          />
          <ol className="m-0 grid list-none gap-px overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-border)] p-0 md:grid-cols-3">
            {LAYERS.map((layer, i) => {
              const Icon = layer.icon;
              return (
                <li key={layer.title} className="min-w-0 bg-[var(--admin-surface)] p-6 transition-colors hover:bg-white/[0.02]">
                  <div className="flex items-center justify-between gap-2">
                    <span
                      className="text-[11px] uppercase tracking-[0.1em] text-[var(--admin-text-dim)]"
                      style={{ fontFamily: MONO }}
                    >
                      {i + 1} · {layer.scope}
                    </span>
                    <Icon size={16} className="shrink-0 text-[var(--admin-text-muted)]" aria-hidden />
                  </div>
                  {/* Blast radius: one segment per layer, so escalation reads at a glance. */}
                  <div aria-hidden className="mt-4 flex gap-1">
                    {LAYERS.map((_, j) => (
                      <span
                        key={j}
                        className={`h-1 flex-1 rounded-full ${
                          j <= i ? "bg-[var(--admin-text-muted)]" : "bg-white/[0.06]"
                        }`}
                      />
                    ))}
                  </div>
                  <h3 className="mt-3 text-[15px] font-semibold text-[var(--admin-text)]">
                    {layer.title}
                  </h3>
                  <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                    {layer.body}
                  </p>
                </li>
              );
            })}
          </ol>
          <p className="mt-3 text-[12px] text-[var(--admin-text-dim)]">
            Each layer is tried only when the one before it has nothing healthy left.
          </p>
        </section>

        {/* ── the order of events, one step at a time ── */}
        <section aria-labelledby="sequence" className="scroll-mt-24">
          <SectionHead
            id="sequence"
            index="03"
            label="sequence"
            title="What happens on a failure"
          />
          <Sequence />
          <p className="mt-5 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
            The exact retry policy is configured under router_settings; the numbers above describe
            the defaults, not a service-level guarantee.
          </p>
        </section>

        {/* ── what the operator can actually see ── */}
        <section aria-labelledby="observability" className="scroll-mt-24">
          <SectionHead
            id="observability"
            index="04"
            label="observability"
            title="Recovery you can observe"
          />
          <ul className="m-0 grid list-none gap-x-10 gap-y-6 p-0 sm:grid-cols-2">
            {FEATURES.map((feature) => {
              const Icon = feature.icon;
              return (
                <li key={feature.title} className="border-t border-[var(--admin-border-hover)] pt-4">
                  <div className="flex items-center gap-2.5">
                    <Icon size={15} className="shrink-0 text-[var(--admin-text-muted)]" aria-hidden />
                    <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">
                      {feature.title}
                    </h3>
                  </div>
                  <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                    {feature.body}
                  </p>
                </li>
              );
            })}
          </ul>
        </section>
      </div>

      <Quickstart />
    </div>
  );
}
