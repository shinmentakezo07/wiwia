// Products — what the gateway actually does: route every dialect to every
// provider, and record every request it routes. Two sections, both grounded
// in the implementation in wiwi/ rather than aspirational copy.

import { Link } from "react-router-dom";
import {
  Activity,
  BarChart3,
  BookOpen,
  Database,
  Gauge,
  KeyRound,
  Layers,
  RefreshCw,
  ScrollText,
  Server,
  ShieldCheck,
  Terminal,
  Waypoints,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

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

const GATEWAY_FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
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

const OBSERVABILITY_FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
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

function FeatureGrid(props: {
  title: string;
  features: { icon: LucideIcon; title: string; body: string }[];
}) {
  return (
    <section>
      <h3 className="mb-5 text-center text-[20px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
        {props.title}
      </h3>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {props.features.map((feature) => {
          const Icon = feature.icon;
          return (
            <Card key={feature.title} className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl border border-[var(--admin-border)] bg-white/[0.02]">
                <Icon className="h-5 w-5 text-[var(--admin-text)]/80" strokeWidth={1.8} />
              </div>
              <h4 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                {feature.title}
              </h4>
              <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {feature.body}
              </p>
            </Card>
          );
        })}
      </div>
    </section>
  );
}

function CodeBlock({ code, filename }: { code: string; filename: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--admin-border)] bg-zinc-950">
      <div className="flex items-center gap-2 border-b border-[var(--admin-border)] bg-white/[0.02] px-4 py-3">
        <span className="h-3 w-3 rounded-full bg-[#ff5f57]" />
        <span className="h-3 w-3 rounded-full bg-[#febc2e]" />
        <span className="h-3 w-3 rounded-full bg-[#28c840]" />
        <span className="ml-2 font-mono text-[12px] text-[var(--admin-text-dim)]">{filename}</span>
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
    <div className="mx-auto max-w-4xl space-y-12 pb-16">
      <section className="text-center">
        <Badge tone="blue">Product</Badge>
        <h1 className="mt-4 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          One gateway,{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            two jobs
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Translate every inbound dialect to every provider, and record everything that
          passes through. Both run in the same self-hosted process.
        </p>
        <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {STATS.map((stat) => (
            <div key={stat.label} className="rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-3 py-4">
              <div className="font-mono text-2xl font-semibold tabular-nums text-[var(--admin-text)]">
                {stat.value}
              </div>
              <div className="mt-1 text-[11px] uppercase tracking-[0.12em] text-[var(--admin-text-muted)]">
                {stat.label}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-8">
        <div>
          <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
            The gateway
          </h2>
          <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Point your existing OpenAI SDK at the gateway. Requests decode into a neutral
            internal representation, route to the provider you configured, and come back
            re-encoded in the dialect your client speaks.
          </p>
        </div>
        <CodeBlock code={CODE_EXAMPLE} filename="app.ts" />
        <FeatureGrid title="What ships in the routing layer" features={GATEWAY_FEATURES} />
      </section>

      <section className="space-y-8">
        <div>
          <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
            The console
          </h2>
          <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Every request the gateway serves becomes a row you can inspect, filter, chart,
            and export. The same admin API drives the console, so anything the UI shows is
            available to your own tooling.
          </p>
        </div>
        <FeatureGrid title="What the console records" features={OBSERVABILITY_FEATURES} />
      </section>

      <section className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-8 text-center">
        <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Run it yourself
        </h2>
        <p className="mx-auto mt-2 max-w-xl text-[15px] text-[var(--admin-text-muted)]">
          The whole platform is MIT-licensed. Start the binary, mint a virtual key, and
          send your first request in three commands.
        </p>
        <div className="mt-5 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link to="/docs" className="admin-btn admin-btn-primary">
            <BookOpen size={15} className="mr-2" />
            Read the quickstart
          </Link>
          <Link to="/open-source" className="admin-btn admin-btn-ghost">
            <Terminal size={15} className="mr-2" />
            Self-hosting details
          </Link>
        </div>
      </section>
    </div>
  );
}
