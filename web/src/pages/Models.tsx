// Models page — the routing ledger.
//
// A model group is a signal source; its deployments are where that signal is
// split. So this page is one continuous ruled table rather than a stack of
// cards: a group header row, then its deployment rows indented beneath it.
// The rule between groups carries the hierarchy, and a hairline carries the
// parent/child relationship — no nested boxes, no card per group.
//
// What the page shows, and why each thing earns its place:
//
//   * the group's own name, in the sans and at size — it is the subject;
//   * a split bar, measured the way the router measures it, so the width of
//     each segment is the deployment's real share of routable traffic;
//   * per-deployment share, p95 and in-flight in right-aligned columns, so
//     a scan down the page compares like with like;
//   * the deployment's state as a word in the state colour, not a pill — a
//     pill per row is a wall of chips that reads as decoration;
//   * the weight editor, the one control on the page you actually touch.
//
// Colour is spent in exactly one place: the provider hue. A provider keeps
// its hue in the split bar, in the legend, and in the summary line, so a
// provider is recognisable before you read its name. Everything else is the
// house greyscale.
//
// The previous layout was one card per group holding a wrapped pile of
// chips. At the live scale on this box (58 groups / 72 deployments) that
// meant scrolling a wall of near-identical cards with no way to search, and
// a bare "w 1" badge that says nothing about a deployment's real share of
// traffic. The search, provider/status filters and summary line address
// that; an explicit warning marks any group holding two deployments with
// the same provider + model_id, which the admin API cannot address
// individually.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Search, X } from "lucide-react";
import { getModels, patchModelGroup } from "@/api/client";
import {
  aliasDisplayName,
  aliasTarget,
  type DeploymentInfo,
  type ModelAliasEntry,
  type ModelGroup,
} from "@/api/types";
import { useAuth } from "@/api/auth";
import { EmptyState, ErrorText, Input, NumberInput, PageHeader, Select, Spinner } from "@/components/ui";
import { fmtInt } from "@/lib/format";

const STRATEGIES = [
  { value: "simple-shuffle", label: "Weighted random (simple-shuffle)" },
  { value: "least-busy", label: "Least busy" },
  { value: "latency-based", label: "Latency based" },
];

const STATUS_FILTERS = [
  { value: "all", label: "All groups" },
  { value: "healthy", label: "Healthy" },
  { value: "attention", label: "Needs attention" },
  { value: "multi", label: "Several routes" },
] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number]["value"];

const SORTS = [
  { value: "name", label: "Sort by name" },
  { value: "deployments", label: "Sort by route count" },
  { value: "weight", label: "Sort by weight" },
];

// Provider hues. Twelve entries so two providers rarely collide, and every
// one of them holds contrast on the near-black surface. This is the ONLY
// colour the page spends: a provider keeps its hue in the split bar, in the
// legend and in the summary line.
const HUES = [
  "#7c9cff",
  "#c084fc",
  "#4ade9b",
  "#fbbf24",
  "#f472b6",
  "#22d3ee",
  "#fb923c",
  "#a3e635",
  "#e879f9",
  "#2dd4bf",
  "#94a3b8",
  "#facc15",
];

/** Stable per-provider hue, so an account looks the same on every page. */
function hueOf(provider: string): string {
  let h = 0;
  for (let i = 0; i < provider.length; i++) h = (h * 31 + provider.charCodeAt(i)) >>> 0;
  return HUES[h % HUES.length];
}

/** Router state per deployment, plus the colour every surface paints it in. */
type DepState = "ok" | "standby" | "cooling" | "down";

const STATE_COLOR: Record<DepState, string> = {
  ok: "#4ade9b",
  standby: "#7c9cff",
  cooling: "#fbbf24",
  down: "#f87171",
};

const STATE_WORD: Record<DepState, string> = {
  ok: "routing",
  standby: "probation",
  cooling: "cooling",
  down: "down",
};

const STATE_TITLE: Record<DepState, string> = {
  ok: "available and taking traffic",
  standby:
    "on probation after a health-check restore — receives no traffic while a healthy sibling is available",
  cooling: "in cooldown — excluded from routing until it expires",
  down: "unavailable — excluded from routing",
};

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

