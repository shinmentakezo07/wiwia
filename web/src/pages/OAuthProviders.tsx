// Connected accounts — the single place to add and manage every OAuth-backed
// provider account this gateway holds: Cline, WorkBuddy, and Hermes (Nous
// Portal).
//
// Each provider keeps its own flow, because their OAuth shapes genuinely
// differ: Cline needs a callback pasted back by hand, WorkBuddy hands back a
// login URL and is polled, and Hermes uses RFC 8628 device authorization. The
// tabs isolate those flows without duplicating any of them — the Cline pieces
// live in `oauth-shared.tsx`/`oauth-routing.tsx`, WorkBuddy in
// `workbuddy-shared.tsx`, and Hermes in `HermesOAuth.tsx`.
//
// Panels stay mounted while hidden so an in-progress sign-in survives a tab
// switch: WorkBuddy and Hermes both poll a server-side session, and unmounting
// the panel would abandon that loop mid-approval.

import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Briefcase,
  Hexagon,
  Link2,
  Loader2,
  Plus,
  RefreshCw,
  Unlink,
  Users,
} from "lucide-react";
import { addProvider, getProviders, workbuddyAccounts } from "@/api/client";
import type { Provider } from "@/api/types";
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
import { HermesOAuthPanel } from "@/pages/HermesOAuth";
import { WorkBuddyOAuthPanel } from "@/pages/oauth-workbuddy";

const CLINE_DEFAULT_BASE = "https://api.cline.bot/api/v1";

// -- add Cline account ----------------------------------------------------------

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
      title="Add Cline account"
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

// -- Cline account card ---------------------------------------------------------

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
              to={`/console/oauth/${encodeURIComponent(props.p.name)}/${encodeURIComponent(props.p.name)}`}
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

// -- Cline panel ----------------------------------------------------------------

function ClineAccountsPanel() {
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
        .filter((p) => p.provider_type === "cline")
        .sort((a, b) => a.name.localeCompare(b.name)),
    [q.data],
  );

  const names = useMemo(() => providers.map((p) => p.name), [providers]);
  const { byName, connectedNames } = useClineStatusIndex(names);

  const attention = connectedNames.filter((n) => byName.get(n)?.data?.needs_refresh).length;

  return (
    <div>
      {error && <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert>}

      <ClineCallbackAlert result={banner.result} onDismiss={banner.clear} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="admin-label">Cline accounts</h3>
          <p className="mt-1 text-[12px] text-[var(--admin-text-muted)]">
            {providers.length} configured · {connectedNames.length} connected
            {attention > 0 ? ` · ${attention} expiring soon` : ""}
          </p>
        </div>
        <Button className="min-h-11" onClick={() => setAddOpen(true)}>
          <Plus size={14} aria-hidden />
          Add account
        </Button>
      </div>

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
        <Card className="mt-4">
          <EmptyState
            icon={Users}
            title="No Cline accounts yet"
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

// -- provider chooser / provider page ------------------------------------------

type OAuthTab = "cline" | "workbuddy" | "hermes";

const OAUTH_TABS: {
  id: OAuthTab;
  label: string;
  icon: typeof Link2;
  blurb: string;
}[] = [
  {
    id: "cline",
    label: "Cline",
    icon: Link2,
    blurb:
      "Route requests through your Cline subscription. Connecting opens Cline's login and asks you to paste the callback URL back.",
  },
  {
    id: "workbuddy",
    label: "WorkBuddy",
    icon: Briefcase,
    blurb:
      "Sign in with a WorkBuddy / CodeBuddy account. The provider is created on first login, and bulk import/export stays available.",
  },
  {
    id: "hermes",
    label: "Hermes",
    icon: Hexagon,
    blurb:
      "Sign in to Nous Portal with the device-code flow. No API key is needed — the account is created when you approve the sign-in.",
  },
];

function normalizeTab(value: string | undefined): OAuthTab | null {
  const v = (value ?? "").toLowerCase();
  const match = OAUTH_TABS.find((t) => t.id === v);
  return match ? match.id : null;
}

function useOAuthCounts() {
  const providersQ = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
    refetchInterval: 15_000,
  });
  const workbuddyQ = useQuery({
    queryKey: ["workbuddy-accounts"],
    queryFn: workbuddyAccounts,
    refetchInterval: 15_000,
  });
  const providers = providersQ.data?.providers ?? [];
  const counts: Record<OAuthTab, number> = {
    cline: providers.filter((p) => p.provider_type === "cline").length,
    workbuddy: workbuddyQ.data?.accounts.length ?? 0,
    hermes: providers
      .filter((p) => p.provider_type === "hermes")
      .reduce((n, p) => n + p.keys.length, 0),
  };
  return { providersQ, counts };
}

/** The landing page at /console/oauth — an explicit provider choice instead of
 *  silently defaulting to Cline. Each card opens /console/oauth/<provider>. */
