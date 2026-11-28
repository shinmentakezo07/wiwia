// ModelsCatalog — public, secret-free model catalog from /public/models.
// No auth required.
//
// Layout: a compact masthead, then a two-pane index. The left pane owns the
// *question* (search + which provider account), the right pane owns the
// *answer* (the matching groups, as rows or cards). On a phone the panes
// stack and the provider list becomes a horizontally scrolling rail, so the
// results start within the first screen instead of under 37 stacked chips.
//
// Colour is spent on one register only — provider identity (`--p-hue`).
// "Serving now" expires into "Not refreshing" after three missed polls and
// reads "Checking" while the first request is in flight or failed.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, ArrowUpDown, Boxes, Braces, Check, Copy, LayoutGrid, Rows3, Search, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getPublicModels } from "@/api/client";
import { aliasDisplayName, aliasTarget, type ModelAliasEntry, type PublicModelGroup } from "@/api/types";
import { providerLogo } from "@/lib/provider-logos";
import { Alert, EmptyState } from "@/components/ui";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50";

const PROVIDER_HUES: Record<string, number> = {
  openai: 152,
  anthropic: 22,
  google: 217,
  openrouter: 76,
  opencode: 262,
};

function providerHue(provider: string): number {
  let h = 0;
  for (let i = 0; i < provider.length; i++) h = (h * 31 + provider.charCodeAt(i)) % 360;
  return PROVIDER_HUES[provider] ?? h;
}

function hueVar(provider: string): CSSProperties {
  return { ["--p-hue" as string]: String(providerHue(provider)) } as CSSProperties;
}

/** Staggered entrance, capped so a long list never waits on its tail. */
function enterStyle(provider: string, i: number): CSSProperties {
  return { ...hueVar(provider), ["--i" as string]: String(Math.min(i, 24)) } as CSSProperties;
}

interface AliasChip {
  alias: string;
  dn: string | null;
}

function aliasChipsByGroup(aliases: Record<string, string | ModelAliasEntry>): Record<string, AliasChip[]> {
  const byGroup: Record<string, AliasChip[]> = {};
  for (const [alias, v] of Object.entries(aliases)) {
    (byGroup[aliasTarget(v)] ??= []).push({ alias, dn: aliasDisplayName(v) });
  }
  return byGroup;
}

type SortKey = "name" | "routes";
type View = "list" | "grid";
const VIEW_KEY = "wiwi.models.view";

function initialView(): View {
  try {
    const saved = localStorage.getItem(VIEW_KEY);
    if (saved === "list" || saved === "grid") return saved;
  } catch {
    // storage unavailable — fall through
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
          timer.current = window.setTimeout(() => setCopied(false), 1400);
        } catch {
          // clipboard unavailable; nothing to recover
        }
      }}
      className={`mc-copy ${FOCUS_RING}`}
    >
      {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
    </button>
  );
}

