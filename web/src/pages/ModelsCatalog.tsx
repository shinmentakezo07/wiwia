// ModelsCatalog — public, secret-free model catalog from /public/models.
// No auth required.
//
// The default reading is an index, not a gallery: eighty-one groups do not fit
// in cards, and a card per group buries the only thing a reader comes here for
// — the group name and the provider account behind it. So each group is one
// ruled row of a single ledger, and its routes read left-to-right on that row.
// A second, card-shaped reading of the same rows is one toggle away, for when
// a single group is what you are looking at rather than the whole list.
//
// The masthead keeps the Landing/Pricing vocabulary (beam backdrop, live
// chip); everything below it is the hairline grid the console's Models ledger
// already uses, minus the operator's weights, health and split.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, ArrowUpDown, Boxes, Check, ChevronDown, Copy, LayoutGrid, Rows3, Search, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getPublicModels } from "@/api/client";
import { aliasDisplayName, aliasTarget, type PublicModelGroup } from "@/api/types";
import { providerLogo } from "@/lib/provider-logos";
import { Alert, EmptyState } from "@/components/ui";
import { HERO_BEAMS_COMPACT, HeroBeamBackdrop } from "@/components/HeroBeamBackdrop";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50";

/** Deterministic accent hue per provider account. Hue is the only colour on
 *  the ledger, so a provider keeps the same one everywhere it appears. */
const PROVIDER_HUES: Record<string, number> = {
  openai: 152,
  anthropic: 22,
  google: 217,
  openrouter: 76,
  opencode: 262,
  workbuddy: 190,
};

function providerHue(provider: string): number {
  let h = 0;
  for (let i = 0; i < provider.length; i++) h = (h * 31 + provider.charCodeAt(i)) % 360;
  return PROVIDER_HUES[provider] ?? h;
}

function hueVar(provider: string): CSSProperties {
  return { ["--p-hue" as string]: String(providerHue(provider)) } as CSSProperties;
}

// -- derived bits -------------------------------------------------------------

interface AliasChip {
  alias: string;
  dn: string | null;
}

/** alias target group → the alias keys pointing at it (string-keyed lookup). */
function aliasChipsByGroup(aliases: Record<string, string>): Record<string, AliasChip[]> {
  const byGroup: Record<string, AliasChip[]> = {};
  for (const [alias, v] of Object.entries(aliases)) {
    const target = aliasTarget(v);
    (byGroup[target] ??= []).push({ alias, dn: aliasDisplayName(v) });
  }
  return byGroup;
}

type SortKey = "name" | "routes";

/** Two readings of the same catalog: the ledger for scanning, the cards for
 *  looking at one group on its own. */
type View = "list" | "grid";

const VIEW_KEY = "wiwi.models.view";

function initialView(): View {
  try {
    const saved = localStorage.getItem(VIEW_KEY);
    if (saved === "list" || saved === "grid") return saved;
  } catch {
    // storage unavailable (private mode / blocked cookies) — fall through
  }
  return "list";
}

// -- small parts --------------------------------------------------------------

function CopyIdButton(props: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <button
      type="button"
      aria-label={copied ? "Copied" : props.label}
      title={copied ? "Copied" : props.label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(props.text);
          setCopied(true);
          clearTimeout(timer.current);
          timer.current = setTimeout(() => setCopied(false), 1400);
        } catch {
          // clipboard unavailable (permissions / insecure context); nothing to recover
        }
      }}
      // 44px hit area; the icon inside stays small so the row keeps its rhythm.
      className={`mc-copy flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.05] hover:text-[var(--admin-text)] ${FOCUS_RING}`}
    >
      {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
    </button>
  );
}

function Route(props: { provider: string; type?: string; modelId: string }) {
  return (
    <span className="mc-route" style={hueVar(props.provider)}>
      <span className="mc-dot" aria-hidden />
      <span className="mc-prov">{props.provider}</span>
      <span
        className="mc-mid"
        title={
          props.type
            ? `${props.provider} (${props.type}) / ${props.modelId}`
            : `${props.provider}/${props.modelId}`
        }
      >
        {props.modelId}
      </span>
    </span>
  );
}

/** The provider's own badge when the type has one, else a letter tile in the
 *  account's hue — so a card is identifiable before its routes are read. */
