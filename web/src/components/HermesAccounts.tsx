// Hermes (Nous Portal) account pool — shared by /console/hermes-oauth and the
// provider detail page so both render the same per-account rows.
//
// A wiwi provider pool can hold several Nous identities (one per pool key,
// AUDIT #427), so `/admin/hermes/oauth/status` answers with a *list*. Each
// managed account has its own access token, its own rotating refresh token and
// its own rate-limit bucket, which is why Refresh/Disconnect are per-account:
// `hermesRefresh(name, keyLabel)` / `hermesDisconnect(name, keyLabel)` forward
// the pool-key label the server uses to pick the target.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCcw, Unlink } from "lucide-react";
import { hermesDisconnect, hermesRefresh, hermesStatus } from "@/api/client";
import type { HermesAccountStatus } from "@/api/types";
import { Badge, Button } from "@/components/ui";

/** Epoch *seconds* → "Mar 4, 2027 (181d)". ``expires_at`` is absolute seconds
 *  (wiwi/providers/hermes_oauth.compute_expires_at), hence the ×1000. */
export function fmtHermesExpiry(epoch: number | null | undefined): string {
  if (!epoch) return "unknown";
  const ms = epoch * 1000;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "unknown";
  const days = (ms - Date.now()) / 86_400_000;
  const rel = days >= 1
    ? `${Math.floor(days)}d`
    : `${Math.max(0, Math.floor(days * 24))}h`;
  return `${d.toLocaleDateString()} (${rel})`;
}

export function useHermesStatusQuery(provider: string) {
  return useQuery({
    queryKey: ["hermes-oauth-status", provider],
    queryFn: () => hermesStatus(provider),
    refetchInterval: 30_000,
    retry: 1,
  });
}

export type HermesAccountActions = {
  refresh: (keyLabel: string) => void;
  disconnect: (keyLabel?: string) => void;
  /** Which (action, key_label) is in flight — used to spin only that row. */
  pending: { kind: "refresh" | "disconnect"; keyLabel: string } | null;
};

export function useHermesAccountActions(
  provider: string,
  opts: {
    /** Set the page error (null clears it). */
    onError: (m: string | null) => void;
    /** Human confirmation, e.g. the ``disconnected`` count. */
    onNotice?: (m: string) => void;
    /** Runs after a successful disconnect (closes a confirm dialog). */
    onDisconnected?: () => void;
  },
): HermesAccountActions {
  const qc = useQueryClient();

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["hermes-oauth-status", provider] });
    void qc.invalidateQueries({ queryKey: ["providers"] });
  };

  const refresh = useMutation({
    mutationFn: (keyLabel: string) => hermesRefresh(provider, keyLabel),
    onSuccess: (d, keyLabel) => {
      opts.onError(null);
      opts.onNotice?.(
        `Refreshed ${d.key_label ?? keyLabel} — new token expires ${fmtHermesExpiry(d.expires_at)}.`,
      );
      invalidate();
    },
    onError: (e) => opts.onError(e.message),
  });

  // No ``keyLabel`` = whole pool: the server drops every managed account.
  const disconnect = useMutation({
    mutationFn: (keyLabel?: string) => hermesDisconnect(provider, keyLabel),
    onSuccess: (d, keyLabel) => {
      opts.onError(null);
      opts.onNotice?.(
        keyLabel
          ? `Disconnected ${d.disconnected} account${d.disconnected === 1 ? "" : "s"} (${keyLabel}).`
          : `Disconnected ${d.disconnected} managed account${d.disconnected === 1 ? "" : "s"} from ${provider}.`,
      );
      opts.onDisconnected?.();
      invalidate();
    },
    onError: (e) => opts.onError(e.message),
  });

  let pending: HermesAccountActions["pending"] = null;
  if (refresh.isPending && refresh.variables) {
    pending = { kind: "refresh", keyLabel: refresh.variables };
  } else if (disconnect.isPending && disconnect.variables) {
    pending = { kind: "disconnect", keyLabel: disconnect.variables };
  }

  return {
    refresh: (keyLabel) => refresh.mutate(keyLabel),
    disconnect: (keyLabel) => disconnect.mutate(keyLabel),
    pending,
  };
}

