// QuickstartBand — the closing "get started" band shared by the landing page
// and the reliability page.
//
// It is one component because it is one design: a two-column close with the
// editorial argument on the left and a live-looking terminal on the right. The
// caller supplies the copy, the three commands, and the status line, so the
// band stays honest per page (the landing page's steps are the quickstart, the
// reliability page's steps are the failover drill) without forking the layout.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, ArrowUpRight, Check, Copy } from "lucide-react";

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
      <div className="hero-grid pointer-events-none absolute -inset-10 opacity-50" aria-hidden />
      <div
        className="pointer-events-none absolute left-1/2 top-1/2 h-[280px] w-[400px] -translate-x-1/2 -translate-y-1/2 rounded-full blur-3xl"
        style={{ background: "radial-gradient(circle, rgba(59,130,246,0.12) 0%, transparent 70%)" }}
        aria-hidden
      />

      <div className="relative overflow-hidden rounded-2xl border border-white/[0.08] bg-[#070707] shadow-[0_32px_90px_-40px_rgba(0,0,0,0.95)]">
        <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-80" aria-hidden />

        <div className="flex h-11 items-center gap-2 border-b border-white/[0.06] px-3.5 sm:px-4">
          <div className="flex items-center gap-1.5" aria-hidden>
            <span className="size-1.5 rounded-full bg-white/[0.16]" />
            <span className="size-1.5 rounded-full bg-white/[0.11]" />
            <span className="size-1.5 rounded-full bg-white/[0.08]" />
          </div>
          <span className="ml-1 truncate font-mono text-[11px] text-[var(--admin-text-dim)]">
            quickstart.sh
          </span>
          <button
            type="button"
            aria-label="Copy all quickstart commands"
            aria-pressed={copiedAll}
            onClick={() => copy(allCommands, () => setCopiedAll(true))}
            className="ml-auto inline-flex min-h-11 min-w-11 items-center gap-1.5 rounded-[7px] border border-white/[0.06] px-2.5 font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--admin-text-dim)] transition-colors hover:border-white/[0.12] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60"
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
                className="group/step relative flex gap-3 rounded-xl px-1.5 py-2.5 transition-colors hover:bg-white/[0.025] focus-within:bg-white/[0.025]"
              >
                {!isLast && (
                  <span
                    aria-hidden
                    className="absolute left-[19px] top-11 h-[calc(100%-24px)] w-px bg-gradient-to-b from-white/[0.12] to-transparent"
                  />
                )}
                <span className="relative z-10 mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border border-white/[0.08] bg-[#0d0d0d] font-mono text-[10px] font-semibold tracking-[0.04em] text-[var(--admin-text-dim)] transition-colors group-hover/step:border-blue-400/40 group-hover/step:text-blue-300">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="text-[13px] font-medium text-[var(--admin-text)]">{step.title}</h3>
                  <p className="mt-0.5 text-[11px] leading-snug text-[var(--admin-text-dim)]">
                    {step.detail}
                  </p>
                  <div className="mt-2 flex min-h-10 items-center gap-1.5 rounded-lg border border-white/[0.05] bg-black/50 pl-2.5 pr-1">
                    <span aria-hidden className="select-none font-mono text-[11px] text-blue-400/80">
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

        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 border-t border-white/[0.06] bg-white/[0.015] px-3.5 py-2.5 sm:px-4">
          <span className="flex items-center gap-1.5 font-mono text-[10.5px] text-emerald-300/90">
            <span className="size-1.5 rounded-full bg-emerald-400" aria-hidden />
            {props.status.left}
          </span>
          <span className="font-mono text-[10px] text-[var(--admin-text-dim)]">
            {props.status.right}
          </span>
        </div>
      </div>
    </div>
  );
}

export function QuickstartBand(props: QuickstartBandProps) {
  return (
    <section
      aria-labelledby={props.id}
      className="relative overflow-hidden py-24 sm:py-32 md:py-40"
    >
      <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />
      <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
        <div className="hero-grid absolute inset-x-0 bottom-0 h-[320px] opacity-35" />
        <div
          className="absolute -left-40 -top-28 h-[380px] w-[380px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(34,211,238,0.045), transparent 62%)" }}
        />
      </div>
      <div className="relative mx-auto grid max-w-6xl items-center gap-14 px-6 lg:grid-cols-[minmax(0,0.94fr)_minmax(0,1.06fr)] lg:gap-16">
        <div className="min-w-0">
          <p className="font-mono text-[11px] uppercase leading-none tracking-[0.22em] text-[var(--admin-text-dim)]">
            <span className="text-blue-400/90">{props.index}</span>
            <span aria-hidden> · </span>
            {props.eyebrow}
          </p>
          <h2
            id={props.id}
            className="mt-5 text-4xl font-bold leading-[1.04] tracking-[-0.035em] text-[var(--admin-text)] sm:text-5xl lg:text-[54px]"
          >
            {props.title}
          </h2>
          <p className="mt-6 max-w-md text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            {props.body}
          </p>
          <div className="mt-10 flex flex-col items-start gap-3 sm:flex-row sm:items-center">
            <Link
              to={props.primary.to}
              className="wiwi-shimmer group inline-flex h-12 w-full items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-500 to-brand-700 px-6 text-base font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110 focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--admin-bg)] focus-visible:outline-none sm:w-auto"
            >
              {props.primary.label}
              <ArrowRight
                size={17}
                className="transition-transform duration-150 group-hover:translate-x-0.5"
              />
            </Link>
            {props.secondary && (
              <Link
                to={props.secondary.to}
                className="inline-flex min-h-12 w-full items-center justify-center gap-1.5 rounded-full border border-white/[0.09] px-6 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.18] hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
              >
                {props.secondary.label}
              </Link>
            )}
            {props.tertiary && (
              <a
                href={props.tertiary.href}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-12 w-full items-center justify-center gap-1 px-3 text-[13px] font-medium text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
              >
                {props.tertiary.label}
                <ArrowUpRight size={14} aria-hidden />
              </a>
            )}
          </div>
          <p className="mt-7 max-w-sm font-mono text-[10.5px] leading-relaxed text-[var(--admin-text-dim)]">
            {props.motto}
          </p>
        </div>
        <TerminalCard
          steps={props.steps}
          status={props.status ?? { left: "listening on 0.0.0.0:4000", right: "GET /health · ok" }}
        />
      </div>
    </section>
  );
}
