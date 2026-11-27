// Providers — the upstream-account ledger. Same language as the Models
// routing ledger: one ruled table, not a stack of identical cards. Each row is
// an account: who it is (hue, name, type, endpoint), whether it can serve, how
// its key pool splits traffic, and what it carried in the recent log window.
// Colour is spent on two registers only: account identity (the shared
// provider hue) and state (serving / cooling / down).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  Layers,
  Plus,
  RefreshCw,
  Search,
  Server,
  Settings2,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  addProvider,
  deleteProvider,
  exportProviders,
  getProviders,
  getRequestLogs,
  importProviders,
  patchProvider,
} from "@/api/client";
import type { PoolKey, Provider, ProviderBackup, RequestLogEntry } from "@/api/types";
import {
  Button,
  Dialog,
  EmptyState,
  ErrorText,
  Field,
  Figure,
  Input,
  PageHeader,
  Select,
  Skeleton,
  Toggle,
} from "@/components/ui";
import { fmtInt, fmtPct, fmtTokens, fmtUsd } from "@/lib/format";
import { providerHue } from "@/lib/provider-hue";
import { datedFilename, downloadJson } from "@/api/backup";

const PROVIDER_TYPE_OPTIONS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
  { value: "nvidia-nim", label: "NVIDIA NIM" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "gmicloud", label: "GMI Cloud" },
  { value: "bai", label: "B.AI" },
  { value: "cline", label: "Cline" },
  { value: "opencode", label: "OpenCode Zen" },
  { value: "hermes", label: "Hermes (Nous Portal)" },
  { value: "ifm", label: "IFM (MBZUAI)" },
  { value: "openai-compatible", label: "OpenAI-compatible URL" },
];

const TYPE_LABEL = new Map(PROVIDER_TYPE_OPTIONS.map((o) => [o.value, o.label]));

/** Lowercased haystack for the account search box: name, provider type, base
 *  URL, routing alias id, and every key label. Callers match each
 *  whitespace-separated term against it so "openrouter or-02" narrows to the
 *  account holding that key instead of widening the result. */
function providerHaystack(p: Provider): string {
  const parts = [p.name, p.provider_type, p.base_url, p.alias_id ?? ""];
  for (const k of p.keys) parts.push(k.label);
  return parts.join(" ").toLowerCase();
}

/** A key with no credential never serves, so counting it as active would
 *  report a pool that cannot route as healthy. */
function isServingKey(k: PoolKey): boolean {
  return k.enabled && k.has_secret && k.status === "active";
}

/** What a key can do right now. A key with no secret cannot authenticate
 *  whatever its status says; it is a placeholder and reads as one. */
function keyState(k: PoolKey): PoolKey["status"] {
  return !k.has_secret || !k.enabled ? "disabled" : k.status;
}

const KEY_COLOR: Record<PoolKey["status"], string> = {
  active: "var(--admin-success)",
  cooling: "var(--admin-warning)",
  invalid: "var(--admin-danger)",
  disabled: "rgba(255, 255, 255, 0.14)",
};

// "Serving" mirrors the server's routing gate: enabled, with a healthy key.
type AccountState = "serving" | "attention" | "off";
type AccountFilter = "all" | AccountState;

function accountState(p: Provider): AccountState {
  if (!p.enabled) return "off";
  return p.healthy ? "serving" : "attention";
}

const FILTERS: { value: AccountFilter; label: string }[] = [
  { value: "all", label: "All accounts" },
  { value: "serving", label: "Serving" },
  { value: "attention", label: "Needs attention" },
  { value: "off", label: "Out of rotation" },
];

/** Status text, not a pill — the ledger's convention. */
function healthOf(p: Provider): { color: string; word: string } {
  if (!p.enabled) return { color: "var(--admin-text-dim)", word: "out of rotation" };
  if (p.healthy) return { color: "var(--admin-success)", word: "serving" };
  if (p.keys.length === 0) return { color: "var(--admin-warning)", word: "no keys" };
  return { color: "var(--admin-danger)", word: "no healthy keys" };
}

function errorTone(rate: number): string | undefined {
  if (rate > 0.1) return "var(--admin-danger)";
  if (rate > 0.03) return "var(--admin-warning)";
  return undefined;
}

// -- per-provider stats aggregation from the request-log ring -------------------

interface ProviderStats {
  requests: number;
  errors: number;
  tokIn: number;
  tokOut: number;
  tokCached: number;
  cost: number;
}

