// Migration — guides to switch from other LLM providers. Listing backed by the
// shared migration data module; each card opens its guide at /migration/:slug.

import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Card } from "@/components/ui";
import { MIGRATIONS } from "@/data/migrations";

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
            <Card className="flex h-full flex-col p-5 transition-colors group-hover:border-[var(--admin-border-hover)]">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] text-[16px] font-bold text-[var(--admin-text)]">
                {migration.icon}
              </div>
              <h2 className="mb-2 text-[16px] font-semibold tracking-[-0.01em] text-[var(--admin-text)] transition-colors group-hover:text-blue-400">
                {migration.title}
              </h2>
              <p className="flex-grow text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {migration.summary}
              </p>
              <span className="mt-3 inline-flex items-center text-[13px] font-medium text-blue-400">
                Read guide
                <ArrowRight size={13} className="ml-1 transition-transform group-hover:translate-x-0.5" />
              </span>
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
