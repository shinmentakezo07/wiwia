// ModelsCatalog — public, secret-free model catalog from /public/models.
// No auth required. Inherits the site's dark console world (hairline borders,
// admin-card surfaces, mono data, blue→violet accents) at the same hero
// quality bar as Pricing: live stats from the real payload, search + provider
// filter, alias chips rendered on the cards they target (no separate panel),
// skeleton loading, and an always-visible "try in playground" affordance —
// the previous hover-only reveal was unusable on touch and keyboard.

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Boxes, Check, Copy, Search, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getPublicModels } from "@/api/client";
import { aliasDisplayName, aliasTarget, type PublicModelGroup } from "@/api/types";
import { Badge, Card, EmptyState, ErrorText } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50";

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

// -- small parts --------------------------------------------------------------

function Stat(props: { value: number; label: string }) {
  return (
    <div className="text-center">
      <div className="font-mono text-2xl font-semibold text-[var(--admin-text)] sm:text-3xl" style={{ fontFamily: MONO }}>
        {props.value}
      </div>
      <div className="mt-1 text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--admin-text-dim)]">
        {props.label}
      </div>
    </div>
  );
}

function FilterChip(props: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={props.active}
      onClick={props.onClick}
      className={`rounded-full border px-3 py-1.5 text-[12px] font-medium transition-colors ${FOCUS_RING} ${
        props.active
          ? "border-blue-500/40 bg-blue-500/10 text-blue-300"
          : "border-[var(--admin-border)] text-[var(--admin-text-muted)] hover:border-[var(--admin-border-hover)] hover:text-[var(--admin-text)]"
      }`}
    >
      {props.children}
    </button>
  );
}

function CopyModelButton(props: { text: string; label: string }) {
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
      className={`shrink-0 rounded-md p-1.5 text-[var(--admin-text-dim)] opacity-70 transition-[opacity,color,background-color] hover:bg-white/[0.04] hover:text-[var(--admin-text)] hover:opacity-100 ${FOCUS_RING}`}
    >
      {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
    </button>
  );
}

function SkeletonCard() {
  return (
    <Card className="p-5">
      <div className="animate-pulse space-y-3 motion-reduce:animate-none" aria-hidden>
        <div className="h-4 w-1/2 rounded bg-white/[0.06]" />
        <div className="h-3 w-1/4 rounded bg-white/[0.04]" />
        <div className="space-y-2 pt-2">
          <div className="h-8 rounded-lg bg-white/[0.03]" />
          <div className="h-8 rounded-lg bg-white/[0.03]" />
        </div>
      </div>
    </Card>
  );
}

