// Pricing — three deployment tiers (Self-host, BYOK, Cloud) plus an FAQ
// accordion. Matches the dark design system shared with the admin console.

import { Link } from "react-router-dom";
import { ArrowRight, Check, KeyRound, Server, Wallet } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

interface Tier {
  icon: LucideIcon;
  name: string;
  price: string;
  priceNote: string;
  features: string[];
  cta: { label: string; to: string };
  featured?: boolean;
}

const TIERS: Tier[] = [
  {
    icon: Server,
    name: "Self-host",
    price: "Free forever",
    priceNote: "AGPLv3 — run it anywhere",
    features: [
      "Full gateway, dashboard, and API on your own infra",
      "Three inbound dialects, every outbound provider",
      "Virtual keys, budgets, key pools, retries, fallbacks",
      "Cost tracking, request logs, live observability",
      "No seats, no minimums, no token markup",
    ],
    cta: { label: "Read the docs", to: "/docs" },
  },
  {
    icon: KeyRound,
    name: "Bring your own keys",
    price: "Free",
    priceNote: "Pay providers directly",
    features: [
      "Route through your own provider API keys",
      "Unified analytics, caching, and failover included",
      "Virtual keys with per-key budgets and limits",
      "No platform fee — you pay your providers",
      "One bill across all your provider accounts",
    ],
    cta: { label: "Create an account", to: "/signup" },
  },
  {
    icon: Wallet,
    name: "Cloud",
    price: "Pay-as-you-go",
    priceNote: "Provider rates, no markup on tokens",
    features: [
      "Managed gateway — no infra to run",
      "Pay per-token at each provider's own rates",
      "Automatic failover and health monitoring",
      "New models within 48 hours of release",
      "One bill across 40+ providers",
    ],
    cta: { label: "Get started", to: "/signup" },
    featured: true,
  },
];

interface FaqItem {
  question: string;
  answer: string;
}

const FAQ_ITEMS: FaqItem[] = [
  {
    question: "How much does wiwi cost?",
    answer:
      "Self-hosting and bring-your-own-keys are both free forever. On the managed cloud plan you pay per-token at each provider's own rates with no markup on the tokens themselves. There are no seats, no minimums, and no subscription.",
  },
  {
    question: "Is there a fee when I bring my own API keys?",
    answer:
      "No platform fee at all. With your own provider keys (BYOK), routing through wiwi is free — you pay your providers directly and still get unified analytics, caching, and automatic failover.",
  },
  {
    question: "Can I self-host wiwi?",
    answer:
      "Yes. wiwi is open source under AGPLv3. Deploy the full gateway on your own infrastructure with a single Docker command — the routing layer, virtual keys, budgets, and admin dashboard are all yours to run.",
  },
  {
    question: "What providers are supported?",
    answer:
      "OpenAI, Anthropic, Gemini, OpenRouter, and any OpenAI-compatible URL. Adding a new provider is one adapter module — core code never branches on provider name. You can mix and match providers within a single key pool with weighted round-robin.",
  },
];

export function PricingPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Three ways to run it.{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            Two are free.
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          No seats, no minimums, no token markup. Start free and only pay for tokens
          when you use the managed cloud.
        </p>
      </section>

      {/* ── tiers ── */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {TIERS.map((tier) => {
          const Icon = tier.icon;
          return (
            <Card
              key={tier.name}
              className={`relative flex flex-col p-6 transition-colors hover:border-[var(--admin-border-hover)] ${
                tier.featured ? "ring-1 ring-blue-500/20" : ""
              }`}
            >
              {tier.featured && (
                <span className="admin-badge admin-badge-blue absolute -top-2.5 left-1/2 -translate-x-1/2">
                  Most popular
                </span>
              )}
              <div className="mb-4 flex items-center gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px] bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
                  <Icon className="h-5 w-5" style={{ color: "rgba(59,130,246,0.85)" }} />
                </span>
                <h3 className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                  {tier.name}
                </h3>
              </div>
              <div className="mb-1">
                <span className="font-mono text-[20px] font-semibold text-blue-400" style={{ fontFamily: MONO }}>
                  {tier.price}
                </span>
              </div>
              <p className="mb-5 text-[12px] text-[var(--admin-text-dim)]">{tier.priceNote}</p>
              <ul className="mb-6 flex-1 space-y-2.5">
                {tier.features.map((f) => (
                  <li key={f} className="flex items-start gap-2">
                    <Check size={14} className="mt-0.5 shrink-0 text-emerald-400" />
                    <span className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{f}</span>
                  </li>
                ))}
              </ul>
              <Link
                to={tier.cta.to}
                className={`inline-flex h-10 items-center justify-center gap-2 rounded-[10px] text-[13px] font-medium transition-[filter] duration-150 hover:brightness-110 ${
                  tier.featured
                    ? "bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-white shadow-lg shadow-brand-600/20"
                    : "border border-white/[0.08] bg-white/[0.02] px-5 text-[var(--admin-text)] hover:bg-white/[0.04]"
                }`}
              >
                {tier.cta.label}
                <ArrowRight size={14} />
              </Link>
            </Card>
          );
        })}
      </section>

      {/* ── FAQ ── */}
      <section>
        <div className="mb-6 text-center">
          <span className="admin-label">Pricing FAQ</span>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Questions, answered
          </h2>
        </div>
        <div className="space-y-3">
          {FAQ_ITEMS.map((item) => (
            <details
              key={item.question}
              className="group rounded-[12px] border border-[var(--admin-border)] bg-[var(--admin-surface)] transition-colors hover:border-[var(--admin-border-hover)] [&_summary]:list-none"
            >
              <summary className="flex cursor-pointer items-center justify-between gap-3 px-5 py-4 text-[14px] font-medium text-[var(--admin-text)]">
                {item.question}
                <span className="text-[var(--admin-text-dim)] transition-transform duration-150 group-open:rotate-45">
                  <span className="text-[18px] leading-none">+</span>
                </span>
              </summary>
              <div className="border-t border-[var(--admin-border)] px-5 py-4">
                <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                  {item.answer}
                </p>
              </div>
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}
