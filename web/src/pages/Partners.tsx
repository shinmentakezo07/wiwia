// Partners — infrastructure behind the gateway. Adapted from the llmgateway.io
// partners page with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Globe2,
  Handshake,
  Leaf,
  MapPin,
  Plug,
  ShieldCheck,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const STATS = [
  { value: "40+", label: "providers" },
  { value: "200+", label: "models" },
  { value: "50B+", label: "tokens routed" },
  { value: "99.99%", label: "uptime SLA" },
];

const TRUST_CHIPS: { icon: LucideIcon; label: string }[] = [
  { icon: Leaf, label: "Renewable-powered infrastructure" },
  { icon: ShieldCheck, label: "No training on API data" },
  { icon: ShieldCheck, label: "Zero data retention" },
  { icon: ShieldCheck, label: "SOC 2 Type II" },
  { icon: ShieldCheck, label: "ISO 27001" },
];

const ENDPOINTS = [
  {
    title: "General purpose",
    blurb: "Standard OpenAI-compatible inference for open models — the default deployment.",
    modelCount: 12,
  },
  {
    title: "Turbo",
    blurb:
      "Accelerated deployment tier for latency-sensitive workloads, served from the same Sydney infrastructure.",
    modelCount: 8,
  },
];

const PARTNER_BENEFITS: { icon: LucideIcon; title: string; blurb: string }[] = [
  {
    icon: Globe2,
    title: "Distribution",
    blurb:
      "Your deployment is listed across the models directory, provider pages, and rankings — in front of every developer already routing through the gateway.",
  },
  {
    icon: Plug,
    title: "One integration",
    blurb:
      "An OpenAI-compatible endpoint is all it takes. Routing, failover, caching, and billing are handled by the gateway.",
  },
  {
    icon: BarChart3,
    title: "Transparent performance",
    blurb:
      "Uptime, time-to-first-token, and throughput are measured on real traffic and published live on your provider page.",
  },
];

const SCX_MODELS = [
  { name: "Llama 3.1 70B", turbo: false, contextSize: 128000, inputPerM: "$0.59", outputPerM: "$0.79" },
  { name: "Llama 3.1 8B", turbo: false, contextSize: 128000, inputPerM: "$0.05", outputPerM: "$0.08" },
  { name: "Mistral 7B", turbo: false, contextSize: 32000, inputPerM: "$0.08", outputPerM: "$0.14" },
  { name: "Qwen 2.5 72B", turbo: true, contextSize: 128000, inputPerM: "$0.35", outputPerM: "$0.40" },
  { name: "DeepSeek R1", turbo: true, contextSize: 64000, inputPerM: "$0.14", outputPerM: "$0.28" },
  { name: "Gemma 2 27B", turbo: false, contextSize: 8000, inputPerM: "$0.10", outputPerM: "$0.12" },
];

const compactNumber = new Intl.NumberFormat("en", { notation: "compact" });

// Southern Cross star positions (echo the Australian flag)
const crossStars = [
  { x: 96, y: 12, r: 7 },
  { x: 40, y: 74, r: 6 },
  { x: 88, y: 150, r: 8 },
  { x: 148, y: 66, r: 6 },
  { x: 118, y: 96, r: 3.5 },
];

function starPath(x: number, y: number, r: number): string {
  const inner = r / 4;
  return [
    `M${x} ${y - r}`,
    `L${x + inner} ${y - inner}`,
    `L${x + r} ${y}`,
    `L${x + inner} ${y + inner}`,
    `L${x} ${y + r}`,
    `L${x - inner} ${y + inner}`,
    `L${x - r} ${y}`,
    `L${x - inner} ${y - inner}`,
    "Z",
  ].join(" ");
}

