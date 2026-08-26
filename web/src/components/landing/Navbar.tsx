// Navbar — full navigation bar with dropdown menus, mobile menu, and auth
// controls. Converted from the llmgateway reference: next/link → react-router-dom
// Link, usePostHog/useSessionStatus/useAppConfig → removed, radix
// NavigationMenu → custom dropdown with hover + focus, framer-motion → CSS,
// @llmgateway/shared MARKETING_STATS → hardcoded. Models/providers props and
// ModelSearch are omitted (not available in this project).

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Activity,
  Blocks,
  BookOpen,
  Bot,
  Boxes,
  Building2,
  Calculator,
  ChevronDown,
  Clock,
  Code,
  GitCompare,
  Gift,
  Github,
  Handshake,
  KeyRound,
  LayoutGrid,
  Menu,
  MessagesSquare,
  Network,
  Newspaper,
  ScrollText,
  Server,
  Shield,
  ShieldCheck,
  Sparkles,
  Trophy,
  Wrench,
  X,
  Zap,
} from "lucide-react";

const DOCS_URL = "https://docs.example.com";
const GITHUB_URL = "https://github.com";
const DISCORD_URL = "https://discord.com";

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
    href: "/models",
    description: "Route requests to 200+ LLMs through a single, unified API endpoint.",
    icon: Network,
    gradient: "hover:from-violet-500/20 hover:to-purple-600/30 group-hover/product:text-violet-400",
  },
  {
    title: "DevPass",
    href: "/pricing",
    description: "Fixed-price monthly plans for Claude Code, Cursor, and every coding tool.",
    icon: Code,
    gradient: "hover:from-indigo-500/20 hover:to-blue-600/30 group-hover/product:text-indigo-400",
  },
  {
    title: "Lounge",
    href: "/playground",
    description: "Every frontier model in one chat — plus image, video and audio studios.",
    icon: MessagesSquare,
    gradient: "hover:from-blue-500/20 hover:to-cyan-600/30 group-hover/product:text-blue-400",
  },
  {
    title: "Observability",
    href: "/docs",
    description: "Monitor usage, costs, and latency with real-time analytics dashboards.",
    icon: Activity,
    gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400",
  },
];

