// Reliability — how the gateway behaves when a key, a deployment, or a whole
// provider fails. Everything on this page maps to code in wiwi/router/ and
// wiwi/core/recovery.py; no invented uptime numbers.

import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Clock,
  Gauge,
  HeartPulse,
  Layers,
  RefreshCw,
  ServerCrash,
  ShieldCheck,
  Timer,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const STATS = [
  { value: "8", label: "Retryable statuses" },
  { value: "3", label: "Failover layers" },
  { value: "1 token", label: "Health probes" },
  { value: "0", label: "Manual intervention" },
];

const LAYERS: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: RefreshCw,
    title: "Key rotation",
    body: "Each provider account holds a pool of keys. Smooth weighted round-robin spreads requests, and a failing key is cooled down while its siblings keep serving.",
  },
  {
    icon: Layers,
    title: "Deployment fallback",
    body: "A model group can carry several deployments. When one provider is cooling or unavailable, the router picks the next healthy deployment for the same model.",
  },
  {
    icon: ServerCrash,
    title: "Model-group fallbacks",
    body: "Fallback chains move a request to another model group entirely when the primary group has no usable deployment left.",
  },
];

const FLOW: { step: string; title: string; body: string }[] = [
  {
    step: "01",
    title: "The upstream returns a retryable error",
    body: "408, 429, 500, 502, 503, 504, and 529 are classified as retryable. The attempt is recorded with the upstream status and message.",
  },
  {
    step: "02",
    title: "Backoff, then another key or deployment",
    body: "The router sleeps with exponential backoff (honouring Retry-After when the upstream sends it) and retries against the next eligible key.",
  },
  {
    step: "03",
    title: "The failing key cools down",
    body: "A key that keeps failing is marked cooling for a cooldown window, so later requests skip it instead of paying the failure again.",
  },
  {
    step: "04",
    title: "The healer restores it early",
    body: "When enabled, a background sweeper probes cooling keys and deployments with a one-token completion and returns them to service before the cooldown expires.",
  },
];

const FEATURES: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: Activity,
    title: "Per-key health state",
    body: "active, cooling, invalid, and disabled are tracked per key and shown live in the console, so you can see which credential is the problem.",
  },
  {
    icon: Gauge,
    title: "Latency-aware selection",
    body: "The router supports simple-shuffle, least-busy, and latency-based strategies; per-deployment weight is respected in every mode.",
  },
  {
    icon: Timer,
    title: "Retry-After aware",
    body: "When an upstream sends Retry-After, the cooldown uses it instead of guessing, so a throttled provider is not hammered.",
  },
  {
    icon: HeartPulse,
    title: "Opt-in health healer",
    body: "Probes spend real provider money, so the healer is off by default. Turn it on when faster recovery is worth the probe cost.",
  },
  {
    icon: ShieldCheck,
    title: "Probation, not instant trust",
    body: "A key restored by the healer re-enters at reduced weight until it proves itself, rather than taking a full share immediately.",
  },
  {
    icon: Clock,
    title: "Every attempt logged",
    body: "The request log records each attempt, its provider, status, latency, and error, so a failover is reconstructable after the fact.",
  },
];

export function ReliabilityPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-12 pb-16">
      <section className="text-center">
        <Badge tone="green">Routing & recovery</Badge>
        <h1 className="mt-4 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          When a provider fails,{" "}
          <span className="bg-gradient-to-r from-emerald-400 to-blue-400 bg-clip-text text-transparent">
            the request does not.
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Retries, per-key cooldowns, deployment fallbacks, and an optional health healer
          keep traffic moving without a manual failover.
        </p>
        <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {STATS.map((stat) => (
            <div key={stat.label} className="rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-3 py-4">
              <div className="font-mono text-2xl font-semibold tabular-nums text-emerald-400">
                {stat.value}
              </div>
              <div className="mt-1 text-[11px] uppercase tracking-[0.12em] text-[var(--admin-text-muted)]">
                {stat.label}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Three layers of failover
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          {LAYERS.map((layer) => {
            const Icon = layer.icon;
            return (
              <Card key={layer.title} className="p-5">
                <Icon size={18} className="text-emerald-400" />
                <h3 className="mt-3 text-[15px] font-semibold text-[var(--admin-text)]">{layer.title}</h3>
                <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{layer.body}</p>
              </Card>
            );
          })}
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          What happens on a failure
        </h2>
        <div className="space-y-3">
          {FLOW.map((item) => (
            <Card key={item.step} className="flex gap-4 p-5">
              <span className="font-mono text-[12px] font-semibold tracking-[0.12em] text-emerald-400">
                {item.step}
              </span>
              <div>
                <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">{item.title}</h3>
                <p className="mt-1 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{item.body}</p>
              </div>
            </Card>
          ))}
        </div>
        <p className="mt-3 flex items-start gap-2 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
          <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-emerald-400" />
          The exact retry policy is configured under router_settings; the numbers above
          describe the defaults, not a service-level guarantee.
        </p>
      </section>

      <section>
        <h2 className="mb-4 text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Recovery you can observe
        </h2>
        <div className="grid gap-4 md:grid-cols-2">
          {FEATURES.map((feature) => {
            const Icon = feature.icon;
            return (
              <Card key={feature.title} className="p-5">
                <div className="flex items-center gap-2">
                  <Icon size={16} className="text-blue-400" />
                  <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">{feature.title}</h3>
                </div>
                <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{feature.body}</p>
              </Card>
            );
          })}
        </div>
      </section>

      <section className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-8 text-center">
        <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          See the state of every key
        </h2>
        <p className="mx-auto mt-2 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          The Providers page shows key health, cooldowns, and per-key request and error
          counters, updated live over SSE.
        </p>
        <div className="mt-5 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link to="/console/providers" className="admin-btn admin-btn-primary">
            Open the console
            <ArrowRight size={15} className="ml-2" />
          </Link>
          <Link to="/docs" className="admin-btn admin-btn-ghost">
            Read about routing
          </Link>
        </div>
      </section>
    </div>
  );
}
