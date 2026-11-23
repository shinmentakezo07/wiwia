// Products — what the gateway actually does: route every dialect to every
// provider, and record every request it routes. Two sections, both grounded
// in the implementation in wiwi/ rather than aspirational copy.
//
// Layout: an editorial hero beside a live diagram of the hub-and-spoke
// translation (the one idea the product is built on), a stat ledger, then each
// job as a numbered section with its features as a two-column ledger rather
// than a stock card grid.

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
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Badge, CopyButton } from "@/components/ui";
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
    body: "OpenAI, Anthropic, Gemini, OpenRouter, NVIDIA NIM, GMI Cloud, B.AI, OpenCode, Cline, WorkBuddy, Hermes, IFM, and any OpenAI-compatible endpoint.",
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

// The hub-and-spoke model in one glance: dialects in, one IR, providers out.
// Stacks vertically on narrow screens; the connectors switch axis with it.
function TranslationDiagram() {
  return (
    <figure
      aria-label="Three inbound dialects decode into one internal representation, which routes to any provider"
      className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 sm:p-5"
    >
      <div className="grid grid-cols-1 items-center gap-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:gap-4">
        <ul className="space-y-2">
          {DIALECTS.map((d) => (
            <li
              key={d.path}
              className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] px-3 py-2"
            >
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

        <div className="flex items-center justify-center gap-2 sm:flex-col">
          <span aria-hidden className="h-4 w-px bg-[var(--admin-border)] sm:h-px sm:w-6" />
          <div className="rounded-full border border-blue-400/30 bg-blue-400/[0.06] px-3 py-1.5 text-center">
            <div className="text-[11px] font-semibold tracking-[0.14em] text-blue-300" style={{ fontFamily: MONO }}>
              IR
            </div>
          </div>
          <span aria-hidden className="h-4 w-px bg-[var(--admin-border)] sm:h-px sm:w-6" />
        </div>

        <ul className="flex flex-wrap gap-1.5 sm:flex-col sm:flex-nowrap">
          {PROVIDERS.map((p) => (
            <li
              key={p}
              className={`rounded-md border px-2.5 py-1 text-[12px] ${
                p.startsWith("+")
                  ? "border-dashed border-[var(--admin-border)] text-[var(--admin-text-dim)]"
                  : "border-[var(--admin-border)] bg-white/[0.02] text-[var(--admin-text-muted)]"
              }`}
            >
              {p}
            </li>
          ))}
        </ul>
      </div>
      <figcaption className="mt-4 border-t border-[var(--admin-border)] pt-3 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
        No pairwise converters. Responses are re-encoded in the dialect the client sent.
      </figcaption>
    </figure>
  );
}

function SectionIntro(props: { index: string; title: string; children: ReactNode }) {
  return (
    <header className="grid gap-2 sm:grid-cols-[5rem_minmax(0,1fr)] sm:gap-6">
      <span
        className="pt-1 text-[12px] tracking-[0.12em] text-[var(--admin-text-dim)]"
        style={{ fontFamily: MONO }}
      >
        {props.index}
      </span>
      <div>
        <h2 className="text-[24px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
          {props.title}
        </h2>
        <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          {props.children}
        </p>
      </div>
    </header>
  );
}

// Features as a ledger: hairline-separated rows, two columns on wide screens.
function FeatureLedger(props: { label: string; features: Feature[] }) {
  return (
    <section aria-label={props.label} className="sm:pl-[6.5rem]">
      <h3
        className="mb-3 text-[11px] uppercase tracking-[0.14em] text-[var(--admin-text-dim)]"
      >
        {props.label}
      </h3>
      <ul className="grid grid-cols-1 border-t border-[var(--admin-border)] md:grid-cols-2 md:gap-x-8">
        {props.features.map((feature) => {
          const Icon = feature.icon;
          return (
            <li
              key={feature.title}
              className="group grid grid-cols-[2rem_minmax(0,1fr)] gap-3 border-b border-[var(--admin-border)] py-5"
            >
              <Icon
                aria-hidden
                className="mt-0.5 h-[18px] w-[18px] text-[var(--admin-text-dim)] transition-colors group-hover:text-blue-300 motion-reduce:transition-none"
                strokeWidth={1.8}
              />
              <div className="min-w-0">
                <h4 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                  {feature.title}
                </h4>
                <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                  {feature.body}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function CodeBlock({ code, filename }: { code: string; filename: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--admin-border)] bg-zinc-950 sm:ml-[6.5rem]">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--admin-border)] bg-white/[0.02] py-1.5 pl-4 pr-1.5">
        <span className="font-mono text-[12px] text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
          {filename}
        </span>
        <CopyButton text={code} className="min-h-11 min-w-11" />
      </div>
      <pre className="overflow-x-auto p-5">
        <code className="font-mono text-[13px] leading-relaxed text-zinc-200" style={{ fontFamily: MONO }}>
          {code}
        </code>
      </pre>
    </div>
  );
}

export function ProductsPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-20 pb-16">
      <section className="grid items-center gap-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div>
          <Badge tone="blue">Product</Badge>
          <h1 className="mt-4 text-[2rem] font-semibold leading-[1.1] tracking-[-0.03em] text-[var(--admin-text)] sm:text-[2.75rem]">
            One gateway,
            <br />
            <span className="text-[var(--admin-text-muted)]">two jobs.</span>
          </h1>
          <p className="mt-5 max-w-md text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            Translate every inbound dialect to every provider, and record everything that
            passes through. Both run in the same self-hosted process.
          </p>
          <div className="mt-7 flex flex-col gap-3 sm:flex-row">
            <Link
              to="/docs"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[14px] font-medium text-white transition-[filter] hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400"
            >
              Read the quickstart
              <ArrowRight size={16} aria-hidden />
            </Link>
            <a
              href="#gateway"
              className="inline-flex min-h-11 items-center justify-center rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400"
            >
              See what ships
            </a>
          </div>
        </div>
        <TranslationDiagram />
      </section>

      <dl className="grid grid-cols-2 border-y border-[var(--admin-border)] sm:grid-cols-4">
        {STATS.map((stat, i) => (
          <div
            key={stat.label}
            className={`px-4 py-5 ${i % 2 === 1 ? "border-l" : ""} ${i >= 2 ? "border-t sm:border-t-0" : ""} ${
              i === 2 ? "sm:border-l" : ""
            } border-[var(--admin-border)]`}
          >
            <dt className="text-[11px] uppercase tracking-[0.12em] text-[var(--admin-text-dim)]">
              {stat.label}
            </dt>
            <dd
              className="mt-1 text-[1.75rem] font-semibold tabular-nums tracking-[-0.02em] text-[var(--admin-text)]"
              style={{ fontFamily: MONO }}
            >
              {stat.value}
            </dd>
          </div>
        ))}
      </dl>

      <section id="gateway" className="scroll-mt-24 space-y-8">
        <SectionIntro index=".01" title="The gateway">
          Point your existing OpenAI SDK at the gateway. Requests decode into a neutral
          internal representation, route to the provider you configured, and come back
          re-encoded in the dialect your client speaks.
        </SectionIntro>
        <CodeBlock code={CODE_EXAMPLE} filename="app.ts" />
        <FeatureLedger label="What ships in the routing layer" features={GATEWAY_FEATURES} />
      </section>

      <section className="space-y-8">
        <SectionIntro index=".02" title="The console">
          Every request the gateway serves becomes a row you can inspect, filter, chart,
          and export. The same admin API drives the console, so anything the UI shows is
          available to your own tooling.
        </SectionIntro>
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
