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
import { ExternalLink, Loader2, LogIn } from "lucide-react";
import {
  getProviders,
  hermesDeviceCode,
  hermesPoll,
} from "@/api/client";
import type { Provider } from "@/api/types";
import {
  HermesAccountList,
  useHermesAccountActions,
  useHermesStatusQuery,
} from "@/components/HermesAccounts";
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

/** First ``base``-suffixed name not already taken: ``hermes-main``, then
 *  ``hermes-main-2``, ``hermes-main-3``… so a new account never silently
 *  collides with (and replaces) an existing one. */
function _uniqueName(base: string, existing: string[]): string {
  const taken = new Set(existing);
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}

// -- connect dialog ------------------------------------------------------------

export function HermesConnectDialog(props: {
  open: boolean;
  provider: string;
  onClose: () => void;
  onConnected: () => void;
  /** Bootstrapping a brand-new account: prompt for a name first, then let the
   *  OAuth device flow create the account server-side (no API key needed). */
  isNew?: boolean;
  /** Names already in use, so a new account can't collide with (and silently
   *  replace) an existing one. */
  existingNames?: string[];
}) {
  const existing = props.existingNames ?? [];
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
      setDraftName(_uniqueName("hermes-main", existing));
    }
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, [props.open]);

  // When opening for a brand-new account, pre-fill a name that does not
  // collide with an existing one (hermes-main, hermes-main-2, …).
  useEffect(() => {
    if (props.open && props.isNew && name === null) {
      setDraftName(_uniqueName("hermes-main", existing));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.open, props.isNew]);

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
                {existing.includes(draftName.trim()) ? (
                  <p className="text-[12px] text-red-400">
                    An account named “{draftName.trim()}” already exists. Pick a
                    different name so this account is added alongside it, not in
                    place of it.
                  </p>
                ) : (
                  <p className="text-[12px] text-[var(--admin-text-muted)]">
                    The account is created automatically when you sign in — no API
                    key needed.
                  </p>
                )}
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
            disabled={!draftName.trim() || existing.includes(draftName.trim())}
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
  /** ``null`` clears a stale message after a successful action. */
  onError: (m: string | null) => void;
  onChanged: () => void;
}) {
  const [connectOpen, setConnectOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const qc = useQueryClient();
  const statusQ = useHermesStatusQuery(props.p.name);

  const p = props.p;
  const accounts = statusQ.data?.accounts ?? [];
  const managed = accounts.filter((a) => a.managed);
  const connected = statusQ.data?.connected ?? false;
  const needsRefresh = managed.some((a) => a.needs_refresh);
  const actions = useHermesAccountActions(p.name, {
    onError: (m) => {
      if (m === null) setNotice(null);
      props.onError(m);
    },
    onNotice: setNotice,
  });

  const onConnect = () => {
    void qc.invalidateQueries({ queryKey: ["hermes-oauth-status", p.name] });
    void qc.invalidateQueries({ queryKey: ["providers"] });
  };

  return (
    <Card className="flex flex-col">
      <div className="flex items-start gap-3 border-b border-[var(--admin-border)] px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/console/providers/${encodeURIComponent(p.name)}`}
              className="min-w-0 break-all text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)] transition-colors hover:text-blue-400"
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
            {managed.length > 1 && (
              <Badge tone="blue" title="OAuth accounts in this provider's key pool">
                {managed.length} accounts
              </Badge>
            )}
          </div>
          <code className="mt-0.5 block break-all font-mono text-[11px] text-[var(--admin-text-dim)]">
            {p.base_url || DEFAULT_BASE_URL}
          </code>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-3 px-5 py-4">
        {statusQ.isLoading && <Spinner />}
        {!statusQ.isLoading && !statusQ.isError && accounts.length > 0 && (
          <HermesAccountList accounts={accounts} actions={actions} />
        )}
        {!statusQ.isLoading && !statusQ.isError && accounts.length === 0 && (
          <p className="text-[12px] text-[var(--admin-text-muted)]">
            This provider has no pool keys. Connect an account to create one.
          </p>
        )}
        {notice && (
          <p className="text-[12px] text-emerald-400">{notice}</p>
        )}
        <div className="flex items-center justify-between gap-2 text-[12px]">
          <span className="admin-label">Pool keys</span>
          <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
            {p.keys.length}
          </span>
        </div>
        {connectOpen && (
          <HermesConnectDialog
            open={connectOpen}
            provider={p.name}
            onClose={() => setConnectOpen(false)}
            onConnected={() => {
              onConnect();
              props.onChanged();
            }}
          />
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--admin-border)] px-5 py-3">
        {connected ? (
          <div className="flex flex-wrap gap-2">
            {/* Whole-pool disconnect: no key_label, so every managed account
                is removed. Per-account disconnect lives on each row above. */}
            <Button
              variant="ghost"
              className="min-h-11"
              disabled={actions.pending?.kind === "disconnect"}
              onClick={() => actions.disconnect()}
            >
              Disconnect all
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
          <Button variant="ghost" className="min-h-11">
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

      <HermesConnectDialog
        open={newOpen}
        provider=""
        isNew
        existingNames={accounts.map((p) => p.name)}
        onClose={() => setNewOpen(false)}
        onConnected={() => {
          void q.refetch();
          setNewOpen(false);
        }}
      />
    </div>
  );
}