/** Paints the query's hits inside a matched string. */
function Hi(props: { text: string; tokens: string[] }) {
  if (props.tokens.length === 0) return <>{props.text}</>;
  const re = new RegExp(
    `(${props.tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`,
    "ig",
  );
  const parts = props.text.split(re);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="mc-hit">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function ProviderMark(props: { name: string; type?: string }) {
  const src = providerLogo(props.type);
  return (
    <span className="mc-mark" style={hueVar(props.name)} aria-hidden>
      {src ? (
        <img src={src} alt="" className="mc-mark-img" width={18} height={18} />
      ) : (
        <span className="mc-mark-letter">{props.name.slice(0, 1).toUpperCase()}</span>
      )}
    </span>
  );
}

function Aliases(props: { aliases: AliasChip[]; group: string; tokens: string[] }) {
  if (props.aliases.length === 0) return null;
  return (
    <span className="mc-aliases">
      {props.aliases.map((a) => (
        <span
          key={a.alias}
          className="mc-alias"
          title={a.dn ? `${a.dn} (alias → ${props.group})` : `alias → ${props.group}`}
        >
          <Hi text={a.alias} tokens={props.tokens} />
        </span>
      ))}
    </span>
  );
}

/** One route: account badge, account name, arrow, upstream id. */
function RouteLine(props: { provider: string; type?: string; modelId: string; tokens: string[] }) {
  return (
    <li
      className="mc-route"
      style={hueVar(props.provider)}
      title={`${props.provider}${props.type ? ` (${props.type})` : ""} → ${props.modelId}`}
    >
      <ProviderMark name={props.provider} type={props.type} />
      <span className="mc-route-prov">
        <Hi text={props.provider} tokens={props.tokens} />
      </span>
      <span className="mc-route-arrow" aria-hidden>
        →
      </span>
      <span className="mc-route-id">
        <Hi text={props.modelId} tokens={props.tokens} />
      </span>
    </li>
  );
}

function GroupItem(props: {
  g: PublicModelGroup;
  aliases: AliasChip[];
  tokens: string[];
  index: number;
  view: View;
}) {
  const g = props.g;
  const accounts = new Set(g.deployments.map((d) => d.provider)).size;
  return (
    <li
      className={`${props.view === "grid" ? "mc-card" : "mc-row"} mc-enter`}
      style={enterStyle(g.deployments[0]?.provider ?? "", props.index)}
    >
      <div className="mc-item-head">
        <span className="mc-glyph" aria-hidden>
          <ProviderMark
            name={g.deployments[0]?.provider ?? g.name}
            type={g.deployments[0]?.type}
          />
          {accounts > 1 && <span className="mc-glyph-more">+{accounts - 1}</span>}
        </span>
        <div className="mc-item-id">
          <span className="mc-name" title={g.name}>
            <Hi text={g.name} tokens={props.tokens} />
          </span>
          <Aliases aliases={props.aliases} group={g.name} tokens={props.tokens} />
        </div>
        <span className="mc-item-count">
          {g.deployments.length} {g.deployments.length === 1 ? "route" : "routes"}
          {accounts !== g.deployments.length && ` · ${accounts} acct`}
        </span>
        <CopyIdButton text={g.name} label={`Copy model id ${g.name}`} />
      </div>
      <ul className="mc-routes">
        {g.deployments.map((d) => (
          <RouteLine
            key={`${d.provider}/${d.model_id}`}
            provider={d.provider}
            type={d.type}
            modelId={d.model_id}
            tokens={props.tokens}
          />
        ))}
      </ul>
    </li>
  );
}

function ViewToggle(props: { view: View; onChange: (view: View) => void }) {
  return (
    <div className="mc-seg" role="group" aria-label="Catalog layout">
      <button
        type="button"
        aria-pressed={props.view === "list"}
        onClick={() => props.onChange("list")}
        title="Row layout"
        aria-label="Row layout"
        className="mc-seg-btn"
      >
        <Rows3 size={15} aria-hidden />
      </button>
      <button
        type="button"
        aria-pressed={props.view === "grid"}
        onClick={() => props.onChange("grid")}
        title="Card layout"
        aria-label="Card layout"
        className="mc-seg-btn"
      >
        <LayoutGrid size={15} aria-hidden />
      </button>
    </div>
  );
}

function ResultsSkeleton(props: { view: View }) {
  return (
    <ul
      className={`mc-results mc-results-${props.view}`}
      aria-busy="true"
      aria-label="Loading the model catalog"
    >
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <li key={i} className={props.view === "grid" ? "mc-card" : "mc-row"} aria-hidden>
          <span
            className="admin-skeleton block h-3.5 rounded"
            style={{ width: `${8 + ((i * 5) % 14)}rem`, maxWidth: "100%" }}
          />
          <span className="admin-skeleton mt-3 block h-3 w-40 max-w-full rounded" />
        </li>
      ))}
    </ul>
  );
}

