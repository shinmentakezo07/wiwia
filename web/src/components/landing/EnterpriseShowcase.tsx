// Enterprise showcase — the "built for teams that ship at scale" band. Shared
// by the landing page (as its enterprise CTA) and the /enterprise page, so the
// two surfaces can never drift. A framed dark panel: ambient glows under a
// masked grid wash, a split hero (copy left, deployment diagram right), a proof
// strip, and spotlight capability cards. Copy is carried over verbatim from the
// original enterprise surfaces; the diagram only restates what the copy says.

import { Link } from "react-router-dom";
import type { PointerEvent } from "react";
import {
  ArrowRight,
  Boxes,
  Building2,
  Check,
  Database,
  Gauge,
  LayoutDashboard,
  LifeBuoy,
  Lock,
  Network,
  Puzzle,
  Server,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

const CAPABILITIES: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: Server,
    title: "Self-hosted by default",
    body: "Run the gateway, console, and database on your own infrastructure. Nothing has to leave your network.",
  },
  {
    icon: Puzzle,
    title: "Custom adapters",
    body: "A provider, wire format, or translation rule that does not exist yet can be built against the adapter interface.",
  },
  {
    icon: LifeBuoy,
    title: "Deployment help",
    body: "Hands-on help with config, Postgres, key pools, and the migration from another gateway.",
  },
  {
    icon: Gauge,
    title: "Priority triage",
    body: "Issues that block your rollout get looked at first. No seat licence and no platform fee.",
  },
];

const PROOF: { value: string; label: string }[] = [
  { value: "13", label: "Provider types" },
  { value: "3", label: "Inbound dialects" },
  { value: "MIT", label: "Licence" },
  { value: "0", label: "Platform fee" },
];

const INCLUDED = [
  "Deployment assistance",
  "Custom adapter work",
  "Priority triage",
  "Architecture review",
];

const DEFAULT_SECONDARY_CTA = { label: "Explore Enterprise", to: "/enterprise" };

/** Track the pointer so the card's radial spotlight follows it. */
function trackSpotlight(e: PointerEvent<HTMLElement>) {
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--x", `${e.clientX - r.left}px`);
  el.style.setProperty("--y", `${e.clientY - r.top}px`);
}

