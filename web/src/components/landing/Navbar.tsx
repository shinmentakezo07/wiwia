// Navbar — floating glass pill navigation bar with animated mega-dropdowns, a
// polished mobile panel, and auth controls. Matches the wiwi dark console
// language: --admin-* tokens, hairline white borders, blue/violet accents.

import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/api/auth";
import {
  ArrowRight,
  Blocks,
  BookOpen,
  Bot,
  Boxes,
  Building2,
  Calculator,
  ChevronDown,
  GitCompare,
  LayoutGrid,
  Menu,
  MessagesSquare,
  Network,
  Newspaper,
  ScrollText,
  ShieldCheck,
  Sparkles,
  X,
  Zap,
} from "lucide-react";

const GITHUB_URL = "https://github.com/shinmentakezo07/wiwia";

// ── dropdown link data ────────────────────────────────────────────────────

interface NavLink {
  title: string;
  href: string;
  description: string;
  icon: React.ElementType;
  gradient: string;
  external?: boolean;
}

const productsLinks: NavLink[] = [
  {
    title: "AI Gateway",
    href: "/products",
    description: "Route every inbound dialect to thirteen provider types through one endpoint.",
    icon: Network,
    gradient: "hover:from-violet-500/20 hover:to-purple-600/30 group-hover/product:text-violet-400",
  },
  {
    title: "Playground",
    href: "/playground",
    description: "Chat with the models configured on this gateway, with full usage logging.",
    icon: MessagesSquare,
    gradient: "hover:from-blue-500/20 hover:to-cyan-600/30 group-hover/product:text-blue-400",
  },
  {
    title: "Open Source",
    href: "/open-source",
    description: "The MIT-licensed source, the Docker quickstart, and the self-hosting story.",
    icon: Boxes,
    gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400",
  },
  {
    title: "Enterprise",
    href: "/enterprise",
    description: "Deployment help, custom adapters, and priority triage for teams.",
    icon: Building2,
    gradient: "hover:from-amber-500/20 hover:to-orange-600/30 group-hover/product:text-amber-400",
  },
];

