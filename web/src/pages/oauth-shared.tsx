// Cline OAuth — the connect flow, status display, and callback banner shared by
// the console accounts page and the provider-detail page.
//
// WHY THE CONNECT FLOW IS A GUIDED MANUAL DIALOG
// ---------------------------------------------
// This file deliberately does NOT offer a one-click "connect and done" path,
// because none exists. `cline_oauth.build_auth_url()`
// (wiwi/providers/cline_oauth.py:43) sends `callback_url` / `redirect_uri` to
// Cline, but Cline's Google OAuth ignores both and lands the browser on its own
// origin at `https://app.cline.bot/auth/callback?code=…`. The gateway's
// `GET /cline/oauth/callback` handler (wiwi/server/app.py:5610) therefore never
// fires in practice. The whole redirect pipeline — `POST
// /admin/cline/oauth/auto-connect`, its single-use state token, that handler,
// and the banner hook below — is correct but dormant, kept alive on purpose so
// it starts working the day Cline honours the parameter (REMINDERS.md).
//
// So connecting genuinely means: open Cline's login, sign in, copy the callback
// URL out of the address bar, paste it back. The old UI described this honestly
// in body copy while presenting a button labelled "Connect with Cline" that read
// as one click, opened a popup with no fallback if the popup was blocked, and
// offered no way to copy the URL. This dialog makes the manual step the
// explicit, guided thing it actually is.

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  Check,
  ChevronDown,
  Loader2,
  LogIn,
  RefreshCcw,
  Unlink,
} from "lucide-react";
import {
  clineConnect,
  clineDisconnect,
  clineLoginUrl,
  clineRefresh,
  clineStatus,
} from "@/api/client";
import type { ClineStatusResponse } from "@/api/types";
import { Alert, Badge, Button, CopyButton, Dialog, Field, Input } from "@/components/ui";

// -- status queries -------------------------------------------------------------

/** Structural view of a react-query status result. Narrower than
 *  `UseQueryResult` on purpose: these components only read these five fields,
 *  and keeping the surface small stops them coupling to react-query generics. */
export type ClineStatusQuery = {
  data: ClineStatusResponse | undefined;
  isPending: boolean;
  isFetching: boolean;
  isError: boolean;
  refetch: () => void;
};

/** Every account's status, fetched under the SAME query key the individual
 *  cards use, so this is one cache entry per provider rather than a parallel
 *  set. Both surfaces (accounts page, provider detail) therefore share status
 *  and invalidate each other correctly. */
export function useClineStatusIndex(names: string[]): {
  byName: Map<string, ClineStatusQuery>;
  connectedNames: string[];
} {
  const results = useQueries({
    queries: names.map((name) => ({
      queryKey: ["cline-oauth-status", name],
      queryFn: () => clineStatus(name),
      refetchInterval: 30_000,
    })),
  });

  return useMemo(() => {
    const byName = new Map<string, ClineStatusQuery>();
    names.forEach((name, i) => {
      const r = results[i];
      if (r) byName.set(name, r as ClineStatusQuery);
    });
    const connectedNames = names.filter((n) => byName.get(n)?.data?.connected === true);
    return { byName, connectedNames };
  }, [names, results]);
}

// -- formatting -----------------------------------------------------------------

