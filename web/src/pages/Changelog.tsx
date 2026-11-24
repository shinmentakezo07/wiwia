// Changelog — release ledger. Entries are newest-first; the first entry is the
// current release. Presentation only — the entry data below is the source of truth.
//
// Visual language: flat ledger, same as the rest of the public front — mono
// micro-labels, hairline rules, a sticky version index on desktop, and small
// type dots on change rows. No boxed cards, no beams, no glow.

import { useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { AlertCircle, Check, Wrench } from "lucide-react";
import { Badge } from "@/components/ui";

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

// Class strings are literal so Tailwind's scanner keeps them.
const KIND_META: Record<
  ChangeKind,
  { label: string; plural: string; icon: LucideIcon; dot: string; count: string }
> = {
  feature: {
    label: "Feature",
    plural: "Features",
    icon: Check,
    dot: "bg-emerald-400",
    count: "text-emerald-300/80",
  },
  fix: {
    label: "Fix",
    plural: "Fixes",
    icon: AlertCircle,
    dot: "bg-amber-400",
    count: "text-amber-300/80",
  },
  improvement: {
    label: "Improvement",
    plural: "Improvements",
    icon: Wrench,
    dot: "bg-sky-400",
    count: "text-sky-300/80",
  },
};

function classifyChange(change: string): ChangeKind {
  if (/^fix\b/i.test(change)) return "fix";
  if (/^add\b/i.test(change)) return "feature";
  return "improvement";
}

/** Drops the leading "Add"/"Fix" verb: the kind dot already carries it. */
function changeText(change: string): string {
  const stripped = change.replace(/^(add|fix)\s+/i, "");
  return stripped.charAt(0).toUpperCase() + stripped.slice(1);
}

function entryCounts(changes: string[]): Record<ChangeKind, number> {
  const counts: Record<ChangeKind, number> = { feature: 0, fix: 0, improvement: 0 };
  for (const change of changes) counts[classifyChange(change)] += 1;
  return counts;
}

const TOTAL_CHANGES = ENTRIES.reduce((n, e) => n + e.changes.length, 0);
const TOTAL_COUNTS = entryCounts(ENTRIES.flatMap((e) => e.changes));

function countLabel(counts: Record<ChangeKind, number>): string {
  return KIND_ORDER.filter((kind) => counts[kind] > 0)
    .map((kind) => `${counts[kind]} ${KIND_META[kind].plural.toLowerCase()}`)
    .join(" · ");
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
    <div className="mx-auto max-w-5xl pb-16">
      {/* Header — quiet typographic open, no boxed hero */}
      <header className="pb-8 pt-4 sm:pt-8">
        <p className="font-mono text-[10.5px] uppercase tracking-[0.16em] text-[var(--admin-text-dim)]">
          <span className="text-blue-400/80">release</span>
          <span aria-hidden> · </span>
          ledger
        </p>
        <h1 className="mt-3 max-w-xl text-[clamp(2.1rem,5vw,3.35rem)] font-semibold leading-[1.06] tracking-[-0.04em] text-[var(--admin-text)]">
          Changelog
        </h1>
        <p className="mt-5 max-w-[52ch] text-[14.5px] leading-[1.75] text-[var(--admin-text-muted)] sm:text-[15px]">
          What shipped, grouped by release. The gateway&rsquo;s behavior is the source of truth;
          this page is the human-readable record.
        </p>
        <p className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-[var(--admin-border)] pt-4 font-mono text-[11px] tracking-[0.04em] text-[var(--admin-text-dim)]">
          <span>
            <span className="text-[var(--admin-text)]">{ENTRIES.length}</span> releases
          </span>
          <span aria-hidden>·</span>
          <span>
            <span className="text-[var(--admin-text)]">{TOTAL_CHANGES}</span> changes
          </span>
          <span aria-hidden>·</span>
          <span>
            latest{" "}
            <a
              href={`#v${latest.version}`}
              className="text-blue-300 outline-none transition-colors hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500/60"
            >
              v{latest.version}
            </a>{" "}
            {latest.date}
          </span>
        </p>
      </header>

      <div className="lg:grid lg:grid-cols-[176px_minmax(0,1fr)] lg:gap-10">
        {/* Version index + filters — sticky sidebar on desktop, inline rows on mobile */}
        <aside className="mb-6 lg:mb-0">
          <div
            role="group"
            aria-label="Filter changes by type"
            className="flex flex-wrap gap-1 lg:sticky lg:top-6 lg:flex-col lg:gap-0.5"
          >
            <p className="mb-2 hidden font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--admin-text-dim)] lg:block">
              Filter
            </p>
            {filters.map((f) => {
              const active = filter === f.id;
              return (
                <button
                  key={f.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setFilter(f.id)}
                  className={[
                    "inline-flex min-h-11 items-center gap-2 rounded-lg px-3 text-[13px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500/60",
                    active
                      ? "bg-white/[0.05] font-medium text-[var(--admin-text)]"
                      : "text-[var(--admin-text-muted)] hover:bg-white/[0.02] hover:text-[var(--admin-text)]",
                    "lg:rounded-md",
                  ].join(" ")}
                >
                  {f.label}
                  <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
                    {f.count}
                  </span>
                </button>
              );
            })}

            <p className="mb-2 mt-6 hidden font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--admin-text-dim)] lg:block">
              Releases
            </p>
            <nav
              aria-label="Jump to release"
              className="hidden flex-col gap-0.5 lg:flex"
            >
              {ENTRIES.map((entry, i) => (
                <a
                  key={entry.version}
                  href={`#v${entry.version}`}
                  aria-current={i === 0 ? "true" : undefined}
                  className="inline-flex min-h-11 items-center gap-2 rounded-md px-3 font-mono text-[12px] text-[var(--admin-text-muted)] outline-none transition-colors hover:bg-white/[0.02] hover:text-[var(--admin-text)] focus-visible:ring-2 focus-visible:ring-blue-500/60"
                >
                  <span
                    aria-hidden
                    className={`size-1.5 rounded-full ${i === 0 ? "bg-blue-400" : "bg-white/25"}`}
                  />
                  v{entry.version}
                  <span className="text-[10px] text-[var(--admin-text-dim)]">
                    {entry.date.split(" ")[1]}
                  </span>
                </a>
              ))}
            </nav>
          </div>
        </aside>

        {/* Release ledger */}
        <section aria-label="Release timeline">
          {visible.length === 0 ? (
            <p className="rounded-xl border border-dashed border-white/[0.08] p-8 text-center text-[13px] text-[var(--admin-text-muted)]">
              No changes match this filter.
            </p>
          ) : (
            <ol>
              {visible.map(({ entry, changes }, i) => {
                const isLatest = entry.version === latest.version;
                const counts = entryCounts(entry.changes);
                return (
                  <li
                    key={entry.version}
                    id={`v${entry.version}`}
                    className={`scroll-mt-24 ${i > 0 ? "mt-10 border-t border-[var(--admin-border)] pt-8" : ""}`}
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        <h2 className="font-mono text-[22px] font-semibold tracking-[-0.03em] text-[var(--admin-text)]">
                          v{entry.version}
                        </h2>
                        {isLatest && <Badge tone="blue">current</Badge>}
                        <span className="font-mono text-[11px] tracking-[0.04em] text-[var(--admin-text-dim)]">
                          {entry.date}
                        </span>
                      </div>
                      <p className="font-mono text-[10.5px] tracking-[0.04em] text-[var(--admin-text-dim)]">
                        {filter === "all" ? countLabel(counts) : `${changes.length} matching`}
                      </p>
                    </div>

                    <ul
                      aria-label={`Changes in v${entry.version}`}
                      className="mt-4 divide-y divide-[var(--admin-border)] border-t border-[var(--admin-border)]"
                    >
                      {changes.map((change) => {
                        const meta = KIND_META[classifyChange(change)];
                        return (
                          <li
                            key={change}
                            className="flex items-start gap-3 py-4 sm:gap-4"
                          >
                            <span
                              className={`mt-[7px] size-[7px] shrink-0 rounded-full ${meta.dot}`}
                              aria-hidden
                            />
                            <span className="min-w-0 break-words text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
                              <span className="sr-only">{meta.label}: </span>
                              {changeText(change)}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}
