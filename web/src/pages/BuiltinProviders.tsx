// Built-in Providers catalog — the provider types that ship with wiwi,
// separate from configured accounts. Each card shows the provider type,
// default endpoint, and a quick "Add account" shortcut.

import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ExternalLink, Plus, RefreshCw, Server } from "lucide-react";
import { getBuiltinProviders, getProviders } from "@/api/client";
import type { BuiltinProvider, Provider } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  PageHeader,
  Spinner,
} from "@/components/ui";

const PROVIDER_LOGO: Record<string, string> = {
  openai: "/logos/openai.png",
  anthropic: "/logos/anthropic.png",
  gemini: "/logos/gemini.png",
  openrouter: "/logos/openrouter.png",
  "openai-compatible": "/logos/openai-compatible.png",
  gmicloud: "/logos/gmicloud.png",
  "nvidia-nim": "/logos/nvidia-nim.png",
};

function providerLogo(type: string): string | null {
  return PROVIDER_LOGO[type] ?? null;
}

function ProviderCatalogCard(props: {
  p: BuiltinProvider;
  configuredAccounts: Provider[];
}) {
  const logo = providerLogo(props.p.provider_type);
  const accounts = props.configuredAccounts;
  const accountCount = accounts.length;
  const totalKeys = accounts.reduce((acc, a) => acc + a.keys.length, 0);
  const anyHealthy = accounts.some((a) => a.healthy);

  return (
    <Card className="flex flex-col">
      {/* header */}
      <div className="flex items-start gap-3 border-b border-[var(--admin-border)] px-5 py-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02]">
          {logo ? (
            <img src={logo} alt={props.p.label} className="h-5 w-5 object-contain" />
          ) : (
            <Server size={16} className="text-[var(--admin-text-muted)]" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
              {props.p.label}
            </h3>
            {props.p.configured ? (
              <Badge tone="green">configured</Badge>
            ) : (
              <Badge tone="gray">not configured</Badge>
            )}
          </div>
          <code className="mt-0.5 block truncate font-mono text-[11px] text-[var(--admin-text-dim)]">
            {props.p.provider_type}
          </code>
        </div>
      </div>

      {/* body */}
      <div className="flex flex-1 flex-col gap-3 px-5 py-4">
        <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
          {props.p.description}
        </p>

        {/* latest models */}
        {props.p.latest_models.length > 0 && (
          <div className="space-y-1.5">
            <span className="admin-label">Latest models</span>
            <div className="flex flex-wrap gap-1">
              {props.p.latest_models.map((m) => (
                <code
                  key={m}
                  className="rounded border border-white/[0.06] bg-white/[0.02] px-1.5 py-0.5 font-mono text-[10px] text-[var(--admin-text-dim)]"
                >
                  {m}
                </code>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-[12px]">
            <span className="admin-label">Context window</span>
            <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
              {props.p.context_window}
            </span>
          </div>
          <div className="flex items-center justify-between text-[12px]">
            <span className="admin-label">Default endpoint</span>
            <code className="truncate pl-2 font-mono text-[11px] text-[var(--admin-text-dim)]">
              {props.p.default_base_url || "(custom URL)"}
            </code>
          </div>
          <div className="flex items-center justify-between text-[12px]">
            <span className="admin-label">Accounts</span>
            <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
              {accountCount}
            </span>
          </div>
          <div className="flex items-center justify-between text-[12px]">
            <span className="admin-label">Keys</span>
            <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
              {totalKeys}
            </span>
          </div>
        </div>

        {/* account list */}
        {accounts.length > 0 && (
          <div className="mt-1 space-y-0.5 border-t border-[var(--admin-border)] pt-3">
            {accounts.slice(0, 4).map((a) => (
              <Link
                key={a.name}
                to={`/providers/${encodeURIComponent(a.name)}`}
                className="flex items-center justify-between rounded-md px-1.5 py-1 text-[12px] transition-colors hover:bg-white/[0.02]"
              >
                <span className="flex items-center gap-2">
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      a.healthy ? "bg-emerald-400" : "bg-red-400"
                    }`}
                  />
                  <span className="font-medium text-[var(--admin-text)]">{a.name}</span>
                </span>
                <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
                  {a.keys.length} keys
                </span>
              </Link>
            ))}
            {accounts.length > 4 && (
              <p className="px-1.5 pt-1 text-[11px] text-[var(--admin-text-dim)]">
                +{accounts.length - 4} more…
              </p>
            )}
          </div>
        )}
      </div>

      {/* footer */}
      <div className="flex items-center justify-between gap-2 border-t border-[var(--admin-border)] px-5 py-3">
        <div className="flex items-center gap-2">
          {accountCount > 0 ? (
            <span className="flex items-center gap-1.5 text-[11px] text-[var(--admin-text-muted)]">
              <span
                className={`h-1.5 w-1.5 rounded-full ${anyHealthy ? "bg-emerald-400" : "bg-amber-400"}`}
              />
              {anyHealthy ? "healthy" : "unhealthy"}
            </span>
          ) : (
            <span className="text-[11px] text-[var(--admin-text-dim)]">No accounts</span>
          )}
          {props.p.docs_url && (
            <a
              href={props.p.docs_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-[11px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
            >
              <ExternalLink size={10} /> Docs
            </a>
          )}
        </div>
        <Link
          to={`/providers?type=${encodeURIComponent(props.p.provider_type)}`}
          className="inline-flex"
        >
          <Button variant="outline">
            <Plus size={13} /> Add account
          </Button>
        </Link>
      </div>
    </Card>
  );
}

export function BuiltinProvidersPage() {
  const catalogQuery = useQuery({
    queryKey: ["provider-catalog"],
    queryFn: getBuiltinProviders,
    refetchInterval: 30_000,
  });
  const providersQuery = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
    refetchInterval: 15_000,
  });

  const catalog = catalogQuery.data?.providers ?? [];
  const configured = providersQuery.data?.providers ?? [];

  const accountsByType = new Map<string, Provider[]>();
  for (const a of configured) {
    const arr = accountsByType.get(a.provider_type);
    if (arr) arr.push(a);
    else accountsByType.set(a.provider_type, [a]);
  }

  const configuredCount = catalog.filter((p) => p.configured).length;
  const totalAccounts = configured.length;

  return (
    <div>
      <PageHeader
        title="Built-in Providers"
        subtitle="Provider types that ship with wiwi — the catalog you can configure accounts against."
        right={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => {
                void catalogQuery.refetch();
                void providersQuery.refetch();
              }}
            >
              <RefreshCw size={14} /> Refresh
            </Button>
            <Link to="/console/providers">
              <Button variant="ghost">
                View configured <ArrowRight size={14} />
              </Button>
            </Link>
          </div>
        }
      />

      {/* summary strip */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card className="px-4 py-3">
          <p className="admin-label">Catalog types</p>
          <p className="mt-1 font-mono text-[18px] font-semibold tabular-nums text-[var(--admin-text)]">
            {catalog.length}
          </p>
        </Card>
        <Card className="px-4 py-3">
          <p className="admin-label">Configured</p>
          <p className="mt-1 font-mono text-[18px] font-semibold tabular-nums text-emerald-400">
            {configuredCount}
          </p>
        </Card>
        <Card className="px-4 py-3">
          <p className="admin-label">Accounts</p>
          <p className="mt-1 font-mono text-[18px] font-semibold tabular-nums text-[var(--admin-text)]">
            {totalAccounts}
          </p>
        </Card>
        <Card className="px-4 py-3">
          <p className="admin-label">Total keys</p>
          <p className="mt-1 font-mono text-[18px] font-semibold tabular-nums text-[var(--admin-text)]">
            {configured.reduce((acc, a) => acc + a.keys.length, 0)}
          </p>
        </Card>
      </div>

      {catalogQuery.error && <ErrorText>{catalogQuery.error.message}</ErrorText>}

      {catalogQuery.isLoading ? (
        <Spinner />
      ) : catalog.length === 0 ? (
        <Card>
          <EmptyState>No built-in providers found.</EmptyState>
        </Card>
      ) : (
        <div className="admin-stagger grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {catalog.map((p) => (
            <ProviderCatalogCard
              key={p.provider_type}
              p={p}
              configuredAccounts={accountsByType.get(p.provider_type) ?? []}
            />
          ))}
        </div>
      )}
    </div>
  );
}