const resourcesLinks: NavLink[] = [
  { title: "Documentation", href: "/docs", description: "Quickstart, endpoints, configuration, and streaming.", icon: BookOpen, gradient: "hover:from-blue-500/20 hover:to-indigo-600/30 group-hover/product:text-blue-400" },
  { title: "Models", href: "/models", description: "The public model catalog served by this gateway.", icon: Boxes, gradient: "hover:from-purple-500/20 hover:to-fuchsia-600/30 group-hover/product:text-purple-400" },
  { title: "Integrations", href: "/integrations", description: "Clients and tools that work with the gateway.", icon: Blocks, gradient: "hover:from-indigo-500/20 hover:to-blue-600/30 group-hover/product:text-indigo-400" },
  { title: "Guides", href: "/guides", description: "Step-by-step setup for Claude Code, Cursor, Cline, and more.", icon: BookOpen, gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400" },
  { title: "Migration", href: "/migration", description: "Move from OpenRouter, LiteLLM, Portkey, or another gateway.", icon: GitCompare, gradient: "hover:from-cyan-500/20 hover:to-blue-600/30 group-hover/product:text-cyan-400" },
  { title: "Reliability", href: "/reliability", description: "Retries, cooldowns, fallbacks, and the optional health healer.", icon: ShieldCheck, gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400" },
  { title: "Compare", href: "/compare", description: "How the gateway differs from OpenRouter, LiteLLM, and Portkey.", icon: GitCompare, gradient: "hover:from-sky-500/20 hover:to-blue-600/30 group-hover/product:text-sky-400" },
  { title: "Changelog", href: "/changelog", description: "What changed across releases.", icon: ScrollText, gradient: "hover:from-violet-500/20 hover:to-purple-600/30 group-hover/product:text-violet-400" },
  { title: "Blog", href: "/blog", description: "Guides, benchmarks, and notes from the project.", icon: Newspaper, gradient: "hover:from-amber-500/20 hover:to-orange-600/30 group-hover/product:text-amber-400" },
  { title: "Token Cost Calculator", href: "/token-cost-calculator", description: "Estimate spend for a model and monthly volume.", icon: Calculator, gradient: "hover:from-green-500/20 hover:to-emerald-600/30 group-hover/product:text-green-400" },
];

const aiLinks: NavLink[] = [
  { title: "Claude Code", href: "/guides/claude-code", description: "Anthropic Messages in, any provider out.", icon: Bot, gradient: "hover:from-violet-500/20 hover:to-purple-600/30 group-hover/product:text-violet-400" },
  { title: "Codex CLI", href: "/guides/codex-cli", description: "OpenAI Responses in, any provider out.", icon: Zap, gradient: "hover:from-amber-500/20 hover:to-orange-600/30 group-hover/product:text-amber-400" },
  { title: "OpenCode", href: "/guides/opencode", description: "Point the OpenCode CLI at the gateway.", icon: Network, gradient: "hover:from-cyan-500/20 hover:to-blue-600/30 group-hover/product:text-cyan-400" },
  { title: "Cline", href: "/guides/cline", description: "OpenAI-compatible provider setup in VS Code.", icon: Blocks, gradient: "hover:from-indigo-500/20 hover:to-blue-600/30 group-hover/product:text-indigo-400" },
  { title: "Cursor", href: "/guides/cursor", description: "Override the OpenAI base URL in Cursor.", icon: LayoutGrid, gradient: "hover:from-pink-500/20 hover:to-rose-600/30 group-hover/product:text-pink-400" },
  { title: "GitHub Copilot", href: "/guides/github-copilot", description: "Use the gateway as a BYOK provider in the Copilot app.", icon: Sparkles, gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400" },
];

const mobileSections = [
  { label: "Products", items: productsLinks },
  { label: "Resources", items: resourcesLinks },
  { label: "AI", items: aiLinks },
];

// ── shared class helpers ──────────────────────────────────────────────────

const DESKTOP_LINK_BASE =
  "block whitespace-nowrap rounded-lg px-3 py-2 text-sm text-[var(--admin-text-muted)] transition-colors duration-200 hover:bg-white/[0.05] hover:text-[var(--admin-text)]";

function directLinkClass(active: boolean) {
  return active
    ? "block whitespace-nowrap rounded-lg bg-blue-500/[0.08] px-3 py-2 text-sm text-blue-300"
    : DESKTOP_LINK_BASE;
}

const SOCIAL_ICON_BTN =
  "flex size-9 items-center justify-center rounded-full border border-white/[0.06] bg-white/[0.02] text-[var(--admin-text-muted)] transition-all duration-200 hover:border-white/[0.14] hover:bg-white/[0.06] hover:text-[var(--admin-text)]";

const GITHUB_ICON = (
  <svg className="size-[18px]" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
  </svg>
);

// Primary CTA — premium gradient pill with animated shimmer sweep, colored
// glow, inset top highlight, and press feedback. Mirrors the hero CTA language
// (wiwi-shimmer + brand gradient) so the nav reads as one system.
const GRADIENT_CTA =
  "wiwi-shimmer group inline-flex items-center justify-center gap-1.5 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-brand-600/30 transition-[transform,filter] duration-150 hover:brightness-110 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/60";

const OUTLINE_PILL =
  "rounded-xl border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-sm font-medium whitespace-nowrap text-[var(--admin-text)] transition-all duration-200 hover:border-white/[0.16] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20";

// ── dropdown sub-component ────────────────────────────────────────────────

function DropdownMenu({ label, links, cols }: { label: string; links: NavLink[]; cols: string }) {
  const [open, setOpen] = useState(false);

  // Escape closes even when focus never entered the panel.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="relative" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex items-center gap-1 rounded-lg px-3 py-2 text-sm text-[var(--admin-text-muted)] transition-colors duration-200 hover:bg-white/[0.05] hover:text-[var(--admin-text)]"
      >
        {label}
        <ChevronDown className={`size-3.5 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {/* Panel stays mounted so open/close fades+scales instead of popping */}
      <div
        className={`absolute left-1/2 top-full z-50 -translate-x-1/2 pt-2 transition-all duration-200 ease-out ${
          open
            ? "visible translate-y-0 scale-100 opacity-100"
            : "invisible pointer-events-none translate-y-1.5 scale-[0.97] opacity-0"
        }`}
      >
        <div
          className={`grid gap-1 overflow-hidden rounded-2xl border border-white/[0.07] bg-[var(--admin-surface)]/95 p-2 shadow-[0_24px_70px_-16px_rgba(0,0,0,0.85)] backdrop-blur-xl ${cols}`}
        >
          {links.map((link) => {
            const IconComp = link.icon;
            const iconColor = link.gradient.split(" ").slice(-1).join(" ");
            const tileClasses = `group/product flex items-start gap-3 rounded-xl border border-transparent bg-linear-to-br from-transparent to-transparent p-2.5 no-underline outline-none transition-all duration-200 ${link.gradient}`;
            const body = (
              <>
                <div className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-white/[0.05] bg-white/[0.04]">
                  <IconComp className={`size-4 text-[var(--admin-text-muted)] transition-colors ${iconColor}`} />
                </div>
                <div className="space-y-0.5">
                  <div className="text-sm font-medium leading-none text-[var(--admin-text)]">{link.title}</div>
                  <p className="line-clamp-2 text-xs leading-snug text-[var(--admin-text-muted)]">{link.description}</p>
                </div>
              </>
            );
            return link.external ? (
              <a key={link.title} href={link.href} target="_blank" rel="noopener noreferrer" className={tileClasses}>
                {body}
              </a>
            ) : (
              <Link key={link.title} to={link.href} className={tileClasses}>
                {body}
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── main navbar ──────────────────────────────────────────────────────────

export function Navbar({ sticky = true }: { sticky?: boolean }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [menuState, setMenuState] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);
  const [openMobileSection, setOpenMobileSection] = useState<string | null>(null);
  const { user } = useAuth();
  const isAuthenticated = !!user;

  // highlight the section the user is currently on
  const isActivePath = (href: string) =>
    location.pathname === href || location.pathname.startsWith(`${href}/`);

  // close the mobile panel whenever the route changes
  useEffect(() => {
    setMenuState(false);
    setOpenMobileSection(null);
  }, [location.pathname]);

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 24);
    window.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <header className={`z-40 w-full px-2 pt-2 sm:px-3 ${sticky ? "sticky top-0" : ""}`}>
      <nav className="mx-auto max-w-[1400px]" aria-label="Main">
        {/* Floating glass pill — hairline border + blur that deepens on scroll */}
        <div
          className={`relative overflow-hidden rounded-2xl border backdrop-blur-xl transition-all duration-300 ${
            isScrolled
              ? "border-white/[0.08] bg-[var(--admin-bg)]/70 shadow-[0_12px_40px_-12px_rgba(0,0,0,0.75)]"
              : "border-white/[0.04] bg-[var(--admin-bg)]/30"
          }`}
        >
          {/* specular top highlight line */}
          <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70" />

          <div className="relative flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-2.5 lg:flex-nowrap lg:px-5 lg:py-3">
            {/* Logo */}
            <div className="flex w-full items-center justify-between gap-3 lg:w-auto">
              <Link to="/" className="group flex items-center gap-2.5 no-underline outline-none">
                <span className="relative rounded-full ring-1 ring-inset ring-white/[0.12] transition-shadow duration-300 group-hover:shadow-[0_0_18px_rgba(116,66,237,0.4)]">
                  <img src="/wiwi-logo.png" alt="" className="block size-9 rounded-full object-cover" />
                </span>
                <span className="whitespace-nowrap text-xl font-bold tracking-tight text-[var(--admin-text)]">
                  wiwi
                </span>
              </Link>

              {/* Mobile toggle */}
              <button
                type="button"
                onClick={() => setMenuState(!menuState)}
                aria-expanded={menuState}
                aria-controls="mobile-nav"
                aria-label={menuState ? "Close Menu" : "Open Menu"}
                className="flex size-11 cursor-pointer items-center justify-center rounded-lg text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.05] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 lg:hidden"
              >
                {menuState ? <X className="size-6" /> : <Menu className="size-6" />}
              </button>
            </div>

            {/* Desktop center nav */}
            <div className="hidden min-w-0 items-center gap-1 lg:flex">
              <div className="flex gap-0.5 text-sm">
                <Link
                  to="/playground"
                  className={`${directLinkClass(isActivePath("/playground"))} hidden min-[1360px]:block`}
                >
                  Playground
                </Link>
                <Link to="/models" className={directLinkClass(isActivePath("/models"))}>
                  Models
                </Link>

                <DropdownMenu label="Products" links={productsLinks} cols="grid-cols-2 md:w-[520px] lg:w-[560px]" />
                <DropdownMenu
                  label="Resources"
                  links={resourcesLinks}
                  cols="grid-cols-2 md:w-[680px] lg:w-[800px] lg:grid-cols-3"
                />
                <DropdownMenu label="AI" links={aiLinks} cols="grid-cols-2 md:w-[520px] lg:w-[560px]" />

<Link to="/docs" className={directLinkClass(isActivePath("/docs"))}>
  Docs
</Link>
                <Link to="/pricing" className={directLinkClass(isActivePath("/pricing"))}>
                  Pricing
                </Link>
              </div>
            </div>

            {/* Right side controls */}
            <div className="hidden shrink-0 items-center gap-2 lg:flex">
              <div className="mr-1 hidden items-center gap-1.5 min-[1280px]:flex">
                <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" className={SOCIAL_ICON_BTN} aria-label="GitHub">
                  {GITHUB_ICON}
                </a>
              </div>

              {isAuthenticated ? (
                <button type="button" onClick={() => navigate("/console")} className={GRADIENT_CTA}>
                  Dashboard
                  <ArrowRight size={15} className="transition-transform duration-150 group-hover:translate-x-0.5" />
                </button>
              ) : (
                <>
                  <Link to="/login" className={OUTLINE_PILL}>
                    Log In
                  </Link>
                  <button type="button" onClick={() => navigate("/signup")} className={GRADIENT_CTA}>
                    Get Started
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Mobile panel */}
          {menuState && (
            <div id="mobile-nav" className="wiwi-enter border-t border-white/[0.06] px-4 pb-5 pt-3 lg:hidden">
              <ul className="space-y-0.5 text-[15px]">
                <li><Link to="/playground" className={directLinkClass(isActivePath("/playground"))}>Playground</Link></li>
                <li><Link to="/models" className={directLinkClass(isActivePath("/models"))}>Models</Link></li>
                <li>
<Link to="/docs" className={directLinkClass(isActivePath("/docs"))}>
  Docs
</Link>
                </li>
                <li><Link to="/pricing" className={directLinkClass(false)}>Pricing</Link></li>
              </ul>

              {mobileSections.map((section) => (
                <div key={section.label}>
                  <button
                    type="button"
                    onClick={() => setOpenMobileSection(openMobileSection === section.label ? null : section.label)}
                    aria-expanded={openMobileSection === section.label}
                    className="flex w-full items-center justify-between gap-2 rounded-lg py-2.5 pl-3 pr-2 text-left text-[15px] text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
                  >
                    {section.label}
                    <ChevronDown
                      className={`size-4 transition-transform duration-200 ${openMobileSection === section.label ? "rotate-180" : ""}`}
                    />
                  </button>
                  <div
                    className={`grid pl-3 transition-all duration-300 ease-out ${
                      openMobileSection === section.label ? "mb-1 grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                    }`}
                  >
                    <ul className="grid min-h-0 grid-cols-2 gap-x-3 overflow-hidden">
                      {section.items.map((item) => {
                        const ItemIcon = item.icon;
                        return (
                          <li key={item.title}>
                            {item.external ? (
                              <a
                                href={item.href}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-2 whitespace-nowrap rounded-lg py-2 pl-3 text-sm text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
                              >
                                <ItemIcon className="size-3.5 text-[var(--admin-text-dim)]" />
                                {item.title}
                              </a>
                            ) : (
                              <Link
                                to={item.href}
                                className="flex items-center gap-2 whitespace-nowrap rounded-lg py-2 pl-3 text-sm text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
                              >
                                <ItemIcon className="size-3.5 text-[var(--admin-text-dim)]" />
                                {item.title}
                              </Link>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                </div>
              ))}

              <div className="mt-2 flex items-center gap-2 border-t border-white/[0.06] pt-4">
                <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" className={SOCIAL_ICON_BTN} aria-label="GitHub">
                  {GITHUB_ICON}
                </a>
              </div>

              <div className="mt-4 flex flex-col gap-2.5">
                {isAuthenticated ? (
                  <button type="button" onClick={() => navigate("/console")} className={`${GRADIENT_CTA} w-full py-2.5`}>
                    Dashboard
                    <ArrowRight size={15} className="transition-transform duration-150 group-hover:translate-x-0.5" />
                  </button>
                ) : (
                  <>
                    <Link to="/login" className={`${OUTLINE_PILL} block w-full py-2.5 text-center`}>
                      Log In
                    </Link>
                    <button
                      type="button"
                      onClick={() => navigate("/signup")}
                      className={`${GRADIENT_CTA} w-full py-2.5`}
                    >
                      Get Started
                    </button>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      </nav>
    </header>
  );
}
