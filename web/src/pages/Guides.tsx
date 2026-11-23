// Guides — step-by-step integration tutorials.
//
// Visual language matches the landing page's Enterprise and Quickstart bands:
// a framed dark hero over a masked grid with drifting glows, conic-bordered
// featured cards, pointer spotlights tinted per category, and a CTA ring.
// All motion is gated on prefers-reduced-motion via the shared CSS classes.

import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BookOpen,
  Clock,
  Code2,
  KeyRound,
  Layers,
  Monitor,
  Route,
  Search,
  Sparkles,
  Terminal,
  Workflow,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";

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

// Class strings are literal so Tailwind's scanner keeps them.
const CATEGORY_META: Record<Category, { icon: LucideIcon; tile: string; chip: string; text: string; glow: string; bar: string }> = {
  Terminal: {
    icon: Terminal,
    tile: "from-emerald-400/20 to-emerald-400/0 text-emerald-300 border-emerald-400/25",
    chip: "border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-300",
    text: "group-hover:text-emerald-300",
    glow: "rgba(52,211,153,0.12)",
    bar: "via-emerald-400/60",
  },
  IDE: {
    icon: Code2,
    tile: "from-blue-400/20 to-blue-400/0 text-blue-300 border-blue-400/25",
    chip: "border-blue-400/20 bg-blue-400/[0.06] text-blue-300",
    text: "group-hover:text-blue-300",
    glow: "rgba(96,165,250,0.13)",
    bar: "via-blue-400/60",
  },
  Desktop: {
    icon: Monitor,
    tile: "from-fuchsia-400/20 to-fuchsia-400/0 text-fuchsia-300 border-fuchsia-400/25",
    chip: "border-fuchsia-400/20 bg-fuchsia-400/[0.06] text-fuchsia-300",
    text: "group-hover:text-fuchsia-300",
    glow: "rgba(232,121,249,0.12)",
    bar: "via-fuchsia-400/60",
  },
  Automation: {
    icon: Workflow,
    tile: "from-amber-400/20 to-amber-400/0 text-amber-300 border-amber-400/25",
    chip: "border-amber-400/20 bg-amber-400/[0.06] text-amber-300",
    text: "group-hover:text-amber-300",
    glow: "rgba(251,191,36,0.11)",
    bar: "via-amber-400/60",
  },
};

const CATEGORIES: (Category | "All")[] = ["All", "Terminal", "IDE", "Desktop", "Automation"];

const STEPS: { n: string; title: string; body: string; icon: LucideIcon }[] = [
  { n: "01", title: "Create a virtual key", body: "Issue a scoped key from the console so each tool gets its own budget and logs.", icon: KeyRound },
  { n: "02", title: "Point your tool at the gateway", body: "Swap the base URL for the gateway endpoint — the API stays OpenAI / Anthropic compatible.", icon: Route },
  { n: "03", title: "Pick any model", body: "Route to any provider, with failover and cost tracking handled for you.", icon: Sparkles },
];

/** Track the pointer so a card's radial spotlight follows it. */
function trackSpotlight(e: PointerEvent<HTMLElement>) {
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--x", `${e.clientX - r.left}px`);
  el.style.setProperty("--y", `${e.clientY - r.top}px`);
}

function spotlightStyle(color: string): CSSProperties {
  return { background: `radial-gradient(260px circle at var(--x, 50%) var(--y, 0%), ${color}, transparent 70%)` };
}

function GuideIcon({ category, large = false }: { category: Category; large?: boolean }) {
  const { icon: Icon, tile } = CATEGORY_META[category];
  return (
    <div
      className={`relative flex ${large ? "h-12 w-12 rounded-xl" : "h-10 w-10 rounded-lg"} shrink-0 items-center justify-center border bg-gradient-to-b shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] transition-transform duration-300 group-hover:scale-105 ${tile}`}
    >
      <Icon size={large ? 22 : 18} strokeWidth={1.75} />
    </div>
  );
}

