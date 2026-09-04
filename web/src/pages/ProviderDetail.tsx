// Provider detail — opened from an "Edit" action on a Providers bento card.
// Three panes: key pool (enable/disable, weights), bulk multi-key add, and
// model IDs fetched live from the upstream with select-and-attach to a group.

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Boxes, Briefcase, CheckCircle2, ChevronDown, ChevronUp, Cloud, Cpu, Eye, EyeOff, Globe, Layers, Link2, Loader2, LogIn, Plus, RefreshCw, Search, Server, Sparkles, Trash2, Unlink, X, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  addDeployment,
  clineConnect,
  clineDisconnect,
  clineLoginUrl,
  clineRefresh,
  clineStatus,
  deleteProvider,
  deleteProviderKey,
  deleteDeployment,
  fetchClineModels,
  fetchProviderModels,
  getModels,
  getProviders,
  patchProvider,
  patchProviderKey,
  addProviderKey,
  updateAliases,
  revealProviderKey,
} from "@/api/client";
import type { ModelAliasEntry, PoolKey, Provider } from "@/api/types";
import {
  aliasDisplayName,
  aliasForceMapping,
  aliasTarget,
} from "@/api/types";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorText,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Table,
  TD,
  Toggle,
} from "@/components/ui";

const STATUS_TONE: Record<PoolKey["status"], "green" | "amber" | "red" | "gray"> = {
  active: "green",
  cooling: "amber",
  invalid: "red",
  disabled: "gray",
};

const PROVIDER_ICON: Record<string, LucideIcon> = {
  openai: Sparkles,
  anthropic: Boxes,
  gemini: Zap,
  openrouter: Globe,
  "nvidia-nim": Cpu,
  "openai-compatible": Server,
  gmicloud: Cloud,
  bai: Globe,
  workbuddy: Briefcase,
  cline: Link2,
  opencode: Layers,
};

const PROVIDER_TYPE_OPTIONS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
  { value: "nvidia-nim", label: "NVIDIA NIM" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "gmicloud", label: "GMI Cloud" },
  { value: "bai", label: "B.AI" },
  { value: "workbuddy", label: "WorkBuddy" },
  { value: "cline", label: "Cline" },
  { value: "opencode", label: "OpenCode Zen" },
  { value: "openai-compatible", label: "OpenAI-compatible URL" },
];

function providerIcon(type: string): LucideIcon {
  return PROVIDER_ICON[type] ?? Server;
}

const STATUS_DOT: Record<PoolKey["status"], string> = {
  active: "bg-emerald-400",
  cooling: "bg-amber-400",
  invalid: "bg-red-400",
  disabled: "bg-zinc-600",
};

// -- key pool ------------------------------------------------------------------

