// Timeline — LLM release dates by year. Adapted from the llmgateway.io timeline
// page with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import { ArrowRight, ArrowUpRight, Sparkles } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

interface TimelineModel {
  id: string;
  name: string;
  provider: string;
  releasedAt: string;
}

const STATS = {
  totalModels: 200,
  totalProviders: 40,
  firstYear: 2018,
  latestReleasedAt: "2026-08-20",
};

const YEAR_SUMMARIES: { year: number; count: number; providers: string[]; highlights: string[] }[] = [
  { year: 2026, count: 45, providers: ["OpenAI", "Anthropic", "Google", "Meta"], highlights: ["GPT-5", "Claude Opus 4", "Gemini 3 Pro"] },
  { year: 2025, count: 38, providers: ["OpenAI", "Anthropic", "Google", "DeepSeek"], highlights: ["GPT-4.1", "Claude Sonnet 4", "Gemini 2.5"] },
  { year: 2024, count: 42, providers: ["OpenAI", "Anthropic", "Google", "Meta"], highlights: ["GPT-4o", "Claude 3.5 Sonnet", "Gemini 1.5"] },
  { year: 2023, count: 28, providers: ["OpenAI", "Anthropic", "Google", "Meta"], highlights: ["GPT-4", "Claude 2", "Gemini"] },
  { year: 2022, count: 12, providers: ["OpenAI", "Meta", "Stability"], highlights: ["ChatGPT", "LLaMA", "Stable Diffusion 2"] },
  { year: 2021, count: 6, providers: ["OpenAI", "Google"], highlights: ["DALL-E", "LaMDA"] },
  { year: 2020, count: 4, providers: ["OpenAI"], highlights: ["GPT-3"] },
  { year: 2019, count: 2, providers: ["OpenAI"], highlights: ["GPT-2"] },
  { year: 2018, count: 1, providers: ["OpenAI"], highlights: ["GPT"] },
];

const MONTH_RELEASES: TimelineModel[] = [
  { id: "gpt-5", name: "GPT-5", provider: "OpenAI", releasedAt: "2026-08-20" },
  { id: "claude-opus-4", name: "Claude Opus 4", provider: "Anthropic", releasedAt: "2026-08-12" },
  { id: "gemini-3-pro", name: "Gemini 3 Pro", provider: "Google", releasedAt: "2026-08-05" },
  { id: "deepseek-r1", name: "DeepSeek R1", provider: "DeepSeek", releasedAt: "2026-07-28" },
  { id: "qwen-2.5-72b", name: "Qwen 2.5 72B", provider: "Qwen", releasedAt: "2026-07-20" },
  { id: "mistral-large-3", name: "Mistral Large 3", provider: "Mistral", releasedAt: "2026-07-15" },
];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
}

const FAQS: { question: string; answer: string }[] = [
  {
    question: "What is an LLM release timeline?",
    answer:
      "A chronological record of when every major large language model was released by its provider — GPT, Claude, Gemini, Llama, Mistral, DeepSeek, and more — and when each became available through the gateway.",
  },
  {
    question: "How often is the timeline updated?",
    answer:
      "Continuously. New models are added within 48 hours of release, often the same day. The dataset reflects every major frontier and open-weight model from 2018 onward.",
  },
  {
    question: "Can I filter by provider or year?",
    answer:
      "Yes. Browse by year using the year cards below, or filter by provider on each year's detail page. Every model links to its own page with pricing, context window, and availability.",
  },
  {
    question: "How many models are on the timeline?",
    answer:
      "200+ models across 40+ providers, from GPT in 2018 through the latest frontier releases. The count grows every month as new models ship.",
  },
];

