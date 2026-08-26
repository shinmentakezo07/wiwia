// Public layout — top-nav + full footer for the unauthenticated front of the
// site (landing, models, docs, pricing, enterprise, etc.). Reuses the admin
// dark surface + ambient background tokens so the public pages share the same
// visual language as the console without pulling in the admin sidebar.

import { Link, NavLink, Outlet } from "react-router-dom";
import { BookOpen, Boxes, DollarSign, Terminal, TrendingUp } from "lucide-react";
import { useAuth } from "@/api/auth";

const NAV_LINKS = [
  { to: "/playground", label: "Playground", icon: Terminal },
  { to: "/models", label: "Models", icon: Boxes },
  { to: "/pricing", label: "Pricing", icon: DollarSign },
  { to: "/compare", label: "Compare", icon: TrendingUp },
  { to: "/docs", label: "Docs", icon: BookOpen },
];

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
export function PublicLayout() {
  const { user } = useAuth();
  return (
    <div data-admin className="relative z-0 min-h-screen bg-[var(--admin-bg)]">
      {/* Ambient background: faint grid + radial glows */}
      <div className="pointer-events-none fixed inset-0" style={{ zIndex: 0 }}>
        <div
          className="absolute inset-0 opacity-[0.02]"
          style={{
            backgroundImage: `
              linear-gradient(rgba(255,255,255,0.08) 1px, transparent 1px),
              linear-gradient(90deg, rgba(255,255,255,0.08) 1px, transparent 1px)
            `,
            backgroundSize: "64px 64px",
          }}
        />
        <div
          className="absolute -left-40 -top-40 h-[600px] w-[600px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(59,130,246,0.04) 0%, transparent 60%)" }}
        />
        <div
          className="absolute -bottom-40 -right-40 h-[500px] w-[500px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(124,58,237,0.03) 0%, transparent 60%)" }}
        />
      </div>

      {/* Top nav */}
      <header className="admin-topbar sticky top-0 z-30">
        <div className="mx-auto flex h-[64px] max-w-[1400px] items-center gap-6 px-6">
          <Link to="/" className="flex items-center gap-3">
            <img src="/wiwi-logo.png" alt="wiwi" className="h-9 w-9 shrink-0 rounded-[10px] object-cover ring-1 ring-white/[0.06] ring-inset" />
            <div>
              <h1 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">wiwi</h1>
              <span className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em]" style={{ color: "rgba(59, 130, 246, 0.5)" }}>
                Gateway
              </span>
            </div>
          </Link>

          <nav className="ml-auto flex items-center gap-1">
            {NAV_LINKS.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13px] transition-colors ${
                      isActive
                        ? "bg-blue-500/[0.06] text-blue-200"
                        : "text-[var(--admin-text-muted)] hover:bg-white/[0.02] hover:text-[var(--admin-text)]"
                    }`
                  }
                >
                  <Icon className="h-[16px] w-[16px]" />
                  {item.label}
                </NavLink>
              );
            })}
            <Link
              to={user ? "/app" : "/login"}
              className="ml-2 flex items-center gap-2 rounded-[10px] border border-white/[0.06] bg-white/[0.02] px-3.5 py-2 text-[13px] font-medium text-[var(--admin-text)] transition-all hover:border-white/[0.1] hover:bg-white/[0.04]"
            >
              {user ? "Console" : "Sign in"}
            </Link>
          </nav>
        </div>
        <div className="admin-topbar-border h-px" />
      </header>

      {/* Content */}
      <main className="admin-scroll relative z-10">
        <div className="admin-stagger mx-auto max-w-[1400px] p-8">
          <Outlet />
        </div>
      </main>

      {/* Footer */}
      <footer className="relative z-10 border-t border-[var(--admin-border)] py-10">
        <div className="mx-auto max-w-[1400px] px-6">
          <div className="flex flex-col gap-8 md:flex-row md:justify-between">
            {/* brand */}
            <div className="md:w-52">
              <Link to="/" className="flex items-center gap-2.5">
                <img src="/wiwi-logo.png" alt="wiwi" className="h-8 w-8 rounded-[8px] object-cover ring-1 ring-white/[0.06] ring-inset" />
                <span className="text-[14px] font-semibold text-[var(--admin-text)]">wiwi</span>
                <span className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">Gateway</span>
              </Link>
              <p className="mt-3 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
                Self-hosted unified LLM gateway. One binary, every model behind a single endpoint.
              </p>
            </div>
            {/* link columns */}
            <div className="grid grid-cols-2 gap-8 sm:grid-cols-3">
              {FOOTER_COLS.map((col) => (
                <div key={col.heading}>
                  <h3 className="mb-3 text-[12px] font-semibold text-[var(--admin-text)]">{col.heading}</h3>
                  <ul className="space-y-1.5">
                    {col.links.map((l) => (
                      <li key={l.label}>
                        <Link to={l.to} className="text-[12px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]">
                          {l.label}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
          <div className="mt-8 border-t border-[var(--admin-border)] pt-6 text-center text-[11px] text-[var(--admin-text-dim)]">
            wiwi · self-hosted LLM gateway · {new Date().getFullYear()}
          </div>
        </div>
      </footer>
    </div>
  );
}
