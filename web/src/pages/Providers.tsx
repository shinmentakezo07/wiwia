// Providers page — full implementation: cards per provider account, live pool
// table with enable/disable + weight editing, add-key and add-provider dialogs.

import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, RefreshCw } from "lucide-react";
import {
  addProvider,
  addProviderKey,
  getProviders,
  patchProviderKey,
} from "@/api/client";
import type { PoolKey, Provider } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorText,
  Field,
  Input,
  Select,
  Table,
  TD,
} from "@/components/ui";
import { fmtAgo } from "@/lib/format";

const STATUS_TONE: Record<PoolKey["status"], "green" | "amber" | "red" | "gray"> = {
  active: "green",
  cooling: "amber",
  invalid: "red",
  disabled: "gray",
};

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
      <TD className="tabular-nums">
        {props.k.req_count}
        {props.k.err_count > 0 && (
          <span className="ml-1 text-red-400/80">({props.k.err_count} err)</span>
        )}
      </TD>
      <TD className="font-mono text-[12px] text-[var(--admin-text-dim)]">{fmtAgo(props.k.last_used_ts)}</TD>
      <TD>
        <Button
          variant="outline"
          disabled={patch.isPending}
          onClick={() => patch.mutate({ enabled: !props.k.enabled })}
        >
          {props.k.enabled ? "Disable" : "Enable"}
        </Button>
      </TD>
    </tr>
  );
}

function ProviderCard(props: { p: Provider; onError: (m: string) => void }) {
  const [addOpen, setAddOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [secret, setSecret] = useState("");
  const [weight, setWeight] = useState("1");
  const qc = useQueryClient();

  const addKey = useMutation({
    mutationFn: () => addProviderKey(props.p.name, { label, key: secret, weight: parseInt(weight, 10) || 1 }),
    onSuccess: () => {
      setAddOpen(false);
      setLabel("");
      setSecret("");
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--admin-border)] px-4 py-3">
        <div className="flex items-center gap-2">
          <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            {props.p.name}
          </h3>
          <Badge tone="blue">{props.p.provider_type}</Badge>
          <Badge tone={props.p.healthy ? "green" : "red"}>
            {props.p.healthy ? "healthy" : "no healthy keys"}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to={`/providers/${encodeURIComponent(props.p.name)}`}
            className="inline-flex"
            title="Open provider detail"
          >
            <Button variant="outline">
              <Pencil size={14} /> Edit
            </Button>
          </Link>
          <Button variant="outline" onClick={() => setAddOpen(true)}>
            <Plus size={14} /> Add key
          </Button>
        </div>
      </div>
      <div className="px-4 pb-3 pt-2 font-mono text-[11px] text-[var(--admin-text-dim)]">
        {props.p.base_url || "(default endpoint)"}
      </div>
      {props.p.keys.length === 0 ? (
        <EmptyState>No keys in this pool.</EmptyState>
      ) : (
        <Table head={["Label", "Key", "Weight", "Status", "Requests", "Last used", ""]}>
          {props.p.keys.map((k) => (
            <KeyRow key={k.label} provider={props.p.name} k={k} onError={props.onError} />
          ))}
        </Table>
      )}

      <Dialog open={addOpen} title={`Add key to ${props.p.name}`} onClose={() => setAddOpen(false)}>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (label.trim() && secret.trim()) addKey.mutate();
          }}
        >
          <Field label="Label">
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="backup" />
          </Field>
          <Field label="API key" hint="Stored server-side only; shown masked afterwards.">
            <Input value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="sk-…" />
          </Field>
          <Field label="Weight">
            <Input type="number" min={1} value={weight} onChange={(e) => setWeight(e.target.value)} />
          </Field>
          {addKey.error && <ErrorText>{addKey.error.message}</ErrorText>}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!label.trim() || !secret.trim() || addKey.isPending}>
              Add
            </Button>
          </div>
        </form>
      </Dialog>
    </Card>
  );
}

export function ProvidersPage() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("openai-compatible");
  const [baseUrl, setBaseUrl] = useState("");
  const [label, setLabel] = useState("default");
  const [secret, setSecret] = useState("");

  const query = useQuery({ queryKey: ["providers"], queryFn: getProviders, refetchInterval: 15_000 });

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

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-2xl font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
          Providers
        </h2>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => void qc.invalidateQueries({ queryKey: ["providers"] })}>
            <RefreshCw size={14} /> Refresh
          </Button>
          <Button onClick={() => setAddOpen(true)}>
            <Plus size={14} /> Add provider
          </Button>
        </div>
      </div>
      {error && (
        <div className="mb-3">
          <ErrorText>{error}</ErrorText>
        </div>
      )}
      {query.isLoading && <p className="text-[13px] text-[var(--admin-text-dim)]">Loading providers…</p>}
      {query.error && <ErrorText>{query.error.message}</ErrorText>}
      <div className="space-y-4">
        {query.data?.providers.map((p) => (
          <ProviderCard key={p.name} p={p} onError={setError} />
        ))}
        {query.data && query.data.providers.length === 0 && (
          <Card>
            <EmptyState>No providers configured.</EmptyState>
          </Card>
        )}
      </div>

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
              options={[
                { value: "openai", label: "OpenAI" },
                { value: "anthropic", label: "Anthropic" },
                { value: "gemini", label: "Gemini" },
                { value: "openai-compatible", label: "OpenAI-compatible URL" },
              ]}
            />
          </Field>
          <Field label="Base URL" hint="Optional for openai/anthropic/gemini. Required for compatible URLs.">
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
