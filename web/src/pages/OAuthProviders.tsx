// Connected accounts — manage the Cline OAuth accounts attached to this
// gateway, and the routing policy built on top of them.
//
// The connect flow is a guided three-step dialog, not a one-click button,
// because no one-click path exists: Cline's OAuth ignores the `callback_url`
// this gateway sends, so the callback must be copied back by hand. See the
// header of `oauth-shared.tsx` for the full account of that, and for why the
// redirect pipeline is kept dormant rather than deleted.
//
// Page order is deliberate: the accounts are the subject of this page and come
// first; routing and default models are configuration *about* those accounts and
// follow. The previous order led with the model catalog.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Loader2, Plus, RefreshCw, Unlink, Users } from "lucide-react";
import { addProvider, getProviders } from "@/api/client";
import type { Provider } from "@/api/types";
import {
  Alert,
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
} from "@/components/ui";
import type { ClineStatusQuery } from "@/pages/oauth-shared";
import {
  ClineCallbackAlert,
  ClineConnectButton,
  ClineStatusBadge,
  DisconnectDialog,
  StatusRefreshButton,
  fmtClineExpiry,
  useClineCallbackBanner,
  useClineRefresh,
  useClineStatusIndex,
} from "@/pages/oauth-shared";
import { CrossAccountRoutingCard, DefaultModelsCard } from "@/pages/oauth-routing";

const OAUTH_PROVIDER_TYPES = ["cline"];
const CLINE_DEFAULT_BASE = "https://api.cline.bot/api/v1";

// -- add account ----------------------------------------------------------------

function AddAccountDialog(props: {
  open: boolean;
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");

  const create = useMutation({
    mutationFn: () =>
      addProvider({
        name: name.trim(),
        provider_type: "cline",
        base_url: CLINE_DEFAULT_BASE,
        label: "cline-oauth",
        // Cline requires a pool key to exist before a connect can land a token
        // on it, so the account is created with a placeholder that the connect
        // flow replaces.
        key: "workos:placeholder",
      }),
    onSuccess: (d) => {
      setName("");
      void qc.invalidateQueries({ queryKey: ["providers"] });
      props.onCreated(d.name);
      props.onClose();
    },
  });

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      icon={Plus}
      title="Add account"
      subtitle="Creates an empty Cline account, then walks you through connecting it."
      footer={
        <>
          <Button variant="ghost" className="min-h-11" onClick={props.onClose}>
            Cancel
          </Button>
          <Button
            className="min-h-11"
            disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? (
              <Loader2 size={14} aria-hidden className="animate-spin motion-reduce:animate-none" />
            ) : (
              <Plus size={14} aria-hidden />
            )}
            {create.isPending ? "Creating…" : "Create account"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field
          label="Account name"
          hint="Used in model group names and deployment keys, so keep it short and lowercase."
        >
          <Input
            value={name}
            placeholder="cline-personal"
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && name.trim() && !create.isPending) create.mutate();
            }}
          />
        </Field>
        {create.error && <ErrorText>{create.error.message}</ErrorText>}
      </div>
    </Dialog>
  );
}

// -- account card ---------------------------------------------------------------

function AccountCard(props: {
  p: Provider;
  q: ClineStatusQuery;
  onError: (m: string) => void;
}) {
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const refresh = useClineRefresh(props.p.name, props.onError);
  const { data: status } = props.q;
  const connected = status?.connected === true;
  const hasKeys = props.p.keys.length > 0;

  return (
    <Card className="flex flex-col">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Link2 size={15} className="shrink-0 text-[var(--admin-text-muted)]" aria-hidden />
            <Link
              to={`/console/providers/${encodeURIComponent(props.p.name)}`}
              className="truncate transition-colors hover:text-blue-300 focus-visible:text-blue-300"
            >
              {props.p.name}
            </Link>
          </span>
        }
        right={<ClineStatusBadge q={props.q} />}
      />

      <div className="flex flex-1 flex-col gap-3 px-5 pb-5 pt-4">
        {!hasKeys && (
          <Alert tone="warning">
            This account has no pool keys, so there is nowhere for an access token to
            land. Add a key on the provider page before connecting.
          </Alert>
        )}
        {notice && <Alert tone="success">{notice}</Alert>}

        {connected ? (
          <>
            <dl className="admin-dl">
              <dt>Account</dt>
              <dd className="truncate font-mono text-[12px]">{status?.email ?? "—"}</dd>
              <dt>Token expires</dt>
              <dd className="font-mono text-[12px] text-[var(--admin-text-muted)]">
                {fmtClineExpiry(status?.expires_at)}
              </dd>
              <dt>Pool keys</dt>
              <dd className="font-mono text-[12px] text-[var(--admin-text-muted)]">
                {props.p.keys.length}
              </dd>
            </dl>

            <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-[var(--admin-border)] pt-4">
              <Button
                variant="ghost"
                className="min-h-11"
                disabled={refresh.isPending}
                onClick={() => refresh.mutate()}
              >
                <RefreshCw
                  size={14}
                  aria-hidden
                  className={`motion-reduce:animate-none ${refresh.isPending ? "animate-spin" : ""}`}
                />
                {refresh.isPending ? "Refreshing…" : "Refresh token"}
              </Button>
              <StatusRefreshButton q={props.q} />
              <Button
                variant="danger"
                className="min-h-11"
                onClick={() => setConfirmDisconnect(true)}
              >
                <Unlink size={14} aria-hidden />
                Disconnect
              </Button>
            </div>
          </>
        ) : (
          <div className="mt-auto flex flex-col items-start gap-2 border-t border-[var(--admin-border)] pt-4">
            <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
              {props.q.isError
                ? "The connection state could not be read, so this may still be connected. Retry the status check before reconnecting."
                : "No Cline account is connected here. Connecting takes three steps and opens Cline's login page in a new tab."}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <ClineConnectButton provider={props.p.name} hasKeys={hasKeys} />
              <StatusRefreshButton q={props.q} />
            </div>
          </div>
        )}
      </div>

      <DisconnectDialog
        open={confirmDisconnect}
        provider={props.p.name}
        onClose={() => setConfirmDisconnect(false)}
        onError={props.onError}
        onDisconnected={() => setNotice("Disconnected.")}
      />
    </Card>
  );
}

