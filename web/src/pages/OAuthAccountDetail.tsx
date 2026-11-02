// OAuth account detail — the edit page behind `/console/oauth/:provider/:account`.
//
// Cline treats one provider as one account, so `account` repeats the provider
// name and the page shows the provider settings plus its key pool. WorkBuddy
// and Hermes store one account per pool key, so `account` is that key's label
// and the page shows that identity, its refresh state, and its pool weight.
//
// Models are deliberately not added per account here. `DefaultModelsCard`
// (Cline) and `SharedModelsCard` (WorkBuddy/Hermes) add one model id to every
// account of the type at once; `DeploymentsCard` then shows and detaches what
// this provider actually serves.

import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Briefcase,
  ExternalLink,
  Hexagon,
  Link2,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";
import {
  deleteProviderKey,
  getProviders,
  patchProviderKey,
  workbuddyAccounts,
} from "@/api/client";
import type { Provider } from "@/api/types";
import {
  HermesAccountList,
  useHermesAccountActions,
  useHermesStatusQuery,
} from "@/components/HermesAccounts";
import {
  AccountSettingsCard,
  DeploymentsCard,
  KeyPoolCard,
} from "@/pages/ProviderDetail";
import { DefaultModelsCard } from "@/pages/oauth-routing";
import { SharedModelsCard } from "@/pages/oauth-models";
import { WorkBuddyAccountsTable } from "@/pages/workbuddy-shared";
import {
  Alert,
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
  Spinner,
  Toggle,
} from "@/components/ui";

const TYPE_META: Record<
  string,
  { label: string; prefix: string; icon: typeof Link2 }
> = {
  cline: { label: "Cline", prefix: "cline", icon: Link2 },
  workbuddy: { label: "WorkBuddy", prefix: "workbuddy", icon: Briefcase },
  hermes: { label: "Hermes", prefix: "hermes", icon: Hexagon },
};

// -- one pool-key account (WorkBuddy / Hermes) ---------------------------------

function KeyAccountSettingsCard(props: {
  p: Provider;
  keyLabel: string;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const key = props.p.keys.find((k) => k.label === props.keyLabel);
  const [weight, setWeight] = useState(String(key?.weight ?? 1));
  const [enabled, setEnabled] = useState(key?.enabled ?? true);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const save = useMutation({
    mutationFn: () =>
      patchProviderKey(props.p.name, props.keyLabel, {
        weight: Math.max(1, parseInt(weight, 10) || 1),
        enabled,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["providers"] }),
    onError: (e) => props.onError(e.message),
  });

  const del = useMutation({
    mutationFn: () => deleteProviderKey(props.p.name, props.keyLabel),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["providers"] });
      navigate(`/console/oauth/${props.p.provider_type}`);
    },
    onError: (e) => props.onError(e.message),
  });

  if (!key) {
    return (
      <Card>
        <EmptyState>
          This account is no longer in the provider&apos;s key pool. It may have been
          disconnected or deleted elsewhere.
        </EmptyState>
      </Card>
    );
  }

  const dirty = weight !== String(key.weight) || enabled !== key.enabled;

  return (
    <Card>
      <CardHeader
        title="Account settings"
        subtitle="Weight and availability of this pool key"
        right={
          <Badge tone={key.enabled ? "green" : "gray"}>
            {key.enabled ? "enabled" : "disabled"}
          </Badge>
        }
      />
      <div className="space-y-3 px-5 pb-5 pt-4">
        <Field label="Key label">
          <Input value={key.label} readOnly className="font-mono" />
        </Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field
            label="Weight"
            hint="Higher weights take more traffic within this provider's pool."
          >
            <Input
              type="number"
              min={1}
              value={weight}
              onChange={(e) => setWeight(e.target.value)}
            />
          </Field>
          <div className="flex items-end gap-2 pb-1">
            <Toggle label="Enabled" checked={enabled} onChange={setEnabled} />
            <span className="pb-0.5 text-[12px] text-[var(--admin-text-muted)]">
              {enabled ? "Receives traffic" : "Excluded from the pool"}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--admin-border)] pt-4">
          <Button
            variant="danger"
            className="min-h-11"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 size={14} aria-hidden /> Delete account
          </Button>
          <Button
            className="min-h-11"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? (
              <Loader2 size={14} aria-hidden className="animate-spin motion-reduce:animate-none" />
            ) : null}
            {save.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </div>

      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        icon={Trash2}
        title={`Delete ${key.label}?`}
        subtitle="This removes the key from the live pool."
        footer={
          <>
            <Button variant="ghost" className="min-h-11" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              className="min-h-11"
              disabled={del.isPending}
              onClick={() => del.mutate()}
            >
              {del.isPending ? (
                <Loader2 size={14} aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : (
                <Trash2 size={14} aria-hidden />
              )}
              {del.isPending ? "Deleting…" : "Delete"}
            </Button>
          </>
        }
      >
        <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
          Requests routed to this account will stop working. Model groups that
          referenced the provider stay in place; only this pool key is removed.
        </p>
      </Dialog>
    </Card>
  );
}

