// DocArticle — /docs/:slug renders one markdown file from /docs with a
// full reading layout: docs-wide sidebar nav, breadcrumb + header, anchored
// markdown body, "on this page" TOC (xl+), mobile browse drawer, and a
// prev/next pager following the registry's reading order.

import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Clock3,
  FileQuestion,
} from "lucide-react";
import { getDoc, getNeighbors, docHrefToSlug, DOC_CATEGORIES, type DocMeta } from "@/components/docs/content";
import { MarkdownDoc } from "@/components/docs/markdown";
import { parseToc, stripLeadingH1 } from "@/components/docs/markdown-utils";

// ── scroll-spy for the TOC ─────────────────────────────────────────────────

function useScrollSpy(ids: string[], resetKey: string) {
  const [active, setActive] = useState(ids[0] ?? "");
  useEffect(() => {
    setActive(ids[0] ?? "");
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(entry.target.id);
        }
      },
      { rootMargin: "-96px 0px -70% 0px", threshold: 0 },
    );
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [ids, resetKey]);
  return active;
}

function smoothTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ── pager ──────────────────────────────────────────────────────────────────

function Pager(props: { prev?: DocMeta; next?: DocMeta }) {
  const { prev, next } = props;
  return (
    <nav className="doc-pager mt-14 grid gap-3 sm:grid-cols-2" aria-label="Docs pagination">
      {prev ? (
        <Link to={`/docs/${prev.slug}`} className="doc-pager-card group min-h-[44px]">
          <span className="doc-pager-label">
            <ArrowLeft size={12} className="transition-transform group-hover:-translate-x-0.5" />
            Previous
          </span>
          <span className="doc-pager-title">{prev.title}</span>
          <span className="doc-pager-desc">{prev.description}</span>
        </Link>
      ) : (
        <span aria-hidden className="hidden sm:block" />
      )}
      {next ? (
        <Link to={`/docs/${next.slug}`} className="doc-pager-card is-next group min-h-[44px]">
          <span className="doc-pager-label">
            Next
            <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" />
          </span>
          <span className="doc-pager-title">{next.title}</span>
          <span className="doc-pager-desc">{next.description}</span>
        </Link>
      ) : null}
    </nav>
  );
}

// ── not found ──────────────────────────────────────────────────────────────

function DocNotFound(props: { slug: string }) {
  const { slug } = props;
  return (
    <div className="mx-auto max-w-2xl py-16 text-center">
      <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-white/[0.03] text-[var(--admin-text-dim)]">
        <FileQuestion size={20} />
      </span>
      <h1 className="mt-4 text-xl font-semibold text-[var(--admin-text)]">Page not found</h1>
      <p className="mx-auto mt-2 max-w-md text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
        There is no docs page at <code className="mdx-code">{`/docs/${slug}`}</code>. It may have
        moved — pick a guide from the index instead.
      </p>
      <Link
        to="/docs"
        className="mt-6 inline-flex h-10 items-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
      >
        <ArrowLeft size={14} /> Back to all docs
      </Link>
    </div>
  );
}

// ── page ───────────────────────────────────────────────────────────────────

