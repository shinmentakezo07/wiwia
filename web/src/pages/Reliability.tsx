// Reliability — automatic failover, health monitoring, and intelligent
// routing. Adapted from the llmgateway.io reliability page, inlined into one
// file with the dark design system.

import { Link } from "react-router-dom";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Globe,
  LineChart,
  Shield,
  ShieldCheck,
  Timer,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const STATS = [
  { value: "99.9999%", label: "Effective uptime" },
  { value: "<32s", label: "Downtime per year" },
  { value: "25+", label: "Providers" },
  { value: "0ms", label: "Failover overhead" },
];

const FAILOVER_STEPS: { icon: LucideIcon; title: string; body: string; accent: string }[] = [
  {
    icon: AlertCircle,
    title: "Provider fails",
    body: "An upstream provider returns a 5xx, times out, or rate limits your request. We detect it within the same request cycle.",
    accent: "text-red-400 bg-red-500/10",
  },
  {
    icon: Zap,
    title: "Instant re-route",
    body: "The Gateway automatically retries the same prompt against the next healthy provider for that model, so your app does not experience additional latency.",
    accent: "text-blue-400 bg-blue-500/10",
  },
  {
    icon: CheckCircle2,
    title: "Response delivered",
    body: "Your user gets their answer. Our status dashboard records the incident for you — your service stays up even when providers don't.",
    accent: "text-emerald-400 bg-emerald-500/10",
  },
];

const FEATURES: { icon: LucideIcon; title: string; body: string; accent: string }[] = [
  {
    icon: Activity,
    title: "Real-time health checks",
    body: "Every provider is continuously probed. Unhealthy endpoints are taken out of rotation within seconds.",
    accent: "text-emerald-400 bg-emerald-500/10",
  },
  {
    icon: Timer,
    title: "Smart routing by latency",
    body: "Requests go to the fastest responsive provider for your region. TTFT is tracked per provider, per model.",
    accent: "text-blue-400 bg-blue-500/10",
  },
  {
    icon: Globe,
    title: "Multi-region redundancy",
    body: "Route across providers spread across US, EU, and APAC so a regional outage never takes you down.",
    accent: "text-purple-400 bg-purple-500/10",
  },
  {
    icon: Shield,
    title: "Rate-limit aware",
    body: "When a provider throttles you, traffic shifts automatically — you keep serving requests without manual intervention.",
    accent: "text-amber-400 bg-amber-500/10",
  },
  {
    icon: LineChart,
    title: "Observable by default",
    body: "Uptime, error rates, and latency tracked per provider in your dashboard. Use it in audits or share with stakeholders.",
    accent: "text-cyan-400 bg-cyan-500/10",
  },
  {
    icon: BarChart3,
    title: "SLA reporting",
    body: "Export uptime and performance reports for compliance. Enterprise plans include 99.9% SLAs with credits.",
    accent: "text-pink-400 bg-pink-500/10",
  },
];

export function ReliabilityPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-4 py-1.5">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
          </span>
          <span className="font-mono text-[11px] text-emerald-400">RELIABILITY</span>
          <span className="text-[11px] text-[var(--admin-text-muted)]">99.9999% effective uptime</span>
        </div>
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Your AI app can&apos;t afford to{" "}
          <span className="bg-gradient-to-r from-emerald-400 to-teal-400 bg-clip-text text-transparent">
            go down
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          The gateway automatically routes requests to healthy providers in real time.
          When one goes down, your traffic seamlessly fails over — your users never
          notice.
        </p>
        <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to="/signup"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            Start routing in minutes
            <ArrowRight size={16} />
          </Link>
          <Link
            to="/enterprise"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            <ShieldCheck size={16} />
            Talk to sales
          </Link>
        </div>
        <div className="mt-10 grid grid-cols-2 gap-4 sm:grid-cols-4">
          {STATS.map((s) => (
            <Card key={s.label} className="p-5 text-center">
              <div className="font-mono text-[20px] font-bold tabular-nums text-blue-400" style={{ fontFamily: MONO }}>
                {s.value}
              </div>
              <div className="mt-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--admin-text-muted)]">
                {s.label}
              </div>
            </Card>
          ))}
        </div>
      </section>

      {/* ── failover ── */}
      <section>
        <div className="mb-6 text-center">
          <span className="admin-label">How it works</span>
          <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Automatic failover in milliseconds
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Every request is health-checked in real time. The moment a provider starts
            failing, returning 5xx, or timing out, traffic is diverted to the next
            healthy one — on the same request.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {FAILOVER_STEPS.map((step) => {
            const Icon = step.icon;
            return (
              <Card key={step.title} className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
                <div className={`mb-4 flex h-10 w-10 items-center justify-center rounded-lg ${step.accent}`}>
                  <Icon className="h-5 w-5" />
                </div>
                <h3 className="mb-2 text-[15px] font-semibold text-[var(--admin-text)]">{step.title}</h3>
                <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{step.body}</p>
              </Card>
            );
          })}
        </div>
        <div className="mt-6 flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-[var(--admin-border)] bg-white/[0.02] p-5 sm:flex-row">
          <span className="text-[13px] font-medium text-[var(--admin-text-muted)]">Works with every request</span>
          <ArrowRight size={14} className="hidden text-[var(--admin-text-dim)] sm:block" />
          <code className="rounded-md bg-[var(--admin-surface)] px-3 py-1 font-mono text-[12px] text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
            POST /v1/chat/completions
          </code>
          <ArrowRight size={14} className="hidden text-[var(--admin-text-dim)] sm:block" />
          <span className="text-[13px] font-medium text-[var(--admin-text)]">No SDK changes. No config.</span>
        </div>
      </section>

      {/* ── features ── */}
      <section>
        <div className="mb-6 text-center">
          <span className="admin-label">What&apos;s included</span>
          <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Built for production traffic
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Reliability is the default — not an add-on. Every account gets the full
            routing engine.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((feature) => {
            const Icon = feature.icon;
            return (
              <Card key={feature.title} className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
                <div className={`mb-4 flex h-10 w-10 items-center justify-center rounded-lg ${feature.accent}`}>
                  <Icon className="h-5 w-5" />
                </div>
                <h3 className="mb-2 text-[15px] font-semibold text-[var(--admin-text)]">{feature.title}</h3>
                <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{feature.body}</p>
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── CTA ── */}
      <section>
        <div className="overflow-hidden rounded-2xl border border-emerald-500/20 p-8 text-center sm:p-12" style={{ background: "linear-gradient(to bottom right, rgba(16,185,129,0.04), transparent)" }}>
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Stop babysitting provider dashboards
          </h2>
          <p className="mx-auto mt-3 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            Switch your base URL to the gateway and get automatic failover, real-time
            health monitoring, and uptime reporting across 25+ providers — in one line
            of code.
          </p>
          <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link
              to="/signup"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
            >
              Get started free
              <ArrowRight size={16} />
            </Link>
            <Link
              to="/enterprise"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              Talk to sales
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
