// Docs hub — the front door of the documentation. Searchable index over the
// real /docs markdown files (see components/docs/content.ts), grouped by
// category, with the animated routing diagram and a CTA strip. Individual
// pages live at /docs/:slug (DocArticle.tsx).

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Search,
  SearchX,
  Terminal,
  Zap,
} from "lucide-react";
import { DOC_CATEGORIES, DOCS, searchDocs, type DocMeta } from "@/components/docs/content";
import { DocsFlowDiagram } from "@/components/docs/FlowDiagram";

// ── search helpers ─────────────────────────────────────────────────────────

function isTypingTarget(t: EventTarget | null): boolean {
  return t instanceof HTMLElement && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
}

// ── doc card ───────────────────────────────────────────────────────────────

function DocCard(props: { doc: DocMeta; toneIndex: number }) {
  const { doc, toneIndex } = props;
  const Icon = doc.icon;
  return (
    <Link
      to={`/docs/${doc.slug}`}
      className="doc-card admin-card docs-spotlight group flex min-h-[44px] flex-col p-4 transition-all hover:-translate-y-px hover:border-[var(--admin-border-hover)] hover:shadow-lg hover:shadow-black/20"
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
        e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
      }}
    >
      <div className="relative z-10 flex items-start justify-between gap-2">
        <span className={`doc-card-icon tone-${toneIndex % 6}`}>
          <Icon className="h-3.5 w-3.5" />
        </span>
        <ArrowUpRight
          size={14}
          className="shrink-0 text-[var(--admin-text-dim)] opacity-0 transition-all group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-blue-300 group-hover:opacity-100"
        />
      </div>
      <h3 className="relative z-10 mt-2.5 text-[13.5px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
        {doc.title}
      </h3>
      <p className="relative z-10 mt-1 line-clamp-2 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
        {doc.description}
      </p>
      <span className="doc-card-read relative z-10 mt-3">
        Read guide
        <ArrowRight
          size={11}
          className="transition-transform duration-150 group-hover:translate-x-0.5"
        />
      </span>
    </Link>
  );
}

// ── page ───────────────────────────────────────────────────────────────────

