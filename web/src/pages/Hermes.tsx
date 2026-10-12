// Hermes (Nous Portal) — manage Hermes provider accounts.
//
// Hermes is OpenAI Chat Completions-compatible (no OAuth, no per-account auth
// surface), so there is no credential-import flow like WorkBuddy's. This page
// is a focused management surface over the generic provider API: it lists every
// configured Hermes account with its key pool health, offers a shortcut to add
// one (delegating to the shared Add-provider dialog via /console/providers?type=hermes),
// and surfaces the endpoint + model IDs from the built-in catalog so an admin
// knows what to point a model group at.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, ExternalLink, Plus, RefreshCw, Server } from "lucide-react";
import { deleteProvider, getProviders } from "@/api/client";
import type { PoolKey, Provider } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  PageHeader,
  Spinner,
} from "@/components/ui";

const STATUS_TONE: Record<PoolKey["status"], "green" | "amber" | "red" | "gray"> = {
  active: "green",
  cooling: "amber",
  invalid: "red",
  disabled: "gray",
};

const DEFAULT_BASE_URL = "https://inference-api.nousresearch.com/v1";

function HermesAccountRow(props: { p: Provider; onDeleted: () => void; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const del = useMutation({
    mutationFn: () => deleteProvider(props.p.name),
    onSuccess: () => {
      props.onDeleted();
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });
  const p = props.p;
  const activeKeys = p.keys.filter((k) => k.enabled && k.status === "active").length;
  return (
    <Card className="flex flex-col">
      <div className="flex items-start gap-3 border-b border-[var(--admin-border)] px-5 py-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02]">
          <Server size={16} className="text-[var(--admin-text-muted)]" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Link
              to={`/console/providers/${encodeURIComponent(p.name)}`}
              className="truncate text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)] transition-colors hover:text-blue-400"
            >
              {p.name}
            </Link>
            {p.healthy ? (
              <Badge tone="green">healthy</Badge>
            ) : (
              <Badge tone="red">unhealthy</Badge>
            )}
          </div>
          <code className="mt-0.5 block truncate font-mono text-[11px] text-[var(--admin-text-dim)]">
            {p.base_url || "(default endpoint)"}
          </code>
        </div>
      </div>
      <div className="flex flex-1 flex-col gap-2 px-5 py-4">
        <div className="flex items-center justify-between text-[12px]">
          <span className="admin-label">Keys</span>
          <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
            {activeKeys} active / {p.keys.length}
          </span>
        </div>
        {p.alias_id && (
          <div className="flex items-center justify-between text-[12px]">
            <span className="admin-label">Alias</span>
            <code className="font-mono text-[11px] text-[var(--admin-text-dim)]">{p.alias_id}</code>
          </div>
        )}
        <div className="flex flex-wrap gap-1 pt-1">
          {p.keys.slice(0, 6).map((k) => (
            <Badge key={k.label} tone={STATUS_TONE[k.status]}>
              {k.label}
            </Badge>
          ))}
          {p.keys.length > 6 && (
            <span className="text-[11px] text-[var(--admin-text-dim)]">+{p.keys.length - 6} more…</span>
          )}
        </div>
      </div>
      <div className="flex items-center justify-between gap-2 border-t border-[var(--admin-border)] px-5 py-3">
        <Link to={`/console/providers/${encodeURIComponent(p.name)}`} className="inline-flex">
          <Button variant="ghost">
            Manage keys <ArrowRight size={13} />
          </Button>
        </Link>
        <Button
          variant="ghost"
          disabled={del.isPending}
          onClick={() => del.mutate()}
        >
          {del.isPending ? "Deleting…" : "Delete"}
        </Button>
      </div>
    </Card>
  );
}

export function HermesPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["providers"], queryFn: getProviders, refetchInterval: 15_000 });
  const [error, setError] = useState<string | null>(null);

  const accounts = (q.data?.providers ?? []).filter((p) => p.provider_type === "hermes");

  return (
    <div>
      <PageHeader
        title="Hermes"
        subtitle="Nous Portal inference accounts — OpenAI Chat Completions-compatible"
        right={
          <div className="flex flex-wrap gap-2">
            <Link to="/providers?type=hermes" className="inline-flex">
              <Button variant="outline">
                <Plus size={14} /> Add account
              </Button>
            </Link>
            <Button
              variant="ghost"
              disabled={q.isFetching}
              onClick={() => void qc.invalidateQueries({ queryKey: ["providers"] })}
            >
              <RefreshCw size={14} /> Refresh
            </Button>
            <a
              href="https://hermes-agent.nousresearch.com/docs/"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex"
            >
              <Button variant="ghost">
                <ExternalLink size={14} /> Docs
              </Button>
            </a>
          </div>
        }
      />

      <Card className="mb-4 px-4 py-3">
        <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
          Authenticate with a Nous API key or an OAuth token from Hermes Agent. The default
          endpoint is <code className="font-mono text-[11px]">{DEFAULT_BASE_URL}</code>; models
          include Hermes-4-405B, Hermes-4-70B and Hermes-4.3-36B. Point a model group at an
          account below to route traffic through the gateway.
        </p>
      </Card>

      {error && (
        <div className="mb-3">
          <ErrorText>{error}</ErrorText>
        </div>
      )}

      {q.isError ? (
        <ErrorText>{q.error.message}</ErrorText>
      ) : q.isLoading ? (
        <Spinner />
      ) : accounts.length === 0 ? (
        <Card>
          <EmptyState>
            No Hermes accounts yet. Add one to start routing through Nous Portal.
          </EmptyState>
        </Card>
      ) : (
        <div className="admin-stagger grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {accounts.map((p) => (
            <HermesAccountRow key={p.name} p={p} onDeleted={() => undefined} onError={setError} />
          ))}
        </div>
      )}
    </div>
  );
}