export function DocArticlePage() {
  const { slug = "" } = useParams<{ slug: string }>();
  const doc = getDoc(slug) ?? null;

  const bodyMd = useMemo(() => (doc ? stripLeadingH1(doc.md) : ""), [doc]);
  const toc = useMemo(() => (doc ? parseToc(bodyMd) : []), [doc, bodyMd]);
  const tocIds = useMemo(() => toc.map((t) => t.id), [toc]);
  const active = useScrollSpy(tocIds, slug);
  const { prev, next } = getNeighbors(slug);

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [slug]);

  if (!doc) return <DocNotFound slug={slug} />;

  const words = bodyMd.split(/\s+/).filter(Boolean).length;
  const minutes = Math.max(1, Math.round(words / 220));
  const resolveHref = (href: string) => docHrefToSlug(href);

  const renderNav = (onNavigate?: () => void) => (
    <>
      {DOC_CATEGORIES.map((cat) => (
        <div key={cat.name} className="mt-4 first:mt-0">
          <div className="doc-rail-cat">{cat.name}</div>
          {cat.docs.map((d) => {
            const isActive = d.slug === doc.slug;
            return (
              <Link
                key={d.slug}
                to={`/docs/${d.slug}`}
                onClick={onNavigate}
                aria-current={isActive ? "page" : undefined}
                className={`docs-nav-item flex min-h-[32px] items-center rounded-lg px-3 py-1.5 text-left text-[12px] transition-colors ${
                  isActive
                    ? "is-active bg-blue-500/[0.06] font-medium text-blue-200"
                    : "text-[var(--admin-text-dim)] hover:bg-white/[0.02] hover:text-[var(--admin-text-muted)]"
                }`}
              >
                {d.title}
              </Link>
            );
          })}
        </div>
      ))}
    </>
  );

  return (
    <div className="doc-shell mx-auto max-w-[1200px] pb-4">
      {/* ── left rail: docs-wide nav ── */}
      <aside className="doc-rail hidden lg:block">
        <nav className="sticky top-[84px] max-h-[calc(100vh-104px)] overflow-y-auto pr-1" aria-label="Documentation">
          <Link
            to="/docs"
            className="mb-3 inline-flex min-h-[32px] items-center gap-1.5 text-[12px] font-medium text-[var(--admin-text-muted)] transition-colors hover:text-blue-300"
          >
            <ArrowLeft size={13} /> All docs
          </Link>
          {renderNav()}
        </nav>
      </aside>

      {/* ── article ── */}
      <article className="min-w-0">
        {/* mobile / tablet browse drawer */}
        <details className="doc-mobile-nav lg:hidden">
          <summary>
            <BookOpen size={14} className="text-blue-300" />
            Browse docs — {doc.title}
            <ChevronDown size={14} className="chev text-[var(--admin-text-dim)]" />
          </summary>
          <div className="doc-mobile-nav-body">{renderNav()}</div>
        </details>

        <nav className="doc-crumbs" aria-label="Breadcrumb">
          <Link to="/docs">Docs</Link>
          <ChevronRight size={11} aria-hidden />
          <span>{doc.category}</span>
        </nav>

        <header className="mb-8 border-b border-[var(--admin-border)] pb-7">
          <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.02em] text-[var(--admin-text)] sm:text-[30px]">
            {doc.title}
          </h1>
          <p className="mt-2.5 max-w-2xl text-[14.5px] leading-relaxed text-[var(--admin-text-muted)]">
            {doc.description}
          </p>
          <div className="doc-meta mt-4">
            <span className="admin-badge admin-badge-gray">{doc.category}</span>
            <span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--admin-text-dim)]">
              <Clock3 size={11} /> {minutes} min read · {words.toLocaleString()} words
            </span>
          </div>
        </header>

        <div className="docs-content">
          <MarkdownDoc md={bodyMd} resolveHref={resolveHref} />
        </div>

        <Pager prev={prev} next={next} />

        <div className="doc-article-foot">
          <Link to="/docs" className="doc-foot-link">
            <ArrowLeft size={13} /> Back to all docs
          </Link>
          <span className="h-1 w-1 rounded-full bg-white/15" aria-hidden />
          <Link to="/playground" className="doc-foot-link">
            Open playground <ArrowRight size={13} />
          </Link>
        </div>
      </article>

      {/* ── right rail: on this page ── */}
      {toc.length > 0 && (
        <aside className="doc-toc hidden xl:block">
          <nav className="sticky top-[84px]" aria-label="On this page">
            <div className="doc-toc-label">On this page</div>
            {toc.map((t) => (
              <a
                key={t.id}
                href={`#${t.id}`}
                onClick={(e) => {
                  e.preventDefault();
                  smoothTo(t.id);
                }}
                className={`doc-toc-item depth-${t.depth}${active === t.id ? " is-active" : ""}`}
              >
                {t.text}
              </a>
            ))}
          </nav>
        </aside>
      )}
    </div>
  );
}
