// Blog — listing page with inline post entries. Adapted from the llmgateway.io
// blog index, rendered in the dark design system.

import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

interface BlogItem {
  id: string;
  slug: string;
  date: string;
  title: string;
  summary: string;
  category: string;
}

const ENTRIES: BlogItem[] = [
  {
    id: "1",
    slug: "ai-gateway-101",
    date: "2026-08-20",
    title: "What is an AI Gateway, and why you need one",
    summary:
      "A practical guide to the AI gateway pattern: one endpoint, every provider, with routing, failover, and cost controls built in.",
    category: "Guides",
  },
  {
    id: "2",
    slug: "copilot-cost-calculator",
    date: "2026-08-15",
    title: "How to estimate your GitHub Copilot bill in 2026",
    summary:
      "Copilot moved to usage-based AI Credits. Here's how the billing works and how to estimate your team's monthly cost.",
    category: "Cost",
  },
  {
    id: "3",
    slug: "prompt-caching-deep-dive",
    date: "2026-08-10",
    title: "Prompt caching: how it works and what it saves",
    summary:
      "Caching repeated context at ~10% of the input rate is the single biggest lever on agentic coding spend.",
    category: "Engineering",
  },
  {
    id: "4",
    slug: "multi-provider-routing",
    date: "2026-08-05",
    title: "Multi-provider routing without lock-in",
    summary:
      "Route to OpenAI, Anthropic, Gemini, and OpenRouter through one OpenAI-compatible endpoint. No SDK changes.",
    category: "Architecture",
  },
  {
    id: "5",
    slug: "self-hosting-guide",
    date: "2026-07-28",
    title: "Self-hosting an LLM gateway in one Docker command",
    summary:
      "The entire platform — gateway, dashboard, and API — ships in a single image. Here's how to deploy it.",
    category: "Self-hosting",
  },
  {
    id: "6",
    slug: "model-comparison-2026",
    date: "2026-07-20",
    title: "GPT-5 vs Claude Opus vs Gemini Pro: a developer comparison",
    summary:
      "We benchmarked the frontier models on coding, reasoning, and latency so you don't have to.",
    category: "Comparisons",
  },
];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function BlogPage() {
  const sorted = [...ENTRIES].sort(
    (a, b) => new Date(b.date).getTime() - new Date(a.date).getTime(),
  );

  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <span className="admin-label">Blog</span>
        <h1 className="mt-2 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          News, tutorials, and{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            deep-dives
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Latest news and updates on AI gateways, model routing, LLM costs, model
          comparisons, and shipping production AI apps.
        </p>
      </section>

      {/* ── post list ── */}
      <section className="space-y-4">
        {sorted.map((entry, index) => (
          <Link key={entry.id} to={`/blog/${entry.slug}`} className="block">
            <Card className="group p-5 transition-colors hover:border-[var(--admin-border-hover)]">
              <div className="flex items-start gap-4">
                <span
                  className="mt-1 font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]"
                  style={{ fontFamily: MONO }}
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div className="flex-1">
                  <div className="mb-1 flex items-center gap-3">
                    <span className="admin-badge admin-badge-blue">{entry.category}</span>
                    <span className="font-mono text-[11px] text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
                      {formatDate(entry.date)}
                    </span>
                  </div>
                  <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--admin-text)] transition-colors group-hover:text-blue-400">
                    {entry.title}
                  </h2>
                  <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                    {entry.summary}
                  </p>
                </div>
                <ArrowRight
                  size={16}
                  className="mt-1 shrink-0 text-[var(--admin-text-dim)] transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-blue-400"
                />
              </div>
            </Card>
          </Link>
        ))}
      </section>
    </div>
  );
}
