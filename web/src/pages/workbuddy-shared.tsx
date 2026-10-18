// WorkBuddy — the browser-login session machine and the account table, shared
// by the WorkBuddy page (its "Log in" dialog) and the Account login page at
// /console/login (an always-visible sign-in section).
//
// WHY THERE IS ONLY ONE COPY OF THE FLOW
// --------------------------------------
// The upstream flow is three legs: start a login session (which returns the
// login URL to open), poll for approval, then fetch the account identity and
// save it as a pool key. Two surfaces drive exactly that sequence, so the state
// machine lives here rather than being copied into each page — a second copy is
// the "second convention beside existing" the project rules prohibit, and the
// two would drift the first time a leg changes.
//
// The login URL is *returned by the upstream* rather than composed here, and
// the backend host-allowlists it (`workbuddy_auth.accepted_auth_url`), so a
// misrouted response cannot turn either surface into an open redirector. What
// the operator opens therefore tracks whatever the provider currently serves.

import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { workbuddyLoginPoll, workbuddyLoginStart, workbuddyRefresh } from "@/api/client";
import type { WorkBuddyAccount, WorkBuddyLoginConnected } from "@/api/types";
import { Alert, Badge, Button, CopyButton, Field, Input, Table, TD } from "@/components/ui";

/** How often to re-poll the upstream for approval. */
export const LOGIN_POLL_MS = 5000;

/** An open login session: the wiwi-side token to poll with, and the URL to open. */
export interface WorkBuddyLoginSession {
  state: string;
  auth_url: string;
}

/** Access-token expiry as an absolute date plus a relative countdown. */
export function fmtWorkBuddyExpiry(expiresAt: number | null | undefined): string {
  if (!expiresAt) return "unknown";
  const days = (expiresAt * 1000 - Date.now()) / 86_400_000;
  const rel =
    days >= 1 ? `${Math.floor(days)}d` : `${Math.max(0, Math.floor(days * 24))}h`;
  return `${new Date(expiresAt * 1000).toLocaleDateString()} (${rel})`;
}

/** The realm a login landed in. */
export function WorkBuddyRegionBadge(region: WorkBuddyAccount["region"]) {
  if (region === "global") return <Badge tone="violet">global</Badge>;
  return <Badge tone="blue">cn</Badge>;
}

// -- session machine -------------------------------------------------------------

/** Everything a login surface renders from — the state plus the trigger. */
export interface WorkBuddyLoginController {
  /** Provider the account will be saved onto; created on first login. */
  provider: string;
  setProvider: (value: string) => void;
  realm: "cn" | "global";
  setRealm: (value: "cn" | "global") => void;
  /** Non-null while waiting for the operator to approve in their browser. */
  sess: WorkBuddyLoginSession | null;
  /** Non-null once the backend has saved the account; carries every detail. */
  done: WorkBuddyLoginConnected | null;
  error: string | null;
  starting: boolean;
  start: () => void;
  /** Back to a blank form (the dialog calls this when it closes). */
  reset: () => void;
}

