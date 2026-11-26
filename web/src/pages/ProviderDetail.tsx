// Provider detail — opened from an "Edit" action on a Providers bento card.
// One identity band (name, type, endpoint, health, key-status counts), then
// section bands: keys (pool + add), models (picker + deployments), account.

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Boxes, Briefcase, CheckCircle2, ChevronDown, ChevronUp, Cloud, Cpu, Eye, EyeOff, Globe, KeyRound, Layers, Link2, Loader2, LogIn, Plus, RefreshCw, Search, Server, Settings, Sparkles, Sunrise, Trash2, Unlink, X, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  addDeployment,
  clineAutoConnect,
  clineConnect,
  clineDisconnect,
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
  testProviderKey,
} from "@/api/client";
import type { ModelAliasEntry, PoolKey, Provider } from "@/api/types";
import {
  HermesAccountList,
  useHermesAccountActions,
  useHermesStatusQuery,
} from "@/components/HermesAccounts";
import { HermesConnectDialog } from "@/pages/HermesOAuth";
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
  PageHeader,
  Field,
  Input,
  SectionHeading,
  Select,
  Skeleton,
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
  hermes: Sparkles,
  ifm: Sunrise,
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
  { value: "hermes", label: "Hermes (Nous Portal)" },
  { value: "ifm", label: "IFM (MBZUAI)" },
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

