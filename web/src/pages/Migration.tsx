// Migration — guides to switch from other LLM providers. Adapted from the
// llmgateway.io migration page with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Card } from "@/components/ui";

interface Migration {
  slug: string;
  title: string;
  description: string;
  fromProvider: string;
  icon: string;
}

const MIGRATIONS: Migration[] = [
  {
    slug: "open-router",
    title: "From OpenRouter",
    description: "Switch from OpenRouter to the gateway with minimal code changes. Our OpenAI-compatible API makes it straightforward.",
    fromProvider: "OpenRouter",
    icon: "OR",
  },
  {
    slug: "vercel-ai-gateway",
    title: "From Vercel AI Gateway",
    description: "Migrate from Vercel AI Gateway to the gateway. Keep the same SDK, change the base URL and API key.",
    fromProvider: "Vercel AI Gateway",
    icon: "▲",
  },
  {
    slug: "litellm",
    title: "From LiteLLM",
    description: "Switch from LiteLLM to the gateway. Open source, self-hostable, with virtual keys and budgets built in.",
    fromProvider: "LiteLLM",
    icon: "🚅",
  },
  {
    slug: "github-copilot",
    title: "From GitHub Copilot",
    description: "Replace Copilot's metered AI Credits with pass-through token pricing and hard budget caps.",
    fromProvider: "GitHub Copilot",
    icon: "GH",
  },
  {
    slug: "portkey",
    title: "From Portkey",
    description: "Migrate from Portkey to the gateway with the same OpenAI-compatible interface and open source under AGPLv3.",
    fromProvider: "Portkey",
    icon: "P",
  },
  {
    slug: "cloudflare-ai-gateway",
    title: "From Cloudflare AI Gateway",
    description: "Switch from Cloudflare AI Gateway to the gateway for self-hostable, open source routing.",
    fromProvider: "Cloudflare",
    icon: "CF",
  },
];

export function MigrationPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Migration{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            Guides
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Switch to the gateway from other LLM providers with minimal code changes. Our
          OpenAI-compatible API makes migration straightforward.
        </p>
      </section>

      {/* ── migration cards ── */}
      <section className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {MIGRATIONS.map((migration) => (
          <Link key={migration.slug} to={`/migration/${migration.slug}`} className="group">
            <Card className="flex h-full flex-col p-5 transition-colors hover:border-[var(--admin-border-hover)]">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] text-[16px] font-bold text-[var(--admin-text)]">
                {migration.icon}
              </div>
              <h2 className="mb-2 text-[16px] font-semibold tracking-[-0.01em] text-[var(--admin-text)] transition-colors group-hover:text-blue-400">
                {migration.title}
              </h2>
              <p className="mb-4 flex-grow text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {migration.description}
              </p>
              <div className="flex items-center text-[13px] font-medium text-blue-400">
                Read guide
                <ArrowRight size={14} className="ml-1 transition-transform group-hover:translate-x-0.5" />
              </div>
            </Card>
          </Link>
        ))}
      </section>

      {/* ── fallback ── */}
      <section>
        <Card className="p-8 text-center">
          <h2 className="mb-2 text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Don&apos;t see your provider?
          </h2>
          <p className="mb-4 text-[14px] text-[var(--admin-text-muted)]">
            The gateway&apos;s OpenAI-compatible API works with any client that supports
            OpenAI. Just change the base URL and API key.
          </p>
          <Link
            to="/docs"
            className="inline-flex items-center text-[14px] font-medium text-blue-400 hover:underline"
          >
            View Quick Start Guide
            <ArrowRight size={14} className="ml-1" />
          </Link>
        </Card>
      </section>
    </div>
  );
}
