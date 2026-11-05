// Pricing — the real model: the software is free and MIT-licensed, you pay
// your providers directly, and enterprise support is a conversation. There is
// no hosted billing product, so this page does not invent one.

import { Link } from "react-router-dom";
import {
  ArrowRight,
  Building2,
  Check,
  ChevronDown,
  CircleDollarSign,
  CreditCard,
  Minus,
  Server,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";
import { ClosingCta } from "@/components/ClosingCta";
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

type CompareMark = "yes" | "no" | string;
type CompareRow = {
  label: string;
  note: string;
  selfHosted: CompareMark;
  enterprise: CompareMark;
};
type CompareGroup = { title: string; rows: CompareRow[] };

const COMPARE_GROUPS: CompareGroup[] = [
  {
    title: "Pricing & licence",
    rows: [
      { label: "Software price", note: "The gateway, console, and site.", selfHosted: "Free", enterprise: "Free" },
      { label: "Licence", note: "Use, fork, and ship it.", selfHosted: "MIT", enterprise: "MIT" },
      { label: "Platform fee", note: "No per-token or seat markup.", selfHosted: "yes", enterprise: "yes" },
      { label: "Provider bill", note: "Paid to your provider account.", selfHosted: "Your providers", enterprise: "Your providers" },
    ],
  },
  {
    title: "What you run",
    rows: [
      { label: "Self-hosting", note: "Your infrastructure, your data.", selfHosted: "yes", enterprise: "yes" },
      { label: "Deployment assistance", note: "Setup, migration, and upgrades.", selfHosted: "Docs", enterprise: "Included" },
      { label: "Architecture review", note: "Multi-tenant and high-volume designs.", selfHosted: "no", enterprise: "Included" },
    ],
  },
  {
    title: "Support & custom work",
    rows: [
      { label: "Support channel", note: "Where help comes from.", selfHosted: "GitHub", enterprise: "Direct" },
      { label: "Response priority", note: "Blocking issues get triaged first.", selfHosted: "Best effort", enterprise: "Priority" },
      { label: "Custom adapter work", note: "New providers or wire shapes.", selfHosted: "Your team", enterprise: "Included" },
    ],
  },
];
const ALL_COMPARE_ROWS = COMPARE_GROUPS.flatMap((g) => g.rows);

function CellMark({ value }: { value: CompareMark }) {
  if (value === "yes") {
    return (
      <span
        className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-emerald-400/20 bg-emerald-400/10"
        title="Included"
      >
        <Check size={13} className="text-emerald-400" aria-hidden />
        <span className="sr-only">Included</span>
      </span>
    );
  }
  if (value === "no") {
    return (
      <span
        className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-white/[0.06] text-[var(--admin-text-dim)]"
        title="Not included"
      >
        <Minus size={13} aria-hidden />
        <span className="sr-only">Not included</span>
      </span>
    );
  }
  // Long strings get a quieter treatment so the cell doesn't scream. A cell
  // that wraps to three lines breaks the row rhythm; the note column already
  // carries detail, so truncate here and surface the rest in the tooltip.
  const long = value.length > 18;
  return (
    <span
      className={`inline-flex max-w-full items-center justify-center rounded-md px-2 py-1 text-center text-[12.5px] leading-snug ${
        long
          ? "bg-white/[0.03] text-[var(--admin-text-muted)]"
          : "font-medium text-[var(--admin-text)]"
      }`}
      title={value}
    >
      {value}
    </span>
  );
}

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
      <section
        aria-labelledby="pricing-heading"
        className="relative isolate overflow-hidden rounded-2xl border border-white/[0.08] bg-[#080808]"
      >
        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="opacity-35" />
        <div
          aria-hidden
          className="hero-grid pointer-events-none absolute inset-0 opacity-25"
        />
        <div
          className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-60"
          aria-hidden
        />

        <div className="relative px-5 pb-7 pt-8 sm:px-8 sm:pb-8 sm:pt-9 lg:px-10">
          <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.75fr)] lg:gap-10">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <Badge tone="green">
                  <span className="mr-1 size-1.5 rounded-full bg-emerald-400" aria-hidden />
                  Free and open source
                </Badge>
                <span className="font-mono text-[11px] tracking-[0.04em] text-[var(--admin-text-dim)]">
                  MIT · self-hosted
                </span>
              </div>
              <h1
                id="pricing-heading"
                className="mt-5 max-w-[17ch] text-[clamp(2.1rem,5vw,3.35rem)] font-semibold leading-[1.06] tracking-[-0.04em] text-[var(--admin-text)]"
              >
                Pay your providers.{" "}
                <span className="text-[var(--admin-text-muted)]">
                  Not a{" "}
                  <span className="text-[var(--admin-text)]">gateway tax.</span>
                </span>
              </h1>
              <p className="mt-5 max-w-[49ch] text-[14.5px] leading-[1.75] text-[var(--admin-text-muted)] sm:text-[15px]">
                The gateway is MIT-licensed and runs on your infrastructure. There is no
                platform fee, no credit balance, and no per-token markup — just the provider
                bill you already pay.
              </p>
            </div>

            <figure className="relative min-w-0 sm:mx-auto sm:w-full sm:max-w-sm lg:max-w-none">
              <figcaption className="mb-4 flex items-center justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.13em] text-[var(--admin-text-dim)]">
                <span>One request. Your provider.</span>
                <span className="text-emerald-300">0% markup</span>
              </figcaption>
              <div className="relative rounded-xl border border-white/[0.07] bg-black/25 px-4 py-5 sm:px-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2.5">
                    <CircleDollarSign size={18} className="text-blue-300" aria-hidden />
                    <span className="text-[12.5px] font-medium text-[var(--admin-text)]">Your provider</span>
                  </div>
                  <span className="inline-flex items-center gap-1.5 text-[11px] text-emerald-300/90">
                    <ShieldCheck size={13} aria-hidden />
                    Direct billing
                  </span>
                </div>
                <div className="mt-4 flex items-center gap-3 border-t border-white/[0.06] pt-4" aria-hidden>
                  <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--admin-text-dim)]">Gateway fee</span>
                  <span className="h-px flex-1 bg-white/[0.08]" />
                  <span className="font-mono text-2xl font-medium tracking-[-0.04em] text-[var(--admin-text)]">$0</span>
                </div>
                <div className="sr-only">Requests go through your gateway to your provider. The gateway adds zero fees.</div>
              </div>
              <div className="mt-3 flex items-center gap-1.5 text-[11px] text-[var(--admin-text-dim)]">
                <ShieldCheck size={13} className="text-blue-400/80" aria-hidden />
                Your infrastructure, your provider account
              </div>
            </figure>
          </div>

          <ul className="relative mt-8 grid list-none border-t border-white/[0.07] pt-6 sm:grid-cols-3">
            {["No platform fee", "No credit card", "No seat licence"].map((item) => (
              <li
                key={item}
                className="flex min-h-11 items-center gap-3 border-b border-white/[0.05] px-1 py-3 text-[13px] text-[var(--admin-text-muted)] last:border-0 sm:justify-center sm:border-l sm:border-white/[0.07] sm:px-3 sm:first:border-l-0"
              >
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full border border-emerald-400/15 bg-emerald-400/[0.06]">
                  <Check size={12} className="text-emerald-400" aria-hidden />
                </span>
                {item}
              </li>
            ))}
          </ul>
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
        <div className="mb-5 text-center">
          <span className="admin-label">Compare</span>
          <h2 className="mt-2 text-[22px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Side by side
          </h2>
          <p className="mx-auto mt-2 max-w-xl text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
            Same software, same licence. The difference is who does the work when you
            need a provider, a translation rule, or a deployment shape that isn't there yet.
          </p>
        </div>

        {/* Desktop / tablet: grouped table with the self-hosted column highlighted.
            On phones the table's column headers have nowhere to live, so the
            dedicated card list below takes over at `sm`. */}
        <Card className="hidden overflow-hidden p-0 sm:block">
          <div
            tabIndex={0}
            role="region"
            aria-label="Self-hosted versus Enterprise comparison, scrollable"
            className="admin-scroll overflow-x-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/50"
          >
            <table className="w-full min-w-[34rem] border-collapse text-left text-[13px]">
              <caption className="sr-only">
                Feature comparison between the free self-hosted gateway and Enterprise support
              </caption>
              <thead>
                <tr className="border-b border-[var(--admin-border)] bg-white/[0.02]">
                  <th scope="col" className="px-5 py-4 admin-label">
                    Capability
                  </th>
                  <th scope="col" className="w-[24%] px-5 py-4 text-center">
                    <span className="inline-flex items-center gap-1.5 font-semibold text-blue-400">
                      Self-hosted
                      <span className="h-1.5 w-1.5 rounded-full bg-blue-400" aria-hidden />
                    </span>
                  </th>
                  <th scope="col" className="w-[24%] px-5 py-4 text-center font-medium text-[var(--admin-text)]">
                    Enterprise
                  </th>
                </tr>
              </thead>
              {COMPARE_GROUPS.map((group) => (
                <tbody key={group.title}>
                  <tr className="bg-white/[0.015]">
                    <th
                      scope="colgroup"
                      colSpan={3}
                      className="px-5 pb-1.5 pt-4 text-left text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--admin-text-dim)]"
                    >
                      {group.title}
                    </th>
                  </tr>
                  {group.rows.map((row) => (
                    <tr
                      key={row.label}
                      className="border-t border-[var(--admin-border)] transition-colors hover:bg-white/[0.015]"
                    >
                      <th scope="row" className="px-5 py-3.5 text-left font-normal">
                        <span className="block font-medium text-[var(--admin-text)]">{row.label}</span>
                        <span className="mt-0.5 block text-[11.5px] leading-snug text-[var(--admin-text-dim)]">
                          {row.note}
                        </span>
                      </th>
                      <td className="border-l border-blue-400/10 bg-blue-500/[0.02] px-5 py-3.5 text-center">
                        <CellMark value={row.selfHosted} />
                      </td>
                      <td className="px-5 py-3.5 text-center">
                        <CellMark value={row.enterprise} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              ))}
            </table>
          </div>
        </Card>

        {/* Phone: a two-column matrix can't survive 375px, so each row becomes
            a card with both plans labelled. Same data, no horizontal scroll. */}
        <div className="space-y-2 sm:hidden">
          {ALL_COMPARE_ROWS.map((row) => (
            <Card key={row.label} className="p-4">
              <span className="text-[13.5px] font-medium text-[var(--admin-text)]">{row.label}</span>
              <p className="mt-0.5 text-[11px] leading-snug text-[var(--admin-text-dim)]">
                {row.note}
              </p>
              <dl className="mt-3 grid grid-cols-2 gap-2">
                <div className="rounded-lg border border-blue-400/15 bg-blue-500/[0.03] px-3 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-[0.12em] text-blue-400">
                    Self-hosted
                  </dt>
                  <dd className="mt-1">
                    <CellMark value={row.selfHosted} />
                  </dd>
                </div>
                <div className="rounded-lg border border-[var(--admin-border)] px-3 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--admin-text-dim)]">
                    Enterprise
                  </dt>
                  <dd className="mt-1">
                    <CellMark value={row.enterprise} />
                  </dd>
                </div>
              </dl>
            </Card>
          ))}
        </div>

        <div className="mt-4 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link to="/docs" className="admin-btn admin-btn-primary min-h-11 px-5">
            Start self-hosting
            <ArrowRight size={15} className="ml-2" />
          </Link>
          <Link to="/contact" className="admin-btn admin-btn-ghost min-h-11 px-5">
            Talk about Enterprise
          </Link>
        </div>
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

      <ClosingCta
        eyebrow=".05 · quickstart"
        title="Three commands, then every dialect answers."
        body="Point any OpenAI or Anthropic client at the gateway, mint a virtual key, and watch requests land in the console. No account, no billing screen."
        primary={{ label: "Read the quickstart", to: "/docs" }}
        secondary={{ label: "Self-hosting details", to: "/open-source" }}
        footnote="MIT-licensed · no phone-home · runs in one process"
        commands={[
          { title: "Prepare the config", command: "cp wiwi.yaml.example wiwi.yaml" },
          { title: "Start the gateway", command: "wiwi --config wiwi.yaml" },
          { title: "Mint a virtual key", command: "curl -X POST localhost:4000/admin/keys/generate \\\n  -H 'Authorization: Bearer $WIWI_MASTER_KEY' \\\n  -d '{\"alias\":\"dev\"}'" },
        ]}
      />
    </div>
  );
}