export function OAuthProvidersPage() {
  const { providersQ, counts } = useOAuthCounts();

  return (
    <div>
      <PageHeader
        title="Connected accounts"
        subtitle="Choose an OAuth provider to add or manage its accounts"
      />

      {providersQ.isError && (
        <Alert
          tone="danger"
          title="Could not load accounts"
          action={
            <Button
              variant="ghost"
              className="min-h-11"
              disabled={providersQ.isFetching}
              onClick={() => void providersQ.refetch()}
            >
              <RefreshCw
                size={14}
                aria-hidden
                className={`motion-reduce:animate-none ${providersQ.isFetching ? "animate-spin" : ""}`}
              />
              Retry
            </Button>
          }
        >
          {providersQ.error.message}
        </Alert>
      )}

      <div className="admin-stagger grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {OAUTH_TABS.map((t) => {
          const Icon = t.icon;
          const count = counts[t.id];
          return (
            <Card key={t.id} className="flex flex-col">
              <CardHeader
                title={
                  <span className="flex items-center gap-2">
                    <Icon
                      size={15}
                      className="shrink-0 text-[var(--admin-text-muted)]"
                      aria-hidden
                    />
                    {t.label}
                  </span>
                }
                right={
                  <Badge tone={count > 0 ? "green" : "gray"}>
                    {count} account{count !== 1 ? "s" : ""}
                  </Badge>
                }
              />
              <div className="flex flex-1 flex-col gap-4 px-5 pb-5 pt-4">
                <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                  {t.blurb}
                </p>
                <div className="mt-auto">
                  <Link to={`/console/oauth/${t.id}`} className="inline-flex">
                    <Button className="min-h-11">
                      Open {t.label}
                      <ArrowRight size={14} aria-hidden />
                    </Button>
                  </Link>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

/** One provider's accounts at /console/oauth/<provider>. The tab bar switches
 *  providers by URL, so a refresh or a deep link lands on the same panel. */
export function OAuthProviderPage() {
  const { provider } = useParams();
  const navigate = useNavigate();
  const tab = normalizeTab(provider);
  const tabsRef = useRef<HTMLDivElement>(null);
  const { counts } = useOAuthCounts();

  // Arrow keys move between tabs and activate on move; Home/End jump to the
  // ends. Tab itself leaves the group, matching the tabs pattern used in the
  // Playground.
  const onTabKeyDown = (e: React.KeyboardEvent) => {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(e.key) || !tab) return;
    e.preventDefault();
    const idx = OAUTH_TABS.findIndex((t) => t.id === tab);
    const next =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? OAUTH_TABS.length - 1
          : e.key === "ArrowLeft"
            ? (idx - 1 + OAUTH_TABS.length) % OAUTH_TABS.length
            : (idx + 1) % OAUTH_TABS.length;
    const target = OAUTH_TABS[next];
    if (!target) return;
    navigate(`/console/oauth/${target.id}`);
    requestAnimationFrame(() => {
      tabsRef.current
        ?.querySelector<HTMLButtonElement>(`[data-tab="${target.id}"]`)
        ?.focus();
    });
  };

  if (!tab) {
    return (
      <div>
        <PageHeader
          title="Connected accounts"
          subtitle={`Unknown OAuth provider “${provider ?? ""}”.`}
        />
        <Link to="/console/oauth">
          <Button variant="outline" className="min-h-11">
            <ArrowLeft size={14} aria-hidden /> Choose a provider
          </Button>
        </Link>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Connected accounts"
        subtitle="Cline, WorkBuddy, and Hermes accounts — choose a provider"
        right={
          <Link to="/console/oauth">
            <Button variant="ghost" className="min-h-11">
              <ArrowLeft size={14} aria-hidden /> All providers
            </Button>
          </Link>
        }
      />

      <div
        ref={tabsRef}
        role="tablist"
        aria-label="OAuth provider"
        onKeyDown={onTabKeyDown}
        className="inline-flex max-w-full items-center gap-1 overflow-x-auto rounded-xl border border-[var(--admin-border)] bg-white/[0.02] p-1"
      >
        {OAUTH_TABS.map((t) => {
          const active = t.id === tab;
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`oauth-tab-${t.id}`}
              data-tab={t.id}
              aria-selected={active}
              aria-controls={`oauth-panel-${t.id}`}
              tabIndex={active ? 0 : -1}
              onClick={() => navigate(`/console/oauth/${t.id}`)}
              className={`flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg px-3.5 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 ${
                active
                  ? "bg-white/[0.09] text-[var(--admin-text)] shadow-sm"
                  : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
              }`}
            >
              <Icon
                size={13}
                aria-hidden
                className={active ? "text-blue-300" : "text-[var(--admin-text-dim)]"}
              />
              {t.label}
              <span
                className={`ml-0.5 rounded px-1.5 py-0.5 font-mono text-[10px] tabular-nums ${
                  active
                    ? "bg-white/[0.08] text-[var(--admin-text)]"
                    : "bg-white/[0.03] text-[var(--admin-text-dim)]"
                }`}
              >
                {counts[t.id]}
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-5">
        <div
          role="tabpanel"
          id="oauth-panel-cline"
          aria-labelledby="oauth-tab-cline"
          hidden={tab !== "cline"}
        >
          <ClineAccountsPanel />
        </div>
        <div
          role="tabpanel"
          id="oauth-panel-workbuddy"
          aria-labelledby="oauth-tab-workbuddy"
          hidden={tab !== "workbuddy"}
        >
          <WorkBuddyOAuthPanel />
        </div>
        <div
          role="tabpanel"
          id="oauth-panel-hermes"
          aria-labelledby="oauth-tab-hermes"
          hidden={tab !== "hermes"}
        >
          <HermesOAuthPanel />
        </div>
      </div>
    </div>
  );
}
