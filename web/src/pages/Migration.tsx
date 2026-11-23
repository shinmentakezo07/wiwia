// Migration — guides to switch from other LLM gateways and providers. Listing
// backed by the shared migration data module; each row opens its guide at
// /migration/:slug. Visual vocabulary (grid wash, pill eyebrow, numbered
// section intros, ClosingCta) matches the Open Source page.

import { Link } from "react-router-dom";
import { ArrowRight, ArrowRightLeft } from "lucide-react";
import { ClosingCta } from "@/components/ClosingCta";
import { MIGRATIONS } from "@/data/migrations";
import type { Migration } from "@/data/migrations";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

function fact(m: Migration, label: string): string | undefined {
  return m.facts.find((f) => f.label === label)?.value;
}

/* Hero visual: the whole migration for an OpenAI-compatible client, as a diff.
   Two lines change; that is the page's one claim. Decorative. */
function EnvDiff() {
  const rows: { sign: "-" | "+" | " "; text: string }[] = [
    { sign: "-", text: "OPENAI_BASE_URL=https://openrouter.ai/api/v1" },
    { sign: "-", text: "OPENAI_API_KEY=sk-or-v1-…" },
    { sign: "+", text: "OPENAI_BASE_URL=http://localhost:4000/v1" },
    { sign: "+", text: "OPENAI_API_KEY=sk-wiwi-…" },
    { sign: " ", text: "MODEL=anthropic/claude-sonnet-4  # unchanged" },
  ];
  return (
    <div className="relative mx-auto w-full min-w-0 max-w-md" aria-hidden>
      <div className="overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[#070707]/95 shadow-[0_30px_80px_-30px_rgba(59,130,246,0.35)]">
        <div className="flex items-center justify-between border-b border-[var(--admin-border)] px-4 py-2.5">
          <div className="flex gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
            <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
            <span className="h-2.5 w-2.5 rounded-full bg-white/10" />
          </div>
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--admin-text-dim)]">.env · 2 changed</span>
        </div>
        <pre className="overflow-x-auto py-3 text-[11.5px] leading-[1.9]" style={{ fontFamily: MONO }}>
          {rows.map((r, i) => (
            <span
              key={i}
              className={`block px-4 ${
                r.sign === "-"
                  ? "bg-rose-500/[0.07] text-rose-200/80"
                  : r.sign === "+"
                    ? "bg-emerald-500/[0.07] text-emerald-200"
                    : "text-[var(--admin-text-dim)]"
              }`}
            >
              <span className="mr-3 select-none opacity-60">{r.sign}</span>
              {r.text}
            </span>
          ))}
        </pre>
      </div>
    </div>
  );
}

function SectionIntro(props: { index: string; title: string; body?: string }) {
  return (
    <div className="mb-7 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between sm:gap-8">
      <div>
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-blue-300/80">{props.index}</span>
        <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.015em] text-[var(--admin-text)] sm:text-[28px]">
          {props.title}
        </h2>
      </div>
      {props.body && (
        <p className="max-w-sm text-[14px] leading-relaxed text-[var(--admin-text-muted)] sm:text-right">{props.body}</p>
      )}
    </div>
  );
}

const PHASES: { title: string; body: string }[] = [
  {
    title: "Repoint",
    body: "Swap the base URL and key. OpenAI Chat, OpenAI Responses, and Anthropic Messages clients each have a matching endpoint.",
  },
  {
    title: "Map",
    body: "Keep the model names your code already sends, and map each one to a provider account in wiwi.yaml.",
  },
  {
    title: "Verify",
    body: "Send one request and check the request log, then move budgets, fallbacks, and team keys across.",
  },
];