function relativeSpan(ms: number): string | null {
  const past = ms < 0;
  const abs = Math.abs(ms);
  const mins = Math.round(abs / 60_000);
  if (mins < 1) return null;
  if (mins < 60) return past ? `${mins}m ago` : `in ${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return past ? `${hours}h ago` : `in ${hours}h`;
  return past ? `${Math.round(hours / 24)}d ago` : `in ${Math.round(hours / 24)}d`;
}

/** Absolute timestamp plus how far away it is — "Mar 4, 02:31 PM (in 9d)".
 *  An absolute time alone forces the reader to do the arithmetic against
 *  "now", which is the one thing they are checking this page for. */
export function fmtClineExpiry(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const abs = d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  const rel = relativeSpan(d.getTime() - Date.now());
  return rel ? `${abs} (${rel})` : abs;
}

// -- status display -------------------------------------------------------------

/** Four-way status. The old badge had no `isError` branch, so a failed status
 *  poll fell through to the same grey "not connected" as a genuinely
 *  disconnected account — inviting a re-connect of an account that was fine.
 *  `clineStatus` 404s for an unknown provider (app.py:5692), so this branch
 *  fires on real conditions, not just theoretical ones. */
export function ClineStatusBadge(props: { q: ClineStatusQuery }) {
  const { q } = props;
  if (q.isPending && !q.data) return <Badge tone="gray">checking…</Badge>;
  if (q.isError) return <Badge tone="red">status unavailable</Badge>;
  if (!q.data?.connected) return <Badge tone="gray">not connected</Badge>;
  if (q.data.needs_refresh) return <Badge tone="amber">expires soon</Badge>;
  return <Badge tone="green">connected</Badge>;
}

/** Re-fetch an account's status, with the pending state made visible. The old
 *  "Recheck" button shared a glyph with "Refresh token" and gave no feedback at
 *  all, so clicking it looked like nothing happened for up to 30s. */
export function StatusRefreshButton(props: { q: ClineStatusQuery; label?: string }) {
  const busy = props.q.isFetching;
  return (
    <Button
      variant="ghost"
      className="min-h-11"
      disabled={busy}
      onClick={() => props.q.refetch()}
    >
      {busy ? (
        <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
      ) : (
        <RefreshCcw size={14} />
      )}
      {busy ? "Checking…" : (props.label ?? "Recheck status")}
    </Button>
  );
}

// -- paste detection ------------------------------------------------------------

export type ClinePasteShape =
  | { kind: "empty" }
  | { kind: "url"; host: string; codeLength: number }
  | { kind: "url-no-code"; host: string }
  | { kind: "blob"; codeLength: number };

/** Describe what the user pasted, without decoding it.
 *
 *  This mirrors the *shape* test in `_extract_code`
 *  (wiwi/providers/cline_oauth.py:51) — a value containing "?" or "#" is treated
 *  as a URL and `code` is read from its query — purely so the UI can tell the
 *  user what will happen. The actual decode stays on the server: it handles
 *  percent-encoded blobs and truncated Google-OAuth codes that a client-side
 *  decoder could not reproduce, so we send the raw string untouched. */
export function describeClinePaste(raw: string): ClinePasteShape {
  const s = raw.trim();
  if (!s) return { kind: "empty" };
  if (s.includes("?") || s.includes("#")) {
    try {
      const u = new URL(s);
      const code = u.searchParams.get("code");
      return code
        ? { kind: "url", host: u.host, codeLength: code.length }
        : { kind: "url-no-code", host: u.host };
    } catch {
      // Has a ? or # but isn't a parseable URL — fall through to "blob".
    }
  }
  return { kind: "blob", codeLength: s.length };
}

function PasteReadout(props: { raw: string }) {
  const shape = describeClinePaste(props.raw);
  if (shape.kind === "empty") return null;
  if (shape.kind === "url") {
    return (
      <p className="mt-2 flex items-start gap-1.5 text-[12px] text-[var(--admin-success)]">
        <Check size={13} className="mt-0.5 shrink-0" aria-hidden />
        <span>
          Detected a callback URL from <span className="font-mono">{shape.host}</span> —{" "}
          {shape.codeLength}-character authorization code.
        </span>
      </p>
    );
  }
  if (shape.kind === "blob") {
    return (
      <p className="mt-2 flex items-start gap-1.5 text-[12px] text-[var(--admin-success)]">
        <Check size={13} className="mt-0.5 shrink-0" aria-hidden />
        <span>Detected a bare authorization code — {shape.codeLength} characters.</span>
      </p>
    );
  }
  return (
    <Alert tone="warning" className="mt-2" icon={null}>
      That URL has no <code className="font-mono">code</code> parameter. Copy the address
      bar after Cline shows its callback page.
    </Alert>
  );
}

// -- connect dialog -------------------------------------------------------------

const STEP_TITLES = ["Open Cline login", "Copy the callback URL", "Paste to connect"] as const;

export function ClineConnectDialog(props: {
  open: boolean;
  provider: string;
  /** No pool key means nothing can receive the token, so the flow is blocked. */
  hasKeys: boolean;
  onClose: () => void;
  onConnected: (email: string | null) => void;
}) {
  const qc = useQueryClient();
  const statusKey = ["cline-oauth-status", props.provider];

  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<1 | 2 | 3>(1);
  const [confirmedCopy, setConfirmedCopy] = useState(false);
  const [paste, setPaste] = useState("");
  const [connectError, setConnectError] = useState<string | null>(null);

  const bodyRefs = useRef<(HTMLDivElement | null)[]>([]);
  const openedRef = useRef(false);

  // A mutation, not a query: each call mints a fresh login URL, so it must not
  // be served from cache across dialog openings.
  const loginUrl = useMutation({
    mutationFn: (callbackUrl: string) => clineLoginUrl(callbackUrl),
  });
  const { mutate: requestLoginUrl } = loginUrl;

  useEffect(() => {
    if (!props.open) {
      openedRef.current = false;
      return;
    }
    if (openedRef.current) return;
    openedRef.current = true;
    setAuthUrl(null);
    setFetchError(null);
    setExpanded(1);
    setConfirmedCopy(false);
    setPaste("");
    setConnectError(null);
    requestLoginUrl(`${window.location.origin}/console/oauth`, {
      onSuccess: (d) => {
        setAuthUrl(d.auth_url);
        setExpanded(2);
      },
      onError: (e) => setFetchError(e.message),
    });
  }, [props.open, props.provider, requestLoginUrl]);

  // Expanding a step moves focus into it, so a keyboard user is never left on a
  // header that just collapsed out from under them. Skipped on the initial open
  // (nothing to move away from, and stealing focus off the step 1 header reads
  // as a glitch).
  const wasExpanded = useRef<1 | 2 | 3>(expanded);
  useEffect(() => {
    if (wasExpanded.current === expanded) return;
    wasExpanded.current = expanded;
    const el = bodyRefs.current[expanded - 1];
    const first = el?.querySelector<HTMLElement>(
      "a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled])",
    );
    first?.focus();
  }, [expanded]);

  const connect = useMutation({
    mutationFn: (code: string) => clineConnect(props.provider, code),
    onSuccess: (d) => {
      setConnectError(null);
      void qc.invalidateQueries({ queryKey: statusKey });
      void qc.invalidateQueries({ queryKey: ["providers"] });
      props.onConnected(d.email);
      props.onClose();
    },
    onError: (e) => setConnectError(e.message),
  });

  const stepDone = [authUrl !== null, confirmedCopy, false];
  const busy = connect.isPending;

  const submit = () => {
    if (!paste.trim() || busy) return;
    connect.mutate(paste.trim());
  };

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      icon={LogIn}
      size="3xl"
      contained
      title={`Connect ${props.provider}`}
      subtitle="Cline's login does not return to this console, so the callback has to come back by hand."
      footer={
        <>
          <Button variant="ghost" className="min-h-11" onClick={props.onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            className="min-h-11"
            disabled={!paste.trim() || busy || !props.hasKeys}
            onClick={submit}
          >
            {busy ? (
              <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
            ) : null}
            {busy ? "Connecting…" : "Connect account"}
          </Button>
        </>
      }
    >
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        {!props.hasKeys && (
          <Alert tone="warning">
            This account has no pool keys, so there is nowhere for the access token to
            land. Add a key on the provider detail page first.
          </Alert>
        )}
        {fetchError && (
          <Alert tone="danger" title="Could not start the Cline login">
            {fetchError}
          </Alert>
        )}

        <ol className="space-y-2">
          {STEP_TITLES.map((title, i) => {
            const n = (i + 1) as 1 | 2 | 3;
            const done = stepDone[n - 1];
            const open = expanded === n;
            const disabled = n === 3 && !authUrl;
            return (
              <li
                key={title}
                className={`rounded-lg border ${
                  open
                    ? "border-[var(--admin-border-hover)] bg-white/[0.02]"
                    : "border-[var(--admin-border)]"
                }`}
              >
                <button
                  type="button"
                  disabled={disabled}
                  aria-expanded={open}
                  aria-current={open ? "step" : undefined}
                  onClick={() => setExpanded(open ? ((n === 3 ? 2 : n) as 1 | 2 | 3) : n)}
                  className="flex min-h-11 w-full items-center gap-3 px-3 py-2.5 text-left disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span
                    aria-hidden
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
                      done
                        ? "bg-[var(--admin-success)]/15 text-[var(--admin-success)]"
                        : open
                          ? "bg-[var(--admin-accent)]/15 text-[var(--admin-accent)]"
                          : "bg-white/[0.06] text-[var(--admin-text-dim)]"
                    }`}
                  >
                    {done ? <Check size={13} /> : n}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13px] font-medium text-[var(--admin-text)]">
                      {title}
                    </span>
                    <span className="sr-only">
                      Step {n} of 3{open ? " (current)" : done ? " (done)" : ""}
                    </span>
                  </span>
                  {!disabled && (
                    <ChevronDown
                      size={15}
                      aria-hidden
                      className={`shrink-0 text-[var(--admin-text-dim)] transition-transform ${
                        open ? "rotate-180" : ""
                      }`}
                    />
                  )}
                </button>

                {open && (
                  <div
                    ref={(el) => {
                      bodyRefs.current[i] = el;
                    }}
                    className="space-y-3 border-t border-[var(--admin-border)] px-3 pb-3.5 pt-3"
                  >
                    {n === 1 && (
                      <>
                        <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                          Open Cline's login page and sign in with the account you want to
                          connect. If the new tab does not open, copy the link below and
                          paste it into your browser.
                        </p>
                        {loginUrl.isPending ? (
                          <div className="flex items-center gap-2 text-[12px] text-[var(--admin-text-muted)]">
                            <Loader2
                              size={13}
                              className="animate-spin motion-reduce:animate-none"
                              aria-hidden
                            />
                            Preparing the login link…
                          </div>
                        ) : authUrl ? (
                          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                            <Input
                              readOnly
                              value={authUrl}
                              aria-label="Cline login URL"
                              onFocus={(e) => e.currentTarget.select()}
                              className="min-w-0 flex-1 font-mono text-[12px]"
                            />
                            <div className="flex shrink-0 gap-2">
                              <CopyButton text={authUrl} className="min-h-11" />
                              <Button
                                href={authUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="min-h-11"
                              >
                                <LogIn size={14} aria-hidden />
                                Open login
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            className="min-h-11"
                            onClick={() =>
                              requestLoginUrl(`${window.location.origin}/console/oauth`, {
                                onSuccess: (d) => setAuthUrl(d.auth_url),
                                onError: (e) => setFetchError(e.message),
                              })
                            }
                          >
                            <RefreshCcw size={14} aria-hidden />
                            Try again
                          </Button>
                        )}
                      </>
                    )}

                    {n === 2 && (
                      <>
                        <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                          After signing in, Cline shows a page at{" "}
                          <span className="font-mono text-[11px]">
                            https://app.cline.bot/auth/callback?code=…
                          </span>
                          . Copy that entire URL from your browser's address bar.
                        </p>
                        <Button
                          variant={confirmedCopy ? "ghost" : "primary"}
                          className="min-h-11"
                          onClick={() => setExpanded(3)}
                        >
                          {confirmedCopy ? (
                            <Check size={14} aria-hidden />
                          ) : (
                            <LogIn size={14} aria-hidden />
                          )}
                          {confirmedCopy ? "Copied — paste it below" : "I copied it"}
                        </Button>
                      </>
                    )}

                    {n === 3 && (
                      <>
                        <Field
                          label="Callback URL or authorization code"
                          hint="Paste the whole address-bar URL, or just the code parameter from it."
                        >
                          <textarea
                            className="admin-input min-h-20 resize-none font-mono text-[12px]"
                            value={paste}
                            readOnly={busy}
                            onChange={(e) => setPaste(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
                            }}
                            autoComplete="off"
                            spellCheck={false}
                          />
                        </Field>
                        <PasteReadout raw={paste} />
                        {connectError && (
                          <Alert tone="danger" title="Could not connect">
                            {connectError}
                          </Alert>
                        )}
                      </>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </Dialog>
  );
}

/** Button that opens the guided connect flow for one account. */
export function ClineConnectButton(props: {
  provider: string;
  hasKeys: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  return (
    <>
      <Button className="min-h-11" onClick={() => setOpen(true)}>
        <LogIn size={14} aria-hidden />
        Connect
      </Button>
      <ClineConnectDialog
        open={open}
        provider={props.provider}
        hasKeys={props.hasKeys}
        onClose={() => setOpen(false)}
        onConnected={setEmail}
      />
      {email && <span className="sr-only" role="status">Connected as {email}</span>}
    </>
  );
}

// -- disconnect -----------------------------------------------------------------

export function DisconnectDialog(props: {
  open: boolean;
  provider: string;
  onClose: () => void;
  onError: (m: string) => void;
  onDisconnected: () => void;
}) {
  const qc = useQueryClient();
  const disconnect = useMutation({
    mutationFn: () => clineDisconnect(props.provider),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cline-oauth-status", props.provider] });
      void qc.invalidateQueries({ queryKey: ["providers"] });
      props.onDisconnected();
      props.onClose();
    },
    onError: (e) => props.onError(e.message),
  });

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      icon={Unlink}
      title={`Disconnect ${props.provider}?`}
      subtitle="Requests routed to this account will stop working until you connect again."
      footer={
        <>
          <Button variant="ghost" className="min-h-11" onClick={props.onClose}>
            Cancel
          </Button>
          <Button
            variant="danger"
            className="min-h-11"
            disabled={disconnect.isPending}
            onClick={() => disconnect.mutate()}
          >
            {disconnect.isPending ? (
              <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
            ) : (
              <Unlink size={14} aria-hidden />
            )}
            {disconnect.isPending ? "Disconnecting…" : "Disconnect"}
          </Button>
        </>
      }
    >
      <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
        Clears the stored OAuth tokens. The pool key keeps its last access token until
        you reconnect or replace it, and any model group still pointing at this account
        will have no working deployment.
      </p>
    </Dialog>
  );
}

/** Refresh an account's access token on demand. */
export function useClineRefresh(provider: string, onError: (m: string) => void) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => clineRefresh(provider),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cline-oauth-status", provider] });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => onError(e.message),
  });
  return m;
}

