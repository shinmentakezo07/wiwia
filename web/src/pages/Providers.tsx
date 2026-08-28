// Providers page — bento box layout with provider-type icons, health
// indicators, accent stat cells, error-rate bars, and stagger entrance.

import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  Boxes,
  Cpu,
  Cloud,
  DollarSign,
  Globe,
  Layers,
  Link2,
  Pencil,
  Plus,
  RefreshCw,
  Server,
  Sparkles,
  Trash2,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
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
import { fmtInt, fmtPct, fmtTokens, fmtUsd } from "@/lib/format";

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
  cline: Link2,
};

const PROVIDER_TYPE_OPTIONS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
  { value: "nvidia-nim", label: "NVIDIA NIM" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "gmicloud", label: "GMI Cloud" },
  { value: "cline", label: "Cline" },
  { value: "openai-compatible", label: "OpenAI-compatible URL" },
];

function providerIcon(type: string): LucideIcon {
  return PROVIDER_ICON[type] ?? Server;
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

function HealthDot({ healthy }: { healthy: boolean }) {
  return (
    <span className="relative flex h-2 w-2">
      {healthy && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
      )}
      <span
        className={`relative inline-flex h-2 w-2 rounded-full ${
          healthy ? "bg-emerald-400" : "bg-red-400"
        }`}
      />
    </span>
  );
}

function StatCell(props: {
  icon: LucideIcon;
  label: string;
  value: string;
  tone?: "danger" | "accent" | "success";
}) {
  const Icon = props.icon;
  const color =
    props.tone === "danger"
      ? "text-red-400"
      : props.tone === "accent"
        ? "text-blue-400"
        : props.tone === "success"
          ? "text-emerald-400"
          : "text-[var(--admin-text)]";
  return (
    <div className="group/cell relative overflow-hidden px-3 py-2.5 transition-colors hover:bg-white/[0.015]">
      <div className="mb-1 flex items-center gap-1.5">
        <Icon size={11} className="text-[var(--admin-text-dim)]" />
        <span className="admin-label">{props.label}</span>
      </div>
      <p className={`font-mono text-[15px] font-semibold tabular-nums ${color}`}>
        {props.value}
      </p>
    </div>
  );
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
  const errorRate = s && s.requests > 0 ? s.errors / s.requests : 0;
  const cacheRate = s && s.tokIn > 0 ? s.tokCached / s.tokIn : 0;
  const Icon = providerIcon(props.p.provider_type);

  return (
    <Card>
      {/* header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--admin-border)] px-4 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02]">
            <Icon size={14} className="text-[var(--admin-text-muted)]" />
          </div>
          <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            {props.p.name}
          </h3>
          <Badge tone="blue">{props.p.provider_type}</Badge>
          <div className="flex items-center gap-1.5">
            <HealthDot healthy={props.p.healthy} />
            <span className="text-[11px] text-[var(--admin-text-muted)]">
              {props.p.healthy ? "healthy" : "no healthy keys"}
            </span>
          </div>
          <Badge tone="gray">
            {activeKeys}/{totalKeys} keys
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to={`/console/providers/${encodeURIComponent(props.p.name)}`}
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

      {/* base url */}
      <div className="flex items-center gap-2 px-4 pb-2 pt-2 font-mono text-[11px] text-[var(--admin-text-dim)]">
        <span className="h-1 w-1 rounded-full bg-[var(--admin-text-dim)]" />
        {props.p.base_url || "(default endpoint)"}
      </div>

      {/* bento stats grid */}
      <div className="grid grid-cols-2 divide-x divide-y divide-[var(--admin-border)] border-t border-[var(--admin-border)] sm:grid-cols-3 lg:grid-cols-6 sm:divide-y-0">
        <StatCell icon={Server} label="Requests" value={s ? fmtInt(s.requests) : "—"} tone="accent" />
        <StatCell
          icon={AlertTriangle}
          label="Errors"
          value={s ? fmtInt(s.errors) : "—"}
          tone={s && s.errors > 0 ? "danger" : undefined}
        />
        <StatCell icon={ArrowDownToLine} label="Tokens In" value={s ? fmtTokens(s.tokIn) : "—"} />
        <StatCell icon={ArrowUpFromLine} label="Tokens Out" value={s ? fmtTokens(s.tokOut) : "—"} />
        <StatCell
          icon={Zap}
          label="Cached"
          value={s ? fmtTokens(s.tokCached) : "—"}
          tone={cacheRate > 0.1 ? "success" : undefined}
        />
        <StatCell icon={DollarSign} label="Cost" value={s ? fmtUsd(s.cost) : "—"} />
      </div>

      {/* error-rate bar */}
      {s && s.requests > 0 && (
        <div className="flex items-center gap-3 px-4 py-2">
          <span className="admin-label whitespace-nowrap">Error rate</span>
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-white/[0.04]">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                errorRate > 0.1
                  ? "bg-red-400"
                  : errorRate > 0.03
                    ? "bg-amber-400"
                    : "bg-emerald-400"
              }`}
              style={{ width: `${Math.max(2, errorRate * 100)}%` }}
            />
          </div>
          <span className="font-mono text-[11px] tabular-nums text-[var(--admin-text-muted)]">
            {fmtPct(errorRate)}
          </span>
        </div>
      )}

      {/* compact key pool preview */}
      {props.p.keys.length > 0 && (
        <div className="space-y-0.5 border-t border-[var(--admin-border)] px-4 py-3">
          {props.p.keys.slice(0, 3).map((k) => (
            <div key={k.label} className="flex items-center justify-between text-[12px]">
              <div className="flex items-center gap-2">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    k.status === "active"
                      ? "bg-emerald-400"
                      : k.status === "cooling"
                        ? "bg-amber-400"
                        : k.status === "invalid"
                          ? "bg-red-400"
                          : "bg-zinc-600"
                  }`}
                />
                <span className="font-medium text-[var(--admin-text)]">{k.label}</span>
              </div>
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
              to={`/console/providers/${encodeURIComponent(props.p.name)}`}
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

export function ProvidersPage() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [searchParams] = useSearchParams();
  const [addOpen, setAddOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("openai-compatible");
  const [baseUrl, setBaseUrl] = useState("");
  const [label, setLabel] = useState("default");
  const [secret, setSecret] = useState("");

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
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
            Providers
          </h2>
          {query.data && (
            <p className="mt-0.5 font-mono text-[11px] text-[var(--admin-text-dim)]">
              {query.data.providers.length} account{query.data.providers.length === 1 ? "" : "s"} ·{" "}
              {query.data.providers.reduce((acc, p) => acc + p.keys.length, 0)} total keys
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Link to="/console/builtin-providers" className="inline-flex">
            <Button variant="ghost">
              <Layers size={14} /> Catalog
            </Button>
          </Link>
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
      {query.isLoading && (
        <div className="space-y-4">
          {[0, 1].map((i) => (
            <div
              key={i}
              className="admin-skeleton h-[180px] rounded-[14px]"
            />
          ))}
        </div>
      )}
      {query.error && <ErrorText>{query.error.message}</ErrorText>}
      <div className="admin-stagger space-y-4">
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
              options={PROVIDER_TYPE_OPTIONS}
            />
          </Field>
          <Field label="Base URL" hint="Optional for openai/anthropic/gemini/openrouter/gmicloud/nvidia-nim. Required for compatible URLs.">
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