export function useWorkBuddyLogin(
  opts: { onConnected?: (detail: WorkBuddyLoginConnected) => void } = {},
): WorkBuddyLoginController {
  const qc = useQueryClient();
  const [provider, setProvider] = useState("workbuddy-main");
  const [realm, setRealm] = useState<"cn" | "global">("cn");
  const [sess, setSess] = useState<WorkBuddyLoginSession | null>(null);
  const [done, setDone] = useState<WorkBuddyLoginConnected | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const name = provider.trim() || "workbuddy-main";

  const start = useMutation({
    mutationFn: () => workbuddyLoginStart(name, realm),
    onSuccess: (d) => {
      setError(null);
      setDone(null);
      setSess({ state: d.state, auth_url: d.auth_url });
    },
    onError: (e) => setError(e.message),
  });

  const schedule = (state: string) => {
    if (pollRef.current) window.clearTimeout(pollRef.current);
    pollRef.current = window.setTimeout(() => poll.mutate(state), LOGIN_POLL_MS);
  };

  const poll = useMutation({
    mutationFn: (state: string) => workbuddyLoginPoll(state),
    onSuccess: (d, state) => {
      // ``{pending: true}`` leaves the session open server-side, so polling
      // resumes; a 404/410 (unknown or expired session) lands in ``onError``
      // instead, which stops the loop and shows why.
      if ("pending" in d) {
        schedule(state);
        return;
      }
      setSess(null);
      setDone(d);
      void qc.invalidateQueries({ queryKey: ["workbuddy-accounts"] });
      void qc.invalidateQueries({ queryKey: ["providers"] });
      opts.onConnected?.(d);
    },
    onError: (e) => {
      setSess(null);
      setError(e.message);
    },
  });

  // Without this the surface would sit on "Waiting for approval…" forever: the
  // session renders once but nothing would ever call the poll endpoint.
  useEffect(() => {
    if (sess && !done && !error) poll.mutate(sess.state);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sess?.state]);

  useEffect(
    () => () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
    },
    [],
  );

  const reset = useCallback(() => {
    if (pollRef.current) window.clearTimeout(pollRef.current);
    setSess(null);
    setDone(null);
    setError(null);
    setProvider("workbuddy-main");
    setRealm("cn");
  }, []);

  return {
    provider,
    setProvider,
    realm,
    setRealm,
    sess,
    done,
    error,
    starting: start.isPending,
    start: () => start.mutate(),
    reset,
  };
}

// -- login surface ---------------------------------------------------------------

/** The login flow's body: idle form, waiting state, saved-account summary, or
 *  the error that ended the attempt — exactly one at a time.
 *
 *  `onDismiss`, when given, adds the trailing ghost button a dialog needs
 *  ("Cancel" before the flow starts, "Done" after it lands). A page-level
 *  section passes nothing: there is nothing to dismiss. */