// -- WorkBuddy account ---------------------------------------------------------

function WorkBuddyAccountSection(props: {
  p: Provider;
  account: string;
  onError: (m: string) => void;
}) {
  const q = useQuery({
    queryKey: ["workbuddy-accounts"],
    queryFn: workbuddyAccounts,
    refetchInterval: 15_000,
  });
  const account = useMemo(
    () =>
      (q.data?.accounts ?? []).find(
        (a) =>
          a.provider === props.p.name &&
          (a.label === props.account || a.nickname === props.account),
      ),
    [q.data, props.p.name, props.account],
  );

  if (q.isLoading) {
    return (
      <Card>
        <Spinner />
      </Card>
    );
  }
  if (q.isError) {
    return (
      <Card>
        <div className="px-5 py-4">
          <ErrorText>{q.error.message}</ErrorText>
        </div>
      </Card>
    );
  }
  if (!account) {
    return (
      <Card>
        <EmptyState>
          This WorkBuddy account was not found. It may have been removed or moved
          to a different provider.
        </EmptyState>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardHeader
          title="WorkBuddy account"
          subtitle={`${account.provider} · ${account.label}`}
        />
        <div className="px-5 pb-5 pt-4">
          <WorkBuddyAccountsTable accounts={[account]} onError={props.onError} />
        </div>
      </Card>
      <KeyAccountSettingsCard
        p={props.p}
        keyLabel={account.label}
        onError={props.onError}
      />
    </>
  );
}

// -- Hermes account ------------------------------------------------------------

function HermesAccountSection(props: {
  p: Provider;
  account: string;
  onError: (m: string | null) => void;
}) {
  const statusQ = useHermesStatusQuery(props.p.name);
  const [notice, setNotice] = useState<string | null>(null);
  const actions = useHermesAccountActions(props.p.name, {
    onError: props.onError,
    onNotice: setNotice,
  });
  const account = useMemo(
    () =>
      (statusQ.data?.accounts ?? []).find(
        (a) => a.key_label === props.account || a.label === props.account,
      ),
    [statusQ.data, props.account],
  );

  if (statusQ.isLoading) {
    return (
      <Card>
        <Spinner />
      </Card>
    );
  }
  if (statusQ.isError) {
    return (
      <Card>
        <div className="px-5 py-4">
          <ErrorText>{statusQ.error.message}</ErrorText>
        </div>
      </Card>
    );
  }
  if (!account) {
    return (
      <Card>
        <EmptyState>
          This Hermes account was not found. It may have been disconnected or
          removed from the provider&apos;s key pool.
        </EmptyState>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardHeader
          title="Hermes account"
          subtitle={`${props.p.name} · ${account.key_label}`}
        />
        <div className="space-y-3 px-5 pb-5 pt-4">
          {notice && <p className="text-[12px] text-emerald-400">{notice}</p>}
          <HermesAccountList
            accounts={[account]}
            actions={actions}
            provider={props.p.name}
            disconnectVariant="danger"
          />
        </div>
      </Card>
      <KeyAccountSettingsCard
        p={props.p}
        keyLabel={account.key_label}
        onError={props.onError}
      />
    </>
  );
}

// -- page ----------------------------------------------------------------------

export function OAuthAccountDetailPage() {
  const { provider = "", account = "" } = useParams();
  const [error, setError] = useState<string | null>(null);
  const providersQ = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
    refetchInterval: 15_000,
  });

  const p = providersQ.data?.providers.find((x) => x.name === provider);
  const meta = p ? TYPE_META[p.provider_type] : undefined;
  const sameType = useMemo(
    () =>
      (providersQ.data?.providers ?? []).filter(
        (x) => x.provider_type === p?.provider_type,
      ),
    [providersQ.data, p?.provider_type],
  );

  if (providersQ.isLoading) return <Spinner />;
  if (providersQ.isError) {
    return (
      <div>
        <PageHeader title={provider || "Account"} subtitle="Failed to load accounts." />
        <ErrorText>{providersQ.error.message}</ErrorText>
        <Link to="/console/oauth">
          <Button variant="outline">
            <ArrowLeft size={14} aria-hidden /> Back to connected accounts
          </Button>
        </Link>
      </div>
    );
  }
  if (!p) {
    return (
      <div>
        <PageHeader title={provider || "Account"} subtitle="Unknown OAuth account." />
        <Link to="/console/oauth">
          <Button variant="outline">
            <ArrowLeft size={14} aria-hidden /> Back to connected accounts
          </Button>
        </Link>
      </div>
    );
  }
  if (!meta) {
    return (
      <div>
        <PageHeader
          title={p.name}
          subtitle={`${p.provider_type} is not an OAuth account type.`}
        />
        <Link to={`/console/providers/${encodeURIComponent(p.name)}`}>
          <Button variant="outline">
            <ArrowLeft size={14} aria-hidden /> Open the provider page
          </Button>
        </Link>
      </div>
    );
  }

  const Icon = meta.icon;
  const displayName =
    p.provider_type === "cline"
      ? p.name
      : p.keys.find((k) => k.label === account)?.label || account || p.name;

  return (
    <div>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            <Link
              to={`/console/oauth/${p.provider_type}`}
              aria-label={`Back to ${meta.label} accounts`}
              className="text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
            >
              <ArrowLeft size={18} />
            </Link>
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02]">
              <Icon size={15} className="text-[var(--admin-text-muted)]" />
            </span>
            <span className="min-w-0 break-all">{displayName}</span>
          </span>
        }
        subtitle={`${meta.label} · ${p.name}`}
        right={
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={p.healthy ? "green" : "red"}>
              {p.healthy ? "healthy" : "no healthy keys"}
            </Badge>
            <Button
              variant="outline"
              className="min-h-11"
              disabled={providersQ.isFetching}
              onClick={() => void providersQ.refetch()}
            >
              <RefreshCw
                size={14}
                aria-hidden
                className={`motion-reduce:animate-none ${providersQ.isFetching ? "animate-spin" : ""}`}
              />
              Refresh
            </Button>
            <Link to={`/console/providers/${encodeURIComponent(p.name)}`} className="inline-flex">
              <Button variant="ghost" className="min-h-11">
                <ExternalLink size={14} aria-hidden /> Full provider
              </Button>
            </Link>
          </div>
        }
      />

      {error && (
        <Alert tone="danger" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}

      <div className="space-y-4">
        {p.provider_type === "cline" ? (
          <>
            <AccountSettingsCard p={p} onError={setError} />
            <KeyPoolCard p={p} providerName={p.name} onError={setError} />
          </>
        ) : p.provider_type === "workbuddy" ? (
          <WorkBuddyAccountSection p={p} account={account} onError={setError} />
        ) : (
          <HermesAccountSection p={p} account={account} onError={setError} />
        )}

        {p.provider_type === "cline" ? (
          <DefaultModelsCard providers={sameType} onError={setError} />
        ) : (
          <SharedModelsCard
            providerType={p.provider_type}
            prefix={meta.prefix}
            label={meta.label}
            accountCount={sameType.reduce((n, x) => n + x.keys.length, 0)}
            providers={sameType}
            onError={setError}
          />
        )}

        <DeploymentsCard provider={p.name} onError={setError} />
      </div>
    </div>
  );
}