function depState(d: DeploymentInfo, group: ModelGroup): DepState {
  if (d.available) return routable(group).includes(d) ? "ok" : "standby";
  return d.cooldown_remaining_s > 0 ? "cooling" : "down";
}

interface GroupHealth {
  color: string;
  word: string;
  title: string;
  down: number;
  cooling: number;
  standby: number;
}

/** Roll the deployment states up into one status for the group header. */
function groupHealth(group: ModelGroup): GroupHealth {
  const routableSet = new Set(routable(group));
  let down = 0;
  let cooling = 0;
  let standby = 0;
  for (const d of group.deployments) {
    if (!d.available) {
      if (d.cooldown_remaining_s > 0) cooling += 1;
      else down += 1;
    } else if (d.probation && !routableSet.has(d)) {
      standby += 1;
    }
  }

  // A group with no deployments routes nothing. It is not "healthy" — it is
  // empty, and saying otherwise would report an idle group as a working one.
  if (group.deployments.length === 0) {
    return {
      color: STATE_COLOR.down,
      word: "no routes",
      title: "this group has no deployments — it cannot route any traffic",
      down: 0,
      cooling: 0,
      standby: 0,
    };
  }

  const color =
    down > 0
      ? STATE_COLOR.down
      : cooling > 0
        ? STATE_COLOR.cooling
        : standby > 0
          ? STATE_COLOR.standby
          : STATE_COLOR.ok;

  const problems: string[] = [];
  if (down > 0) problems.push(`${fmtInt(down)} down`);
  if (cooling > 0) problems.push(`${fmtInt(cooling)} cooling`);
  if (standby > 0) problems.push(`${fmtInt(standby)} on probation`);

  return {
    color,
    word: problems.length > 0 ? problems.join(", ") : "all routing",
    title:
      problems.length > 0
        ? `${problems.join(", ")} — routing excludes the affected deployments`
        : "every deployment is available and in rotation",
    down,
    cooling,
    standby,
  };
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
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The last weight this field committed. Enter and the debounce both commit,
  // and without this a single keystroke PATCHed twice (two audit rows, two
  // config writes) — AUDIT #378.
  const savedRef = useRef<number | null>(null);

  // Track the server value while idle so a refetch corrects the field, but
  // never yank the box out from under someone mid-edit.
  useEffect(() => {
    if (!dirty) {
      setValue(String(props.weight));
      savedRef.current = props.weight;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.weight]);

  const saveRef = useRef(props.onSave);
  useEffect(() => {
    saveRef.current = props.onSave;
  });

  const save = useCallback((n: number) => {
    if (savedRef.current === n) return;
    savedRef.current = n;
    saveRef.current(n);
    // Show exactly what was committed. ``parseInt`` truncates a typed decimal,
    // and leaving "2.5" in the box kept ``dirty`` true forever, so every later
    // refetch was ignored and the field never agreed with the server
    // (AUDIT #378).
    setValue(String(n));
  }, []);

  useEffect(() => {
    if (!dirty || props.disabled) return;
    const n = parseInt(value, 10);
    if (!Number.isFinite(n) || n < 1) return;
    const t = setTimeout(() => {
      timerRef.current = null;
      save(n);
    }, 500);
    timerRef.current = t;
    return () => {
      clearTimeout(t);
      if (timerRef.current === t) timerRef.current = null;
    };
  }, [value, dirty, props.disabled, save]);

  const commit = useCallback(() => {
    // Enter commits now, so disarm the debounce first: leaving it armed PATCHed
    // the same weight a second time 500 ms later (AUDIT #378).
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const n = parseInt(value, 10);
    if (!Number.isFinite(n) || n < 1) {
      setValue(String(props.weight));
      return;
    }
    if (n !== props.weight) save(n);
    else setValue(String(n));
  }, [value, props.weight, save]);

  if (props.disabled) {
    return (
      <span
        className="wl-num inline-block"
        title={props.disabledReason}
        style={{ color: "var(--admin-text-dim)" }}
      >
        {props.weight}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center justify-end gap-2">
      <NumberInput
        value={value}
        onChange={setValue}
        min={1}
        step={1}
        className="wl-weight px-1.5 text-center"
        aria-label={`Deployment weight, currently ${props.weight}`}
        onKeyDown={(e) => {
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
    </span>
  );
}

/**
 * The split bar: one segment per provider, each segment's width being that
 * provider's share of the group's routable weight.
 *
 * This is the page's protagonist, so it is drawn at real scale against a
 * measured background rather than as a decorative strip.
 */
function SplitBar(props: { group: ModelGroup }) {
  const g = props.group;
  const avail = routable(g);

  const byProvider = new Map<string, number>();
  for (const d of avail) {
    byProvider.set(d.provider, (byProvider.get(d.provider) ?? 0) + weightOf(d));
  }
  const total = [...byProvider.values()].reduce((a, b) => a + b, 0);
  if (total <= 0) return null;
  const segs = [...byProvider.entries()].sort((a, b) => b[1] - a[1]);

  // A group with one routable provider has no split to show. Drawing a
  // full-width 100% bar for it states nothing and fills the row with a
  // slab, and naming the provider repeats the row below. So the band says
  // only what the row cannot: how many deployments are NOT taking traffic.
  if (segs.length === 1) {
    const held = g.deployments.length - avail.length;
    if (held === 0) return null;
    return (
      <p className="text-[11.5px] text-amber-400/90">
        {fmtInt(held)} deployment{held === 1 ? "" : "s"} held back from routing
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
      {/* The track is a fixed-width meter with visible empty space, not a
          full-width banner: a bar that stretches the whole card turns a
          measurement into a slab. At this width a 50/50 split and a 90/10
          split are visibly different quantities, which is the whole point
          of drawing it. */}
      <div
        className="wl-split w-full max-w-[22rem] shrink-0"
        role="img"
        aria-label={`Traffic split by provider — ${segs
          .map(([p, w]) => `${p} ${Math.round((w / total) * 100)}%`)
          .join(", ")}`}
      >
        {segs.map(([p, w]) => (
          <span
            key={p}
            title={`${p} — ${Math.round((w / total) * 100)}% of routable weight`}
            style={{ width: `${(w / total) * 100}%`, backgroundColor: hueOf(p) }}
          />
        ))}
      </div>
      {/* The legend names each segment, so the bar is readable without
          hovering it. The percentage lives on the deployment row, which is
          the per-route truth; repeating it here would say the same number
          twice on the same screen. */}
      <span className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1">
        {segs.map(([p]) => (
          <span
            key={p}
            className="flex items-center gap-1.5 text-[11px] text-[var(--admin-text-dim)]"
          >
            <span className="wl-hue" style={{ backgroundColor: hueOf(p) }} />
            <span className="font-mono">{p}</span>
          </span>
        ))}
      </span>
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
      : STATE_TITLE[state];

  return (
    <tr className="wl-dep">
      <td colSpan={2} className="wl-indent wl-ident">
        <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <span
            aria-hidden
            className="wl-hue !h-2 !w-2 !rounded-full"
            style={{
              backgroundColor: hueOf(d.provider),
              boxShadow: `0 0 6px ${hueOf(d.provider)}55`,
            }}
          />
          <span className="text-[13px] font-medium text-[var(--admin-text)]">{d.provider}</span>
          <span className="truncate font-mono text-[11.5px] text-[var(--admin-text-dim)]">
            {d.model_id}
          </span>
          <span className="wl-state" style={{ color: STATE_COLOR[state] }} title={statusTitle}>
            {state === "ok" ? (
              <>
                {Math.round(pct * 100)}% share
              </>
            ) : (
              <>
                {STATE_WORD[state]}
                {state === "cooling" && (
                  <span className="ml-1.5 text-[var(--admin-text-dim)]">
                    {Math.ceil(d.cooldown_remaining_s)}s
                  </span>
                )}
              </>
            )}
          </span>
          {/* The metrics ride inline here below md, where the table's own
              p95 / in-flight columns cannot fit. One set of values, one
              place: rendering a second copy for the narrow layout gave
              two editors for one weight, and the two fought. */}
          <span className="wl-m-inline">
            {state === "ok" && d.p95_latency_ms > 0 ? (
              <span title="p95 latency of recent requests on this deployment">
                p95 {fmtInt(Math.round(d.p95_latency_ms))}ms
              </span>
            ) : null}
            <span title={`${d.inflight} request(s) in flight`}>
              {fmtInt(d.inflight)} in flight
            </span>
          </span>
        </span>
      </td>

      <td
        className="wl-num wl-metric wl-m-p95"
        data-label="p95"
        title="p95 latency of recent requests on this deployment"
      >
        {state === "ok" && d.p95_latency_ms > 0 ? `${fmtInt(Math.round(d.p95_latency_ms))}ms` : "—"}
      </td>

      <td
        className="wl-num wl-metric wl-m-inflight"
        data-label="in flight"
        title={`${d.inflight} request(s) in flight`}
      >
        {state === "ok" ? fmtInt(d.inflight) : "—"}
      </td>

      <td
        className="wl-num wl-weight"
        data-label="weight"
        title="Configured weight — the router picks by this"
      >
        <WeightEditor
          weight={d.weight}
          pending={props.pending}
          disabled={!props.editable || props.ambiguous}
          disabledReason={
            props.ambiguous
              ? "This group has two deployments with the same provider and model — the admin API keys weights by that pair, so it can only address one of them."
              : "Deployment weights are admin-only."
          }          onSave={(w) => props.onSave(group.name, ident, w)}
        />
      </td>
    </tr>
  );
}
function GroupRows(props: {
  g: ModelGroup;
  aliases: Record<string, string | ModelAliasEntry>;
  editable: boolean;
  pendingIdent: string | null;
  onSave: (group: string, ident: string, weight: number) => void;
}) {
  const { g } = props;
  const health = groupHealth(g);

  // Count how many deployments share each provider/model_id pair: the admin
  // API keys weights by that pair (app.py builds a last-wins dict), so a
  // duplicate is genuinely unaddressable rather than merely ugly.
  const identCounts = new Map<string, number>();
  for (const d of g.deployments) {
    const k = `${d.provider}/${d.model_id}`;
    identCounts.set(k, (identCounts.get(k) ?? 0) + 1);
  }
  const dupIdents = [...identCounts.entries()].filter(([, n]) => n > 1).map(([k]) => k);

  const aliasEntries = Object.entries(props.aliases).filter(([, v]) => aliasTarget(v) === g.name);

  return (
    <>
      {/* The group is a band, not a row of the same table: it spans the full
          width so the split bar has room to be read as a bar, and so it never
          sits under the p95 / in-flight / weight headers, which describe the
          deployment rows beneath it. */}
      <tr className="wl-group">
        <td colSpan={5}>
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <span className="wl-group-name truncate" title={g.name}>
              {g.name}
            </span>
            {/* The group's own health, as a word in the state colour. A pill
                per group was a row of chips; one coloured word reads faster
                and spends no chrome. */}
            <span className="wl-state" style={{ color: health.color }} title={health.title}>
              {health.word}
            </span>
            {aliasEntries.map(([alias, v]) => (
              <span
                key={alias}
                className="truncate rounded-[5px] border border-[var(--admin-border)] px-1.5 py-0.5 text-[10.5px] text-[var(--admin-text-muted)]"
                title={
                  aliasDisplayName(v) ? `${aliasDisplayName(v)} → ${g.name}` : `alias → ${g.name}`
                }
              >
                {alias}
              </span>
            ))}
            {/* Only what the rows below do NOT already say. The provider
                names, the route count and the per-route weight all appear
                on the deployment rows, so repeating them here made the band
                a second, redundant copy of its own children. */}
            {dupIdents.length > 0 && (
              <span className="text-[11px] text-amber-400/90">
                {dupIdents.length} duplicate pair{dupIdents.length === 1 ? "" : "s"} (
                {dupIdents.join(", ")}) — weight cannot be set individually
              </span>
            )}
          </div>

          {/* The split, on its own line under the group's identity, indented
              to line up with the deployment rows below it, so the group
              reads as a heading over its own children. */}
          <div className="wl-indent mt-2.5">
            <SplitBar group={g} />
          </div>
        </td>
      </tr>

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
    </>
  );
}

// -- loading skeleton ---------------------------------------------------------

function ModelsSkeleton() {
  return (
    <div aria-hidden aria-busy="true">
      <div className="mb-5 space-y-2">
        <span className="admin-skeleton block h-7 w-28" />
        <span className="admin-skeleton block h-3 w-80 max-w-full" />
      </div>
      {/* The summary line, in the same shape as the loaded page, so the
          layout does not jump when the data arrives. */}
      <div className="mb-5 flex items-center gap-x-7 gap-y-4 border-y border-[var(--admin-border)] py-4">
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className="flex flex-col gap-2">
            <span className="admin-skeleton block h-5 w-16" />
            <span className="admin-skeleton block h-2 w-20" />
          </span>
        ))}
      </div>
      {/* The ledger's own shape: a group band, then its rows. */}
      <div className="wl-shell">
        {[0, 1, 2].map((g) => (
          <div key={g} className="wl-group border-t border-[var(--admin-border)] px-4 py-3.5">
            <span className="admin-skeleton mb-2.5 block h-4 w-52" />
            <span className="admin-skeleton block h-2 w-full max-w-[22rem]" />
            <div className="mt-3 space-y-2.5 pl-[1.125rem]">
              <span className="admin-skeleton block h-3 w-72" />
              <span className="admin-skeleton block h-3 w-60" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** One figure in the summary line: a number with a quiet label under it. */
function Figure(props: { label: string; value: string; tone?: string }) {
  return (
    <span className="flex flex-col">
      <span
        className="font-mono text-[19px] font-semibold leading-none tabular-nums tracking-[-0.02em]"
        style={{ color: props.tone ?? "var(--admin-text)" }}
      >
        {props.value}
      </span>
      <span className="mt-1.5 text-[11px] leading-none text-[var(--admin-text-dim)]">
        {props.label}
      </span>
    </span>
  );
}

export function ModelsPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const editable = user?.role === "admin";
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
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

      // A group with no deployments routes nothing, so it is not healthy —
      // same rule the filter counts use, so the count and the result agree.
      if (
        status === "healthy" &&
        !(g.deployments.length > 0 && g.deployments.every((d) => d.available))
      )
        return false;
      if (
        status === "attention" &&
        g.deployments.length > 0 &&
        g.deployments.every((d) => d.available)
      )
        return false;
      if (status === "multi" && g.deployments.length < 2) return false;

      return true;
    });

    const sorted = [...filtered];
    if (sort === "deployments") {
      sorted.sort(
        (a, b) => b.deployments.length - a.deployments.length || a.name.localeCompare(b.name),
      );
    } else if (sort === "weight") {
      const w = (g: ModelGroup) => g.deployments.reduce((s, d) => s + weightOf(d), 0);
      sorted.sort((a, b) => w(b) - w(a) || a.name.localeCompare(b.name));
    } else {
      sorted.sort((a, b) => a.name.localeCompare(b.name));
    }
    return sorted;
  }, [query.data, q, status, provider, sort]);

  // Counts for the filter rail, over ALL groups (not the search hits): the
  // counts answer "how many healthy groups exist", which is also the useful
  // number before any search text narrows the list.
  const filterCounts = useMemo<Record<StatusFilter, number>>(() => {
    const groups = query.data?.groups ?? [];
    const healthy = groups.filter(
      (g) => g.deployments.length > 0 && g.deployments.every((d) => d.available),
    ).length;
    return {
      all: groups.length,
      healthy,
      attention: groups.length - healthy,
      multi: groups.filter((g) => g.deployments.length >= 2).length,
    };
  }, [query.data]);

  if (query.isLoading) return <ModelsSkeleton />;
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

  // Provider shares for the summary line, coloured with the same hues the
  // ledger uses, so the line and the table read as one system.
  const byProviderCount = new Map<string, number>();
  for (const d of allDeployments) {
    byProviderCount.set(d.provider, (byProviderCount.get(d.provider) ?? 0) + 1);
  }
  const providerShares = [...byProviderCount.entries()].sort((a, b) => b[1] - a[1]);

  // Strategy label for the read-only (non-admin) view.
  const strategyLabel = STRATEGIES.find((s) => s.value === data.strategy)?.label ?? data.strategy;

  return (
    <div>
      <PageHeader
        title="Models"
        subtitle={
          editable
            ? "Model groups, their deployments, and how traffic splits across them."
            : "Model groups and their deployments. Read-only — ask an admin to change weights."
        }
        right={
          editable ? (
            <div className="flex items-center gap-2">
              <Select
                value={data.strategy}
                onChange={(s) => {
                  if (!data.groups.length) return;
                  // PATCH applies globally on the router settings; any group path works.
                  setStrategy.mutate(s);
                }}
                className="min-h-11"
                options={STRATEGIES}
              />
              {setStrategy.isPending && <Spinner className="h-4 w-4" />}
            </div>
          ) : (
            <span className="text-[12.5px] text-[var(--admin-text-dim)]">{strategyLabel}</span>
          )
        }
      />

      {error && (
        <div className="mb-3">
          <ErrorText>{error}</ErrorText>
        </div>
      )}

      {/* Summary: one line of figures, not four stat cards. Four cards would
          be the generic answer; the operator needs six numbers, and a single
          ruled line carries them without four boxes of chrome. */}
      <div className="mb-5 flex flex-wrap items-center gap-x-7 gap-y-4 border-y border-[var(--admin-border)] py-4">
        <Figure label="Model groups" value={fmtInt(totalGroups)} />
        <Figure label="Deployments" value={fmtInt(totalDeployments)} />
        <Figure
          label="Available"
          value={`${fmtInt(healthy)} of ${fmtInt(totalDeployments)}`}
          tone={attention === 0 ? "var(--admin-success)" : undefined}
        />
        <Figure
          label="Needs attention"
          value={fmtInt(attention)}
          tone={attention > 0 ? "var(--admin-warning)" : undefined}
        />
        <Figure
          label="Held back"
          value={fmtInt(standby)}
          tone={standby > 0 ? "var(--admin-accent)" : undefined}
        />
        {providerShares.length > 0 && (
          <span className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1.5">
            {providerShares.map(([p, n]) => (
              <span key={p} className="flex items-center gap-1.5" title={`${p}: ${n} deployment(s)`}>
                <span className="wl-hue" style={{ backgroundColor: hueOf(p) }} />
                <span className="font-mono text-[11.5px] text-[var(--admin-text-dim)]">{p}</span>
                <span
                  className="wl-num !text-[11.5px]"
                  style={{ color: "var(--admin-text-muted)" }}
                >
                  {n}
                </span>
              </span>
            ))}
          </span>
        )}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
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
            placeholder="Search groups, providers, model ids, aliases"
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
        <Select value={sort} onChange={setSort} className="min-h-11" options={SORTS} />
        {hasFilters && (
          <button
            type="button"
            onClick={() => {
              setQ("");
              setStatus("all");
              setProvider("");
            }}
            className="flex min-h-11 items-center gap-1.5 rounded-lg px-2 text-[12.5px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
          >
            <X size={13} aria-hidden /> Clear filters
          </button>
        )}
      </div>

      {/* Filter rail: quiet text choices with an underline on the active one. */}
      <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-[var(--admin-border)]">
        {STATUS_FILTERS.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={status === o.value}
            onClick={() => setStatus(o.value)}
            className="wl-filter focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
          >
            {o.label}
            <span className="ml-1.5 font-mono text-[11px] tabular-nums opacity-60">
              {fmtInt(filterCounts[o.value] ?? 0)}
            </span>
          </button>
        ))}
        <span
          className="ml-auto py-2 font-mono text-[11px] tabular-nums text-[var(--admin-text-muted)]"
          aria-live="polite"
        >
          {fmtInt(visible.length)} of {fmtInt(totalGroups)} groups
        </span>
      </div>

      {visible.length === 0 ? (
        <div className="wl-shell">
          {hasFilters ? (
            <EmptyState icon={Search} title="No groups match these filters.">
              Try a shorter search term, a different provider, or the All groups filter.
            </EmptyState>
          ) : (
            <EmptyState icon={Layers} title="No model groups configured.">
              Groups appear here once a model_list entry exists in this gateway&apos;s config,
              or once an admin creates one.
            </EmptyState>
          )}
        </div>
      ) : (
        <div className="wl-shell">
          <table className="wl-table w-full text-left">
            <thead className="wl-head">
              <tr>
                {/* The first header spans the group's own band, so it names
                    the route column only — the group sits above it, not in
                    it. */}
                <th colSpan={2}>Route</th>
                <th className="wl-plain wl-right">p95</th>
                <th className="wl-right">In flight</th>
                <th className="wl-right">Weight</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((g) => (
                <GroupRows
                  key={g.name}
                  g={g}
                  aliases={data.aliases}
                  editable={editable}
                  pendingIdent={pendingIdent}
                  onSave={save}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