function ProviderMark(props: { name: string; type?: string }) {
  const src = providerLogo(props.type);
  return (
    <span
      className="mc-mark"
      style={hueVar(props.name)}
      title={props.type ? `${props.name} · ${props.type}` : props.name}
    >
      {src ? (
        // Not lazy: there are only twelve distinct badges, so the browser
        // fetches each once — and a badge that pops in after the card has
        // painted reads as a broken tile.
        <img src={src} alt="" className="mc-mark-img" width={24} height={24} />
      ) : (
        <span className="mc-mark-letter" aria-hidden>
          {props.name.slice(0, 1).toUpperCase()}
        </span>
      )}
    </span>
  );
}

function CatalogRow(props: { g: PublicModelGroup; aliases: AliasChip[] }) {
  const g = props.g;
  return (
    <li className="mc-row" style={hueVar(g.deployments[0]?.provider ?? "")}>
      <div className="mc-idcell">
        <span className="mc-name" title={g.name}>
          {g.name}
        </span>
        {props.aliases.map((a) => (
          <span
            key={a.alias}
            className="mc-alias"
            title={a.dn ? `${a.dn} (alias → ${g.name})` : `alias → ${g.name}`}
          >
            {a.alias}
          </span>
        ))}
        <CopyIdButton text={g.name} label={`Copy model id ${g.name}`} />
      </div>
      <div className="mc-routes">
        {g.deployments.map((d) => (
          <Route
            key={`${d.provider}/${d.model_id}`}
            provider={d.provider}
            type={d.type}
            modelId={d.model_id}
          />
        ))}
      </div>
    </li>
  );
}

function CatalogCard(props: { g: PublicModelGroup; aliases: AliasChip[] }) {
  const g = props.g;
  // One badge per account, not per route: two routes on the same account are
  // the same identity.
  const accounts = new Map<string, string | undefined>();
  for (const d of g.deployments) if (!accounts.has(d.provider)) accounts.set(d.provider, d.type);
  const shown = [...accounts.entries()].slice(0, 4);
  const hidden = accounts.size - shown.length;
  return (
    <li className="mc-card" style={hueVar(g.deployments[0]?.provider ?? "")}>
      <div className="mc-card-head">
        <span className="mc-marks">
          {shown.map(([name, type]) => (
            <ProviderMark key={name} name={name} type={type} />
          ))}
          {hidden > 0 && <span className="mc-mark-more">+{hidden}</span>}
        </span>
        <CopyIdButton text={g.name} label={`Copy model id ${g.name}`} />
      </div>
      <span className="mc-card-name" title={g.name}>
        {g.name}
      </span>
      {props.aliases.length > 0 && (
        <div className="mc-card-aliases">
          {props.aliases.map((a) => (
            <span
              key={a.alias}
              className="mc-alias"
              title={a.dn ? `${a.dn} (alias → ${g.name})` : `alias → ${g.name}`}
            >
              {a.alias}
            </span>
          ))}
        </div>
      )}
      <ul className="mc-card-routes">
        {g.deployments.map((d) => (
          <li
            key={`${d.provider}/${d.model_id}`}
            className="mc-card-route"
            style={hueVar(d.provider)}
          >
            <span className="mc-dot" aria-hidden />
            <span className="mc-prov">{d.provider}</span>
            <span
              className="mc-mid"
              title={
                d.type ? `${d.provider} (${d.type}) / ${d.model_id}` : `${d.provider}/${d.model_id}`
              }
            >
              {d.model_id}
            </span>
          </li>
        ))}
      </ul>
    </li>
  );
}

function ViewToggle(props: { view: View; onChange: (view: View) => void }) {
  return (
    <div className="mc-view" role="group" aria-label="Catalog layout">
      <button
        type="button"
        aria-pressed={props.view === "list"}
        onClick={() => props.onChange("list")}
        title="Row layout"
        aria-label="Row layout"
        className="mc-view-btn"
      >
        <Rows3 size={15} aria-hidden />
      </button>
      <button
        type="button"
        aria-pressed={props.view === "grid"}
        onClick={() => props.onChange("grid")}
        title="Card layout"
        aria-label="Card layout"
        className="mc-view-btn"
      >
        <LayoutGrid size={15} aria-hidden />
      </button>
    </div>
  );
}

