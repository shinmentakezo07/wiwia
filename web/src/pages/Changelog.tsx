// Changelog — release ledger. Entries are newest-first; the first entry is the
// current release. Presentation only — the entry data below is the source of truth.
//
// Visual language: flat ledger, same as the rest of the public front — mono
// micro-labels, hairline rules, a sticky version index on desktop, and a
// timeline spine with node dots running down the releases. No boxed cards,
// no beams, no glow.

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui";

type ChangeKind = "feature" | "fix" | "improvement";
type Filter = "all" | ChangeKind;

interface ChangelogEntry {
  version: string;
  date: string;
  changes: string[];
}

// Entries are curated from the git history on main — one release per month,
// each change traceable to a commit. Prefix verbs feed classifyChange().
const ENTRIES: ChangelogEntry[] = [
  {
    version: "2026.11",
    date: "November 2026",
    changes: [
      "Add a full Go port of the gateway, interoperable with Python's shared database",
      "Add backend selection in Docker (BACKEND=go|py) with the Go playground served from the session cookie",
      "Match the console's admin API shapes in the Go backend and flush its SSE stream so the console shows LIVE",
      "Add per-key 429 failover to a sibling key without burning retries",
      "Wrap an entitlement refusal to a fallback instead of erroring",
      "Throttle auth attempts per client IP",
      "Model Anthropic MCP servers and context management on the IR",
      "Add performance optimizations across gateway hot paths and reduce console polling intervals",
      "Add provider-level enable/disable and bulk detach selection on providers and models",
      "Record TPS and percentile histograms across rollups, counting thinking and tool-arg deltas",
      "Skip idle background sweeps so a serverless database can suspend",
      "Ship the real wiwi logo — the black-hole cat mark, everywhere",
      "Redesign login, signup, and the sign-in card in a monochrome responsive layout",
      "Redesign the dashboard, docs, and models catalog with monochrome layouts",
      "Redesign the Enterprise showcase, Quickstart band, and token cost calculator",
      "Add guides for 9 more clients and redesign the Integrations, Migration, and Products pages",
      "Fix SSE event ordering and parallel tool-call interleaving",
      "Fix cancelled streams from storing completed Responses turns",
      "Guard typed-wrong tool names and audio formats in the wire codecs",
      "Make IR reasoning accessors total and reject non-finite numeric parameters",
      "Redesign the changelog itself as the flat release ledger and restyle the About page",
    ],
  },
  {
    version: "2026.10",
    date: "October 2026",
    changes: [
      "Persist Responses-API state and accept previous_response_id continuation, served back over GET and DELETE",
      "Add the legacy completions surface at /v1/completions",
      "Add a no-op-by-default OTLP tracing facade with proven OTLP export",
      "Shed, partition, and pin requests by concurrency with priority lanes carried on the virtual key",
      "Relay realtime WebSocket sessions to the upstream with per-session billing and refusal semantics",
      "Add the Hermes (Nous Portal) provider with OAuth, an auto-refresh sweeper, and one Nous account per pool key",
      "Add whole-gateway JSON backup and restore",
      "Add the IFM (MBZUAI) provider and close the round-183 to 187 audit sweep",
      "Add CSV import for the request log and export of the full usage history",
      "Add the Runtime Cloud deployment harness, failing loudly on bad config",
      "Speak the current OpenCode client fingerprint for the free tier and document the request-shape gate",
      "Measure TPS as output generation speed, recorded even for tool-call-only streams",
      "Rebuild the public docs page as a reference manual with a nine-tile Features grid",
      "Rebuild the Models console page as a ruled table and the public catalog as a ledger with a card view",
      "Rebuild the docs provider map by upstream wire family",
      "Redesign the Virtual Keys console, its dialogs, and the connected-accounts console",
      "Restore the official provider logos in the hero and rebrand the pricing tiers as DevPass service plans",
      "Fix the SSE replay cursor so a reconnect repeats nothing and misses nothing",
      "Fix thirteen defects in the delta and chunk flow",
      "Fix six translation losses between the dialects and providers, and report the survivals to callers",
      "Fix the data a mid-stream failover and a failed attempt quietly dropped",
      "Fix an unwritable stream journal dir that refused virtual-key streams",
      "Preserve the upstream 529 so an overload reaches the client as overloaded_error",
      "Fix five wire-codec defects found by source review, including the structured-output schema coercion at every inbound decoder",
      "Fix the backup merge so imported state actually reaches the database",
    ],
  },
  {
    version: "2026.09",
    date: "September 2026",
    changes: [
      "Add the exact-match response cache with cache-hit observability",
      "Add a durable stream journal for restart-safe SSE replay",
      "Add a Redis response-cache backend and Anthropic prompt-cache breakpoints",
      "Add Backoff and CircuitBreaker recovery primitives, shared by the Cline and WorkBuddy circuits",
      "Add the HealthHealer: probe sick keys and deployments, restore them via half-weight probation",
      "Add the OpenCode Zen provider with per-model protocol routing",
      "Add the Combos admin page for composing cross-provider model groups",
      "Add provider export/import backup endpoints and UI",
      "Enforce budget caps on both response paths, answering refusals with 402",
      "Enforce per-deployment rpm/tpm caps as sliding windows",
      "Bound the request log by row count and roll long-range stats up in the DB",
      "Give Anthropic Messages and OpenAI Chat one shared translation spine",
      "Serve all stats windows from the DB so 1h/6h/24h survive restarts",
      "Fetch live Cline CLI and CodeBuddy versions so client fingerprints stay current",
      "Default WorkBuddy reasoning_effort to max and Cline to xhigh when the client is silent",
      "Fix SSE data loss when a provider stream ends without a trailing blank line",
      "Fix cross-request tool state corruption by using a private adapter per stream",
      "Fix eleven streaming contract violations, from tool open/close ordering to synthesized finish frames",
      "Fix Anthropic /v1/messages fidelity for Claude Code: tool search, effort, and server-tool round-trips",
      "Make revocation, rate limits, and config load fail closed",
      "Surface accounting losses instead of reporting success",
      "Fix rate-limiter RPM refunds and the unbounded window sweep",
      "Key retroactive pricing on the full model id",
      "Fix the round-41, 45, 46 and 90 audit sweeps with regression suites for each",
    ],
  },
  {
    version: "2026.08",
    date: "August 2026",
    changes: [
      "Add the gateway core: IR types, wire codecs for OpenAI Chat and Anthropic Messages, provider adapters and registry",
      "Add the model router, streaming SSE, virtual-key auth, cost pricing, and rate limiting",
      "Add the FastAPI server, CLI entrypoint, structured logging, Docker image, and pytest suite",
      "Add the admin stats API and the React console with live SSE dashboards",
      "Add provider edit and delete endpoints, the provider detail page, and the stats bento",
      "Add the NVIDIA NIM and GMI Cloud provider adapters, and B.AI with DeepSeek thinking-mode handling",
      "Add the OpenRouter adapter with cross-provider reasoning-effort translation",
      "Add the Cline provider with automatic OAuth redirect flow, on-demand token refresh, and cross-account weighted routing",
      "Add the WorkBuddy provider with region routing, token refresh, and auths JSON import/export",
      "Add user accounts: pbkdf2 hashing, signed sessions, /auth endpoints, and owner-scoped keys and logs",
      "Add the public front — Landing, Signup, dual-mode Login, ModelsCatalog, Docs — with session auth and role-aware console",
      "Add the Playground as a chat arena with history, auto-minted key, and Markdown rendering",
      "Add Postgres support and DB persistence for admin config, with 7d/30d/all-time Usage stats backed by indexes",
      "Add per-provider model pricing CRUD on /analytics",
      "Add DB-backed long-range stats windows and the request detail drawer with prompt/response display",
      "Fix 51 bugs across gateway, wire codecs, providers, router, and admin UI from the first full audit",
      "Fix cost=0 for all requests by stripping the provider-type prefix in pricing lookups",
      "Fix the streaming ASGI crash on connect-phase upstream errors",
      "Stop returning plaintext provider keys from the admin API",
      "Make loop detection O(1) per token and bound the rate-limit windows and auth cache",
      "Pin key-pool deployments and harden provider prompt-cache plumbing",
    ],
  },
];

