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
  Network,
  Server,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";
import { ClosingCta } from "@/components/ClosingCta";
import { HERO_BEAMS_COMPACT, HeroBeamBackdrop } from "@/components/HeroBeamBackdrop";

const cn = (...parts: (string | false | undefined)[]) => parts.filter(Boolean).join(" ");

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
      "All 12 provider types and every inbound dialect",
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
      { label: "Platform fee", note: "No per-token or seat markup.", selfHosted: "None", enterprise: "None" },
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

function CellMark({ value }: { value: CompareMark }) {
  if (value === "yes") {
    return (
      <span
        className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-emerald-400/25 bg-emerald-400/10"
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
  const long = value.length > 18;
  const strong = value === "Included" || value === "Priority" || value === "Direct";
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center justify-center rounded-md px-2 py-1 text-center text-[12.5px] leading-snug",
        long ? "bg-white/[0.03] text-[var(--admin-text-muted)]" : "font-medium text-[var(--admin-text)]",
        strong && "bg-violet-400/10 text-violet-200",
      )}
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

const PRINCIPLES: { icon: LucideIcon; title: string; body: string }[] = [
  { icon: CreditCard, title: "No billing layer", body: "wiwi never holds a balance or resells tokens. Your provider account is the only account." },
  { icon: ShieldCheck, title: "No lock-in", body: "MIT licence, standard SQL, and a config file you own. Leave whenever you want." },
  { icon: Terminal, title: "Runs in one process", body: "No worker, queue, or control plane to operate alongside the gateway." },
];

const STATS: { value: string; label: string }[] = [
  { value: "$0", label: "Platform fee" },
  { value: "0%", label: "Token markup" },
  { value: "13", label: "Provider types" },
  { value: "MIT", label: "Open licence" },
];

function FlowNode(props: { icon: LucideIcon; title: string; sub: string; accent?: boolean }) {
  const Icon = props.icon;
  return (
    <div
      className={cn(
        "relative flex min-w-0 flex-1 flex-col items-center gap-2 rounded-xl border px-3 py-4 text-center backdrop-blur-sm",
        props.accent
          ? "border-blue-400/30 bg-blue-500/[0.08] shadow-[0_0_40px_-8px_rgba(59,130,246,0.55)]"
          : "border-white/[0.08] bg-white/[0.03]",
      )}
    >
      <span
        className={cn(
          "flex size-10 items-center justify-center rounded-lg border",
          props.accent ? "border-blue-400/30 bg-blue-500/15" : "border-white/[0.08] bg-white/[0.04]",
        )}
      >
        <Icon size={18} className={props.accent ? "text-blue-300" : "text-[var(--admin-text-muted)]"} aria-hidden />
      </span>
      <span className="text-[12.5px] font-medium text-[var(--admin-text)]">{props.title}</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--admin-text-dim)]">{props.sub}</span>
    </div>
  );
}

function FlowLink({ label }: { label: string }) {
  return (
    <div className="flex w-10 shrink-0 flex-col items-center gap-1.5 sm:w-16" aria-hidden>
      <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-emerald-300">{label}</span>
      <span className="relative block h-px w-full bg-gradient-to-r from-blue-400/10 via-blue-400/70 to-blue-400/10">
        <span className="absolute -top-[2px] left-1/2 size-[5px] -translate-x-1/2 rounded-full bg-blue-300 motion-safe:animate-pulse" />
      </span>
    </div>
  );
}

