// Guides — step-by-step integration tutorials.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, BookOpen, Clock, Code2, Monitor, Search, Terminal, Workflow, X, type LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

type Category = "Terminal" | "IDE" | "Desktop" | "Automation";

interface Guide {
  slug: string;
  title: string;
  description: string;
  category: Category;
  minutes: number;
  featured?: boolean;
}

const GUIDES: Guide[] = [
  { slug: "claude-code", title: "Claude Code", description: "Connect Claude Code to the gateway for AI-powered terminal assistance and coding with any model.", category: "Terminal", minutes: 3, featured: true },
  { slug: "cursor", title: "Cursor IDE", description: "Use the gateway with Cursor in plan and agent mode. Tab autocomplete and inline edit stay on Cursor's backend.", category: "IDE", minutes: 4, featured: true },
  { slug: "codex-cli", title: "Codex CLI", description: "Use the gateway with OpenAI's Codex CLI for AI-powered terminal coding.", category: "Terminal", minutes: 3, featured: true },
  { slug: "cline", title: "Cline (VS Code)", description: "Integrate the gateway with Cline for AI-powered coding assistance directly in VS Code.", category: "IDE", minutes: 3 },
  { slug: "opencode", title: "OpenCode", description: "Use the gateway with OpenCode CLI for AI-powered development workflows.", category: "Terminal", minutes: 3 },
  { slug: "continue", title: "Continue CLI", description: "Use the gateway with Continue's open-source AI code assistant CLI.", category: "Terminal", minutes: 3 },
  { slug: "github-copilot", title: "GitHub Copilot app", description: "Use the gateway as a model provider in GitHub's Copilot desktop app for agent sessions with any model.", category: "Desktop", minutes: 4 },
  { slug: "n8n", title: "n8n Workflows", description: "Connect n8n workflow automation to the gateway for AI-powered automation pipelines.", category: "Automation", minutes: 5 },
];

const CATEGORY_META: Record<Category, { icon: LucideIcon; tint: string }> = {
  Terminal: { icon: Terminal, tint: "from-emerald-400/20 to-emerald-400/0 text-emerald-300 border-emerald-400/25" },
  IDE: { icon: Code2, tint: "from-blue-400/20 to-blue-400/0 text-blue-300 border-blue-400/25" },
  Desktop: { icon: Monitor, tint: "from-fuchsia-400/20 to-fuchsia-400/0 text-fuchsia-300 border-fuchsia-400/25" },
  Automation: { icon: Workflow, tint: "from-amber-400/20 to-amber-400/0 text-amber-300 border-amber-400/25" },
};

const CATEGORIES: (Category | "All")[] = ["All", "Terminal", "IDE", "Desktop", "Automation"];

const STEPS = [
  { n: "01", title: "Create a virtual key", body: "Issue a scoped key from the console so each tool gets its own budget and logs." },
  { n: "02", title: "Point your tool at the gateway", body: "Swap the base URL for the gateway endpoint — the API stays OpenAI / Anthropic compatible." },
  { n: "03", title: "Pick any model", body: "Route to any provider, with failover and cost tracking handled for you." },
];

function GuideIcon({ category, large = false }: { category: Category; large?: boolean }) {
  const { icon: Icon, tint } = CATEGORY_META[category];
  return (
    <div className={`flex ${large ? "h-12 w-12 rounded-xl" : "h-10 w-10 rounded-lg"} shrink-0 items-center justify-center border bg-gradient-to-b ${tint}`}>
      <Icon size={large ? 22 : 18} strokeWidth={1.75} />
    </div>
  );
}

function GuideCard({ guide, featured = false }: { guide: Guide; featured?: boolean }) {
  return (
    <Link to={`/guides/${guide.slug}`} className="group block h-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60">
      <Card className={`relative flex h-full flex-col overflow-hidden p-5 transition-all duration-200 hover:-translate-y-0.5 hover:border-[var(--admin-border-hover)] hover:shadow-[0_8px_30px_-12px_rgba(59,130,246,0.35)] ${featured ? "bg-gradient-to-b from-white/[0.04] to-transparent" : ""}`}>
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-blue-400/50 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
        <div className="flex items-start justify-between gap-3">
          <GuideIcon category={guide.category} large={featured} />
          <span className="admin-badge admin-badge-gray">{guide.category}</span>
        </div>
        <h3 className="mt-4 text-[15px] font-semibold text-[var(--admin-text)] transition-colors group-hover:text-blue-400">{guide.title}</h3>
        <p className="mt-1.5 flex-1 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{guide.description}</p>
        <div className="mt-5 flex items-center justify-between border-t border-[var(--admin-border)] pt-3 text-[12px]">
          <span className="inline-flex items-center gap-1.5 text-[var(--admin-text-dim)]"><Clock size={12} />{guide.minutes} min setup</span>
          <span className="inline-flex items-center font-medium text-blue-400">Read guide<ArrowRight size={14} className="ml-1 transition-transform group-hover:translate-x-1" /></span>
        </div>
      </Card>
    </Link>
  );
}

