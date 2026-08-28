// App shell — cloned from the Dra admin console: fixed sidebar with sections
// and collapse, blurred topbar with page identity + live badge + clock, and an
// ambient near-black backdrop with faint grid + radial glows.
//
// Role-aware: the sidebar nav and identity card reflect the logged-in user's
// role (admin sees everything; user sees Overview/Traffic/Configuration/Budgets
// but not Providers/Settings/Users). Master-key admins keep the bearer token so
// /admin/stream SSE stays live.

import { useEffect, useMemo, useState } from "react";
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
  Link2,
  LogOut,
  Menu,
  ScrollText,
  Server,
  ShieldCheck,
  Terminal,
  Users,
} from "lucide-react";
import { useAuth } from "@/api/auth";
import { useAdminStream } from "@/api/stream";
import { getToken } from "@/api/client";
import { useClientPrefs } from "@/lib/settings";

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

// Sections visible to every authenticated user. Admin-only routes
// (Providers, Built-in Providers, Proxy Logs, Settings, Users) are appended
// only when the current user is an admin.
const USER_NAV_SECTIONS: NavSection[] = [
  {
    title: "Overview",
    items: [{ to: "/console", label: "Dashboard", icon: LayoutDashboard, end: true }],
  },
  {
    title: "Traffic",
    items: [
      { to: "/console/request-logs", label: "Request Logs", icon: ScrollText },
      { to: "/console/usage", label: "Usage", icon: Activity },
      { to: "/console/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
  {
    title: "Configuration",
    items: [
      { to: "/console/models", label: "Models", icon: Database },
      { to: "/console/keys", label: "Virtual Keys", icon: KeyRound },
    ],
  },
  {
    title: "Admin",
    items: [{ to: "/console/budgets", label: "Budgets & Alerts", icon: BellRing }],
  },
];

const ADMIN_ONLY_SECTIONS: NavSection[] = [
  {
    title: "Configuration",
    items: [
      { to: "/console/providers", label: "Providers", icon: Server },
      { to: "/console/oauth", label: "OAuth Providers", icon: Link2 },
      { to: "/console/builtin-providers", label: "Built-in Providers", icon: Boxes },
    ],
  },
  {
    title: "Admin",
    items: [
      { to: "/console/proxy-logs", label: "Proxy Logs", icon: Terminal },
      { to: "/console/users", label: "Users", icon: Users },
      { to: "/console/settings", label: "Settings", icon: CreditCard },
    ],
  },
];

const PAGE_META: Record<string, { title: string; section: string }> = {
  "/console": { title: "Dashboard", section: "Overview" },
  "/console/request-logs": { title: "Request Logs", section: "Traffic" },
  "/console/proxy-logs": { title: "Proxy Logs", section: "Traffic" },
  "/console/usage": { title: "Usage", section: "Traffic" },
  "/console/analytics": { title: "Analytics", section: "Traffic" },
  "/console/providers": { title: "Providers", section: "Configuration" },
  "/console/oauth": { title: "OAuth Providers", section: "Configuration" },
  "/console/builtin-providers": { title: "Built-in Providers", section: "Configuration" },
  "/console/models": { title: "Models", section: "Configuration" },
  "/console/keys": { title: "Virtual Keys", section: "Configuration" },
  "/console/budgets": { title: "Budgets & Alerts", section: "Admin" },
  "/console/settings": { title: "Settings", section: "Admin" },
  "/console/users": { title: "Users", section: "Admin" },
};

function LiveClock() {
  const { prefs } = useClientPrefs();
  const [time, setTime] = useState("");
  useEffect(() => {
    const update = () =>
      setTime(
        new Date().toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: !prefs.clock24h,
        }),
      );
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [prefs.clock24h]);
  return (
    <span className="font-mono text-[10px] tabular-nums tracking-wider text-[var(--admin-text-dim)]">
      {time}
    </span>
  );
}

export function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const connected = useAdminStream("__noop__", () => undefined);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const sidebarWidth = collapsed ? SIDEBAR_COLLAPSED : SIDEBAR_WIDE;

  const isAdmin = user?.role === "admin";
  const navSections = useMemo<NavSection[]>(
    () => (isAdmin ? [...USER_NAV_SECTIONS, ...ADMIN_ONLY_SECTIONS] : USER_NAV_SECTIONS),
    [isAdmin],
  );

  // Cmd/Ctrl+B toggles the sidebar
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        setCollapsed((c) => !c);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Close mobile sidebar on route change
  useEffect(() => setMobileOpen(false), [location.pathname]);

  const meta = PAGE_META[location.pathname] ?? { title: "wiwi", section: "Admin" };
  const maskedKey = (() => {
    const k = getToken();
    return k.length > 17 ? `${k.slice(0, 13)}…${k.slice(-4)}` : "master key";
  })();

  const identityName = user ? user.username : "user";
  const identityRole = isAdmin ? "admin" : "user";
  const identityBadge = isAdmin ? (user?.id === "master" ? "root" : "admin") : "user";

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
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      {/* ── Sidebar ── */}
      <aside
        className={`fixed left-0 top-0 z-40 flex h-screen flex-col bg-[var(--admin-surface)] transition-all duration-300 ${
          collapsed ? "w-[72px]" : "w-[260px]"
        } ${
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
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
            <img src="/wiwi-logo.png" alt="wiwi" className="mx-auto h-9 w-9 rounded-[10px] object-cover ring-1 ring-white/[0.06] ring-inset" />
          ) : (
            <div className="flex items-center gap-3.5">
              <img src="/wiwi-logo.png" alt="wiwi" className="h-10 w-10 shrink-0 rounded-[12px] object-cover ring-1 ring-white/[0.06] ring-inset" />
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
          {navSections.map((section) => (
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
        <div className="relative p-3">
          {/* hairline divider */}
          <div className="mb-3 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />

          {/* ── identity card ── */}
          {!collapsed ? (
            <div className="admin-identity group relative overflow-hidden rounded-[12px] border border-white/[0.05] bg-white/[0.02] transition-colors duration-300 hover:border-white/[0.09]">
              {/* top accent line */}
              <span className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-blue-500/30 to-transparent" />
              <div className="relative flex items-center gap-3 px-3 py-2.5">
                {/* avatar + status */}
                <div className="relative shrink-0">
                  <div
                    className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/20 to-violet-500/20 ring-1 ring-white/[0.08]"
                    style={{ boxShadow: "0 0 14px -3px rgba(59,130,246,0.14)" }}
                  >
                    <ShieldCheck className="h-4 w-4" style={{ color: "rgba(59,130,246,0.75)" }} />
                  </div>
                  <span
                    className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-[var(--admin-surface)] ${
                      connected ? "bg-emerald-400" : "bg-zinc-600"
                    }`}
                    style={connected ? { boxShadow: "0 0 6px rgba(52,211,153,0.5)" } : undefined}
                  />
                </div>
                {/* name + role + key */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <p className="truncate text-[12.5px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                      {identityName}
                    </p>
                    <span className="admin-badge admin-badge-blue !px-1.5 !py-0 !text-[8px]">
                      {identityBadge}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-1.5">
                    <KeyRound className="h-3 w-3 shrink-0 text-[var(--admin-text-dim)]" />
                    <p className="truncate font-mono text-[10px] text-[var(--admin-text-dim)]">
                      {isAdmin ? maskedKey : identityRole}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            /* collapsed: avatar with status dot only */
            <div className="mb-2 flex justify-center">
              <div className="relative" title={identityName}>
                <div
                  className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/20 to-violet-500/20 ring-1 ring-white/[0.08]"
                  style={{ boxShadow: "0 0 14px -3px rgba(59,130,246,0.14)" }}
                >
                  <ShieldCheck className="h-4 w-4" style={{ color: "rgba(59,130,246,0.75)" }} />
                </div>
                <span
                  className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-[var(--admin-surface)] ${
                    connected ? "bg-emerald-400" : "bg-zinc-600"
                  }`}
                />
              </div>
            </div>
          )}

          {/* ── collapse button ── */}
          <button
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="admin-collapse-btn mt-2 flex w-full items-center justify-center gap-2 rounded-[10px] border border-white/[0.04] py-2.5 font-mono text-[11px] tracking-wider text-[var(--admin-text-dim)] transition-all duration-200 hover:border-white/[0.08] hover:bg-white/[0.02] hover:text-[var(--admin-text-muted)]"
          >
            <ChevronLeft
              className={`h-3.5 w-3.5 transition-transform duration-300 ${collapsed ? "rotate-180" : ""}`}
            />
            {!collapsed && (
              <>
                <span>Collapse</span>
                <kbd className="admin-kbd ml-0.5">⌘B</kbd>
              </>
            )}
          </button>
        </div>
      </aside>

      {/* ── Topbar ── */}
      <div
        className="fixed top-0 z-30 transition-all duration-300"
        style={{ left: 0, right: 0 }}
      >
        <header className="admin-topbar relative">
          <div className="flex h-[64px] items-center gap-3 px-4 sm:gap-4 sm:px-6">
            {/* Mobile sidebar toggle */}
            <button
              onClick={() => setMobileOpen(true)}
              aria-label="Open sidebar"
              className="-ml-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/[0.04] bg-white/[0.02] text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.05] hover:text-[var(--admin-text)] lg:hidden"
            >
              <Menu className="h-4.5 w-4.5" />
            </button>
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
                  void logout();
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
        className="relative h-screen pt-[65px] transition-all duration-300 lg:ml-[var(--sidebar-w)]"
        style={{ "--sidebar-w": `${sidebarWidth}px`, zIndex: 1 } as React.CSSProperties}
      >
        <main className="admin-scroll h-full overflow-y-auto">
          <div className="admin-stagger mx-auto max-w-[1400px] p-4 sm:p-6 lg:p-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
