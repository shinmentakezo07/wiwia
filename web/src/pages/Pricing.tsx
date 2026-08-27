// Pricing — three deployment tiers (Self-host, BYOK, Cloud), a feature
// comparison matrix, FAQ accordion, and a closing CTA. Matches the dark
// design system shared with the admin console; reuses the animated beam
// backdrop and ambient glow language from the Landing page.

import { useId } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Check,
  KeyRound,
  Minus,
  Server,
  Sparkles,
  Wallet,
} from "lucide-react";
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

// ── comparison matrix ────────────────────────────────────────────────────

type Cell = boolean | "partial" | string;
interface CompareRow {
  label: string;
  self: Cell;
  byok: Cell;
  cloud: Cell;
}
interface CompareGroup {
  heading: string;
  rows: CompareRow[];
}

const COMPARE: CompareGroup[] = [
  {
    heading: "Core",
    rows: [
      { label: "Unified gateway & API", self: true, byok: true, cloud: true },
      { label: "Admin dashboard", self: true, byok: true, cloud: true },
      { label: "Virtual keys & budgets", self: true, byok: true, cloud: true },
      { label: "Open source (AGPLv3)", self: true, byok: true, cloud: "Managed" },
      { label: "Infrastructure", self: "Yours", byok: "Yours", cloud: "Managed" },
    ],
  },
  {
    heading: "Routing & reliability",
    rows: [
      { label: "Key pools & weighted round-robin", self: true, byok: true, cloud: true },
      { label: "Retries & automatic failover", self: true, byok: true, cloud: true },
      { label: "Prompt caching", self: true, byok: true, cloud: true },
      { label: "Health monitoring", self: true, byok: true, cloud: true },
      { label: "New models within 48h", self: "Manual", byok: "Manual", cloud: true },
    ],
  },
  {
    heading: "Cost & billing",
    rows: [
      { label: "Platform fee", self: "$0", byok: "$0", cloud: "$0" },
      { label: "Token markup", self: "None", byok: "None", cloud: "None" },
      { label: "Per-token cost tracking", self: true, byok: true, cloud: true },
      { label: "One bill across providers", self: "Self", byok: true, cloud: true },
      { label: "Seats / minimums", self: "None", byok: "None", cloud: "None" },
    ],
  },
  {
    heading: "Support",
    rows: [
      { label: "Community support", self: true, byok: true, cloud: true },
      { label: "Request logs & analytics", self: true, byok: true, cloud: true },
      { label: "Data retention", self: "Unlimited", byok: "Unlimited", cloud: "Unlimited" },
      { label: "SLA guarantee", self: false, byok: false, cloud: "99.99%" },
    ],
  },
];

const COLS = [
  { key: "self" as const, name: "Self-host" },
  { key: "byok" as const, name: "BYOK" },
  { key: "cloud" as const, name: "Cloud" },
];

function CellMark({ value }: { value: Cell }) {
  if (value === true)
    return <Check size={15} className="mx-auto text-emerald-400" />;
  if (value === false)
    return <Minus size={15} className="mx-auto text-[var(--admin-text-dim)]" />;
  if (value === "partial")
    return <span className="text-[var(--admin-text-muted)]">~</span>;
  return (
    <span className="font-mono text-[12px] text-[var(--admin-text-muted)]" style={{ fontFamily: MONO }}>
      {value}
    </span>
  );
}

// ── FAQ ──────────────────────────────────────────────────────────────────

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

// ── hero beam backdrop (compact, local) ───────────────────────────────────