export function GuidesPage() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category | "All">("All");

  const counts = useMemo(() => {
    const c: Record<string, number> = { All: GUIDES.length };
    for (const g of GUIDES) c[g.category] = (c[g.category] ?? 0) + 1;
    return c;
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return GUIDES.filter((g) => (category === "All" || g.category === category) && (!q || g.title.toLowerCase().includes(q) || g.description.toLowerCase().includes(q)));
  }, [query, category]);

  const isDefaultView = category === "All" && !query.trim();
  const featured = GUIDES.filter((g) => g.featured);
  const reset = () => { setQuery(""); setCategory("All"); };

  return (
    <div className="mx-auto max-w-5xl space-y-14 pb-20">
      {/* ── hero ── */}
      <section className="relative pt-6 text-center">
        <div className="pointer-events-none absolute left-1/2 top-0 -z-10 h-56 w-[36rem] max-w-full -translate-x-1/2 rounded-full bg-gradient-to-r from-blue-500/15 to-fuchsia-500/15 blur-3xl" />
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--admin-border)] bg-white/[0.03] px-3 py-1 text-[12px] font-medium text-[var(--admin-text-muted)]">
          <BookOpen size={13} className="text-blue-400" />{GUIDES.length} integration guides
        </span>
        <h1 className="mt-5 text-4xl font-semibold tracking-[-0.03em] text-[var(--admin-text)] sm:text-5xl">
          Connect your tools in{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">minutes</span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Step-by-step tutorials to integrate the gateway with your favorite development tools and workflows.
        </p>
        <div className="relative mx-auto mt-8 max-w-md">
          <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search guides…"
            aria-label="Search guides"
            className="h-11 w-full rounded-xl border border-[var(--admin-border)] bg-white/[0.03] pl-10 pr-10 text-[14px] text-[var(--admin-text)] transition-colors placeholder:text-[var(--admin-text-dim)] focus:border-blue-400/50 focus:outline-none focus:ring-2 focus:ring-blue-400/20"
          />
          {query && (
            <button type="button" onClick={() => setQuery("")} aria-label="Clear search" className="absolute right-3 top-1/2 -translate-y-1/2 rounded p-0.5 text-[var(--admin-text-dim)] hover:text-[var(--admin-text)]">
              <X size={14} />
            </button>
          )}
        </div>
      </section>

      {/* ── quick start ── */}
      {isDefaultView && (
        <section className="grid gap-3 sm:grid-cols-3">
          {STEPS.map((s) => (
            <div key={s.n} className="rounded-xl border border-[var(--admin-border)] bg-white/[0.02] p-4">
              <div className="font-mono text-[12px] font-semibold text-blue-400">{s.n}</div>
              <div className="mt-1.5 text-[14px] font-semibold text-[var(--admin-text)]">{s.title}</div>
              <p className="mt-1 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{s.body}</p>
            </div>
          ))}
        </section>
      )}

      {/* ── featured ── */}
      {isDefaultView && (
        <section className="space-y-4">
          <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--admin-text-dim)]">Popular</h2>
          <div className="grid gap-4 md:grid-cols-3">
            {featured.map((g) => <GuideCard key={g.slug} guide={g} featured />)}
          </div>
        </section>
      )}

      {/* ── all guides ── */}
      <section className="space-y-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--admin-text-dim)]">
            {isDefaultView ? "All guides" : `${filtered.length} result${filtered.length === 1 ? "" : "s"}`}
          </h2>
          <div role="tablist" aria-label="Filter by category" className="flex flex-wrap gap-1.5">
            {CATEGORIES.map((c) => {
              const active = c === category;
              return (
                <button
                  key={c}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setCategory(c)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] font-medium transition-colors ${active ? "border-blue-400/40 bg-blue-400/10 text-blue-300" : "border-[var(--admin-border)] text-[var(--admin-text-muted)] hover:border-[var(--admin-border-hover)] hover:text-[var(--admin-text)]"}`}
                >
                  {c}<span className="text-[11px] opacity-60">{counts[c] ?? 0}</span>
                </button>
              );
            })}
          </div>
        </div>

        {filtered.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((g) => <GuideCard key={g.slug} guide={g} />)}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-[var(--admin-border)] px-6 py-14 text-center">
            <Search size={20} className="mx-auto text-[var(--admin-text-dim)]" />
            <p className="mt-3 text-[14px] font-medium text-[var(--admin-text)]">No guides match your filters</p>
            <button type="button" onClick={reset} className="mt-3 text-[13px] font-medium text-blue-400 hover:underline">Clear filters</button>
          </div>
        )}
      </section>

      {/* ── CTA ── */}
      <section className="rounded-2xl border border-[var(--admin-border)] bg-gradient-to-br from-blue-500/10 via-transparent to-fuchsia-500/10 p-8 text-center sm:p-10">
        <h2 className="text-xl font-semibold tracking-[-0.02em] text-[var(--admin-text)]">Don't see your tool?</h2>
        <p className="mx-auto mt-2 max-w-md text-[14px] text-[var(--admin-text-muted)]">
          Anything that speaks the OpenAI or Anthropic API works with the gateway. Browse the docs or tell us what to cover next.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <Link to="/docs" className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-blue-500 px-4 text-[13px] font-medium text-white transition-colors hover:bg-blue-400">Read the docs <ArrowRight size={14} /></Link>
          <Link to="/contact" className="inline-flex h-10 items-center rounded-lg border border-[var(--admin-border)] px-4 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)]">Request a guide</Link>
        </div>
      </section>
    </div>
  );
}
