// Models page — model groups, their deployments, and how traffic splits
// across them.
//
// The previous layout was one card per group holding a wrapped pile of
// chips. At the live scale on this box (58 groups / 72 deployments) that
// meant scrolling a wall of near-identical cards with no way to search, and
// a bare "w 1" badge that says nothing about a deployment's real share of
// traffic. What this page does instead:
//
//   * search plus provider/status filters, so 58 groups are navigable;
//   * a summary row: groups / deployments / healthy / needs attention;
//   * per-deployment effective share of the group's AVAILABLE weight and a
//     stacked provider bar per group, computed the way the router computes
//     it so the number matches routing rather than the config file;
//   * an explicit warning on any group holding two deployments with the same
//     provider + model_id, which the admin API cannot address individually.
//
// Deployment rows reflow from one line to two below `md` rather than
// wrapping into an unreadable stack.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Layers, Search, Server, TriangleAlert, X } from "lucide-react";
import { getModels, patchModelGroup } from "@/api/client";
import {
  aliasDisplayName,
  aliasTarget,
  type DeploymentInfo,
  type ModelAliasEntry,
  type ModelGroup,
} from "@/api/types";
import { useAuth } from "@/api/auth";
import {
  Badge,
  Card,
  EmptyState,
  ErrorText,
  Input,
  NumberInput,
  PageHeader,
  Select,
  Spinner,
  StatCard,
} from "@/components/ui";
import { fmtInt } from "@/lib/format";

const STRATEGIES = [
  { value: "simple-shuffle", label: "Weighted random (simple-shuffle)" },
  { value: "least-busy", label: "Least busy" },
  { value: "latency-based", label: "Latency based" },
];

const STATUS_FILTERS = [
  { value: "all", label: "All groups" },
  { value: "healthy", label: "Healthy only" },
  { value: "attention", label: "Needs attention" },
  { value: "multi", label: "Multi-deployment" },
];

const SORTS = [
  { value: "name", label: "Sort: name" },
  { value: "deployments", label: "Sort: most deployments" },
  { value: "weight", label: "Sort: most weight" },
];

// Stacked-bar segment palette. Inline styles, not utility classes: the colour
// is chosen at runtime from the provider name, so Tailwind cannot see it.
const SEGMENT_COLORS = [
  "#60a5fa",
  "#a78bfa",
  "#34d399",
  "#fbbf24",
  "#f472b6",
  "#22d3ee",
  "#fb923c",
  "#a3e635",
];

/** Stable per-provider colour so a provider keeps its hue across groups. */
function segColor(provider: string): string {
  let h = 0;
  for (let i = 0; i < provider.length; i++) h = (h * 31 + provider.charCodeAt(i)) >>> 0;
  return SEGMENT_COLORS[h % SEGMENT_COLORS.length];
}

/** The router clamps weights to >= 1 and has no "pause" semantic. */
function weightOf(d: DeploymentInfo): number {
  return Math.max(1, d.weight);
}

/**
 * The deployments the router actually picks from, mirroring
 * `Router.pick_deployment`: available ones, and among those only the
 * non-probation ones whenever at least one exists. A healer-restored
 * (probation) deployment gets zero traffic while a healthy sibling is up.
 */
function routable(group: ModelGroup): DeploymentInfo[] {
  const avail = group.deployments.filter((x) => x.available);
  const fresh = avail.filter((x) => !x.probation);
  return fresh.length > 0 ? fresh : avail;
}

/**
 * Share of the group's routable weight.
 *
 * The router recomputes weights from the routable candidate set on every
 * pick, and its two-level cross-provider WRR composes back down to exactly
 * this ratio per deployment — so this matches routing, not the config file.
 */
function sharePct(d: DeploymentInfo, group: ModelGroup): number {
  const set = routable(group);
  if (!set.includes(d)) return 0;
  const total = set.reduce((s, x) => s + weightOf(x), 0);
  return total > 0 ? weightOf(d) / total : 0;
}

type DepState = "ok" | "standby" | "cooling" | "down";

function depState(d: DeploymentInfo, group: ModelGroup): DepState {
  if (d.available) return routable(group).includes(d) ? "ok" : "standby";
  return d.cooldown_remaining_s > 0 ? "cooling" : "down";
}

