// AppSidebar — floating admin sidebar with nav menu and a user dropdown in
// the footer that signs the user out. Ported from the Next.js reference's
// app-sidebar.tsx, adapted to the project's dark admin design system and the
// useAuth / react-router stack. The reference used shadcn Sidebar primitives;
// this version inlines a compact sidebar so it has no extra UI deps.

import { ChevronUp, Settings, User2 } from "lucide-react";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/api/auth";
import { Badge, Button } from "@/components/ui";

const items = [
  { title: "Dashboard", url: "/app", icon: User2 },
  { title: "Settings", url: "/app/settings", icon: Settings },
];

export function AppSidebar() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <aside
      data-admin
      className="flex h-full w-60 flex-col border-r border-[var(--admin-border)] bg-[var(--admin-surface)]"
    >
      {/* Group */}
      <div className="flex-1 overflow-y-auto admin-scroll px-3 py-4">
        <div className="admin-label mb-2 flex items-center gap-2 px-2">
          wiwi <Badge tone="blue">Admin</Badge>
        </div>
        <nav className="space-y-0.5">
          {items.map((item) => {
            const active = location.pathname === item.url;
            return (
              <Link
                key={item.title}
                to={item.url}
                className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-[var(--admin-accent-soft)] text-[var(--admin-accent)]"
                    : "text-[var(--admin-text-muted)] hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
                }`}
              >
                <item.icon className="h-4 w-4" />
                <span>{item.title}</span>
              </Link>
            );
          })}
        </nav>
      </div>

      {/* Footer — user dropdown */}
      <div className="relative border-t border-[var(--admin-border)] px-3 py-3">
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
        >
          <User2 className="h-4 w-4" />
          <span className="flex-1 text-left truncate">{user?.username ?? "User"}</span>
          <ChevronUp className="h-4 w-4" />
        </button>
        {menuOpen && (
          <div className="absolute bottom-14 left-3 right-3 overflow-hidden rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] shadow-xl">
            <button
              type="button"
              onClick={handleLogout}
              className="flex w-full items-center px-3 py-2 text-sm text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

export { Button };