const HERO_BEAMS = [
  { d: "M 0,120 Q 300,60 600,140 T 1200,120", dur: 10, delay: 0, w: 1.5, c0: "#3b82f6", c1: "#8b5cf6" },
  { d: "M 0,220 Q 250,160 500,240 T 1200,200", dur: 12, delay: 1.5, w: 1, c0: "#8b5cf6", c1: "#ec4899" },
  { d: "M 0,320 Q 350,260 700,340 T 1200,300", dur: 14, delay: 0.8, w: 1.5, c0: "#22d3ee", c1: "#3b82f6" },
  { d: "M 0,80 Q 400,20 800,100 T 1200,60", dur: 11, delay: 2, w: 1, c0: "#a78bfa", c1: "#22d3ee" },
] as const;

function HeroBeamBackdrop() {
  const id = useId().replace(/[:]/g, "");
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full opacity-60"
      viewBox="0 0 1200 400"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden
    >
      <defs>
        {HERO_BEAMS.map((b, i) => (
          <linearGradient key={i} id={`pb-${id}-${i}`} gradientUnits="userSpaceOnUse">
            <stop stopColor={b.c0} stopOpacity="0">
              <animate attributeName="offset" values="-0.3;1" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
            <stop stopColor={b.c0}>
              <animate attributeName="offset" values="-0.1;1.1" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
            <stop offset="0.3" stopColor={b.c1}>
              <animate attributeName="offset" values="0;1.3" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
            <stop offset="1" stopColor={b.c1} stopOpacity="0">
              <animate attributeName="offset" values="0.3;1.6" dur={`${b.dur}s`} begin={`${b.delay}s`} repeatCount="indefinite" />
            </stop>
          </linearGradient>
        ))}
      </defs>
      {HERO_BEAMS.map((b, i) => (
        <g key={i}>
          <path d={b.d} fill="none" stroke="rgba(255,255,255,0.03)" strokeWidth={b.w} />
          <path d={b.d} fill="none" stroke={`url(#pb-${id}-${i})`} strokeWidth={b.w} strokeLinecap="round" />
        </g>
      ))}
    </svg>
  );
}

const TRUST_STATS = [
  { value: "40+", label: "Providers" },
  { value: "200+", label: "Models" },
  { value: "100B+", label: "Tokens routed" },
  { value: "99.9999%", label: "Uptime" },
];

const BADGES = ["No credit card required", "AGPLv3 open source", "Cancel anytime"];

