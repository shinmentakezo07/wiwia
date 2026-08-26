// Public layout — top-nav + full footer for the unauthenticated front of the
// site (landing, models, docs, pricing, enterprise, etc.). Reuses the admin
// dark surface + ambient background tokens so the public pages share the same
// visual language as the console without pulling in the admin sidebar.

import { Link, Outlet } from "react-router-dom";
import { Navbar } from "@/components/landing/Navbar";

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
      <Navbar />

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