// -- page -----------------------------------------------------------------------

export function OAuthProvidersPage() {
  const [addOpen, setAddOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const banner = useClineCallbackBanner();

  const q = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
    refetchInterval: 15_000,
  });

  const providers = useMemo(
    () =>
      (q.data?.providers ?? [])
        .filter((p) => OAUTH_PROVIDER_TYPES.includes(p.provider_type))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [q.data],
  );

  const names = useMemo(() => providers.map((p) => p.name), [providers]);
  const { byName, connectedNames } = useClineStatusIndex(names);

  const attention = connectedNames.filter((n) => byName.get(n)?.data?.needs_refresh).length;

  return (
    <div>
      <PageHeader
        title="Connected accounts"
        subtitle="Cline OAuth accounts attached to this gateway"
        right={
          <Button className="min-h-11" onClick={() => setAddOpen(true)}>
            <Plus size={14} aria-hidden />
            Add account
          </Button>
        }
      />

      {error && <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert>}

      <ClineCallbackAlert result={banner.result} onDismiss={banner.clear} />

      {providers.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="admin-label">Accounts</h2>
          <p className="text-[12px] text-[var(--admin-text-muted)]">
            {providers.length} configured · {connectedNames.length} connected
            {attention > 0 ? ` · ${attention} expiring soon` : ""}
          </p>
        </div>
      )}

      {q.isLoading ? (
        <div className="flex items-center justify-center py-16" aria-busy>
          <Spinner />
        </div>
      ) : q.isError ? (
        <Alert
          tone="danger"
          title="Could not load accounts"
          action={
            <Button
              variant="ghost"
              className="min-h-11"
              disabled={q.isFetching}
              onClick={() => void q.refetch()}
            >
              <RefreshCw
                size={14}
                aria-hidden
                className={`motion-reduce:animate-none ${q.isFetching ? "animate-spin" : ""}`}
              />
              Retry
            </Button>
          }
        >
          {q.error.message}
        </Alert>
      ) : providers.length === 0 ? (
        <Card>
          <EmptyState
            icon={Users}
            title="No accounts yet"
            action={
              <Button className="min-h-11" onClick={() => setAddOpen(true)}>
                <Plus size={14} aria-hidden />
                Add account
              </Button>
            }
          >
            Add a Cline account to route requests through your Cline subscription. You will
            sign in at Cline and paste the callback URL back.
          </EmptyState>
        </Card>
      ) : (
        <>
          <div className="admin-stagger mt-4 grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
            {providers.map((p) => {
              const statusQ = byName.get(p.name);
              return statusQ ? (
                <AccountCard key={p.name} p={p} q={statusQ} onError={setError} />
              ) : null;
            })}
          </div>

          {connectedNames.length >= 2 && (
            <div className="mt-6">
              <CrossAccountRoutingCard
                providers={providers}
                connectedNames={connectedNames}
                onError={setError}
              />
            </div>
          )}

          {providers.length >= 1 && (
            <div className="mt-6">
              <DefaultModelsCard providers={providers} onError={setError} />
            </div>
          )}
        </>
      )}

      <AddAccountDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={() => setError(null)}
      />
    </div>
  );
}
