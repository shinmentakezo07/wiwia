// Provider detail — opened from an "Edit" action on a Providers bento card.
// Three panes: key pool (enable/disable, weights), bulk multi-key add, and
// model IDs fetched live from the upstream with select-and-attach to a group.

import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, RefreshCw, Search, X } from "lucide-react";
import {
  addDeployment,
  fetchProviderModels,
  getModels,
  getProviders,
  patchProviderKey,
  addProviderKey,
} from "@/api/client";
import type { PoolKey, Provider } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorText,
  Field,
  Input,
  PageHeader,
  Spinner,
  Table,
  TD,
} from "@/components/ui";

const STATUS_TONE: Record<PoolKey["status"], "green" | "amber" | "red" | "gray"> = {
  active: "green",
  cooling: "amber",
  invalid: "red",
  disabled: "gray",
};

// -- key pool ------------------------------------------------------------------

function KeyRow(props: { provider: string; k: PoolKey; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const [editingWeight, setEditingWeight] = useState(false);
  const [weight, setWeight] = useState(String(props.k.weight));

  const patch = useMutation({
    mutationFn: (p: { enabled?: boolean; weight?: number }) =>
      patchProviderKey(props.provider, props.k.label, p),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["providers"] }),
    onError: (e) => props.onError(e.message),
  });

  return (
    <tr>
      <TD className="font-medium">{props.k.label}</TD>
      <TD className="font-mono text-[12px] text-[var(--admin-text-dim)]">{props.k.masked}</TD>
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
    </tr>
  );
}

function KeyPoolCard(props: { p: Provider; onError: (m: string) => void }) {
  return (
    <Card className="xl:col-span-3">
      <CardHeader
        title="Key pool"
        right={<Badge tone="blue">{props.p.keys.length} keys</Badge>}
      />
      {props.p.keys.length === 0 ? (
        <EmptyState>No keys yet — add some below.</EmptyState>
      ) : (
        <Table head={["Label", "Key", "Weight", "Status", ""]}>
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
  const [group, setGroup] = useState("");
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
        await addDeployment(group.trim(), {
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
        subtitle="Fetch what this account can serve, pick, attach to a group."
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
                <div className="grid grid-cols-[1fr_64px] gap-2">
                  <Field label="Model group">
                    <Input
                      value={group}
                      list="wiwi-model-groups"
                      placeholder="gpt-4o-mini"
                      onChange={(e) => setGroup(e.target.value)}
                    />
                    <datalist id="wiwi-model-groups">
                      {(groupsQuery.data?.groups ?? []).map((g) => (
                        <option key={g.name} value={g.name} />
                      ))}
                    </datalist>
                  </Field>
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
                    disabled={selected.length === 0 || !group.trim() || attach.isPending}
                    onClick={() => attach.mutate()}
                  >
                    {attach.isPending ? "Adding…" : `Add ${selected.length || ""} to ${group.trim() || "group"}`}
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
      <CardHeader title="Serving groups" right={<Badge tone="blue">{rows.length}</Badge>} />
      {q.isLoading ? (
        <Spinner />
      ) : rows.length === 0 ? (
        <EmptyState>Not referenced by any model group yet.</EmptyState>
      ) : (
        <Table head={["Group", "Model ID", "Weight", "Ready"]}>
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

// -- page -------------------------------------------------------------------------------

export function ProviderDetailPage() {
  const { name = "" } = useParams();
  const [error, setError] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["providers"], queryFn: getProviders, refetchInterval: 15_000 });

  const p = q.data?.providers.find((x) => x.name === name);

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
            {p.name}
          </span>
        }
        subtitle={`${p.provider_type} · ${p.base_url || "(default endpoint)"}`}
        right={
          <div className="flex items-center gap-2">
            <Badge tone={p.healthy ? "green" : "red"}>
              {p.healthy ? "healthy" : "no healthy keys"}
            </Badge>
            <Button variant="outline" onClick={() => void q.refetch()}>
              <RefreshCw size={14} /> Refresh
            </Button>
          </div>
        }
      />
      {error && <div className="mb-3"><ErrorText>{error}</ErrorText></div>}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <KeyPoolCard p={p} onError={setError} />
        <AddKeysCard provider={p.name} existing={p.keys.length} onError={setError} />
        <ModelPickerCard p={p} onError={setError} />
        <DeploymentsCard provider={p.name} />
      </div>
    </div>
  );
}