function KeyRow(props: { provider: string; k: PoolKey; models: string[]; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [editingWeight, setEditingWeight] = useState(false);
  const [weight, setWeight] = useState(String(props.k.weight));
  const [revealed, setRevealed] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [editingSecret, setEditingSecret] = useState(false);
  const [newSecret, setNewSecret] = useState("");

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

  const setSecretMutation = useMutation({
    mutationFn: (s: string) => patchProviderKey(props.provider, props.k.label, { secret: s }),
    onSuccess: () => {
      setEditingSecret(false);
      setNewSecret("");
      // The revealed plaintext is now stale.
      setSecret(null);
      setRevealed(false);
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const submitSecret = () => {
    const value = newSecret.trim();
    if (!value) {
      props.onError("A key secret cannot be empty.");
      return;
    }
    setSecretMutation.mutate(value);
  };

  const del = useMutation({
    mutationFn: () => deleteProviderKey(props.provider, props.k.label),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["providers"] }),
    onError: (e) => props.onError(e.message),
  });

  const [testing, setTesting] = useState(false);
  const [testModel, setTestModel] = useState(props.models[0] ?? "");
  const test = useMutation({
    mutationFn: (model: string) => testProviderKey(props.provider, props.k.label, model),
    onError: (e) => props.onError(e.message),
  });
  const runTest = () => {
    const m = testModel.trim();
    if (!m) {
      props.onError("Pick or type a model to test the key against.");
      return;
    }
    test.mutate(m);
  };
  const listId = `test-models-${props.k.label}`;

  return (
    <Fragment>
    <tr className="group">
      <TD className="font-medium">
        <div className="flex items-center gap-2">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              props.k.has_secret ? STATUS_DOT[props.k.status] : "bg-amber-400"
            }`}
          />
          {props.k.label}
        </div>
      </TD>
      <TD className="font-mono text-[12px] text-[var(--admin-text-dim)]">
        {editingSecret ? (
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => { e.preventDefault(); submitSecret(); }}
          >
            <Input
              className="h-auto w-44 text-[12px]"
              type="password"
              autoComplete="off"
              autoFocus
              placeholder="Paste the key"
              value={newSecret}
              onChange={(e) => setNewSecret(e.target.value)}
            />
            <Button type="submit" variant="outline" disabled={setSecretMutation.isPending}>
              {setSecretMutation.isPending ? "Saving…" : "Save"}
            </Button>
            <Button
              variant="outline"
              onClick={() => { setEditingSecret(false); setNewSecret(""); }}
            >
              Cancel
            </Button>
          </form>
        ) : (
          <div className="flex items-center gap-1.5">
            <span className={revealed ? "break-all" : ""}>
              {props.k.has_secret
                ? (revealed ? (secret ?? props.k.masked) : props.k.masked)
                : "no secret"}
            </span>
            {props.k.has_secret && (
              <button
                type="button"
                onClick={toggleReveal}
                disabled={reveal.isPending}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text-muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 sm:h-8 sm:w-8 disabled:opacity-50"
                title={revealed ? "Hide key" : "Reveal key"}
                aria-label={revealed ? "Hide key" : "Reveal key"}
              >
                {reveal.isPending ? <Loader2 size={12} className="animate-spin" /> : revealed ? <EyeOff size={12} /> : <Eye size={12} />}
              </button>
            )}
            <button
              type="button"
              onClick={() => setEditingSecret(true)}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text-muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 sm:h-8 sm:w-8"
              title={props.k.has_secret ? "Replace key" : "Set key — this key has no credential and is disabled"}
              aria-label={props.k.has_secret ? "Replace key" : "Set key"}
            >
              <KeyRound size={12} />
            </button>
          </div>
        )}
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
            className="min-h-[44px] min-w-[44px] rounded-md px-1.5 font-mono tabular-nums text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 sm:min-h-8 sm:min-w-8"
            title="Click to edit weight"
            aria-label={`Weight ${props.k.weight}, click to edit`}
            onClick={() => setEditingWeight(true)}
          >
            {props.k.weight}
          </button>
        )}
      </TD>
      <TD>
        <Badge tone={props.k.has_secret ? STATUS_TONE[props.k.status] : "amber"}>
          {props.k.has_secret ? props.k.status : "no secret"}
        </Badge>
      </TD>
      <TD>
        <div className="flex items-center justify-end gap-1">
        <Button variant="outline" disabled={patch.isPending}
                onClick={() => patch.mutate({ enabled: !props.k.enabled })}>
          {props.k.enabled ? "Disable" : "Enable"}
        </Button>
        <Button
          variant="outline"
          disabled={!props.k.has_secret}
          title={props.k.has_secret ? "Send a 1-token test request with this key" : "Set a secret first"}
          onClick={() => setTesting((v) => !v)}
        >
          <Zap size={14} /> Test
        </Button>
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
        </div>
      </TD>
    </tr>
    {testing && (
      <tr>
        <td colSpan={5} className="px-3 pb-3">
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => { e.preventDefault(); runTest(); }}
          >
            <Input
              className="min-h-[44px] w-full sm:w-72 text-[12px]"
              list={listId}
              aria-label="Model to test"
              placeholder="Model id"
              value={testModel}
              onChange={(e) => setTestModel(e.target.value)}
            />
            <datalist id={listId}>
              {props.models.map((m) => <option key={m} value={m} />)}
            </datalist>
            <Button type="submit" variant="outline" disabled={test.isPending}>
              {test.isPending ? <Loader2 size={14} className="animate-spin" /> : "Run test"}
            </Button>
            {test.data && (
              <span className="flex items-center gap-2 text-[12px]">
                <Badge tone={test.data.ok ? "green" : "red"}>{test.data.verdict}</Badge>
                <span className="text-[var(--admin-text-dim)]">
                  {test.data.status ?? "—"} · {test.data.latency_ms} ms · {test.data.model}
                </span>
                {test.data.detail && (
                  <span className="break-all font-mono text-[var(--admin-text-dim)]">{test.data.detail}</span>
                )}
              </span>
            )}
          </form>
        </td>
      </tr>
    )}
    </Fragment>
  );
}

export function KeyPoolCard(props: { p: Provider; providerName: string; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [roundRobin, setRoundRobin] = useState(props.p.round_robin);
  const groupsQuery = useQuery({ queryKey: ["model-groups"], queryFn: getModels });
  const models = useMemo(() => {
    const s = new Set<string>();
    for (const g of groupsQuery.data?.groups ?? []) {
      for (const d of g.deployments) if (d.provider === props.providerName) s.add(d.model_id);
    }
    return [...s].sort();
  }, [groupsQuery.data, props.providerName]);

  const toggleRR = useMutation({
    mutationFn: () => patchProvider(props.providerName, { round_robin: !roundRobin }),
    onSuccess: () => {
      setRoundRobin((v) => !v);
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  return (
    <Card>
      <CardHeader
        icon={KeyRound}
        title="Key pool"
        subtitle={
          roundRobin
            ? "Round-robin — each enabled key takes a turn, weighted."
            : "Sequential — the first healthy key serves until it fails."
        }
        right={
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-[12px] text-[var(--admin-text-muted)]">
                {roundRobin ? "round-robin" : "sequential"}
              </span>
              <Toggle
                label="Round-robin key selection"
                checked={roundRobin}
                onChange={() => toggleRR.mutate()}
                disabled={toggleRR.isPending}
                className="after:absolute after:-inset-3 after:content-['']"
              />
            </div>
          </div>
        }
      />
      {props.p.keys.length === 0 ? (
        <EmptyState icon={KeyRound} title="No keys in the pool">
          This account cannot serve requests until it holds at least one key. Add the first one below.
        </EmptyState>
      ) : (
        <Table head={["Label", "Key", "Weight", "Status", ""]}>
          {props.p.keys.map((k) => (
            <KeyRow key={k.label} provider={props.providerName} k={k} models={models} onError={props.onError} />
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
      <CardHeader
        icon={Plus}
        title="Add API keys"
        subtitle="One row per key. Stored server-side only; masked afterwards."
      />
      <div className="space-y-3 p-5">
        {rows.map((r, i) => (
          <div key={i} className="rounded-lg border border-[var(--dash-line)] bg-white/[0.01] p-3">
            {/* Row header: number on the left, remove on the right. Present at
                every width so the mobile layout has no orphaned dead cell. */}
            <div className="mb-2 flex h-8 items-center justify-between sm:h-7">
              <span className="admin-label">Key {props.existing + i + 1}</span>
              <button
                type="button"
                aria-label={`Remove key row ${i + 1}`}
                disabled={rows.length === 1}
                onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
                className="flex h-11 w-11 items-center justify-center rounded-md text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 disabled:invisible sm:h-10 sm:w-10"
              >
                <X size={14} />
              </button>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_5rem_minmax(0,2fr)] sm:items-center sm:gap-3">
              <div className="min-w-0">
                <Input
                  value={r.label}
                  aria-label="Key label"
                  placeholder={`key-${props.existing + i + 1}`}
                  onChange={(e) => update(i, { label: e.target.value })}
                />
              </div>
              <div className="min-w-0">
                <Input
                  type="number"
                  min={1}
                  value={r.weight}
                  aria-label="Key weight"
                  title="Weight"
                  onChange={(e) => update(i, { weight: e.target.value })}
                />
              </div>
              <div className="min-w-0">
                <Input
                  value={r.secret}
                  type="password"
                  autoComplete="off"
                  aria-label="API key"
                  placeholder="sk-…"
                  className="font-mono"
                  onChange={(e) => update(i, { secret: e.target.value })}
                />
              </div>
            </div>
          </div>
        ))}
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
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
      </div>
    </Card>
  );
}

// -- upstream model fetch + select + attach ----------------------------------------

function ModelPickerCard(props: { p: Provider; onError: (m: string) => void; className?: string }) {
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
  const allVisibleOn = visible.length > 0 && visible.every((m) => selected.includes(m));
  const toggleAllVisible = () =>
    setSelected((sel) =>
      allVisibleOn
        ? sel.filter((m) => !visible.includes(m))
        : [...sel, ...visible.filter((m) => !sel.includes(m))],
    );
  const w = Math.max(1, parseInt(weight, 10) || 1);
  const sectionLabel = "admin-label mb-2 flex items-center justify-between gap-2";

  return (
    <Card className={props.className}>
      <CardHeader
        icon={Layers}
        title="Attach models"
        subtitle={
          props.p.provider_type === "cline"
            ? "Global Cline list, shared across all Cline accounts."
            : `${deployedHere.size} model${deployedHere.size === 1 ? "" : "s"} already deployed from this account`
        }
        right={
          <Button variant="outline" disabled={fetchM.isPending} onClick={() => fetchM.mutate()}>
            {fetchM.isPending
              ? <Loader2 size={14} className="motion-safe:animate-spin" />
              : <RefreshCw size={14} />}
            {models === null ? "Fetch" : "Refetch"}
          </Button>
        }
      />
      <div className="space-y-5 p-5">
        {/* ── 1. pick from upstream ─────────────────────────────── */}
        <section aria-label="Upstream models">
          <div className={sectionLabel}>
            <span>From upstream</span>
            {models !== null && models.length > 0 && (
              <span className="font-mono normal-case tracking-normal">
                {visible.length}/{models.length}
              </span>
            )}
          </div>

          {fetchM.error && <div className="mb-2"><ErrorText>{fetchM.error.message}</ErrorText></div>}

          {fetchM.isPending && (
            <div className="space-y-1.5 rounded-lg border border-[var(--dash-line)] p-2" aria-busy="true">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-7 rounded-md bg-white/[0.03] motion-safe:animate-pulse" />
              ))}
            </div>
          )}

          {models === null && !fetchM.isPending && (
            <button
              type="button"
              onClick={() => fetchM.mutate()}
              className="flex min-h-[88px] w-full flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-[var(--dash-line-strong)] px-4 text-center transition-colors hover:border-[var(--admin-accent)]/40 hover:bg-white/[0.015] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
            >
              <RefreshCw size={16} className="text-[var(--admin-text-dim)]" aria-hidden />
              <span className="text-[13px] text-[var(--admin-text-muted)]">Fetch model list</span>
              <span className="max-w-full break-all font-mono text-[11px] text-[var(--admin-text-dim)]">
                {props.p.base_url || "default endpoint"}
              </span>
            </button>
          )}

          {models !== null && !fetchM.isPending && models.length === 0 && (
            <EmptyState>
              No models returned{props.p.provider_type === "cline" ? " by any Cline account" : " by this account"}.
            </EmptyState>
          )}

          {models !== null && !fetchM.isPending && models.length > 0 && (
            <div className="overflow-hidden rounded-lg border border-[var(--dash-line)]">
              <div className="flex items-center gap-2 border-b border-[var(--dash-line)] px-2.5">
                <Search size={13} className="shrink-0 text-[var(--admin-text-dim)]" aria-hidden />
                <input
                  value={filter}
                  aria-label="Filter models"
                  placeholder="Filter models…"
                  onChange={(e) => setFilter(e.target.value)}
                  className="min-h-[40px] w-full bg-transparent text-[13px] text-[var(--admin-text)] placeholder:text-[var(--admin-text-dim)] focus:outline-none"
                />
                {filter && (
                  <button type="button" aria-label="Clear filter" onClick={() => setFilter("")}
                          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--admin-text-dim)] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50">
                    <X size={13} />
                  </button>
                )}
                <button
                  type="button"
                  onClick={toggleAllVisible}
                  disabled={visible.length === 0}
                  className="min-h-[32px] shrink-0 rounded-md px-2 text-[11px] font-medium text-[var(--admin-text-muted)] hover:bg-white/[0.04] hover:text-[var(--admin-text)] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                >
                  {allVisibleOn ? "Clear" : "Select all"}
                </button>
              </div>
              <ul className="max-h-64 overflow-y-auto p-1" role="listbox" aria-multiselectable="true" aria-label="Models">
                {visible.map((mid) => {
                  const on = selected.includes(mid);
                  const deployed = deployedHere.has(mid);
                  return (
                    <li key={mid} role="option" aria-selected={on}>
                      <button
                        type="button"
                        onClick={() => toggle(mid)}
                        className={`flex min-h-[40px] w-full items-center gap-2.5 rounded-md px-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 ${
                          on ? "bg-[var(--admin-accent-soft)]" : "hover:bg-white/[0.03]"
                        }`}
                      >
                        <span
                          aria-hidden
                          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors ${
                            on ? "border-[var(--admin-accent)] bg-[var(--admin-accent)]" : "border-white/20"
                          }`}
                        >
                          {on && <CheckCircle2 size={11} className="text-white" />}
                        </span>
                        <span className={`min-w-0 flex-1 truncate font-mono text-[12px] ${on ? "text-[var(--admin-text)]" : "text-[var(--admin-text-muted)]"}`} title={mid}>
                          {mid}
                        </span>
                        {deployed && <Badge tone="green">deployed</Badge>}
                      </button>
                    </li>
                  );
                })}
                {visible.length === 0 && (
                  <li className="px-2 py-4 text-center text-[12px] text-[var(--admin-text-dim)]">
                    No models match “{filter}”.
                  </li>
                )}
              </ul>
            </div>
          )}
        </section>

        {/* ── 2. hand-typed id ──────────────────────────────────── */}
        <section aria-label="Add model by id">
          <div className={sectionLabel}><span>Add by id</span></div>
          <form
            className="flex flex-col gap-2 sm:flex-row"
            onSubmit={(e) => {
              e.preventDefault();
              addCustom.mutate();
            }}
          >
            <div className="min-w-0 flex-1">
              <Input
                value={customId}
                placeholder="gpt-4o, z-ai/glm-5.2…"
                aria-label="Model id"
                className="font-mono"
                onChange={(e) => {
                  setCustomId(e.target.value);
                  setCustomError(null);
                }}
              />
            </div>
            <Button type="submit" variant="outline" disabled={!customId.trim() || addCustom.isPending}>
              <Plus size={14} /> {addCustom.isPending ? "Adding…" : "Add"}
            </Button>
          </form>
          {customError
            ? <div className="mt-2"><ErrorText>{customError}</ErrorText></div>
            : <p className="mt-1.5 text-[11px] text-[var(--admin-text-dim)]">Becomes its own model group, named after the id.</p>}
        </section>

        {/* ── action bar: weight is shared by both paths ────────── */}
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-[var(--dash-line)] bg-white/[0.01] px-3 py-2.5">
          <label className="flex items-center gap-2 text-[12px] text-[var(--admin-text-muted)]">
            Weight
            <span className="w-20">
              <Input type="number" min={1} value={weight} aria-label="Deployment weight"
                     onChange={(e) => setWeight(e.target.value)} />
            </span>
          </label>
          <span className="min-w-[10rem] flex-1 whitespace-nowrap text-[12px] text-[var(--admin-text-dim)]">
            {selected.length > 0
              ? `${selected.length} selected${selected.length > 1 ? " · added as fallbacks of one group" : ""}`
              : "Nothing selected yet"}
          </span>
          <Button disabled={selected.length === 0 || attach.isPending} onClick={() => attach.mutate()}>
            {attach.isPending
              ? "Applying…"
              : `Apply${selected.length ? ` ${selected.length}` : ""} model${selected.length === 1 ? "" : "s"} · w${w}`}
          </Button>
          {attach.error && <div className="w-full"><ErrorText>{attach.error.message}</ErrorText></div>}
        </div>
      </div>
    </Card>
  );
}