function KeyRow(props: { provider: string; k: PoolKey; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [editingWeight, setEditingWeight] = useState(false);
  const [weight, setWeight] = useState(String(props.k.weight));
  const [revealed, setRevealed] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);

  const reveal = useMutation({
    mutationFn: () => revealProviderKey(props.provider, props.k.label),
    onSuccess: (r) => { setSecret(r.secret); setRevealed(true); },
    onError: (e) => props.onError(e.message),
  });

  const toggleReveal = () => {
    if (revealed) {
      setRevealed(false);
      return;
    }
    if (secret !== null) setRevealed(true);
    else reveal.mutate();
  };

  const patch = useMutation({
    mutationFn: (p: { enabled?: boolean; weight?: number }) =>
      patchProviderKey(props.provider, props.k.label, p),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["providers"] }),
    onError: (e) => props.onError(e.message),
  });

  const del = useMutation({
    mutationFn: () => deleteProviderKey(props.provider, props.k.label),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["providers"] }),
    onError: (e) => props.onError(e.message),
  });

  return (
    <tr className="group">
      <TD className="font-medium">
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[props.k.status]}`} />
          {props.k.label}
        </div>
      </TD>
      <TD className="font-mono text-[12px] text-[var(--admin-text-dim)]">
        <div className="flex items-center gap-1.5">
          <span className={revealed ? "break-all" : ""}>
            {revealed ? (secret ?? props.k.masked) : props.k.masked}
          </span>
          <button
            type="button"
            onClick={toggleReveal}
            disabled={reveal.isPending}
            className="shrink-0 rounded p-1 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text-muted)] disabled:opacity-50"
            title={revealed ? "Hide key" : "Reveal key"}
            aria-label={revealed ? "Hide key" : "Reveal key"}
          >
            {reveal.isPending ? <Loader2 size={12} className="animate-spin" /> : revealed ? <EyeOff size={12} /> : <Eye size={12} />}
          </button>
        </div>
      </TD>
      <TD>
        {editingWeight ? (
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              const w = parseInt(weight, 10);
              if (Number.isFinite(w) && w >= 1) patch.mutate({ weight: w });
              setEditingWeight(false);
            }}
          >
            <Input
              className="h-auto w-16 text-[12px]"
              type="number"
              min={1}
              value={weight}
              autoFocus
              onChange={(e) => setWeight(e.target.value)}
              onBlur={() => setEditingWeight(false)}
            />
          </form>
        ) : (
          <button
            className="rounded px-1.5 py-0.5 font-mono tabular-nums text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
            title="Click to edit weight"
            onClick={() => setEditingWeight(true)}
          >
            {props.k.weight}
          </button>
        )}
      </TD>
      <TD>
        <Badge tone={STATUS_TONE[props.k.status]}>{props.k.status}</Badge>
      </TD>
      <TD>
        <Button variant="outline" disabled={patch.isPending}
                onClick={() => patch.mutate({ enabled: !props.k.enabled })}>
          {props.k.enabled ? "Disable" : "Enable"}
        </Button>
      </TD>
      <TD>
        <Button
          variant="danger"
          aria-label="Delete key"
          disabled={del.isPending}
          onClick={() => {
            if (window.confirm(`Delete key "${props.k.label}"? This cannot be undone.`)) {
              del.mutate();
            }
          }}
        >
          <Trash2 size={14} />
        </Button>
      </TD>
    </tr>
  );
}

function KeyPoolCard(props: { p: Provider; providerName: string; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [roundRobin, setRoundRobin] = useState(props.p.round_robin);

  const toggleRR = useMutation({
    mutationFn: () => patchProvider(props.providerName, { round_robin: !roundRobin }),
    onSuccess: () => {
      setRoundRobin((v) => !v);
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  return (
    <Card className="xl:col-span-3">
      <CardHeader
        title="Key pool"
        right={
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-[var(--admin-text-dim)]">
                {roundRobin ? "round-robin" : "sequential"}
              </span>
              <Toggle
                checked={roundRobin}
                onChange={() => toggleRR.mutate()}
                disabled={toggleRR.isPending}
              />
            </div>
            <Badge tone="blue">{props.p.keys.length} keys</Badge>
          </div>
        }
      />
      {props.p.keys.length === 0 ? (
        <EmptyState>No keys yet — add some below.</EmptyState>
      ) : (
        <Table head={["Label", "Key", "Weight", "Status", "Action"]}>
          {props.p.keys.map((k) => (
            <KeyRow key={k.label} provider={props.providerName} k={k} onError={props.onError} />
          ))}
        </Table>
      )}
    </Card>
  );
}

// -- bulk key add ----------------------------------------------------------------

interface KeyDraftRow {
  label: string;
  secret: string;
  weight: string;
}

function emptyRow(): KeyDraftRow {
  return { label: "", secret: "", weight: "1" };
}

function AddKeysCard(props: { provider: string; existing: number; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [rows, setRows] = useState<KeyDraftRow[]>([emptyRow()]);

  const save = useMutation({
    mutationFn: async () => {
      // Sequential keeps deterministic pool order and stops at the first bad row.
      for (const r of rows) {
        await addProviderKey(props.provider, {
          label: r.label.trim(),
          key: r.secret.trim(),
          weight: Math.max(1, parseInt(r.weight, 10) || 1),
        });
      }
    },
    onSuccess: () => {
      setRows([emptyRow()]);
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const valid = rows.every((r) => r.label.trim() && r.secret.trim());

  const update = (i: number, patch: Partial<KeyDraftRow>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <Card>
      <CardHeader title="Add API keys" subtitle="Paste several at once — one row per key." />
      <div className="space-y-2 px-4 pb-4 pt-1">
        {rows.map((r, i) => (
          <div key={i} className="flex items-start gap-2">
            <div className="flex-1 space-y-2">
              <Input
                value={r.label}
                placeholder={`Label (key-${props.existing + i + 1})`}
                onChange={(e) => update(i, { label: e.target.value })}
              />
              <Input
                value={r.secret}
                type="password"
                autoComplete="off"
                placeholder="sk-…"
                onChange={(e) => update(i, { secret: e.target.value })}
              />
            </div>
            <Input
              className="w-16"
              type="number"
              min={1}
              value={r.weight}
              title="Weight"
              onChange={(e) => update(i, { weight: e.target.value })}
            />
            <Button
              variant="ghost"
              aria-label="Remove row"
              disabled={rows.length === 1}
              onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
            >
              <X size={14} />
            </Button>
          </div>
        ))}
        <div className="flex items-center justify-between pt-1">
          <Button variant="outline" onClick={() => setRows((rs) => [...rs, emptyRow()])}>
            <Plus size={14} /> Another key
          </Button>
          <Button
            disabled={!valid || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : `Add ${rows.length} key${rows.length > 1 ? "s" : ""}`}
          </Button>
        </div>
        {save.error && <ErrorText>{save.error.message}</ErrorText>}
        <p className="text-[11px] text-[var(--admin-text-dim)]">
          Keys are stored server-side only; masked afterwards.
        </p>
      </div>
    </Card>
  );
}

// -- upstream model fetch + select + attach ----------------------------------------

function ModelPickerCard(props: { p: Provider; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [models, setModels] = useState<string[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [weight, setWeight] = useState("1");
  const [customId, setCustomId] = useState("");
  const [customError, setCustomError] = useState<string | null>(null);

  const groupsQuery = useQuery({ queryKey: ["model-groups"], queryFn: getModels });

  const deployedHere = useMemo(() => {
    const s = new Set<string>();
    for (const g of groupsQuery.data?.groups ?? []) {
      for (const d of g.deployments) {
        if (d.provider === props.p.name) s.add(d.model_id);
      }
    }
    return s;
  }, [groupsQuery.data, props.p.name]);

  const fetchM = useMutation({
    mutationFn: () =>
      props.p.provider_type === "cline"
        ? fetchClineModels()
        : fetchProviderModels(props.p.name),
    onSuccess: (d) => {
      setModels(d.models.map((m) => m.id));
      setSelected([]);
      setFilter("");
    },
    onError: (e) => props.onError(e.message),
  });

  const attach = useMutation({
    mutationFn: async () => {
      for (const mid of selected) {
        await addDeployment(mid, {
          provider: props.p.name,
          model_id: mid,
          weight: Math.max(1, parseInt(weight, 10) || 1),
        });
      }
    },
    onSuccess: () => {
      setSelected([]);
      setModels(null);
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
    },
    onError: (e) => props.onError(e.message),
  });

  // Hand-typed model id: for upstreams whose /models listing is incomplete or
  // behind a key that can't list, and for ids that don't exist in it at all.
  const addCustom = useMutation({
    mutationFn: () => {
      const mid = customId.trim();
      if (!mid) throw new Error("enter a model id");
      if (/\s/.test(mid)) throw new Error("model id cannot contain spaces");
      return addDeployment(mid, {
        provider: props.p.name,
        model_id: mid,
        weight: Math.max(1, parseInt(weight, 10) || 1),
      });
    },
    onSuccess: () => {
      setCustomId("");
      setCustomError(null);
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
    },
    onError: (e) => setCustomError(e.message),
  });

  const visible = (models ?? []).filter((m) => m.toLowerCase().includes(filter.toLowerCase()));
  const toggle = (mid: string) =>
    setSelected((sel) => (sel.includes(mid) ? sel.filter((x) => x !== mid) : [...sel, mid]));
  return (
    <Card>
      <CardHeader
        title="Model IDs"
        subtitle={
          props.p.provider_type === "cline"
            ? "Global Cline model list (shared across all Cline accounts)."
            : "Fetch what this account can serve, pick, apply."
        }
        right={
          <Button
            variant="outline"
            disabled={fetchM.isPending}
            onClick={() => fetchM.mutate()}
          >
            <RefreshCw size={14} /> {fetchM.isPending ? "Fetching…" : "Fetch"}
          </Button>
        }
      />
      <div className="space-y-3 px-4 pb-4 pt-1">
        {/* Add by id, without needing a successful upstream fetch first. */}
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            addCustom.mutate();
          }}
        >
          <div className="flex items-start gap-2">
            <Input
              value={customId}
              placeholder="Add a model id (e.g. gpt-4o, z-ai/glm-5.2)"
              aria-label="Model id"
              onChange={(e) => {
                setCustomId(e.target.value);
                setCustomError(null);
              }}
            />
            <div className="w-20">
              <Field label="Weight">
                <Input type="number" min={1} value={weight}
                       onChange={(e) => setWeight(e.target.value)} />
              </Field>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-[var(--admin-text-dim)]">
              Becomes its own model group, named after the id.
            </span>
            <Button
              type="submit"
              disabled={!customId.trim() || addCustom.isPending}
            >
              <Plus size={14} /> {addCustom.isPending ? "Adding…" : "Add model"}
            </Button>
          </div>
          {customError && <ErrorText>{customError}</ErrorText>}
        </form>

        <div className="flex items-center gap-2">
          <span className="h-px flex-1 bg-white/[0.06]" />
          <span className="text-[11px] text-[var(--admin-text-dim)]">or pick from upstream</span>
          <span className="h-px flex-1 bg-white/[0.06]" />
        </div>

        {fetchM.error && <ErrorText>{fetchM.error.message}</ErrorText>}

        {models !== null && (
          <>
            {models.length === 0 ? (
              <EmptyState>No models returned{props.p.provider_type === "cline" ? " by any Cline account" : " by this account"}.</EmptyState>
            ) : (
              <>
                <div className="relative">
                  <Search size={13} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-[var(--admin-text-dim)]" />
                  <Input
                    className="pl-7"
                    value={filter}
                    placeholder="Filter models…"
                    onChange={(e) => setFilter(e.target.value)}
                  />
                </div>
                <div className="max-h-56 space-y-0.5 overflow-y-auto rounded-lg border border-white/[0.04] p-1">
                  {visible.map((mid) => {
                    const on = selected.includes(mid);
                    return (
                      <button
                        key={mid}
                        type="button"
                        onClick={() => toggle(mid)}
                        className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left font-mono text-[12px] transition-colors ${
                          on
                            ? "bg-blue-500/15 text-blue-200"
                            : "hover:bg-white/[0.03] text-[var(--admin-text-dim)]"
                        }`}
                      >
                        <span className="truncate">{mid}</span>
                        {deployedHere.has(mid) && <Badge tone="green">deployed</Badge>}
                      </button>
                    );
                  })}
                  {visible.length === 0 && (
                    <p className="px-2 py-3 text-[12px] text-[var(--admin-text-dim)]">No matches.</p>
                  )}
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-[var(--admin-text-dim)]">
                    {selected.length} selected{selected.length > 1 ? " — added as fallback deployments" : ""} · weight {Math.max(1, parseInt(weight, 10) || 1)}
                  </span>
                  <Button
                    disabled={selected.length === 0 || attach.isPending}
                    onClick={() => attach.mutate()}
                  >
                    {attach.isPending ? "Adding…" : `Apply ${selected.length || ""} model${selected.length > 1 ? "s" : ""}`}
                  </Button>
                </div>
                {attach.error && <ErrorText>{attach.error.message}</ErrorText>}
              </>
            )}
          </>
        )}

        {models === null && !fetchM.isPending && (
          <EmptyState>Click “Fetch” to pull model IDs from {props.p.base_url || "the upstream"}.</EmptyState>
        )}
      </div>
    </Card>
  );
}

