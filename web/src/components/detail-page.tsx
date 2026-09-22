// Shared chrome for public-site detail pages (/blog/:slug, /migration/:slug,
// /templates/:slug, /agents/:slug, /compare/:slug). Mirrors the layout that
// GuideDetail pioneered — back link, badge + title + intro header, numbered
// step cards with terminal-styled code blocks — so every detail surface
// follows one convention instead of five parallel ones.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Check, Copy, FileQuestion } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// ── code block with copy ───────────────────────────────────────────────────

export function DetailCodeBlock(props: { code: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <div className="group relative overflow-hidden rounded-xl border border-[var(--admin-border)] bg-zinc-950">
      <div className="flex items-center gap-2 border-b border-[var(--admin-border)] bg-white/[0.02] px-4 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" aria-hidden />
        <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" aria-hidden />
        <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" aria-hidden />
        {props.label && (
          <span className="ml-1.5 font-mono text-[11px] text-[var(--admin-text-dim)]">
            {props.label}
          </span>
        )}
        <button
          type="button"
          onClick={async () => {
            await navigator.clipboard.writeText(props.code);
            setCopied(true);
            timer.current = setTimeout(() => setCopied(false), 1500);
          }}
          aria-label="Copy code"
          className="absolute right-2 flex h-11 w-11 items-center justify-center rounded-md text-[var(--admin-text-dim)] opacity-100 transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 sm:right-2.5 sm:top-2.5 sm:h-8 sm:w-8 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
      <pre
        tabIndex={0}
        role="group"
        aria-label={props.label ? `${props.label} code example` : "Code example"}
        className="overflow-x-auto p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
      >
        <code className="text-[12.5px] leading-relaxed text-zinc-200" style={{ fontFamily: MONO }}>
          {props.code}
        </code>
      </pre>
    </div>
  );
}

// ── page header ────────────────────────────────────────────────────────────

export function DetailHeader(props: {
  backTo: string;
  backLabel: string;
  badge?: string;
  title: string;
  intro: string;
  meta?: string;
}) {
  return (
    <div className="space-y-6">
      <Link
        to={props.backTo}
        className="inline-flex items-center gap-1.5 text-[13px] font-medium text-[var(--admin-text-dim)] transition-colors hover:text-blue-400"
      >
        <ArrowLeft size={14} /> {props.backLabel}
      </Link>
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          {props.badge && <span className="admin-badge admin-badge-blue">{props.badge}</span>}
          {props.meta && (
            <span className="font-mono text-[11px] text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
              {props.meta}
            </span>
          )}
        </div>
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          {props.title}
        </h1>
        <p className="max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          {props.intro}
        </p>
      </header>
    </div>
  );
}

// ── numbered steps ─────────────────────────────────────────────────────────

export interface DetailStep {
  title: string;
  body: string;
  code?: { code: string; label?: string };
}

export function StepList(props: { steps: DetailStep[] }) {
  return (
    <ol className="space-y-6">
      {props.steps.map((step, i) => (
        <li key={step.title}>
          <Card className="p-5">
            <div className="flex items-start gap-3.5">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--admin-border)] bg-white/[0.03] text-[12px] font-semibold text-blue-300">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-3">
                <h2 className="text-[15px] font-semibold text-[var(--admin-text)]">
                  {step.title}
                </h2>
                <p className="text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
                  {step.body}
                </p>
                {step.code && <DetailCodeBlock code={step.code.code} label={step.code.label} />}
              </div>
            </div>
          </Card>
        </li>
      ))}
    </ol>
  );
}

// ── fact table ─────────────────────────────────────────────────────────────

export interface FactRow {
  label: string;
  value: ReactNode;
}