// -- callback banner ------------------------------------------------------------

export type ClineCallbackResult =
  | { ok: true; provider: string; email: string }
  | { ok: false; provider: string; error: string };

/** Reads the redirect params Cline *would* send back to this console, surfaces
 *  them once, and strips them from the URL so a refresh doesn't re-fire.
 *
 *  Dormant today — see the file header. Kept because removing the only reader
 *  would mean that on the day Cline honours `callback_url`, a successful connect
 *  would land the admin on a page showing nothing at all. */
export function useClineCallbackBanner(): {
  result: ClineCallbackResult | null;
  clear: () => void;
} {
  const [searchParams, setSearchParams] = useSearchParams();
  const [result, setResult] = useState<ClineCallbackResult | null>(null);
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
      strip(searchParams, setSearchParams);
    } else if (error) {
      seen.current = true;
      setResult({
        ok: false,
        provider: searchParams.get("cline_provider") ?? "",
        error,
      });
      strip(searchParams, setSearchParams);
    }
  }, [searchParams, setSearchParams]);

  return { result, clear: () => setResult(null) };
}

function strip(
  searchParams: URLSearchParams,
  setSearchParams: (
    next: string | URLSearchParams,
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

/** Page-level rendering of the callback result.
 *
 *  Rendered once for the page rather than inside each account card: the old code
 *  matched `callbackResult.provider === p.name` and passed `null` to every card
 *  when the param was missing, so a real round-trip could complete and show the
 *  operator nothing — and because `_stripClineParams` had already cleared the
 *  URL, the information was gone rather than merely hidden. At page level there
 *  is no key to mismatch, and the dismiss button makes it closable. */
export function ClineCallbackAlert(props: {
  result: ClineCallbackResult | null;
  onDismiss: () => void;
}) {
  const r = props.result;
  if (!r) return null;
  if (r.ok) {
    return (
      <Alert tone="success" onDismiss={props.onDismiss}>
        Connected{r.provider ? ` — ${r.provider}` : ""}
        {r.email ? ` as ${r.email}` : ""}.
      </Alert>
    );
  }
  return (
    <Alert
      tone="danger"
      onDismiss={props.onDismiss}
      title={r.provider ? `Could not connect ${r.provider}` : "Could not connect"}
    >
      {r.error}
    </Alert>
  );
}