const resourcesLinks: NavLink[] = [
  { title: "Enterprise", href: "/enterprise", description: "Custom billing, extended retention, and priority support for teams.", icon: Building2, gradient: "hover:from-blue-500/20 hover:to-blue-600/30 group-hover/product:text-blue-400" },
  { title: "Blog", href: "/changelog", description: "Product updates, tutorials, benchmarks, and announcements.", icon: Newspaper, gradient: "hover:from-amber-500/20 hover:to-orange-600/30 group-hover/product:text-amber-400" },
  { title: "Changelog", href: "/changelog", description: "What's new in wiwi across releases.", icon: ScrollText, gradient: "hover:from-violet-500/20 hover:to-purple-600/30 group-hover/product:text-violet-400" },
  { title: "Integrations", href: "/docs", description: "Connect seamlessly with popular frameworks, SDKs, and tools.", icon: Blocks, gradient: "hover:from-indigo-500/20 hover:to-blue-600/30 group-hover/product:text-indigo-400" },
  { title: "Reliability", href: "/docs", description: "Automatic failover and 99.9999% effective uptime across providers.", icon: ShieldCheck, gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400" },
  { title: "Guardrails", href: "/docs", description: "Protect your AI with content moderation and safety filters.", icon: Shield, gradient: "hover:from-rose-500/20 hover:to-red-600/30 group-hover/product:text-rose-400" },
  { title: "Providers", href: "/models", description: "Connect and manage your provider API keys.", icon: KeyRound, gradient: "hover:from-cyan-500/20 hover:to-blue-600/30 group-hover/product:text-cyan-400" },
  { title: "Partners", href: "/about", description: "The inference partners powering the gateway.", icon: Handshake, gradient: "hover:from-teal-500/20 hover:to-emerald-600/30 group-hover/product:text-teal-400" },
  { title: "Rankings", href: "/models", description: "Top models by real token volume routed through the gateway.", icon: Trophy, gradient: "hover:from-amber-500/20 hover:to-yellow-600/30 group-hover/product:text-amber-400" },
  { title: "Apps", href: "/docs", description: "Browse apps and tools that work with wiwi.", icon: LayoutGrid, gradient: "hover:from-pink-500/20 hover:to-rose-600/30 group-hover/product:text-pink-400" },
  { title: "Models", href: "/models", description: "Browse all available LLM models and capabilities.", icon: Boxes, gradient: "hover:from-purple-500/20 hover:to-fuchsia-600/30 group-hover/product:text-purple-400" },
  { title: "Timeline", href: "/changelog", description: "Track the release history of all models.", icon: Clock, gradient: "hover:from-teal-500/20 hover:to-cyan-600/30 group-hover/product:text-teal-400" },
  { title: "Compare", href: "/compare", description: "Compare models side by side.", icon: GitCompare, gradient: "hover:from-sky-500/20 hover:to-blue-600/30 group-hover/product:text-sky-400" },
  { title: "Token Cost Calculator", href: "/pricing", description: "Calculate your LLM token costs and savings instantly.", icon: Calculator, gradient: "hover:from-green-500/20 hover:to-emerald-600/30 group-hover/product:text-green-400" },
  { title: "Referral Program", href: "/about", description: "Earn 1% of LLM spending.", icon: Gift, gradient: "hover:from-yellow-500/20 hover:to-amber-600/30 group-hover/product:text-yellow-400" },
];

const aiLinks: NavLink[] = [
  { title: "MCP Server", href: "/docs", description: "Connect AI assistants to 200+ LLMs via MCP protocol.", icon: Server, gradient: "hover:from-cyan-500/20 hover:to-blue-600/30 group-hover/product:text-cyan-400" },
  { title: "Agents", href: "/docs", description: "Pre-built AI agents with tool calling capabilities.", icon: Bot, gradient: "hover:from-violet-500/20 hover:to-purple-600/30 group-hover/product:text-violet-400" },
  { title: "AI SDK Provider", href: GITHUB_URL, description: "Use wiwi with Vercel's AI SDK.", icon: Zap, gradient: "hover:from-amber-500/20 hover:to-orange-600/30 group-hover/product:text-amber-400", external: true },
  { title: "Agent Skills", href: GITHUB_URL, description: "Skills for Claude Code and other AI agents.", icon: Sparkles, gradient: "hover:from-pink-500/20 hover:to-rose-600/30 group-hover/product:text-pink-400", external: true },
  { title: "Templates", href: "/docs", description: "Production-ready templates for AI applications.", icon: Wrench, gradient: "hover:from-emerald-500/20 hover:to-teal-600/30 group-hover/product:text-emerald-400" },
  { title: "Guides", href: "/docs", description: "Integration and usage guides for every framework.", icon: BookOpen, gradient: "hover:from-blue-500/20 hover:to-indigo-600/30 group-hover/product:text-blue-400" },
];

const mobileSections = [
  { label: "Products", items: productsLinks },
  { label: "Resources", items: resourcesLinks },
  { label: "AI", items: aiLinks },
];

// ── dropdown sub-component ────────────────────────────────────────────────

function DropdownMenu({ label, links, cols }: { label: string; links: NavLink[]; cols: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 px-3 py-2 text-sm text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
      >
        {label}
        <ChevronDown className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="absolute left-1/2 top-full z-50 -translate-x-1/2 pt-1">
          <div className={`grid gap-2 rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 shadow-2xl ${cols}`}>
            {links.map((link) => {
              const IconComp = link.icon;
              const iconColor = link.gradient.split(" ").slice(-1).join(" ");
              return link.external ? (
                <a
                  key={link.title}
                  href={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`group/product flex flex-row items-start gap-3 rounded-lg bg-linear-to-br from-transparent to-transparent p-3 no-underline outline-none transition-all duration-300 ${link.gradient} hover:shadow-lg`}
                >
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-white/[0.04]">
                    <IconComp className={`size-4 text-[var(--admin-text-muted)] transition-colors ${iconColor}`} />
                  </div>
                  <div className="space-y-0.5">
                    <div className="text-sm font-medium leading-none text-[var(--admin-text)]">{link.title}</div>
                    <p className="line-clamp-2 text-xs leading-snug text-[var(--admin-text-muted)]">{link.description}</p>
                  </div>
                </a>
              ) : (
                <Link
                  key={link.title}
                  to={link.href}
                  className={`group/product flex flex-row items-start gap-3 rounded-lg bg-linear-to-br from-transparent to-transparent p-3 no-underline outline-none transition-all duration-300 ${link.gradient} hover:shadow-lg`}
                >
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-white/[0.04]">
                    <IconComp className={`size-4 text-[var(--admin-text-muted)] transition-colors ${iconColor}`} />
                  </div>
                  <div className="space-y-0.5">
                    <div className="text-sm font-medium leading-none text-[var(--admin-text)]">{link.title}</div>
                    <p className="line-clamp-2 text-xs leading-snug text-[var(--admin-text-muted)]">{link.description}</p>
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── main navbar ──────────────────────────────────────────────────────────

export function Navbar({ sticky = true }: { sticky?: boolean; children?: React.ReactNode }) {
  const navigate = useNavigate();
  const [menuState, setMenuState] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);
  const [openMobileSection, setOpenMobileSection] = useState<string | null>(null);

  const isAuthenticated = !!localStorage.getItem("wiwi.user");

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 50);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <header>
      <nav data-state={menuState ? "active" : undefined} className={`z-20 w-full px-2 group ${sticky ? "fixed" : ""}`}>
        <div
          className={`mx-auto mt-2 max-w-[1400px] px-6 transition-all duration-300 ${
            isScrolled
              ? "rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-bg)]/50 backdrop-blur-lg lg:px-5"
              : ""
          }`}
        >
          <div className="relative flex flex-wrap items-center justify-between gap-6 py-3 nav:flex-nowrap nav:gap-0 nav:py-4">
            {/* Logo */}
            <div className="flex w-full justify-between nav:w-auto">
              <Link to="/" className="flex items-center gap-2">
                <img src="/wiwi-logo.png" alt="wiwi" className="size-8 rounded-full object-cover" />
                <span className="whitespace-nowrap text-xl font-bold tracking-tight text-[var(--admin-text)]">
                  wiwi
                </span>
              </Link>

              <button
                onClick={() => setMenuState(!menuState)}
                aria-label={menuState ? "Close Menu" : "Open Menu"}
                className="relative z-20 -m-2.5 -mr-4 block cursor-pointer p-2.5 nav:hidden"
              >
                {menuState ? (
                  <X className="size-6 duration-200" />
                ) : (
                  <Menu className="size-6 duration-200" />
                )}
              </button>
            </div>

            {/* Desktop center nav */}
            <div className="m-auto hidden items-center gap-1 nav:flex min-w-0">
              <div className="flex gap-0.5 text-sm">
                {/* Direct links */}
                <Link to="/pricing" className="block px-3 py-2 whitespace-nowrap text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">
                  DevPass
                </Link>
                <Link to="/playground" className="hidden min-[1360px]:block px-3 py-2 whitespace-nowrap text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">
                  Lounge
                </Link>
                <Link to="/models" className="block px-3 py-2 whitespace-nowrap text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">
                  Models
                </Link>

                {/* Dropdowns */}
                <DropdownMenu label="Products" links={productsLinks} cols="md:w-[520px] lg:w-[580px] grid-cols-2" />
                <DropdownMenu label="Resources" links={resourcesLinks} cols="md:w-[680px] lg:w-[820px] lg:grid-cols-3 grid-cols-2" />
                <DropdownMenu label="AI" links={aiLinks} cols="md:w-[520px] lg:w-[580px] grid-cols-2" />

                {/* Docs link */}
                <a
                  href={DOCS_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block px-3 py-2 text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
                >
                  Docs
                </a>

                {/* Pricing link */}
                <Link to="/pricing" className="block px-3 py-2 whitespace-nowrap text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">
                  Pricing
                </Link>
              </div>
            </div>

            {/* Right side */}
            <div className="bg-[var(--admin-bg)] mb-6 hidden max-h-[calc(100dvh-7rem)] w-full flex-wrap items-center justify-end space-y-6 overflow-y-auto overscroll-contain rounded-3xl border border-[var(--admin-border)] p-6 shadow-2xl group-data-[state=active]:block nav:group-data-[state=active]:flex md:flex-nowrap nav:m-0 nav:flex nav:max-h-none nav:w-fit nav:shrink-0 nav:gap-3 nav:space-y-0 nav:overflow-visible nav:border-transparent nav:bg-transparent nav:p-0 nav:shadow-none">
              {/* Mobile nav */}
              <div className="nav:hidden">
                <ul className="text-base">
                  <li>
                    <Link to="/pricing" className="block py-2.5 text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">DevPass</Link>
                  </li>
                  <li>
                    <Link to="/playground" className="block py-2.5 text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">Lounge</Link>
                  </li>
                  <li>
                    <Link to="/pricing" className="block py-2.5 text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">Pricing</Link>
                  </li>
                  <li>
                    <a href={DOCS_URL} target="_blank" rel="noopener noreferrer" className="block py-2.5 text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">Docs</a>
                  </li>
                  <li>
                    <Link to="/models" className="block py-2.5 text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">Models</Link>
                  </li>

                  {mobileSections.map((section) => (
                    <li key={section.label}>
                      <button
                        type="button"
                        onClick={() =>
                          setOpenMobileSection(openMobileSection === section.label ? null : section.label)
                        }
                        className="flex w-full items-center justify-between gap-2 py-2.5 text-left"
                        aria-expanded={openMobileSection === section.label}
                      >
                        <span className="text-[var(--admin-text-muted)]">{section.label}</span>
                        <ChevronDown
                          className={`size-4 text-[var(--admin-text-muted)] transition-transform duration-200 ${
                            openMobileSection === section.label ? "rotate-180" : ""
                          }`}
                        />
                      </button>
                      <ul
                        className={`grid grid-cols-2 gap-x-4 rounded-xl bg-white/[0.02] px-3 py-2 mb-2 ${
                          openMobileSection !== section.label ? "hidden" : ""
                        }`}
                      >
                        {section.items.map((item) => (
                          <li key={item.title}>
                            {item.external ? (
                              <a
                                href={item.href}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="block py-2 text-sm text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
                              >
                                {item.title}
                              </a>
                            ) : (
                              <Link
                                to={item.href}
                                className="block py-2 text-sm text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
                              >
                                {item.title}
                              </Link>
                            )}
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}

                  <li className="flex items-center gap-4 border-t border-[var(--admin-border)] pt-3 mt-2">
                    <a
                      href={GITHUB_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded-md p-2 text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
                      aria-label="GitHub"
                    >
                      <Github className="size-5" />
                    </a>
                  </li>
                </ul>
              </div>

              {/* Right side controls */}
              <div className="flex w-full flex-col items-center space-y-3 sm:flex-row sm:gap-3 sm:space-y-0 md:w-fit">
                <div className="hidden min-[1280px]:flex items-center gap-1">
                  <a
                    href={DISCORD_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-1.5 text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
                    aria-label="Discord"
                  >
                    <svg className="size-5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                      <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" />
                    </svg>
                  </a>
                </div>

                {isAuthenticated ? (
                  <button
                    onClick={() => navigate("/app")}
                    className="w-full rounded-xl bg-white px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-white/90 md:w-fit"
                  >
                    Dashboard
                  </button>
                ) : (
                  <>
                    <Link
                      to="/login"
                      className="hidden whitespace-nowrap text-sm text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)] nav:block"
                    >
                      Log In
                    </Link>
                    <button
                      onClick={() => navigate("/signup")}
                      className="w-full rounded-xl bg-white px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-white/90 md:w-fit"
                    >
                      Get Started
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </nav>
    </header>
  );
}