function HealthDot(props: { state: DepState; title: string }) {
  const color =
    props.state === "ok"
      ? "#34d399"
      : props.state === "standby"
        ? "#60a5fa"
        : props.state === "cooling"
          ? "#fbbf24"
          : "#f87171";
  return (
    <span
      title={props.title}
      aria-hidden
      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
      style={{ backgroundColor: color, boxShadow: `0 0 8px -2px ${color}` }}
    />
  );
}

/** p95 / inflight / cooldown, phrased for whichever state the row is in. */
function DeploymentMetrics(props: { d: DeploymentInfo; group: ModelGroup }) {
  const { d } = props;
  const state = depState(d, props.group);
  if (state === "standby") {
    return (
      <span className="font-mono text-[11px] tabular-nums text-sky-400/90">probation</span>
    );
  }
  if (state === "cooling") {
    return (
      <span className="font-mono text-[11px] tabular-nums text-amber-400/90">
        cooling {Math.ceil(d.cooldown_remaining_s)}s
      </span>
    );
  }
  if (state === "down") {
    return (
      <span className="font-mono text-[11px] tabular-nums text-red-400/90">unavailable</span>
    );
  }
  // p95 is 0 until the deployment has served enough requests to have a sample.
  const p95 = d.p95_latency_ms > 0 ? `p95 ${fmtInt(Math.round(d.p95_latency_ms))}ms` : "p95 —";
  return (
    <span className="font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
      {p95}
      <span className="mx-1.5 text-[var(--admin-border-hover)]">·</span>
      {d.inflight} inflight
    </span>
  );
}

/**
 * Per-deployment weight editor.
 *
 * Commits on a short debounce rather than on an explicit Save button, so the
 * steppers, the keyboard and touch all behave the same. Blur and Escape
 * simply stop editing — committing on blur would fight the NumberInput
 * steppers, which move focus on mousedown and would commit the pre-click
 * value.
 */
function WeightEditor(props: {
  weight: number;
  disabled: boolean;
  disabledReason: string;
  pending: boolean;
  onSave: (w: number) => void;
}) {
  const [value, setValue] = useState(String(props.weight));
  const dirty = value !== String(props.weight);

  // Track the server value while idle so a refetch corrects the field, but
  // never yank the box out from under someone mid-edit.
  useEffect(() => {
    if (!dirty) setValue(String(props.weight));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.weight]);

  const saveRef = useRef(props.onSave);
  useEffect(() => {
    saveRef.current = props.onSave;
  });

  useEffect(() => {
    if (!dirty || props.disabled) return;
    const n = parseInt(value, 10);
    if (!Number.isFinite(n) || n < 1) return;
    const t = setTimeout(() => saveRef.current(n), 500);
    return () => clearTimeout(t);
  }, [value, dirty, props.disabled]);

  const commit = useCallback(() => {
    const n = parseInt(value, 10);
    if (!Number.isFinite(n) || n < 1) {
      setValue(String(props.weight));
      return;
    }
    if (n !== props.weight) saveRef.current(n);
  }, [value, props.weight]);

  if (props.disabled) {
    return (
      <Badge tone="gray" title={props.disabledReason}>
        w {props.weight}
      </Badge>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <NumberInput
        value={value}
        onChange={setValue}
        min={1}
        step={1}
        className="w-[5.5rem] px-1 text-center font-mono text-[12px] tabular-nums"
        aria-label={`Deployment weight, currently ${props.weight}`}        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            setValue(String(props.weight));
            e.currentTarget.blur();
          }
        }}
      />
      {props.pending && <Spinner className="h-3.5 w-3.5" />}
    </div>
  );
}

/** Stacked bar of available weight per provider, for one group. */
function ProviderBar(props: { group: ModelGroup }) {
  const avail = routable(props.group);
  if (avail.length < 2) return null;

  const byProvider = new Map<string, number>();
  for (const d of avail) {
    byProvider.set(d.provider, (byProvider.get(d.provider) ?? 0) + weightOf(d));
  }
  const total = [...byProvider.values()].reduce((a, b) => a + b, 0);
  if (total <= 0) return null;
  const segs = [...byProvider.entries()].sort((a, b) => b[1] - a[1]);
  const label = segs.map(([p, w]) => `${p} ${Math.round((w / total) * 100)}%`).join(", ");

  return (
    <div className="px-4 pb-2 pt-3">
      <div
        role="img"
        aria-label={`Traffic split by provider — ${label}`}
        className="flex h-1.5 w-full gap-0.5 overflow-hidden rounded-full"
      >
        {segs.map(([p, w]) => (
          <span
            key={p}
            title={`${p} · ${Math.round((w / total) * 100)}% of available weight`}
            style={{ width: `${(w / total) * 100}%`, backgroundColor: segColor(p) }}
          />
        ))}
      </div>
    </div>
  );
}

