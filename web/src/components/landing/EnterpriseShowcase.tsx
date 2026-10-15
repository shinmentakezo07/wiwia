// Enterprise showcase — the "built for teams that ship at scale" band. Shared
// by the landing page (as its enterprise CTA) and the /enterprise page, so the
// two surfaces can never drift. A framed dark panel: ambient glows under a
// masked grid wash, gradient headline, proof strip, and capability cards.
// Copy is carried over verbatim from the original enterprise surfaces.

import { Link } from "react-router-dom";
import { ArrowRight, Building2, Check, Lock, Server, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";

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

const PROOF: { value: string; label: string }[] = [
  { value: "99.9%", label: "Uptime SLA" },
  { value: "SOC 2", label: "Type II" },
  { value: "100B+", label: "Tokens routed" },
  { value: "40+", label: "Providers" },
];

const INCLUDED = [
  "Dedicated support",
  "Compliance controls",
  "Custom SLAs",
  "On-boarding assistance",
];

const DEFAULT_SECONDARY_CTA = { label: "Explore Enterprise", to: "/enterprise" };

export function EnterpriseShowcase(props: {
  /** h1 on the /enterprise page, h2 where the band is one section of a page. */
  headingLevel?: "h1" | "h2";
  /** Pass null to drop the secondary button (e.g. on /enterprise itself). */
  secondaryCta?: { label: string; to: string } | null;
}) {
  const Heading = props.headingLevel ?? "h2";
  // keep the card titles one level under the band heading (h1 → h2, h2 → h3)
  const CardHeading = props.headingLevel === "h1" ? "h2" : "h3";
  const secondary =
    props.secondaryCta === undefined ? DEFAULT_SECONDARY_CTA : props.secondaryCta;

  return (
    <section className="scroll-reveal relative">
      <div className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)] sm:rounded-[32px]">
        {/* vertical lift so the panel reads as an object, not a hole */}
        <div
          className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/[0.03] via-transparent to-transparent"
          aria-hidden
        />
        <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-60" aria-hidden />

        {/* ambient backdrop: panning grid wash + blue/violet glows */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="hero-grid absolute inset-x-0 top-0 h-[520px] opacity-70" />
          <div
            className="absolute -left-28 -top-32 h-[420px] w-[420px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(59,130,246,0.16) 0%, transparent 62%)" }}
          />
          <div
            className="absolute -right-32 -top-24 h-[380px] w-[380px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(139,92,246,0.14) 0%, transparent 62%)" }}
          />
          <div
            className="absolute inset-x-0 bottom-0 h-[240px]"
            style={{ background: "radial-gradient(ellipse 70% 100% at 50% 120%, rgba(59,130,246,0.09), transparent 70%)" }}
          />
        </div>

        <div className="relative px-6 py-14 sm:px-10 sm:py-16 lg:px-14 lg:py-20">
          {/* ── hero ── */}
          <div className="mx-auto max-w-2xl text-center">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/[0.06] px-3.5 py-1.5 text-blue-300">
              <Building2 size={12} />
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em]">
                Enterprise
              </span>
              <span className="hero-live-dot text-blue-400" aria-hidden />
            </div>

            <Heading className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl lg:text-5xl">
              Built for teams that
              <br />
              <span className="hero-gradient-text">ship at scale</span>
            </Heading>

            <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base">
              When your LLM infrastructure becomes mission-critical, you need dedicated support,
              compliance controls, and infrastructure that matches your ambitions.
            </p>

            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                to="/contact"
                className="wiwi-shimmer group inline-flex h-11 w-full items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-7 text-sm font-semibold text-white shadow-lg shadow-brand-600/25 transition-[transform,filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/60 active:scale-[0.98] sm:w-auto"
              >
                Talk to Sales
                <ArrowRight
                  size={15}
                  className="transition-transform duration-150 group-hover:translate-x-0.5"
                />
              </Link>
              {secondary && (
                <Link
                  to={secondary.to}
                  className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.02] px-6 text-sm font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.16] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
                >
                  {secondary.label}
                </Link>
              )}
            </div>

            <ul className="mt-7 flex flex-wrap items-center justify-center gap-x-5 gap-y-2.5">
              {INCLUDED.map((item) => (
                <li
                  key={item}
                  className="flex items-center gap-1.5 text-[13px] text-[var(--admin-text-muted)]"
                >
                  <Check size={13} className="shrink-0 text-emerald-400" />
                  {item}
                </li>
              ))}
            </ul>
          </div>

          {/* ── proof strip ── */}
          <div className="mx-auto mt-14 grid max-w-4xl grid-cols-2 gap-px overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-border)] sm:grid-cols-4">
            {PROOF.map((stat) => (
              <div
                key={stat.label}
                className="group/stat bg-[var(--admin-surface)] px-5 py-6 text-center transition-colors duration-300 hover:bg-white/[0.02]"
              >
                <p className="admin-stat-value text-2xl sm:text-[28px]">{stat.value}</p>
                <p className="admin-label mt-1.5">{stat.label}</p>
              </div>
            ))}
          </div>

          {/* ── capability cards ── */}
          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {CAPABILITIES.map((cap) => {
              const Icon = cap.icon;
              return (
                <div
                  key={cap.title}
                  className="group relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-white/[0.015] p-5 transition-all duration-300 hover:border-blue-500/25 hover:bg-white/[0.03]"
                >
                  <div
                    className="pointer-events-none absolute -top-16 left-1/2 h-32 w-32 -translate-x-1/2 rounded-full bg-blue-500/10 opacity-0 blur-2xl transition-opacity duration-300 group-hover:opacity-100"
                    aria-hidden
                  />
                  <div className="relative">
                    <span className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl border border-white/[0.06] bg-gradient-to-br from-blue-500/15 to-violet-500/10 text-blue-300 transition-colors duration-300 group-hover:border-blue-500/30">
                      <Icon size={17} />
                    </span>
                    <CardHeading className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                      {cap.title}
                    </CardHeading>
                    <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                      {cap.body}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