export function FactTable(props: { title?: string; rows: FactRow[] }) {
  return (
    <Card className="overflow-hidden p-0">
      {props.title && (
        <div className="border-b border-[var(--admin-border)] bg-white/[0.015] px-5 py-3">
          <span className="admin-label">{props.title}</span>
        </div>
      )}
      <dl className="m-0">
        {props.rows.map((row, i) => (
          <div
            key={row.label}
            className={`flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 px-5 py-3 ${
              i < props.rows.length - 1 ? "border-b border-[var(--admin-border)]" : ""
            }`}
          >
            <dt className="admin-label shrink-0">{row.label}</dt>
            <dd className="m-0 min-w-0 flex-1 text-right text-[13px] text-[var(--admin-text)]">
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

// ── prose sections (articles) ──────────────────────────────────────────────

export interface ProseBlock {
  heading?: string;
  paragraphs?: string[];
  bullets?: string[];
  code?: { code: string; label?: string };
  callout?: string;
}

function InlineCode({ text }: { text: string }) {
  // Renders `code` spans inside prose: split on backticks.
  const parts = text.split("`");
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <code
            key={i}
            className="rounded-md border border-white/[0.075] bg-white/[0.045] px-1.5 py-0.5 text-[0.85em] text-blue-200"
            style={{ fontFamily: MONO }}
          >
            {part}
          </code>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

export function ProseSection(props: { block: ProseBlock }) {
  const { block } = props;
  return (
    <section className="space-y-4">
      {block.heading && (
        <h2 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {block.heading}
        </h2>
      )}
      {block.paragraphs?.map((p, i) => (
        <p key={i} className="text-[14.5px] leading-relaxed text-[var(--admin-text-muted)]">
          <InlineCode text={p} />
        </p>
      ))}
      {block.bullets && (
        <ul className="space-y-2">
          {block.bullets.map((b, i) => (
            <li key={i} className="flex items-start gap-2.5 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-blue-400" aria-hidden />
              <InlineCode text={b} />
            </li>
          ))}
        </ul>
      )}
      {block.code && <DetailCodeBlock code={block.code.code} label={block.code.label} />}
      {block.callout && (
        <div className="rounded-xl border border-blue-500/15 bg-blue-500/[0.05] px-4 py-3 text-[13.5px] leading-relaxed text-blue-100">
          <InlineCode text={block.callout} />
        </div>
      )}
    </section>
  );
}

// ── not-found ──────────────────────────────────────────────────────────────

export function DetailNotFound(props: { backTo: string; backLabel: string; what: string }) {
  return (
    <div className="mx-auto max-w-2xl space-y-6 py-20 text-center">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-[var(--admin-border)] bg-white/[0.02]">
        <FileQuestion className="h-7 w-7 text-[var(--admin-text-dim)]" />
      </div>
      <h1 className="text-2xl font-semibold text-[var(--admin-text)]">
        {props.what} not found
      </h1>
      <p className="text-[14px] text-[var(--admin-text-muted)]">
        Nothing exists at this address.
      </p>
      <Link
        to={props.backTo}
        className="inline-flex items-center gap-1.5 text-[13px] font-medium text-blue-400 hover:text-blue-300"
      >
        <ArrowLeft size={14} /> {props.backLabel}
      </Link>
    </div>
  );
}

// ── closing CTA ────────────────────────────────────────────────────────────

export function DetailCta(props: {
  title: string;
  body: string;
  primary: { label: string; to: string };
  secondary?: { label: string; to: string };
}) {
  return (
    <div className="rounded-2xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent p-8 text-center">
      <h2 className="text-xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
        {props.title}
      </h2>
      <p className="mx-auto mt-2 max-w-md text-[14px] text-[var(--admin-text-muted)]">
        {props.body}
      </p>
      <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
        <Link
          to={props.primary.to}
          className="inline-flex h-10 items-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110"
        >
          {props.primary.label} <ArrowLeft size={13} className="rotate-180" />
        </Link>
        {props.secondary && (
          <Link
            to={props.secondary.to}
            className="inline-flex h-10 items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            {props.secondary.label}
          </Link>
        )}
      </div>
    </div>
  );
}
