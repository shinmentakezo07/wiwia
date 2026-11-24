// Changelog — release ledger. Entries are newest-first; the first entry is the
// current release. Presentation only — the entry data below is the source of truth.

import { useMemo, useState } from "react";
import { Bug, Layers, Sparkles } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";
import { HERO_BEAMS_COMPACT, HeroBeamBackdrop } from "@/components/HeroBeamBackdrop";

type ChangeKind = "feature" | "fix" | "improvement";
type Filter = "all" | ChangeKind;

interface ChangelogEntry {
  version: string;
  date: string;
  changes: string[];
}

const ENTRIES: ChangelogEntry[] = [
  {
    version: "0.6.0",
    date: "August 2025",
    changes: [
      "Add session auth with public/guarded route split and role-aware admin layout",
      "Add Landing, Signup, Users, Playground, ModelsCatalog, and Docs pages",
      "Add dual-mode Login (master-key admin vs user account)",
      "Fix post-login navigation to /app for master-key admin",
    ],
  },
  {
    version: "0.5.0",
    date: "August 2025",
    changes: [
      "Add cross-provider routing: Claude Code backed by GPT and vice versa",
      "Add OpenRouter adapter with reasoning parameter translation",
      "Fix reasoning/thinking parameter translation between OpenAI and Anthropic",
      "Fix multi-turn conversation tool_result message handling",
    ],
  },
  {
    version: "0.4.0",
    date: "July 2025",
    changes: [
      "Add key pools with smooth weighted round-robin routing",
      "Add per-key cooldowns and automatic retries on transient failures",
      "Add fallback model groups so a flaky upstream never reaches the caller",
      "Add cost tracking with per-key, per-model, per-provider breakdowns",
    ],
  },
  {
    version: "0.3.0",
    date: "July 2025",
    changes: [
      "Add virtual keys with model allowlists, expiry, and spend caps",
      "Add per-key budgets and RPM/TPM rate limits",
      "Add structured request logs with token usage and latency",
      "Add admin web UI (React 19 + TypeScript + Vite + Tailwind 4)",
    ],
  },
  {
    version: "0.2.0",
    date: "June 2025",
    changes: [
      "Add OpenAI Responses (Codex CLI) inbound dialect",
      "Add Anthropic Messages (Claude Code) inbound dialect",
      "Define IRStreamDelta taxonomy as the streaming contract",
      "Add Gemini provider adapter",
    ],
  },
];

const KIND_ORDER: ChangeKind[] = ["feature", "fix", "improvement"];

const KIND_META: Record<
  ChangeKind,
  { label: string; plural: string; icon: LucideIcon; bar: string; text: string; chip: string }
> = {
  feature: {
    label: "Feature",
    plural: "Features",
    icon: Sparkles,
    bar: "bg-emerald-400/70",
    text: "text-emerald-300",
    chip: "border-emerald-400/20 bg-emerald-400/[0.07]",
  },
  fix: {
    label: "Fix",
    plural: "Fixes",
    icon: Bug,
    bar: "bg-amber-400/70",
    text: "text-amber-300",
    chip: "border-amber-400/20 bg-amber-400/[0.07]",
  },
  improvement: {
    label: "Improvement",
    plural: "Improvements",
    icon: Layers,
    bar: "bg-sky-400/70",
    text: "text-sky-300",
    chip: "border-sky-400/20 bg-sky-400/[0.07]",
  },
};

function classifyChange(change: string): ChangeKind {
  if (/^fix\b/i.test(change)) return "fix";
  if (/^add\b/i.test(change)) return "feature";
  return "improvement";
}

/** Drops the leading "Add"/"Fix" verb: the kind icon already carries it. */
function changeText(change: string): string {
  const stripped = change.replace(/^(add|fix)\s+/i, "");
  return stripped.charAt(0).toUpperCase() + stripped.slice(1);
}

function countKinds(changes: string[]) {
  const counts: Record<ChangeKind, number> = { feature: 0, fix: 0, improvement: 0 };
  for (const change of changes) counts[classifyChange(change)] += 1;
  return counts;
}

const TOTAL_CHANGES = ENTRIES.reduce((n, e) => n + e.changes.length, 0);
const TOTAL_COUNTS = countKinds(ENTRIES.flatMap((e) => e.changes));