export function PricingPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-16 pb-16 lg:space-y-24">
      {/* ───────── Hero ───────── */}
      <section
        aria-labelledby="pricing-heading"
        className="relative isolate overflow-hidden rounded-3xl border border-white/[0.08] bg-[#070707]"
      >
        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="opacity-40" />
        <div aria-hidden className="hero-grid pointer-events-none absolute inset-0 opacity-20" />
        <div
          aria-hidden
          className="pointer-events-none absolute left-1/2 top-0 h-72 w-[46rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-blue-500/20 blur-[100px]"
        />
        <div
          aria-hidden
          className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70"
        />

        <div className="relative px-5 pb-8 pt-12 text-center sm:px-10 sm:pt-16 lg:px-14">
          <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-2">
            <Badge tone="green">
              <span className="mr-1 size-1.5 rounded-full bg-emerald-400 motion-safe:animate-pulse" aria-hidden />
              Free and open source
            </Badge>
            <span className="font-mono text-[11px] tracking-[0.06em] text-[var(--admin-text-dim)]">MIT · self-hosted</span>
          </div>

          <h1
            id="pricing-heading"
            className="mx-auto mt-6 max-w-[18ch] text-[clamp(2.4rem,6vw,4.4rem)] font-semibold leading-[1.02] tracking-[-0.045em] text-[var(--admin-text)]"
          >
            Pay your providers.{" "}
            <span className="bg-gradient-to-r from-blue-300 via-violet-300 to-blue-200 bg-clip-text text-transparent">
              Not a gateway tax.
            </span>
          </h1>
          <p className="mx-auto mt-6 max-w-[56ch] text-[15px] leading-[1.75] text-[var(--admin-text-muted)] sm:text-[16px]">
            The gateway is MIT-licensed and runs on your infrastructure. There is no platform fee, no
            credit balance, and no per-token markup — just the provider bill you already pay.
          </p>

          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link to="/docs" className="admin-btn admin-btn-primary min-h-11 px-6">
              Start self-hosting
              <ArrowRight size={15} className="ml-2" aria-hidden />
            </Link>
            <Link to="/contact" className="admin-btn admin-btn-ghost min-h-11 px-6">
              Talk about Enterprise
            </Link>
          </div>

          {/* Request-flow visual */}
          <figure className="mx-auto mt-12 max-w-2xl">
            <figcaption className="mb-4 flex items-center justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.13em] text-[var(--admin-text-dim)]">
              <span>One request. Your provider.</span>
              <span className="text-emerald-300">0% markup</span>
            </figcaption>
            <div className="flex items-center gap-1 sm:gap-2">
              <FlowNode icon={Terminal} title="Your app" sub="any dialect" />
              <FlowLink label="req" />
              <FlowNode icon={Network} title="wiwi gateway" sub="fee · $0" accent />
              <FlowLink label="req" />
              <FlowNode icon={CircleDollarSign} title="Your provider" sub="direct billing" />
            </div>
            <div className="sr-only">Requests go through your gateway to your provider. The gateway adds zero fees.</div>
          </figure>
        </div>

        {/* Stat strip */}
        <ul className="relative grid list-none grid-cols-2 border-t border-white/[0.07] bg-black/30 backdrop-blur-sm sm:grid-cols-4">
          {STATS.map((s, i) => (
            <li
              key={s.label}
              className={cn(
                "flex flex-col items-center gap-1 px-4 py-5",
                i % 2 === 1 && "border-l border-white/[0.06]",
                i >= 2 && "border-t border-white/[0.06] sm:border-t-0",
                i > 0 && "sm:border-l sm:border-white/[0.06]",
              )}
            >
              <span className="font-mono text-2xl font-semibold tracking-[-0.04em] text-[var(--admin-text)]">{s.value}</span>
              <span className="text-[11px] uppercase tracking-[0.12em] text-[var(--admin-text-dim)]">{s.label}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* ───────── Plans ───────── */}
      <section aria-labelledby="plans-heading">
        <div className="mb-8 text-center">
          <span className="admin-label">Plans</span>
          <h2 id="plans-heading" className="mt-2 text-[26px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
            Two ways to run wiwi
          </h2>
          <p className="mx-auto mt-2 max-w-md text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
            Same code either way. Pick whether you want to go it alone or have us alongside.
          </p>
        </div>

        {/* Void-black plans. Self-hosted gets the slow conic border; both panes
            carry a pointer-tracked spotlight (fine pointers only, CSS-gated). */}
        <div className="grid items-stretch gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          {PLANS.map((plan, i) => {
            const Icon = plan.icon;
            const primary = plan.featured;
            return (
              <div
                key={plan.name}
                className={cn("wiwi-plan-in rounded-2xl", primary ? "wiwi-conic-border" : "border border-white/[0.07]")}
                style={{ animationDelay: `${i * 90}ms` }}
              >
                <article
                  aria-labelledby={`plan-${i}`}
                  onPointerMove={(e) => {
                    const r = e.currentTarget.getBoundingClientRect();
                    e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
                    e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
                  }}
                  className="wiwi-plan group relative flex h-full flex-col overflow-hidden rounded-[15px] bg-black p-6 sm:p-8 lg:p-10"
                >
                  <div aria-hidden className="wiwi-plan-grid pointer-events-none absolute inset-0" />
                  {primary && <div aria-hidden className="wiwi-plan-scan pointer-events-none absolute inset-x-0 top-0 h-px" />}

                  <header className="relative flex items-center justify-between gap-3">
                    <span className="flex items-center gap-3">
                      <Icon size={18} className={primary ? "text-blue-300" : "text-violet-300"} aria-hidden />
                      <h3 id={`plan-${i}`} className="text-[15px] font-semibold text-[var(--admin-text)]">
                        {plan.name}
                      </h3>
                    </span>
                    <span className="font-mono text-[10px] uppercase tracking-[0.13em] text-[var(--admin-text-dim)]">
                      .{String(i + 1).padStart(2, "0")} · {plan.tag}
                    </span>
                  </header>

                  <p className="relative mt-10 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span
                      className={cn(
                        "font-mono text-[clamp(2.75rem,6vw,4.25rem)] font-semibold leading-none tracking-[-0.06em]",
                        primary
                          ? "bg-gradient-to-b from-white to-white/40 bg-clip-text text-transparent"
                          : "text-[var(--admin-text)]",
                      )}
                    >
                      {plan.price}
                    </span>
                    <span className="text-[13px] text-[var(--admin-text-dim)]">{plan.priceNote}</span>
                  </p>
                  <p className="relative mt-4 max-w-[46ch] text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
                    {plan.summary}
                  </p>

                  <p className="relative mt-8 flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.13em] text-[var(--admin-text-dim)]">
                    <span>{primary ? "Included" : "Everything in Self-hosted, plus"}</span>
                    <span aria-hidden className="h-px flex-1 bg-gradient-to-r from-white/[0.1] to-transparent" />
                  </p>
                  <ul className="relative mt-4 space-y-3">
                    {plan.features.map((feature, j) => (
                      <li
                        key={feature}
                        className="wiwi-plan-in flex items-start gap-3 text-[13px] leading-snug text-[var(--admin-text-muted)]"
                        style={{ animationDelay: `${180 + i * 90 + j * 45}ms` }}
                      >
                        <Check
                          size={14}
                          className={cn("mt-0.5 shrink-0", primary ? "text-emerald-400" : "text-violet-300")}
                          aria-hidden
                        />
                        <span>{feature}</span>
                      </li>
                    ))}
                  </ul>

                  <div className="relative mt-auto pt-10">
                    <Link
                      to={plan.cta.to}
                      className={cn(
                        "admin-btn group/cta min-h-12 w-full gap-2",
                        primary ? "admin-btn-primary wiwi-shimmer" : "admin-btn-ghost",
                      )}
                    >
                      {plan.cta.label}
                      <ArrowRight size={15} className="transition-transform motion-safe:group-hover/cta:translate-x-0.5" aria-hidden />
                    </Link>
                  </div>
                </article>
              </div>
            );
          })}
        </div>
      </section>

      {/* ───────── Principles ───────── */}
      <section aria-label="Why it is simple" className="grid gap-4 sm:grid-cols-3">
        {PRINCIPLES.map((item, i) => {
          const Icon = item.icon;
          return (
            <Card
              key={item.title}
              className="group relative overflow-hidden p-6 transition-colors hover:border-blue-400/25"
            >
              <span
                aria-hidden
                className="absolute right-4 top-3 font-mono text-[44px] font-semibold leading-none tracking-[-0.05em] text-white/[0.04]"
              >
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="flex size-10 items-center justify-center rounded-xl border border-blue-400/20 bg-blue-500/10 transition-colors group-hover:bg-blue-500/15">
                <Icon size={18} className="text-blue-400" aria-hidden />
              </span>
              <h3 className="mt-5 text-[15px] font-semibold text-[var(--admin-text)]">{item.title}</h3>
              <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{item.body}</p>
            </Card>
          );
        })}
      </section>

      {/* ───────── Compare ───────── */}
      <section aria-labelledby="compare-heading">
        <div className="mb-8 text-center">
          <span className="admin-label">Compare</span>
          <h2 id="compare-heading" className="mt-2 text-[26px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
            Side by side
          </h2>
          <p className="mx-auto mt-2 max-w-xl text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
            Same software, same licence. The difference is who does the work when you need a provider,
            a translation rule, or a deployment shape that isn't there yet.
          </p>
        </div>

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
                  <th scope="col" className="px-6 py-5 admin-label">
                    Capability
                  </th>
                  <th scope="col" className="w-[24%] border-t-2 border-t-blue-400 bg-blue-500/[0.07] px-5 py-5 text-center">
                    <span className="inline-flex items-center gap-1.5 font-semibold text-blue-300">
                      <Server size={14} aria-hidden />
                      Self-hosted
                    </span>
                  </th>
                  <th scope="col" className="w-[24%] border-t-2 border-t-violet-400/60 px-5 py-5 text-center font-medium text-[var(--admin-text)]">
                    <span className="inline-flex items-center gap-1.5">
                      <Building2 size={14} className="text-violet-300" aria-hidden />
                      Enterprise
                    </span>
                  </th>
                </tr>
              </thead>
              {COMPARE_GROUPS.map((group) => (
                <tbody key={group.title}>
                  <tr className="bg-white/[0.02]">
                    <th
                      scope="colgroup"
                      colSpan={3}
                      className="px-6 pb-2 pt-5 text-left text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--admin-text-dim)]"
                    >
                      {group.title}
                    </th>
                  </tr>
                  {group.rows.map((row) => (
                    <tr key={row.label} className="border-t border-[var(--admin-border)] transition-colors hover:bg-white/[0.02]">
                      <th scope="row" className="px-6 py-3.5 text-left font-normal">
                        <span className="block font-medium text-[var(--admin-text)]">{row.label}</span>
                        <span className="mt-0.5 block text-[12px] text-[var(--admin-text-dim)]">{row.note}</span>
                      </th>
                      <td className="bg-blue-500/[0.04] px-5 py-3.5 text-center">
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

        {/* Phones: grouped cards */}
        <div className="space-y-6 sm:hidden">
          {COMPARE_GROUPS.map((group) => (
            <div key={group.title} className="space-y-3">
              <h3 className="px-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--admin-text-dim)]">
                {group.title}
              </h3>
              {group.rows.map((row) => (
                <Card key={row.label} className="p-4">
                  <p className="text-[13.5px] font-medium text-[var(--admin-text)]">{row.label}</p>
                  <p className="mt-0.5 text-[12px] leading-snug text-[var(--admin-text-dim)]">{row.note}</p>
                  <dl className="mt-3 grid grid-cols-2 gap-2">
                    <div className="rounded-lg border border-blue-400/20 bg-blue-500/[0.05] px-3 py-2">
                      <dt className="text-[10px] font-semibold uppercase tracking-[0.12em] text-blue-400">Self-hosted</dt>
                      <dd className="mt-1">
                        <CellMark value={row.selfHosted} />
                      </dd>
                    </div>
                    <div className="rounded-lg border border-[var(--admin-border)] px-3 py-2">
                      <dt className="text-[10px] font-semibold uppercase tracking-[0.12em] text-violet-300">Enterprise</dt>
                      <dd className="mt-1">
                        <CellMark value={row.enterprise} />
                      </dd>
                    </div>
                  </dl>
                </Card>
              ))}
            </div>
          ))}
        </div>
      </section>

      {/* ───────── FAQ ───────── */}
      <section className="grid gap-8 lg:grid-cols-[minmax(0,0.6fr)_minmax(0,1.4fr)] lg:gap-14">
        <div className="lg:sticky lg:top-24 lg:self-start">
          <span className="admin-label">FAQ</span>
          <h2 className="mt-2 text-[26px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]">Questions</h2>
          <p className="mt-2 max-w-[32ch] text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
            Short answers about cost, hosting, and what Enterprise adds.
          </p>
          <Link
            to="/contact"
            className="mt-5 inline-flex items-center gap-1.5 text-[13px] font-medium text-blue-300 hover:text-blue-200"
          >
            Still unsure? Ask us
            <ArrowRight size={14} aria-hidden />
          </Link>
        </div>
        <div className="space-y-2.5">
          {FAQ.map((item) => (
            <details
              key={item.q}
              className="group rounded-[14px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-5 py-1.5 transition-colors hover:border-[var(--admin-border-hover)] open:border-blue-400/25 open:bg-blue-500/[0.03]"
            >
              <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 text-[14px] font-medium text-[var(--admin-text)]">
                {item.q}
                <ChevronDown size={18} className="shrink-0 text-[var(--admin-text-dim)] transition-transform group-open:rotate-180" aria-hidden />
              </summary>
              <p className="mb-3 mt-1 border-l-2 border-blue-400/30 pl-4 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
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
