// ClosingCta — the one "get started" band that ends a public page.
//
// The marketing pages used to close with the same bordered, centered box,
// which made them read as templates. This replaces that with a numbered
// command ledger: the copy sits on the left, and the actual commands sit on
// the right as a terminal panel whose steps are numbered, copyable, and
// verified against the quickstart. It is deliberately borderless on the
// outside so it reads as the page's conclusion, not one more card.

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Check, Copy } from "lucide-react";

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
    <li className="relative grid grid-cols-[2.25rem_minmax(0,1fr)] gap-3 pb-4 last:pb-0">
      <div className="relative flex justify-center">
        {index > 0 && (
          <span
            aria-hidden
            className="absolute -top-4 bottom-1/2 left-1/2 w-px -translate-x-1/2 bg-gradient-to-b from-transparent via-white/10 to-white/10"
          />
        )}
        <span
          className="relative mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-[var(--admin-border)] bg-[#0b0b0b] font-mono text-[10px] font-medium tracking-[0.06em] text-[var(--admin-text-dim)]"
          style={{ fontFamily: MONO }}
        >
          {String(index + 1).padStart(2, "0")}
        </span>
      </div>
      <div className="min-w-0 space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[12.5px] font-medium text-[var(--admin-text)]">{step.title}</span>
          <button
            type="button"
            aria-label={`Copy step ${index + 1}: ${step.title}`}
            onClick={async () => onResult(index, await writeClipboard(step.command))}
            className="flex size-11 shrink-0 items-center justify-center rounded-md text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--admin-accent)]/60"
          >
            {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
          </button>
        </div>
        <div className="min-w-0 rounded-md border border-white/[0.06] bg-black/50 px-3 py-2">
          <code
            className="block whitespace-pre-wrap break-words text-[12px] leading-relaxed text-zinc-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--admin-accent)]/40"
            style={{ fontFamily: MONO, overflowWrap: "anywhere" }}
          >
            <span aria-hidden className="select-none text-[var(--admin-accent)]/70">
              ${" "}
            </span>
            {step.command}
          </code>
        </div>
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
      className="relative border-t border-[var(--admin-border)] pt-10 sm:pt-12"
    >
      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)] lg:gap-12">
        <div className="min-w-0">
          <p
            className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--admin-text-dim)]"
            style={{ fontFamily: MONO }}
          >
            {props.eyebrow}
          </p>
          <h2
            id={headingId}
            className="mt-3 max-w-[18ch] text-[clamp(1.55rem,3.4vw,2.35rem)] font-semibold leading-[1.08] tracking-[-0.03em] text-[var(--admin-text)]"
          >
            {props.title}
          </h2>
          <p className="mt-3 max-w-[46ch] text-[14.5px] leading-relaxed text-[var(--admin-text-muted)]">
            {props.body}
          </p>
          <div className="mt-6 flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
            <Link
              to={props.primary.to}
              className="admin-btn admin-btn-primary inline-flex min-h-11 items-center justify-center"
            >
              {props.primary.label}
              <ArrowRight size={15} className="ml-2" aria-hidden />
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
            <p className="mt-4 text-[12.5px] leading-relaxed text-[var(--admin-text-dim)]">
              {props.footnote}
            </p>
          )}
        </div>

        <div className="min-w-0 overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[#070707]">
          <div className="flex items-center gap-2 border-b border-[var(--admin-border)] px-4 py-2.5">
            <span className="h-1.5 w-1.5 rounded-full bg-white/25" aria-hidden />
            <span
              className="font-mono text-[11px] text-[var(--admin-text-dim)]"
              style={{ fontFamily: MONO }}
            >
              quickstart.sh
            </span>
            <span
              className="ml-auto font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--admin-text-dim)]"
              style={{ fontFamily: MONO }}
            >
              bash
            </span>
          </div>

          <ol className="m-0 list-none p-4">
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

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[var(--admin-border)] bg-white/[0.015] px-4 py-2.5">
            <span
              className="inline-flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--admin-text-dim)]"
              style={{ fontFamily: MONO }}
            >
              <span
                className="h-1.5 w-1.5 rounded-full bg-emerald-400 motion-safe:animate-pulse"
                aria-hidden
              />
              listening on 0.0.0.0:4000
            </span>
            <span
              className="ml-auto font-mono text-[10.5px] text-[var(--admin-text-dim)]"
              style={{ fontFamily: MONO }}
            >
              MIT · nothing phones home
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
