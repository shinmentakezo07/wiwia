// OAuth Cline — manage Cline OAuth accounts and cross-account routing.
//
// This page lists every configured Cline provider. For each you can:
//   • Add a new Cline provider (creates the account with a placeholder key,
//     since the real access token lands on that key after connect).
//   • Connect — automatic redirect flow: click once, log in at Cline, done.
//     A manual paste-code fallback is available for setups without a public
//     callback URL (e.g. localhost dev).
//   • See connection status (email, token expiry, auto-refresh badge).
//   • Refresh the access token on demand.
//   • Disconnect — clear the stored OAuth state.
//
// When 2+ accounts are connected, a cross-account routing card lets you
// create a model group that deploys to all Cline providers and toggle the
// routing strategy between round-robin and fallback.

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Globe2,
  Link2,
  Loader2,
  LogIn,
  Plus,
  RefreshCw,
  Search,
  Shuffle,
  Trash2,
  Unlink,
} from "lucide-react";
import {
  addDeployment,
  addProvider,
  api,
  clineConnect,
  clineDisconnect,
  clineLoginUrl,
  clineRefresh,
  clineStatus,
  deleteClineDefaultModel,
  fetchClineModels,
  getClineSettings,
  getModels,
  getProviders,
  patchModelGroup,
  putClineSettings,
} from "@/api/client";
import type {
  ClineStatusResponse,
  ModelGroup,
  Provider,
  UpstreamModel,
} from "@/api/types";
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
  Toggle,
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

  // Connect: open Cline's login page. After login, Cline shows a callback
  // page with ?code=… — the user copies that URL and pastes it below.
  // (Cline's Google OAuth ignores our callback_url and lands on its own
  // /auth/callback page, so we can't auto-redirect; the paste-code flow
  // accepts the full callback URL and handles truncated Google OAuth codes.)
  const autoConnect = useMutation({
    mutationFn: () => clineLoginUrl(`${window.location.origin}/console/oauth`),
    onSuccess: (d) => {
      setError(null);
      setShowManual(true);
      window.open(d.auth_url, "_blank", "noopener,noreferrer");
    },
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
                Click to open Cline's login page. After signing in, copy the
                URL from your browser's address bar and paste it below.
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
                  {autoConnect.isPending ? "Opening…" : "Connect with Cline"}
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
                  Paste URL / code
                </Button>
              </div>
            </div>

            {/* collapsible paste section — accepts the full Cline callback URL or bare code */}
            {showManual && (
              <div className="space-y-2 border-t border-[var(--admin-border)] pt-3">
                <p className="text-[12px] text-[var(--admin-text-muted)]">
                  After logging in at Cline, copy the full URL from the
                  address bar (it starts with https://app.cline.bot/auth/
                  callback?…code=…) and paste it here.
                </p>
                <textarea
                  className="admin-input min-h-20 resize-none font-mono text-[12px]"
                  placeholder="https://app.cline.bot/auth/callback?…code=…"
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

// -- global default model ------------------------------------------------------
// Pick model ids once and have them auto-deployed to every Cline account
// (existing + future).  The backend creates a router group
// ``cline:<model_id>`` with one Deployment per Cline provider and a
// cross-provider WRR cursor, so requests smooth-round-robin across
// accounts.  Catalog comes from a 5-minute in-memory cache on the
// backend; ``?refresh=true`` busts it.

function GlobalDefaultModelCard(props: {
  providers: Provider[];
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const settingsQ = useQuery({
    queryKey: ["cline-settings"],
    queryFn: getClineSettings,
  });
  const catalogQ = useQuery({
    queryKey: ["cline-catalog"],
    queryFn: fetchClineModels,
    refetchOnWindowFocus: false,
  });

  const savedIds: string[] = settingsQ.data?.default_models ?? [];
  const catalog: { id: string }[] = catalogQ.data?.models ?? [];

  // Combine the saved ids (top of the picker, even if not in catalog) with
  // the live catalog so the admin sees both.
  const allOptions = useMemo(() => {
    const out = new Map<string, { id: string; source: "saved" | "catalog" }>();
    for (const id of savedIds) out.set(id, { id, source: "saved" });
    for (const m of catalog) {
      if (!out.has(m.id)) out.set(m.id, { id: m.id, source: "catalog" });
    }
    return Array.from(out.values());
  }, [savedIds, catalog]);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Sync local selection with the persisted list once it loads.
  useEffect(() => {
    if (settingsQ.data) setSelected(new Set(settingsQ.data.default_models));
  }, [settingsQ.data]);

  // Free-text filter on the catalog (case-insensitive substring match).
  // Saved ids always stay visible even if they aren't in the live catalog,
  // so the admin can still see + remove what they previously saved.
  const [search, setSearch] = useState("");
  const filteredOptions = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allOptions;
    return allOptions.filter((m) => m.id.toLowerCase().includes(q));
  }, [allOptions, search]);

  const isSelected = (id: string) => selected.has(id);
  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < 32) next.add(id);
      return next;
    });
  };

  const save = useMutation({
    mutationFn: () => putClineSettings(Array.from(selected)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cline-settings"] });
      void qc.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const removeOne = useMutation({
    mutationFn: (id: string) => deleteClineDefaultModel(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cline-settings"] });
      void qc.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const refresh = useMutation({
    mutationFn: () =>
      api<{ models: UpstreamModel[] }>("/admin/cline/models?refresh=true"),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["cline-catalog"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const dirty = useMemo(() => {
    const a = Array.from(selected).sort();
    const b = [...savedIds].sort();
    return a.length !== b.length || a.some((x, i) => x !== b[i]);
  }, [selected, savedIds]);

  const clineCount = props.providers.filter((p) => p.provider_type === "cline").length;

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Globe2 size={15} className="text-[var(--admin-text-muted)]" />
            Global default model
          </span>
        }
        right={
          <div className="flex items-center gap-2">
            <Badge tone={savedIds.length > 0 ? "green" : "gray"}>
              {savedIds.length} default{savedIds.length !== 1 ? "s" : ""}
            </Badge>
            <Button
              variant="ghost"
              disabled={refresh.isPending || catalogQ.isLoading}
              onClick={() => refresh.mutate()}
            >
              <RefreshCw
                size={12}
                className={refresh.isPending ? "animate-spin" : ""}
              />
              Refresh catalog
            </Button>
          </div>
        }
      />
      <div className="space-y-4 px-4 pb-4 pt-2">
        <p className="text-[12px] text-[var(--admin-text-muted)]">
          Pick a model id once and it auto-deploys to every Cline account
          (existing + new). Requests to <code>cline:&lt;id&gt;</code> then
          smooth-round-robin across all {clineCount} account
          {clineCount !== 1 ? "s" : ""} via the cross-provider WRR cursor.
        </p>

        {allOptions.length === 0 && catalogQ.isLoading && (
          <div className="flex items-center gap-2 text-[12px] text-[var(--admin-text-muted)]">
            <Loader2 size={12} className="animate-spin" /> Loading catalog…
          </div>
        )}

        {allOptions.length === 0 && !catalogQ.isLoading && (
          <div className="text-[12px] text-[var(--admin-text-muted)]">
            No Cline catalog available — connect a Cline account first.
          </div>
        )}

        {allOptions.length > 0 && (
          <div className="space-y-2">
            <div className="relative">
              <Search
                size={13}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--admin-text-muted)]"
              />
              <Input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search model id (e.g. glm-5.2, claude-sonnet-5)"
                className="pl-8"
              />
            </div>
            <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-2">
              {filteredOptions.length === 0 && (
                <div className="px-2 py-3 text-center text-[12px] text-[var(--admin-text-muted)]">
                  No model ids match “{search}”.
                </div>
              )}
              {filteredOptions.map((m) => (
                <label
                  key={m.id}
                  className="flex cursor-pointer items-center justify-between gap-2 rounded px-2 py-1.5 hover:bg-white/[0.04]"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <input
                      type="checkbox"
                      checked={isSelected(m.id)}
                      onChange={() => toggle(m.id)}
                      className="h-3.5 w-3.5 cursor-pointer accent-blue-500"
                    />
                    <span className="truncate font-mono text-[12px]">{m.id}</span>
                    {m.source === "saved" && (
                      <Badge tone="blue">saved</Badge>
                    )}
                  </div>
                </label>
              ))}
            </div>
            {search && (
              <div className="text-[11px] text-[var(--admin-text-muted)]">
                Showing {filteredOptions.length} of {allOptions.length} models.
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="ml-2 text-blue-300 hover:underline"
                >
                  clear
                </button>
              </div>
            )}
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-[var(--admin-border)] pt-3">
          <div className="text-[12px] text-[var(--admin-text-muted)]">
            {selected.size} selected
            {selected.size >= 32 ? " (max)" : ""}
          </div>
          <div className="flex items-center gap-2">
            {savedIds.length > 0 && (
              <Button
                variant="ghost"
                disabled={removeOne.isPending}
                onClick={() => {
                  // Remove all saved defaults one at a time (simple, safe).
                  for (const id of savedIds) removeOne.mutate(id);
                }}
              >
                <Trash2 size={12} />
                Clear all
              </Button>
            )}
            <Button
              disabled={!dirty || save.isPending || selected.size === 0}
              onClick={() => save.mutate()}
            >
              {save.isPending ? <Loader2 size={12} className="animate-spin" /> : null}
              Save
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}

// -- cross-account routing -----------------------------------------------------

function CrossAccountRouting(props: {
  providers: Provider[];
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [modelId, setModelId] = useState("z-ai/glm-5.2");
  const [groupName, setGroupName] = useState("cline-pool");

  // Fetch existing model groups to show current routing state.
  const modelsQ = useQuery({
    queryKey: ["models"],
    queryFn: getModels,
    refetchInterval: 15_000,
  });

  // Find model groups that deploy to any of our Cline providers.
  const clineProviderNames = useMemo(
    () => new Set(props.providers.map((p) => p.name)),
    [props.providers],
  );

  const clineGroups: ModelGroup[] = useMemo(
    () =>
      (modelsQ.data?.groups ?? []).filter((g) =>
        g.deployments.some((d) => clineProviderNames.has(d.provider)),
      ),
    [modelsQ.data, clineProviderNames],
  );

  const globalStrategy = modelsQ.data?.strategy ?? "simple-shuffle";

  // Create a model group spanning all Cline providers with the given model.
  const createPool = useMutation({
    mutationFn: async () => {
      for (const p of props.providers) {
        await addDeployment(groupName, {
          provider: p.name,
          model_id: modelId,
          weight: 1,
        });
      }
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (e) => props.onError(e.message),
  });

  // Toggle routing strategy between round-robin and fallback (sequential).
  const toggleStrategy = useMutation({
    mutationFn: (strategy: string) =>
      patchModelGroup(clineGroups[0]?.name ?? groupName, { strategy }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const isRoundRobin = globalStrategy === "simple-shuffle";

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Shuffle size={15} className="text-[var(--admin-text-muted)]" />
            Cross-account routing
          </span>
        }
        right={
          <Badge tone={isRoundRobin ? "green" : "amber"}>
            {isRoundRobin ? "round-robin" : "fallback"}
          </Badge>
        }
      />
      <div className="space-y-4 px-4 pb-4 pt-2">
        <p className="text-[12px] text-[var(--admin-text-muted)]">
          {props.providers.length} Cline accounts configured. Create a model
          group that deploys to all of them so requests are load-balanced
          across accounts.
        </p>

        {/* existing groups that include Cline providers */}
        {clineGroups.length > 0 && (
          <div className="space-y-2">
            <div className="text-[13px] font-medium">Active routing groups</div>
            {clineGroups.map((g) => (
              <div
                key={g.name}
                className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] px-3 py-2"
              >
                <div className="flex items-center justify-between">
                  <Link
                    to="/console/models"
                    className="text-[13px] font-medium text-blue-300 hover:underline"
                  >
                    {g.name}
                  </Link>
                  <Badge tone="gray">
                    {g.deployments.length} deployment{g.deployments.length !== 1 ? "s" : ""}
                  </Badge>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {g.deployments.map((d) => (
                    <span
                      key={`${d.provider}/${d.model_id}`}
                      className="rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-[var(--admin-text-muted)]"
                    >
                      {d.provider}/{d.model_id}
                    </span>
                  ))}
                </div>
              </div>
            ))}

            {/* routing strategy toggle */}
            <div className="flex items-center justify-between gap-3 border-t border-[var(--admin-border)] pt-3">
              <div className="min-w-0">
                <div className="text-[13px] font-medium">Routing strategy</div>
                <div className="text-[12px] text-[var(--admin-text-muted)]">
                  {isRoundRobin
                    ? "Round-robin — distribute requests across all accounts evenly."
                    : "Fallback — use the first account until it fails, then move to the next."}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--admin-text-muted)]">
                  {isRoundRobin ? "Round-robin" : "Fallback"}
                </span>
                <Toggle
                  checked={isRoundRobin}
                  disabled={toggleStrategy.isPending}
                  onChange={(v) =>
                    toggleStrategy.mutate(v ? "simple-shuffle" : "least-busy")
                  }
                />
              </div>
            </div>
          </div>
        )}

        {/* create a new pool spanning all Cline accounts */}
        {clineGroups.length === 0 && (
          <div className="space-y-3 border-t border-[var(--admin-border)] pt-3">
            <div className="text-[13px] font-medium">Create a routing pool</div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-[var(--admin-text-muted)]">
                  Model group name
                </label>
                <Input
                  value={groupName}
                  onChange={(e) => setGroupName(e.target.value)}
                  placeholder="cline-pool"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-[var(--admin-text-muted)]">
                  Upstream model ID
                </label>
                <Input
                  value={modelId}
                  onChange={(e) => setModelId(e.target.value)}
                  placeholder="z-ai/glm-5.2"
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                disabled={!groupName.trim() || !modelId.trim() || createPool.isPending}
                onClick={() => createPool.mutate()}
              >
                {createPool.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Plus size={14} />
                )}
                {createPool.isPending ? "Creating…" : "Create pool"}
              </Button>
              <span className="text-[12px] text-[var(--admin-text-muted)]">
                Adds {props.providers.length} deployment{props.providers.length !== 1 ? "s" : ""} to the group.
              </span>
            </div>
          </div>
        )}
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

      {oauthProviders.length >= 1 && (
        <div className="mb-4">
          <GlobalDefaultModelCard
            providers={oauthProviders}
            onError={setError}
          />
        </div>
      )}

      {oauthProviders.length >= 2 && (
        <div className="mb-4">
          <CrossAccountRouting
            providers={oauthProviders}
            onError={setError}
          />
        </div>
      )}

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