export function WorkBuddyLoginForm(props: {
  state: WorkBuddyLoginController;
  onDismiss?: () => void;
}) {
  const { provider, setProvider, realm, setRealm, sess, done, error, starting, start } =
    props.state;

  return (
    <>
      {error && (
        <Alert tone="danger" title="Could not log in">
          <p className="text-[13px]">{error}</p>
        </Alert>
      )}

      {!error && done && (
        <Alert tone="success" title="Connected">
          <p className="text-[13px]">
            {done.nickname || done.key_label} is now a pool key on{" "}
            <strong>{done.provider}</strong> ({done.region}).
          </p>
          {/* The whole identity, as saved — no import step, no paste-back. */}
          <div className="mt-2 space-y-0.5 text-[12px] text-[var(--admin-text-muted)]">
            <div>uid: {done.uid || "—"}</div>
            <div>token: {done.access_token_masked}</div>
            <div>expires: {fmtWorkBuddyExpiry(done.expires_at)}</div>
          </div>
        </Alert>
      )}

      {!error && !done && sess && (
        <div className="space-y-3">
          <p className="text-[13px] text-[var(--admin-text-muted)]">
            Open the link below and sign in. wiwi detects the approval
            automatically — there is nothing to paste back here.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              href={sess.auth_url}
              target="_blank"
              rel="noopener noreferrer"
              variant="outline"
              className="min-h-11"
            >
              <ExternalLink size={14} /> Open WorkBuddy sign-in
            </Button>
            <CopyButton text={sess.auth_url} className="min-h-11" />
          </div>
          <code className="block break-all rounded-lg border border-[var(--admin-border)] px-3 py-2 text-[11px] text-[var(--admin-text-muted)]">
            {sess.auth_url}
          </code>
          <p className="flex items-center gap-2 text-[12px] text-[var(--admin-text-muted)]">
            <Loader2 size={13} className="animate-spin motion-reduce:animate-none" />
            Waiting for approval…
          </p>
        </div>
      )}

      {!error && !done && !sess && (
        <div className="space-y-3">
          <p className="text-[13px] text-[var(--admin-text-muted)]">
            Sign in with a WorkBuddy / CodeBuddy account. The account becomes a
            pool key on the provider below; the provider is created on first
            login.
          </p>
          <Field label="Provider name">
            <Input
              value={provider}
              placeholder="workbuddy-main"
              className="min-h-11"
              onChange={(e) => setProvider(e.target.value)}
            />
          </Field>
          <Field
            label="Realm"
            hint="Pick where the account lives — the two realms have separate logins and separate tokens."
          >
            <select
              className="admin-input w-full min-h-11"
              value={realm}
              onChange={(e) => setRealm(e.target.value === "global" ? "global" : "cn")}
            >
              <option value="cn">China — copilot.tencent.com</option>
              <option value="global">Global — workbuddy.ai</option>
            </select>
          </Field>
          {starting ? (
            <p className="flex items-center gap-2 text-[13px] text-[var(--admin-text-muted)]">
              <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
              Starting login…
            </p>
          ) : (
            <p className="text-[12px] text-[var(--admin-text-muted)]">
              wiwi asks the provider for a login link and shows it to you. The
              link is only valid for about ten minutes.
            </p>
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap justify-end gap-2">
        {props.onDismiss && (
          <Button variant="ghost" className="min-h-11" onClick={props.onDismiss}>
            {done ? "Done" : "Cancel"}
          </Button>
        )}
        {!done && !sess && (
          <Button className="min-h-11" disabled={starting} onClick={start}>
            {starting ? "Starting…" : "Start login"}
          </Button>
        )}
      </div>
    </>
  );
}

// -- accounts table --------------------------------------------------------------

export function WorkBuddyAccountsTable(props: {
  accounts: WorkBuddyAccount[];
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState<string | null>(null);

  const refresh = useMutation({
    mutationFn: (a: WorkBuddyAccount) => workbuddyRefresh(a.provider, a.label),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workbuddy-accounts"] });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const refreshOne = (a: WorkBuddyAccount) => {
    setRefreshing(`${a.provider}/${a.label}`);
    refresh.mutate(a, {
      onSettled: () => setRefreshing(null),
    });
  };

  return (
    <Table head={["Account", "Provider", "Region", "Token", "Expires", "Status", ""]}>
      {props.accounts.map((a) => (
        <tr key={`${a.provider}/${a.label}`}>
          <TD>
            <div className="font-medium">{a.nickname || a.label}</div>
            <div className="text-[11px] text-[var(--admin-text-muted)]">
              {a.uid || "—"} · {a.label}
            </div>
          </TD>
          <TD>{a.provider}</TD>
          <TD>{WorkBuddyRegionBadge(a.region)}</TD>
          <TD>
            <code className="text-[11px] text-[var(--admin-text-muted)]">
              {a.access_token_masked ?? "—"}
            </code>
          </TD>
          <TD>{fmtWorkBuddyExpiry(a.expires_at)}</TD>
          <TD>
            {!a.valid_auth ? (
              <Badge tone="red">invalid auth</Badge>
            ) : a.needs_refresh ? (
              <Badge tone="amber">needs refresh</Badge>
            ) : (
              <Badge tone="green">valid</Badge>
            )}
            {a.enabled === false && <Badge tone="gray">disabled</Badge>}
          </TD>
          <TD>
            <Button
              variant="ghost"
              className="min-h-11"
              disabled={!a.has_refresh_token || refreshing !== null}
              onClick={() => refreshOne(a)}
            >
              <RefreshCw
                size={13}
                className={refreshing === `${a.provider}/${a.label}` ? "animate-spin" : ""}
              />
              Refresh
            </Button>
          </TD>
        </tr>
      ))}
    </Table>
  );
}