const KIND_ORDER: ChangeKind[] = ["feature", "fix", "improvement"];

// Class strings are literal so Tailwind's scanner keeps them.
const KIND_META: Record<
  ChangeKind,
  { label: string; plural: string; dot: string; text: string }
> = {
  feature: {
    label: "Feature",
    plural: "Features",
    dot: "bg-emerald-400",
    text: "text-emerald-300/80",
  },
  fix: {
    label: "Fix",
    plural: "Fixes",
    dot: "bg-amber-400",
    text: "text-amber-300/80",
  },
  improvement: {
    label: "Improvement",
    plural: "Improvements",
    dot: "bg-sky-400",
    text: "text-sky-300/80",
  },
};

function classifyChange(change: string): ChangeKind {
  if (/^fix\b/i.test(change)) return "fix";
  if (/^add\b/i.test(change)) return "feature";
  return "improvement";
}

/** Drops the leading "Add"/"Fix" verb: the kind column already carries it. */
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
                  {f.id !== "all" && (
                    <span
                      aria-hidden
                      className={`size-1.5 shrink-0 rounded-full ${KIND_META[f.id].dot}`}
                    />
                  )}
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
                    {entry.date.split(" ")[0]}
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
                    className={`relative scroll-mt-24 sm:pl-7 ${i > 0 ? "mt-10" : ""}`}
                  >
                    {/* Timeline spine: hairline + node dot per release. The
                        rule stops at the last node so the ledger doesn't trail
                        off past the final release. hidden on mobile where the
                        grid is gone and the dot would float untethered. */}
                    <span
                      aria-hidden
                      className={`absolute bottom-0 left-[5px] top-1 w-px bg-[var(--admin-border)] ${i === visible.length - 1 ? "hidden sm:hidden" : "hidden sm:block"}`}
                    />
                    <span
                      aria-hidden
                      className={`absolute left-0 top-[7px] hidden size-[11px] rounded-full border sm:block ${isLatest ? "border-blue-400 bg-blue-400/20" : "border-white/20 bg-[var(--admin-bg)]"}`}
                    />
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
                      <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 font-mono text-[10.5px] tracking-[0.04em]">
                        {filter === "all" ? (
                          KIND_ORDER.filter((kind) => counts[kind] > 0).map((kind) => (
                            <span key={kind} className={KIND_META[kind].text}>
                              {counts[kind]} {kind}
                            </span>
                          ))
                        ) : (
                          <span className="text-[var(--admin-text-dim)]">
                            {changes.length} matching
                          </span>
                        )}
                      </p>
                    </div>

                    <ul
                      aria-label={`Changes in v${entry.version}`}
                      className="mt-4 divide-y divide-[var(--admin-border)] sm:divide-transparent sm:space-y-3"
                    >
                      {changes.map((change) => {
                        const meta = KIND_META[classifyChange(change)];
                        return (
                          <li
                            key={change}
                            className="rounded-md py-3 transition-colors sm:rounded-lg sm:py-2.5 sm:hover:bg-white/[0.02]"
                          >
                            <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-4">
                              <span
                                className={`font-mono text-[10.5px] uppercase tracking-[0.14em] sm:w-24 sm:shrink-0 sm:text-right ${meta.text}`}
                              >
                                {meta.label}
                              </span>
                              <p className="min-w-0 break-words text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
                                {changeText(change)}
                              </p>
                            </div>
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
