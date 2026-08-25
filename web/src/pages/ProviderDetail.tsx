// Provider detail — opened from an "Edit" action on a Providers bento card.
// Three panes: key pool (enable/disable, weights), bulk multi-key add, and
// model IDs fetched live from the upstream with select-and-attach to a group.

import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Boxes, Cloud, Cpu, Eye, EyeOff, Globe, Plus, RefreshCw, Search, Server, Sparkles, Trash2, X, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  addDeployment,
  deleteProvider,
  deleteProviderKey,
  fetchProviderModels,
  getModels,
  getProviders,
  patchProvider,
  patchProviderKey,
  addProviderKey,
} from "@/api/client";
import type { PoolKey, Provider } from "@/api/types";
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
};

const PROVIDER_TYPE_OPTIONS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "nvidia-nim", label: "NVIDIA NIM" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "gmicloud", label: "GMI Cloud" },
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
            {revealed ? props.k.secret : props.k.masked}
          </span>
          <button
            type="button"
            onClick={() => setRevealed((v) => !v)}
            className="shrink-0 rounded p-1 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text-muted)]"
            title={revealed ? "Hide key" : "Reveal key"}
            aria-label={revealed ? "Hide key" : "Reveal key"}
          >
            {revealed ? <EyeOff size={12} /> : <Eye size={12} />}
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

function KeyPoolCard(props: { p: Provider; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [roundRobin, setRoundRobin] = useState(props.p.round_robin);

  const toggleRR = useMutation({
    mutationFn: () => patchProvider(props.p.name, { round_robin: !roundRobin }),
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
            <KeyRow key={k.label} provider={props.p.name} k={k} onError={props.onError} />
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
    mutationFn: () => fetchProviderModels(props.p.name),
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

  const visible = (models ?? []).filter((m) => m.toLowerCase().includes(filter.toLowerCase()));
  const toggle = (mid: string) =>
    setSelected((sel) => (sel.includes(mid) ? sel.filter((x) => x !== mid) : [...sel, mid]));

  return (
    <Card>
      <CardHeader
        title="Model IDs"
        subtitle="Fetch what this account can serve, pick, apply."
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
        {fetchM.error && <ErrorText>{fetchM.error.message}</ErrorText>}

        {models !== null && (
          <>
            {models.length === 0 ? (
              <EmptyState>No models returned by this account.</EmptyState>
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
                <div className="w-20">
                  <Field label="Weight">
                    <Input type="number" min={1} value={weight}
                           onChange={(e) => setWeight(e.target.value)} />
                  </Field>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-[var(--admin-text-dim)]">
                    {selected.length} selected{selected.length > 1 ? " — added as fallback deployments" : ""}
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

function DeploymentsCard(props: { provider: string }) {
  const q = useQuery({ queryKey: ["model-groups"], queryFn: getModels });
  const rows = useMemo(
    () =>
      (q.data?.groups ?? []).flatMap((g) =>
        g.deployments
          .filter((d) => d.provider === props.provider)
          .map((d) => ({ ...d, group: g.name })),
      ),
    [q.data, props.provider],
  );
  return (
    <Card>
      <CardHeader title="Models" right={<Badge tone="blue">{rows.length}</Badge>} />
      {q.isLoading ? (
        <Spinner />
      ) : rows.length === 0 ? (
        <EmptyState>Not referenced by any model group yet.</EmptyState>
      ) : (
        <Table head={["Model", "Model ID", "Weight", "Ready"]}>
          {rows.map((d) => (
            <tr key={`${d.group}/${d.model_id}`}>
              <TD className="font-medium">{d.group}</TD>
              <TD className="font-mono text-[12px]">{d.model_id}</TD>
              <TD className="tabular-nums">{d.weight}</TD>
              <TD><Badge tone={d.available ? "green" : "amber"}>{d.available ? "yes" : "cooldown"}</Badge></TD>
            </tr>
          ))}
        </Table>
      )}
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
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const patch: { name?: string; base_url?: string; provider_type?: string } = {};
      if (name.trim() !== props.p.name) patch.name = name.trim();
      if (type !== props.p.provider_type) patch.provider_type = type;
      if (baseUrl.trim() !== props.p.base_url) patch.base_url = baseUrl.trim();
      return patchProvider(props.p.name, patch);
    },
    onSuccess: (data) => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["providers"] });
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
      if (data.name !== props.p.name) {
        navigate(`/providers/${encodeURIComponent(data.name)}`, { replace: true });
      }
    },
    onError: (e) => setError(e.message),
  });

  const dirty =
    name.trim() !== props.p.name ||
    type !== props.p.provider_type ||
    baseUrl.trim() !== props.p.base_url;

  return (
    <Card className="xl:col-span-3">
      <CardHeader title="Account settings" subtitle="Rename, change type or base URL." />
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
        <Field label="Base URL" hint="Optional for openai/anthropic/gemini/openrouter/gmicloud/nvidia-nim. Required for compatible URLs.">
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" />
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
      navigate("/providers");
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
        <Link to="/providers"><Button variant="outline"><ArrowLeft size={14} /> Back to providers</Button></Link>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            <Link to="/providers" aria-label="Back to providers"
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
        <KeyPoolCard p={p} onError={setError} />
        <AddKeysCard provider={p.name} existing={p.keys.length} onError={setError} />
        <ModelPickerCard p={p} onError={setError} />
        <DeploymentsCard provider={p.name} />
      </div>
    </div>
  );
}