export function EnterpriseShowcase(props: {
  /** h1 on the /enterprise page, h2 where the band is one section of a page. */
  headingLevel?: "h1" | "h2";
  /** Pass null to drop the secondary button (e.g. on /enterprise itself). */
  secondaryCta?: { label: string; to: string } | null;
}) {
  const Heading = props.headingLevel ?? "h2";
  // keep the card titles one level under the band heading (h1 → h2, h2 → h3)
  const CardHeading = props.headingLevel === "h1" ? "h2" : "h3";
  const secondary =
    props.secondaryCta === undefined ? DEFAULT_SECONDARY_CTA : props.secondaryCta;

  return (
    <section className="scroll-reveal relative">
      {/* soft outer halo so the panel lifts off the page */}
      <div
        className="pointer-events-none absolute -inset-x-6 -inset-y-10 -z-10 opacity-70 blur-3xl"
        style={{ background: "radial-gradient(ellipse 60% 50% at 50% 40%, rgba(99,102,241,0.10), transparent 70%)" }}
        aria-hidden
      />

      <div className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)] shadow-[0_40px_120px_-40px_rgba(0,0,0,0.9)] sm:rounded-[32px]">
        <div
          className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/[0.035] via-transparent to-transparent"
          aria-hidden
        />
        <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70" aria-hidden />

        {/* ambient backdrop: panning grid wash + blue/violet glows */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="hero-grid absolute inset-x-0 top-0 h-[620px] opacity-70" />
          <div
            className="hero-orb-1 absolute -left-28 -top-32 h-[460px] w-[460px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(59,130,246,0.18) 0%, transparent 62%)" }}
          />
          <div
            className="hero-orb-2 absolute -right-32 -top-16 h-[420px] w-[420px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(139,92,246,0.16) 0%, transparent 62%)" }}
          />
          <div
            className="absolute inset-x-0 bottom-0 h-[280px]"
            style={{ background: "radial-gradient(ellipse 70% 100% at 50% 120%, rgba(59,130,246,0.10), transparent 70%)" }}
          />
        </div>

        <div className="relative px-6 py-14 sm:px-10 sm:py-16 lg:px-14 lg:py-20">
          {/* ── hero: copy + deployment diagram ── */}
          <div className="grid items-center gap-12 lg:grid-cols-[1.1fr_1fr] lg:gap-14">
            <div className="text-center lg:text-left">
              <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/[0.06] px-3.5 py-1.5 text-blue-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                <Building2 size={12} />
                <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em]">
                  Enterprise
                </span>
                <span className="hero-live-dot text-blue-400" aria-hidden />
              </div>

              <Heading className="text-3xl font-semibold leading-[1.08] tracking-[-0.025em] text-[var(--admin-text)] sm:text-4xl lg:text-[52px]">
                Built for teams that
                <br />
                <span className="hero-gradient-text">ship at scale</span>
              </Heading>

              <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base lg:mx-0">
                When the gateway becomes mission-critical, you may want help running it —
                deployment, custom adapters, and a direct line when something blocks you.
                The software stays the same MIT-licensed build.
              </p>

              <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row lg:justify-start">
                <span className="relative isolate inline-flex w-full sm:w-auto">
                  <span className="hero-cta-ring" aria-hidden />
                  <Link
                    to="/contact"
                    className="wiwi-shimmer group inline-flex h-11 w-full items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-7 text-sm font-semibold text-white shadow-lg shadow-brand-600/25 transition-[transform,filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/60 active:scale-[0.98] sm:w-auto"
                  >
                    Talk to us
                    <ArrowRight
                      size={15}
                      className="transition-transform duration-150 group-hover:translate-x-0.5"
                    />
                  </Link>
                </span>
                {secondary && (
                  <Link
                    to={secondary.to}
                    className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.02] px-6 text-sm font-medium text-[var(--admin-text)] backdrop-blur transition-colors hover:border-white/[0.16] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
                  >
                    {secondary.label}
                  </Link>
                )}
              </div>

              <ul className="mx-auto mt-8 grid max-w-md grid-cols-2 gap-x-5 gap-y-2.5 text-left lg:mx-0">
                {INCLUDED.map((item) => (
                  <li
                    key={item}
                    className="flex items-center gap-2 text-[13px] text-[var(--admin-text-muted)]"
                  >
                    <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-400/10 ring-1 ring-emerald-400/25">
                      <Check size={10} strokeWidth={3} className="text-emerald-400" />
                    </span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>

            <DeploymentDiagram />
          </div>

          {/* ── proof strip ── */}
          <div className="mx-auto mt-16 grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-border)] sm:grid-cols-4">
            {PROOF.map((stat) => (
              <div
                key={stat.label}
                className="group/stat relative bg-[var(--admin-surface)] px-5 py-7 text-center transition-colors duration-300 hover:bg-white/[0.02]"
              >
                <span
                  className="pointer-events-none absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-blue-400/50 to-transparent opacity-0 transition-opacity duration-300 group-hover/stat:opacity-100"
                  aria-hidden
                />
                <p className="admin-stat-value text-2xl sm:text-[30px]">{stat.value}</p>
                <p className="admin-label mt-1.5">{stat.label}</p>
              </div>
            ))}
          </div>

          {/* ── capability cards ── */}
          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {CAPABILITIES.map((cap, i) => {
              const Icon = cap.icon;
              return (
                <div
                  key={cap.title}
                  onPointerMove={trackSpotlight}
                  className="group relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-white/[0.015] p-5 transition-[border-color,background-color,transform] duration-300 hover:-translate-y-0.5 hover:border-blue-500/25 hover:bg-white/[0.03] motion-reduce:hover:translate-y-0"
                >
                  <div className="ent-spotlight pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100" aria-hidden />
                  <div className="relative">
                    <div className="mb-5 flex items-start justify-between">
                      <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/[0.06] bg-gradient-to-br from-blue-500/15 to-violet-500/10 text-blue-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] transition-colors duration-300 group-hover:border-blue-500/30 group-hover:text-blue-200">
                        <Icon size={17} />
                      </span>
                      <span className="font-mono text-[10px] tabular-nums tracking-[0.18em] text-[var(--admin-text-dim)]" aria-hidden>
                        0{i + 1}
                      </span>
                    </div>
                    <CardHeading className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                      {cap.title}
                    </CardHeading>
                    <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                      {cap.body}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

/* ── deployment diagram ──────────────────────────────────────────────────────
   Visual restatement of "Self-hosted by default": your apps → a dashed
   network perimeter holding gateway, console and database → upstream
   providers. Decorative, so it is hidden from assistive tech. */

function DiagramNode(props: { icon: LucideIcon; label: string; sub: string; primary?: boolean }) {
  const Icon = props.icon;
  return (
    <div
      className={
        props.primary
          ? "relative flex items-center gap-3 rounded-xl border border-blue-400/30 bg-gradient-to-b from-blue-500/[0.12] to-violet-500/[0.06] px-3.5 py-3 shadow-[0_0_40px_-8px_rgba(59,130,246,0.45),inset_0_1px_0_rgba(255,255,255,0.08)]"
          : "relative flex items-center gap-3 rounded-xl border border-white/[0.07] bg-white/[0.025] px-3.5 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]"
      }
    >
      <span
        className={
          props.primary
            ? "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-500/20 text-blue-200"
            : "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/[0.05] text-[var(--admin-text-muted)]"
        }
      >
        <Icon size={15} />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[13px] font-medium text-[var(--admin-text)]">{props.label}</span>
        <span className="block truncate font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--admin-text-dim)]">
          {props.sub}
        </span>
      </span>
      {props.primary && <span className="hero-live-dot ml-auto shrink-0 text-emerald-400" />}
    </div>
  );
}

function Beam() {
  return (
    <div className="relative mx-auto h-8 w-px overflow-hidden bg-white/[0.08]">
      <span className="ent-beam absolute inset-x-0 top-0 h-4 bg-gradient-to-b from-transparent via-blue-400 to-transparent" />
    </div>
  );
}

function DeploymentDiagram() {
  return (
    <div className="relative mx-auto w-full max-w-md" aria-hidden>
      <div className="wiwi-conic-border shadow-[0_30px_80px_-30px_rgba(59,130,246,0.35)]">
        <div className="relative rounded-[15px] bg-[#070707]/95 p-5 backdrop-blur sm:p-6">
          {/* window chrome */}
          <div className="mb-5 flex items-center justify-between">
            <div className="flex gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
              <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
              <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
            </div>
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">
              deployment
            </span>
          </div>

          <DiagramNode icon={Boxes} label="Your apps" sub="3 inbound dialects" />
          <Beam />

          {/* network perimeter */}
          <div className="relative rounded-2xl border border-dashed border-emerald-400/25 bg-emerald-400/[0.02] p-3 pt-6">
            <span className="absolute -top-2.5 left-4 inline-flex items-center gap-1.5 rounded-full border border-emerald-400/25 bg-[#070707] px-2.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.18em] text-emerald-300">
              <Lock size={9} />
              Your network
            </span>
            <div className="space-y-2.5">
              <DiagramNode icon={Network} label="Gateway" sub="routing · failover" primary />
              <div className="grid grid-cols-2 gap-2.5">
                <DiagramNode icon={LayoutDashboard} label="Console" sub="admin" />
                <DiagramNode icon={Database} label="Database" sub="sqlite · pg" />
              </div>
            </div>
          </div>

          <Beam />

          {/* upstream providers */}
          <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
            <div className="mb-2.5 flex items-center justify-between">
              <span className="text-[13px] font-medium text-[var(--admin-text)]">Upstream providers</span>
              <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--admin-text-dim)]">
                13 types
              </span>
            </div>
            <div className="grid grid-cols-6 gap-1.5">
              {Array.from({ length: 12 }).map((_, i) => (
                <span
                  key={i}
                  className="ent-chip h-5 rounded-md border border-white/[0.06] bg-white/[0.03]"
                  style={{ animationDelay: `${i * 0.25}s` }}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