export function PricingPage() {
  return (
    <div className="space-y-20 pb-20">
      {/* ════════ hero ════════ */}
      <section className="relative overflow-hidden pt-6 pb-2">
        <HeroBeamBackdrop />
        {/* soft radial glow behind the headline */}
        <div
          className="pointer-events-none absolute left-1/2 top-10 h-72 w-[640px] max-w-full -translate-x-1/2 rounded-full"
          style={{ background: "radial-gradient(circle, rgba(59,130,246,0.10) 0%, transparent 65%)" }}
          aria-hidden
        />
        <div className="relative z-10 mx-auto max-w-3xl text-center">
          <span className="admin-badge admin-badge-blue mb-5">
            <Sparkles size={12} />
            Pricing
          </span>
          <h1 className="text-4xl font-bold tracking-[-0.02em] text-[var(--admin-text)] sm:text-5xl md:text-6xl">
            Three ways to run it.{" "}
            <span className="bg-gradient-to-r from-blue-400 via-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
              Two are free.
            </span>
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base">
            No seats, no minimums, no token markup. Start free and only pay for
            tokens when you use the managed cloud.
          </p>
          {/* trust badges */}
          <div className="mt-7 flex flex-wrap items-center justify-center gap-2.5">
            {BADGES.map((b) => (
              <span
                key={b}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--admin-border)] bg-white/[0.02] px-3 py-1.5 text-[12px] text-[var(--admin-text-muted)]"
              >
                <Check size={12} className="text-emerald-400/80" />
                {b}
              </span>
            ))}
          </div>
        </div>
        {/* trust stats */}
        <div className="relative z-10 mx-auto mt-12 grid max-w-3xl grid-cols-2 gap-4 sm:grid-cols-4">
          {TRUST_STATS.map((s) => (
            <div key={s.label} className="text-center">
              <div
                className="font-mono text-2xl font-semibold text-[var(--admin-text)] sm:text-3xl"
                style={{ fontFamily: MONO }}
              >
                {s.value}
              </div>
              <div className="mt-1 text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--admin-text-dim)]">
                {s.label}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ════════ tiers ════════ */}
      <section className="scroll-reveal">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-5 md:grid-cols-3">
          {TIERS.map((tier, idx) => {
            const Icon = tier.icon;
            const featured = tier.featured;
            return (
              <div
                key={tier.name}
                className="admin-stagger"
                style={{ animationDelay: `${idx * 80}ms` }}
              >
                <Card
                  className={`relative flex h-full flex-col p-7 ${
                    featured
                      ? "ring-1 ring-blue-500/25"
                      : ""
                  }`}
                >
                  {featured && (
                    <>
                      {/* brand glow behind the featured card */}
                      <div
                        className="pointer-events-none absolute -inset-px rounded-[var(--admin-radius)] opacity-60"
                        style={{
                          background:
                            "radial-gradient(120% 80% at 50% 0%, rgba(59,130,246,0.10) 0%, transparent 60%)",
                        }}
                        aria-hidden
                      />
                      <span className="admin-badge admin-badge-blue absolute -top-3 left-1/2 -translate-x-1/2 shadow-lg shadow-blue-500/10">
                        <Sparkles size={12} />
                        Most popular
                      </span>
                    </>
                  )}
                  <div className="relative z-10 flex h-full flex-col">
                    <div className="mb-5 flex items-center gap-3">
                      <span
                        className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-[12px] ring-1 ring-white/[0.06] ${
                          featured
                            ? "bg-gradient-to-br from-blue-500/20 to-violet-500/20"
                            : "bg-gradient-to-br from-blue-500/10 to-violet-500/10"
                        }`}
                      >
                        <Icon
                          className="h-5 w-5"
                          style={{ color: featured ? "rgba(96,165,250,0.95)" : "rgba(59,130,246,0.85)" }}
                        />
                      </span>
                      <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                        {tier.name}
                      </h3>
                    </div>
                    <div className="mb-1">
                      <span
                        className="font-mono text-[22px] font-semibold text-blue-400"
                        style={{ fontFamily: MONO }}
                      >
                        {tier.price}
                      </span>
                    </div>
                    <p className="mb-5 text-[12px] text-[var(--admin-text-dim)]">{tier.priceNote}</p>
                    <div className="mb-6 h-px w-full bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />
                    <ul className="mb-7 flex-1 space-y-3">
                      {tier.features.map((f) => (
                        <li key={f} className="flex items-start gap-2.5">
                          <span
                            className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full ${
                              featured ? "bg-blue-500/15" : "bg-emerald-500/10"
                            }`}
                          >
                            <Check
                              size={11}
                              className={featured ? "text-blue-300" : "text-emerald-400"}
                            />
                          </span>
                          <span className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                            {f}
                          </span>
                        </li>
                      ))}
                    </ul>
                    <Link
                      to={tier.cta.to}
                      className={`inline-flex h-11 items-center justify-center gap-2 rounded-[10px] text-[13px] font-medium transition-[filter,background-color,border-color] duration-150 ${
                        featured
                          ? "bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-white shadow-lg shadow-brand-600/25 hover:brightness-110"
                          : "border border-white/[0.08] bg-white/[0.02] px-5 text-[var(--admin-text)] hover:border-white/[0.14] hover:bg-white/[0.04]"
                      }`}
                    >
                      {tier.cta.label}
                      <ArrowRight size={14} />
                    </Link>
                  </div>
                </Card>
              </div>
            );
          })}
        </div>
      </section>

      {/* ════════ comparison matrix ════════ */}
      <section className="scroll-reveal mx-auto max-w-6xl">
        <div className="mb-10 text-center">
          <span className="admin-label">Compare plans</span>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-3xl">
            Every feature, side by side
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-[14px] text-[var(--admin-text-muted)]">
            The same gateway core across every plan — pick where it runs and who
            handles the infrastructure.
          </p>
        </div>
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto admin-scroll">
            <table className="w-full min-w-[640px] border-collapse text-left">
              <thead>
                <tr className="border-b border-[var(--admin-border)]">
                  <th className="px-5 py-4 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--admin-text-dim)]">
                    Feature
                  </th>
                  {COLS.map((c, i) => (
                    <th
                      key={c.key}
                      className={`px-5 py-4 text-center text-[13px] font-semibold text-[var(--admin-text)] ${
                        i === COLS.length - 1 ? "text-blue-400" : ""
                      }`}
                    >
                      {c.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {COMPARE.map((group) => (
                  <GroupBlock key={group.heading} group={group} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </section>

      {/* ════════ FAQ ════════ */}
      <section className="scroll-reveal mx-auto max-w-4xl">
        <div className="mb-8 text-center">
          <span className="admin-label">Pricing FAQ</span>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-3xl">
            Questions, answered
          </h2>
        </div>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {FAQ_ITEMS.map((item) => (
            <details
              key={item.question}
              className="group rounded-[12px] border border-[var(--admin-border)] bg-[var(--admin-surface)] transition-colors hover:border-[var(--admin-border-hover)] [&_summary]:list-none"
            >
              <summary className="flex cursor-pointer items-center justify-between gap-3 px-5 py-4 text-[14px] font-medium text-[var(--admin-text)]">
                {item.question}
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-[var(--admin-border)] text-[var(--admin-text-dim)] transition-all duration-200 group-open:rotate-45 group-open:border-blue-500/30 group-open:text-blue-400">
                  <span className="text-[16px] leading-none">+</span>
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

      {/* ════════ final CTA ════════ */}
      <section className="scroll-reveal mx-auto max-w-5xl">
        <div className="relative overflow-hidden rounded-[var(--admin-radius-lg)] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-6 py-14 text-center sm:px-12">
          {/* ambient glow */}
          <div
            className="pointer-events-none absolute left-1/2 top-0 h-64 w-[520px] max-w-full -translate-x-1/2 rounded-full"
            style={{ background: "radial-gradient(circle, rgba(124,58,237,0.10) 0%, transparent 65%)" }}
            aria-hidden
          />
          <div className="relative z-10">
            <h2 className="text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-3xl">
              Ready to start?
            </h2>
            <p className="mx-auto mt-3 max-w-md text-[14px] text-[var(--admin-text-muted)]">
              Create a free account in seconds — no credit card, no commitment.
              Upgrade to managed cloud only if you want us to run it.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <Link
                to="/signup"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 text-[13px] font-medium text-white shadow-lg shadow-brand-600/25 transition-[filter] duration-150 hover:brightness-110"
              >
                Get started free
                <ArrowRight size={14} />
              </Link>
              <Link
                to="/docs"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.14] hover:bg-white/[0.04]"
              >
                Read the docs
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function GroupBlock({ group }: { group: CompareGroup }) {
  return (
    <>
      <tr className="border-b border-[var(--admin-border)] bg-white/[0.012]">
        <td
          colSpan={4}
          className="px-5 py-2.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--admin-text-dim)]"
        >
          {group.heading}
        </td>
      </tr>
      {group.rows.map((row, i) => (
        <tr
          key={row.label}
          className={`border-b border-[var(--admin-border)] transition-colors hover:bg-white/[0.015] ${
            i === group.rows.length - 1 ? "border-b-0" : ""
          }`}
        >
          <td className="px-5 py-3.5 text-[13px] text-[var(--admin-text-muted)]">
            {row.label}
          </td>
          {COLS.map((c) => (
            <td key={c.key} className="px-5 py-3.5 text-center">
              <CellMark value={row[c.key]} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