function computeProviderStats(logs: RequestLogEntry[]): Map<string, ProviderStats> {
  const m = new Map<string, ProviderStats>();
  for (const log of logs) {
    const p = log.provider;
    let s = m.get(p);
    if (!s) {
      s = { requests: 0, errors: 0, tokIn: 0, tokOut: 0, tokCached: 0, cost: 0 };
      m.set(p, s);
    }
    s.requests += 1;
    if (log.status >= 400) s.errors += 1;
    s.tokIn += log.tok_in;
    s.tokOut += log.tok_out;
    s.tokCached += log.tok_cached;
    s.cost += log.cost;
  }
  return m;
}

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50";

// One grid for the column guide and every row, so the columns cannot drift.
// Phones: checkbox | identity | actions, with the pool and metrics on their
// own lines under the identity. lg+: the full ledger.
const ROW_GRID =
  "grid grid-cols-[44px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 px-2 lg:grid-cols-[44px_minmax(0,1.4fr)_minmax(10rem,1fr)_4.5rem_4.5rem_8rem_4.5rem_7.5rem] xl:grid-cols-[44px_minmax(0,1.4fr)_minmax(12rem,1fr)_4.5rem_4.5rem_8rem_4.5rem_11rem] lg:px-3";

const HEAD =
  "text-[9.5px] font-semibold uppercase tracking-[0.14em] text-[var(--admin-text-dim)] whitespace-nowrap";

/** The key pool drawn as the traffic split it is. Round-robin segments are
 *  sized by weight (that is how the router divides requests); a sequential
 *  pool draws equal segments, because weight does not decide who serves. */
function PoolSplit(props: { p: Provider; href: string }) {
  const { p } = props;
  if (p.keys.length === 0) {
    return (
      <p className="wl-meta">
        <span style={{ color: "var(--admin-warning)" }}>Empty pool.</span>{" "}
        <Link
          to={props.href}
          className={`rounded text-[var(--admin-text-muted)] underline underline-offset-2 hover:text-[var(--admin-text)] ${FOCUS_RING}`}
        >
          Add a key
        </Link>{" "}
        before it can serve.
      </p>
    );
  }
  const share = (k: PoolKey) => (p.round_robin ? Math.max(1, k.weight) : 1);
  const total = p.keys.reduce((acc, k) => acc + share(k), 0);
  const serving = p.keys.filter(isServingKey).length;
  const cooling = p.keys.filter((k) => keyState(k) === "cooling").length;
  const invalid = p.keys.filter((k) => keyState(k) === "invalid").length;
  return (
    <div className="min-w-0">
      <div
        className="wl-split flex gap-px"
        role="img"
        aria-label={`Key pool: ${p.keys
          .map((k) => `${k.label} ${keyState(k)}, weight ${k.weight}`)
          .join("; ")}`}
      >
        {p.keys.map((k) => (
          <span
            key={k.label}
            title={`${k.label} · ${keyState(k)} · weight ${k.weight}`}
            style={{ width: `${(share(k) / total) * 100}%`, backgroundColor: KEY_COLOR[keyState(k)] }}
          />
        ))}
      </div>
      <p className="wl-meta mt-1.5">
        <span className="font-mono tabular-nums text-[var(--admin-text-muted)]">
          {serving}/{p.keys.length}
        </span>{" "}
        serving · {p.round_robin ? "round-robin" : "sequential"}
        {cooling > 0 && <span style={{ color: "var(--admin-warning)" }}>, {cooling} cooling</span>}
        {invalid > 0 && <span style={{ color: "var(--admin-danger)" }}>, {invalid} invalid</span>}
      </p>
    </div>
  );
}

function Num(props: { value: string; sub?: string; tone?: string; subTone?: string; empty?: boolean }) {
  return (
    <div className="hidden text-right lg:block">
      <div
        className="wl-num"
        style={{ color: props.empty ? "var(--admin-text-dim)" : (props.tone ?? "var(--admin-text)") }}
      >
        {props.value}
      </div>
      {props.sub && (
        <div
          className="wl-num mt-0.5 !text-[10.5px]"
          style={{ color: props.subTone ?? "var(--admin-text-dim)" }}
        >
          {props.sub}
        </div>
      )}
    </div>
  );
}

