// Pricing — three deployment tiers (Self-host, BYOK, Cloud), a "what you
// actually pay" receipt visual, a feature comparison matrix, FAQ accordion,
// and a closing CTA. Matches the dark design system shared with the admin
// console; reuses the animated beam backdrop and ambient glow language from
// the Landing page.

import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  Boxes,
  CalendarX2,
  Check,
  ChevronDown,
  CreditCard,
  GitBranch,
  Infinity as InfinityIcon,
  Info,
  KeyRound,
  Minus,
  Route,
  Server,
  ShieldCheck,
  Sparkles,
  Wallet,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { HERO_BEAMS_COMPACT, HeroBeamBackdrop } from "@/components/HeroBeamBackdrop";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

interface Tier {
  icon: LucideIcon;
  name: string;
  tag: string;
  /** The headline number — deliberately `$0` on every tier, because "no platform
   *  fee" is the claim this page exists to make. `payFor` says what you *do*
   *  pay, so the zero is never mistaken for "the product is free to run". */
  price: string;
  priceNote: string;
  payFor: string;
  features: string[];
  cta: { label: string; to: string };
  accent: string;
  featured?: boolean;
}

const TIERS: Tier[] = [
  {
    icon: Server,
    name: "Self-host",
    tag: "Open source",
    price: "$0",
    priceNote: "forever",
    payFor: "You pay for your own infrastructure",
    accent: "#34d399",
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
    tag: "No platform fee",
    price: "$0",
    priceNote: "platform fee",
    payFor: "You pay providers directly",
    accent: "#a855f7",
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
    tag: "Most popular",
    price: "$0",
    priceNote: "markup on tokens",
    payFor: "You pay each provider's own token rates",
    accent: "#60a5fa",
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

// ── hero trust content ───────────────────────────────────────────────────

const TRUST_STATS = [
  { icon: Boxes, value: "40+", label: "Providers" },
  { icon: Sparkles, value: "200+", label: "Models" },
  { icon: Activity, value: "100B+", label: "Tokens routed" },
  { icon: ShieldCheck, value: "99.9999%", label: "Uptime" },
];

const BADGES: { icon: LucideIcon; label: string }[] = [
  { icon: CreditCard, label: "No credit card required" },
  { icon: GitBranch, label: "AGPLv3 open source" },
  { icon: CalendarX2, label: "Cancel anytime" },
];

/** The three zeros this page argues for, restated as facts rather than claims. */
const ZERO_FACTS = [
  { label: "Platform fee", value: "$0" },
  { label: "Token markup", value: "0%" },
  { label: "Seats & minimums", value: "None" },
];

/** Sample month used by the receipt visual. Illustrative, and labelled as such. */
const SAMPLE_BILL = {
  provider: "$1,000.00",
  fee: "$0.00",
  total: "$1,000.00",
};

/** `rgba()` string from a 6-digit hex plus an alpha, for the per-tier washes. */
function withAlpha(hex: string, alpha: number) {
  const r = hex.slice(1, 3);
  const g = hex.slice(3, 5);
  const b = hex.slice(5, 7);
  return `rgba(${parseInt(r, 16)}, ${parseInt(g, 16)}, ${parseInt(b, 16)}, ${alpha})`;
}

// ── page ──────────────────────────────────────────────────────────────────

export function PricingPage() {
  return (
    <div className="space-y-24 pb-24">
      {/* ════════ hero ════════ */}
      <section className="relative overflow-hidden pt-10 pb-2">
        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="opacity-60" />
        {/* drifting colour wash — gives the black a sense of depth behind the
            headline without competing with it for attention */}
        <div className="wiwi-aurora pointer-events-none absolute -top-24 left-[12%] h-64 w-64 rounded-full opacity-40" style={{ background: "rgba(59,130,246,0.22)" }} aria-hidden />
        <div className="wiwi-aurora pointer-events-none absolute -top-10 right-[10%] h-56 w-56 rounded-full opacity-40" style={{ background: "rgba(168,85,247,0.18)", animationDelay: "-7s" }} aria-hidden />
        <div
          className="pointer-events-none absolute left-1/2 top-16 h-80 w-[720px] max-w-full -translate-x-1/2 rounded-full"
          style={{ background: "radial-gradient(circle, rgba(59,130,246,0.10) 0%, transparent 65%)" }}
          aria-hidden
        />

        <div className="relative z-10 mx-auto max-w-3xl text-center">
          <span className="admin-badge admin-badge-blue mb-6 inline-flex">
            <Sparkles size={12} />
            Pricing
          </span>
          <h1 className="text-4xl font-bold leading-[1.05] tracking-[-0.03em] text-[var(--admin-text)] sm:text-5xl md:text-6xl">
            Three ways to run it.{" "}
            <span className="hero-gradient-text">Two are free.</span>
          </h1>
          <p className="mx-auto mt-6 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base">
            No seats, no minimums, no token markup. Start free and only pay for
            tokens when you use the managed cloud.
          </p>

          {/* trust badges */}
          <div className="mt-8 flex flex-wrap items-center justify-center gap-2.5">
            {BADGES.map(({ icon: Icon, label }) => (
              <span
                key={label}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--admin-border)] bg-white/[0.02] px-3.5 py-2 text-[12px] text-[var(--admin-text-muted)] transition-colors hover:border-[var(--admin-border-hover)] hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
              >
                <Icon size={12} className="text-emerald-400/80" aria-hidden />
                {label}
              </span>
            ))}
          </div>
        </div>

        {/* trust stats */}
        <div className="relative z-10 mx-auto mt-14 grid max-w-4xl grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          {TRUST_STATS.map(({ icon: Icon, value, label }) => (
            <div
              key={label}
              className="group relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-4 py-6 text-center transition-all duration-200 hover:-translate-y-0.5 hover:border-white/[0.12] hover:bg-white/[0.03] hover:shadow-lg hover:shadow-blue-500/5"
            >
              <span
                className="pointer-events-none absolute -top-10 left-1/2 h-20 w-20 -translate-x-1/2 rounded-full bg-brand-500/20 opacity-0 blur-2xl transition-opacity duration-300 group-hover:opacity-100"
                aria-hidden
              />
              <Icon className="relative mx-auto h-4 w-4 text-blue-400/70" aria-hidden />
              <div
                className="relative mt-3 bg-gradient-to-b from-[var(--admin-text)] to-[var(--admin-text-muted)] bg-clip-text text-2xl font-bold tabular-nums text-transparent sm:text-3xl"
                style={{ fontFamily: MONO }}
              >
                {value}
              </div>
              <div className="relative mt-1.5 text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--admin-text-dim)]">
                {label}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ════════ tiers ════════ */}
      <section className="scroll-reveal relative overflow-hidden">
        <div className="relative mb-10 text-center">
          <span className="admin-label">Plans</span>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-3xl">
            Pick where it runs, not what you pay
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            The same gateway in every plan — the only difference is whose
            infrastructure it runs on.
          </p>
        </div>

        <div className="relative mx-auto grid max-w-6xl grid-cols-1 gap-5 md:grid-cols-3 md:py-6">
          {TIERS.map((tier, idx) => (
            <div
              key={tier.name}
              className="admin-stagger relative"
              style={{ animationDelay: `${idx * 80}ms` }}
            >
              <TierCard tier={tier} />
            </div>
          ))}
        </div>
      </section>

      {/* ════════ what you actually pay ════════ */}
      <section className="scroll-reveal relative mx-auto max-w-5xl">
        <div
          className="pointer-events-none absolute inset-x-0 -top-10 h-40 opacity-70"
          style={{ background: "radial-gradient(60% 100% at 50% 100%, rgba(59,130,246,0.07) 0%, transparent 70%)" }}
          aria-hidden
        />
        <div className="relative mb-10 text-center">
          <span className="admin-label">The whole pitch, in one number</span>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-3xl">
            What you actually pay
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            The number that matters is the one on your provider bill. wiwi never
            changes it — routing through the gateway costs the same as not using it.
          </p>
        </div>

        <div className="relative grid grid-cols-1 gap-4 md:grid-cols-[1fr_auto_1fr] md:items-stretch md:gap-6">
          {/* dashed line connecting the two receipts through the equals badge —
              hidden below md because the cards stack with the badge between */}
          <span
            className="pointer-events-none absolute left-1/2 top-1/2 hidden h-px w-[calc(100%-11rem)] -translate-x-1/2 -translate-y-1/2 md:block"
            style={{
              backgroundImage:
                "linear-gradient(90deg, transparent 0%, rgba(59,130,246,0.35) 20%, rgba(59,130,246,0.35) 80%, transparent 100%)",
            }}
            aria-hidden
          />
          <Receipt title="Straight to your provider" subtitle="No gateway in the path" />
          <div className="relative flex items-center justify-center md:py-8">
            {/* halo behind the badge */}
            <span
              className="pointer-events-none absolute inset-0 m-auto h-16 w-16 rounded-full"
              style={{ background: "radial-gradient(circle, rgba(59,130,246,0.18) 0%, transparent 70%)" }}
              aria-hidden
            />
            <span
              className="wiwi-aurora relative flex h-12 w-12 items-center justify-center rounded-full border border-blue-400/30 bg-[var(--admin-surface-elevated)] text-[16px] font-semibold text-blue-300 shadow-lg shadow-blue-500/[0.12]"
              style={{ fontFamily: MONO }}
              aria-label="equals"
              role="img"
            >
              =
            </span>
          </div>
          <Receipt
            title="Through wiwi"
            subtitle="Routing, retries, analytics, failover"
            accent
          />
        </div>

        <p className="mt-5 flex items-center justify-center gap-1.5 text-center text-[12px] text-[var(--admin-text-dim)]">
          <Info size={12} aria-hidden />
          Illustrative month at $1,000 of provider usage.
        </p>

        {/* the three zeros */}
        <div className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {ZERO_FACTS.map(({ label, value }) => (
            <div
              key={label}
              className="group relative overflow-hidden rounded-[12px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-5 py-4 transition-colors duration-150 hover:border-emerald-400/25"
            >
              {/* emerald wash that lifts on hover/focus-within — subtle, same
                  accent the value already carries */}
              <span
                className="pointer-events-none absolute inset-0 bg-gradient-to-br from-emerald-400/[0.06] to-transparent opacity-60 transition-opacity duration-150 group-hover:opacity-100"
                aria-hidden
              />
              <div className="relative flex items-center justify-between gap-3">
                <span className="flex items-center gap-2 text-[13px] text-[var(--admin-text-muted)]">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-400/10">
                    <InfinityIcon size={13} className="text-emerald-400/90" aria-hidden />
                  </span>
                  {label}
                </span>
                <span className="text-[17px] font-semibold text-emerald-400" style={{ fontFamily: MONO }}>
                  {value}
                </span>
              </div>
            </div>
          ))}
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
        <div className="overflow-hidden rounded-[var(--admin-radius)] border border-[var(--admin-border)] bg-[var(--admin-surface)]">
          <div className="overflow-x-auto admin-scroll">
            <table className="w-full min-w-[640px] border-collapse text-left">
              <thead>
                <tr className="border-b border-[var(--admin-border)]">
                  <th
                    scope="col"
                    className="sticky left-0 z-10 bg-[var(--admin-surface)] px-5 py-4 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--admin-text-dim)] shadow-[6px_0_10px_-8px_rgba(0,0,0,0.9)]"
                  >
                    Feature
                  </th>
                  {COLS.map((c, i) => (
                    <th
                      key={c.key}
                      scope="col"
                      className={`px-5 py-4 text-center text-[13px] font-semibold ${
                        i === COLS.length - 1
                          ? "text-blue-400"
                          : "text-[var(--admin-text)]"
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
        </div>
        {/* The table scrolls horizontally on a phone; say so rather than letting
            the reader discover it by dragging. */}
        <p className="mt-3 text-center text-[12px] text-[var(--admin-text-dim)] sm:hidden">
          Swipe the table sideways to compare all three plans →
        </p>
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
              <summary className="flex cursor-pointer items-center justify-between gap-3 px-5 py-4 text-[14px] font-medium text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/25">
                {item.question}
                <span
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--admin-border)] text-[var(--admin-text-dim)] transition-all duration-200 group-open:border-blue-500/30 group-open:bg-blue-500/10 group-open:text-blue-400"
                  aria-hidden
                >
                  <ChevronDown size={14} className="transition-transform duration-200 group-open:rotate-180" />
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
        <div className="relative overflow-hidden rounded-[var(--admin-radius-lg)] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-6 py-16 text-center sm:px-12">
          <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="opacity-40" />
          <div
            className="pointer-events-none absolute left-1/2 top-0 h-64 w-[520px] max-w-full -translate-x-1/2 rounded-full"
            style={{ background: "radial-gradient(circle, rgba(124,58,237,0.12) 0%, transparent 65%)" }}
            aria-hidden
          />
          <div className="relative z-10">
            <span className="admin-badge admin-badge-blue mb-5 inline-flex">
              <Sparkles size={12} />
              Free to start
            </span>
            <h2 className="text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)] sm:text-3xl">
              Ready to start?
            </h2>
            <p className="mx-auto mt-3 max-w-md text-[14px] text-[var(--admin-text-muted)]">
              Create a free account in seconds — no credit card, no commitment.
              Upgrade to managed cloud only if you want us to run it.
            </p>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                to="/signup"
                className="wiwi-shimmer group inline-flex h-12 w-full items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 text-[13px] font-medium text-white shadow-lg shadow-brand-600/25 transition-[filter] duration-150 hover:brightness-110 sm:w-auto"
              >
                Get started free
                <ArrowRight size={14} className="transition-transform duration-150 group-hover:translate-x-0.5" />
              </Link>
              <Link
                to="/docs"
                className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.14] hover:bg-white/[0.04] sm:w-auto"
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

// ── tier card ────────────────────────────────────────────────────────────

function TierCard({ tier }: { tier: Tier }) {
  const Icon = tier.icon;
  const featured = Boolean(tier.featured);
  return (
    <div
      className={`group relative flex h-full flex-col rounded-[var(--admin-radius-lg)] p-7 transition-all duration-300 hover:-translate-y-1 ${
        featured
          ? "border border-blue-500/25 bg-[var(--admin-surface-elevated)] md:scale-[1.03]"
          : "border border-[var(--admin-border)] bg-[var(--admin-surface)] hover:border-[var(--admin-border-hover)]"
      }`}
    >
      {/* top specular edge */}
      <span
        className="pointer-events-none absolute inset-x-6 top-0 h-px"
        style={{ background: `linear-gradient(90deg, transparent, ${withAlpha(tier.accent, 0.35)}, transparent)` }}
        aria-hidden
      />

      {featured && (
        <span className="absolute -top-3.5 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-blue-500/30 bg-[var(--admin-surface-elevated)] px-3.5 py-1.5 text-[11px] font-semibold text-blue-300">
          <Sparkles size={11} aria-hidden />
          Most popular
        </span>
      )}

      <div className="relative z-10 flex h-full flex-col">
        <div className="mb-6 flex items-center gap-3">
          <span
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[12px] border transition-transform duration-300 group-hover:scale-110"
            style={{
              background: withAlpha(tier.accent, 0.1),
              borderColor: withAlpha(tier.accent, 0.22),
              color: tier.accent,
            }}
          >
            <Icon className="h-5 w-5" aria-hidden />
          </span>
          <div className="min-w-0">
            <h3 className="text-[17px] font-semibold leading-tight tracking-[-0.01em] text-[var(--admin-text)]">
              {tier.name}
            </h3>
            <span className="mt-0.5 block text-[11px] uppercase tracking-[0.12em]" style={{ color: withAlpha(tier.accent, 0.85) }}>
              {tier.tag}
            </span>
          </div>
        </div>

        {/* price block */}
        <div
          className="relative rounded-[12px] border px-4 py-4"
          style={{ borderColor: withAlpha(tier.accent, 0.14), background: "rgba(255,255,255,0.02)" }}
        >
          <div className="flex items-baseline gap-2">
            <span className="text-[42px] font-bold leading-none tracking-[-0.03em] tabular-nums" style={{ fontFamily: MONO, color: tier.accent }}>
              {tier.price}
            </span>
            <span className="text-[12px] leading-tight text-[var(--admin-text-dim)]">
              {tier.priceNote}
            </span>
          </div>
          <p className="mt-2.5 flex items-start gap-1.5 text-[12px] leading-snug text-[var(--admin-text-muted)]">
            <ArrowRight size={12} className="mt-0.5 shrink-0" style={{ color: withAlpha(tier.accent, 0.8) }} aria-hidden />
            {tier.payFor}
          </p>
        </div>

        <div className="my-6 h-px w-full bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />

        <ul className="mb-7 flex-1 space-y-3">
          {tier.features.map((f) => (
            <li key={f} className="group/feat flex items-start gap-2.5">
              <span
                className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full transition-transform duration-150 group-hover/feat:scale-110"
                style={{ background: withAlpha(tier.accent, 0.14) }}
              >
                <Check size={10} style={{ color: tier.accent }} aria-hidden />
              </span>
              <span className="text-[13px] leading-relaxed text-[var(--admin-text-muted)] transition-colors duration-150 group-hover/feat:text-[var(--admin-text)]">
                {f}
              </span>
            </li>
          ))}
        </ul>

        <Link
          to={tier.cta.to}
          className={`inline-flex h-11 w-full items-center justify-center gap-2 rounded-[10px] px-5 text-[13px] font-medium transition-all duration-150 ${
            featured
              ? "wiwi-shimmer bg-gradient-to-b from-brand-500 to-brand-700 text-white shadow-lg shadow-brand-600/25 hover:brightness-110"
              : "border border-white/[0.08] bg-white/[0.02] text-[var(--admin-text)] hover:border-white/[0.14] hover:bg-white/[0.05]"
          }`}
        >
          {tier.cta.label}
          <ArrowRight size={14} className="transition-transform duration-150 group-hover:translate-x-0.5" />
        </Link>
      </div>
    </div>
  );
}

// ── receipt ──────────────────────────────────────────────────────────────

function Receipt({
  title,
  subtitle,
  accent = false,
}: {
  title: string;
  subtitle: string;
  accent?: boolean;
}) {
  return (
    <div className="relative">
      {/* receipt-paper zigzag bottom edge — pure CSS, two stacked triangles
          via a repeating background, offset by half a unit */}
      <div
        className={`pointer-events-none absolute inset-x-3 bottom-0 h-2 translate-y-full ${
          accent ? "opacity-25" : "opacity-15"
        }`}
        style={{
          background: accent ? "#3b82f6" : "var(--admin-border)",
          clipPath:
            "polygon(0 0, 100% 0, 100% 100%, 97.5% 0, 95% 100%, 92.5% 0, 90% 100%, 87.5% 0, 85% 100%, 82.5% 0, 80% 100%, 77.5% 0, 75% 100%, 72.5% 0, 70% 100%, 67.5% 0, 65% 100%, 62.5% 0, 60% 100%, 57.5% 0, 55% 100%, 52.5% 0, 50% 100%, 47.5% 0, 45% 100%, 42.5% 0, 40% 100%, 37.5% 0, 35% 100%, 32.5% 0, 30% 100%, 27.5% 0, 25% 100%, 22.5% 0, 20% 100%, 17.5% 0, 15% 100%, 12.5% 0, 10% 100%, 7.5% 0, 5% 100%, 2.5% 0, 0 100%)",
        }}
        aria-hidden
      />
      <div
        className={`relative overflow-hidden rounded-t-[14px] border p-5 transition-colors sm:p-6 ${
          accent
            ? "border-blue-500/25 border-b-0 bg-[var(--admin-surface-elevated)] shadow-lg shadow-blue-500/[0.06]"
            : "border-[var(--admin-border)] border-b-0 bg-[var(--admin-surface)]"
        }`}
      >
        {/* faint top accent wash — the wiwi receipt reads "this is the one you want" */}
        {accent && (
          <span
            className="pointer-events-none absolute inset-x-0 top-0 h-20"
            style={{ background: "linear-gradient(180deg, rgba(59,130,246,0.07) 0%, transparent 100%)" }}
            aria-hidden
          />
        )}

        <div className="relative flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <span
              className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] ${
                accent ? "bg-blue-500/12 text-blue-300" : "bg-white/[0.04] text-[var(--admin-text-muted)]"
              }`}
            >
              {accent ? <Route size={16} aria-hidden /> : <Zap size={16} aria-hidden />}
            </span>
            <div className="min-w-0">
              <div className="text-[13px] font-semibold text-[var(--admin-text)]">{title}</div>
              <div className="mt-0.5 text-[11px] text-[var(--admin-text-dim)]">{subtitle}</div>
            </div>
          </div>
          {accent && (
            <span className="admin-badge admin-badge-blue shrink-0">
              <Check size={11} aria-hidden />
              No markup
            </span>
          )}
        </div>

        <dl className="relative mt-5 space-y-2.5 rounded-[10px] border border-white/[0.04] bg-black/[0.14] px-3.5 py-3.5">
          <ReceiptRow label="Provider usage" value={SAMPLE_BILL.provider} />
          <ReceiptRow label="wiwi platform fee" value={SAMPLE_BILL.fee} muted />
          <div className="!mt-4 flex items-baseline gap-3 border-t border-dashed border-[var(--admin-border)] pt-3.5">
            <dt className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--admin-text-muted)]">Total</dt>
            <span
              className="mx-1 h-px flex-1 translate-y-[-3px]"
              style={{
                backgroundImage:
                  "radial-gradient(circle, var(--admin-text-dim) 1px, transparent 1px)",
                backgroundSize: "6px 6px",
              }}
              aria-hidden
            />
            <dd
              className={`text-[21px] font-bold tabular-nums ${
                accent ? "text-blue-300" : "text-[var(--admin-text)]"
              }`}
              style={{ fontFamily: MONO }}
            >
              {SAMPLE_BILL.total}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

function ReceiptRow({
  label,
  value,
  muted = false,
}: {
  label: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="shrink-0 text-[12px] text-[var(--admin-text-dim)]">{label}</dt>
      <span
        className="mx-1 h-px flex-1 translate-y-[-3px]"
        style={{
          backgroundImage:
            "radial-gradient(circle, var(--admin-text-dim) 1px, transparent 1px)",
          backgroundSize: "6px 6px",
        }}
        aria-hidden
      />
      <dd
        className={`shrink-0 text-[12px] tabular-nums ${
          muted ? "text-emerald-400" : "text-[var(--admin-text-muted)]"
        }`}
        style={{ fontFamily: MONO }}
      >
        {value}
      </dd>
    </div>
  );
}

// ── comparison table helpers ─────────────────────────────────────────────

function CellMark({ value }: { value: Cell }) {
  if (value === true)
    return (
      <span className="mx-auto flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/12">
        <Check size={12} className="text-emerald-400" aria-label="Included" />
      </span>
    );
  if (value === false)
    return (
      <span className="mx-auto flex h-5 w-5 items-center justify-center">
        <Minus size={14} className="text-[var(--admin-text-dim)]" aria-label="Not included" />
      </span>
    );
  if (value === "partial")
    return <span className="text-[var(--admin-text-muted)]">~</span>;
  return (
    <span className="text-[12px] text-[var(--admin-text-muted)]" style={{ fontFamily: MONO }}>
      {value}
    </span>
  );
}

function GroupBlock({ group }: { group: CompareGroup }) {
  return (
    <>
      <tr>
        <th
          scope="colgroup"
          colSpan={4}
          className="sticky left-0 border-b border-[var(--admin-border)] bg-[#0b0b0b] px-5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--admin-text-dim)]"
        >
          {group.heading}
        </th>
      </tr>
      {group.rows.map((row) => (
        <tr key={row.label} className="group transition-colors hover:bg-white/[0.02]">
          <th
            scope="row"
            className="sticky left-0 z-10 border-b border-[var(--admin-border)] bg-[var(--admin-surface)] px-5 py-3.5 text-left text-[13px] font-normal text-[var(--admin-text-muted)] shadow-[6px_0_10px_-8px_rgba(0,0,0,0.9)] transition-colors group-hover:bg-[#0d0d0d] group-hover:text-[var(--admin-text)]"
          >
            {row.label}
          </th>
          {COLS.map((c) => (
            <td key={c.key} className="border-b border-[var(--admin-border)] px-5 py-3.5 text-center">
              <CellMark value={row[c.key]} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}