function DeploymentRow(props: {
  d: DeploymentInfo;
  group: ModelGroup;
  ambiguous: boolean;
  editable: boolean;
  pending: boolean;
  onSave: (group: string, ident: string, weight: number) => void;
}) {
  const { d, group } = props;
  const ident = `${d.provider}/${d.model_id}`;
  const state = depState(d, group);
  const pct = sharePct(d, group);

  const statusTitle =
    state === "ok"
      ? `available · ${d.inflight} in flight${
          d.p95_latency_ms > 0 ? ` · p95 ${Math.round(d.p95_latency_ms)}ms` : ""
        }`
      : state === "standby"
        ? "on probation after a health-check restore — receives no traffic while a healthy sibling is available"
      : state === "cooling"
        ? `cooling for another ${Math.ceil(d.cooldown_remaining_s)}s — excluded from routing`
        : "unavailable — excluded from routing";

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 transition-colors hover:bg-white/[0.015]">
      {/* Line 1 on mobile: identity left, share right. The share cell drops
          its auto margin at md+ so the desktop row stays one line. */}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <HealthDot state={state} title={statusTitle} />
        <span className="truncate text-[13px] font-medium text-[var(--admin-text)]">
          {d.provider}
        </span>
      </div>

      <div className="ml-auto shrink-0 text-right md:ml-0">
        {state === "ok" ? (
          <span
            className="font-mono text-[12px] tabular-nums text-[var(--admin-text)]"
            title={`${Math.round(pct * 100)}% of this group's currently-available weight`}
          >
            {Math.round(pct * 100)}%
          </span>
        ) : (
          <span
            className="font-mono text-[12px] tabular-nums text-[var(--admin-text-dim)]"
            title={
              state === "standby"
                ? "Held back while on probation — a healthy sibling takes the traffic"
                : "Excluded from routing while it is cooling or down"
            }
          >
            —
          </span>
        )}
      </div>

      {/* basis-full forces the model id onto its own line below md, so the
          wrap is deterministic instead of ragged. */}
      <div className="min-w-0 basis-full truncate font-mono text-[12px] text-[var(--admin-text-muted)] md:basis-auto">
        {d.model_id}
      </div>

      <div className="shrink-0">
        <DeploymentMetrics d={d} group={group} />
      </div>

      <div className="ml-auto shrink-0 md:ml-0">
        <WeightEditor
          weight={d.weight}
          pending={props.pending}
          disabled={!props.editable || props.ambiguous}
          disabledReason={
            props.ambiguous
              ? "This group has two deployments with the same provider and model — the admin API keys weights by that pair, so it can only address one of them."
              : "Deployment weights are admin-only."
          }
          onSave={(w) => props.onSave(group.name, ident, w)}
        />
      </div>
    </div>
  );
}

function GroupCard(props: {
  g: ModelGroup;
  aliases: Record<string, string | ModelAliasEntry>;
  editable: boolean;
  pendingIdent: string | null;
  onSave: (group: string, ident: string, weight: number) => void;
}) {
  const { g } = props;

  // Count how many deployments share each provider/model_id pair: the admin
  // API keys weights by that pair (app.py builds a last-wins dict), so a
  // duplicate is genuinely unaddressable rather than merely ugly.
  const identCounts = new Map<string, number>();
  for (const d of g.deployments) {
    const k = `${d.provider}/${d.model_id}`;
    identCounts.set(k, (identCounts.get(k) ?? 0) + 1);
  }
  const dupIdents = [...identCounts.entries()].filter(([, n]) => n > 1).map(([k]) => k);

  const totalWeight = g.deployments.reduce((s, d) => s + weightOf(d), 0);
  const providerCount = new Set(g.deployments.map((d) => d.provider)).size;

  const aliasBadges = Object.entries(props.aliases)
    .filter(([, v]) => aliasTarget(v) === g.name)
    .map(([alias, v]) => {
      const dn = aliasDisplayName(v);
      return (
        <Badge key={alias} tone="blue" title={dn ? `${dn} (alias → ${g.name})` : `alias → ${g.name}`}>
          alias: {alias}
          {dn ? ` · ${dn}` : ""}
        </Badge>
      );
    });

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3">
        <h3
          className="min-w-0 truncate font-mono text-[13px] font-semibold text-[var(--admin-text)]"
          title={g.name}
        >
          {g.name}
        </h3>
        {aliasBadges}
        <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
          {g.deployments.length} deployment{g.deployments.length === 1 ? "" : "s"}
          {providerCount > 1 ? ` · ${providerCount} providers` : ""}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {dupIdents.length > 0 && (
            <Badge
              tone="amber"
              title={`Duplicate provider/model pairs (${dupIdents.join(", ")}) — weights for these cannot be addressed individually through the admin API.`}
            >
              {dupIdents.length} ambiguous
            </Badge>
          )}
          <Badge tone="gray" title="Sum of configured deployment weights">
            Σ {fmtInt(totalWeight)}
          </Badge>
        </div>
      </div>

      <ProviderBar group={g} />

      <div className="divide-y divide-[var(--admin-border)] border-t border-[var(--admin-border)]">
        {g.deployments.map((d, i) => {
          const ident = `${d.provider}/${d.model_id}`;
          return (
            <DeploymentRow
              // Duplicate pairs are addressable only by position, so the
              // index disambiguates React keys as well as the API warning.
              key={`${ident}#${i}`}
              d={d}
              group={g}
              ambiguous={dupIdents.includes(ident)}
              editable={props.editable}
              pending={props.pendingIdent === ident}
              onSave={props.onSave}
            />
          );
        })}
      </div>
    </Card>
  );
}

