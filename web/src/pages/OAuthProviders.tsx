// OAuth Providers — manage Cline (and future OAuth-backed) providers.
//
// This page lists every configured provider whose type supports OAuth (today:
// "cline"). For each you can:
//   • Add a new OAuth-backed provider (creates the account with a placeholder
//     key, since the real access token lands on that key after connect).
//   • Connect — get the login URL, paste the code Cline returns.
//   • See connection status (email, token expiry, auto-refresh badge).
//   • Refresh the access token on demand.
//   • Disconnect — clear the stored OAuth state.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Link2,
  LogIn,
  Plus,
  RefreshCw,
  Unlink,
} from "lucide-react";
import {
  addProvider,
  clineConnect,
  clineDisconnect,
  clineLoginUrl,
  clineRefresh,
  clineStatus,
  getProviders,
} from "@/api/client";
import type { ClineStatusResponse, Provider } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorText,
  Input,
  PageHeader,
  Spinner,
} from "@/components/ui";

const OAUTH_PROVIDER_TYPES = ["cline"];
const CLINE_DEFAULT_BASE = "https://api.cline.bot/api/v1";

function fmtExpiry(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

// -- add-provider dialog -------------------------------------------------------

function AddOAuthProviderDialog(props: {
  open: boolean;
  onClose: () => void;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");

  const create = useMutation({
    mutationFn: () =>
      // Cline requires at least one pool key to exist before connect can land
      // a token on it. We create a placeholder key — connect replaces it.
      addProvider({
        name: name.trim(),
        provider_type: "cline",
        base_url: CLINE_DEFAULT_BASE,
        label: "cline-oauth",
        key: "workos:placeholder",
      }),
    onSuccess: () => {
      setName("");
      void qc.invalidateQueries({ queryKey: ["providers"] });
      props.onClose();
    },
    onError: (e) => props.onError(e.message),
  });

  return (
    <Dialog open={props.open} title="Add Cline provider" onClose={props.onClose}>
      <p className="text-[13px] text-[var(--admin-text-muted)]">
        Creates a new provider account with a placeholder key. After creating,
        use the connect flow to replace it with your Cline access token.
      </p>
      <div className="mt-4 space-y-3">
        <Input
          value={name}
          placeholder="cline-account"
          autoFocus
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim() && !create.isPending) {
              create.mutate();
            }
          }}
        />
        {create.error && <ErrorText>{create.error.message}</ErrorText>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={props.onClose}>
            Cancel
          </Button>
          <Button disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? "Creating…" : "Create provider"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// -- per-provider OAuth card ---------------------------------------------------

function OAuthProviderCard(props: { p: Provider }) {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const qKey = ["cline-oauth-status", props.p.name];

  const statusQ = useQuery({
    queryKey: qKey,
    queryFn: () => clineStatus(props.p.name),
    refetchInterval: 30_000,
  });

  const status: ClineStatusResponse | undefined = statusQ.data;
  const connected = status?.connected === true;
  const needsRefresh = status?.needs_refresh === true;
  const hasKeys = props.p.keys.length > 0;

  const loginUrl = useMutation({
    mutationFn: () => clineLoginUrl(`${window.location.origin}/console/oauth`),
    onSuccess: (d) => setAuthUrl(d.auth_url),
    onError: (e) => setError(e.message),
  });

  const connect = useMutation({
    mutationFn: () => clineConnect(props.p.name, code.trim()),
    onSuccess: () => {
      setCode("");
      setAuthUrl(null);
      setError(null);
      void qc.invalidateQueries({ queryKey: qKey });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => setError(e.message),
  });

  const refresh = useMutation({
    mutationFn: () => clineRefresh(props.p.name),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: qKey });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => setError(e.message),
  });

  const disconnect = useMutation({
    mutationFn: () => clineDisconnect(props.p.name),
    onSuccess: () => {
      setConfirmDisconnect(false);
      setError(null);
      void qc.invalidateQueries({ queryKey: qKey });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => setError(e.message),
  });

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Link2 size={15} className="text-[var(--admin-text-muted)]" />
            <Link
              to={`/console/providers/${encodeURIComponent(props.p.name)}`}
              className="transition-colors hover:text-blue-300"
            >
              {props.p.name}
            </Link>
          </span>
        }
        right={
          <div className="flex items-center gap-2">
            {statusQ.isLoading ? (
              <Badge tone="gray">…</Badge>
            ) : connected ? (
              <Badge tone={needsRefresh ? "amber" : "green"}>
                {needsRefresh ? "expires soon" : "connected"}
              </Badge>
            ) : (
              <Badge tone="gray">not connected</Badge>
            )}
          </div>
        }
      />
      <div className="space-y-3 px-4 pb-4 pt-2">
        {error && <ErrorText>{error}</ErrorText>}

        {!hasKeys && (
          <div className="flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/[0.04] px-3 py-2 text-[12px] text-amber-300">
            <AlertTriangle size={14} />
            No pool keys — add a key on the provider detail page before connecting.
          </div>
        )}

        {/* connected status */}
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

        {/* not connected: login + paste code */}
        {!connected && (
          <>
            <div className="space-y-2">
              <p className="text-[12px] text-[var(--admin-text-muted)]">
                Step 1 — open the Cline login page, sign in, then copy the code
                back here.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  disabled={loginUrl.isPending}
                  onClick={() => loginUrl.mutate()}
                >
                  <LogIn size={14} /> {loginUrl.isPending ? "Generating…" : "Get login URL"}
                </Button>
                {authUrl && (
                  <>
                    <a href={authUrl} target="_blank" rel="noopener noreferrer">
                      <Button variant="primary">
                        <Link2 size={14} /> Open Cline login
                      </Button>
                    </a>
                    <Button
                      variant="ghost"
                      onClick={() => void navigator.clipboard.writeText(authUrl)}
                    >
                      Copy URL
                    </Button>
                  </>
                )}
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-[12px] text-[var(--admin-text-muted)]">
                Step 2 — paste the code Cline gave you.
              </p>
              <textarea
                className="admin-input min-h-20 resize-none font-mono text-[12px]"
                placeholder="eyJhY2Nlc3NUb2tlbiI6…"
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
          </>
        )}

        {/* connected: refresh + disconnect */}
        {connected && (
          <div className="space-y-2 border-t border-[var(--admin-border)] pt-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                disabled={refresh.isPending}
                onClick={() => refresh.mutate()}
              >
                <RefreshCw size={14} className={refresh.isPending ? "animate-spin" : ""} />
                {refresh.isPending ? "Refreshing…" : "Refresh token"}
              </Button>
              <Button
                variant="ghost"
                onClick={() => void statusQ.refetch()}
              >
                <RefreshCw size={14} /> Recheck
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
          title={`Disconnect ${props.p.name}?`}
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
      </div>
    </Card>
  );
}

// -- page --------------------------------------------------------------------

export function OAuthProvidersPage() {
  const [addOpen, setAddOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
    refetchInterval: 15_000,
  });

  const oauthProviders = useMemo(
    () =>
      (q.data?.providers ?? [])
        .filter((p) => OAUTH_PROVIDER_TYPES.includes(p.provider_type))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [q.data],
  );

  return (
    <div>
      <PageHeader
        title="OAuth Providers"
        subtitle="connect and manage Cline and other OAuth-backed accounts"
        right={
          <Button onClick={() => setAddOpen(true)}>
            <Plus size={14} /> Add Cline provider
          </Button>
        }
      />
      {error && <div className="mb-3"><ErrorText>{error}</ErrorText></div>}

      <AddOAuthProviderDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onError={setError}
      />

      {q.isLoading ? (
        <Spinner />
      ) : oauthProviders.length === 0 ? (
        <Card>
          <EmptyState>
            No OAuth providers configured yet. Click "Add Cline provider" to
            get started.
          </EmptyState>
        </Card>
      ) : (
        <div className="admin-stagger grid grid-cols-1 gap-4 lg:grid-cols-2">
          {oauthProviders.map((p) => (
            <OAuthProviderCard key={p.name} p={p} />
          ))}
        </div>
      )}
    </div>
  );
}
