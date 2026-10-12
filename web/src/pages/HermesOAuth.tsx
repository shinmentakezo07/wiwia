// Hermes (Nous Portal) OAuth — connect accounts via the RFC 8628 device-code
// flow, monitor token state, refresh, and disconnect.
//
// Unlike Cline (a paste-code flow because its OAuth ignores callback URLs),
// Nous Portal uses standard device authorization: wiwi mints a device code and
// a verification URL, the admin opens that URL in a browser and approves, and
// wiwi polls the token endpoint. This page drives that loop: start → open
// verification URL → poll until approved → connected. See wiwi/providers/
// hermes_oauth.py for the verified endpoint contract and server/app.py for the
// /admin/hermes/oauth/* routes.

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ExternalLink, Loader2, LogIn, RefreshCcw, Unlink } from "lucide-react";
import {
  getProviders,
  hermesDeviceCode,
  hermesDisconnect,
  hermesPoll,
  hermesRefresh,
  hermesStatus,
} from "@/api/client";
import type { Provider } from "@/api/types";
import {
  Alert,
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorText,
  Input,
  PageHeader,
  Spinner,
} from "@/components/ui";

const DEFAULT_BASE_URL = "https://inference-api.nousresearch.com/v1";
const POLL_INTERVAL_MS = 5000;

function fmtExpiry(epoch: number | null | undefined): string {
  if (!epoch) return "unknown";
  const ms = epoch * 1000;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "unknown";
  const days = (ms - Date.now()) / 86_400_000;
  const rel = days >= 1 ? `${Math.floor(days)}d` : `${Math.max(0, Math.floor(days * 24))}h`;
  return `${d.toLocaleDateString()} (${rel})`;
}

function useHermesStatus(provider: string) {
  return useQuery({
    queryKey: ["hermes-oauth-status", provider],
    queryFn: () => hermesStatus(provider),
    refetchInterval: 30_000,
    retry: 1,
  });
}

// -- connect dialog ------------------------------------------------------------

