// EnterpriseCTA — enterprise upsell section. Replaces next/link with
// react-router-dom Link, framer-motion AnimatedGroup with CSS AnimatedGroup,
// and radix Button with inline anchors styled via admin tokens.

import { ArrowRight, Building2, Lock, Server, Zap } from "lucide-react";
import { Link } from "react-router-dom";
import { AnimatedGroup } from "./AnimatedGroup";

const capabilities = [
  {
    icon: Lock,
    title: "Enterprise SSO",
    description: "SAML & OIDC single sign-on with role-based access control",
  },
  {
    icon: Server,
    title: "Self-hosted or Managed",
    description: "Deploy on your infrastructure or let us handle it with 99.9% SLA",
  },
  {
    icon: Zap,
    title: "Volume Pricing",
    description: "Custom rate limits and pricing that scales with your usage",
  },
  {
    icon: Building2,
    title: "White-label Ready",
    description: "White-label gateway and chat app with your own branding",
  },
];

export function EnterpriseCTA() {
  return (
    <section className="relative overflow-hidden py-24 md:py-32">
      {/* Subtle top separator */}
      <div className="absolute left-0 right-0 top-0 h-px bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />

      {/* Background treatment — dark panel effect */}
      <div className="absolute inset-0 bg-gradient-to-b from-[var(--admin-text)]/[0.03] via-[var(--admin-text)]/[0.05] to-[var(--admin-text)]/[0.03]" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(59,130,246,0.08),transparent)]" />

      <div className="container relative mx-auto px-4">
        <div className="mx-auto max-w-6xl">
          {/* Header */}
          <AnimatedGroup preset="blur-slide" className="mb-16 text-center">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/5 px-4 py-1.5">
              <span className="font-mono text-xs font-medium uppercase tracking-wider text-blue-500">
                Enterprise
              </span>
            </div>
            <h2 className="text-3xl font-bold tracking-tight text-[var(--admin-text)] md:text-4xl lg:text-5xl">
              Built for teams that
              <br />
              ship at scale
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-lg text-[var(--admin-text-muted)]">
              When your LLM infrastructure becomes mission-critical, you need dedicated
              support, compliance controls, and infrastructure that matches your ambitions.
            </p>
          </AnimatedGroup>

          {/* Capability cards */}
          <AnimatedGroup
            preset="slide"
            className="mb-12 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
          >
            {capabilities.map((cap) => (
              <div
                key={cap.title}
                className="group rounded-xl border border-[var(--admin-border)] bg-[var(--admin-bg)]/80 p-6 backdrop-blur-sm transition-all duration-300 hover:border-blue-500/30 hover:shadow-md hover:shadow-blue-500/5"
              >
                <div className="mb-4 flex size-10 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] transition-colors group-hover:border-blue-500/30 group-hover:bg-blue-500/5">
                  <cap.icon className="size-5 text-[var(--admin-text-muted)] transition-colors group-hover:text-blue-500" />
                </div>
                <h3 className="mb-1.5 text-base font-semibold tracking-tight text-[var(--admin-text)]">
                  {cap.title}
                </h3>
                <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">
                  {cap.description}
                </p>
              </div>
            ))}
          </AnimatedGroup>

          {/* CTA row */}
          <AnimatedGroup
            preset="blur-slide"
            className="flex flex-col items-center justify-center gap-4 sm:flex-row"
          >
            <Link
              to="/enterprise"
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--admin-text)] px-8 py-6 text-base font-medium text-[var(--admin-bg)] transition-opacity hover:opacity-90 sm:w-auto"
            >
              Talk to Sales
              <ArrowRight className="size-4" />
            </Link>
            <Link
              to="/enterprise"
              className="inline-flex w-full items-center justify-center rounded-xl border border-[var(--admin-border)] bg-transparent px-8 py-6 text-base text-[var(--admin-text)] transition-colors hover:bg-white/[0.04] sm:w-auto"
            >
              Explore Enterprise
            </Link>
          </AnimatedGroup>

          {/* Trust line */}
          <AnimatedGroup
            preset="fade"
            className="mt-8 flex items-center justify-center gap-6 text-sm text-[var(--admin-text-muted)]"
          >
            <span>Custom SLAs</span>
            <span className="h-3 w-px bg-[var(--admin-border)]" />
            <span>Priority support</span>
            <span className="h-3 w-px bg-[var(--admin-border)]" />
            <span>SOC 2 Type II certified</span>
            <span className="hidden h-3 w-px bg-[var(--admin-border)] sm:block" />
            <span className="hidden sm:inline">On-boarding assistance</span>
          </AnimatedGroup>
        </div>
      </div>
    </section>
  );
}