/** One account: identity, expiry, refresh-token state, per-account actions.
 *  Unmanaged keys (a raw Nous API key, not an OAuth grant) get no actions —
 *  a disabled button would hide its reason behind hover, and there is nothing
 *  for the server to refresh or disconnect anyway. */
function HermesAccountRow(props: {
  a: HermesAccountStatus;
  actions: HermesAccountActions;
  disconnectVariant: "ghost" | "danger";
  onRequestDisconnect?: (keyLabel: string) => void;
}) {
  const { a, actions } = props;
  const busy = actions.pending?.keyLabel === a.key_label ? actions.pending.kind : null;
  const name = a.managed ? a.label || a.key_label : a.key_label;

  return (
    /* Stacked (never side-by-side): the console lays these cards out three
       across, so a container can be ~390 px wide even on a desktop viewport —
       a viewport `sm:` split would collide the actions with a long account
       email there. */
    <div className="flex flex-col gap-2">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="min-w-0 break-words text-[13px] font-medium text-[var(--admin-text)]">
            {name}
          </span>
          {a.managed ? (
            a.needs_refresh ? (
              <Badge tone="amber" title="Expired or within the 120 s refresh lead — refresh it or wait for the sweeper.">
                expires soon
              </Badge>
            ) : (
              <Badge tone="green">connected</Badge>
            )
          ) : (
            <Badge tone="gray" title="Pool key holds a raw Nous API key, not an OAuth grant.">
              unmanaged
            </Badge>
          )}
        </div>
        {name !== a.key_label && (
          <code className="mt-0.5 block break-all font-mono text-[11px] text-[var(--admin-text-dim)]">
            {a.key_label}
          </code>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--admin-text-muted)]">
          {a.managed ? (
            <>
              <span>
                Token expires{" "}
                <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
                  {fmtHermesExpiry(a.expires_at)}
                </span>
              </span>
              <span>
                {a.has_refresh_token
                  ? "refresh token stored"
                  : "no refresh token — reconnect to restore auto-refresh"}
              </span>
            </>
          ) : (
            <span>Usable for inference, never auto-refreshed.</span>
          )}
        </div>
      </div>

      {a.managed && (
        <div className="flex flex-wrap gap-2">
          {a.has_refresh_token && (
            <Button
              variant="outline"
              className="min-h-11"
              disabled={busy !== null}
              onClick={() => actions.refresh(a.key_label)}
            >
              {busy === "refresh" ? (
                <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
              ) : (
                <RefreshCcw size={14} />
              )}
              Refresh
            </Button>
          )}
          {a.managed && (
            <Button
              variant={props.disconnectVariant}
              className="min-h-11"
              disabled={busy !== null}
              onClick={() =>
                props.onRequestDisconnect
                  ? props.onRequestDisconnect(a.key_label)
                  : actions.disconnect(a.key_label)
              }
            >
              {busy === "disconnect" ? (
                <Loader2 size={14} className="animate-spin motion-reduce:animate-none" />
              ) : (
                <Unlink size={14} />
              )}
              Disconnect
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/** One row per pool key. A single-account pool renders borderless, matching
 *  the flat card this replaced; several accounts each get their own framed row
 *  that stacks on narrow viewports instead of overflowing. */
export function HermesAccountList(props: {
  accounts: HermesAccountStatus[];
  actions: HermesAccountActions;
  disconnectVariant?: "ghost" | "danger";
  /** Provider detail confirms a disconnect in a dialog; the console acts
   *  immediately, as its card has always done. */
  onRequestDisconnect?: (keyLabel: string) => void;
}) {
  const accounts = props.accounts ?? [];
  if (accounts.length === 0) {
    return (
      <p className="text-[12px] text-[var(--admin-text-muted)]">
        This provider has no pool keys, so there is nothing to authenticate.
      </p>
    );
  }
  const multi = accounts.length > 1;
  return (
    <div className={multi ? "space-y-2" : undefined}>
      {accounts.map((a, i) => (
        <div
          key={`${a.key_label}-${i}`}
          className={multi
            ? "rounded-lg border border-[var(--admin-border)] bg-white/[0.01] p-3"
            : undefined}
        >
          <HermesAccountRow
            a={a}
            actions={props.actions}
            disconnectVariant={props.disconnectVariant ?? "ghost"}
            onRequestDisconnect={props.onRequestDisconnect}
          />
        </div>
      ))}
    </div>
  );
}