function GroupCard(props: { g: PublicModelGroup; aliases: AliasChip[] }) {
  const g = props.g;
  const provs = [...new Set(g.deployments.map((d) => d.provider))];
  return (
    <Card className="group flex h-full flex-col p-5">
      <div className="flex items-start justify-between gap-3">
        <h3
          className="break-words font-mono text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]"
          style={{ fontFamily: MONO }}
        >
          {g.name}
        </h3>
        <CopyModelButton text={g.name} label={`Copy model id ${g.name}`} />
      </div>
      {props.aliases.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {props.aliases.map((a) => (
            <Badge
              key={a.alias}
              tone="blue"
              title={a.dn ? `${a.dn} (alias → ${g.name})` : `alias → ${g.name}`}
            >
              alias: {a.alias}
            </Badge>
          ))}
        </div>
      )}
      <p className="mt-1.5 text-[11px] text-[var(--admin-text-dim)]">
        {g.deployments.length} deployment{g.deployments.length === 1 ? "" : "s"}
        {" · "}
        {provs.length} provider{provs.length === 1 ? "" : "s"}
      </p>
      <div className="mt-3 space-y-1.5">
        {g.deployments.map((d) => (
          <div
            key={`${d.provider}/${d.model_id}`}
            className="flex items-center gap-2 rounded-lg border border-[var(--admin-border)] bg-white/[0.015] px-2.5 py-1.5 transition-colors hover:border-[var(--admin-border-hover)]"
          >
            <Badge tone="violet">{d.provider}</Badge>
            <span className="truncate font-mono text-[12px] text-[var(--admin-text-muted)]" style={{ fontFamily: MONO }}>
              {d.model_id}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-auto pt-4">
        <Link
          to="/playground"
          className={`inline-flex items-center gap-1.5 text-[12px] font-medium text-blue-300/90 transition-colors hover:text-blue-200 ${FOCUS_RING}`}
        >
          Try in playground
          <ArrowRight size={12} className="transition-transform duration-150 group-hover:translate-x-0.5" />
        </Link>
      </div>
    </Card>
  );
}

// -- page ---------------------------------------------------------------------

export function ModelsCatalogPage() {
  const query = useQuery({ queryKey: ["public-models"], queryFn: getPublicModels });
  const [q, setQ] = useState("");
  const [provider, setProvider] = useState<string | null>(null);

  const groups = useMemo(() => query.data?.groups ?? [], [query.data]);
  const aliases = useMemo(() => query.data?.aliases ?? {}, [query.data]);

  const aliasChips = useMemo(() => aliasChipsByGroup(aliases), [aliases]);

  const providers = useMemo(
    () => [...new Set(groups.flatMap((g) => g.deployments.map((d) => d.provider)))].sort(),
    [groups],
  );

  const filtered = useMemo(() => {
    const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
    return groups.filter((g) => {
      if (provider && !g.deployments.some((d) => d.provider === provider)) return false;
      if (tokens.length === 0) return true;
      const aliasList = aliasChips[g.name] ?? [];
      const hay = [
        g.name,
        ...aliasList.map((a) => a.alias),
        ...g.deployments.map((d) => `${d.provider} ${d.model_id}`),
      ]
        .join(" ")
        .toLowerCase();
      return tokens.every((t) => hay.includes(t));
    });
  }, [groups, provider, q, aliasChips]);

  const totals = useMemo(
    () => ({
      deployments: groups.reduce((n, g) => n + g.deployments.length, 0),
      providers: new Set(groups.flatMap((g) => g.deployments.map((d) => d.provider))).size,
      aliases: Object.keys(aliases).length,
    }),
    [groups, aliases],
  );

  const filtering = q.trim() !== "" || provider !== null;

  return (
    <div className="pb-20">
      {/* ════════ hero ════════ */}
      <section className="relative overflow-hidden pb-2 pt-10 text-center">
        {/* soft radial glow behind the headline */}
        <div
          className="pointer-events-none absolute left-1/2 top-6 h-64 w-[560px] max-w-full -translate-x-1/2 rounded-full"
          style={{ background: "radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 65%)" }}
          aria-hidden
        />
        <div className="animate-hero-enter relative z-10 mx-auto max-w-3xl">
          <h1 className="text-4xl font-bold tracking-[-0.02em] text-[var(--admin-text)] sm:text-5xl">
            Every model.{" "}
            <span className="bg-gradient-to-r from-blue-400 via-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
              One endpoint.
            </span>
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            The model groups this gateway routes, the providers behind each one, and a playground
            to try them — straight from the live router config.
          </p>
          <div className="mt-7 flex flex-wrap items-center justify-center gap-2.5">
            <Link
              to="/playground"
              className={`inline-flex h-11 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-medium text-white shadow-lg shadow-brand-600/25 transition-[filter] duration-150 hover:brightness-110 ${FOCUS_RING}`}
            >
              Open Playground
              <ArrowRight size={14} />
            </Link>
            <Link
              to="/docs"
              className={`inline-flex h-11 items-center justify-center rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors duration-150 hover:border-white/[0.14] hover:bg-white/[0.04] ${FOCUS_RING}`}
            >
              Read the docs
            </Link>
          </div>
        </div>
        {groups.length > 0 && (
          <div className="relative z-10 mx-auto mt-12 grid max-w-3xl grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat value={groups.length} label="Model groups" />
            <Stat value={totals.deployments} label="Deployments" />
            <Stat value={totals.providers} label="Providers" />
            <Stat value={totals.aliases} label="Aliases" />
          </div>
        )}
      </section>

      {/* ════════ catalog ════════ */}
      <section className="scroll-reveal mx-auto mt-16 max-w-6xl">
        <div className="flex flex-col gap-3">
          <div className="relative">
            <Search
              size={15}
              className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
            />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search groups, providers, or deployment ids…"
              aria-label="Search model catalog"
              className="admin-input h-11 pl-10 pr-9"
            />
            {q !== "" && (
              <button
                type="button"
                onClick={() => setQ("")}
                aria-label="Clear search"
                className={`absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] ${FOCUS_RING}`}
              >
                <X size={14} />
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {providers.length > 1 && (
              <>
                <FilterChip active={provider === null} onClick={() => setProvider(null)}>
                  All providers
                </FilterChip>
                {providers.map((p) => (
                  <FilterChip
                    key={p}
                    active={provider === p}
                    onClick={() => setProvider(provider === p ? null : p)}
                  >
                    {p}
                  </FilterChip>
                ))}
              </>
            )}
            {groups.length > 0 && (
              <span
                className="ml-auto font-mono text-[11px] text-[var(--admin-text-dim)]"
                style={{ fontFamily: MONO }}
              >
                {filtering ? `${filtered.length} of ${groups.length} groups` : `${groups.length} groups`}
              </span>
            )}
          </div>
        </div>

        {query.isLoading ? (
          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : query.error ? (
          <div className="mt-8 flex flex-col items-center gap-3">
            <ErrorText>{query.error.message}</ErrorText>
            <button type="button" onClick={() => void query.refetch()} className="admin-btn admin-btn-ghost">
              Retry
            </button>
          </div>
        ) : groups.length === 0 ? (
          <Card className="mt-6">
            <EmptyState>
              <Boxes size={20} className="mx-auto mb-3 opacity-40" />
              No model groups are configured on this gateway yet. Add model_list entries in
              wiwi.yaml and they'll appear here.
            </EmptyState>
          </Card>
        ) : filtered.length === 0 ? (
          <Card className="mt-6 px-4 py-12 text-center">
            <Search size={20} className="mx-auto mb-3 opacity-40" />
            <p className="text-[13px] text-[var(--admin-text-muted)]">
              No models match
              {q !== "" && (
                <>
                  {" "}
                  “<span className="font-mono" style={{ fontFamily: MONO }}>{q}</span>”
                </>
              )}
              {provider && (
                <>
                  {" "}
                  in <span className="font-mono" style={{ fontFamily: MONO }}>{provider}</span>
                </>
              )}
              .
            </p>
            <button
              type="button"
              onClick={() => {
                setQ("");
                setProvider(null);
              }}
              className="admin-btn admin-btn-ghost mt-4"
            >
              Clear filters
            </button>
          </Card>
        ) : (
          <div className="admin-stagger mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((g) => (
              <GroupCard key={g.name} g={g} aliases={aliasChips[g.name] ?? []} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
