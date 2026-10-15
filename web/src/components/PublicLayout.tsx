// Public layout — top-nav + full footer for the unauthenticated front of the
// site (landing, models, docs, pricing, enterprise, etc.). Reuses the admin
// dark surface + ambient background tokens so the public pages share the same
// visual language as the console without pulling in the admin sidebar.

import { Outlet } from "react-router-dom";
import { Navbar } from "@/components/landing/Navbar";
import { PublicFooter } from "@/components/landing/Footer";

export function PublicLayout() {
  return (
    <div data-admin className="relative z-0 min-h-screen bg-[var(--admin-bg)]">
      {/* Ambient background: faint grid + radial glows */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden" style={{ zIndex: 0 }}>
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
        <div className="admin-stagger mx-auto max-w-[1400px] p-4 sm:p-6 lg:p-8">
          <Outlet />
        </div>
      </main>

      {/* Footer */}
      <PublicFooter />
    </div>
  );
}
