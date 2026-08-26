// Landing — public marketing front door for the gateway. Hero + feature grid +
// how-it-works (dialect → IR → provider) + CTAs. Reuses the dark design tokens
// shared with the admin console so the public face reads as the same product.

import { Link } from "react-router-dom";
import {
  ArrowRight,
  Boxes,
  BookOpen,
  Eye,
  KeyRound,
  Layers,
  RefreshCw,
  Terminal,
  TrendingUp,
  Wallet,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: Layers,
    title: "Three inbound dialects",
    body: "OpenAI Chat Completions, OpenAI Responses (Codex CLI), and Anthropic Messages all speak the same canonical IR — one client, every model.",
  },
  {
    icon: KeyRound,
    title: "Virtual keys",
    body: "Issue per-client credentials with model allowlists, expiry, and spend caps. Callers never see your provider keys.",
  },
  {
    icon: Wallet,
    title: "Budgets & rate limits",
    body: "Per-key spend ceilings and RPM/TPM throttles keep a noisy tenant from burning through your upstream quota.",
  },
  {
    icon: Boxes,
    title: "Key pools",
    body: "Pool multiple keys per provider and route across them with smooth weighted round-robin. Exhausted keys cool down automatically.",
  },
  {
    icon: RefreshCw,
    title: "Retries & fallbacks",
    body: "Automatic retries on transient failures, per-key cooldowns, and fallback model groups so a flaky upstream never reaches your caller.",
  },
  {
    icon: Eye,
    title: "Observability",
    body: "Request logs, token usage, cost tracking, and live stats — every call accounted for, per key, per model, per provider.",
  },
];

const DIALECTS = [
  { name: "chat", note: "OpenAI Chat Completions" },
  { name: "responses", note: "OpenAI Responses (Codex CLI)" },
  { name: "messages", note: "Anthropic Messages (Claude Code)" },
];

const PROVIDERS = ["openai", "anthropic", "gemini", "openrouter"];

function HubNode() {
  return (
    <div className="relative flex h-14 w-14 items-center justify-center rounded-[14px] bg-gradient-to-br from-brand-500 to-fuchsia-600 font-mono text-xl font-bold text-white shadow-lg shadow-brand-600/20 ring-1 ring-white/[0.06] ring-inset">
      w
      <span className="absolute inset-0 rounded-[14px] bg-blue-500/10 blur-md" aria-hidden />
    </div>
  );
}