export function PartnersPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <Badge tone="green">
          <Handshake size={12} className="mr-1.5" />
          Partners
        </Badge>
        <h1 className="mt-4 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          The infrastructure behind the{" "}
          <span className="bg-gradient-to-r from-emerald-400 to-teal-400 bg-clip-text text-transparent">
            gateway
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          The gateway routes one OpenAI-compatible API across 40+ providers and 200+
          models. Partners are the inference platforms running that capacity —
          integrated, measured on real traffic, and billed through a single endpoint.
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-3">
          {STATS.map((s, i) => (
            <div key={s.label} className="flex items-baseline gap-1.5">
              {i > 0 && <span className="mr-3 text-[var(--admin-text-dim)]">•</span>}
              <span className="font-mono text-[20px] font-bold tabular-nums text-[var(--admin-text)]">
                {s.value}
              </span>
              <span className="text-[13px] text-[var(--admin-text-muted)]">{s.label}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ── featured partner ── */}
      <section>
        <div className="mb-4 text-center">
          <h2 className="text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Launch partner
          </h2>
          <p className="mt-1 text-[14px] text-[var(--admin-text-muted)]">
            The first partner in the program, serving open models from Australia.
          </p>
        </div>
        <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-[#1a1a2e] p-8 md:p-10">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "radial-gradient(70% 60% at 85% 0%, rgba(45,212,191,0.14) 0%, transparent 60%), radial-gradient(60% 50% at 0% 100%, rgba(251,191,36,0.08) 0%, transparent 55%)",
            }}
          />
          <svg
            aria-hidden
            viewBox="0 0 200 200"
            className="pointer-events-none absolute right-4 top-4 h-32 w-32 text-white/25"
          >
            {crossStars.map((star) => (
              <path key={`${star.x}-${star.y}`} fill="currentColor" d={starPath(star.x, star.y, star.r)} />
            ))}
          </svg>
          <div className="relative grid gap-8 lg:grid-cols-[1.15fr_1fr]">
            <div className="space-y-5">
              <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-widest text-teal-300/90">
                <MapPin size={14} />
                Sydney, Australia
              </div>
              <h3 className="text-[24px] font-bold tracking-tight text-white">SCX.ai</h3>
              <p className="max-w-xl text-[14px] leading-relaxed text-white/70">
                SCX.ai is an Australian sovereign AI platform serving open models from
                renewable-powered infrastructure. It routes through the gateway as two
                OpenAI-compatible deployments — a general-purpose endpoint and a Turbo
                endpoint built for latency-sensitive workloads — giving teams in the
                region local inference without leaving the gateway API.
              </p>
              <ul className="flex flex-wrap gap-2">
                {TRUST_CHIPS.map((chip) => (
                  <li
                    key={chip.label}
                    className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-[11px] text-white/80"
                  >
                    <chip.icon className="h-3 w-3 text-teal-300" />
                    {chip.label}
                  </li>
                ))}
              </ul>
              <a
                href="https://scx.ai"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-[14px] font-medium text-teal-300 transition-colors hover:text-teal-200"
              >
                scx.ai
                <ArrowUpRight size={14} />
              </a>
            </div>
            <div className="flex flex-col justify-center gap-3">
              {ENDPOINTS.map((endpoint) => (
                <div
                  key={endpoint.title}
                  className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 transition-colors hover:border-white/25 hover:bg-white/[0.08]"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[15px] font-semibold text-white">{endpoint.title}</span>
                    {endpoint.title === "Turbo" && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-400/15 px-2.5 py-0.5 text-[11px] font-medium text-amber-300">
                        <Zap size={12} />
                        Turbo
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-[13px] text-white/60">{endpoint.blurb}</p>
                  <div className="mt-4 flex items-center justify-between text-[13px]">
                    <span className="tabular-nums text-white/70">
                      {endpoint.modelCount} {endpoint.modelCount === 1 ? "model" : "models"}
                    </span>
                    <span className="inline-flex items-center gap-1 font-medium text-teal-300">
                      View endpoint
                      <ArrowRight size={14} />
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── models served ── */}
      <section>
        <div className="mb-4 text-center">
          <h2 className="text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Most-used models on SCX
          </h2>
          <p className="mt-1 text-[14px] text-[var(--admin-text-muted)]">
            Ordered by token volume routed through the gateway over the last 30 days.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {SCX_MODELS.map((model, index) => (
            <Card key={model.name} className="flex flex-col p-5 transition-colors hover:border-[var(--admin-border-hover)]">
              <div className="flex items-center gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] text-[13px] font-bold text-[var(--admin-text)]">
                  {model.name.charAt(0)}
                </span>
                <span className="text-[14px] font-semibold leading-tight text-[var(--admin-text)]">{model.name}</span>
                <span className="ml-auto font-mono text-[11px] font-bold tabular-nums text-[var(--admin-text-dim)]">
                  #{index + 1}
                </span>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-[var(--admin-text-muted)]">
                {model.turbo ? (
                  <span className="inline-flex items-center gap-1 font-medium text-amber-400">
                    <Zap size={12} />
                    Turbo
                  </span>
                ) : (
                  <span>General purpose</span>
                )}
                <span className="tabular-nums">{compactNumber.format(model.contextSize)} context</span>
              </div>
              <div className="mt-3 flex items-center justify-between border-t border-[var(--admin-border)] pt-3 text-[12px]">
                <span className="tabular-nums text-[var(--admin-text-muted)]">
                  {model.inputPerM} in · {model.outputPerM} out /M
                </span>
              </div>
            </Card>
          ))}
        </div>
        <div className="mt-6 text-center">
          <Link
            to="/providers"
            className="inline-flex items-center gap-1.5 text-[14px] font-medium text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
          >
            All {SCX_MODELS.length} SCX models
            <ArrowRight size={14} />
          </Link>
        </div>
      </section>

      {/* ── become a partner ── */}
      <section>
        <div className="mb-6 text-center">
          <h2 className="text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Run your models on the gateway
          </h2>
          <p className="mt-1 text-[14px] text-[var(--admin-text-muted)]">
            Inference providers join the catalogue with one integration and get measured
            the same way as everyone else.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {PARTNER_BENEFITS.map((benefit) => {
            const Icon = benefit.icon;
            return (
              <Card key={benefit.title} className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
                <Icon className="h-5 w-5 text-emerald-400" />
                <h3 className="mt-3 text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                  {benefit.title}
                </h3>
                <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                  {benefit.blurb}
                </p>
              </Card>
            );
          })}
        </div>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to="/add-provider"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            List your provider
            <ArrowRight size={16} />
          </Link>
          <Link
            to="/providers"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            Browse all providers
          </Link>
        </div>
      </section>
    </div>
  );
}