/** Same shape as a loaded row, so the page does not jump when data lands. */
function LedgerSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading the model catalog">
      <div className="mc-ledger" aria-hidden>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="mc-row">
            <div className="mc-idcell">
              <span
                className="admin-skeleton block h-3.5 rounded"
                style={{ width: `${9 + ((i * 5) % 17)}rem` }}
              />
            </div>
            <div className="mc-routes">
              <span className="admin-skeleton block h-3 w-40 rounded" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function GridSkeleton() {
  return (
    <ul className="mc-grid m-0 list-none p-0" aria-busy="true" aria-label="Loading the model catalog">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <li key={i} className="mc-card" aria-hidden>
          <span
            className="admin-skeleton block h-4 rounded"
            style={{ width: `${8 + ((i * 4) % 12)}rem` }}
          />
          <span className="admin-skeleton block h-3 w-28 rounded" />
        </li>
      ))}
    </ul>
  );
}

function Figure(props: { label: string; value: number }) {
  return (
    <div className="mc-figure">
      <dt className="mc-figure-label">{props.label}</dt>
      <dd className="mc-figure-num">{props.value}</dd>
    </div>
  );
}

// -- page ---------------------------------------------------------------------

export function ModelsCatalogPage() {
  const query = useQuery({ queryKey: ["public-models"], queryFn: getPublicModels });
  const [q, setQ] = useState("");
  const [provider, setProvider] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("name");
  const [view, setView] = useState<View>(initialView);
  const [filterOpen, setFilterOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const filterRef = useRef<HTMLDivElement>(null);
  const filterBtnRef = useRef<HTMLButtonElement>(null);

  // Closing the panel returns focus to the control that opened it — the
  // focused chip is about to leave the document.
  const closeFilter = useCallback(() => {
    setFilterOpen(false);
    filterBtnRef.current?.focus();
  }, []);

  const changeView = useCallback((next: View) => {
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // storage unavailable — the choice simply does not survive a reload
    }
  }, []);

  // "/" jumps to the search field, the way every index page a developer reads
  // already behaves. Ignored while typing somewhere else.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable) return;
      e.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // A panel that opens over the page closes the two ways every panel does.
  useEffect(() => {
    if (!filterOpen) return;
    const onDown = (e: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) setFilterOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeFilter();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [filterOpen, closeFilter]);

  const groups = useMemo(() => query.data?.groups ?? [], [query.data]);
  const aliases = useMemo(() => query.data?.aliases ?? {}, [query.data]);
  const aliasChips = useMemo(() => aliasChipsByGroup(aliases), [aliases]);

  // Providers by how many groups they carry: the account that serves eighteen
  // groups is the one a reader is most likely to be filtering for. A group is
  // counted once per account even when two of its routes share that account,
  // so the chip's number always equals the number of rows the chip yields.
  const providerCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const g of groups) {
      for (const p of new Set(g.deployments.map((d) => d.provider))) {
        counts.set(p, (counts.get(p) ?? 0) + 1);
      }
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [groups]);

  const filtered = useMemo(() => {
    const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
    const rows = groups.filter((g) => {
      if (provider && !g.deployments.some((d) => d.provider === provider)) return false;
      if (tokens.length === 0) return true;
      const hay = [
        g.name,
        ...(aliasChips[g.name] ?? []).map((a) => a.alias),
        ...g.deployments.map((d) => `${d.provider} ${d.model_id}`),
      ]
        .join(" ")
        .toLowerCase();
      return tokens.every((t) => hay.includes(t));
    });
    rows.sort(
      sort === "routes"
        ? (a, b) => b.deployments.length - a.deployments.length || a.name.localeCompare(b.name)
        : (a, b) => a.name.localeCompare(b.name),
    );
    return rows;
  }, [groups, provider, q, sort, aliasChips]);

  const deployments = useMemo(
    () => groups.reduce((n, g) => n + g.deployments.length, 0),
    [groups],
  );
  const aliasCount = Object.keys(aliases).length;
  const filtering = q.trim() !== "" || provider !== null;
  const loaded = !query.isLoading && !query.error && groups.length > 0;

  let body: ReactNode;
  if (query.isLoading) {
    body = view === "grid" ? <GridSkeleton /> : <LedgerSkeleton />;
  } else if (query.error) {
    body = (
      <Alert
        tone="danger"
        title="The catalog could not be loaded"
        action={
          <button
            type="button"
            onClick={() => void query.refetch()}
            className="admin-btn admin-btn-ghost min-h-11"
          >
            Retry
          </button>
        }
      >
        {query.error.message}
      </Alert>
    );
  } else if (groups.length === 0) {
    body = (
      <div className="admin-empty-state rounded-[var(--admin-radius)] border px-4">
        <EmptyState icon={Boxes} title="No model groups on this gateway yet">
          Add <span className="font-mono">model_list</span> entries in{" "}
          <span className="font-mono">wiwi.yaml</span> and they appear here on the next load.
        </EmptyState>
      </div>
    );
  } else if (filtered.length === 0) {
    body = (
      <div className="admin-empty-state rounded-[var(--admin-radius)] border px-4">
        <EmptyState
          icon={Search}
          title="No group matches"
          action={
            <button
              type="button"
              onClick={() => {
                setQ("");
                setProvider(null);
              }}
              className="admin-btn admin-btn-ghost min-h-11"
            >
              Clear search and filters
            </button>
          }
        >
          {q !== "" ? (
            <>
              Nothing matches “<span className="font-mono">{q}</span>”
              {provider && (
                <>
                  {" "}
                  on <span className="font-mono">{provider}</span>
                </>
              )}
              .
            </>
          ) : (
            <>
              No group routes through <span className="font-mono">{provider}</span>.
            </>
          )}
        </EmptyState>
      </div>
    );
  } else if (view === "grid") {
    body = (
      <ul className="mc-grid m-0 list-none p-0">
        {filtered.map((g) => (
          <CatalogCard key={g.name} g={g} aliases={aliasChips[g.name] ?? []} />
        ))}
      </ul>
    );
  } else {
    body = (
      <div className="mc-ledger">
        <div className="mc-ledger-head" aria-hidden>
          <span>Model group</span>
          <span>Routed through</span>
        </div>
        <ul className="m-0 list-none p-0">
          {filtered.map((g) => (
            <CatalogRow key={g.name} g={g} aliases={aliasChips[g.name] ?? []} />
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="pb-24">
      {/* ════════ masthead ════════ */}
      <header className="mc-masthead full-bleed">
        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="opacity-50" />
        <div className="hero-grid pointer-events-none absolute inset-0 opacity-50" aria-hidden />
        <div className="relative mx-auto max-w-5xl px-6 pb-12 pt-14 sm:pb-14 sm:pt-16">
          <div className="animate-hero-enter lg:grid lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end lg:gap-12">
            <div className="max-w-xl">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <span className="docs-chip-live">
                  <span className="docs-chip-live-dot" aria-hidden />
                  Live from the router
                </span>
                <span className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--admin-text-dim)]">
                  /public/models
                </span>
              </div>

              <h1 className="mc-title mt-5">
                Model <span className="hero-gradient-text">catalog</span>
              </h1>

              <p className="mt-4 text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
                Every model group this gateway routes, the provider account behind each route, and
                the id to send.
              </p>

              <div className="mt-7 flex flex-wrap items-center gap-2.5">
                <Link
                  to="/playground"
                  className={`wiwi-shimmer inline-flex h-11 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-medium text-white shadow-lg shadow-brand-600/25 transition-[filter,transform] duration-150 hover:scale-[1.02] hover:brightness-110 active:scale-[0.98] ${FOCUS_RING}`}
                >
                  Open Playground
                  <ArrowRight size={14} />
                </Link>
                <Link
                  to="/docs"
                  className={`inline-flex h-11 items-center justify-center rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors duration-150 hover:border-white/[0.14] hover:bg-white/[0.04] ${FOCUS_RING}`}
                >
                  API reference
                </Link>
              </div>
            </div>

            {loaded && (
              <dl className="mc-figures mt-9 lg:mt-0">
                <Figure label="Model groups" value={groups.length} />
                <Figure label="Routes" value={deployments} />
                <Figure label="Providers" value={providerCounts.length} />
                <Figure label="Aliases" value={aliasCount} />
              </dl>
            )}
          </div>
        </div>
      </header>

      {/* ════════ toolbar + ledger ════════ */}
      <div className="mx-auto max-w-5xl px-6">
        {loaded && (
          <div className="mc-toolbar">
            <div className="relative w-full min-w-0 sm:w-auto sm:flex-1">
              <Search
                size={15}
                className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
                aria-hidden
              />
              <input
                ref={searchRef}
                type="search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Escape") return;
                  if (q !== "") setQ("");
                  else e.currentTarget.blur();
                }}
                placeholder="Search groups, providers or ids"
                aria-label="Search the model catalog"
                className="admin-input h-11 pl-10 pr-14 text-[13px]"
              />
              {q === "" ? (
                <kbd
                  className="admin-kbd pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 sm:block"
                  aria-hidden
                >
                  /
                </kbd>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setQ("");
                    searchRef.current?.focus();
                  }}
                  aria-label="Clear search"
                  className={`absolute right-0 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)] ${FOCUS_RING}`}
                >
                  <X size={14} />
                </button>
              )}
            </div>

            <div ref={filterRef} className="contents">
              <button
                type="button"
                ref={filterBtnRef}
                aria-expanded={filterOpen}
                aria-controls="mc-provider-panel"
                onClick={() => setFilterOpen((open) => !open)}
                className="mc-filter-btn"
              >
                {provider ? (
                  <span className="mc-dot" style={hueVar(provider)} aria-hidden />
                ) : (
                  <span
                    className="size-1.5 shrink-0 rounded-full bg-[var(--admin-text-dim)]"
                    aria-hidden
                  />
                )}
                <span className="mc-filter-name">{provider ?? "All providers"}</span>
                <span className="font-mono text-[10px] tabular-nums text-[var(--admin-text-dim)]">
                  {provider
                    ? (providerCounts.find(([p]) => p === provider)?.[1] ?? 0)
                    : groups.length}
                </span>
                <ChevronDown
                  size={13}
                  className={`shrink-0 transition-transform duration-150 ${
                    filterOpen ? "rotate-180" : ""
                  }`}
                  aria-hidden
                />
              </button>
            </div>

            {/* One control, naming the order it is in. With two orders, a
                segmented pair is a widget where a word will do. */}
            <button
              type="button"
              onClick={() => setSort((s) => (s === "name" ? "routes" : "name"))}
              aria-label={
                sort === "name"
                  ? "Sorted by group name — switch to sorting by route count"
                  : "Sorted by route count — switch to sorting by group name"
              }
              title={
                sort === "name"
                  ? "Sorted by group name — switch to route count"
                  : "Sorted by route count — switch to group name"
              }
              className="mc-sort"
            >
              <ArrowUpDown size={12} aria-hidden />
              {sort === "name" ? "Name" : "Routes"}
            </button>

            <span className="mc-count hidden lg:inline" aria-hidden>
              {filtering ? `${filtered.length} of ${groups.length}` : `${groups.length}`} groups
            </span>

            <div className="hidden lg:flex">
              <ViewToggle view={view} onChange={changeView} />
            </div>

            {/* One announcement for the count at every width, so the two
                visible copies (toolbar and status line) never double up. */}
            <span className="sr-only" aria-live="polite">
              {filtering ? `${filtered.length} of ${groups.length}` : `${groups.length}`} groups
            </span>

            {filterOpen && (
              <div id="mc-provider-panel" className="mc-panel">
                <div className="mc-panel-head">
                  <span className="admin-label">Filter by provider account</span>
                  <button
                    type="button"
                    onClick={() => {
                      setProvider(null);
                      closeFilter();
                    }}
                    className="mc-reset"
                  >
                    Reset
                  </button>
                </div>
                <div className="mc-chips">
                  <button
                    type="button"
                    aria-pressed={provider === null}
                    onClick={() => {
                      setProvider(null);
                      closeFilter();
                    }}
                    className="mc-chip"
                  >
                    <span className="mc-chip-label">All providers</span>
                    <span className="font-mono text-[10px] tabular-nums text-[var(--admin-text-dim)]">
                      {groups.length}
                    </span>
                  </button>
                  {providerCounts.map(([p, n]) => (
                    <button
                      key={p}
                      type="button"
                      aria-pressed={provider === p}
                      onClick={() => {
                        setProvider(provider === p ? null : p);
                        closeFilter();
                      }}
                      className="mc-chip"
                    >
                      <span className="mc-dot" style={hueVar(p)} aria-hidden />
                      <span className="mc-chip-label">{p}</span>
                      <span className="font-mono text-[10px] tabular-nums text-[var(--admin-text-dim)]">
                        {n}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Below lg the toolbar has no room for the count and the layout
            switch, so both ride on their own line under it — which also
            keeps that line out of the sticky area. */}
        {loaded && (
          <div className="mt-3 flex items-center justify-between gap-3 lg:hidden">
            <p
              className="font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]"
              aria-hidden
            >
              {filtering ? `${filtered.length} of ${groups.length}` : `${groups.length}`} groups
            </p>
            <ViewToggle view={view} onChange={changeView} />
          </div>
        )}

        <div className="mt-4 sm:mt-6">{body}</div>

        {loaded && (
          <p className="mt-6 max-w-2xl text-[11.5px] leading-relaxed text-[var(--admin-text-dim)]">
            Ids are matched exactly. Aliases resolve to a group before the router picks the
            provider account, so the client only ever names the group or one of its aliases.
          </p>
        )}
      </div>
    </div>
  );
}