export function LandingPage() {
  return (
    <div className="space-y-20 pb-20">
      {/* ── hero ── */}
      <section className="relative overflow-hidden rounded-3xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent px-6 py-16 sm:px-12 sm:py-24">
        {/* aurora wash */}
        <div
          className="pointer-events-none absolute -left-20 -top-20 h-[420px] w-[420px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 60%)" }}
          aria-hidden
        />
        <div
          className="pointer-events-none absolute -bottom-24 -right-16 h-[380px] w-[380px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(168,85,247,0.06) 0%, transparent 60%)" }}
          aria-hidden
        />

        <div className="relative mx-auto max-w-3xl text-center">
          <span className="admin-badge admin-badge-blue mb-6 inline-flex items-center gap-1.5">
            <Zap size={11} /> self-hosted · unified LLM gateway
          </span>
          <h1 className="text-4xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-5xl">
            One gateway,
            <br />
            <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
              every model
            </span>
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            wiwi speaks every inbound dialect — OpenAI, Anthropic, Codex CLI — and routes
            through one canonical IR to any provider. Virtual keys, budgets, key pools,
            retries, and live observability, all in one binary.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link
              to="/signup"
              className="wiwi-shimmer group inline-flex h-11 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-[transform,filter] duration-150 hover:brightness-110 active:scale-[0.98]"
            >
              Create an account
              <ArrowRight size={15} className="transition-transform duration-150 group-hover:translate-x-0.5" />
            </Link>
            <Link
              to="/playground"
              className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-sm font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.14] hover:bg-white/[0.04]"
            >
              <Terminal size={15} /> Try the playground
            </Link>
            <Link
              to="/docs"
              className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] px-5 text-sm font-medium text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.02] hover:text-[var(--admin-text)]"
            >
              <BookOpen size={15} /> Read the docs
            </Link>
          </div>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-2 font-mono text-[11px] text-[var(--admin-text-dim)]">
            <Badge tone="gray">OpenAI</Badge>
            <Badge tone="violet">Anthropic</Badge>
            <Badge tone="blue">Gemini</Badge>
            <Badge tone="gray">OpenRouter</Badge>
            <Badge tone="amber">OpenAI-compatible</Badge>
          </div>
        </div>
      </section>

      {/* ── features ── */}
      <section>
        <div className="mb-8 text-center">
          <h2 className="text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Everything a gateway should ship with
          </h2>
          <p className="mt-2 text-[14px] text-[var(--admin-text-muted)]">
            Built-in for every deployment. No plugins, no sidecars.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => {
            const Icon = f.icon;
            return (
              <Card key={f.title} className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
                    <Icon className="h-4 w-4" style={{ color: "rgba(59,130,246,0.85)" }} />
                  </span>
                  <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                    {f.title}
                  </h3>
                </div>
                <p className="mt-3 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{f.body}</p>
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── how it works ── */}
      <section>
        <div className="mb-8 text-center">
          <h2 className="text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Hub-and-spoke translation
          </h2>
          <p className="mt-2 text-[14px] text-[var(--admin-text-muted)]">
            No pairwise converters. Every direction goes dialect → IR → provider, so adding
            a surface or a provider is one module — core code never branches.
          </p>
        </div>

        <Card className="overflow-hidden p-0">
          <div className="grid grid-cols-1 gap-px bg-[var(--admin-border)] md:grid-cols-3">
            {/* inbound */}
            <div className="bg-[var(--admin-surface)] p-6">
              <span className="admin-label mb-3 block">Inbound dialect</span>
              <div className="space-y-2">
                {DIALECTS.map((d) => (
                  <div
                    key={d.name}
                    className="flex items-center gap-2 rounded-lg border border-[var(--admin-border)] bg-white/[0.015] px-3 py-2"
                  >
                    <span className="font-mono text-[13px] text-blue-300">{d.name}</span>
                    <span className="text-[11px] text-[var(--admin-text-dim)]">{d.note}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* hub */}
            <div className="flex flex-col items-center justify-center bg-[var(--admin-surface)] p-6">
              <span className="admin-label mb-3 block">wiwi IR</span>
              <HubNode />
              <p className="mt-4 text-center text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                Decode to a canonical internal representation, then re-encode in the
                caller&apos;s dialect on the way back out.
              </p>
              <div className="mt-4 flex items-center gap-1.5 font-mono text-[10px] text-[var(--admin-text-dim)]">
                <span className="rounded bg-blue-500/10 px-1.5 py-0.5 text-blue-300">decode</span>
                <ArrowRight size={11} />
                <span className="rounded bg-violet-500/10 px-1.5 py-0.5 text-violet-300">encode</span>
              </div>
            </div>

            {/* outbound */}
            <div className="bg-[var(--admin-surface)] p-6">
              <span className="admin-label mb-3 block">Outbound provider</span>
              <div className="space-y-2">
                {PROVIDERS.map((p) => (
                  <div
                    key={p}
                    className="flex items-center gap-2 rounded-lg border border-[var(--admin-border)] bg-white/[0.015] px-3 py-2"
                  >
                    <span className="font-mono text-[13px] text-violet-300">{p}</span>
                    <span className="text-[11px] text-[var(--admin-text-dim)]">
                      {p === "openrouter" || p === "openai" ? "+ any URL" : "adapter"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Card>

        {/* compact code preview */}
        <Card className="mt-4 overflow-hidden p-0">
          <div className="flex items-center gap-2 border-b border-[var(--admin-border)] px-4 py-2.5">
            <TrendingUp size={13} className="text-[var(--admin-text-dim)]" />
            <span className="admin-label">point any client at the gateway</span>
          </div>
          <pre className="overflow-x-auto px-4 py-3 text-[12px] leading-relaxed" style={{ fontFamily: MONO }}>
            <code className="text-[var(--admin-text-muted)]">{`# OpenAI Python SDK, retargeted at wiwi
from openai import OpenAI
client = OpenAI(base_url="http://localhost:4000/v1", api_key="sk-wiwi-…")
client.chat.completions.create(model="gpt-4o", messages=[{"role":"user","content":"hi"}])`}</code>
          </pre>
        </Card>
      </section>

      {/* ── CTA ── */}
      <section className="rounded-2xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent p-8 text-center sm:p-12">
        <h2 className="text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Spin up a gateway in a minute
        </h2>
        <p className="mx-auto mt-3 max-w-md text-[14px] text-[var(--admin-text-muted)]">
          One binary, one config, every model behind a single endpoint.
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          <Link
            to="/signup"
            className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110"
          >
            Get started <ArrowRight size={15} />
          </Link>
          <Link
            to="/docs"
            className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-sm font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            <BookOpen size={15} /> Docs
          </Link>
        </div>
      </section>
    </div>
  );
}
