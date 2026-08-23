// App shell — cloned from the Dra admin console: fixed sidebar with sections
// and collapse, blurred topbar with page identity + live badge + clock, and an
// ambient near-black backdrop with faint grid + radial glows.

import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  BarChart3,
  BellRing,
  Boxes,
  ChevronLeft,
  CreditCard,
  Database,
  KeyRound,
  LayoutDashboard,
  LogOut,
  ScrollText,
  Server,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import { useAuth } from "@/api/auth";
import { useAdminStream } from "@/api/stream";
import { getToken } from "@/api/client";

const SIDEBAR_WIDE = 260;
const SIDEBAR_COLLAPSED = 72;

interface NavItem {
  to: string;
  label: string;
  icon: typeof Server;
  end?: boolean;
}

interface NavSection {
  title: string;
  items: NavItem[];
}

const NAV_SECTIONS: NavSection[] = [
  {
    title: "Overview",
    items: [{ to: "/", label: "Dashboard", icon: LayoutDashboard, end: true }],
  },
  {
    title: "Traffic",
    items: [
      { to: "/request-logs", label: "Request Logs", icon: ScrollText },
      { to: "/proxy-logs", label: "Proxy Logs", icon: Terminal },
      { to: "/usage", label: "Usage", icon: Activity },
      { to: "/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
  {
    title: "Configuration",
    items: [
      { to: "/providers", label: "Providers", icon: Server },
      { to: "/builtin-providers", label: "Built-in Providers", icon: Boxes },
      { to: "/models", label: "Models", icon: Database },
      { to: "/keys", label: "Virtual Keys", icon: KeyRound },
    ],
  },
  {
    title: "Admin",
    items: [
      { to: "/budgets", label: "Budgets & Alerts", icon: BellRing },
      { to: "/settings", label: "Settings", icon: CreditCard },
    ],
  },
];

const PAGE_META: Record<string, { title: string; section: string }> = {
  "/": { title: "Dashboard", section: "Overview" },
  "/request-logs": { title: "Request Logs", section: "Traffic" },
  "/proxy-logs": { title: "Proxy Logs", section: "Traffic" },
  "/usage": { title: "Usage", section: "Traffic" },
  "/analytics": { title: "Analytics", section: "Traffic" },
  "/providers": { title: "Providers", section: "Configuration" },
  "/builtin-providers": { title: "Built-in Providers", section: "Configuration" },
  "/models": { title: "Models", section: "Configuration" },
  "/keys": { title: "Virtual Keys", section: "Configuration" },
  "/budgets": { title: "Budgets & Alerts", section: "Admin" },
  "/settings": { title: "Settings", section: "Admin" },
};

function LiveClock() {
  const [time, setTime] = useState("");
  useEffect(() => {
    const update = () =>
      setTime(
        new Date().toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }),
      );
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, []);
  return (
    <span className="font-mono text-[10px] tabular-nums tracking-wider text-[var(--admin-text-dim)]">
      {time}
    </span>
  );
}

export function Layout() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const connected = useAdminStream("__noop__", () => undefined);
  const [collapsed, setCollapsed] = useState(false);
  const sidebarWidth = collapsed ? SIDEBAR_COLLAPSED : SIDEBAR_WIDE;

  const meta = PAGE_META[location.pathname] ?? { title: "wiwi", section: "Admin" };
  const maskedKey = (() => {
    const k = getToken();
    return k.length > 17 ? `${k.slice(0, 13)}…${k.slice(-4)}` : "master key";
  })();

  return (
    <div data-admin className="relative z-0 h-screen overflow-hidden bg-[var(--admin-bg)]">
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
          style={{
            background:
              "radial-gradient(circle, rgba(59,130,246,0.04) 0%, transparent 60%)",
          }}
        />
        <div
          className="absolute -bottom-40 -right-40 h-[500px] w-[500px] rounded-full"
          style={{
            background:
              "radial-gradient(circle, rgba(124,58,237,0.03) 0%, transparent 60%)",
          }}
        />
        <div
          className="absolute left-1/3 top-1/2 h-[400px] w-[400px] -translate-y-1/2 rounded-full"
          style={{
            background:
              "radial-gradient(circle, rgba(168,85,247,0.02) 0%, transparent 60%)",
          }}
        />
      </div>

      {/* ── Sidebar ── */}
      <aside
        className={`fixed left-0 top-0 z-40 flex h-screen flex-col bg-[var(--admin-surface)] transition-all duration-300 ${
          collapsed ? "w-[72px]" : "w-[260px]"
        }`}
      >
        {/* Ambient glow layers */}
        <div
          className="pointer-events-none absolute -left-24 -top-24 h-64 w-64 rounded-full"
          style={{
            background:
              "radial-gradient(circle, rgba(59,130,246,0.05) 0%, rgba(124,58,237,0.02) 50%, transparent 70%)",
          }}
        />
        <div
          className="pointer-events-none absolute -bottom-24 -right-24 h-48 w-48 rounded-full"
          style={{
            background:
              "radial-gradient(circle, rgba(168,85,247,0.03) 0%, transparent 60%)",
          }}
        />

        {/* Logo */}
        <div className="relative flex h-[72px] items-center px-5">
          {collapsed ? (
            <span className="mx-auto flex h-9 w-9 items-center justify-center rounded-[10px] bg-gradient-to-br from-brand-500 to-fuchsia-600 font-mono text-sm font-bold text-white ring-1 ring-white/[0.06] ring-inset">
              w
            </span>
          ) : (
            <div className="flex items-center gap-3.5">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px] bg-gradient-to-br from-brand-500 to-fuchsia-600 font-mono text-lg font-bold text-white ring-1 ring-white/[0.06] ring-inset">
                w
              </span>
              <div>
                <h1 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                  wiwi
                </h1>
                <span
                  className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em]"
                  style={{ color: "rgba(59, 130, 246, 0.5)" }}
                >
                  Gateway
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Navigation */}
        <nav className="admin-scroll flex-1 space-y-6 overflow-y-auto px-3 py-5">
          {NAV_SECTIONS.map((section) => (
            <div key={section.title}>
              {!collapsed && <p className="admin-label mb-2.5 px-2.5">{section.title}</p>}
              <div className="space-y-0.5">
                {section.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      end={item.end}
                      title={collapsed ? item.label : undefined}
                      className={({ isActive }) =>
                        `group relative flex items-center gap-3 rounded-[12px] px-3 py-[9px] text-[13px] transition-all duration-200 ${
                          collapsed ? "justify-center px-2" : ""
                        } ${
                          isActive
                            ? "bg-blue-500/[0.06] text-blue-200"
                            : "text-[var(--admin-text-muted)] hover:bg-white/[0.02] hover:text-[var(--admin-text)]"
                        }`
                      }
                    >
                      {({ isActive }) => (
                        <>
                          {isActive && (
                            <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-blue-400" />
                          )}
                          <Icon
                            className={`h-[18px] w-[18px] shrink-0 transition-colors duration-200 ${
                              isActive
                                ? "text-blue-400"
                                : "text-white/20 group-hover:text-white/40"
                            }`}
                          />
                          {!collapsed && (
                            <span className="truncate font-medium tracking-[-0.01em]">
                              {item.label}
                            </span>
                          )}
                        </>
                      )}
                    </NavLink>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Bottom: identity + collapse */}
        <div className="space-y-2 p-3">
          {!collapsed && (
            <div className="flex items-center gap-3 rounded-[10px] border border-white/[0.04] bg-white/[0.02] px-3 py-2.5">
              <div
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500/20 to-violet-500/20 ring-1 ring-white/[0.06]"
                style={{ boxShadow: "0 0 12px rgba(59,130,246,0.06)" }}
              >
                <ShieldCheck className="h-4 w-4" style={{ color: "rgba(59,130,246,0.6)" }} />
              </div>
              <div className="min-w-0">
                <p className="truncate text-[12px] font-medium text-[var(--admin-text)]">
                  master admin
                </p>
                <p className="truncate font-mono text-[10px] text-[var(--admin-text-dim)]">
                  {maskedKey}
                </p>
              </div>
            </div>
          )}
          <button
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="flex w-full items-center justify-center gap-2 rounded-[10px] py-2.5 font-mono text-[11px] tracking-wider text-[var(--admin-text-dim)] transition-all duration-200 hover:bg-white/[0.02] hover:text-[var(--admin-text-muted)]"
          >
            <ChevronLeft
              className={`h-3.5 w-3.5 transition-transform duration-300 ${collapsed ? "rotate-180" : ""}`}
            />
            {!collapsed && <span>Collapse</span>}
          </button>
        </div>
      </aside>

      {/* ── Topbar ── */}
      <div
        className="fixed top-0 z-30 transition-all duration-300"
        style={{ left: sidebarWidth, right: 0 }}
      >
        <header className="admin-topbar relative">
          <div className="flex h-[64px] items-center gap-4 px-6">
            <div className="min-w-0 shrink-0">
              <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-[var(--admin-accent)] opacity-60">
                {meta.section}
              </span>
              <h1 className="mt-0.5 text-[17px] font-semibold leading-tight tracking-[-0.02em] text-[var(--admin-text)]">
                {meta.title}
              </h1>
            </div>

            <div className="mx-auto hidden items-center gap-2 lg:flex">
              <span className="admin-live-badge">
                <span
                  className={connected ? "admin-pulse-dot" : "h-1.5 w-1.5 rounded-full bg-zinc-600"}
                />
                {connected ? "live" : "offline"}
              </span>
            </div>

            <div className="flex shrink-0 items-center gap-2">
              <div className="hidden items-center gap-1.5 px-2 md:flex">
                <LiveClock />
              </div>
              <div className="mx-1 h-5 w-px bg-white/[0.04]" />
              <button
                onClick={() => {
                  logout();
                  navigate("/login");
                }}
                className="flex items-center gap-2 rounded-xl border border-white/[0.04] bg-white/[0.02] px-3 py-2 text-[12px] text-[var(--admin-text-dim)] transition-all duration-200 hover:border-white/[0.08] hover:bg-white/[0.03] hover:text-red-400"
              >
                <LogOut className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Sign out</span>
              </button>
            </div>
          </div>
          <div className="admin-topbar-border h-px" />
        </header>
      </div>

      {/* ── Scrollable content ── */}
      <div
        className="relative h-screen pt-[65px] transition-all duration-300"
        style={{ marginLeft: sidebarWidth, zIndex: 1 }}
      >
        <main className="admin-scroll h-full overflow-y-auto">
          <div className="admin-stagger mx-auto max-w-[1400px] p-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