// -- deployments referencing this account ---------------------------------------------

export function DeploymentsCard(props: { provider: string; onError: (m: string) => void; className?: string }) {
  const q = useQuery({ queryKey: ["model-groups"], queryFn: getModels });
  const qc = useQueryClient();
  const [pending, setPending] = useState<{ group: string; model_id: string } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const selectAllRef = useRef<HTMLInputElement>(null);

  // Pair identity: group names and model ids can each contain slashes, so a
  // delimiter-joined string could alias two different deployments.
  const selKey = (group: string, modelId: string) => JSON.stringify([group, modelId]);

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

  const selectedTargets = rows.filter((d) => selected.has(selKey(d.group, d.model_id)));
  const allSelected = rows.length > 0 && selectedTargets.length === rows.length;
  const someSelected = selectedTargets.length > 0;

  // A background refetch (or a single detach) can remove a row under the
  // selection; drop those keys so the bulk bar never counts a vanished pair.
  useEffect(() => {
    setSelected((prev) => {
      if (prev.size === 0) return prev;
      const alive = new Set(rows.map((d) => selKey(d.group, d.model_id)));
      const next = new Set([...prev].filter((k) => alive.has(k)));
      return next.size === prev.size ? prev : next;
    });
  }, [rows]);

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someSelected && !allSelected;
  }, [someSelected, allSelected]);

  // Sequential, and a failure does not cancel the rest: each delete mutates
  // the routing ledger on the backend, and the operator asked for all of them.
  const bulkDetach = useMutation({
    mutationFn: async (targets: { group: string; model_id: string }[]) => {
      const failed: { group: string; model_id: string; message: string }[] = [];
      for (const t of targets) {
        try {
          await deleteDeployment(t.group, props.provider, t.model_id);
        } catch (e) {
          failed.push({ ...t, message: e instanceof Error ? e.message : String(e) });
        }
      }
      return { failed };
    },
    onSuccess: ({ failed }) => {
      const failedKeys = new Set(failed.map((f) => selKey(f.group, f.model_id)));
      setSelected((prev) => new Set([...prev].filter((k) => !failedKeys.has(k))));
      setBulkOpen(false);
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
      if (failed.length > 0) {
        props.onError(
          `Could not detach ${failed.length} deployment${failed.length === 1 ? "" : "s"} — ` +
            failed.map((f) => `${f.group}/${f.model_id}: ${f.message}`).join("; "),
        );
      }
    },
    onError: (e) => props.onError(e.message),
  });

  return (
    <Card className={props.className}>
      <CardHeader
        icon={Boxes}
        title="Deployments"
        subtitle="The model groups this account serves, and the aliases that resolve to them."
        right={<Badge tone="blue">{rows.length} deployment{rows.length === 1 ? "" : "s"}</Badge>}
      />
      {q.isLoading ? (
        <div className="space-y-3 p-5" role="status" aria-label="Loading deployments">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-2/3" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState icon={Boxes} title="Not serving any model group">
          Attach a model from the picker — every deployment is a (model group, model id) pair that routes here.
        </EmptyState>
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
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--dash-line)] px-4 py-1">
            <label className="flex h-11 cursor-pointer items-center gap-2 rounded-lg px-2 text-[12px] text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]">
              <input
                ref={selectAllRef}
                type="checkbox"
                className="h-4 w-4 cursor-pointer accent-blue-500"
                checked={allSelected}
                onChange={(e) => {
                  const on = e.target.checked;
                  setSelected(
                    on ? new Set(rows.map((d) => selKey(d.group, d.model_id))) : new Set(),
                  );
                }}
                aria-label="Select all deployments of this provider"
              />
              Select all
            </label>
            <span
              className="font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]"
              aria-live="polite"
            >
              {selectedTargets.length} selected
            </span>
            <div className="ml-auto flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
              <Button
                variant="ghost"
                disabled={selectedTargets.length === 0 || bulkDetach.isPending}
                onClick={() => setSelected(new Set())}
              >
                Clear
              </Button>
              <Button
                variant="danger"
                disabled={selectedTargets.length === 0 || bulkDetach.isPending}
                onClick={() => setBulkOpen(true)}
              >
                <Unlink size={14} />{" "}
                {bulkDetach.isPending
                  ? "Detaching…"
                  : `Detach selected${selectedTargets.length > 0 ? ` (${selectedTargets.length})` : ""}`}
              </Button>
            </div>
          </div>
          <Table head={["", "Model group", "Model ID", "Weight", "Ready", ""]}>
            {(() => {
              const seen = new Set<string>();
              return rows.map((d) => {
                const isFirst = !seen.has(d.group);
                const expanded = openGroup === d.group;
                if (isFirst) seen.add(d.group);
                // A group's first row carries the group name; later rows of the
                // same group show an unbroken left rail so the block reads as one.
                return (
                  <Fragment key={`${d.group}/${d.model_id}`}>
                    <tr>
                      <TD className="w-10">
                        <label className="flex h-11 w-11 cursor-pointer items-center justify-center">
                          <input
                            type="checkbox"
                            className="h-4 w-4 cursor-pointer accent-blue-500"
                            checked={selected.has(selKey(d.group, d.model_id))}
                            onChange={(e) => {
                              const k = selKey(d.group, d.model_id);
                              setSelected((prev) => {
                                const next = new Set(prev);
                                if (e.target.checked) next.add(k);
                                else next.delete(k);
                                return next;
                              });
                            }}
                            aria-label={`Select ${d.model_id} in ${d.group}`}
                          />
                        </label>
                      </TD>
                      {/* The group name on the first row of a block; a quiet
                          rail on its siblings so one group reads as one unit. */}
                      <TD className={`font-medium ${!isFirst ? "border-l-2 border-l-[var(--dash-line-strong)]" : ""}`}>
                        {isFirst ? d.group : (
                          <span className="font-mono text-[12px] text-[var(--admin-text-dim)]">
                            in {d.group}
                          </span>
                        )}
                      </TD>
                      <TD className="font-mono text-[12px]">{d.model_id}</TD>
                      <TD className="tabular-nums">{d.weight}</TD>
                      <TD>
                        <Badge tone={d.available ? "green" : d.probation ? "blue" : "amber"}>
                          {d.available ? "ready" : d.probation ? "probation" : "cooldown"}
                        </Badge>
                        {!d.available && !d.probation && d.cooldown_remaining_s > 0 && (
                          <span className="ml-1.5 font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
                            {Math.ceil(d.cooldown_remaining_s)}s
                          </span>
                        )}
                      </TD>
                      <TD>
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            title={expanded ? `Hide aliases for ${d.group}` : `Show aliases for ${d.group}`}
                            aria-label={expanded ? `Hide aliases for ${d.group}` : `Show aliases for ${d.group}`}
                            aria-expanded={expanded}
                            className={isFirst ? "" : "invisible"}
                            tabIndex={isFirst ? 0 : -1}
                            onClick={() => {
                              setOpenGroup(expanded ? null : d.group);
                              setDraft({});
                              setRemoved([]);
                              setNewAliasInput("");
                            }}
                          >
                            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                            <span className="hidden md:inline">Aliases</span>
                          </Button>
                          <Button
                            variant="ghost"
                            title={`Detach ${d.model_id} from ${d.group}`}
                            aria-label={`Detach ${d.model_id}`}
                            onClick={() => setPending({ group: d.group, model_id: d.model_id })}
                          >
                            <Unlink size={14} />
                            <span className="hidden md:inline">Detach</span>
                          </Button>
                        </div>
                      </TD>
                    </tr>
                    {expanded && isFirst && (
                      <tr>
                        <td colSpan={6} className="border-t border-[var(--dash-line)] bg-black/20 px-4 py-3">
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
      <Dialog
        open={bulkOpen}
        title={`Detach ${selectedTargets.length} model${selectedTargets.length === 1 ? "" : "s"}?`}
        onClose={() => {
          if (!bulkDetach.isPending) setBulkOpen(false);
        }}
      >
        <p className="text-[13px] text-[var(--admin-text-muted)]">
          These deployments stop routing to <span className="font-mono">{props.provider}</span>.
          A model group left with no deployments disappears.
        </p>
        <ul className="mt-3 max-h-48 space-y-0.5 overflow-y-auto rounded-lg border border-[var(--dash-line)] p-2 font-mono text-[12px]">
          {selectedTargets
            .slice()
            .sort((a, b) => `${a.group}/${a.model_id}`.localeCompare(`${b.group}/${b.model_id}`))
            .map((d) => (
              <li key={selKey(d.group, d.model_id)} className="truncate px-1 py-0.5">
                <span className="text-[var(--admin-text)]">{d.group}</span>
                <span className="text-[var(--admin-text-dim)]"> / {d.model_id}</span>
              </li>
            ))}
        </ul>
        {bulkDetach.error && (
          <div className="mt-3"><ErrorText>{bulkDetach.error.message}</ErrorText></div>
        )}
        <div className="flex justify-end gap-2 pt-4">
          <Button
            variant="ghost"
            type="button"
            disabled={bulkDetach.isPending}
            onClick={() => setBulkOpen(false)}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={bulkDetach.isPending}
            onClick={() =>
              bulkDetach.mutate(selectedTargets.map((d) => ({ group: d.group, model_id: d.model_id })))
            }
          >
            {bulkDetach.isPending ? "Detaching…" : `Detach ${selectedTargets.length}`}
          </Button>
        </div>
      </Dialog>
    </Card>
  );
}

// -- account settings (edit metadata) -------------------------------------------

export function AccountSettingsCard(props: { p: Provider; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(props.p.name);
  const [type, setType] = useState(props.p.provider_type);
  const [baseUrl, setBaseUrl] = useState(props.p.base_url);
  const [aliasId, setAliasId] = useState(props.p.alias_id ?? "");
  const [enabled, setEnabled] = useState(props.p.enabled);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const patch: { name?: string; base_url?: string;
                     provider_type?: string; alias_id?: string | null;
                     enabled?: boolean } = {};
      if (name.trim() !== props.p.name) patch.name = name.trim();
      if (type !== props.p.provider_type) patch.provider_type = type;
      if (baseUrl.trim() !== props.p.base_url) patch.base_url = baseUrl.trim();
      if (enabled !== props.p.enabled) patch.enabled = enabled;
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
    aliasId.trim() !== (props.p.alias_id ?? "") ||
    enabled !== props.p.enabled;

  return (
    <Card>
      <CardHeader
        icon={Settings}
        title="Account"
        subtitle="Identity, endpoint, and availability. Changes apply to every deployment on this account."
      />
      <div className="grid grid-cols-1 gap-x-5 gap-y-4 p-5 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="openai-backup" />
        </Field>
        <Field label="Type">
          <Select
            value={type}
            onChange={setType}
            options={PROVIDER_TYPE_OPTIONS}
            className="w-full"
          />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Base URL" hint="Optional for every built-in type. Required only for an OpenAI-compatible URL.">
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" className="font-mono" />
          </Field>
        </div>
        <Field
          label="Alias id"
          hint="Accounts sharing an alias form a cross-provider weighted pool for any model id they both serve."
        >
          <Input
            value={aliasId}
            onChange={(e) => setAliasId(e.target.value)}
            placeholder="e.g. shared-openai"
            className="font-mono"
          />
        </Field>
        <div className="flex items-end">
          <div className="flex w-full items-center justify-between gap-3 rounded-lg border border-[var(--dash-line)] bg-white/[0.01] px-3 py-2.5">
            <div className="min-w-0">
              <p className="text-[13px] font-medium text-[var(--admin-text)]">
                Enabled
              </p>
              <p className="text-[11px] text-[var(--admin-text-dim)]">
                Off: deployments report unavailable. Keys are kept.
              </p>
            </div>
            <Toggle
              label="Enable provider account"
              checked={enabled}
              onChange={setEnabled}
              className="after:absolute after:-inset-3 after:content-['']"
            />
          </div>
        </div>
        {error && <div className="sm:col-span-2 lg:col-span-4"><ErrorText>{error}</ErrorText></div>}
        <div className="flex justify-end sm:col-span-2 lg:col-span-4">
          <Button disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : dirty ? "Save changes" : "Saved"}
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

  // Connect: open Cline's login page in a new tab through the redirect
  // pipeline — wiwi's own /cline/oauth/callback is the callback_url (guarded by
  // a single-use state token minted by /admin/cline/oauth/auto-connect). If
  // Cline honours callback_url, the new tab returns here with
  // ?cline_connected=1 and the banner above connects the account by itself.
  // Cline's Google OAuth currently ignores callback_url and lands on its own
  // /auth/callback page, so the paste-code fallback below remains the reliable
  // path (it accepts the full callback URL and handles truncated codes).
  const autoConnect = useMutation({
    mutationFn: () => clineAutoConnect(props.p.name, returnPath),
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
    <Card>
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
              <div className="space-y-2 border-t border-[var(--dash-line)] pt-3">
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
          <div className="space-y-2 border-t border-[var(--dash-line)] pt-3">
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

// Hermes OAuth card — the device-code connect flow, right where the account is
// managed. Reuses the shared HermesConnectDialog so the poll loop is defined
// once (also used on /console/hermes-oauth), and the shared HermesAccountList so
// a multi-account pool (one Nous identity per key, AUDIT #427) reads the same
// here as on the console. Connect bootstraps the account, so no API key is
// needed up front; the token lands on the first pool key.
function HermesOAuthCard(props: { p: Provider; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [localError, setLocalError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [connectOpen, setConnectOpen] = useState(false);
  /** ``null`` = closed; ``{}`` = whole pool; ``{ keyLabel }`` = one account. */
  const [confirmDisconnect, setConfirmDisconnect] = useState<{ keyLabel?: string } | null>(null);
  const qKey = ["hermes-oauth-status", props.p.name];

  const statusQ = useHermesStatusQuery(props.p.name);

  const accounts = statusQ.data?.accounts ?? [];
  const managed = accounts.filter((a) => a.managed);
  const connected = statusQ.data?.connected ?? false;
  const needsRefresh = managed.some((a) => a.needs_refresh);

  const actions = useHermesAccountActions(props.p.name, {
    onError: (m) => {
      if (m === null) setNotice(null);
      setLocalError(m);
      if (m !== null) props.onError(m);
    },
    onNotice: setNotice,
    onDisconnected: () => setConfirmDisconnect(null),
  });

  return (
    <Card>
      <CardHeader
        title="Hermes OAuth"
        subtitle="Nous Portal · device-code sign-in"
        right={
          statusQ.isLoading ? (
            <Badge tone="gray">…</Badge>
          ) : statusQ.isError ? (
            <Badge tone="red">status unavailable</Badge>
          ) : connected ? (
            <div className="flex flex-wrap items-center justify-end gap-2">
              {managed.length > 1 && <Badge tone="blue">{managed.length} accounts</Badge>}
              <Badge tone={needsRefresh ? "amber" : "green"}>
                {needsRefresh ? "expires soon" : "connected"}
              </Badge>
            </div>
          ) : (
            <Badge tone="gray">not connected</Badge>
          )
        }
      />
      <div className="space-y-4 px-4 pb-4 pt-2">
        {statusQ.isLoading && (
          <div className="flex justify-center py-4"><Spinner /></div>
        )}
        {localError && <ErrorText>{localError}</ErrorText>}
        {notice && !localError && (
          <p className="text-[12px] text-emerald-400">{notice}</p>
        )}

        {accounts.length > 0 && (
          <div className="space-y-2">
            {connected && (
              <p className="text-[12px] text-[var(--admin-text-muted)]">
                Each pool key is one Nous account with its own rotating refresh
                token. Refresh forces a rotation now; the gateway already refreshes
                automatically within the final 120 s before expiry.
              </p>
            )}
            <HermesAccountList
              accounts={accounts}
              actions={actions}
              provider={props.p.name}
              disconnectVariant="danger"
              onRequestDisconnect={(keyLabel) => setConfirmDisconnect({ keyLabel })}
            />
          </div>
        )}

        {!connected && !statusQ.isLoading && (
          <div className="space-y-2">
            <p className="text-[12px] text-[var(--admin-text-muted)]">
              Sign in with your Nous Portal account via the OAuth 2.0
              device-code flow — no API key needed. The account is created
              automatically and the token lands on this provider&apos;s first pool
              key.
            </p>
            <Button variant="primary" className="min-h-11" onClick={() => setConnectOpen(true)}>
              <LogIn size={14} /> Connect with Nous Portal
            </Button>
          </div>
        )}

        {connected && (
          <div className="flex flex-wrap items-center gap-2 border-t border-[var(--dash-line)] pt-3">
            <Button variant="ghost" className="min-h-11" onClick={() => void statusQ.refetch()}>
              <RefreshCw size={14} /> Recheck status
            </Button>
            {managed.length > 1 && (
              <Button
                variant="danger"
                className="min-h-11"
                disabled={actions.pending?.kind === "disconnect"}
                onClick={() => setConfirmDisconnect({})}
              >
                <Unlink size={14} /> Disconnect all {managed.length} accounts
              </Button>
            )}
          </div>
        )}

        <Dialog
          open={confirmDisconnect !== null}
          title={
            confirmDisconnect?.keyLabel
              ? `Disconnect Hermes account “${confirmDisconnect.keyLabel}”?`
              : `Disconnect all Hermes accounts for ${props.p.name}?`
          }
          onClose={() => setConfirmDisconnect(null)}
        >
          <p className="text-[13px] text-[var(--admin-text-muted)]">
            {confirmDisconnect?.keyLabel
              ? "Clears the stored OAuth tokens for this account only; its sibling accounts keep working. The pool key keeps its last access token until you reconnect or replace it."
              : `Clears the stored OAuth tokens for all ${managed.length} managed account${managed.length === 1 ? "" : "s"} in this pool. Each pool key keeps its last access token until you reconnect or replace it.`}
          </p>
          <div className="flex justify-end gap-2 pt-4">
            <Button variant="ghost" onClick={() => setConfirmDisconnect(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={actions.pending?.kind === "disconnect"}
              onClick={() => actions.disconnect(confirmDisconnect?.keyLabel)}
            >
              {actions.pending?.kind === "disconnect" ? "Disconnecting…" : "Disconnect"}
            </Button>
          </div>
        </Dialog>

        <HermesConnectDialog
          open={connectOpen}
          provider={props.p.name}
          onClose={() => setConnectOpen(false)}
          onConnected={() => {
            void qc.invalidateQueries({ queryKey: qKey });
            void qc.invalidateQueries({ queryKey: ["providers"] });
          }}
        />

        <p className="text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
          Hermes uses OAuth 2.0 device authorization (RFC 8628) with no
          callback URL or client secret. Each account is stored on its own pool
          key (prefixed <code>hermes:</code>; the adapter strips it for the
          bearer), so a multi-account pool gives each Nous identity its own
          refresh grant and rate-limit bucket; the refresh token is kept
          server-side and rotates on each refresh. A terminal grant (
          <code>invalid_grant</code> / reused refresh token) requires
          reconnecting that account.
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

  if (q.isLoading) {
    return (
      <div className="admin-dash" role="status" aria-busy="true" aria-label="Loading provider account">
        <Skeleton className="mb-3 h-5 w-24" />
        <Skeleton className="h-40 w-full rounded-2xl" />
        <Skeleton className="mt-8 h-4 w-16" />
        <Skeleton className="mt-4 h-64 w-full rounded-xl" />
      </div>
    );
  }
  const backLink = (
    <Link
      to="/console/providers"
      className="inline-flex min-h-[44px] items-center gap-1.5 rounded-md text-[13px] font-medium text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 sm:min-h-0"
    >
      <ArrowLeft size={14} /> Back to providers
    </Link>
  );
  if (q.isError) {
    return (
      <div className="admin-dash">
        <div className="mb-3">{backLink}</div>
        <PageHeader title={name} subtitle="Failed to load the provider account." />
        <ErrorText>{q.error.message}</ErrorText>
        <div className="mt-4">
          <Button variant="outline" onClick={() => void q.refetch()}>Try again</Button>
        </div>
      </div>
    );
  }
  if (!p) {
    return (
      <div className="admin-dash">
        <div className="mb-3">{backLink}</div>
        <PageHeader title={name} subtitle="No provider account by this name." />
        <p className="text-[13px] text-[var(--admin-text-dim)]">
          It may have been renamed or deleted.
        </p>
      </div>
    );
  }

  const keyCounts = p.keys.reduce(
    (acc, k) => {
      // A key with no secret cannot authenticate whatever its status says;
      // it is a placeholder and reads as one.
      const s: PoolKey["status"] = k.has_secret ? k.status : "disabled";
      return { ...acc, [s]: acc[s] + 1 };
    },
    { active: 0, cooling: 0, invalid: 0, disabled: 0 } as Record<PoolKey["status"], number>,
  );

  return (
    <div className="admin-dash">
      <Link
        to="/console/providers"
        className="mb-3 inline-flex min-h-[44px] items-center gap-1.5 rounded-md text-[13px] font-medium text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 sm:min-h-0"
      >
        <ArrowLeft size={14} /> Providers
      </Link>
      {/* ── identity band ─────────────────────────────────────────────────
          The operator's first question is "is this account serving, and with
          how many keys" — so health and the pool summary share one surface
          with the name instead of living in a separate strip below it. */}
      <section
        aria-label="Provider account"
        className="overflow-hidden rounded-2xl border border-[var(--dash-line)] bg-[linear-gradient(180deg,var(--dash-surface-2)_0%,var(--dash-surface)_100%)] shadow-[inset_0_1px_0_0_rgba(255,255,255,0.045),0_8px_30px_-16px_rgba(0,0,0,0.9)]"
      >
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4 px-5 py-5 sm:px-6">
          <div className="flex min-w-0 items-start gap-4">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-[var(--dash-line-strong)] bg-white/[0.03] shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)]">
              <PIcon size={20} className="text-[var(--admin-text)]" aria-hidden />
            </span>
            <div className="min-w-0">
              <h2 className="break-all text-2xl font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
                {p.name}
              </h2>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <Badge tone="gray">{p.provider_type}</Badge>
                {!p.enabled && <Badge tone="amber">disabled</Badge>}
                {p.alias_id && (
                  <span className="font-mono text-[12px] text-[var(--admin-text-dim)]">
                    alias <span className="text-[var(--admin-text-muted)]">{p.alias_id}</span>
                  </span>
                )}
              </div>
              <p
                className="mt-1.5 max-w-full truncate font-mono text-[12px] text-[var(--admin-text-dim)]"
                title={p.base_url || undefined}
              >
                {p.base_url || "default endpoint"}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span
              role="status"
              className={`admin-health-pill ${p.healthy ? "is-ok" : p.keys.length === 0 ? "is-warn" : "is-down"}`}
            >
              <span className="admin-health-pill-dot" aria-hidden />
              {p.healthy ? "Healthy" : p.keys.length === 0 ? "No keys" : "No healthy keys"}
            </span>
            <Button variant="outline" onClick={() => void q.refetch()} disabled={q.isFetching}>
              <RefreshCw size={14} className={q.isFetching ? "motion-safe:animate-spin" : ""} /> Refresh
            </Button>
            <Button variant="danger" onClick={() => setDelOpen(true)}>
              <Trash2 size={14} /> Delete
            </Button>
          </div>
        </div>
        {/* Pool summary: the figures the key table would make you count. */}
        <dl className="grid grid-cols-2 border-t border-[var(--dash-line)] sm:grid-cols-5">
          {/* Mobile: Keys spans the top row, the four statuses sit in a 2×2
              under it. sm+: five equal cells split by hairlines. */}
          <div className="col-span-2 border-b border-[var(--dash-line)] px-5 py-3.5 sm:col-span-1 sm:border-b-0 sm:border-r sm:px-6">
            <dt className="admin-label">Keys</dt>
            <dd className="m-0 mt-1 text-[26px] font-medium leading-none tracking-[-0.03em] tabular-nums text-[#f5f5f6]">
              {p.keys.length}
            </dd>
          </div>
          {[
            { label: "Active", value: keyCounts.active, dot: "bg-emerald-400" },
            { label: "Cooling", value: keyCounts.cooling, dot: "bg-amber-400" },
            { label: "Invalid", value: keyCounts.invalid, dot: "bg-red-400" },
            { label: "Disabled", value: keyCounts.disabled, dot: "bg-zinc-500" },
          ].map((s, i) => (
            <div
              key={s.label}
              className={`px-5 py-3.5 sm:px-6 ${i < 2 ? "border-b sm:border-b-0" : ""} ${i % 2 === 0 ? "border-r" : ""} ${i < 3 ? "sm:border-r" : ""} border-[var(--dash-line)]`}
            >
              <dt className="admin-label flex items-center gap-1.5">
                <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} aria-hidden /> {s.label}
              </dt>
              <dd className={`m-0 mt-1 text-[26px] font-medium leading-none tracking-[-0.03em] tabular-nums ${s.value ? "text-[#f5f5f6]" : "text-[var(--admin-text-dim)]"}`}>
                {s.value}
              </dd>
            </div>
          ))}
        </dl>
      </section>
      {error && <div className="mt-4"><ErrorText>{error}</ErrorText></div>}
      <Dialog open={delOpen} title={`Delete provider ${p.name}?`} onClose={() => setDelOpen(false)}>
        <p className="text-[13px] text-[var(--admin-text-muted)]">
          This removes the account and all its keys from the live pool. Any
          model group that referenced it will have those deployments removed.
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
      {/* ── section bands ────────────────────────────────────────────────
          Three ranked bands replace the flat six-card wall: what serves
          (keys), what it serves (models), and how the account is configured. */}
      <SectionHeading label="Keys" hint="Credentials in the pool. Higher-weight keys are picked more often." />
      <div className="admin-stagger grid grid-cols-1 gap-4">
        <KeyPoolCard p={p} providerName={name} onError={setError} />
        <AddKeysCard provider={p.name} existing={p.keys.length} onError={setError} />
      </div>
      <SectionHeading label="Models" hint="Fetch the upstream list to attach, or type an id. Each attachment is one deployment." />
      <div className="admin-stagger grid grid-cols-1 gap-4">
        <ModelPickerCard p={p} onError={setError} />
        <DeploymentsCard provider={p.name} onError={setError} />
      </div>
      <SectionHeading label="Account" />
      <div className="admin-stagger grid grid-cols-1 gap-4">
        <AccountSettingsCard p={p} onError={setError} />
        {p.provider_type === "cline" && <ClineOAuthCard p={p} onError={setError} />}
        {p.provider_type === "hermes" && <HermesOAuthCard p={p} onError={setError} />}
      </div>
    </div>
  );
}