export function ModelsPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const editable = user?.role === "admin";
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [provider, setProvider] = useState("");
  const [sort, setSort] = useState("name");
  const [pendingIdent, setPendingIdent] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["models"], queryFn: getModels, refetchInterval: 10_000 });

  // Hooks must run unconditionally: declared before the loading/error early
  // returns, reading query.data at call time inside the mutation.
  const setStrategy = useMutation({
    mutationFn: (strategy: string) => {
      const groups = query.data?.groups ?? [];
      if (!groups.length) return Promise.reject(new Error("no groups to patch"));
      // strategy is global router state; any known group path applies it
      return patchModelGroup(groups[0].name, { strategy });
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e) => setError(e.message),
  });

  const patchWeight = useMutation({
    mutationFn: (v: { group: string; ident: string; weight: number }) =>
      patchModelGroup(v.group, { weights: { [v.ident]: v.weight } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e) => setError(e.message),
    onSettled: () => setPendingIdent(null),
  });

  const save = useCallback(
    (group: string, ident: string, weight: number) => {
      setPendingIdent(ident);
      patchWeight.mutate({ group, ident, weight });
    },
    [patchWeight],
  );

  const providers = useMemo(() => {
    const groups = query.data?.groups ?? [];
    return [...new Set(groups.flatMap((g) => g.deployments.map((d) => d.provider)))].sort();
  }, [query.data]);

  const visible = useMemo(() => {
    const groups = query.data?.groups ?? [];
    const aliases = query.data?.aliases ?? {};
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean);

    const filtered = groups.filter((g) => {
      // Alias names are searchable so a group is findable by the name a
      // client actually requests.
      const aliasKeys = Object.entries(aliases)
        .filter(([, v]) => aliasTarget(v) === g.name)
        .map(([k, v]) => `${k} ${aliasDisplayName(v) ?? ""}`);
      const hay = [
        g.name,
        ...aliasKeys,
        ...g.deployments.flatMap((d) => [d.provider, d.model_id]),
      ]
        .join(" ")
        .toLowerCase();
      if (terms.length > 0 && !terms.every((t) => hay.includes(t))) return false;

      if (provider && !g.deployments.some((d) => d.provider === provider)) return false;

      if (status === "healthy" && !g.deployments.every((d) => d.available)) return false;
      if (status === "attention" && g.deployments.every((d) => d.available)) return false;
      if (status === "multi" && g.deployments.length < 2) return false;

      return true;
    });

    const sorted = [...filtered];
    if (sort === "deployments") {
      sorted.sort((a, b) => b.deployments.length - a.deployments.length || a.name.localeCompare(b.name));
    } else if (sort === "weight") {
      const w = (g: ModelGroup) => g.deployments.reduce((s, d) => s + weightOf(d), 0);
      sorted.sort((a, b) => w(b) - w(a) || a.name.localeCompare(b.name));
    } else {
      sorted.sort((a, b) => a.name.localeCompare(b.name));
    }
    return sorted;
  }, [query.data, q, status, provider, sort]);

  if (query.isLoading) return <Spinner />;
  if (query.error) return <ErrorText>{query.error.message}</ErrorText>;

  const data = query.data!;
  const allDeployments = data.groups.flatMap((g) => g.deployments);
  const healthy = allDeployments.filter((d) => d.available).length;
  const attention = allDeployments.length - healthy;
  // Available but held back by the router while a healthy sibling serves.
  const standby = data.groups.reduce(
    (n, g) => n + g.deployments.filter((d) => depState(d, g) === "standby").length,
    0,
  );
  const totalGroups = data.groups.length;
  const totalDeployments = allDeployments.length;
  const hasFilters = q !== "" || status !== "all" || provider !== "";

  // Strategy label for the read-only (non-admin) view.
  const strategyLabel = STRATEGIES.find((s) => s.value === data.strategy)?.label ?? data.strategy;

  return (
    <div>
      <PageHeader
        title="Models"
        subtitle={
          editable
            ? "Model groups, their deployments, and how traffic splits across them."
            : "Model groups and their deployments. (Read-only — ask an admin to change weights.)"
        }
        right={
          editable ? (
            <Select
              value={data.strategy}
              onChange={(s) => {
                if (!data.groups.length) return;
                // PATCH applies globally on the router settings; any group path works.
                setStrategy.mutate(s);
              }}
              options={STRATEGIES}
            />
          ) : (
            <Badge tone="gray" title="Routing strategy">
              {strategyLabel}
            </Badge>
          )
        }
      />

      {error && (
        <div className="mb-3">
          <ErrorText>{error}</ErrorText>
        </div>
      )}
      {editable && setStrategy.isPending && <Spinner />}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          icon={Layers}
          tone="brand"
          featured
          label="model groups"
          value={fmtInt(totalGroups)}
          numeric={totalGroups}
          format={fmtInt}
        />
        <StatCard
          icon={Server}
          label="deployments"
          value={fmtInt(totalDeployments)}
          numeric={totalDeployments}
          format={fmtInt}
          sub={`across ${providers.length} providers`}
        />
        <StatCard
          icon={Activity}
          tone={attention === 0 ? "success" : "default"}
          label="available"
          value={`${fmtInt(healthy)}/${fmtInt(totalDeployments)}`}
          sub={
            standby > 0
              ? `${fmtInt(standby)} on probation, held back`
              : attention === 0
                ? "all deployments routing"
                : "in rotation right now"
          }
        />
        <StatCard
          icon={TriangleAlert}
          tone={attention === 0 ? "default" : "warning"}
          label="needs attention"
          value={fmtInt(attention)}
          numeric={attention}
          format={fmtInt}
          sub={attention === 0 ? "nothing cooling or down" : "cooling or unavailable"}
        />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 basis-64">
          <Search
            size={14}
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
          />
          <Input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search groups, providers, model ids, aliases…"
            aria-label="Search model groups"
            spellCheck={false}
            autoComplete="off"
            className="min-h-11 pl-9 pr-12"
          />
          {q !== "" && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setQ("")}
              className="absolute right-1 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
            >
              <X size={14} />
            </button>
          )}
        </div>
        <Select
          value={provider}
          onChange={setProvider}
          className="min-h-11"
          options={[
            { value: "", label: "All providers" },
            ...providers.map((p) => ({ value: p, label: p })),
          ]}
        />
        <Select
          value={status}
          onChange={setStatus}
          className="min-h-11"
          options={STATUS_FILTERS}
        />
        <Select value={sort} onChange={setSort} className="min-h-11" options={SORTS} />
        {visible.length > 0 && (
          <p
            className="font-mono text-[11px] tabular-nums text-[var(--admin-text-muted)]"
            aria-live="polite"
          >
            {visible.length} of {totalGroups} group{totalGroups === 1 ? "" : "s"}
          </p>
        )}
      </div>

      <div className="space-y-3">
        {visible.map((g) => (
          <GroupCard
            key={g.name}
            g={g}
            aliases={data.aliases}
            editable={editable}
            pendingIdent={pendingIdent}
            onSave={save}
          />
        ))}
        {visible.length === 0 && (
          <Card>
            {hasFilters ? (
              <EmptyState>
                No groups match these filters.
                <button
                  type="button"
                  onClick={() => {
                    setQ("");
                    setStatus("all");
                    setProvider("");
                  }}
                  className="ml-2 text-[var(--admin-text)] underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                >
                  Clear filters
                </button>
              </EmptyState>
            ) : (
              <EmptyState>No model groups configured.</EmptyState>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}
