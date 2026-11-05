// ClosingCta — the one "get started" band that ends a public page.
//
// The marketing pages used to close with the same bordered, centered box,
// which made them read as templates. This replaces that with a numbered
// command ledger: the copy sits on the left, and the actual commands sit on
// the right as a terminal panel whose steps are numbered, copyable, and
// verified against the quickstart. A single framed composition contains the
// editorial introduction and the command ledger without reading as a stock CTA.

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Check, Copy, Terminal } from "lucide-react";

export interface ClosingCtaCommand {
  title: string;
  command: string;
}

export interface ClosingCtaProps {
  eyebrow: string;
  title: string;
  body: string;
  primary: { label: string; to: string };
  secondary?: { label: string; to: string };
  commands: ClosingCtaCommand[];
  /** Short reassurance line under the actions. */
  footnote?: string;
  /** Heading id; derived from the eyebrow by default so two bands can coexist. */
  id?: string;
}

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function StepRow(props: {
  index: number;
  step: ClosingCtaCommand;
  copied: boolean;
  failed: boolean;
  onResult: (index: number, ok: boolean) => void;
}) {
  const { index, step, copied, failed, onResult } = props;
  return (
    <li className="group/step relative grid grid-cols-[1.75rem_minmax(0,1fr)] gap-2.5 px-1 py-4 sm:grid-cols-[2.5rem_minmax(0,1fr)] sm:gap-3">
      <div className="relative flex justify-center">
        {index > 0 && (
          <span
            aria-hidden
            className="absolute -top-4 bottom-1/2 left-1/2 w-px -translate-x-1/2 bg-white/[0.07]"
          />
        )}
        <span
          className={`relative mt-0.5 flex size-7 items-center justify-center rounded-full border font-mono text-[10px] font-medium tracking-[0.06em] transition-colors ${
            copied
              ? "border-emerald-400/25 bg-emerald-400/[0.06] text-emerald-300"
              : "border-white/[0.1] bg-[#111111] text-[var(--admin-text-muted)] group-hover:border-blue-400/30 group-hover:text-blue-300"
          }`}
          style={{ fontFamily: MONO }}
        >
          {copied ? <Check size={12} aria-hidden /> : String(index + 1).padStart(2, "0")}
        </span>
      </div>
      <div className="min-w-0">
        <div className="flex min-h-7 items-center justify-between gap-3">
          <h3 className="text-[12.5px] font-medium text-[var(--admin-text-muted)] transition-colors group-hover:text-[var(--admin-text)]">
            {step.title}
          </h3>
          <button
            type="button"
            aria-label={`Copy step ${index + 1}: ${step.title}`}
            aria-pressed={copied}
            onClick={async () => onResult(index, await writeClipboard(step.command))}
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg border border-transparent text-[var(--admin-text-dim)] transition-[color,background-color,transform] duration-150 hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60 active:scale-95"
          >
            {copied ? <Check size={15} className="text-emerald-400" /> : <Copy size={14} />}
          </button>
        </div>
        <div
          className={`relative min-w-0 overflow-hidden rounded-lg border px-3 py-3 transition-colors duration-200 ${
            copied
              ? "border-emerald-400/20 bg-emerald-400/[0.025]"
              : "border-white/[0.06] bg-black/40 focus-within:border-blue-400/25 hover:border-white/[0.1]"
          }`}
        >
          <code
            className="block whitespace-pre-wrap break-words text-[12px] leading-relaxed text-zinc-300"
            style={{ fontFamily: MONO, overflowWrap: "anywhere" }}
          >
            <span aria-hidden className="select-none text-blue-400/90">
              ${" "}
            </span>
            {step.command}
          </code>
        </div>
        <p className="sr-only" role="status">
          {copied ? `${step.title} command copied.` : ""}
        </p>
        {failed && (
          <p className="text-[11.5px] text-amber-300/90" role="status">
            Clipboard blocked — select the command and copy it manually.
          </p>
        )}
      </div>
    </li>
  );
}