function ScopeBar({ counts, total }: { counts: Record<ChangeKind, number>; total: number }) {
  return (
    <div
      className="flex h-1 w-full overflow-hidden rounded-full bg-white/[0.06] sm:w-40"
      role="img"
      aria-label={`Scope: ${counts.feature} features, ${counts.fix} fixes, ${counts.improvement} improvements`}
    >
      {KIND_ORDER.map((kind) =>
        counts[kind] === 0 ? null : (
          <span
            key={kind}
            className={KIND_META[kind].bar}
            style={{ width: `${(counts[kind] / (total || 1)) * 100}%` }}
          />
        ),
      )}
    </div>
  );
}

export function ChangelogPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const latest = ENTRIES[0];

  const visible = useMemo(
    () =>
      ENTRIES.map((entry) => ({
        entry,
        changes: entry.changes.filter((c) => filter === "all" || classifyChange(c) === filter),
      })).filter((r) => r.changes.length > 0),
    [filter],
  );

  const filters: { id: Filter; label: string; count: number }[] = [
    { id: "all", label: "All", count: TOTAL_CHANGES },
    ...KIND_ORDER.map((kind) => ({
      id: kind as Filter,
      label: KIND_META[kind].plural,
      count: TOTAL_COUNTS[kind],
    })),
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-10 pb-16 lg:space-y-12">
      <section
        aria-labelledby="changelog-heading"
        className="relative isolate overflow-hidden rounded-2xl border border-white/[0.08] bg-[#080808]"
      >
        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="opacity-35" />
        <div aria-hidden className="hero-grid pointer-events-none absolute inset-0 opacity-25" />
        <div
          className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-60"
          aria-hidden
        />

        <div className="relative px-5 pb-7 pt-8 sm:px-8 sm:pb-8 sm:pt-9 lg:px-10">
          <div className="flex flex-col gap-8 lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0 max-w-xl">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <Badge tone="blue">
                  <span className="mr-1 size-1.5 rounded-full bg-blue-400" aria-hidden />
                  Current v{latest.version}
                </Badge>
                <span className="font-mono text-[11px] tracking-[0.04em] text-[var(--admin-text-dim)]">
                  {latest.date}
                </span>
              </div>
              <h1
                id="changelog-heading"
                className="mt-5 text-[clamp(2.1rem,5vw,3.35rem)] font-semibold leading-[1.06] tracking-[-0.04em] text-[var(--admin-text)]"
              >
                Release ledger.{" "}
                <span className="text-[var(--admin-text-muted)]">Every change, by version.</span>
              </h1>
              <p className="mt-5 max-w-[49ch] text-[14.5px] leading-[1.75] text-[var(--admin-text-muted)] sm:text-[15px]">
                What shipped, grouped by release. The gateway&apos;s behavior is the source of
                truth; this page is the human-readable record.
              </p>
            </div>

            <dl className="grid w-full grid-cols-3 gap-px overflow-hidden rounded-xl border border-white/[0.07] bg-white/[0.07] sm:max-w-sm lg:w-[320px]">
              {[
                { label: "Releases", value: String(ENTRIES.length) },
                { label: "Changes", value: String(TOTAL_CHANGES) },
                { label: "Fixes", value: String(TOTAL_COUNTS.fix) },
              ].map((item) => (
                <div key={item.label} className="bg-black/40 px-4 py-3">
                  <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--admin-text-dim)]">
                    {item.label}
                  </dt>
                  <dd className="mt-1 font-mono text-xl font-medium tracking-[-0.03em] text-[var(--admin-text)]">
                    {item.value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          <nav
            aria-label="Jump to release"
            className="mt-8 flex flex-wrap gap-2 border-t border-white/[0.06] pt-5"
          >
            {ENTRIES.map((entry, i) => (
              <a
                key={entry.version}
                href={`#v${entry.version}`}
                className="inline-flex min-h-11 items-center rounded-lg border border-white/[0.08] bg-white/[0.02] px-3.5 font-mono text-[12px] text-[var(--admin-text-muted)] outline-none transition-colors hover:border-white/[0.16] hover:text-[var(--admin-text)] focus-visible:ring-2 focus-visible:ring-blue-500/60"
                aria-current={i === 0 ? "true" : undefined}
              >
                v{entry.version}
              </a>
            ))}
          </nav>
        </div>
      </section>

      <section aria-label="Release timeline">
        <div
          role="group"
          aria-label="Filter changes by type"
          className="mb-6 flex flex-wrap items-center gap-2"
        >
          {filters.map((f) => {
            const active = filter === f.id;
            return (
              <button
                key={f.id}
                type="button"
                aria-pressed={active}
                onClick={() => setFilter(f.id)}
                className={[
                  "inline-flex min-h-11 items-center gap-2 rounded-lg border px-3.5 text-[13px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500/60",
                  active
                    ? "border-blue-400/30 bg-blue-500/[0.1] text-[var(--admin-text)]"
                    : "border-white/[0.08] bg-white/[0.02] text-[var(--admin-text-muted)] hover:border-white/[0.16] hover:text-[var(--admin-text)]",
                ].join(" ")}
              >
                {f.label}
                <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">{f.count}</span>
              </button>
            );
          })}
        </div>

        {visible.length === 0 ? (
          <Card className="p-8 text-center text-[13px] text-[var(--admin-text-muted)]">
            No changes match this filter.
          </Card>
        ) : (
          <ol className="relative space-y-5">
            <span
              aria-hidden
              className="absolute bottom-2 left-[7px] top-2 hidden w-px bg-gradient-to-b from-white/[0.14] via-white/[0.07] to-transparent lg:block"
            />
            {visible.map(({ entry, changes }) => {
              const isLatest = entry.version === latest.version;
              const counts = countKinds(entry.changes);
              return (
                <li key={entry.version} className="relative lg:pl-10">
                  <span
                    aria-hidden
                    className={[
                      "absolute left-0 top-7 hidden size-[15px] rounded-full border-2 lg:block",
                      isLatest
                        ? "border-blue-400 bg-blue-400/30"
                        : "border-white/[0.2] bg-[var(--admin-surface)]",
                    ].join(" ")}
                  />
                  <Card
                    className={[
                      "overflow-hidden p-0",
                      isLatest ? "border-blue-400/[0.18]" : "border-white/[0.06]",
                    ].join(" ")}
                  >
                    <header
                      className={[
                        "flex flex-col gap-4 border-b border-white/[0.05] px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6",
                        isLatest ? "bg-blue-500/[0.04]" : "bg-white/[0.01]",
                      ].join(" ")}
                    >
                      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
                        <h2 className="font-mono text-2xl font-semibold tracking-[-0.03em] text-[var(--admin-text)]">
                          <a
                            href={`#v${entry.version}`}
                            id={`v${entry.version}`}
                            className="scroll-mt-24 rounded-sm outline-none transition-colors hover:text-blue-300 focus-visible:ring-2 focus-visible:ring-blue-500/60"
                          >
                            v{entry.version}
                          </a>
                        </h2>
                        {isLatest && <Badge tone="blue">current</Badge>}
                        <span className="font-mono text-[11px] tracking-[0.04em] text-[var(--admin-text-dim)]">
                          {entry.date}
                        </span>
                      </div>
                      <div className="flex flex-col gap-2 sm:items-end">
                        <ScopeBar counts={counts} total={entry.changes.length} />
                        <p className="font-mono text-[10.5px] text-[var(--admin-text-dim)]">
                          {filter === "all"
                            ? `${entry.changes.length} changes`
                            : `${changes.length} of ${entry.changes.length} changes`}
                        </p>
                      </div>
                    </header>

                    <ul
                      className="divide-y divide-white/[0.05] px-5 sm:px-6"
                      aria-label={`Changes in v${entry.version}`}
                    >
                      {changes.map((change) => {
                        const meta = KIND_META[classifyChange(change)];
                        const Icon = meta.icon;
                        return (
                          <li key={change} className="flex items-start gap-3.5 py-3.5 sm:gap-4">
                            <span
                              className={`mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-md border ${meta.chip} ${meta.text}`}
                            >
                              <Icon size={13} aria-hidden />
                              <span className="sr-only">{meta.label}: </span>
                            </span>
                            <span className="min-w-0 break-words text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
                              {changeText(change)}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </Card>
                </li>
              );
            })}
          </ol>
        )}
      </section>
    </div>
  );
}
