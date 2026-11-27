// Products — what the gateway actually does: route every dialect to every
// provider, and record every request it routes. Two sections, both grounded
// in the implementation in wiwi/ rather than aspirational copy.
//
// Visual vocabulary (grid wash, orbs, pill eyebrow, proof strip, spotlight
// cards, terminal chrome) matches Open Source / Enterprise so the public pages
// read as one system. The hero diagram carries the one idea the product is
// built on: hub-and-spoke translation through a single IR.

import {
  Activity,
  ArrowRight,
  BarChart3,
  Database,
  Gauge,
  KeyRound,
  Layers,
  RefreshCw,
  ScrollText,
  Server,
  ShieldCheck,
  Waypoints,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { PointerEvent } from "react";
import { Link } from "react-router-dom";
import { CopyButton } from "@/components/ui";
import { ClosingCta } from "@/components/ClosingCta";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const CODE_EXAMPLE = `import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:4000/v1",
  apiKey: process.env.WIWI_VIRTUAL_KEY,
});

const completion = await client.chat.completions.create({
  model: "gpt-5", // a model_name from your model_list
  messages: [{ role: "user", content: "Hello!" }],
});`;

type Feature = { icon: LucideIcon; title: string; body: string };

const GATEWAY_FEATURES: Feature[] = [
  {
    icon: Waypoints,
    title: "Three inbound dialects",
    body: "OpenAI Chat Completions, OpenAI Responses (Codex CLI), and Anthropic Messages (Claude Code) all decode into one internal representation — plus the legacy /v1/completions shape.",
  },
  {
    icon: Layers,
    title: "Thirteen provider types",
    body: "OpenAI, Anthropic, Gemini, OpenRouter, NVIDIA NIM, GMI Cloud, B.AI, OpenCode, Cline, Hermes, IFM, and any OpenAI-compatible endpoint.",
  },
  {
    icon: KeyRound,
    title: "Key pools & weighted routing",
    body: "Several keys per provider, smooth weighted round-robin across them, per-key status, and automatic cooldowns when one starts failing.",
  },
  {
    icon: RefreshCw,
    title: "Retries, fallbacks, healing",
    body: "Exponential backoff on retryable statuses, model-group fallback chains, and an optional background healer that probes cooling keys with a 1-token request.",
  },
  {
    icon: ShieldCheck,
    title: "Virtual keys & budgets",
    body: "SHA-256-hashed virtual keys with per-key model allow-lists, spend budgets, RPM/TPM limits, expiry, and a one-time plaintext reveal.",
  },
  {
    icon: Database,
    title: "Cache & stream journals",
    body: "An opt-in exact-match response cache and durable per-request stream journals, so a reconnecting client can replay a stream after a restart.",
  },
];

const OBSERVABILITY_FEATURES: Feature[] = [
  {
    icon: ScrollText,
    title: "Request and proxy logs",
    body: "Every request with tokens, cost, latency, TTFT, attempts, and the upstream error text — plus a separate proxy stream for upstream metadata.",
  },
  {
    icon: BarChart3,
    title: "Usage & analytics",
    body: "Token, spend, and error rollups by key, model, provider, and time window, with CSV import/export and an in-memory live ring.",
  },
  {
    icon: Activity,
    title: "Live SSE stream",
    body: "The console subscribes to /admin/stream for new requests, proxy events, health changes, and stat ticks — no polling for the live views.",
  },
  {
    icon: Gauge,
    title: "Prometheus metrics",
    body: "A configurable /metrics endpoint exports request, token, cost, and prompt-cache counters for your existing monitoring stack.",
  },
  {
    icon: ShieldCheck,
    title: "Audit trail",
    body: "Every admin mutation is written to an append-only audit log with actor, action, target, and a diff of what changed.",
  },
  {
    icon: Server,
    title: "One process, any storage",
    body: "SQLite by default or Postgres via DATABASE_URL, with Redis optional for the response cache. No worker or sidecar to operate.",
  },
];

const STATS = [
  { value: "3", label: "Inbound dialects" },
  { value: "13", label: "Provider types" },
  { value: "9", label: "/v1 routes" },
  { value: "1", label: "Process" },
];

const DIALECTS = [
  { name: "Chat Completions", path: "/v1/chat/completions" },
  { name: "Responses", path: "/v1/responses" },
  { name: "Messages", path: "/v1/messages" },
];

const PROVIDERS = ["OpenAI", "Anthropic", "Gemini", "OpenRouter", "NVIDIA NIM", "+8 more"];

const STEPS: { title: string; body: string }[] = [
  { title: "Decode", body: "Your SDK's request is parsed into the internal representation — no per-pair converter." },
  { title: "Route", body: "A healthy deployment is picked from the key pool; retries and fallbacks happen here." },
  { title: "Re-encode", body: "The provider's reply streams back in the exact shape your client sent." },
];

/** Track the pointer so the card's radial spotlight follows it. */
function trackSpotlight(e: PointerEvent<HTMLElement>) {
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--x", `${e.clientX - r.left}px`);
  el.style.setProperty("--y", `${e.clientY - r.top}px`);
}

// The hub-and-spoke model in one glance: dialects in, one IR, providers out.
// Stacks vertically on narrow screens; the connectors switch axis with it.
function TranslationDiagram() {
  return (
    <figure
      aria-label="Three inbound dialects decode into one internal representation, which routes to any provider"
      className="wiwi-conic-border mx-auto w-full max-w-md shadow-[0_30px_80px_-30px_rgba(59,130,246,0.35)]"
    >
      <div className="relative rounded-[15px] bg-[#070707]/95 p-4 backdrop-blur sm:p-5">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex gap-1.5" aria-hidden>
            <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
            <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
            <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
          </div>
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">
            request path
          </span>
        </div>

        <div className="grid grid-cols-1 items-center gap-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
          <div className="min-w-0">
            <p className="admin-label mb-2">Inbound</p>
            <ul className="space-y-2">
              {DIALECTS.map((d) => (
                <li key={d.path} className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                  <div className="text-[12.5px] font-medium text-[var(--admin-text)]">{d.name}</div>
                  <div
                    className="truncate text-[11px] text-[var(--admin-text-dim)]"
                    style={{ fontFamily: MONO }}
                    title={d.path}
                  >
                    {d.path}
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="flex items-center justify-center gap-2 sm:flex-col sm:pt-6" aria-hidden>
            <span className="h-5 w-px bg-gradient-to-b from-transparent to-blue-400/50 sm:h-px sm:w-5 sm:bg-gradient-to-r" />
            <span
              className="flex h-12 w-12 items-center justify-center rounded-xl border border-blue-400/30 bg-blue-500/15 text-[11px] font-semibold tracking-[0.14em] text-blue-200 ring-4 ring-blue-500/[0.06]"
              style={{ fontFamily: MONO }}
            >
              IR
            </span>
            <span className="h-5 w-px bg-gradient-to-t from-transparent to-blue-400/50 sm:h-px sm:w-5 sm:bg-gradient-to-l" />
          </div>

          <div className="min-w-0">
            <p className="admin-label mb-2">Outbound</p>
            <ul className="flex flex-wrap gap-1.5 sm:flex-col sm:flex-nowrap">
              {PROVIDERS.map((p) => (
                <li
                  key={p}
                  className={`rounded-md border px-2.5 py-1 text-[12px] ${
                    p.startsWith("+")
                      ? "border-dashed border-white/[0.08] text-[var(--admin-text-dim)]"
                      : "border-white/[0.06] bg-white/[0.02] text-[var(--admin-text-muted)]"
                  }`}
                >
                  {p}
                </li>
              ))}
            </ul>
          </div>
        </div>

        <figcaption className="mt-4 border-t border-white/[0.06] pt-3 font-mono text-[10.5px] leading-relaxed text-[var(--admin-text-dim)]">
          No pairwise converters. Responses are re-encoded in the dialect the client sent.
        </figcaption>
      </div>
    </figure>
  );
}

function SectionIntro(props: { index: string; title: string; body?: string }) {
  return (
    <div className="mb-7 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between sm:gap-8">
      <div>
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-blue-300/80">{props.index}</span>
        <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.015em] text-[var(--admin-text)] sm:text-[28px]">
          {props.title}
        </h2>
      </div>
      {props.body && (
        <p className="max-w-sm text-[14px] leading-relaxed text-[var(--admin-text-muted)] sm:text-right">{props.body}</p>
      )}
    </div>
  );
}

// Routing features: spotlight cards, the same vocabulary as Open Source.
function FeatureCards(props: { label: string; features: Feature[] }) {
  return (
    <ul aria-label={props.label} className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {props.features.map((feature, i) => {
        const Icon = feature.icon;
        return (
          <li
            key={feature.title}
            onPointerMove={trackSpotlight}
            className="group relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-white/[0.015] p-5 transition-[border-color,background-color,transform] duration-300 hover:-translate-y-0.5 hover:border-blue-500/25 hover:bg-white/[0.03] motion-reduce:transition-none motion-reduce:hover:translate-y-0"
          >
            <div className="ent-spotlight pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100" aria-hidden />
            <div className="relative">
              <div className="mb-5 flex items-start justify-between">
                <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/[0.06] bg-gradient-to-br from-blue-500/15 to-violet-500/10 text-blue-300 transition-colors duration-300 group-hover:border-blue-500/30 group-hover:text-blue-200">
                  <Icon size={17} aria-hidden />
                </span>
                <span className="font-mono text-[10px] tabular-nums tracking-[0.18em] text-[var(--admin-text-dim)]" aria-hidden>
                  0{i + 1}
                </span>
              </div>
              <h4 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">{feature.title}</h4>
              <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{feature.body}</p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// Console features: a hairline ledger inside one surface — it reads as a record
// list, which is what the console is.
function FeatureLedger(props: { label: string; features: Feature[] }) {
  return (
    <ul
      aria-label={props.label}
      className="divide-y divide-[var(--admin-border)] rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)]"
    >
      {props.features.map((feature) => {
        const Icon = feature.icon;
        return (
          <li
            key={feature.title}
            className="group grid grid-cols-[2.5rem_minmax(0,1fr)] gap-4 px-5 py-5 transition-colors first:rounded-t-2xl last:rounded-b-2xl hover:bg-white/[0.02] motion-reduce:transition-none"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.06] bg-white/[0.02] text-[var(--admin-text-dim)] transition-colors group-hover:border-blue-500/30 group-hover:text-blue-300">
              <Icon size={16} aria-hidden />
            </span>
            <div className="min-w-0">
              <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">{feature.title}</h3>
              <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{feature.body}</p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function CodeBlock({ code, filename }: { code: string; filename: string }) {
  return (
    <div className="min-w-0 overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[#070707]">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--admin-border)] py-1.5 pl-4 pr-1.5">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-white/10" aria-hidden />
          <span className="h-2.5 w-2.5 rounded-full bg-white/10" aria-hidden />
          <span className="h-2.5 w-2.5 rounded-full bg-white/10" aria-hidden />
          <span className="ml-2 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">
            {filename}
          </span>
        </div>
        <CopyButton text={code} className="min-h-11 min-w-11" />
      </div>
      <pre className="overflow-x-auto p-5">
        <code className="text-[12.5px] leading-[1.8] text-zinc-200" style={{ fontFamily: MONO }}>
          {code}
        </code>
      </pre>
    </div>
  );
}

export function ProductsPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-20 pb-16 sm:space-y-24">
      {/* ── hero ── */}
      <section className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)] sm:rounded-[32px]">
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="hero-grid absolute inset-x-0 top-0 h-[620px] opacity-70" />
          <div
            className="hero-orb-1 absolute -left-28 -top-32 h-[460px] w-[460px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(59,130,246,0.18) 0%, transparent 62%)" }}
          />
          <div
            className="hero-orb-2 absolute -right-32 -top-16 h-[420px] w-[420px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(139,92,246,0.16) 0%, transparent 62%)" }}
          />
        </div>
        <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70" aria-hidden />

        <div className="relative grid items-center gap-12 px-6 py-14 sm:px-10 sm:py-16 lg:grid-cols-[1.05fr_1fr] lg:gap-14 lg:px-14 lg:py-20">
          <div className="text-center lg:text-left">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/[0.06] px-3.5 py-1.5 text-blue-300">
              <Waypoints size={12} aria-hidden />
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em]">Gateway · Console</span>
            </div>
            <h1 className="text-3xl font-semibold leading-[1.08] tracking-[-0.025em] text-[var(--admin-text)] sm:text-4xl lg:text-[52px]">
              One gateway,
              <br />
              <span className="hero-gradient-text">two jobs.</span>
            </h1>
            <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base lg:mx-0">
              Translate every inbound dialect to every provider, and record everything that
              passes through. Both run in the same self-hosted process.
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row lg:justify-start">
              <Link
                to="/docs"
                className="group inline-flex h-11 w-full items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-7 text-sm font-semibold text-white shadow-lg shadow-brand-600/25 transition-[transform,filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/60 active:scale-[0.98] motion-reduce:transition-none sm:w-auto"
              >
                Read the quickstart
                <ArrowRight size={15} aria-hidden className="transition-transform duration-150 group-hover:translate-x-0.5" />
              </Link>
              <a
                href="#gateway"
                className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.02] px-6 text-sm font-medium text-[var(--admin-text)] backdrop-blur transition-colors hover:border-white/[0.16] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
              >
                See what ships
              </a>
            </div>
          </div>
          <TranslationDiagram />
        </div>

        {/* proof strip */}
        <dl className="relative grid grid-cols-2 gap-px border-t border-[var(--admin-border)] bg-[var(--admin-border)] sm:grid-cols-4">
          {STATS.map((stat) => (
            <div key={stat.label} className="flex flex-col-reverse bg-[var(--admin-surface)] px-5 py-6 text-center">
              <dt className="admin-label mt-1.5">{stat.label}</dt>
              <dd className="admin-stat-value text-2xl sm:text-[28px]">{stat.value}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* ── gateway ── */}
      <section id="gateway" className="scroll-reveal scroll-mt-24">
        <SectionIntro
          index=".01 · the gateway"
          title="Swap one base URL"
          body="Point your existing OpenAI SDK at the gateway. Nothing else in your code changes."
        />
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <CodeBlock code={CODE_EXAMPLE} filename="app.ts" />
          <ol className="grid content-start gap-3" aria-label="What happens to a request">
            {STEPS.map((step, i) => (
              <li
                key={step.title}
                className="grid grid-cols-[2.25rem_minmax(0,1fr)] gap-3 rounded-2xl border border-[var(--admin-border)] bg-white/[0.015] p-4"
              >
                <span
                  className="flex h-8 w-8 items-center justify-center rounded-full border border-blue-400/25 bg-blue-500/10 text-[11px] text-blue-200"
                  style={{ fontFamily: MONO }}
                  aria-hidden
                >
                  {i + 1}
                </span>
                <div className="min-w-0">
                  <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">{step.title}</h3>
                  <p className="mt-1 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
        <h3 className="admin-label mb-4 mt-12">What ships in the routing layer</h3>
        <FeatureCards label="What ships in the routing layer" features={GATEWAY_FEATURES} />
      </section>

      {/* ── console: sticky heading on desktop, ledger beside it ── */}
      <section className="scroll-reveal grid gap-8 lg:grid-cols-[1fr_2fr] lg:gap-14">
        <div className="lg:sticky lg:top-24 lg:self-start">
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-blue-300/80">.02 · the console</span>
          <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.015em] text-[var(--admin-text)] sm:text-[28px]">
            Every request, on the record
          </h2>
          <p className="mt-3 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Each request the gateway serves becomes a row you can inspect, filter, chart, and
            export. The same admin API drives the console, so anything the UI shows is
            available to your own tooling.
          </p>
        </div>
        <FeatureLedger label="What the console records" features={OBSERVABILITY_FEATURES} />
      </section>

      <ClosingCta
        eyebrow=".03 · get started"
        title="One gateway. Every client. Your providers."
        body="Point any OpenAI, Anthropic, or Codex client at one base URL. The gateway translates the dialect, picks a healthy deployment, and logs the request — you keep the keys."
        primary={{ label: "Read the quickstart", to: "/docs" }}
        secondary={{ label: "Self-host it", to: "/open-source" }}
        footnote="Streaming, tool calls, and prompt-cache accounting work on every surface."
        commands={[
          { title: "Prepare the config", command: "cp wiwi.yaml.example wiwi.yaml" },
          { title: "Start the gateway", command: "wiwi --config wiwi.yaml" },
          { title: "Swap one base URL", command: "export OPENAI_BASE_URL=http://localhost:4000/v1\nexport OPENAI_API_KEY=<wiwi-key>" },
        ]}
      />
    </div>
  );
}