export function ClosingCta(props: ClosingCtaProps) {
  const slug = props.eyebrow
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  const headingId = props.id ?? `cta-${slug}`;
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const [failedIndex, setFailedIndex] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  function record(index: number, ok: boolean) {
    if (!ok) {
      setFailedIndex(index);
      return;
    }
    setFailedIndex(null);
    setCopiedIndex(index);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopiedIndex(null), 1600);
  }

  return (
    <section
      aria-labelledby={headingId}
      className="relative isolate overflow-hidden rounded-2xl border border-white/[0.07] bg-[#080808] px-5 py-7 sm:p-9 lg:px-12 lg:py-12"
    >
      <div
        aria-hidden
        className="hero-grid pointer-events-none absolute inset-x-0 bottom-0 h-64 opacity-20 [mask-image:linear-gradient(to_bottom,transparent,black)]"
      />
      <div
        aria-hidden
        className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70"
      />

      <div className="relative grid items-center gap-10 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1fr)] lg:gap-12">
        <div className="min-w-0 lg:py-4">
          <div className="flex items-center gap-2.5">
            <span className="h-px w-5 bg-blue-400" aria-hidden />
            <p
              className="font-mono text-[11px] uppercase tracking-[0.16em] text-[var(--admin-text-dim)]"
              style={{ fontFamily: MONO }}
            >
              {props.eyebrow}
            </p>
          </div>
          <h2
            id={headingId}
            className="mt-5 max-w-[18ch] text-[clamp(1.85rem,3.4vw,2.5rem)] font-semibold leading-[1.08] tracking-[-0.035em] text-[var(--admin-text)]"
          >
            {props.title}
          </h2>
          <p className="mt-5 max-w-[43ch] text-[14.5px] leading-relaxed text-[var(--admin-text-muted)]">
            {props.body}
          </p>
          <div className="mt-7 flex flex-col items-stretch gap-3 sm:flex-row sm:items-center lg:flex-col lg:items-stretch xl:flex-row xl:items-center">
            <Link
              to={props.primary.to}
              className="admin-btn admin-btn-primary group inline-flex min-h-11 items-center justify-center"
            >
              {props.primary.label}
              <ArrowRight
                size={15}
                className="ml-2 transition-transform duration-150 group-hover:translate-x-0.5 motion-reduce:transform-none"
                aria-hidden
              />
            </Link>
            {props.secondary && (
              <Link
                to={props.secondary.to}
                className="admin-btn admin-btn-ghost inline-flex min-h-11 items-center justify-center"
              >
                {props.secondary.label}
              </Link>
            )}
          </div>
          {props.footnote && (
            <p className="mt-5 flex flex-wrap items-center gap-x-2.5 gap-y-2 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
              {props.footnote.split(" · ").map((item, index, items) => (
                <span key={item} className="inline-flex items-center gap-2">
                  <Check size={11} className="text-emerald-400/80" aria-hidden />
                  {item}
                  {index < items.length - 1 && (
                    <span aria-hidden className="ml-1.5 text-white/20">
                      ·
                    </span>
                  )}
                </span>
              ))}
            </p>
          )}
        </div>

        <div className="relative min-w-0">
          <div
            aria-hidden
            className="hero-grid pointer-events-none absolute -inset-6 opacity-30"
          />
          <div className="relative overflow-hidden rounded-xl border border-white/[0.09] bg-[#0a0a0a] shadow-[0_24px_64px_-32px_rgba(0,0,0,0.9)]">
            <div
              className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-60"
              aria-hidden
            />
            <div className="flex min-h-12 items-center gap-2.5 border-b border-white/[0.07] bg-white/[0.015] px-3.5 sm:px-4">
              <Terminal size={13} className="shrink-0 text-blue-400/80" aria-hidden />
              <span
                className="truncate font-mono text-[11px] text-[var(--admin-text-muted)]"
                style={{ fontFamily: MONO }}
              >
                quickstart.sh
              </span>
              <span
                className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--admin-text-dim)]"
                style={{ fontFamily: MONO }}
              >
                bash
              </span>
            </div>

            <ol className="m-0 list-none divide-y divide-white/[0.05] px-3 py-1 sm:px-4">
              {props.commands.map((step, i) => (
                <StepRow
                  key={step.command}
                  index={i}
                  step={step}
                  copied={copiedIndex === i}
                  failed={failedIndex === i && copiedIndex !== i}
                  onResult={record}
                />
              ))}
            </ol>

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-white/[0.07] bg-white/[0.015] px-3.5 py-3 sm:px-4">
              <span
                className="inline-flex items-center gap-2 font-mono text-[10.5px] text-emerald-300/90"
                style={{ fontFamily: MONO }}
              >
                <span className="size-1.5 rounded-full bg-emerald-400" aria-hidden />
                listening on 0.0.0.0:4000
              </span>
              <span
                className="ml-auto font-mono text-[10.5px] text-[var(--admin-text-dim)]"
                style={{ fontFamily: MONO }}
              >
                MIT · no phone-home
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
