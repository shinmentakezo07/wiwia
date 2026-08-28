// OAuth Providers — manage Cline (and future OAuth-backed) providers.
//
// This page lists every configured provider whose type supports OAuth (today:
// "cline"). For each you can:
//   • Add a new OAuth-backed provider (creates the account with a placeholder
//     key, since the real access token lands on that key after connect).
//   • Connect — automatic redirect flow: click once, log in at Cline, done.
//     A manual paste-code fallback is available for setups without a public
//     callback URL (e.g. localhost dev).
//   • See connection status (email, token expiry, auto-refresh badge).
//   • Refresh the access token on demand.
//   • Disconnect — clear the stored OAuth state.

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Link2,
  Loader2,
  LogIn,
  Plus,
  RefreshCw,
  Unlink,
} from "lucide-react";
import {
  addProvider,
  clineAutoConnect,
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

// -- Cline OAuth callback banner ----------------------------------------------
// After the automatic redirect flow, Cline sends the browser back to this
// page with ?cline_connected=1&cline_email=… (or ?cline_error=…). This hook
// reads those params on mount, surfaces a one-shot banner, and cleans the
// URL so a refresh doesn't re-trigger the banner. Returns null when there
// is no callback result to show.

type CallbackResult =
  | { ok: true; provider: string; email: string }
  | { ok: false; provider: string; error: string };

function useClineCallbackBanner(): CallbackResult | null {
  const [searchParams, setSearchParams] = useSearchParams();
  const [result, setResult] = useState<CallbackResult | null>(null);
  const seen = useRef(false);
  useEffect(() => {
    if (seen.current) return;
    const connected = searchParams.get("cline_connected");
    const error = searchParams.get("cline_error");
    if (connected === "1") {
      seen.current = true;
      setResult({
        ok: true,
        provider: searchParams.get("cline_provider") ?? "",
        email: searchParams.get("cline_email") ?? "",
      });
      _stripClineParams(searchParams, setSearchParams);
    } else if (error) {
      seen.current = true;
      setResult({
        ok: false,
        provider: searchParams.get("cline_provider") ?? "",
        error,
      });
      _stripClineParams(searchParams, setSearchParams);
    }
  }, [searchParams, setSearchParams]);
  return result;
}

function _stripClineParams(
  searchParams: URLSearchParams,
  setSearchParams: (
    nextInit: string | URLSearchParams,
    opts?: { replace?: boolean },
  ) => void,
): void {
  const next = new URLSearchParams(searchParams);
  next.delete("cline_connected");
  next.delete("cline_email");
  next.delete("cline_error");
  next.delete("cline_provider");
  setSearchParams(next.size === 0 ? "" : next, { replace: true });
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

function OAuthProviderCard(props: {
  p: Provider;
  callbackResult: CallbackResult | null;
}) {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showManual, setShowManual] = useState(false);
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

  // Surface the redirect-callback result when it targets this provider. The
  // page-level hook already cleaned the URL; this banner is one-shot.
  const cb = props.callbackResult;

  // Automatic connect: POST auto-connect → open the returned Cline auth URL
  // in a new tab. After login, Cline redirects back to /cline/oauth/callback
  // which persists tokens and redirects here with ?cline_connected=1.
  const autoConnect = useMutation({
    mutationFn: () => clineAutoConnect(props.p.name, "/console/oauth"),
    onSuccess: (d) => {
      setError(null);
      window.open(d.auth_url, "_blank", "noopener,noreferrer");
    },
    onError: (e) => setError(e.message),
  });

  // Manual paste-code fallback (kept for localhost / no-public-URL setups).
  const loginUrl = useMutation({
    mutationFn: () => clineLoginUrl(`${window.location.origin}/console/oauth`),
    onSuccess: (d) => window.open(d.auth_url, "_blank", "noopener,noreferrer"),
    onError: (e) => setError(e.message),
  });

  const connect = useMutation({
    mutationFn: () => clineConnect(props.p.name, code.trim()),
    onSuccess: () => {
      setCode("");
      setShowManual(false);
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
        {/* callback result banner — one-shot after redirect flow */}
        {cb && (
          <div
            className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-[12px] ${
              cb.ok
                ? "border-emerald-500/20 bg-emerald-500/[0.04] text-emerald-300"
                : "border-red-500/20 bg-red-500/[0.04] text-red-300"
            }`}
          >
            {cb.ok ? (
              <>
                <CheckCircle2 size={14} />
                Connected{cb.email ? ` as ${cb.email}` : ""}.
              </>
            ) : (
              <>
                <AlertTriangle size={14} />
                {cb.error}
              </>
            )}
          </div>
        )}

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

        {/* not connected: one-click auto connect + manual fallback */}
        {!connected && (
          <>
            <div className="space-y-2">
              <p className="text-[12px] text-[var(--admin-text-muted)]">
                Click once to connect. You'll sign in at Cline and be
                redirected back automatically — no code to paste.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="primary"
                  disabled={autoConnect.isPending || !hasKeys}
                  onClick={() => autoConnect.mutate()}
                >
                  {autoConnect.isPending ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <LogIn size={14} />
                  )}
                  {autoConnect.isPending ? "Starting…" : "Connect with Cline"}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => setShowManual((s) => !s)}
                >
                  {showManual ? (
                    <ChevronUp size={14} />
                  ) : (
                    <ChevronDown size={14} />
                  )}
                  Manual paste-code
                </Button>
              </div>
            </div>

            {/* collapsible manual paste-code fallback */}
            {showManual && (
              <div className="space-y-2 border-t border-[var(--admin-border)] pt-3">
                <p className="text-[12px] text-[var(--admin-text-muted)]">
                  Open the Cline login page, sign in, then paste the code
                  here. Use this when the automatic redirect can't land
                  (e.g. localhost without a public URL).
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="outline"
                    disabled={loginUrl.isPending}
                    onClick={() => loginUrl.mutate()}
                  >
                    <LogIn size={14} />{" "}
                    {loginUrl.isPending ? "Opening…" : "Open Cline login"}
                  </Button>
                </div>
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
            )}
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
  const callbackResult = useClineCallbackBanner();
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
        title="OAuth Cline"
        subtitle="connect and manage Cline accounts"
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
            No Cline accounts configured yet. Click "Add Cline provider" to
            get started.
          </EmptyState>
        </Card>
      ) : (
        <div className="admin-stagger grid grid-cols-1 gap-4 lg:grid-cols-2">
          {oauthProviders.map((p) => (
            <OAuthProviderCard
              key={p.name}
              p={p}
              callbackResult={
                callbackResult?.provider === p.name ? callbackResult : null
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