export function MigrationPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-20 pb-16 sm:space-y-24">
      {/* ── hero ── */}
      <section className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)] sm:rounded-[32px]">
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="hero-grid absolute inset-x-0 top-0 h-[560px] opacity-60" />
          <div
            className="absolute -left-28 -top-32 h-[420px] w-[420px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(59,130,246,0.14) 0%, transparent 62%)" }}
          />
        </div>
        <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-70" aria-hidden />

        <div className="relative grid items-center gap-12 px-6 py-14 sm:px-10 sm:py-16 lg:grid-cols-[1.1fr_1fr] lg:gap-14 lg:px-14 lg:py-20">
          <div className="min-w-0 text-center lg:text-left">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/[0.06] px-3.5 py-1.5 text-blue-300">
              <ArrowRightLeft size={12} aria-hidden />
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em]">
                {MIGRATIONS.length} migration guides
              </span>
            </div>
            <h1 className="text-3xl font-semibold leading-[1.08] tracking-[-0.025em] text-[var(--admin-text)] sm:text-4xl lg:text-[50px]">
              Move to wiwi
              <br />
              <span className="hero-gradient-text">without a rewrite</span>
            </h1>
            <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)] sm:text-base lg:mx-0">
              Coming from another gateway or a hosted router? Your client most likely already speaks
              a dialect wiwi accepts, so the switch is a base URL, a key, and a model map. Each guide
              covers the exact steps and what changes.
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row lg:justify-start">
              <a
                href="#guides"
                className="group inline-flex h-11 w-full items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-7 text-sm font-semibold text-white shadow-lg shadow-brand-600/25 transition-[transform,filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/60 active:scale-[0.98] sm:w-auto"
              >
                Pick your source
                <ArrowRight size={15} className="transition-transform duration-150 group-hover:translate-x-0.5 motion-reduce:transition-none" />
              </a>
              <Link
                to="/compare"
                className="inline-flex h-11 w-full items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.02] px-6 text-sm font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.16] hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 sm:w-auto"
              >
                Compare gateways
              </Link>
            </div>
          </div>
          <EnvDiff />
        </div>
      </section>

      {/* ── guides: a ledger, one row per source ── */}
      <section id="guides" className="scroll-mt-24">
        <SectionIntro
          index=".01 · guides"
          title="Choose where you're coming from"
          body="Each guide lists the code changes, the config to copy, and the caveats worth knowing before you switch."
        />
        <ul className="divide-y divide-[var(--admin-border)] overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)]">
          {MIGRATIONS.map((m, i) => {
            const time = fact(m, "Typical time");
            return (
              <li key={m.slug}>
                <Link
                  to={`/migration/${m.slug}`}
                  className="group grid min-h-11 grid-cols-[auto_1fr_auto] items-center gap-4 px-5 py-5 transition-colors hover:bg-white/[0.025] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/60 sm:gap-6 sm:px-6"
                >
                  <span
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-white/[0.06] bg-white/[0.02] text-[14px] font-bold text-[var(--admin-text)] transition-colors group-hover:border-blue-500/30"
                    aria-hidden
                  >
                    {m.icon}
                  </span>
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)] transition-colors group-hover:text-blue-300">
                        {m.title}
                      </span>
                      <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--admin-text-dim)]">
                        {String(i + 1).padStart(2, "0")} · {m.steps.length} steps{time ? ` · ${time}` : ""}
                      </span>
                    </span>
                    <span className="mt-1.5 block text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{m.summary}</span>
                  </span>
                  <ArrowRight
                    size={16}
                    className="text-[var(--admin-text-dim)] transition-[transform,color] duration-150 group-hover:translate-x-0.5 group-hover:text-blue-300 motion-reduce:transition-none"
                    aria-hidden
                  />
                </Link>
              </li>
            );
          })}
        </ul>
      </section>

      {/* ── the shape every migration shares ── */}
      <section>
        <SectionIntro index=".02 · the pattern" title="Every migration is the same three moves" />
        <ol className="grid gap-px overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-border)] md:grid-cols-3">
          {PHASES.map((p, i) => (
            <li key={p.title} className="bg-[var(--admin-surface)] p-6">
              <span className="font-mono text-[11px] tabular-nums tracking-[0.18em] text-blue-300/80" aria-hidden>
                0{i + 1}
              </span>
              <h3 className="mt-3 text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">{p.title}</h3>
              <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{p.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <ClosingCta
        eyebrow=".03 · not listed?"
        title="Don't see your provider? You probably don't need a guide."
        body="Any client that speaks OpenAI Chat, OpenAI Responses, or Anthropic Messages works against wiwi. Change the base URL and key, and map the model names you send."
        primary={{ label: "Read the quickstart", to: "/docs" }}
        secondary={{ label: "Browse client guides", to: "/guides" }}
        footnote="Self-hosted and MIT-licensed. You pay providers directly."
        commands={[
          { title: "Prepare the config", command: "cp wiwi.yaml.example wiwi.yaml" },
          { title: "Start the gateway", command: "wiwi --config wiwi.yaml" },
          { title: "Repoint your client", command: "export OPENAI_BASE_URL=http://localhost:4000/v1" },
        ]}
      />
    </div>
  );
}
