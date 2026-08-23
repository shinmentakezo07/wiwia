// Providers page — bento box layout: one card per provider account with
// aggregate stats (requests, tokens in/out/cached, errors, cost) computed
// from the request-log ring, plus edit/delete actions and a compact key pool.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import {
  addProvider,
  deleteProvider,
  getProviders,
  getRequestLogs,
} from "@/api/client";
import type { PoolKey, Provider, RequestLogEntry } from "@/api/types";
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
} from "@/components/ui";
import { fmtInt, fmtTokens, fmtUsd } from "@/lib/format";

const STATUS_TONE: Record<PoolKey["status"], "green" | "amber" | "red" | "gray"> = {
  active: "green",
  cooling: "amber",
  invalid: "red",
  disabled: "gray",
};

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

function ProviderCard(props: {
  p: Provider;
  stats: ProviderStats | undefined;
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

  const s = props.stats;
  const totalKeys = props.p.keys.length;
  const activeKeys = props.p.keys.filter((k) => k.enabled && k.status === "active").length;

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
          <Badge tone="gray">
            {activeKeys}/{totalKeys} keys active
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
          <Button variant="danger" onClick={() => setDelOpen(true)}>
            <Trash2 size={14} /> Delete
          </Button>
        </div>
      </div>
      <div className="px-4 pb-3 pt-2 font-mono text-[11px] text-[var(--admin-text-dim)]">
        {props.p.base_url || "(default endpoint)"}
      </div>
      {/* bento stats grid */}
      <div className="grid grid-cols-2 gap-px border-t border-[var(--admin-border)] bg-[var(--admin-border)] sm:grid-cols-3 lg:grid-cols-6">
        <StatCell label="Requests" value={s ? fmtInt(s.requests) : "—"} />
        <StatCell
          label="Errors"
          value={s ? fmtInt(s.errors) : "—"}
          tone={s && s.errors > 0 ? "danger" : undefined}
        />
        <StatCell label="Tokens In" value={s ? fmtTokens(s.tokIn) : "—"} />
        <StatCell label="Tokens Out" value={s ? fmtTokens(s.tokOut) : "—"} />
        <StatCell label="Cached" value={s ? fmtTokens(s.tokCached) : "—"} />
        <StatCell label="Cost" value={s ? fmtUsd(s.cost) : "—"} />
      </div>
      {/* compact key pool preview */}
      {props.p.keys.length > 0 && (
        <div className="space-y-0.5 px-4 py-3">
          {props.p.keys.slice(0, 3).map((k) => (
            <div key={k.label} className="flex items-center justify-between text-[12px]">
              <span className="font-medium">{k.label}</span>
              <div className="flex items-center gap-2">
                <Badge tone={STATUS_TONE[k.status]}>{k.status}</Badge>
                <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
                  {fmtInt(k.req_count)} reqs
                </span>
              </div>
            </div>
          ))}
          {props.p.keys.length > 3 && (
            <Link
              to={`/providers/${encodeURIComponent(props.p.name)}`}
              className="inline-block pt-1 text-[11px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
            >
              +{props.p.keys.length - 3} more…
            </Link>
          )}
        </div>
      )}

      <Dialog open={delOpen} title={`Delete provider ${props.p.name}?`} onClose={() => setDelOpen(false)}>
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
    </Card>
  );
}

function StatCell(props: { label: string; value: string; tone?: "danger" }) {
  return (
    <div className="bg-[var(--admin-surface)] px-3 py-2.5">
      <p className="admin-label mb-0.5">{props.label}</p>
      <p
        className={`font-mono text-[15px] tabular-nums ${
          props.tone === "danger" ? "text-red-400" : "text-[var(--admin-text)]"
        }`}
      >
        {props.value}
      </p>
    </div>
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
  const logsQuery = useQuery({ queryKey: ["request-logs"], queryFn: getRequestLogs, refetchInterval: 15_000 });

  const statsByProvider = useMemo(
    () => computeProviderStats(logsQuery.data?.logs ?? []),
    [logsQuery.data],
  );

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
          <ProviderCard
            key={p.name}
            p={p}
            stats={statsByProvider.get(p.name)}
            onError={setError}
          />
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
