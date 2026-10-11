// Cross-account routing and global default models for Cline accounts.
//
// Split out of OAuthProviders.tsx: these two cards configure model *groups*
// rather than accounts, so they depend on `getModels`/`addDeployment` rather than
// on provider status, and they are gated on different thresholds.

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Globe2,
  Link as LinkIcon,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Shuffle,
  Trash2,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  addDeployment,
  api,
  deleteClineDefaultModel,
  fetchClineModels,
  getClineSettings,
  getModels,
  putClineSettings,
} from "@/api/client";
import type { ModelGroup, Provider, UpstreamModel } from "@/api/types";
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
} from "@/components/ui";

const MAX_DEFAULTS = 32;

// -- global default models ------------------------------------------------------

/** Model ids deployed to every Cline account, existing and future. The backend
 *  reconciles these into `cline:<id>` model groups on save. */
export function DefaultModelsCard(props: {
  providers: Provider[];
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const settingsQ = useQuery({ queryKey: ["cline-settings"], queryFn: getClineSettings });
  const catalogQ = useQuery({
    queryKey: ["cline-catalog"],
    queryFn: fetchClineModels,
    refetchOnWindowFocus: false,
  });

  // Memoised on `settingsQ.data` rather than `settingsQ.data?.default_models ?? []`,
  // because the `??` builds a fresh array every render and would make every
  // identity comparison downstream always report "changed".
  const savedIds = useMemo(
    () => settingsQ.data?.default_models ?? [],
    [settingsQ.data],
  );
  const catalog: UpstreamModel[] = useMemo(
    () => catalogQ.data?.models ?? [],
    [catalogQ.data],
  );

  // `null` means "pristine — no local edits, render straight from the server".
  // The previous version kept a plain Set and synced it with a useEffect on
  // `settingsQ.data`, which re-ran on *every* refetch (including the default
  // refetch-on-window-focus) and silently discarded whatever the operator had
  // ticked since. Reading through until the first local edit, and only resyncing
  // when the saved value genuinely changes identity, keeps unsaved work.
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const savedSet = useMemo(() => new Set(savedIds), [savedIds]);
  const current = selected ?? savedSet;

  const [search, setSearch] = useState("");
  const [confirmRemoveAll, setConfirmRemoveAll] = useState(false);

  // Saved ids stay listed even when the catalog no longer offers them, so an
  // operator can always see — and drop — what they previously configured.
  const allOptions = useMemo(() => {
    const out = new Map<string, "saved" | "catalog">();
    for (const id of savedIds) out.set(id, "saved");
    for (const m of catalog) if (!out.has(m.id)) out.set(m.id, "catalog");
    return Array.from(out.entries());
  }, [savedIds, catalog]);

  const filteredOptions = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allOptions;
    return allOptions.filter(([id]) => id.toLowerCase().includes(q));
  }, [allOptions, search]);

  const atCap = current.size >= MAX_DEFAULTS;
  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev ?? savedIds);
      if (next.has(id)) next.delete(id);
      else if (next.size < MAX_DEFAULTS) next.add(id);
      return next;
    });
  };

  const dirty = useMemo(() => {
    const a = Array.from(current).sort();
    const b = [...savedIds].sort();
    return a.length !== b.length || a.some((x, i) => x !== b[i]);
  }, [current, savedIds]);

  const save = useMutation({
    mutationFn: (ids: string[]) => putClineSettings(ids),
    onSuccess: () => {
      setSelected(null);
      void qc.invalidateQueries({ queryKey: ["cline-settings"] });
      void qc.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const refreshCatalog = useMutation({
    mutationFn: () => api<{ models: UpstreamModel[] }>("/admin/cline/models?refresh=true"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["cline-catalog"] }),
    onError: (e) => props.onError(e.message),
  });

  // SERIALIZED ON PURPOSE — do not "simplify" this into Promise.all.
  //
  // Every DELETE is an unsynchronized read-modify-write on the single
  // `cline_settings:default_models` row: the handler reads the list, filters one
  // id, writes it back. Firing them concurrently let each request read the same
  // pre-state, so the last writer won and N-1 defaults silently survived while
  // the UI reported success (AUDIT #209). Awaiting each in turn makes every read
  // observe the previous write.
  //
  // A single PUT of the remainder is NOT equivalent: `_apply_cline_default_models`
  // only *adds* deployments, so a PUT cannot drop the `cline:<id>` group that the
  // per-id DELETE removes. Serializing preserves the server's exact semantics.
  const removeAll = useMutation({
    mutationFn: async () => {
      for (const id of savedIds) await deleteClineDefaultModel(id);
    },
    onSuccess: () => {
      setSelected(null);
      setConfirmRemoveAll(false);
      void qc.invalidateQueries({ queryKey: ["cline-settings"] });
      void qc.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (e) => props.onError(e.message),
  });

  // Reset local edits whenever the persisted list actually changes underneath us.
  useEffect(() => {
    setSelected(null);
  }, [savedIds]);

  const loading = catalogQ.isLoading && allOptions.length === 0;

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Globe2 size={15} className="text-[var(--admin-text-muted)]" aria-hidden />
            Default models
          </span>
        }
        right={
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={savedIds.length > 0 ? "green" : "gray"}>
              {savedIds.length} default{savedIds.length !== 1 ? "s" : ""}
            </Badge>
            <Button
              variant="ghost"
              className="min-h-11"
              disabled={refreshCatalog.isPending || catalogQ.isFetching}
              onClick={() => refreshCatalog.mutate()}
            >
              <RefreshCw
                size={12}
                aria-hidden
                className={`motion-reduce:animate-none ${
                  refreshCatalog.isPending || catalogQ.isFetching ? "animate-spin" : ""
                }`}
              />
              Refresh catalog
            </Button>
          </div>
        }
      />
      <div className="space-y-4 px-5 pb-5 pt-4">
        <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
          Pick a model id once and it is deployed to every Cline account — the{" "}
          {props.providers.length} configured here and any added later. Requests to{" "}
          <code className="font-mono text-[11px]">cline:&lt;id&gt;</code> then round-robin
          across those accounts.
        </p>

        {settingsQ.isError && (
          <ErrorText>Could not load saved defaults: {settingsQ.error.message}</ErrorText>
        )}

        {loading ? (
          <div className="flex items-center gap-2 py-2 text-[12px] text-[var(--admin-text-muted)]">
            <Loader2
              size={12}
              aria-hidden
              className="animate-spin motion-reduce:animate-none"
            />
            Loading catalog…
          </div>
        ) : allOptions.length === 0 ? (
          <EmptyState icon={Globe2} title="No Cline catalog available">
            {catalogQ.isError
              ? "The catalog could not be fetched. Check that the gateway can reach Cline, then refresh."
              : "Connect a Cline account first — the model catalog is read from it."}
          </EmptyState>
        ) : (
          <div className="space-y-2">
            <Field label="Search models">
              <div className="relative">
                <Search
                  size={13}
                  aria-hidden
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--admin-text-muted)]"
                />
                <Input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Filter by model id"
                  className="pl-8"
                />
              </div>
            </Field>

            <div className="max-h-56 overflow-y-auto rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-1.5 sm:max-h-72">
              {filteredOptions.length === 0 ? (
                <p className="px-2 py-4 text-center text-[12px] text-[var(--admin-text-muted)]">
                  No model ids match “{search}”.
                </p>
              ) : (
                filteredOptions.map(([id, source]) => {
                  const on = current.has(id);
                  return (
                    <label
                      key={id}
                      className={`flex min-h-11 cursor-pointer items-center justify-between gap-2 rounded px-3 py-2 transition-colors hover:bg-white/[0.04] focus-within:bg-white/[0.04] ${
                        on ? "bg-white/[0.04]" : ""
                      }`}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() => toggle(id)}
                          className="h-4 w-4 shrink-0 cursor-pointer accent-blue-500"
                        />
                        <span className="truncate font-mono text-[12px]">{id}</span>
                      </span>
                      {source === "saved" && <Badge tone="blue">saved</Badge>}
                    </label>
                  );
                })
              )}
            </div>

            {search && (
              <p className="text-[11px] text-[var(--admin-text-muted)]">
                Showing {filteredOptions.length} of {allOptions.length} models.{" "}
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="min-h-11 rounded px-1 text-blue-300 hover:underline"
                >
                  Clear filter
                </button>
              </p>
            )}
          </div>
        )}

        {/* Removing an id from the list and deleting its deployments are two
            different operations with two different server paths. The old UI
            disabled Save at zero selections, which is the one state where the
            difference matters most, and gave no way to discover why. */}
        {current.size === 0 && savedIds.length > 0 && (
          <Alert tone="warning">
            Nothing selected. Saving removes these ids from the default list and stops
            future auto-deploys. It does not delete the deployments already created —
            use <strong>Remove all defaults</strong> to remove the model groups too.
          </Alert>
        )}

        <div className="flex flex-col gap-3 border-t border-[var(--admin-border)] pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p id="defaults-status" className="text-[12px] text-[var(--admin-text-muted)]">
            {current.size} selected{atCap ? ` (maximum ${MAX_DEFAULTS})` : ""}
            {dirty ? " · unsaved" : ""}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {savedIds.length > 0 && (
              <Button
                variant="danger"
                className="min-h-11"
                disabled={removeAll.isPending}
                onClick={() => setConfirmRemoveAll(true)}
              >
                <Trash2 size={12} aria-hidden />
                Remove all defaults
              </Button>
            )}
            <Button
              className="min-h-11"
              aria-describedby="defaults-status"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate(Array.from(current))}
            >
              {save.isPending ? (
                <Loader2 size={12} aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : null}
              Save
            </Button>
          </div>
        </div>
      </div>

      <Dialog
        open={confirmRemoveAll}
        onClose={() => setConfirmRemoveAll(false)}
        icon={Trash2}
        title="Remove all default models?"
        footer={
          <>
            <Button variant="ghost" className="min-h-11" onClick={() => setConfirmRemoveAll(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              className="min-h-11"
              disabled={removeAll.isPending}
              onClick={() => removeAll.mutate()}
            >
              {removeAll.isPending ? (
                <Loader2 size={14} aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : (
                <Trash2 size={14} aria-hidden />
              )}
              {removeAll.isPending ? "Removing…" : `Remove ${savedIds.length}`}
            </Button>
          </>
        }
      >
        <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
          Removes {savedIds.length} default model
          {savedIds.length !== 1 ? "s" : ""} and deletes the model groups created for
          them. Your Cline accounts stay connected — only the auto-created routing is
          removed. This cannot be undone from here.
        </p>
      </Dialog>
    </Card>
  );
}

// -- cross-account routing ------------------------------------------------------

/** A model group that deploys one model across several Cline accounts, so the
 *  router spreads requests over them. */
export function CrossAccountRoutingCard(props: {
  providers: Provider[];
  /** Only these accounts have a live OAuth record. Deploying to the rest would
   *  create deployments that 401 on first use. */
  connectedNames: string[];
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [modelId, setModelId] = useState("");
  const [groupName, setGroupName] = useState("cline-pool");

  const modelsQ = useQuery({
    queryKey: ["models"],
    queryFn: getModels,
    refetchInterval: 15_000,
  });

  const providerNames = useMemo(
    () => new Set(props.providers.map((p) => p.name)),
    [props.providers],
  );
  const clineGroups: ModelGroup[] = useMemo(
    () =>
      (modelsQ.data?.groups ?? []).filter((g) =>
        g.deployments.some((d) => providerNames.has(d.provider)),
      ),
    [modelsQ.data, providerNames],
  );

  const skipped = props.providers.length - props.connectedNames.length;

  const createPool = useMutation({
    mutationFn: async () => {
      for (const name of props.connectedNames) {
        await addDeployment(groupName.trim(), { provider: name, model_id: modelId.trim(), weight: 1 });
      }
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e) => props.onError(e.message),
  });

  const strategy = modelsQ.data?.strategy ?? "simple-shuffle";

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Shuffle size={15} className="text-[var(--admin-text-muted)]" aria-hidden />
            Cross-account routing
          </span>
        }
        right={<Badge tone="gray">{clineGroups.length} group{clineGroups.length !== 1 ? "s" : ""}</Badge>}
      />
      <div className="space-y-4 px-5 pb-5 pt-4">
        <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
          A model group that deploys one model to several connected accounts spreads
          requests across them.
        </p>

        {skipped > 0 && (
          <Alert tone="info">
            {skipped} configured account{skipped !== 1 ? "s are" : " is"} not connected and
            will be skipped when creating a pool.
          </Alert>
        )}

        {clineGroups.length > 0 ? (
          <div className="space-y-2">
            <h4 className="text-[13px] font-medium">Active routing groups</h4>
            {clineGroups.map((g) => (
              <div
                key={g.name}
                className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] px-3 py-2.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <Link
                    to="/console/models"
                    className="truncate text-[13px] font-medium text-blue-300 hover:underline"
                  >
                    {g.name}
                  </Link>
                  <Badge tone="gray">
                    {g.deployments.length} deployment{g.deployments.length !== 1 ? "s" : ""}
                  </Badge>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
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
          </div>
        ) : (
          <div className="space-y-3 border-t border-[var(--admin-border)] pt-4">
            <h4 className="text-[13px] font-medium">Create a routing pool</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Model group name">
                <Input
                  value={groupName}
                  onChange={(e) => setGroupName(e.target.value)}
                  placeholder="cline-pool"
                />
              </Field>
              <Field label="Upstream model ID" hint="The model id Cline exposes, e.g. from the catalog above.">
                <Input
                  value={modelId}
                  onChange={(e) => setModelId(e.target.value)}
                  placeholder="z-ai/glm-5.2"
                />
              </Field>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                className="min-h-11"
                disabled={
                  !groupName.trim() ||
                  !modelId.trim() ||
                  props.connectedNames.length < 2 ||
                  createPool.isPending
                }
                onClick={() => createPool.mutate()}
              >
                {createPool.isPending ? (
                  <Loader2 size={14} aria-hidden className="animate-spin motion-reduce:animate-none" />
                ) : (
                  <Plus size={14} aria-hidden />
                )}
                {createPool.isPending ? "Creating…" : "Create pool"}
              </Button>
              <span className="text-[12px] text-[var(--admin-text-muted)]">
                {props.connectedNames.length < 2
                  ? "Needs at least two connected accounts."
                  : `Adds ${props.connectedNames.length} deployment${props.connectedNames.length !== 1 ? "s" : ""}.`}
              </span>
            </div>
          </div>
        )}

        {/* Routing strategy is GLOBAL router state — PATCH /admin/model-groups/{name}
            ignores the path parameter and writes state.router.settings.routing_strategy
            (wiwi/server/app.py:4672-4680). A switch styled as pool-local here would
            silently re-route every model group in the gateway, so this is read-only
            and points at the page that owns the control. */}
        <div className="flex flex-col gap-2 border-t border-[var(--admin-border)] pt-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="text-[13px] font-medium">Routing strategy</div>
            <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
              {strategy === "simple-shuffle"
                ? "Requests are distributed evenly across deployments."
                : strategy === "least-busy"
                  ? "Requests go to the least busy deployment."
                  : `Strategy: ${strategy}.`}{" "}
              This applies gateway-wide, not just to these groups.
            </p>
          </div>
          <Link
            to="/console/models"
            className="inline-flex min-h-11 shrink-0 items-center gap-1.5 self-start rounded px-1 text-[12px] text-blue-300 hover:underline sm:self-auto"
          >
            <LinkIcon size={13} aria-hidden />
            Change on Models
          </Link>
        </div>
      </div>
    </Card>
  );
}