function Stat(props: { label: string; value: number }) {
  return (
    <div className="mc-stat">
      <dt>{props.label}</dt>
      <dd>{props.value}</dd>
    </div>
  );
}

// -- page ---------------------------------------------------------------------

export function ModelsCatalogPage() {
  const query = useQuery({
    queryKey: ["public-models"],
    queryFn: getPublicModels,
    refetchInterval: 30_000,
  });
  const [stale, setStale] = useState(false);
  const [announce, setAnnounce] = useState("");
  const [q, setQ] = useState("");
  const [provider, setProvider] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("name");
  const [view, setView] = useState<View>(initialView);
  const searchRef = useRef<HTMLInputElement>(null);
  const [quietSince, setQuietSince] = useState(() => Date.now());

  const announcedRef = useRef(false);
  useEffect(() => {
    if (!query.isSuccess) return;
    setQuietSince(Date.now());
    setStale(false);
    if (announcedRef.current) setAnnounce("Model catalog refreshed");
    announcedRef.current = true;
  }, [query.dataUpdatedAt, query.isSuccess]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (Date.now() - quietSince > 90_000) setStale(true);
    }, 15_000);
    return () => window.clearInterval(id);
  }, [quietSince]);

  const changeView = useCallback((next: View) => {
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // storage unavailable — the choice does not survive a reload
    }
  }, []);

  // "/" focuses search, unless the user is already typing somewhere.
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

  const groups = useMemo(() => query.data?.groups ?? [], [query.data]);
  const aliases = useMemo(() => query.data?.aliases ?? {}, [query.data]);
  const aliasChips = useMemo(() => aliasChipsByGroup(aliases), [aliases]);
  const tokens = useMemo(() => q.toLowerCase().split(/\s+/).filter(Boolean), [q]);

  // A group counts once per account, so a provider's number equals the rows
  // it yields when selected.
  const providerCounts = useMemo(() => {
    const counts = new Map<string, { n: number; type?: string }>();
    for (const g of groups) {
      const seen = new Set<string>();
      for (const d of g.deployments) {
        if (seen.has(d.provider)) continue;
        seen.add(d.provider);
        const cur = counts.get(d.provider);
        counts.set(d.provider, { n: (cur?.n ?? 0) + 1, type: cur?.type ?? d.type });
      }
    }
    return [...counts.entries()].sort((a, b) => b[1].n - a[1].n || a[0].localeCompare(b[0]));
  }, [groups]);

  const filtered = useMemo(() => {
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
  }, [groups, provider, tokens, sort, aliasChips]);

  const routeCount = useMemo(() => groups.reduce((n, g) => n + g.deployments.length, 0), [groups]);
  const aliasCount = Object.keys(aliases).length;
  const filtering = q.trim() !== "" || provider !== null;
  const loaded = !query.isLoading && !query.error && groups.length > 0;
  const countLabel = `${filtering ? `${filtered.length} of ${groups.length}` : groups.length} groups`;
  const clearAll = () => {
    setQ("");
    setProvider(null);
  };

  let body: ReactNode;
  if (query.isLoading) {
    body = <ResultsSkeleton view={view} />;
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
            <button type="button" onClick={clearAll} className="admin-btn admin-btn-ghost min-h-11">
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
  } else {
    body = (
      <ul className={`mc-results mc-results-${view}`}>
        {filtered.map((g, i) => (
          <GroupItem
            key={g.name}
            g={g}
            aliases={aliasChips[g.name] ?? []}
            tokens={tokens}
            index={i}
            view={view}
          />
        ))}
      </ul>
    );
  }

  const liveText = !loaded ? "Checking" : stale ? "Not refreshing" : "Serving now";

  return (
    <div className="mx-auto max-w-6xl px-4 pb-24 pt-10 sm:px-6 sm:pt-14">
      <header className="mc-head">
        <div className="min-w-0">
          <span className={`mc-live${!loaded || stale ? " mc-live-stale" : ""}`} role="status">
            <span className="mc-live-dot" aria-hidden />
            {liveText}
          </span>
          <h1 className="mc-title">Model catalog</h1>
          <p className="mc-lede">
            The id your client sends, and the account each route lands on. Same list as{" "}
            <code className="font-mono">/v1/models</code> — no key needed.
          </p>
        </div>
        <div className="mc-head-actions">
          <Link to="/v1/models" className={`mc-btn ${FOCUS_RING}`}>
            <Braces size={14} aria-hidden />
            Raw JSON
          </Link>
          <Link to="/playground" className={`mc-btn mc-btn-primary ${FOCUS_RING}`}>
            Open Playground
            <ArrowRight size={14} aria-hidden />
          </Link>
        </div>
      </header>

      {loaded && (
        <dl className="mc-stats">
          <Stat label="Groups" value={groups.length} />
          <Stat label="Routes" value={routeCount} />
          <Stat label="Providers" value={providerCounts.length} />
          <Stat label="Aliases" value={aliasCount} />
        </dl>
      )}
      {!loaded && !query.isLoading && <p className="mc-unavail">catalog unavailable</p>}

      <div className={`mc-layout${loaded ? "" : " mc-layout-solo"}`}>
        {loaded && (
          <aside className="mc-side" aria-label="Search and filter">
            <div className="mc-search">
              <Search size={15} className="mc-search-icon" aria-hidden />
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
                placeholder="Search id, alias, account"
                aria-label="Search the model catalog"
                className="admin-input h-11 pl-10 pr-12 text-[13px]"
              />
              {q === "" ? (
                <kbd className="admin-kbd mc-search-kbd" aria-hidden>
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
                  className={`mc-search-clear ${FOCUS_RING}`}
                >
                  <X size={14} />
                </button>
              )}
            </div>

            <div className="mc-side-head">
              <span className="admin-label">Provider account</span>
              {filtering && (
                <button type="button" onClick={clearAll} className="mc-reset">
                  Reset
                </button>
              )}
            </div>
            <div className="mc-providers" role="group" aria-label="Filter by provider account">
              <button
                type="button"
                aria-pressed={provider === null}
                onClick={() => setProvider(null)}
                className={`mc-prov-btn ${FOCUS_RING}`}
              >
                <span className="mc-prov-all" aria-hidden />
                <span className="mc-prov-name">All providers</span>
                <span className="mc-prov-n">{groups.length}</span>
              </button>
              {providerCounts.map(([p, info]) => (
                <button
                  key={p}
                  type="button"
                  aria-pressed={provider === p}
                  onClick={() => setProvider(provider === p ? null : p)}
                  className={`mc-prov-btn ${FOCUS_RING}`}
                  style={hueVar(p)}
                >
                  <ProviderMark name={p} type={info.type} />
                  <span className="mc-prov-name">{p}</span>
                  <span className="mc-prov-n">{info.n}</span>
                </button>
              ))}
            </div>
          </aside>
        )}

        <section className="mc-main" aria-label="Model groups">
          {loaded && (
            <div className="mc-bar">
              <p className="mc-bar-count" aria-hidden>
                {countLabel}
              </p>
              <span className="sr-only" aria-live="polite">
                {countLabel}
                {announce ? ` — ${announce}` : ""}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setSort((s) => (s === "name" ? "routes" : "name"))}
                  aria-label={
                    sort === "name"
                      ? "Sorted by group name — switch to sorting by route count"
                      : "Sorted by route count — switch to sorting by group name"
                  }
                  className={`mc-btn ${FOCUS_RING}`}
                >
                  <ArrowUpDown size={12} aria-hidden />
                  {sort === "name" ? "Name" : "Routes"}
                </button>
                <ViewToggle view={view} onChange={changeView} />
              </div>
            </div>
          )}
          {body}
          {loaded && (
            <p className="mc-foot">
              Ids are matched exactly. Aliases resolve to a group before the router picks the
              provider account, so the client only ever names the group or one of its aliases.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
