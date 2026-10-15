// Public footer — the closing band of every unauthenticated page (landing,
// models, docs, pricing, enterprise, …). Brand block with the self-host CTA
// and socials on the left, the link columns on the right, and a hairline
// bottom bar with a back-to-top control. Same dark language as the rest of the
// public site: --admin-* tokens, hairline white borders, blue/violet accents.

import { Link, useLocation } from "react-router-dom";
import { ArrowRight, ArrowUp, BookOpen, Github } from "lucide-react";

const GITHUB_URL = "https://github.com/shinmentakezo07/wiwia";
const DISCORD_URL = "https://discord.com";

const FOOTER_COLS: { heading: string; links: { label: string; to: string }[] }[] = [
  {
    heading: "Product",
    links: [
      { label: "Features", to: "/" },
      { label: "Models", to: "/models" },
      { label: "Pricing", to: "/pricing" },
      { label: "Compare", to: "/compare" },
      { label: "Playground", to: "/playground" },
      { label: "Enterprise", to: "/enterprise" },
    ],
  },
  {
    heading: "Resources",
    links: [
      { label: "Documentation", to: "/docs" },
      { label: "Changelog", to: "/changelog" },
      { label: "About", to: "/about" },
      { label: "Contact", to: "/contact" },
      { label: "Legal", to: "/legal" },
    ],
  },
  {
    heading: "Get started",
    links: [
      { label: "Sign up", to: "/signup" },
      { label: "Sign in", to: "/login" },
      { label: "Self-host", to: "/docs" },
    ],
  },
];

// Discord has no lucide mark (the set dropped brand icons), so the path is
// inlined the same way the navbar inlines it.
const DISCORD_PATH =
  "M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z";

// 44px square so the icon controls stay tappable on a phone.
const SOCIAL_BTN =
  "flex size-11 items-center justify-center rounded-full border border-white/[0.06] bg-white/[0.02] text-[var(--admin-text-muted)] transition-all duration-200 hover:border-white/[0.14] hover:bg-white/[0.06] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20";

function BackToTop() {
  return (
    <button
      type="button"
      aria-label="Back to top"
      onClick={() =>
        window.scrollTo({
          top: 0,
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "auto"
            : "smooth",
        })
      }
      className="inline-flex min-h-11 items-center gap-2 rounded-lg px-3 font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/15"
    >
      Back to top
      <ArrowUp size={13} />
    </button>
  );
}

export function PublicFooter() {
  const { pathname } = useLocation();

  return (
    <footer className="relative z-10 overflow-hidden border-t border-[var(--admin-border)]">
      {/* specular top edge + a soft blue wash behind the link columns */}
      <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-40" aria-hidden />
      <div className="pointer-events-none absolute inset-0" aria-hidden>
        <div
          className="absolute -bottom-56 left-1/2 h-[460px] w-[760px] -translate-x-1/2 rounded-full"
          style={{ background: "radial-gradient(ellipse at center, rgba(59,130,246,0.09) 0%, transparent 65%)" }}
        />
      </div>

      <div className="relative mx-auto max-w-[1400px] px-6 py-14">
        <div className="grid gap-10 lg:grid-cols-12 lg:gap-8">
          {/* ── brand ── */}
          <div className="lg:col-span-4">
            <Link to="/" className="group inline-flex min-h-11 items-center gap-2.5 no-underline outline-none">
              <span className="relative rounded-xl ring-1 ring-white/[0.10] ring-inset transition-shadow duration-300 group-hover:shadow-[0_0_20px_rgba(116,66,237,0.4)] group-focus-visible:shadow-[0_0_20px_rgba(116,66,237,0.4)]">
                <img src="/wiwi-logo.png" alt="" className="block size-11 rounded-xl object-cover" />
              </span>
              <span className="flex items-baseline gap-2">
                <span className="text-[15px] font-semibold tracking-tight text-[var(--admin-text)]">wiwi</span>
                <span className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-[var(--admin-text-muted)]">
                  Gateway
                </span>
              </span>
            </Link>

            <p className="mt-4 max-w-xs text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
              Self-hosted unified LLM gateway. One binary, every model behind a single endpoint.
            </p>

            <div className="mt-6 flex flex-wrap items-center gap-2.5">
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="wiwi-shimmer group inline-flex h-11 items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.16] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20"
              >
                <Github size={15} />
                Self-host wiwi
                <ArrowRight size={14} className="transition-transform duration-150 group-hover:translate-x-0.5" />
              </a>
              <Link
                to="/docs"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] px-3 text-[13px] font-medium text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/15"
              >
                <BookOpen size={15} />
                Read the docs
              </Link>
            </div>

            <div className="mt-6 flex items-center gap-2.5">
              <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" className={SOCIAL_BTN} aria-label="GitHub">
                <Github size={17} />
              </a>
              <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" className={SOCIAL_BTN} aria-label="Discord">
                <svg className="size-[17px]" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d={DISCORD_PATH} />
                </svg>
              </a>
            </div>
          </div>

          {/* ── link columns ── */}
          <div className="grid grid-cols-2 gap-x-6 gap-y-9 sm:grid-cols-3 lg:col-span-8 lg:pt-1">
            {FOOTER_COLS.map((col) => (
              <nav key={col.heading} aria-label={col.heading}>
                <h3 className="admin-label mb-3">{col.heading}</h3>
                <ul className="space-y-0.5">
                  {col.links.map((l) => (
                    <li key={l.label}>
                      <Link
                        to={l.to}
                        aria-current={pathname === l.to ? "page" : undefined}
                        className={`flex min-h-11 items-center rounded-lg text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/15 lg:min-h-0 lg:py-1.5 ${
                          pathname === l.to
                            ? "font-medium text-[var(--admin-text)]"
                            : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
                        }`}
                      >
                        {l.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </nav>
            ))}
          </div>
        </div>

        {/* ── bottom bar ── */}
        <div className="mt-12 flex flex-col items-center gap-2 border-t border-[var(--admin-border)] pt-4 sm:flex-row sm:justify-between">
          <p className="text-[11px] text-[var(--admin-text-muted)]">
            wiwi · self-hosted LLM gateway · {new Date().getFullYear()}
          </p>
          <BackToTop />
        </div>
      </div>
    </footer>
  );
}