export function DocsPage() {
  const [q, setQ] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // "/" focuses search from anywhere on the page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && !isTypingTarget(e.target)) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const query = q.trim();
  const results = useMemo(() => (query ? searchDocs(query) : null), [query]);

  return (
    <div className="mx-auto max-w-6xl pb-4">
      {/* ── hero ── */}
      <section className="docs-hero relative mb-12 overflow-hidden rounded-2xl border border-[var(--admin-border)] px-6 py-12 sm:px-10 sm:py-14">
        <div className="docs-hero-glow" aria-hidden />
        <div className="docs-hero-aurora" aria-hidden />
        <div className="relative z-10 mx-auto max-w-3xl text-center">
          <div className="mb-4 flex flex-wrap items-center justify-center gap-2">
            <span className="admin-badge admin-badge-blue inline-flex items-center gap-1.5">
              <BookOpen size={11} /> Documentation
            </span>
            <span className="admin-badge admin-badge-gray inline-flex items-center gap-1.5">
              {DOCS.length} guides
            </span>
          </div>
          <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
            wiwi{" "}
            <span className="docs-gradient-text bg-gradient-to-r from-blue-400 via-fuchsia-400 to-blue-400 bg-clip-text text-transparent">
              Documentation
            </span>
          </h1>
          <p className="mx-auto mt-3 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            One gateway, every dialect. Learn how requests flow from any client through the
            canonical IR to eleven providers — and how to configure, secure, and observe it all.
          </p>

          {/* search */}
          <div className="doc-search">
            <Search size={16} className="doc-search-icon" aria-hidden />
            <input
              ref={inputRef}
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setQ("");
                  inputRef.current?.blur();
                }
              }}
              placeholder="Search the docs…"
              aria-label="Search the docs"
            />
            <kbd className="doc-kbd" aria-hidden>/</kbd>
          </div>

          <div className="mt-7 flex flex-wrap items-center justify-center gap-x-5 gap-y-1.5 text-[11px] text-[var(--admin-text-dim)]">
            {["3 inbound dialects", "11 provider adapters", "1 canonical IR", "SSE streaming"].map(
              (s, i) => (
                <span key={s} className="inline-flex items-center gap-5">
                  {i > 0 && <span className="h-1 w-1 rounded-full bg-white/20" aria-hidden />}
                  {s}
                </span>
              ),
            )}
          </div>
        </div>
      </section>

      {/* ── search results ── */}
      {results ? (
        <section className="mb-12" aria-live="polite">
          <h2 className="mb-4 text-[13px] font-semibold uppercase tracking-wider text-[var(--admin-text-dim)]">
            {results.length > 0
              ? `${results.length} result${results.length === 1 ? "" : "s"} for “${query}”`
              : `No results for “${query}”`}
          </h2>
          {results.length === 0 ? (
            <div className="admin-card flex flex-col items-center gap-3 p-10 text-center">
              <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white/[0.03] text-[var(--admin-text-dim)]">
                <SearchX size={18} />
              </span>
              <p className="text-[13px] text-[var(--admin-text-muted)]">
                Nothing matched. Try “streaming”, “keys”, “yaml”, or “failover”.
              </p>
              <button
                type="button"
                onClick={() => setQ("")}
                className="admin-btn admin-btn-ghost mt-1"
              >
                Clear search
              </button>
            </div>
          ) : (
            <div className="grid gap-2.5">
              {results.map((d) => {
                const Icon = d.icon;
                return (
                  <Link
                    key={d.slug}
                    to={`/docs/${d.slug}`}
                    className="admin-card group flex min-h-[44px] items-center gap-3.5 p-3.5 transition-all hover:-translate-y-px hover:border-[var(--admin-border-hover)] hover:shadow-lg hover:shadow-black/20"
                  >
                    <span className="doc-card-icon tone-0 shrink-0">
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="text-[13.5px] font-semibold text-[var(--admin-text)]">
                          {d.title}
                        </span>
                        <span className="admin-badge admin-badge-gray">{d.category}</span>
                      </span>
                      <span className="mt-0.5 block truncate text-[12px] text-[var(--admin-text-muted)]">
                        {d.description}
                      </span>
                    </span>
                    <ArrowRight
                      size={14}
                      className="shrink-0 text-[var(--admin-text-dim)] transition-all group-hover:translate-x-0.5 group-hover:text-blue-300"
                    />
                  </Link>
                );
              })}
            </div>
          )}
        </section>
      ) : (
        <>
          {/* ── categorized guides ── */}
          {DOC_CATEGORIES.map((cat, ci) => (
            <section key={cat.name} className="mb-11">
              <div className="mb-4 flex items-center gap-3">
                <span className={`doc-card-icon tone-${ci % 6} h-8 w-8`}>
                  <cat.icon className="h-4 w-4" />
                </span>
                <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                  {cat.name}
                </h2>
                <span className="admin-badge admin-badge-gray ml-0.5">{cat.docs.length}</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {cat.docs.map((d) => (
                  <DocCard key={d.slug} doc={d} toneIndex={ci} />
                ))}
              </div>
            </section>
          ))}

          {/* ── how it works ── */}
          <section className="mb-12">
            <div className="mb-1 flex items-center gap-3">
              <span className="doc-card-icon tone-1 h-8 w-8">
                <Zap className="h-4 w-4" />
              </span>
              <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                How wiwi routes a request
              </h2>
            </div>
            <p className="mb-2 max-w-2xl text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
              Every guide below is grounded in the same core loop: inbound wire codec → canonical
              IR → provider adapter, and back.
            </p>
            <DocsFlowDiagram />
          </section>

          {/* ── CTA ── */}
          <section className="docs-cta rounded-2xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent p-8 text-center">
            <h2 className="text-xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
              Ready to try it?
            </h2>
            <p className="mx-auto mt-2 max-w-md text-[14px] text-[var(--admin-text-muted)]">
              Spin up a gateway in a minute, or jump straight into the playground.
            </p>
            <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
              <Link
                to="/playground"
                className="inline-flex h-10 items-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110"
              >
                <Terminal size={14} /> Open playground
              </Link>
              <Link
                to="/docs/quickstart"
                className="inline-flex h-10 items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
              >
                Start with Quickstart <ArrowRight size={13} />
              </Link>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