function CardFooter({ guide }: { guide: Guide }) {
  return (
    <div className="mt-5 flex items-center justify-between border-t border-white/[0.06] pt-3 text-[12px]">
      <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-[var(--admin-text-dim)]">
        <Clock size={12} aria-hidden />
        {guide.minutes} min setup
      </span>
      <span className="inline-flex items-center font-medium text-[var(--admin-text-muted)] transition-colors group-hover:text-blue-300">
        Read guide
        <ArrowRight size={14} className="ml-1 transition-transform duration-200 group-hover:translate-x-1" aria-hidden />
      </span>
    </div>
  );
}

function GuideCard({ guide }: { guide: Guide }) {
  const meta = CATEGORY_META[guide.category];
  return (
    <Link
      to={`/guides/${guide.slug}`}
      onPointerMove={trackSpotlight}
      className="group relative flex h-full flex-col overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5 transition-[transform,border-color,box-shadow] duration-200 hover:-translate-y-0.5 hover:border-white/[0.14] hover:shadow-[0_18px_40px_-20px_rgba(0,0,0,0.9)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60"
    >
      <span className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100" style={spotlightStyle(meta.glow)} aria-hidden />
      <span className={`pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent ${meta.bar} to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100`} aria-hidden />
      <div className="relative flex items-start justify-between gap-3">
        <GuideIcon category={guide.category} />
        <span className={`rounded-full border px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.12em] ${meta.chip}`}>{guide.category}</span>
      </div>
      <h3 className={`relative mt-4 text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)] transition-colors ${meta.text}`}>{guide.title}</h3>
      <p className="relative mt-1.5 flex-1 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{guide.description}</p>
      <div className="relative">
        <CardFooter guide={guide} />
      </div>
    </Link>
  );
}

function FeaturedCard({ guide, rank }: { guide: Guide; rank: number }) {
  const meta = CATEGORY_META[guide.category];
  return (
    <Link to={`/guides/${guide.slug}`} className="group block h-full rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60">
      <div className="wiwi-conic-border h-full shadow-[0_30px_80px_-40px_rgba(59,130,246,0.45)] transition-transform duration-200 group-hover:-translate-y-1">
        <div onPointerMove={trackSpotlight} className="relative flex h-full flex-col overflow-hidden rounded-[15px] bg-[#070707] p-6">
          <span className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70" aria-hidden />
          <span className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100" style={spotlightStyle(meta.glow)} aria-hidden />
          <span aria-hidden className="pointer-events-none absolute -right-2 -top-6 select-none font-mono text-[88px] font-bold leading-none text-white/[0.03] transition-colors duration-300 group-hover:text-white/[0.06]">
            {String(rank).padStart(2, "0")}
          </span>
          <div className="relative flex items-start justify-between gap-3">
            <GuideIcon category={guide.category} large />
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/20 bg-amber-400/[0.06] px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-amber-300">
              <Zap size={10} aria-hidden />
              Popular
            </span>
          </div>
          <h3 className={`relative mt-5 text-[17px] font-semibold tracking-[-0.015em] text-[var(--admin-text)] transition-colors ${meta.text}`}>{guide.title}</h3>
          <p className="relative mt-2 flex-1 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{guide.description}</p>
          <div className="relative">
            <CardFooter guide={guide} />
          </div>
        </div>
      </div>
    </Link>
  );
}

function SectionLabel({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">{children}</h2>
      <span className="h-px flex-1 bg-gradient-to-r from-white/[0.08] to-transparent" aria-hidden />
      {right}
    </div>
  );
}