function ConnectDialog(props: {
  open: boolean;
  provider: string;
  onClose: () => void;
  onConnected: () => void;
  /** Bootstrapping a brand-new account: prompt for a name first, then let the
   *  OAuth device flow create the account server-side (no API key needed). */
  isNew?: boolean;
}) {
  const [name, setName] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("hermes-main");
  const [dc, setDc] = useState<{
    state: string;
    user_code: string;
    verification_uri_complete: string;
    interval: number;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const pollRef = useRef<number | null>(null);

  // The provider to connect: the card's own account, or a freshly named one
  // when bootstrapping from the empty state (OAuth creates the account, so no
  // API key is ever required).
  const target = name ?? props.provider;

  // Start the device-code session once we have a target provider name.
  const start = useMutation({
    mutationFn: () => hermesDeviceCode(target),
    onSuccess: (d) => {
      setError(null);
      setDone(false);
      setDc({
        state: d.state,
        user_code: d.user_code,
        verification_uri_complete: d.verification_uri_complete,
        interval: d.interval,
      });
    },
    onError: (e) => setError(e.message),
  });

  // Poll until the admin approves (or the dialog closes / a terminal error).
  const poll = useMutation({
    mutationFn: () => hermesPoll(dc!.state),
    onSuccess: (d) => {
      if (d.connected) {
        setDone(true);
        setDc(null);
        props.onConnected();
      } else if (d.pending) {
        // schedule the next poll
        scheduleNext();
      }
    },
    onError: (e) => {
      setError(e.message);
      setDc(null);
    },
  });

  const scheduleNext = () => {
    if (pollRef.current) window.clearTimeout(pollRef.current);
    pollRef.current = window.setTimeout(() => poll.mutate(), POLL_INTERVAL_MS);
  };

  useEffect(() => {
    if (props.open && !dc && !done && name !== null) start.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.open, name]);

  // Kick off the polling loop the moment a device code is in hand. Without
  // this the dialog only ever showed "Waiting for approval…" and never polled,
  // so an approved login was never detected.
  useEffect(() => {
    if (dc && !done && !error) poll.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dc?.state]);

  useEffect(() => {
    if (!props.open) {
      if (pollRef.current) window.clearTimeout(pollRef.current);
      setDc(null);
      setDone(false);
      setError(null);
      setName(null);
      setDraftName("hermes-main");
    }
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, [props.open]);

  const needsName = props.isNew && name === null;

  return (
    <Dialog
      open={props.open}
      title="Connect Hermes to Nous Portal"
      onClose={props.onClose}
    >
      <div className="space-y-3">
        {error && (
          <Alert tone="danger" title="Could not connect">
            <p className="text-[13px]">{error}</p>
          </Alert>
        )}
        {!error && done && (
          <Alert tone="success" title="Connected">
            <p className="text-[13px]">
              This account is now authenticated through Nous Portal. The gateway
              will refresh the token before it expires.
            </p>
          </Alert>
        )}
        {!error && !done && (
          <>
            {needsName ? (
              <div className="space-y-2">
                <label className="admin-label" htmlFor="hermes-new-name">
                  Account name
                </label>
                <Input
                  id="hermes-new-name"
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  placeholder="hermes-main"
                  autoComplete="off"
                  className="min-h-11"
                />
                <p className="text-[12px] text-[var(--admin-text-muted)]">
                  The account is created automatically when you sign in — no API
                  key needed.
                </p>
              </div>
            ) : dc ? (
              <>
                <p className="text-[13px] text-[var(--admin-text-muted)]">
                  Open the link below in your browser and approve access. wiwi
                  detects the approval automatically.
                </p>
                <a
                  href={dc.verification_uri_complete}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-[var(--admin-border)] px-3 text-[13px] font-medium text-blue-400 transition-colors hover:bg-white/[0.03]"
                >
                  <ExternalLink size={14} /> Open Nous Portal sign-in
                </a>
                <p className="text-[12px] text-[var(--admin-text-muted)]">
                  Code:{" "}
                  <code className="font-mono text-[12px] text-[var(--admin-text)]">
                    {dc.user_code}
                  </code>{" "}
                  (pre-filled by the link)
                </p>
                <p className="flex items-center gap-2 text-[12px] text-[var(--admin-text-muted)]">
                  <Loader2 size={13} className="animate-spin motion-reduce:animate-none" />
                  Waiting for approval…
                </p>
              </>
            ) : (
              <p className="flex items-center gap-2 text-[13px] text-[var(--admin-text-muted)]">
                <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
                Starting device authorization…
              </p>
            )}
          </>
        )}
      </div>
      <div className="mt-4 flex justify-end gap-2">
        {needsName ? (
          <Button
            className="min-h-11"
            disabled={!draftName.trim()}
            onClick={() => setName(draftName.trim())}
          >
            <LogIn size={14} /> Continue
          </Button>
        ) : (
          <Button className="min-h-11" onClick={props.onClose}>
            {done ? "Done" : "Cancel"}
          </Button>
        )}
      </div>
    </Dialog>
  );
}

// -- per-account card ----------------------------------------------------------

function HermesAccountCard(props: {
  p: Provider;
  onError: (m: string) => void;
  onChanged: () => void;
}) {
  const [connectOpen, setConnectOpen] = useState(false);
  const qc = useQueryClient();
  const statusQ = useHermesStatus(props.p.name);

  const refresh = useMutation({
    mutationFn: () => hermesRefresh(props.p.name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["hermes-oauth-status", props.p.name] });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const disconnect = useMutation({
    mutationFn: () => hermesDisconnect(props.p.name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["hermes-oauth-status", props.p.name] });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const p = props.p;
  const connected = statusQ.data?.connected ?? false;
  const needsRefresh = statusQ.data?.needs_refresh ?? false;

  return (
    <Card className="flex flex-col">
      <div className="flex items-start gap-3 border-b border-[var(--admin-border)] px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Link
              to={`/console/providers/${encodeURIComponent(p.name)}`}
              className="truncate text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)] transition-colors hover:text-blue-400"
            >
              {p.name}
            </Link>
            {statusQ.isPending && !statusQ.data ? (
              <Badge tone="gray">checking…</Badge>
            ) : statusQ.isError ? (
              <Badge tone="red">status unavailable</Badge>
            ) : !connected ? (
              <Badge tone="gray">not connected</Badge>
            ) : needsRefresh ? (
              <Badge tone="amber">expires soon</Badge>
            ) : (
              <Badge tone="green">connected</Badge>
            )}
          </div>
          <code className="mt-0.5 block truncate font-mono text-[11px] text-[var(--admin-text-dim)]">
            {p.base_url || DEFAULT_BASE_URL}
          </code>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-2 px-5 py-4">
        {connected && (
          <div className="flex items-center justify-between text-[12px]">
            <span className="admin-label">Token expires</span>
            <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
              {fmtExpiry(statusQ.data?.expires_at)}
            </span>
          </div>
        )}
        <div className="flex items-center justify-between text-[12px]">
          <span className="admin-label">Pool keys</span>
          <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
            {p.keys.length}
          </span>
        </div>
        {connectOpen && (
          <ConnectDialog
            open={connectOpen}
            provider={p.name}
            onClose={() => setConnectOpen(false)}
            onConnected={() => {
              void qc.invalidateQueries({ queryKey: ["hermes-oauth-status", p.name] });
              void qc.invalidateQueries({ queryKey: ["providers"] });
            }}
          />
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--admin-border)] px-5 py-3">
        {connected ? (
          <div className="flex gap-2">
            <Button
              variant="ghost"
              className="min-h-11"
              disabled={refresh.isPending || !statusQ.data?.has_refresh_token}
              onClick={() => refresh.mutate()}
            >
              {refresh.isPending ? (
                <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
              ) : (
                <RefreshCcw size={14} />
              )}
              Refresh token
            </Button>
            <Button
              variant="ghost"
              className="min-h-11"
              disabled={disconnect.isPending}
              onClick={() => disconnect.mutate()}
            >
              <Unlink size={14} /> Disconnect
            </Button>
          </div>
        ) : (
          <Button
            className="min-h-11"
            disabled={p.keys.length === 0}
            onClick={() => setConnectOpen(true)}
          >
            <LogIn size={14} /> Connect
          </Button>
        )}
        <Link to={`/console/providers/${encodeURIComponent(p.name)}`} className="inline-flex">
          <Button variant="ghost">
            Manage keys
          </Button>
        </Link>
      </div>
    </Card>
  );
}

// -- page -----------------------------------------------------------------------

export function HermesOAuthPage() {
  const q = useQuery({ queryKey: ["providers"], queryFn: getProviders, refetchInterval: 15_000 });
  const [error, setError] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);

  const accounts = (q.data?.providers ?? []).filter((p) => p.provider_type === "hermes");

  return (
    <div>
      <PageHeader
        title="Hermes OAuth"
        subtitle="Nous Portal accounts connected via device-code sign-in"
        right={
          <div className="flex items-center gap-2">
            <Button onClick={() => setNewOpen(true)}>
              <LogIn size={14} /> Connect account
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
          Hermes uses the OAuth 2.0 device-authorization flow. Connect an account
          here to sign in with your Nous Portal account instead of pasting an API
          key — the gateway stores the token, refreshes it before expiry, and
          routes inference through it. Accounts are listed under{" "}
          <Link to="/console/hermes" className="text-blue-400 underline underline-offset-2">
            Hermes
          </Link>
          .
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
            No Hermes accounts yet. Use “Connect account” to sign in with your
            Nous Portal account — the account is created automatically, no API
            key needed.
          </EmptyState>
        </Card>
      ) : (
        <div className="admin-stagger grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {accounts.map((p) => (
            <HermesAccountCard
              key={p.name}
              p={p}
              onError={setError}
              onChanged={() => undefined}
            />
          ))}
        </div>
      )}

      <ConnectDialog
        open={newOpen}
        provider=""
        isNew
        onClose={() => setNewOpen(false)}
        onConnected={() => {
          void q.refetch();
          setNewOpen(false);
        }}
      />
    </div>
  );
}
