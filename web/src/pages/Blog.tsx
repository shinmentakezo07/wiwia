// Blog — listing page backed by the shared blog data module. Each entry links
// to its full article at /blog/:slug. Matches the dark design system.

import { Link } from "react-router-dom";
import { ArrowRight, Clock } from "lucide-react";
import { Card } from "@/components/ui";
import { BLOG_POSTS } from "@/data/blog";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function BlogPage() {
  const sorted = [...BLOG_POSTS].sort(
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
          <Link key={entry.slug} to={`/blog/${entry.slug}`} className="group block">
            <Card className="p-5 transition-colors group-hover:border-[var(--admin-border-hover)]">
              <div className="flex items-start gap-4">
                <span
                  className="mt-1 font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]"
                  style={{ fontFamily: MONO }}
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-3">
                    <span className="admin-badge admin-badge-blue">{entry.category}</span>
                    <span className="font-mono text-[11px] text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
                      {formatDate(entry.date)}
                    </span>
                    <span className="inline-flex items-center gap-1 font-mono text-[11px] text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
                      <Clock size={11} /> {entry.readMinutes} min read
                    </span>
                  </div>
                  <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--admin-text)] transition-colors group-hover:text-blue-400">
                    {entry.title}
                  </h2>
                  <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                    {entry.summary}
                  </p>
                  <span className="mt-2 inline-flex items-center gap-1 text-[13px] font-medium text-blue-400">
                    Read article
                    <ArrowRight size={13} className="transition-transform group-hover:translate-x-0.5" />
                  </span>
                </div>
              </div>
            </Card>
          </Link>
        ))}
      </section>
    </div>
  );
}