export function GuidesPage() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category | "All">("All");
  const inputRef = useRef<HTMLInputElement>(null);

  // "/" focuses search from anywhere on the page (unless already typing).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const counts = useMemo(() => {
    const c: Record<string, number> = { All: GUIDES.length };
    for (const g of GUIDES) c[g.category] = (c[g.category] ?? 0) + 1;
    return c;
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return GUIDES.filter(
      (g) => (category === "All" || g.category === category) && (!q || g.title.toLowerCase().includes(q) || g.description.toLowerCase().includes(q)),
    );
  }, [query, category]);

  const isDefaultView = category === "All" && !query.trim();
  const featured = GUIDES.filter((g) => g.featured);
  const avgMinutes = Math.round(GUIDES.reduce((s, g) => s + g.minutes, 0) / GUIDES.length);
  const reset = () => {
    setQuery("");
    setCategory("All");
  };

  const stats: { icon: LucideIcon; value: string; label: string }[] = [
    { icon: BookOpen, value: String(GUIDES.length), label: "guides" },
    { icon: Layers, value: String(CATEGORIES.length - 1), label: "categories" },
    { icon: Clock, value: `~${avgMinutes} min`, label: "avg setup" },
  ];

  return (
    <div className="mx-auto max-w-6xl space-y-16 px-1 pb-24">
      {/* ── hero ── */}
      <section aria-labelledby="guides-title" className="relative pt-4">
        <div
          className="pointer-events-none absolute -inset-x-4 -inset-y-8 -z-10 opacity-70 blur-3xl"
          style={{ background: "radial-gradient(ellipse 60% 50% at 50% 40%, rgba(59,130,246,0.10), transparent 70%)" }}
          aria-hidden
        />
        <div className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)] shadow-[0_40px_120px_-40px_rgba(0,0,0,0.9)] sm:rounded-[32px]">
          <div className="pointer-events-none absolute inset-0" aria-hidden>
            <div className="hero-grid absolute inset-0 opacity-70" />
            <div className="wiwi-aurora absolute -left-24 -top-24 h-72 w-72 rounded-full bg-blue-500/20" />
            <div className="wiwi-aurora absolute -right-20 top-10 h-64 w-64 rounded-full bg-fuchsia-500/15" style={{ animationDelay: "-8s" }} />
            <div className="absolute inset-x-0 bottom-0 h-40 bg-gradient-to-t from-[var(--admin-surface)] to-transparent" />
          </div>

          <div className="relative px-6 py-14 text-center sm:px-10 sm:py-20">
            <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/[0.06] px-3.5 py-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-blue-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
              <BookOpen size={12} aria-hidden />
              Integration guides
              <span className="hero-live-dot text-blue-400" aria-hidden />
            </div>

            <h1 id="guides-title" className="mx-auto mt-6 max-w-3xl text-4xl font-bold leading-[1.05] tracking-[-0.035em] text-[var(--admin-text)] sm:text-5xl lg:text-[60px]">
              Connect your tools in <span className="hero-gradient-text">minutes</span>
            </h1>
            <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base">
              Step-by-step tutorials to integrate the gateway with your favorite development tools and workflows.
            </p>

            {/* search */}
            <div className="mx-auto mt-9 max-w-lg">
              <div className="wiwi-conic-border rounded-2xl shadow-[0_20px_60px_-30px_rgba(59,130,246,0.6)]">
                <div className="relative rounded-[15px] bg-[#070707]">
                  <Search size={16} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]" aria-hidden />
                  <input
                    ref={inputRef}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") {
                        setQuery("");
                        e.currentTarget.blur();
                      }
                    }}
                    placeholder="Search guides — Claude Code, Cursor, n8n…"
                    aria-label="Search guides"
                    className="h-12 w-full rounded-[15px] bg-transparent pl-11 pr-14 text-[14px] text-[var(--admin-text)] placeholder:text-[var(--admin-text-dim)] focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/30"
                  />
                  {query ? (
                    <button
                      type="button"
                      onClick={() => {
                        setQuery("");
                        inputRef.current?.focus();
                      }}
                      aria-label="Clear search"
                      className="absolute right-2 top-1/2 inline-flex size-8 -translate-y-1/2 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.05] hover:text-[var(--admin-text)]"
                    >
                      <X size={14} />
                    </button>
                  ) : (
                    <kbd className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 rounded-md border border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 font-mono text-[11px] text-[var(--admin-text-dim)] sm:block" aria-hidden>
                      /
                    </kbd>
                  )}
                </div>
              </div>
            </div>

            {/* stats */}
            <dl className="mx-auto mt-8 flex max-w-lg flex-wrap items-center justify-center gap-2">
              {stats.map(({ icon: Icon, value, label }) => (
                <div key={label} className="inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1.5">
                  <Icon size={12} className="text-blue-400/80" aria-hidden />
                  <dt className="sr-only">{label}</dt>
                  <dd className="font-mono text-[11.5px] text-[var(--admin-text)]">
                    {value} <span className="text-[var(--admin-text-dim)]">{label}</span>
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </section>

      {/* ── how it works ── */}
      {isDefaultView && (
        <section aria-label="How it works" className="space-y-5">
          <SectionLabel>How every guide works</SectionLabel>
          <ol className="relative grid gap-3 sm:grid-cols-3">
            <span aria-hidden className="pointer-events-none absolute left-[16%] right-[16%] top-[38px] hidden h-px bg-gradient-to-r from-blue-400/0 via-blue-400/30 to-fuchsia-400/0 sm:block" />
            {STEPS.map(({ n, title, body, icon: Icon }) => (
              <li
                key={n}
                onPointerMove={trackSpotlight}
                className="group relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5 transition-colors hover:border-white/[0.12]"
              >
                <span className="ent-spotlight pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100" aria-hidden />
                <div className="relative flex items-center gap-3">
                  <span className="flex size-9 items-center justify-center rounded-full border border-white/[0.08] bg-gradient-to-b from-[#121212] to-[#0a0a0a] text-[var(--admin-text-dim)] shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] transition-[color,border-color,box-shadow] duration-300 group-hover:border-blue-400/40 group-hover:text-blue-300 group-hover:shadow-[0_0_18px_-2px_rgba(59,130,246,0.55)]">
                    <Icon size={15} aria-hidden />
                  </span>
                  <span className="font-mono text-[11px] font-semibold tracking-[0.12em] text-blue-400/80">{n}</span>
                </div>
                <h3 className="relative mt-4 text-[14px] font-semibold text-[var(--admin-text)]">{title}</h3>
                <p className="relative mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{body}</p>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* ── featured ── */}
      {isDefaultView && (
        <section aria-label="Popular guides" className="space-y-5">
          <SectionLabel>Most popular</SectionLabel>
          <div className="grid gap-5 md:grid-cols-3">
            {featured.map((g, i) => (
              <FeaturedCard key={g.slug} guide={g} rank={i + 1} />
            ))}
          </div>
        </section>
      )}

      {/* ── all guides ── */}
      <section aria-label="All guides" className="space-y-5">
        <SectionLabel
          right={
            <span className="font-mono text-[11px] text-[var(--admin-text-dim)]" aria-live="polite">
              {filtered.length} / {GUIDES.length}
            </span>
          }
        >
          {isDefaultView ? "All guides" : `${filtered.length} result${filtered.length === 1 ? "" : "s"}`}
        </SectionLabel>

        <div
          role="tablist"
          aria-label="Filter by category"
          className="flex gap-1 overflow-x-auto rounded-2xl border border-[var(--admin-border)] bg-white/[0.015] p-1 sm:inline-flex sm:flex-wrap"
        >
          {CATEGORIES.map((c) => {
            const active = c === category;
            const Icon = c === "All" ? Layers : CATEGORY_META[c].icon;
            return (
              <button
                key={c}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setCategory(c)}
                className={`inline-flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-[12.5px] font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60 ${
                  active
                    ? "bg-gradient-to-b from-blue-500/20 to-blue-500/[0.06] text-blue-200 shadow-[inset_0_0_0_1px_rgba(96,165,250,0.35),0_6px_20px_-10px_rgba(59,130,246,0.7)]"
                    : "text-[var(--admin-text-muted)] hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
                }`}
              >
                <Icon size={13} aria-hidden className={active ? "text-blue-300" : "opacity-70"} />
                {c}
                <span className={`rounded-md px-1.5 font-mono text-[10.5px] ${active ? "bg-blue-400/15 text-blue-200" : "bg-white/[0.04] text-[var(--admin-text-dim)]"}`}>
                  {counts[c] ?? 0}
                </span>
              </button>
            );
          })}
        </div>

        {filtered.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((g) => (
              <GuideCard key={g.slug} guide={g} />
            ))}
          </div>
        ) : (
          <div className="relative overflow-hidden rounded-2xl border border-dashed border-white/[0.1] px-6 py-16 text-center">
            <div className="hero-grid pointer-events-none absolute inset-0 opacity-40" aria-hidden />
            <div className="relative mx-auto flex size-12 items-center justify-center rounded-xl border border-white/[0.08] bg-white/[0.03]">
              <Search size={20} className="text-[var(--admin-text-dim)]" aria-hidden />
            </div>
            <p className="relative mt-4 text-[15px] font-medium text-[var(--admin-text)]">No guides match your filters</p>
            <p className="relative mt-1 text-[13px] text-[var(--admin-text-muted)]">
              {query.trim() ? `Nothing for “${query.trim()}”${category !== "All" ? ` in ${category}` : ""}.` : `No ${category} guides yet.`}
            </p>
            <button
              type="button"
              onClick={reset}
              className="relative mt-5 inline-flex h-9 items-center gap-1.5 rounded-full border border-white/[0.09] bg-white/[0.02] px-4 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.18] hover:bg-white/[0.05]"
            >
              <X size={13} aria-hidden />
              Clear filters
            </button>
          </div>
        )}
      </section>

      {/* ── CTA ── */}
      <section aria-labelledby="guides-cta" className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-6 py-12 text-center sm:rounded-[32px] sm:px-10 sm:py-16">
        <div className="pointer-events-none absolute inset-0" aria-hidden>
          <div className="hero-grid absolute inset-0 opacity-50" />
          <div className="absolute inset-x-0 bottom-0 h-[260px]" style={{ background: "radial-gradient(ellipse 70% 100% at 50% 120%, rgba(59,130,246,0.16), transparent 70%)" }} />
          <div className="wiwi-top-highlight absolute inset-x-0 top-0 h-px opacity-60" />
        </div>
        <div className="relative">
          <div className="mx-auto flex size-12 items-center justify-center rounded-2xl border border-blue-400/25 bg-gradient-to-b from-blue-400/20 to-blue-400/0 text-blue-300 shadow-[0_0_30px_-6px_rgba(59,130,246,0.6)]">
            <Sparkles size={20} aria-hidden />
          </div>
          <h2 id="guides-cta" className="mt-5 text-2xl font-bold tracking-[-0.03em] text-[var(--admin-text)] sm:text-3xl">
            Don't see your tool?
          </h2>
          <p className="mx-auto mt-3 max-w-md text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Anything that speaks the OpenAI or Anthropic API works with the gateway. Browse the docs or tell us what to cover next.
          </p>
          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <span className="relative isolate inline-flex w-full sm:w-auto">
              <span className="hero-cta-ring" aria-hidden />
              <Link
                to="/docs"
                className="wiwi-shimmer group inline-flex h-11 w-full items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-6 text-[14px] font-semibold text-white shadow-lg shadow-brand-600/25 transition-[transform,filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 active:scale-[0.98] sm:w-auto"
              >
                Read the docs
                <ArrowRight size={15} className="transition-transform duration-150 group-hover:translate-x-0.5" aria-hidden />
              </Link>
            </span>
            <Link
              to="/contact"
              className="inline-flex h-11 w-full items-center justify-center rounded-full border border-white/[0.09] bg-white/[0.02] px-6 text-[14px] font-medium text-[var(--admin-text)] backdrop-blur transition-colors hover:border-white/[0.18] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
            >
              Request a guide
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
