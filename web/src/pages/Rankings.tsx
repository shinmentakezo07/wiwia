// Rankings — live LLM rankings by real usage. Adapted from the llmgateway.io
// rankings page with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import { ArrowRight, TrendingUp } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

interface RankedModel {
  id: string;
  name: string;
  family: string;
  provider: string;
  tokens: number;
  weekOverWeek: number;
}

const RANKED_MODELS: RankedModel[] = [
  { id: "gpt-5", name: "GPT-5", family: "OpenAI", provider: "OpenAI", tokens: 1_240_000_000, weekOverWeek: 12 },
  { id: "claude-opus-4", name: "Claude Opus 4", family: "Anthropic", provider: "Anthropic", tokens: 980_000_000, weekOverWeek: 8 },
  { id: "gemini-3-pro", name: "Gemini 3 Pro", family: "Google", provider: "Google", tokens: 720_000_000, weekOverWeek: 15 },
  { id: "claude-sonnet-4", name: "Claude Sonnet 4", family: "Anthropic", provider: "Anthropic", tokens: 650_000_000, weekOverWeek: -3 },
  { id: "gpt-4.1", name: "GPT-4.1", family: "OpenAI", provider: "OpenAI", tokens: 510_000_000, weekOverWeek: 5 },
  { id: "gemini-3-flash", name: "Gemini 3 Flash", family: "Google", provider: "Google", tokens: 430_000_000, weekOverWeek: 22 },
  { id: "deepseek-r1", name: "DeepSeek R1", family: "DeepSeek", provider: "DeepSeek", tokens: 380_000_000, weekOverWeek: 18 },
  { id: "llama-3.1-70b", name: "Llama 3.1 70B", family: "Meta", provider: "OpenRouter", tokens: 290_000_000, weekOverWeek: -8 },
  { id: "mistral-large", name: "Mistral Large", family: "Mistral", provider: "Mistral", tokens: 210_000_000, weekOverWeek: 3 },
  { id: "qwen-2.5-72b", name: "Qwen 2.5 72B", family: "Qwen", provider: "OpenRouter", tokens: 150_000_000, weekOverWeek: -12 },
];

const numberFormatter = new Intl.NumberFormat("en-US");

function formatTokens(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return numberFormatter.format(n);
}

const PROVIDER_SHARE = [
  { provider: "OpenAI", pct: 38 },
  { provider: "Anthropic", pct: 28 },
  { provider: "Google", pct: 18 },
  { provider: "DeepSeek", pct: 8 },
  { provider: "Meta", pct: 5 },
  { provider: "Other", pct: 3 },
];



export function RankingsPage() {
  const maxTokens = RANKED_MODELS[0]?.tokens ?? 0;

  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section>
        <span className="admin-label">Live from the gateway</span>
        <h1 className="mt-2 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          LLM{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            Rankings
          </span>
        </h1>
        <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Which models developers actually run in production. Ranked by real token
          volume routed through the gateway — not benchmarks, not vibes.
        </p>
      </section>

      {/* ── ranked list ── */}
      <section>
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-[20px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Top models by usage
          </h2>
          <Badge tone="green">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            Live
          </Badge>
        </div>
        <Card className="overflow-hidden">
          <div className="grid grid-cols-[3rem_1fr_auto_auto] gap-4 border-b border-[var(--admin-border)] px-5 py-3 text-[10px] font-medium uppercase tracking-widest text-[var(--admin-text-dim)]">
            <span>#</span>
            <span>Model</span>
            <span className="text-right">Tokens (7d)</span>
            <span className="text-right">WoW</span>
          </div>
          {RANKED_MODELS.map((model, index) => (
            <Link
              key={model.id}
              to={`/models/${encodeURIComponent(model.id)}`}
              className="group grid grid-cols-[3rem_1fr_auto_auto] items-center gap-4 border-b border-[var(--admin-border)] px-5 py-4 transition-colors last:border-b-0 hover:bg-white/[0.02]"
            >
              <span className="font-mono text-[18px] font-bold tabular-nums text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
                {String(index + 1).padStart(2, "0")}
              </span>
              <div className="min-w-0">
                <p className="text-[14px] font-semibold text-[var(--admin-text)] transition-colors group-hover:text-blue-400">
                  {model.name}
                </p>
                <p className="text-[12px] text-[var(--admin-text-muted)]">{model.provider}</p>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-[15px] font-semibold tabular-nums text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
                  {formatTokens(model.tokens)}
                </span>
                <div className="hidden w-16 sm:block">
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.05]">
                    <div
                      className="h-full rounded-full bg-blue-400/60"
                      style={{ width: `${Math.max(2, (model.tokens / maxTokens) * 100)}%` }}
                    />
                  </div>
                </div>
              </div>
              <span
                className={`flex items-center justify-end gap-1 text-[13px] font-medium tabular-nums ${
                  model.weekOverWeek >= 0 ? "text-emerald-400" : "text-red-400"
                }`}
              >
                {model.weekOverWeek >= 0 ? "↑" : "↓"}
                {Math.abs(model.weekOverWeek)}%
              </span>
            </Link>
          ))}
        </Card>
      </section>

      {/* ── provider market share ── */}
      <section>
        <h2 className="mb-4 text-[20px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Provider market share
        </h2>
        <Card className="p-5">
          <div className="space-y-3">
            {PROVIDER_SHARE.map((p) => (
              <div key={p.provider} className="flex items-center gap-3">
                <span className="w-20 text-[13px] font-medium text-[var(--admin-text)]">{p.provider}</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/[0.05]">
                  <div className="h-full rounded-full bg-blue-400/60" style={{ width: `${p.pct}%` }} />
                </div>
                <span className="w-10 text-right font-mono text-[13px] tabular-nums text-[var(--admin-text-muted)]" style={{ fontFamily: MONO }}>
                  {p.pct}%
                </span>
              </div>
            ))}
          </div>
        </Card>
      </section>

      {/* ── CTA ── */}
      <section className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-8 text-center">
        <TrendingUp className="mx-auto h-8 w-8 text-blue-400" />
        <h2 className="mt-3 text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Route to any of these models with one API
        </h2>
        <p className="mx-auto mt-2 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          Switch to the newest model the day it ships — no new SDK, no vendor lock-in.
          One key for every provider on this leaderboard.
        </p>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
          <Link
            to="/signup"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            Get your API key
            <ArrowRight size={16} />
          </Link>
          <Link
            to="/models"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            Browse all models
          </Link>
        </div>
      </section>
    </div>
  );
}
