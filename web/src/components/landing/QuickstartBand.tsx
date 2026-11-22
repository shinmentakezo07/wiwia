// QuickstartBand — the closing "get started" band shared by the landing page
// and the reliability page.
//
// It is one component because it is one design: a framed two-column close with
// the editorial argument on the left and a live-looking terminal on the right.
// The caller supplies the copy, the three commands, and the status line, so the
// band stays honest per page (the landing page's steps are the quickstart, the
// reliability page's steps are the failover drill) without forking the layout.
// Visual language matches EnterpriseShowcase: framed dark panel, drifting
// glows over a masked grid, conic-bordered card, pointer spotlight.

import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, ArrowUpRight, Check, Copy, ShieldCheck, Terminal } from "lucide-react";

export interface QuickstartStep {
  title: string;
  detail: string;
  command: string;
}

export interface QuickstartBandProps {
  /** Heading id for `aria-labelledby`; must be unique per page. */
  id: string;
  /** Two-digit section index shown before the eyebrow, e.g. "02". */
  index: string;
  eyebrow: string;
  title: ReactNode;
  body: string;
  primary: { label: string; to: string };
  secondary?: { label: string; to: string };
  /** Optional external link rendered after the two buttons. */
  tertiary?: { label: string; href: string };
  steps: QuickstartStep[];
  /** Mono sign-off under the actions; pass accent markup for the tail. */
  motto: ReactNode;
  status?: { left: string; right: string };
}

/** Track the pointer so the step's radial spotlight follows it. */
function trackSpotlight(e: PointerEvent<HTMLElement>) {
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--x", `${e.clientX - r.left}px`);
  el.style.setProperty("--y", `${e.clientY - r.top}px`);
}

