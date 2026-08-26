// Enterprise — marketing page for teams running the gateway at scale.
// Hero, capability cards, trust stats row, and a sales CTA. Matches the dark
// design system shared with the admin console.

import { Link } from "react-router-dom";
import { ArrowRight, Building2, Lock, Server, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const CAPABILITIES: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: Lock,
    title: "Enterprise SSO",
    body: "SAML & OIDC single sign-on with role-based access control across every key and model.",
  },
  {
    icon: Server,
    title: "Self-hosted or Managed",
    body: "Deploy on your own infrastructure or let us run it for you with a 99.9% uptime SLA.",
  },
  {
    icon: Zap,
    title: "Volume Pricing",
    body: "Custom rate limits and pricing that scales with your usage — no per-seat licence.",
  },
  {
    icon: Building2,
    title: "White-label Ready",
    body: "Brand the gateway and admin UI as your own. Your callers never see the wiwi name.",
  },
];

const STATS: { value: string; label: string }[] = [
  { value: "99.9%", label: "Uptime SLA" },
  { value: "SOC 2", label: "Type II" },
  { value: "100B+", label: "Tokens routed" },
  { value: "40+", label: "Providers" },
];

export function EnterprisePage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="relative overflow-hidden rounded-3xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent px-6 py-16 text-center sm:px-12 sm:py-20">
        <div
          className="pointer-events-none absolute -left-20 -top-20 h-[420px] w-[420px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 60%)" }}
          aria-hidden
        />
        <div className="relative">
          <span className="admin-badge admin-badge-blue mb-6 inline-flex items-center gap-1.5">
            <Building2 size={11} /> Enterprise
          </span>
          <h1 className="text-4xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-5xl">
            Built for teams that
            <br />
            <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
              ship at scale
            </span>
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            When your LLM infrastructure becomes mission-critical, you need dedicated
            support, compliance controls, and infrastructure that matches your ambitions.
          </p>
        </div>
      </section>

      {/* ── capability cards ── */}
      <section>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {CAPABILITIES.map((cap) => {
            const Icon = cap.icon;
            return (
              <Card key={cap.title} className="p-5">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
                    <Icon className="h-4 w-4" style={{ color: "rgba(59,130,246,0.85)" }} />
                  </span>
                  <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                    {cap.title}
                  </h3>
                </div>
                <p className="mt-3 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                  {cap.body}
                </p>
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── stats row ── */}
      <section>
        <Card className="p-0">
          <div className="grid grid-cols-2 gap-px bg-[var(--admin-border)] md:grid-cols-4">
            {STATS.map((s) => (
              <div key={s.label} className="bg-[var(--admin-surface)] p-6 text-center">
                <p className="text-[28px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
                  {s.value}
                </p>
                <p className="mt-1 admin-label">{s.label}</p>
              </div>
            ))}
          </div>
        </Card>
      </section>

      {/* ── CTA ── */}
      <section className="rounded-2xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent p-8 text-center sm:p-12">
        <h2 className="text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Let's talk about your deployment
        </h2>
        <p className="mx-auto mt-3 max-w-md text-[14px] text-[var(--admin-text-muted)]">
          Custom SLAs, priority support, and on-boarding assistance for regulated teams
          putting LLMs in production.
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          <Link
            to="/signup"
            className="wiwi-shimmer group inline-flex h-11 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110"
          >
            Talk to Sales
            <ArrowRight size={15} className="transition-transform duration-150 group-hover:translate-x-0.5" />
          </Link>
          <Link
            to="/docs"
            className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-sm font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            Explore Enterprise
          </Link>
        </div>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 font-mono text-[11px] text-[var(--admin-text-dim)]">
          <span>Custom SLAs</span>
          <span className="h-3 w-px bg-[var(--admin-border)]" />
          <span>Priority support</span>
          <span className="h-3 w-px bg-[var(--admin-border)]" />
          <span>SOC 2 Type II</span>
          <span className="h-3 w-px bg-[var(--admin-border)]" />
          <span>On-boarding assistance</span>
        </div>
      </section>
    </div>
  );
}