export function TimelinePage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <Badge tone="blue">
          <Sparkles size={12} className="mr-1.5" />
          Model release timeline
        </Badge>
        <h1 className="mt-4 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          When every{" "}
          <span className="bg-gradient-to-r from-sky-400 to-blue-400 bg-clip-text text-transparent">
            LLM was released
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          A continuously updated timeline of large language model releases — when each
          model shipped from its provider and when it landed on the gateway. Track GPT,
          Claude, Gemini, Llama, Mistral, DeepSeek and more. Browse the full history by
          year.
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-3">
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-[20px] font-bold tabular-nums text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
              {STATS.totalModels}
            </span>
            <span className="text-[13px] text-[var(--admin-text-muted)]">models</span>
          </div>
          <span className="text-[var(--admin-text-dim)]">•</span>
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-[20px] font-bold tabular-nums text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
              {STATS.totalProviders}
            </span>
            <span className="text-[13px] text-[var(--admin-text-muted)]">providers</span>
          </div>
          <span className="text-[var(--admin-text-dim)]">•</span>
          <div className="flex items-baseline gap-1.5">
            <span className="text-[13px] text-[var(--admin-text-muted)]">since</span>
            <span className="font-mono text-[20px] font-bold tabular-nums text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
              {STATS.firstYear}
            </span>
          </div>
          <span className="text-[var(--admin-text-dim)]">•</span>
          <div className="flex items-center gap-1.5">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/70" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
            </span>
            <span className="text-[13px] text-[var(--admin-text-muted)]">
              Updated <time className="font-medium text-[var(--admin-text)]">{formatDate(STATS.latestReleasedAt)}</time>
            </span>
          </div>
        </div>
      </section>

      {/* ── latest month releases ── */}
      <section>
        <h2 className="text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          New AI models released this month
        </h2>
        <p className="mt-3 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          {MONTH_RELEASES.length} new AI models were released this month, from{" "}
          {new Set(MONTH_RELEASES.map((m) => m.provider)).size} providers. The most recent
          is {MONTH_RELEASES[0].name} from {MONTH_RELEASES[0].provider}, released{" "}
          {formatDate(MONTH_RELEASES[0].releasedAt)}. Every one is available through a
          single API.
        </p>
        <Card className="mt-5 overflow-hidden">
          {MONTH_RELEASES.map((model) => (
            <Link
              key={model.id}
              to={`/models/${encodeURIComponent(model.id)}`}
              className="flex items-baseline justify-between gap-4 border-b border-[var(--admin-border)] px-4 py-3 transition-colors last:border-b-0 hover:bg-white/[0.02]"
            >
              <span className="text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:text-blue-400">
                {model.name}
              </span>
              <span className="shrink-0 text-[12px] text-[var(--admin-text-muted)]">
                {model.provider} · <time>{formatDate(model.releasedAt)}</time>
              </span>
            </Link>
          ))}
        </Card>
      </section>

      {/* ── browse by year ── */}
      <section>
        <div className="mb-4">
          <h2 className="text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Browse by year
          </h2>
          <p className="mt-1 text-[14px] text-[var(--admin-text-muted)]">
            Jump to the models released in a given year.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {YEAR_SUMMARIES.map((summary) => (
            <Link key={summary.year} to={`/timeline/${summary.year}`} className="group">
              <Card className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
                <div className="flex items-baseline justify-between">
                  <h3 className="font-mono text-[22px] font-bold tabular-nums text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
                    {summary.year}
                  </h3>
                  <span className="text-[13px] text-[var(--admin-text-muted)]">
                    {summary.count} {summary.count === 1 ? "model" : "models"}
                  </span>
                </div>
                {summary.providers.length > 0 && (
                  <p className="mt-2 truncate text-[12px] text-[var(--admin-text-muted)]">
                    {summary.providers.slice(0, 4).join(" · ")}
                  </p>
                )}
                {summary.highlights.length > 0 && (
                  <p className="mt-3 line-clamp-2 text-[13px] text-[var(--admin-text)]/80">
                    {summary.highlights.join(", ")}
                  </p>
                )}
                <span className="mt-4 inline-flex items-center gap-1 text-[12px] font-medium text-blue-400">
                  View {summary.year} releases
                  <ArrowUpRight size={12} className="transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                </span>
              </Card>
            </Link>
          ))}
        </div>
      </section>

      {/* ── FAQ ── */}
      <section>
        <div className="mb-6 text-center">
          <span className="admin-label">FAQ</span>
          <h2 className="mt-2 text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            LLM release dates, answered
          </h2>
        </div>
        <div className="space-y-4">
          {FAQS.map((faq) => (
            <div key={faq.question} className="border-b border-[var(--admin-border)] py-5">
              <dt className="text-[15px] font-semibold text-[var(--admin-text)]">{faq.question}</dt>
              <dd className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{faq.answer}</dd>
            </div>
          ))}
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-8 text-center">
        <h2 className="text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Route to any of these models with one API
        </h2>
        <p className="mx-auto mt-2 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          Switch to the newest model the day it ships — no new SDK, no vendor lock-in. One
          key for every provider on this timeline.
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