function TerminalCard(props: { steps: QuickstartStep[]; status: { left: string; right: string } }) {
  const [copied, setCopied] = useState<number | null>(null);
  const [copiedAll, setCopiedAll] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  async function copy(text: string, mark: () => void) {
    try {
      await navigator.clipboard.writeText(text);
      mark();
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        setCopied(null);
        setCopiedAll(false);
      }, 1500);
    } catch {
      // Clipboard can be denied in an insecure context; leave the commands
      // selectable rather than claiming a copy that did not happen.
    }
  }

  const allCommands = props.steps.map((s) => s.command).join("\n");

  return (
    <div className="relative min-w-0">
      {/* glow pooled under the card */}
      <div
        className="pointer-events-none absolute left-1/2 top-1/2 h-[320px] w-[440px] max-w-full -translate-x-1/2 -translate-y-1/2 rounded-full blur-3xl"
        style={{ background: "radial-gradient(circle, rgba(59,130,246,0.16) 0%, rgba(139,92,246,0.06) 45%, transparent 70%)" }}
        aria-hidden
      />

      <div className="wiwi-conic-border relative shadow-[0_40px_100px_-40px_rgba(59,130,246,0.4)]">
        <div className="relative overflow-hidden rounded-[15px] bg-[#070707]">
          <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-80" aria-hidden />

          {/* window chrome */}
          <div className="flex h-11 items-center gap-2 border-b border-white/[0.06] bg-white/[0.015] px-3.5 sm:px-4">
            <div className="flex items-center gap-1.5" aria-hidden>
              <span className="size-2.5 rounded-full bg-[#ff5f57]/70" />
              <span className="size-2.5 rounded-full bg-[#febc2e]/60" />
              <span className="size-2.5 rounded-full bg-[#28c840]/60" />
            </div>
            <span className="ml-2 inline-flex min-w-0 items-center gap-1.5 truncate rounded-md border border-white/[0.06] bg-black/40 px-2 py-0.5 font-mono text-[11px] text-[var(--admin-text-muted)]">
              <Terminal size={11} className="shrink-0 text-blue-400/80" aria-hidden />
              quickstart.sh
            </span>
            <button
              type="button"
              aria-label="Copy all quickstart commands"
              aria-pressed={copiedAll}
              onClick={() => copy(allCommands, () => setCopiedAll(true))}
              className="ml-auto inline-flex min-h-11 min-w-11 items-center gap-1.5 rounded-[7px] border border-white/[0.06] px-2.5 font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--admin-text-dim)] transition-colors hover:border-blue-400/30 hover:bg-blue-500/[0.06] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60"
            >
              {copiedAll ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
              {copiedAll ? "Copied" : "Copy all"}
            </button>
          </div>

          <ol className="p-2.5 sm:p-3">
            {props.steps.map((step, i) => {
              const isLast = i === props.steps.length - 1;
              return (
                <li
                  key={step.title}
                  onPointerMove={trackSpotlight}
                  className="group/step relative flex gap-3 overflow-hidden rounded-xl px-2 py-3 transition-colors hover:bg-white/[0.02] focus-within:bg-white/[0.02]"
                >
                  <span
                    className="ent-spotlight pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover/step:opacity-100"
                    aria-hidden
                  />
                  {!isLast && (
                    <span
                      aria-hidden
                      className="absolute left-[21.5px] top-12 h-[calc(100%-28px)] w-px overflow-hidden bg-gradient-to-b from-white/[0.14] to-white/[0.03]"
                    >
                      <span
                        className="qs-spine-beam absolute inset-x-0 h-6 bg-gradient-to-b from-transparent via-blue-400 to-transparent"
                        style={{ animationDelay: `${i * 0.9}s` }}
                      />
                    </span>
                  )}
                  <span className="relative z-10 mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-white/[0.08] bg-gradient-to-b from-[#121212] to-[#0a0a0a] font-mono text-[10px] font-semibold tracking-[0.04em] text-[var(--admin-text-dim)] shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] transition-[color,border-color,box-shadow] duration-300 group-hover/step:border-blue-400/40 group-hover/step:text-blue-300 group-hover/step:shadow-[0_0_18px_-2px_rgba(59,130,246,0.55)]">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <div className="relative min-w-0 flex-1">
                    <h3 className="text-[13.5px] font-medium tracking-[-0.005em] text-[var(--admin-text)]">
                      {step.title}
                    </h3>
                    <p className="mt-0.5 text-[11.5px] leading-snug text-[var(--admin-text-dim)]">
                      {step.detail}
                    </p>
                    <div className="mt-2.5 flex min-h-10 items-center gap-1.5 rounded-lg border border-white/[0.05] bg-black/60 pl-2.5 pr-1 shadow-[inset_0_1px_2px_rgba(0,0,0,0.6)] transition-colors group-hover/step:border-blue-400/15">
                      <span aria-hidden className="select-none font-mono text-[11px] text-emerald-400/80">
                        $
                      </span>
                      <code className="min-w-0 flex-1 overflow-x-auto py-2 font-mono text-[11.5px] leading-snug whitespace-nowrap text-[var(--admin-text-muted)]">
                        {step.command}
                      </code>
                      <button
                        type="button"
                        aria-label={`Copy ${step.title.toLowerCase()} command`}
                        aria-pressed={copied === i}
                        onClick={() => copy(step.command, () => setCopied(i))}
                        className="inline-flex size-11 shrink-0 items-center justify-center rounded-[7px] text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.05] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60 active:scale-[0.96]"
                      >
                        {copied === i ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>

          {/* status bar */}
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 border-t border-white/[0.06] bg-gradient-to-r from-emerald-400/[0.04] via-white/[0.01] to-transparent px-3.5 py-2.5 sm:px-4">
            <span className="flex items-center gap-2 font-mono text-[10.5px] text-emerald-300/90">
              <span className="hero-live-dot text-emerald-400" aria-hidden />
              {props.status.left}
              <span className="qs-caret inline-block h-3 w-[6px] bg-emerald-300/70" aria-hidden />
            </span>
            <span className="rounded-full border border-white/[0.06] bg-white/[0.02] px-2 py-0.5 font-mono text-[10px] text-[var(--admin-text-dim)]">
              {props.status.right}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function QuickstartBand(props: QuickstartBandProps) {
  return (
    <section aria-labelledby={props.id} className="scroll-reveal relative py-20 sm:py-28 md:py-32">
      <div className="relative mx-auto max-w-6xl px-4 sm:px-6">
        {/* soft outer halo so the panel lifts off the page */}
        <div
          className="pointer-events-none absolute -inset-x-2 -inset-y-10 -z-10 opacity-70 blur-3xl"
          style={{ background: "radial-gradient(ellipse 60% 50% at 50% 50%, rgba(34,211,238,0.07), transparent 70%)" }}
          aria-hidden
        />

        <div className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)] shadow-[0_40px_120px_-40px_rgba(0,0,0,0.9)] sm:rounded-[32px]">
          <div
            className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/[0.035] via-transparent to-transparent"
            aria-hidden
          />
          <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70" aria-hidden />

          {/* ambient backdrop: panning grid wash + cyan/blue/violet glows */}
          <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
            <div className="hero-grid absolute inset-0 opacity-60" />
            <div
              className="hero-orb-1 absolute -left-32 -top-32 h-[440px] w-[440px] rounded-full"
              style={{ background: "radial-gradient(circle, rgba(34,211,238,0.12) 0%, transparent 62%)" }}
            />
            <div
              className="hero-orb-2 absolute -right-24 top-1/4 h-[420px] w-[420px] rounded-full"
              style={{ background: "radial-gradient(circle, rgba(139,92,246,0.14) 0%, transparent 62%)" }}
            />
            <div
              className="absolute inset-x-0 bottom-0 h-[260px]"
              style={{ background: "radial-gradient(ellipse 70% 100% at 50% 120%, rgba(59,130,246,0.10), transparent 70%)" }}
            />
          </div>

          <div className="relative grid items-center gap-12 px-6 py-14 sm:px-10 sm:py-16 lg:grid-cols-[minmax(0,0.94fr)_minmax(0,1.06fr)] lg:gap-14 lg:px-14 lg:py-20">
            <div className="min-w-0 text-center lg:text-left">
              <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/[0.06] px-3.5 py-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-blue-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                <span className="text-blue-200">{props.index}</span>
                <span aria-hidden className="text-blue-400/50">·</span>
                {props.eyebrow}
                <span className="hero-live-dot text-blue-400" aria-hidden />
              </div>

              <h2
                id={props.id}
                className="mt-6 text-4xl font-bold leading-[1.04] tracking-[-0.035em] text-[var(--admin-text)] sm:text-5xl lg:text-[56px]"
              >
                {props.title}
              </h2>

              <p className="mx-auto mt-6 max-w-md text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base lg:mx-0">
                {props.body}
              </p>

              <div className="mt-10 flex flex-col items-center gap-3 sm:flex-row sm:flex-wrap sm:justify-center lg:justify-start">
                <span className="relative isolate inline-flex w-full sm:w-auto">
                  <span className="hero-cta-ring" aria-hidden />
                  <Link
                    to={props.primary.to}
                    className="wiwi-shimmer group inline-flex h-12 w-full items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-7 text-[15px] font-semibold text-white shadow-lg shadow-brand-600/25 transition-[transform,filter] duration-150 hover:brightness-110 focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--admin-bg)] focus-visible:outline-none active:scale-[0.98] sm:w-auto"
                  >
                    {props.primary.label}
                    <ArrowRight
                      size={17}
                      className="transition-transform duration-150 group-hover:translate-x-0.5"
                    />
                  </Link>
                </span>
                {props.secondary && (
                  <Link
                    to={props.secondary.to}
                    className="inline-flex min-h-12 w-full items-center justify-center gap-1.5 rounded-full border border-white/[0.09] bg-white/[0.02] px-6 text-[14px] font-medium text-[var(--admin-text)] backdrop-blur transition-colors hover:border-white/[0.18] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
                  >
                    {props.secondary.label}
                  </Link>
                )}
                {props.tertiary && (
                  <a
                    href={props.tertiary.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group inline-flex min-h-12 w-full items-center justify-center gap-1 px-3 text-[13px] font-medium text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
                  >
                    {props.tertiary.label}
                    <ArrowUpRight
                      size={14}
                      aria-hidden
                      className="transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
                    />
                  </a>
                )}
              </div>

              <div className="mx-auto mt-8 inline-flex max-w-sm items-start gap-2 rounded-xl border border-white/[0.05] bg-white/[0.015] px-3.5 py-2.5 text-left lg:mx-0">
                <ShieldCheck size={14} className="mt-px shrink-0 text-emerald-400/80" aria-hidden />
                <p className="font-mono text-[10.5px] leading-relaxed text-[var(--admin-text-dim)]">
                  {props.motto}
                </p>
              </div>
            </div>

            <TerminalCard
              steps={props.steps}
              status={props.status ?? { left: "listening on 0.0.0.0:4000", right: "GET /health · ok" }}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