function ProviderRow(props: {
  p: Provider;
  stats: ProviderStats | undefined;
  selected: boolean;
  onToggleSelect: (name: string, on: boolean) => void;
  onError: (m: string) => void;
}) {
  const [delOpen, setDelOpen] = useState(false);
  const qc = useQueryClient();

  const delProvider = useMutation({
    mutationFn: () => deleteProvider(props.p.name),
    onSuccess: () => {
      setDelOpen(false);
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  // Account-level switch. The server gates routing on it, so the list can
  // change a provider's availability without deleting the account or keys.
  const setEnabled = useMutation({
    mutationFn: (enabled: boolean) => patchProvider(props.p.name, { enabled }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["providers"] }),
    onError: (e) => props.onError(e.message),
  });

  const p = props.p;
  const s = props.stats;
  const href = `/console/providers/${encodeURIComponent(p.name)}`;
  const health = healthOf(p);
  const errorRate = s && s.requests > 0 ? s.errors / s.requests : 0;
  const cacheRate = s && s.tokIn > 0 ? s.tokCached / s.tokIn : 0;
  const cacheTone = cacheRate > 0.1 ? "var(--admin-success)" : undefined;

  return (
    <li
      className={`${ROW_GRID} border-t border-[var(--admin-border)] py-3 transition-colors first:border-t-0 hover:bg-white/[0.016] ${
        props.selected ? "bg-blue-500/[0.05] hover:bg-blue-500/[0.07]" : ""
      }`}
    >
      <label
        className="flex h-11 w-11 cursor-pointer items-center justify-center rounded-lg"
        title={`Select ${p.name} for bulk actions`}
      >
        <input
          type="checkbox"
          className="h-4 w-4 cursor-pointer accent-blue-500"
          checked={props.selected}
          onChange={(e) => props.onToggleSelect(p.name, e.target.checked)}
          aria-label={`Select provider ${p.name}`}
        />
      </label>

      {/* identity: the account's hue, name and state on one line */}
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className="wl-hue"
            style={{ backgroundColor: providerHue(p.name), opacity: p.enabled ? 1 : 0.35 }}
            aria-hidden
          />
          <h3 className="m-0 min-w-0">
            <Link
              to={href}
              className={`wl-group-name block truncate rounded underline-offset-4 hover:underline ${FOCUS_RING} ${
                p.enabled ? "" : "!text-[var(--admin-text-muted)]"
              }`}
            >
              {p.name}
            </Link>
          </h3>
          <span className="wl-state shrink-0" style={{ color: health.color }}>
            {health.word}
          </span>
        </div>
        <p className="wl-meta mt-0.5 flex min-w-0 gap-2 pl-[18px]">
          <span className="shrink-0">{TYPE_LABEL.get(p.provider_type) ?? p.provider_type}</span>
          <span className="min-w-0 truncate font-mono" title={p.base_url || undefined}>
            {p.base_url || "default endpoint"}
          </span>
          {p.alias_id && (
            <span className="hidden shrink-0 font-mono xl:inline">alias {p.alias_id}</span>
          )}
        </p>
      </div>

      {/* actions: row one on phones, the last column from lg up */}
      <div className="flex items-center justify-end lg:order-last">
        <span className="flex h-11 w-11 items-center justify-center">
          <Toggle
            label={`Route traffic to ${p.name}`}
            checked={p.enabled}
            onChange={(v) => setEnabled.mutate(v)}
            disabled={setEnabled.isPending}
            className="after:absolute after:-inset-3 after:content-['']"
          />
        </span>
        <Link
          to={href}
          title={`Manage ${p.name}`}
          className={`admin-btn admin-btn-ghost min-h-11 min-w-11 justify-center px-2.5 lg:min-h-9 lg:min-w-9 ${FOCUS_RING}`}
        >
          <Settings2 size={14} aria-hidden />
          <span className="sr-only xl:not-sr-only">Manage</span>
        </Link>
        <Button
          variant="ghost"
          onClick={() => setDelOpen(true)}
          aria-label={`Delete provider ${p.name}`}
          title="Delete provider"
          className="min-h-11 min-w-11 justify-center px-0 text-[var(--admin-text-dim)] hover:text-red-400 lg:min-h-9 lg:min-w-9"
        >
          <Trash2 size={14} aria-hidden />
        </Button>
      </div>

      <div className="col-span-2 col-start-2 min-w-0 lg:col-span-1 lg:col-start-auto">
        <PoolSplit p={p} href={href} />
      </div>

      {/* phones: the four figures ride one quiet line under the pool */}
      <p className="wl-m col-span-2 col-start-2 m-0 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)] lg:hidden">
        {s ? (
          <>
            <span>
              <span className="text-[var(--admin-text)]">{fmtInt(s.requests)}</span> req
            </span>
            <span style={{ color: s.errors > 0 ? errorTone(errorRate) : undefined }}>
              {fmtPct(errorRate)} err
            </span>
            <span>
              <span className="text-[var(--admin-text)]">{fmtTokens(s.tokIn)}</span> →{" "}
              <span className="text-[var(--admin-text)]">{fmtTokens(s.tokOut)}</span>
            </span>
            <span className="text-[var(--admin-text)]">{fmtUsd(s.cost)}</span>
          </>
        ) : (
          <span className="font-sans">No requests in the recent log window.</span>
        )}
      </p>

      {s ? (
        <>
          <Num value={fmtInt(s.requests)} />
          <Num
            value={fmtInt(s.errors)}
            sub={fmtPct(errorRate)}
            tone={s.errors > 0 ? (errorTone(errorRate) ?? "var(--admin-text)") : undefined}
            subTone={s.errors > 0 ? errorTone(errorRate) : undefined}
          />
          <Num
            value={`${fmtTokens(s.tokIn)} → ${fmtTokens(s.tokOut)}`}
            sub={s.tokIn > 0 ? `${fmtPct(cacheRate)} cached` : undefined}
            subTone={cacheTone}
          />
          <Num value={fmtUsd(s.cost)} />
        </>
      ) : (
        <>
          <Num value="—" empty />
          <Num value="—" empty />
          <Num value="—" empty />
          <Num value="—" empty />
        </>
      )}

      <Dialog open={delOpen} title={`Delete provider ${p.name}?`} onClose={() => setDelOpen(false)}>
        <p className="text-[13px] text-[var(--admin-text-muted)]">
          This removes the account and all its keys from the live pool. If any
          model group still references it, deletion will be blocked.
        </p>
        {delProvider.error && (
          <div className="mt-3">
            <ErrorText>{delProvider.error.message}</ErrorText>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-4">
          <Button variant="ghost" type="button" onClick={() => setDelOpen(false)}>
            Cancel
          </Button>
          <Button variant="danger" disabled={delProvider.isPending} onClick={() => delProvider.mutate()}>
            Delete
          </Button>
        </div>
      </Dialog>
    </li>
  );
}

export function ProvidersPage() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [searchParams] = useSearchParams();
  const [addOpen, setAddOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("openai-compatible");
  const [baseUrl, setBaseUrl] = useState("");
  const [label, setLabel] = useState("default");
  const [secret, setSecret] = useState("");
  const [importing, setImporting] = useState(false);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<AccountFilter>("all");
  const [selectedNames, setSelectedNames] = useState<Set<string>>(() => new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Allow deep-linking from the Built-in Providers catalog: ?type=openrouter
  // pre-selects the provider type and opens the Add dialog.
  const presetType = searchParams.get("type");
  useEffect(() => {
    if (presetType && PROVIDER_TYPE_OPTIONS.some((o) => o.value === presetType)) {
      setType(presetType);
      setAddOpen(true);
    }
  }, [presetType]);

  const query = useQuery({ queryKey: ["providers"], queryFn: getProviders, refetchInterval: 15_000 });
  const logsQuery = useQuery({ queryKey: ["request-logs"], queryFn: getRequestLogs, refetchInterval: 15_000 });

  const statsByProvider = useMemo(
    () => computeProviderStats(logsQuery.data?.logs ?? []),
    [logsQuery.data],
  );

  // Case-insensitive, term-wise AND match over name/type/base URL/alias/key
  // labels. Filtering is client-side over the already-fetched pool list, so it
  // stays correct while the 15s poll refreshes in the background.
  const terms = useMemo(
    () => q.trim().toLowerCase().split(/\s+/).filter(Boolean),
    [q],
  );
  // Stable identities so the filter memo only recomputes on real data changes.
  const accounts = useMemo(() => query.data?.providers ?? [], [query.data]);
  const searched = useMemo(
    () =>
      terms.length === 0
        ? accounts
        : accounts.filter((p) => {
            const hay = providerHaystack(p);
            return terms.every((t) => hay.includes(t));
          }),
    [accounts, terms],
  );
  // Counts follow the search, so each filter says what clicking it would show.
  const filterCounts = useMemo(() => {
    const c: Record<AccountFilter, number> = { all: searched.length, serving: 0, attention: 0, off: 0 };
    for (const p of searched) c[accountState(p)] += 1;
    return c;
  }, [searched]);
  const visibleAccounts = useMemo(
    () => (filter === "all" ? searched : searched.filter((p) => accountState(p) === filter)),
    [searched, filter],
  );

  // Fleet totals for the summary band. "Serving" mirrors the server's routing
  // gate: an account must be enabled and hold at least one healthy key.
  const fleet = useMemo(() => {
    let serving = 0;
    let enabled = 0;
    let keys = 0;
    let activeKeys = 0;
    let requests = 0;
    let errors = 0;
    let cost = 0;
    for (const p of accounts) {
      if (p.enabled) enabled += 1;
      if (p.enabled && p.healthy) serving += 1;
      keys += p.keys.length;
      activeKeys += p.keys.filter(isServingKey).length;
      const s = statsByProvider.get(p.name);
      if (s) {
        requests += s.requests;
        errors += s.errors;
        cost += s.cost;
      }
    }
    return { serving, enabled, keys, activeKeys, requests, errors, cost };
  }, [accounts, statsByProvider]);

  const allVisibleSelected =
    visibleAccounts.length > 0 && visibleAccounts.every((p) => selectedNames.has(p.name));
  const someVisibleSelected = visibleAccounts.some((p) => selectedNames.has(p.name));

  // The native checkbox's "mixed" state is not a prop: set it imperatively so
  // a partial selection reads as partial instead of as unchecked.
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someVisibleSelected && !allVisibleSelected;
    }
  }, [someVisibleSelected, allVisibleSelected]);

  // Drop names that no longer exist after a delete or a background refresh, so
  // the bulk bar never offers to delete a phantom account.
  useEffect(() => {
    setSelectedNames((prev) => {
      if (prev.size === 0) return prev;
      const alive = new Set(accounts.map((p) => p.name));
      const next = new Set([...prev].filter((name) => alive.has(name)));
      return next.size === prev.size ? prev : next;
    });
  }, [accounts]);

  const toggleSelect = useCallback((name: string, on: boolean) => {
    setSelectedNames((prev) => {
      const next = new Set(prev);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });
  }, []);

  // Sequential rather than parallel: each delete rewrites routing pools and
  // tombstones, so overlapping writes would interleave on the backend. A
  // failure does not abort the rest — the operator asked for all of them and
  // the survivors stay selected.
  const bulkDelete = useMutation({
    mutationFn: async (names: string[]) => {
      const failed: { name: string; message: string }[] = [];
      for (const name of names) {
        try {
          await deleteProvider(name);
        } catch (e) {
          failed.push({ name, message: e instanceof Error ? e.message : String(e) });
        }
      }
      return { failed, attempted: names.length };
    },
    onSuccess: ({ failed, attempted }) => {
      const failedNames = new Set(failed.map((f) => f.name));
      setSelectedNames((prev) => new Set([...prev].filter((name) => !failedNames.has(name))));
      setBulkOpen(false);
      void qc.invalidateQueries({ queryKey: ["providers"] });
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
      if (failed.length > 0) {
        setNotice(null);
        setError(
          `Could not delete ${failed.length} provider${failed.length === 1 ? "" : "s"} — ` +
            failed.map((f) => `${f.name}: ${f.message}`).join("; "),
        );
      } else {
        setError(null);
        setNotice(
          `Deleted ${attempted} provider${attempted === 1 ? "" : "s"}. ` +
            "Their deployments were detached from every model group.",
        );
      }
    },
    onError: (e) => setError(e.message),
  });

  // Enable/disable every selected account. Sequential for the same reason as
  // the deletes: each write rewrites the provider row that startup layers over
  // YAML, and overlapping writes would interleave there. Partial failures keep
  // the survivors selected so a retry targets exactly what failed.
  const bulkSetEnabled = useMutation({
    mutationFn: async ({ names, enabled }: { names: string[]; enabled: boolean }) => {
      const failed: { name: string; message: string }[] = [];
      for (const name of names) {
        try {
          await patchProvider(name, { enabled });
        } catch (e) {
          failed.push({ name, message: e instanceof Error ? e.message : String(e) });
        }
      }
      return { failed, enabled, attempted: names.length };
    },
    onSuccess: ({ failed, enabled, attempted }) => {
      const failedNames = new Set(failed.map((f) => f.name));
      setSelectedNames((prev) => new Set([...prev].filter((name) => failedNames.has(name))));
      void qc.invalidateQueries({ queryKey: ["providers"] });
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
      if (failed.length > 0) {
        setNotice(null);
        setError(
          `Could not ${enabled ? "enable" : "disable"} ${failed.length} provider` +
            `${failed.length === 1 ? "" : "s"} — ` +
            failed.map((f) => `${f.name}: ${f.message}`).join("; "),
        );
      } else {
        setError(null);
        setNotice(
          `${enabled ? "Enabled" : "Disabled"} ${attempted} provider` +
            `${attempted === 1 ? "" : "s"}.`,
        );
      }
    },
    onError: (e) => setError(e.message),
  });

  const addProvider_ = useMutation({
    mutationFn: () =>
      addProvider({
        name: name.trim(),
        provider_type: type,
        base_url: baseUrl.trim() || undefined,
        label: label.trim() || "default",
        key: secret,
      }),
    onSuccess: () => {
      setAddOpen(false);
      setName("");
      setBaseUrl("");
      setSecret("");
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => setError(e.message),
  });

  const doExport = async () => {
    try {
      const data = await exportProviders();
      downloadJson(datedFilename("wiwi-providers"), data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const doImportFile = async (f: File) => {
    setImporting(true);
    setNotice(null);
    try {
      const parsed = JSON.parse(await f.text()) as
        | { providers?: ProviderBackup[] }
        | ProviderBackup[];
      const providers = Array.isArray(parsed) ? parsed : (parsed.providers ?? []);
      const res = await importProviders(providers);
      await qc.invalidateQueries({ queryKey: ["providers"] });
      setError(null);
      setNotice(
        `Imported ${res.imported_providers} provider${res.imported_providers === 1 ? "" : "s"}, ` +
          `${res.imported_keys} key${res.imported_keys === 1 ? "" : "s"}, ` +
          `${res.imported_deployments} deployment${res.imported_deployments === 1 ? "" : "s"}.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className={`admin-dash${query.isFetching && query.data ? " is-refetching" : ""}`}>
      <PageHeader
        title="Providers"
        subtitle="Upstream accounts and the key pools that serve them."
        right={
          <>
            <Link
              to="/console/builtin-providers"
              className={`admin-btn admin-btn-ghost ${FOCUS_RING}`}
            >
              <Layers size={14} aria-hidden />
              <span className="sr-only sm:not-sr-only">Catalog</span>
            </Link>
            <Button variant="outline" onClick={() => void doExport()}>
              <Download size={14} aria-hidden />
              <span className="sr-only sm:not-sr-only">Export</span>
            </Button>
            <Button
              variant="outline"
              disabled={importing}
              onClick={() => fileRef.current?.click()}
            >
              <Upload size={14} aria-hidden />
              <span className="sr-only sm:not-sr-only">{importing ? "Importing…" : "Import"}</span>
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void doImportFile(f);
                e.target.value = "";
              }}
            />
            <Button
              variant="outline"
              onClick={() => void qc.invalidateQueries({ queryKey: ["providers"] })}
              aria-label="Refresh providers"
              disabled={query.isFetching}
            >
              <RefreshCw
                size={14}
                className={query.isFetching ? "motion-safe:animate-spin" : ""}
                aria-hidden
              />
            </Button>
            <Button onClick={() => setAddOpen(true)}>
              <Plus size={14} /> Add provider
            </Button>
          </>
        }
      />
      {/* Summary: one ruled line of figures, matching the Models and Combos
          ledgers — not a band of stat boxes. "Can the fleet serve right now"
          is answered before any single account is read. */}
      {query.data && accounts.length > 0 && (
        <div className="mb-5 flex flex-wrap items-center gap-x-7 gap-y-4 border-y border-[var(--admin-border)] py-4">
          <Figure label="Accounts" value={fmtInt(accounts.length)} />
          <Figure
            label="Serving"
            value={`${fmtInt(fleet.serving)} of ${fmtInt(fleet.enabled)}`}
            tone={
              fleet.enabled === 0
                ? undefined
                : fleet.serving === fleet.enabled
                  ? "var(--admin-success)"
                  : fleet.serving > 0
                    ? "var(--admin-warning)"
                    : "var(--admin-danger)"
            }
          />
          <Figure
            label="Keys serving"
            value={`${fmtInt(fleet.activeKeys)} of ${fmtInt(fleet.keys)}`}
            tone={fleet.activeKeys < fleet.keys ? "var(--admin-warning)" : undefined}
          />
          <Figure label="Requests" value={fmtInt(fleet.requests)} />
          <Figure
            label="Error rate"
            value={fleet.requests > 0 ? fmtPct(fleet.errors / fleet.requests) : "—"}
            tone={fleet.requests > 0 ? errorTone(fleet.errors / fleet.requests) : undefined}
          />
          <Figure label="Cost" value={fmtUsd(fleet.cost)} />
          <span className="ml-auto hidden font-mono text-[11px] text-[var(--admin-text-dim)] sm:inline">
            traffic from the recent request log
          </span>
        </div>
      )}

      <div className="relative mb-2">
        <Search
          size={14}
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
        />
        <Input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search by name, type, URL, alias, or key label…"
          aria-label="Search provider accounts"
          spellCheck={false}
          autoComplete="off"
          className="min-h-11 pl-9 pr-12"
        />
        {q !== "" && (
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => setQ("")}
            className={`absolute right-1 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] ${FOCUS_RING}`}
          >
            <X size={14} />
          </button>
        )}
      </div>

      {/* Filter rail: quiet text choices with an underline on the active one. */}
      {accounts.length > 0 && (
        <div className="mb-3 flex items-center gap-x-5 overflow-x-auto whitespace-nowrap border-b border-[var(--admin-border)] [scrollbar-width:none]">
          {FILTERS.map((o) => (
            <button
              key={o.value}
              type="button"
              aria-pressed={filter === o.value}
              onClick={() => setFilter(o.value)}
              className={`wl-filter ${FOCUS_RING}`}
            >
              {o.label}
              <span className="ml-1.5 font-mono text-[11px] tabular-nums opacity-60">
                {fmtInt(filterCounts[o.value])}
              </span>
            </button>
          ))}
          <span
            className="ml-auto hidden py-2 font-mono text-[11px] tabular-nums text-[var(--admin-text-muted)] sm:block"
            aria-live="polite"
          >
            {fmtInt(visibleAccounts.length)} of {fmtInt(accounts.length)} accounts
          </span>
        </div>
      )}

      {error && (
        <div className="mb-3">
          <ErrorText>{error}</ErrorText>
        </div>
      )}
      {notice && (
        <div className="mb-3">
          <p className="rounded-[10px] border border-emerald-500/10 bg-emerald-500/[0.04] px-2.5 py-2 text-[12px] text-emerald-400">
            {notice}
          </p>
        </div>
      )}
      {query.error && <ErrorText>{query.error.message}</ErrorText>}

      {query.isLoading && (
        <div className="wl-shell" role="status" aria-busy="true" aria-label="Loading providers">
          {[0, 1, 2].map((i) => (
            <div key={i} className="border-t border-[var(--admin-border)] px-4 py-4 first:border-t-0">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="mt-2.5 h-1.5 w-full max-w-md" />
            </div>
          ))}
        </div>
      )}

      {query.data && (
        <div className="wl-shell">
          {visibleAccounts.length > 0 && (
            // Column guide. With a selection it turns into the bulk bar, so
            // the actions sit where the selection was made instead of in a
            // second toolbar that is empty most of the time.
            <div
              className={`${selectedNames.size > 0 ? "flex flex-wrap items-center gap-x-2 gap-y-1 px-2 lg:px-3" : ROW_GRID} min-h-11 border-b border-[var(--admin-border)] bg-white/[0.012] py-1`}
            >
              <label
                className="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg"
                title="Select all visible accounts"
              >
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  className="h-4 w-4 cursor-pointer accent-blue-500"
                  checked={allVisibleSelected}
                  onChange={(e) => {
                    const on = e.target.checked;
                    setSelectedNames((prev) => {
                      const next = new Set(prev);
                      for (const p of visibleAccounts) {
                        if (on) next.add(p.name);
                        else next.delete(p.name);
                      }
                      return next;
                    });
                  }}
                  aria-label="Select all visible provider accounts"
                />
              </label>
              {selectedNames.size === 0 ? (
                <>
                  <span className={HEAD}>Account</span>
                  <span className={`${HEAD} hidden lg:block`}>Key pool</span>
                  <span className={`${HEAD} hidden text-right lg:block`}>Requests</span>
                  <span className={`${HEAD} hidden text-right lg:block`}>Errors</span>
                  <span className={`${HEAD} hidden text-right lg:block`}>Tokens in → out</span>
                  <span className={`${HEAD} hidden text-right lg:block`}>Cost</span>
                  <span aria-hidden />
                </>
              ) : (
                <>
                  <span
                    className="font-mono text-[11px] tabular-nums text-[var(--admin-text)]"
                    aria-live="polite"
                  >
                    {selectedNames.size} selected
                  </span>
                  <div className="ml-auto flex flex-wrap items-center justify-end gap-1">
                    <Button
                      variant="ghost"
                      disabled={bulkSetEnabled.isPending || bulkDelete.isPending}
                      onClick={() =>
                        bulkSetEnabled.mutate({ names: [...selectedNames], enabled: true })
                      }
                    >
                      {bulkSetEnabled.isPending && bulkSetEnabled.variables?.enabled
                        ? "Enabling…"
                        : "Enable"}
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={bulkSetEnabled.isPending || bulkDelete.isPending}
                      onClick={() =>
                        bulkSetEnabled.mutate({ names: [...selectedNames], enabled: false })
                      }
                    >
                      {bulkSetEnabled.isPending && !bulkSetEnabled.variables?.enabled
                        ? "Disabling…"
                        : "Disable"}
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={bulkDelete.isPending || bulkSetEnabled.isPending}
                      onClick={() => setSelectedNames(new Set())}
                    >
                      Clear
                    </Button>
                    <Button
                      variant="danger"
                      disabled={bulkDelete.isPending || bulkSetEnabled.isPending}
                      onClick={() => setBulkOpen(true)}
                    >
                      <Trash2 size={14} aria-hidden />
                      {bulkDelete.isPending ? "Deleting…" : `Delete ${selectedNames.size}`}
                    </Button>
                  </div>
                </>
              )}
            </div>
          )}
          <ul className="admin-stagger m-0 list-none p-0">
            {visibleAccounts.map((p) => (
              <ProviderRow
                key={p.name}
                p={p}
                stats={statsByProvider.get(p.name)}
                selected={selectedNames.has(p.name)}
                onToggleSelect={toggleSelect}
                onError={setError}
              />
            ))}
          </ul>
          {accounts.length === 0 && (
            <EmptyState
              icon={Server}
              title="No provider accounts yet"
              action={
                <Button onClick={() => setAddOpen(true)}>
                  <Plus size={14} /> Add provider
                </Button>
              }
            >
              Requests cannot route until at least one upstream account holds a key.
              Add one here, or start from a preset in the catalog.
            </EmptyState>
          )}
          {accounts.length > 0 && visibleAccounts.length === 0 && (
            <EmptyState
              icon={Search}
              title="No account matches."
              action={
                <Button
                  variant="outline"
                  onClick={() => {
                    setQ("");
                    setFilter("all");
                  }}
                >
                  Clear search and filter
                </Button>
              }
            >
              {q.trim() ? `Nothing matches “${q.trim()}”` : "Nothing is in this state"}
              {filter !== "all" ? ` under ${FILTERS.find((f) => f.value === filter)?.label}.` : "."}
            </EmptyState>
          )}
        </div>
      )}

      <Dialog
        open={bulkOpen}
        title={`Delete ${selectedNames.size} provider${selectedNames.size === 1 ? "" : "s"}?`}
        onClose={() => {
          if (!bulkDelete.isPending) setBulkOpen(false);
        }}
      >
        <p className="text-[13px] text-[var(--admin-text-muted)]">
          Each account and its keys are removed from the live pool. Deployments that
          pointed at these accounts are detached from their model groups; a group left
          with no deployments disappears.
        </p>
        <ul className="mt-3 max-h-40 space-y-0.5 overflow-y-auto rounded-lg border border-[var(--admin-border)] p-2 font-mono text-[12px] text-[var(--admin-text)]">
          {[...selectedNames].sort().map((n) => (
            <li key={n} className="truncate px-1 py-0.5">
              {n}
            </li>
          ))}
        </ul>
        {bulkDelete.error && (
          <div className="mt-3">
            <ErrorText>{bulkDelete.error.message}</ErrorText>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-4">
          <Button
            variant="ghost"
            type="button"
            disabled={bulkDelete.isPending}
            onClick={() => setBulkOpen(false)}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={bulkDelete.isPending}
            onClick={() => bulkDelete.mutate([...selectedNames])}
          >
            {bulkDelete.isPending
              ? "Deleting…"
              : `Delete ${selectedNames.size} provider${selectedNames.size === 1 ? "" : "s"}`}
          </Button>
        </div>
      </Dialog>

      <Dialog open={addOpen} title="Add provider account" onClose={() => setAddOpen(false)}>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim() && secret.trim()) addProvider_.mutate();
          }}
        >
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="openai-backup" />
          </Field>
          <Field label="Type">
            <Select
              value={type}
              onChange={setType}
              options={PROVIDER_TYPE_OPTIONS}
            />
          </Field>
          <Field label="Base URL" hint="Optional for openai/anthropic/gemini/openrouter/gmicloud/bai/nvidia-nim/cline/opencode/hermes/ifm. Required for compatible URLs.">
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" />
          </Field>
          <Field label="First key label">
            <Input value={label} onChange={(e) => setLabel(e.target.value)} />
          </Field>
          <Field label="API key">
            <Input value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="sk-…" />
          </Field>
          <p className="text-[11px] text-[var(--admin-text-dim)]">
            Runtime-only until restart: point a model group at this account in wiwi.yaml to persist it.
          </p>
          {addProvider_.error && <ErrorText>{addProvider_.error.message}</ErrorText>}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || !secret.trim() || addProvider_.isPending}>
              Create
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