// -- deployments referencing this account ---------------------------------------------

function DeploymentsCard(props: { provider: string; onError: (m: string) => void }) {
  const q = useQuery({ queryKey: ["model-groups"], queryFn: getModels });
  const qc = useQueryClient();
  const [pending, setPending] = useState<{ group: string; model_id: string } | null>(null);

  const detach = useMutation({
    mutationFn: (t: { group: string; model_id: string }) =>
      deleteDeployment(t.group, props.provider, t.model_id),
    onSuccess: () => {
      setPending(null);
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
    },
    onError: (e) => props.onError(e.message),
  });

  // -- per-group alias editor (model_group_alias, the `ox-alpha → stealth/...`
  //    style rewrites). The aliases map arrives in `q.data.aliases` from the
  //    existing /admin/models call (ModelsResponse.aliases). State is held
  //    per-group: `openGroup` controls which row is expanded; `draft` is the
  //    new/edit alias key → target for that group; `removed` is the keys the
  //    admin wants to delete. A single Apply submits the batch via POST
  //    /admin/aliases (atomic validate-then-mutate on the backend).
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  // Rich-form draft: a value may be a plain string (target only) or a
  // ModelAliasEntry dict (target + force_mapping + display_name + fork).
  // ForceMapping defaults to true on the backend; we keep the explicit flag
  // in the staged payload only when the user has toggled it off, so existing
  // str-only aliases stay str-only on the wire.
  const [draft, setDraft] = useState<Record<string, string | ModelAliasEntry>>({});
  const [removed, setRemoved] = useState<string[]>([]);
  const [newAliasInput, setNewAliasInput] = useState("");
  // Per-new-alias options for the add-row (force_mapping + display_name).
  // Reset on submit / on input change.
  const [newForceMapping, setNewForceMapping] = useState(true);
  const [newDisplayName, setNewDisplayName] = useState("");

  const aliasUpdate = useMutation({
    mutationFn: () => updateAliases({ set: draft, unset: removed }),
    onSuccess: () => {
      setOpenGroup(null);
      setDraft({});
      setRemoved([]);
      setNewAliasInput("");
      setNewForceMapping(true);
      setNewDisplayName("");
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const openGroupAliases = useMemo(() => {
    if (!openGroup || !q.data) return [] as [string, string | ModelAliasEntry][];
    return Object.entries(q.data.aliases).filter(
      ([, v]) => aliasTarget(v) === openGroup,
    );
  }, [openGroup, q.data]);
  // Dirty gate: don't allow Apply unless there's at least one new or removed
  // alias staged for the currently-open group.
  const dirty = Object.keys(draft).length > 0 || removed.length > 0;

  // Build the rich payload for a new alias: defaults to a plain string when
  // nothing differentiates from the legacy shape; emits a dict form only
  // when force_mapping is off or display_name is set.
  const buildNewAlias = (target: string): string | ModelAliasEntry => {
    if (newForceMapping && !newDisplayName.trim()) return target;
    return {
      target,
      force_mapping: newForceMapping,
      ...(newDisplayName.trim() ? { display_name: newDisplayName.trim() } : {}),
    };
  };

  const rows = useMemo(
    () =>
      (q.data?.groups ?? []).flatMap((g) =>
        g.deployments
          .filter((d) => d.provider === props.provider)
          .map((d) => ({ ...d, group: g.name })),
      ),
    [q.data, props.provider],
  );
  // Cross-provider pool hint: for every model_id this account serves, find
  // other providers that also serve the same model_id under the same group.
  const poolPeers = useMemo(() => {
    if (!q.data) return [];
    const mineById = new Map<string, Set<string>>();
    for (const g of q.data.groups) {
      for (const d of g.deployments) {
        if (d.provider === props.provider) {
          if (!mineById.has(d.model_id)) mineById.set(d.model_id, new Set());
          mineById.get(d.model_id)!.add(g.name);
        }
      }
    }
    const peers: Array<{ model_id: string; group: string; others: string[] }> = [];
    for (const g of q.data.groups) {
      for (const d of g.deployments) {
        if (d.provider === props.provider) continue;
        if (mineById.has(d.model_id)) {
          for (const gname of mineById.get(d.model_id)!) {
            if (gname === g.name) {
              peers.push({ model_id: d.model_id, group: g.name,
                            others: [d.provider] });
            }
          }
        }
      }
    }
    // de-dupe per (group, model_id) and merge provider names
    const merged = new Map<string, { model_id: string; group: string; others: Set<string> }>();
    for (const p of peers) {
      const k = `${p.group}::${p.model_id}`;
      if (!merged.has(k)) merged.set(k, { model_id: p.model_id, group: p.group, others: new Set() });
      merged.get(k)!.others.add(p.others[0]);
    }
    return Array.from(merged.values());
  }, [q.data, props.provider]);

  return (
    <Card>
      <CardHeader title="Models" right={<Badge tone="blue">{rows.length}</Badge>} />
      {q.isLoading ? (
        <Spinner />
      ) : rows.length === 0 ? (
        <EmptyState>Not referenced by any model group yet.</EmptyState>
      ) : (
        <>
          {poolPeers.length > 0 && (
            <div className="mx-4 mt-3 rounded-lg border border-blue-500/15 bg-blue-500/[0.04] px-3 py-2 text-[11px] text-blue-200">
              <div className="font-semibold uppercase tracking-wide text-blue-300">
                Cross-provider pool active
              </div>
              <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
                {poolPeers.map((p) => (
                  <li key={`${p.group}::${p.model_id}`}>
                    {p.group}/{p.model_id} — shares WRR with {Array.from(p.others).join(", ")}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <Table head={["Model", "Model ID", "Weight", "Ready", ""]}>
            {(() => {
              const seen = new Set<string>();
              return rows.map((d) => {
                const isFirst = !seen.has(d.group);
                const expanded = openGroup === d.group;
                if (isFirst) seen.add(d.group);
                return (
                  <Fragment key={`${d.group}/${d.model_id}`}>
                    <tr>
                      <TD className="font-medium">{d.group}</TD>
                      <TD className="font-mono text-[12px]">{d.model_id}</TD>
                      <TD className="tabular-nums">{d.weight}</TD>
                      <TD><Badge tone={d.available ? "green" : "amber"}>{d.available ? "yes" : "cooldown"}</Badge></TD>
                      <TD>
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost"
                            title={expanded ? `Hide aliases for ${d.group}` : `Show aliases for ${d.group}`}
                            aria-label={expanded ? `Hide aliases for ${d.group}` : `Show aliases for ${d.group}`}
                            aria-expanded={expanded}
                            onClick={() => {
                              setOpenGroup(expanded ? null : d.group);
                              setDraft({});
                              setRemoved([]);
                              setNewAliasInput("");
                            }}
                          >
                            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                          </Button>
                          <Button
                            variant="ghost"
                            title={`Detach ${d.model_id} from ${d.group}`}
                            aria-label={`Detach ${d.model_id}`}
                            onClick={() => setPending({ group: d.group, model_id: d.model_id })}
                          >
                            <Unlink size={14} />
                          </Button>
                        </div>
                      </TD>
                    </tr>
                    {expanded && isFirst && (
                      <tr>
                        <td colSpan={5} className="bg-[var(--admin-bg-raised)] px-4 py-3">
                          <div className="space-y-3">
                            <div>
                              <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--admin-text-dim)]">
                                Aliases pointing to {d.group}
                              </div>
                              {openGroupAliases.length === 0 ? (
                                <p className="text-[12px] text-[var(--admin-text-dim)]">None yet.</p>
                              ) : (
                                <div className="flex flex-wrap gap-2">
                                  {openGroupAliases.map(([k, v]) => {
                                    const isRemoved = removed.includes(k);
                                    const fm = aliasForceMapping(v);
                                    const dn = aliasDisplayName(v);
                                    return (
                                      <span
                                        key={k}
                                        title={
                                          dn
                                            ? `${dn} (force_mapping=${String(fm)})`
                                            : `force_mapping=${String(fm)}`
                                        }
                                        className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-[11px] ${
                                          isRemoved
                                            ? "border-red-500/30 bg-red-500/10 text-red-300 line-through"
                                            : "border-blue-500/30 bg-blue-500/10 text-blue-200"
                                        }`}
                                      >
                                        {k}
                                        {dn && (
                                          <span className="text-[var(--admin-text-dim)]">
                                            · {dn}
                                          </span>
                                        )}
                                        {!fm && (
                                          <span className="rounded bg-amber-500/20 px-1 text-[10px] text-amber-200">
                                            real
                                          </span>
                                        )}
                                        <button
                                          type="button"
                                          className="ml-1 inline-flex items-center text-[var(--admin-text-dim)] hover:text-red-300"
                                          aria-label={isRemoved ? `Restore alias ${k}` : `Remove alias ${k}`}
                                          onClick={() =>
                                            setRemoved((rs) =>
                                              isRemoved ? rs.filter((x) => x !== k) : [...rs, k],
                                            )
                                          }
                                        >
                                          <X size={11} />
                                        </button>
                                      </span>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                            <div>
                              <div className="mb-1 text-[11px] uppercase tracking-wide text-[var(--admin-text-dim)]">
                                Add alias
                              </div>
                              <div className="flex flex-wrap items-center gap-2">
                                <Input
                                  className="min-w-[10rem] flex-1 font-mono"
                                  value={newAliasInput}
                                  placeholder="new-alias-name"
                                  aria-label="New alias name"
                                  onChange={(e) => setNewAliasInput(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter" && newAliasInput.trim()) {
                                      e.preventDefault();
                                      const v = buildNewAlias(d.group);
                                      setDraft((dr) => ({ ...dr, [newAliasInput.trim()]: v }));
                                      setNewAliasInput("");
                                    }
                                  }}
                                />
                                <Input
                                  className="min-w-[8rem] flex-1"
                                  value={newDisplayName}
                                  placeholder="display name (optional)"
                                  aria-label="New alias display name"
                                  onChange={(e) => setNewDisplayName(e.target.value)}
                                />
                                <label
                                  className="flex items-center gap-1 text-[11px] text-[var(--admin-text-dim)]"
                                  title="When checked, the response model field echoes the alias. Uncheck to reveal the resolved group instead."
                                >
                                  <input
                                    type="checkbox"
                                    checked={newForceMapping}
                                    onChange={(e) => setNewForceMapping(e.target.checked)}
                                  />
                                  Force mapping
                                </label>
                                <Button
                                  variant="outline"
                                  type="button"
                                  disabled={!newAliasInput.trim()}
                                  onClick={() => {
                                    const v = buildNewAlias(d.group);
                                    setDraft((dr) => ({ ...dr, [newAliasInput.trim()]: v }));
                                    setNewAliasInput("");
                                  }}
                                >
                                  <Plus size={14} /> Add
                                </Button>
                              </div>
                              {Object.keys(draft).length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {Object.entries(draft).map(([k, v]) => {
                                    const target = aliasTarget(v);
                                    const fm = aliasForceMapping(v);
                                    const dn = aliasDisplayName(v);
                                    return (
                                      <span
                                        key={k}
                                        className="inline-flex items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 font-mono text-[11px] text-emerald-200"
                                      >
                                        {k} → {target}
                                        {dn && (
                                          <span className="text-[var(--admin-text-dim)]">
                                            · {dn}
                                          </span>
                                        )}
                                        {!fm && (
                                          <span className="rounded bg-amber-500/20 px-1 text-[10px] text-amber-200">
                                            real
                                          </span>
                                        )}
                                        <button
                                          type="button"
                                          className="ml-1 inline-flex items-center text-[var(--admin-text-dim)] hover:text-red-300"
                                          aria-label={`Discard staged alias ${k}`}
                                          onClick={() =>
                                            setDraft((dr) => {
                                              const c = { ...dr };
                                              delete c[k];
                                              return c;
                                            })
                                          }
                                        >
                                          <X size={11} />
                                        </button>
                                      </span>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                            {aliasUpdate.error && (
                              <ErrorText>{aliasUpdate.error.message}</ErrorText>
                            )}
                            <div className="flex items-center justify-end gap-2 border-t border-white/[0.06] pt-2">
                              <Button
                                variant="ghost"
                                type="button"
                                disabled={aliasUpdate.isPending}
                                onClick={() => {
                                  setDraft({});
                                  setRemoved([]);
                                  setNewAliasInput("");
                                }}
                              >
                                Reset
                              </Button>
                              <Button
                                disabled={!dirty || aliasUpdate.isPending}
                                onClick={() => aliasUpdate.mutate()}
                              >
                                {aliasUpdate.isPending ? "Applying…" : "Apply"}
                              </Button>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              });
            })()}
          </Table>
        </>
      )}
      <Dialog
        open={pending !== null}
        title={pending ? `Detach ${pending.model_id}?` : ""}
        onClose={() => setPending(null)}
      >
        <p className="text-[13px] text-[var(--admin-text-muted)]">
          Removes this deployment from the{" "}
          <span className="font-mono">{pending?.group}</span> model group. Requests
          for that model will stop routing to this account.
          {pending && rows.filter((r) => r.group === pending.group).length === 1 &&
            " This is the only deployment, so the group disappears too."}
        </p>
        {detach.error && (
          <div className="mt-3"><ErrorText>{detach.error.message}</ErrorText></div>
        )}
        <div className="flex justify-end gap-2 pt-4">
          <Button variant="ghost" type="button" onClick={() => setPending(null)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={detach.isPending}
            onClick={() => pending && detach.mutate(pending)}
          >
            {detach.isPending ? "Detaching…" : "Detach"}
          </Button>
        </div>
      </Dialog>
    </Card>
  );
}

// -- account settings (edit metadata) -------------------------------------------

function AccountSettingsCard(props: { p: Provider; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(props.p.name);
  const [type, setType] = useState(props.p.provider_type);
  const [baseUrl, setBaseUrl] = useState(props.p.base_url);
  const [aliasId, setAliasId] = useState(props.p.alias_id ?? "");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const patch: { name?: string; base_url?: string;
                     provider_type?: string; alias_id?: string | null } = {};
      if (name.trim() !== props.p.name) patch.name = name.trim();
      if (type !== props.p.provider_type) patch.provider_type = type;
      if (baseUrl.trim() !== props.p.base_url) patch.base_url = baseUrl.trim();
      const trimmedAlias = aliasId.trim();
      if (trimmedAlias !== (props.p.alias_id ?? "")) {
        patch.alias_id = trimmedAlias === "" ? null : trimmedAlias;
      }
      return patchProvider(props.p.name, patch);
    },
    onSuccess: (data) => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["providers"] });
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
      if (data.name !== props.p.name) {
        navigate(`/console/providers/${encodeURIComponent(data.name)}`, { replace: true });
      }
    },
    onError: (e) => setError(e.message),
  });

  const dirty =
    name.trim() !== props.p.name ||
    type !== props.p.provider_type ||
    baseUrl.trim() !== props.p.base_url ||
    aliasId.trim() !== (props.p.alias_id ?? "");

  return (
    <Card className="xl:col-span-3">
      <CardHeader title="Account settings" subtitle="Rename, change type, base URL, or alias id." />
      <div className="space-y-3 px-4 pb-4 pt-2">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
        </div>
        <Field label="Base URL" hint="Optional for openai/anthropic/gemini/openrouter/gmicloud/bai/workbuddy/nvidia-nim/cline/opencode. Required for compatible URLs.">
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" />
        </Field>
        <Field
          label="Alias id"
          hint="Optional caller-facing alias. Providers sharing an alias form a cross-provider weighted round-robin pool for any model id both serve. Leave blank to clear."
        >
          <Input
            value={aliasId}
            onChange={(e) => setAliasId(e.target.value)}
            placeholder="e.g. shared-openai"
            className="font-mono"
          />
        </Field>
        {error && <ErrorText>{error}</ErrorText>}
        <div className="flex justify-end">
          <Button disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

// -- Cline OAuth (automatic redirect + paste-code fallback) -------------------

type DetailCallbackResult =
  | { ok: true; email: string }
  | { ok: false; error: string };

/** Read the ?cline_connected / ?cline_error params left by the redirect
 * callback and clean the URL. One-shot (guarded by a ref). */
function useClineCallbackBanner(): DetailCallbackResult | null {
  const [searchParams, setSearchParams] = useSearchParams();
  const [result, setResult] = useState<DetailCallbackResult | null>(null);
  const seen = useRef(false);
  useEffect(() => {
    if (seen.current) return;
    const connected = searchParams.get("cline_connected");
    const error = searchParams.get("cline_error");
    if (connected === "1") {
      seen.current = true;
      setResult({ ok: true, email: searchParams.get("cline_email") ?? "" });
      _stripClineParams(searchParams, setSearchParams);
    } else if (error) {
      seen.current = true;
      setResult({ ok: false, error });
      _stripClineParams(searchParams, setSearchParams);
    }
  }, [searchParams, setSearchParams]);
  return result;
}

function _stripClineParams(
  searchParams: URLSearchParams,
  setSearchParams: (
    nextInit: string | URLSearchParams,
    opts?: { replace?: boolean },
  ) => void,
): void {
  const next = new URLSearchParams(searchParams);
  next.delete("cline_connected");
  next.delete("cline_email");
  next.delete("cline_error");
  next.delete("cline_provider");
  setSearchParams(next.size === 0 ? "" : next, { replace: true });
}

function ClineOAuthCard(props: { p: Provider; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [showManual, setShowManual] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const callbackResult = useClineCallbackBanner();

  // Poll status every 30s so the "needs refresh" badge stays current without
  // a manual page reload. Only poll once we've loaded the first response to
  // avoid wasting requests while disconnected.
  const statusQ = useQuery({
    queryKey: ["cline-oauth-status", props.p.name],
    queryFn: () => clineStatus(props.p.name),
    refetchInterval: 30_000,
  });

  const status = statusQ.data;
  const connected = status?.connected === true;
  const needsRefresh = status?.needs_refresh === true;
  const hasKeys = props.p.keys.length > 0;
  const qKey = ["cline-oauth-status", props.p.name];

  const returnPath = `/console/providers/${encodeURIComponent(props.p.name)}`;

  // Connect: open Cline's login page. After login, Cline shows a callback
  // page with ?code=… — the user copies that URL and pastes it below.
  // (Cline's Google OAuth ignores our callback_url and lands on its own
  // /auth/callback page, so we can't auto-redirect; the paste-code flow
  // accepts the full callback URL and handles truncated Google OAuth codes.)
  const autoConnect = useMutation({
    mutationFn: () => clineLoginUrl(`${window.location.origin}${returnPath}`),
    onSuccess: (d) => {
      setLocalError(null);
      setShowManual(true);
      window.open(d.auth_url, "_blank", "noopener,noreferrer");
    },
    onError: (e) => setLocalError(e.message),
  });

  const connect = useMutation({
    mutationFn: () => clineConnect(props.p.name, code.trim()),
    onSuccess: () => {
      setCode("");
      setShowManual(false);
      setLocalError(null);
      void qc.invalidateQueries({ queryKey: qKey });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => setLocalError(e.message),
  });

  const refresh = useMutation({
    mutationFn: () => clineRefresh(props.p.name),
    onSuccess: () => {
      setLocalError(null);
      void qc.invalidateQueries({ queryKey: qKey });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => {
      setLocalError(e.message);
      // A 401 means the refresh token is dead — force a status recheck so the
      // card flips back to the login flow.
      if (e instanceof Error && e.message.includes("re-login")) {
        void qc.invalidateQueries({ queryKey: qKey });
      }
    },
  });

  const disconnect = useMutation({
    mutationFn: () => clineDisconnect(props.p.name),
    onSuccess: () => {
      setConfirmDisconnect(false);
      setLocalError(null);
      void qc.invalidateQueries({ queryKey: qKey });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => setLocalError(e.message),
  });

  const fmtExpiry = (iso: string | null | undefined) => {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };

  return (
    <Card className="xl:col-span-3">
      <CardHeader
        title="Cline OAuth"
        subtitle="tokens live in the first key of the pool"
        right={
          statusQ.isLoading ? (
            <Badge tone="gray">…</Badge>
          ) : connected ? (
            <Badge tone={needsRefresh ? "amber" : "green"}>
              {needsRefresh ? "expires soon" : "connected"}
            </Badge>
          ) : (
            <Badge tone="gray">not connected</Badge>
          )
        }
      />
      <div className="space-y-4 px-4 pb-4 pt-2">
        {statusQ.isLoading && (
          <div className="flex justify-center py-4"><Spinner /></div>
        )}

        {/* callback result banner — one-shot after redirect flow */}
        {callbackResult && (
          <div
            className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-[12px] ${
              callbackResult.ok
                ? "border-emerald-500/20 bg-emerald-500/[0.04] text-emerald-300"
                : "border-red-500/20 bg-red-500/[0.04] text-red-300"
            }`}
          >
            {callbackResult.ok ? (
              <>
                <CheckCircle2 size={14} />
                Connected{callbackResult.email ? ` as ${callbackResult.email}` : ""}.
              </>
            ) : (
              <>
                <Link2 size={14} />
                {callbackResult.error}
              </>
            )}
          </div>
        )}

        {localError && <ErrorText>{localError}</ErrorText>}

        {!hasKeys && !statusQ.isLoading && (
          <div className="flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/[0.04] px-3 py-2 text-[12px] text-amber-300">
            No pool keys — add a key below before connecting. The access token
            replaces the secret of the first pool key.
          </div>
        )}

        {/* connection status */}
        {connected && (
          <dl className="admin-dl">
            <dt>Account</dt>
            <dd className="font-mono text-[12px]">{status?.email ?? "—"}</dd>
            <dt>Access token expires</dt>
            <dd className="font-mono text-[12px] text-[var(--admin-text-muted)]">
              {fmtExpiry(status?.expires_at)}
            </dd>
          </dl>
        )}

        {/* not connected: connect + paste section */}
        {!connected && !statusQ.isLoading && (
          <>
            <div className="space-y-2">
              <p className="text-[12px] text-[var(--admin-text-muted)]">
                Click to open Cline's login page. After signing in, copy the
                URL from your browser's address bar and paste it below.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="primary"
                  disabled={autoConnect.isPending || !hasKeys}
                  onClick={() => autoConnect.mutate()}
                >
                  {autoConnect.isPending ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <LogIn size={14} />
                  )}
                  {autoConnect.isPending ? "Opening…" : "Connect with Cline"}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => setShowManual((s) => !s)}
                >
                  {showManual ? (
                    <ChevronUp size={14} />
                  ) : (
                    <ChevronDown size={14} />
                  )}
                  Paste URL / code
                </Button>
              </div>
            </div>

            {/* collapsible paste section — accepts the full Cline callback URL or bare code */}
            {showManual && (
              <div className="space-y-2 border-t border-[var(--admin-border)] pt-3">
                <p className="text-[12px] text-[var(--admin-text-muted)]">
                  After logging in at Cline, copy the full URL from the
                  address bar (it starts with https://app.cline.bot/auth/
                  callback?…code=…) and paste it here.
                </p>
                <textarea
                  className="admin-input min-h-20 resize-none font-mono text-[12px]"
                  placeholder="https://app.cline.bot/auth/callback?…code=…"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
                <div className="flex justify-end">
                  <Button
                    disabled={!code.trim() || connect.isPending || !hasKeys}
                    onClick={() => connect.mutate()}
                  >
                    {connect.isPending ? "Connecting…" : "Connect"}
                  </Button>
                </div>
              </div>
            )}
          </>
        )}

        {/* refresh + disconnect (only when connected) */}
        {connected && (
          <div className="space-y-2 border-t border-[var(--admin-border)] pt-3">
            <p className="text-[12px] text-[var(--admin-text-muted)]">
              Refresh rotates the access token and the single-use refresh
              token. Cline tokens auto-refresh in the final 5 minutes before
              expiry; use this to force a rotation now.
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                disabled={refresh.isPending}
                onClick={() => refresh.mutate()}
              >
                <RefreshCw size={14} className={refresh.isPending ? "animate-spin" : ""} />
                {refresh.isPending ? "Refreshing…" : "Refresh token now"}
              </Button>
              <Button variant="ghost" onClick={() => void statusQ.refetch()}>
                <RefreshCw size={14} /> Recheck status
              </Button>
              <Button
                variant="danger"
                disabled={disconnect.isPending}
                onClick={() => setConfirmDisconnect(true)}
              >
                <Unlink size={14} /> Disconnect
              </Button>
            </div>
          </div>
        )}

        <Dialog
          open={confirmDisconnect}
          title={`Disconnect Cline OAuth for ${props.p.name}?`}
          onClose={() => setConfirmDisconnect(false)}
        >
          <p className="text-[13px] text-[var(--admin-text-muted)]">
            Clears the stored OAuth tokens. The pool key keeps its last access
            token until you reconnect or replace it.
          </p>
          <div className="flex justify-end gap-2 pt-4">
            <Button variant="ghost" onClick={() => setConfirmDisconnect(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={disconnect.isPending}
              onClick={() => disconnect.mutate()}
            >
              {disconnect.isPending ? "Disconnecting…" : "Disconnect"}
            </Button>
          </div>
        </Dialog>

        <p className="text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
          Cline uses WorkOS-backed OAuth with no client_id or PKCE. The
          automatic flow redirects through wiwi's own callback; the manual
          paste-code flow is a fallback. The access token replaces the
          secret of this provider's first pool key; refresh tokens are
          stored server-side and rotate on each refresh.
        </p>
      </div>
    </Card>
  );
}

// -- page -------------------------------------------------------------------------------

export function ProviderDetailPage() {
  const { name = "" } = useParams();
  const [error, setError] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["providers"], queryFn: getProviders, refetchInterval: 15_000 });

  const navigate = useNavigate();
  const [delOpen, setDelOpen] = useState(false);
  const delProvider = useMutation({
    mutationFn: () => deleteProvider(name),
    onSuccess: () => {
      setDelOpen(false);
      navigate("/console/providers");
    },
    onError: (e) => setError(e.message),
  });

  const p = q.data?.providers.find((x) => x.name === name);
  const PIcon = p ? providerIcon(p.provider_type) : Server;

  if (q.isLoading) return <Spinner />;
  if (!p) {
    return (
      <div>
        <PageHeader title={name} subtitle="Unknown provider account." />
        <Link to="/console/providers"><Button variant="outline"><ArrowLeft size={14} /> Back to providers</Button></Link>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            <Link to="/console/providers" aria-label="Back to providers"
                  className="text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]">
              <ArrowLeft size={18} />
            </Link>
            <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02]">
              <PIcon size={15} className="text-[var(--admin-text-muted)]" />
            </span>
            {p.name}
          </span>
        }
        subtitle={`${p.provider_type} · ${p.base_url || "(default endpoint)"}`}
        right={
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 rounded-lg border border-[var(--admin-border)] px-2.5 py-1.5">
              <span className="relative flex h-2 w-2">
                {p.healthy && (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
                )}
                <span className={`relative inline-flex h-2 w-2 rounded-full ${p.healthy ? "bg-emerald-400" : "bg-red-400"}`} />
              </span>
              <span className="text-[11px] text-[var(--admin-text-muted)]">
                {p.healthy ? "healthy" : "no healthy keys"}
              </span>
            </div>
            <Button variant="outline" onClick={() => void q.refetch()}>
              <RefreshCw size={14} /> Refresh
            </Button>
            <Button variant="danger" onClick={() => setDelOpen(true)}>
              <Trash2 size={14} /> Delete provider
            </Button>
          </div>
        }
      />
      {error && <div className="mb-3"><ErrorText>{error}</ErrorText></div>}
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
      <div className="admin-stagger grid grid-cols-1 gap-4 xl:grid-cols-3">
        <AccountSettingsCard p={p} onError={setError} />
        {p.provider_type === "cline" && <ClineOAuthCard p={p} onError={setError} />}
        <KeyPoolCard p={p} providerName={name} onError={setError} />
        <AddKeysCard provider={p.name} existing={p.keys.length} onError={setError} />
        <ModelPickerCard p={p} onError={setError} />
        <DeploymentsCard provider={p.name} onError={setError} />
      </div>
    </div>
  );
}
