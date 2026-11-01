// Pricing — the real model: the software is free and MIT-licensed, you pay
// your providers directly, and enterprise support is a conversation. There is
// no hosted billing product, so this page does not invent one.

import { Link } from "react-router-dom";
import {
  ArrowRight,
  Building2,
  Check,
  ChevronDown,
  CreditCard,
  Server,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";
import { HERO_BEAMS_COMPACT, HeroBeamBackdrop } from "@/components/HeroBeamBackdrop";

interface Plan {
  icon: LucideIcon;
  name: string;
  tag: string;
  price: string;
  priceNote: string;
  summary: string;
  features: string[];
  cta: { label: string; to: string };
  featured?: boolean;
}

const PLANS: Plan[] = [
  {
    icon: Server,
    name: "Self-hosted",
    tag: "Everything, free",
    price: "$0",
    priceNote: "forever",
    summary:
      "Run the gateway on your own machine or server. You pay your providers at their rates — wiwi adds no per-token fee.",
    features: [
      "All 13 provider types and every inbound dialect",
      "Virtual keys, budgets, rate limits, and key pools",
      "Retries, cooldowns, fallbacks, and the optional health healer",
      "Request logs, usage analytics, Prometheus metrics, audit trail",
      "SQLite by default, Postgres and Redis when you want them",
      "MIT-licensed source — fork it, patch it, ship it",
    ],
    cta: { label: "Read the quickstart", to: "/docs" },
    featured: true,
  },
  {
    icon: Building2,
    name: "Enterprise",
    tag: "Support & custom work",
    price: "Custom",
    priceNote: "talk to us",
    summary:
      "For teams that want help running the gateway or need a provider adapter, translation rule, or deployment shape that does not exist yet.",
    features: [
      "Deployment and migration assistance",
      "Custom provider adapter or wire-format work",
      "Priority triage on issues that block you",
      "Architecture review for multi-tenant or high-volume setups",
      "No seat licence and no platform fee",
    ],
    cta: { label: "Contact us", to: "/contact" },
  },
];

const COMPARE: { label: string; selfHosted: string; enterprise: string }[] = [
  { label: "Price", selfHosted: "Free", enterprise: "Custom" },
  { label: "Licence", selfHosted: "MIT", enterprise: "MIT" },
  { label: "Platform fee", selfHosted: "None", enterprise: "None" },
  { label: "Provider cost", selfHosted: "Paid to providers", enterprise: "Paid to providers" },
  { label: "Self-hosting", selfHosted: "Yes", enterprise: "Yes" },
  { label: "Community support", selfHosted: "GitHub", enterprise: "Direct" },
  { label: "Custom adapter work", selfHosted: "Your team", enterprise: "Included" },
  { label: "Deployment assistance", selfHosted: "Docs", enterprise: "Included" },
];

const FAQ: { q: string; a: string }[] = [
  {
    q: "Is the gateway really free?",
    a: "Yes. The source is MIT-licensed and the whole platform — gateway, console, and public site — is in the repository. There is no paid tier of the software and no per-token markup.",
  },
  {
    q: "What do I actually pay for?",
    a: "Your provider usage, billed by the provider to your own account. wiwi forwards the request and reports the cost it computed from the pricing table; it never sits between you and the provider's invoice.",
  },
  {
    q: "Do I need a hosted account?",
    a: "No. You can run the gateway locally with SQLite in a few seconds. A hosted deployment is just the same binary with a Postgres URL and your provider keys in environment variables.",
  },
  {
    q: "What does Enterprise include?",
    a: "Hands-on help rather than a different build: deployment and migration work, custom provider adapters, priority triage, and architecture review. The software stays the same MIT-licensed code.",
  },
  {
    q: "Can I try it without installing anything?",
    a: "The playground on this site talks to a live gateway, and the docs walk through the three commands needed to run your own. Nothing requires a credit card.",
  },
];

export function PricingPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-12 pb-16">
      <section className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] px-6 py-12 text-center sm:px-10">
        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} />
        <div className="relative">
          <Badge tone="green">Free and open source</Badge>
          <h1 className="mt-4 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
            Pay your providers.{" "}
            <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
              Not a gateway tax.
            </span>
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            The gateway is MIT-licensed and runs on your infrastructure. There is no
            platform fee, no credit balance, and no per-token markup — just the provider
            bill you already pay.
          </p>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[13px] text-[var(--admin-text-muted)]">
            <span className="flex items-center gap-1.5">
              <Check size={14} className="text-emerald-400" /> No platform fee
            </span>
            <span className="flex items-center gap-1.5">
              <Check size={14} className="text-emerald-400" /> No credit card
            </span>
            <span className="flex items-center gap-1.5">
              <Check size={14} className="text-emerald-400" /> No seat licence
            </span>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        {PLANS.map((plan) => {
          const Icon = plan.icon;
          return (
            <Card
              key={plan.name}
              className={`flex h-full flex-col p-6 ${
                plan.featured ? "border-blue-500/30 bg-blue-500/[0.03]" : ""
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-[var(--admin-border)] bg-white/[0.02]">
                  <Icon size={18} className="text-blue-400" />
                </div>
                {plan.featured && <Badge tone="blue">{plan.tag}</Badge>}
                {!plan.featured && <span className="admin-label">{plan.tag}</span>}
              </div>
              <h2 className="mt-4 text-[18px] font-semibold text-[var(--admin-text)]">{plan.name}</h2>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="font-mono text-[30px] font-semibold tracking-tight text-[var(--admin-text)]">
                  {plan.price}
                </span>
                <span className="text-[13px] text-[var(--admin-text-muted)]">{plan.priceNote}</span>
              </div>
              <p className="mt-3 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {plan.summary}
              </p>
              <ul className="mt-5 space-y-2.5">
                {plan.features.map((feature) => (
                  <li key={feature} className="flex items-start gap-2 text-[13px] text-[var(--admin-text-muted)]">
                    <Check size={14} className="mt-0.5 shrink-0 text-emerald-400" />
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
              <Link
                to={plan.cta.to}
                className={`mt-6 inline-flex min-h-11 items-center justify-center gap-2 rounded-[10px] px-4 text-[14px] font-medium transition-colors ${
                  plan.featured
                    ? "bg-gradient-to-b from-brand-500 to-brand-700 text-white hover:brightness-110"
                    : "border border-[var(--admin-border)] bg-white/[0.02] text-[var(--admin-text)] hover:bg-white/[0.04]"
                }`}
              >
                {plan.cta.label}
                <ArrowRight size={15} />
              </Link>
            </Card>
          );
        })}
      </section>

      <section>
        <h2 className="mb-4 text-center text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Side by side
        </h2>
        <Card className="overflow-hidden p-0">
          <div className="admin-scroll overflow-x-auto">
            <table className="w-full min-w-[32rem] text-left text-[13px]">
              <thead className="bg-white/[0.02]">
                <tr>
                  <th className="px-5 py-3.5 font-semibold text-[var(--admin-text)]">Feature</th>
                  <th className="px-5 py-3.5 text-center font-semibold text-blue-400">Self-hosted</th>
                  <th className="px-5 py-3.5 text-center font-semibold text-[var(--admin-text)]">Enterprise</th>
                </tr>
              </thead>
              <tbody>
                {COMPARE.map((row) => (
                  <tr key={row.label} className="border-t border-[var(--admin-border)]">
                    <td className="px-5 py-3 text-[var(--admin-text-muted)]">{row.label}</td>
                    <td className="px-5 py-3 text-center text-[var(--admin-text)]">{row.selfHosted}</td>
                    <td className="px-5 py-3 text-center text-[var(--admin-text)]">{row.enterprise}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        {[
          { icon: CreditCard, title: "No billing layer", body: "wiwi never holds a balance or resells tokens. Your provider account is the only account." },
          { icon: ShieldCheck, title: "No lock-in", body: "MIT licence, standard SQL, and a config file you own. Leave whenever you want." },
          { icon: Terminal, title: "Runs in one process", body: "No worker, queue, or control plane to operate alongside the gateway." },
        ].map((item) => {
          const Icon = item.icon;
          return (
            <Card key={item.title} className="p-5">
              <Icon size={18} className="text-blue-400" />
              <h3 className="mt-3 text-[14px] font-semibold text-[var(--admin-text)]">{item.title}</h3>
              <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{item.body}</p>
            </Card>
          );
        })}
      </section>

      <section>
        <h2 className="mb-4 text-center text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Questions
        </h2>
        <div className="space-y-2">
          {FAQ.map((item) => (
            <details
              key={item.q}
              className="group rounded-[12px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-5 py-4 transition-colors hover:border-[var(--admin-border-hover)]"
            >
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-[14px] font-medium text-[var(--admin-text)]">
                {item.q}
                <ChevronDown size={18} className="shrink-0 text-[var(--admin-text-dim)] transition-transform group-open:rotate-180" />
              </summary>
              <p className="mt-3 border-l-2 border-white/[0.06] pl-4 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {item.a}
              </p>
            </details>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-8 text-center">
        <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Start with the quickstart
        </h2>
        <p className="mx-auto mt-2 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          Three commands: start the gateway, mint a virtual key, send any dialect.
        </p>
        <div className="mt-5 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link to="/docs" className="admin-btn admin-btn-primary">
            Read the docs
            <ArrowRight size={15} className="ml-2" />
          </Link>
          <Link to="/open-source" className="admin-btn admin-btn-ghost">
            Self-hosting
          </Link>
        </div>
      </section>
    </div>
  );
}
