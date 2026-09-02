// Combos — compose named model groups out of provider deployments.
//
// A "combo" is just a model group: its name is the client-facing model string,
// and the deployments attached to it (each provider + model_id + weight) are
// what wiwi round-robins across. A group that spans two or more providers
// automatically gets cross-provider weighted round-robin on the backend, and
// each provider's keys round-robin internally. So the page is a composition
// editor: name a combo, tick model_ids from different providers, and the
// round-robin falls out for free.
//
// There is no dedicated "create empty group" endpoint — the group is created
// implicitly by attaching its first deployment (POST
// /admin/model-groups/{name}/deployments uses body.group). "Create combo"
// therefore attaches at least one deployment under the chosen name; a combo
// can't exist empty. The same dialog handles create and edit (add/remove
// deployments on an existing group).
//
// The dialog lists only model ids that are already registered for a provider
// (i.e. deployed in some model group) — read from /admin/models — plus a small
// "add custom model id" input per provider for ids not yet registered. It does
// NOT fetch the provider's full upstream catalog.

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Boxes, Layers, Pencil, Plus, RefreshCw, Search, Unlink, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  addDeployment,
  deleteDeployment,
  getModels,
  getProviders,
  patchModelGroup,
} from "@/api/client";
import type { ModelGroup } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorText,
  Field,
  Input,
  PageHeader,
  Spinner,
  Table,
  TD,
} from "@/components/ui";

// Models.tsx and ProviderDetail.tsx use *different* query keys for the same
// /admin/models data. Every mutation below invalidates both so all three
// pages (Models, ProviderDetail, Combos) stay in sync.
function invalidateModels(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ["models"] });
  void qc.invalidateQueries({ queryKey: ["model-groups"] });
}

interface ProviderOption {
  name: string;
  type: string;
}

interface DeploymentPick {
  provider: string;
  model_id: string;
}

// Trade a provider name + model id for a stable, printable selection key. The
// pair lives in the Map value, so we never need to split the key — it only
// serves as a Set/Map identifier. "provider/model_id" is unambiguous enough
// for this purpose.
const depKey = (d: DeploymentPick) => `${d.provider}/${d.model_id}`;

// -- create/edit dialog --------------------------------------------------------
//
// The dialog is the whole compose UX. It collects a combo name and, grouped by
// provider, a checkbox list of registered model ids (plus any custom ids the
// admin types). "Create" attaches the ticking models under the name; "Edit"
// adds newly ticked ones and detaches unticked ones on an existing combo. The
// backend has no empty-group endpoint, so the name is only meaningful once at
// least one deployment is attached.

function ComboDialog(props: {
  open: boolean;
  editing: ModelGroup | null; // null = create mode
  providerOptions: ProviderOption[];
  registeredByProvider: Record<string, string[]>;
  onClose: () => void;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const isEdit = props.editing !== null;

  const [name, setName] = useState("");
  const [selected, setSelected] = useState<Map<string, DeploymentPick>>(new Map());
  const [localError, setLocalError] = useState<string | null>(null);
  // Case-insensitive filter across every provider's model id.
  const [search, setSearch] = useState("");
  // Per-provider drafts for the "add custom model id" inputs.
  const [customDrafts, setCustomDrafts] = useState<Record<string, string>>({});

  // The model ids shown per provider: the registered ones, plus any custom ids
  // the admin has already ticked for that provider (so they stay visible).
  // Filtered by `search` ("" shows all).
  const visibleByProvider = useMemo(() => {
    const q = search.trim().toLowerCase();
    const match = (m: string) => !q || m.toLowerCase().includes(q);
    const out: Record<string, string[]> = {};
    for (const p of props.providerOptions) {
      const registered = props.registeredByProvider[p.name] ?? [];
      const custom = Array.from(selected.values())
        .filter((s) => s.provider === p.name)
        .map((s) => s.model_id);
      // De-dupe, prefer registered order, then filter by search.
      out[p.name] = Array.from(new Set([...custom, ...registered])).filter(match);
    }
    return out;
  }, [props.providerOptions, props.registeredByProvider, selected, search]);

  // Toggle every visible model in a provider.
  const toggleProvider = (pname: string) => {
    setSelected((s) => {
      const n = new Map(s);
      const visible = visibleByProvider[pname] ?? [];
      const allOn = visible.length > 0 && visible.every((m) => n.has(depKey({ provider: pname, model_id: m })));
      for (const m of visible) {
        const k = depKey({ provider: pname, model_id: m });
        if (allOn) {
          n.delete(k);
        } else {
          n.set(k, { provider: pname, model_id: m });
        }
      }
      return n;
    });
  };

  // Reset state whenever the dialog (re)opens for a given target.
  useEffect(() => {
    if (!props.open) return;
    if (props.editing) {
      setName(props.editing.name);
      setSelected(
        new Map(
          props.editing.deployments.map((d) => [depKey(d), { provider: d.provider, model_id: d.model_id }]),
        ),
      );
    } else {
      setName("");
      setSelected(new Map());
    }
    setSearch("");
    setCustomDrafts({});
    setLocalError(null);
  }, [props.open, props.editing]);

  const toggle = (p: DeploymentPick) =>
    setSelected((s) => {
      const n = new Map(s);
      const k = depKey(p);
      if (n.has(k)) {
        n.delete(k);
      } else {
        n.set(k, p);
      }
      return n;
    });

  // Add a custom model id for a provider straight into the selection (registers
  // it on save via addDeployment) and keep it visible.
  const selectCustom = (pname: string) => {
    const mid = (customDrafts[pname] ?? "").trim();
    if (!mid) return;
    if (/\s/.test(mid)) {
      setLocalError("model id cannot contain spaces");
      return;
    }
    setSelected((s) => {
      const n = new Map(s);
      n.set(depKey({ provider: pname, model_id: mid }), { provider: pname, model_id: mid });
      return n;
    });
    setCustomDrafts((d) => ({ ...d, [pname]: "" }));
    setLocalError(null);
  };

  const save = useMutation({
    mutationFn: async () => {
      const gname = name.trim();
      if (!gname) throw new Error("enter a combo name");
      if (/\s/.test(gname)) throw new Error("combo name cannot contain spaces");
      if (selected.size === 0) throw new Error("tick at least one model");
      const entries = Array.from(selected.values());
      if (isEdit) {
        // Detach the ones that were there but are no longer ticked.
        const unticked = props.editing!.deployments
          .filter((d) => !selected.has(depKey(d)))
          .map((d) => ({ provider: d.provider, model_id: d.model_id }));
        for (const e of unticked) await deleteDeployment(gname, e.provider, e.model_id);
      }
      // (Re)attach every ticked deployment. Idempotent for ones already there.
      for (const e of entries) {
        await addDeployment(gname, { provider: e.provider, model_id: e.model_id, weight: 1 });
      }
    },
    onSuccess: () => {
      invalidateModels(qc);
      setLocalError(null);
      props.onClose();
    },
    onError: (e) => setLocalError(e.message),
  });

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      title={isEdit ? `Edit combo · ${props.editing!.name}` : "Create combo"}
      wide
    >
      <div className="space-y-4">
        <Field label="Combo name" hint="The model string clients send. Names a group; spaces are not allowed.">
          <Input
            value={name}
            placeholder="e.g. shin"
            onChange={(e) => setName(e.target.value)}
            disabled={isEdit}
          />
        </Field>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <div className="text-[11px] uppercase tracking-wide text-[var(--admin-text-dim)]">
              Models
            </div>
            <div className="text-[11px] text-[var(--admin-text-dim)]">
              <span className="text-blue-300">{selected.size}</span> selected
            </div>
          </div>

          {/* search — filters every provider's model id, with a clear button */}
          <div className="relative mb-2">
            <Search
              size={13}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
            />
            <Input
              value={search}
              placeholder="Search model ids… (e.g. gpt, glm, 5.2)"
              aria-label="Search model ids"
              onChange={(e) => setSearch(e.target.value)}
              className="pl-7 pr-8"
            />
            {search && (
              <button
                type="button"
                aria-label="Clear search"
                onClick={() => setSearch("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
              >
                <X size={13} />
              </button>
            )}
          </div>

          <div className="max-h-[48vh] space-y-3 overflow-y-auto rounded-lg border border-white/[0.04] p-3">
            {props.providerOptions.length === 0 && (
              <div className="px-2 py-6 text-center">
                <Boxes size={16} className="mx-auto mb-2 opacity-40" />
                <p className="text-[12px] text-[var(--admin-text-dim)]">
                  No providers configured yet. Add one first, then come back.
                </p>
              </div>
            )}

            {props.providerOptions.map((p) => {
              const visible = visibleByProvider[p.name] ?? [];
              const registered = props.registeredByProvider[p.name] ?? [];
              const selectedHere = visible.filter((m) => selected.has(depKey({ provider: p.name, model_id: m }))).length;
              const allOn = selectedHere > 0 && selectedHere === visible.length;

              return (
                <div key={p.name}>
                  <div className="mb-1.5 flex items-center gap-2">
                    <Boxes size={13} className="shrink-0 text-[var(--admin-text-dim)]" />
                    <span className="truncate text-[12px] font-semibold text-[var(--admin-text)]">
                      {p.name}
                    </span>
                    <span className="shrink-0">
                      <Badge tone="gray">{p.type}</Badge>
                    </span>
                    {visible.length > 0 && (
                      <>
                        <span className="ml-auto shrink-0 text-[10px] text-[var(--admin-text-dim)]">
                          {selectedHere}/{visible.length}
                        </span>
                        <button
                          type="button"
                          aria-label={allOn ? `Deselect all ${p.name} models` : `Select all ${p.name} models`}
                          onClick={() => toggleProvider(p.name)}
                          className="shrink-0 rounded-md border border-white/[0.06] px-1.5 py-0.5 text-[10px] text-[var(--admin-text-dim)] transition-colors hover:border-blue-500/30 hover:text-blue-300"
                        >
                          {allOn ? "clear" : "all"}
                        </button>
                      </>
                    )}
                  </div>

                  {visible.length === 0 ? (
                    <p className="px-1 py-2 text-[11px] text-[var(--admin-text-dim)]">
                      {registered.length === 0
                        ? "No registered models for this provider yet."
                        : search.trim()
                          ? `No match for “${search.trim()}”.`
                          : "No models to show."}
                    </p>
                  ) : (
                    <div className="space-y-0.5">
                      {visible.map((mid) => {
                        const pick = { provider: p.name, model_id: mid };
                        const key = depKey(pick);
                        const on = selected.has(key);
                        return (
                          <label
                            key={key}
                            className={`flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 font-mono text-[12px] transition-colors ${
                              on
                                ? "bg-blue-500/15 text-blue-200"
                                : "text-[var(--admin-text-dim)] hover:bg-white/[0.03]"
                            }`}
                          >
                            <input
                              type="checkbox"
                              className="shrink-0 accent-blue-500"
                              checked={on}
                              onChange={() => toggle(pick)}
                              aria-label={`${p.name} · ${mid}`}
                            />
                            <span className="truncate">{mid}</span>
                            <span className="ml-auto shrink-0 pl-2 text-[10px] text-[var(--admin-text-dim)]">
                              {p.name}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  )}

                  {/* add a not-yet-registered model id for this provider */}
                  <form
                    className="mt-1.5 flex items-center gap-1"
                    onSubmit={(e) => {
                      e.preventDefault();
                      selectCustom(p.name);
                    }}
                  >
                    <Input
                      value={customDrafts[p.name] ?? ""}
                      placeholder="add custom model id"
                      aria-label={`Add custom model id for ${p.name}`}
                      onChange={(e) => setCustomDrafts((d) => ({ ...d, [p.name]: e.target.value }))}
                      className="h-auto py-1 font-mono text-[11px]"
                    />
                    <Button
                      type="submit"
                      variant="outline"
                      disabled={!(customDrafts[p.name] ?? "").trim()}
                      aria-label={`Add custom model id to ${p.name}`}
                      className="h-auto px-2 py-1"
                    >
                      <Plus size={12} />
                    </Button>
                  </form>
                </div>
              );
            })}
          </div>
        </div>

        {(localError || save.error) && (
          <ErrorText>{localError ?? (save.error ? save.error.message : "")}</ErrorText>
        )}

        <div className="flex justify-end gap-2 border-t border-white/[0.06] pt-3">
          <Button variant="ghost" type="button" onClick={props.onClose}>
            Cancel
          </Button>
          <Button disabled={save.isPending || !name.trim() || selected.size === 0} onClick={() => save.mutate()}>
            {save.isPending ? "Applying…" : isEdit ? "Save changes" : "Create combo"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// -- detail pane ---------------------------------------------------------------
//
// Read-only roster of a combo's deployments with weight editing + detach. All
// add/remove is handled by the dialog (its "Edit" button).

function ComboDetail(props: {
  combo: ModelGroup;
  crossProvider: boolean;
  onError: (m: string) => void;
  onEdit: () => void;
}) {
  const qc = useQueryClient();
  const [editingWeight, setEditingWeight] = useState<string | null>(null);
  const [weightVal, setWeightVal] = useState("");

  const remove = useMutation({
    mutationFn: (t: { provider: string; model_id: string }) =>
      deleteDeployment(props.combo.name, t.provider, t.model_id),
    onSuccess: () => invalidateModels(qc),
    onError: (e) => props.onError(e.message),
  });

  const setWeight = useMutation({
    mutationFn: (t: { ident: string; weight: number }) =>
      patchModelGroup(props.combo.name, { weights: { [t.ident]: t.weight } }),
    onSuccess: () => invalidateModels(qc),
    onError: (e) => props.onError(e.message),
  });

  const deployments = props.combo.deployments;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {props.combo.name}
        </h3>
        {props.crossProvider && (
          <Badge tone="blue" title="Deployments span 2+ providers — cross-provider weighted round-robin">
            cross-provider RR
          </Badge>
        )}
        <Badge tone="gray">
          {deployments.length} deployment{deployments.length === 1 ? "" : "s"}
        </Badge>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" onClick={props.onEdit}>
            <Pencil size={13} /> Edit
          </Button>
          <Button variant="outline" onClick={() => void qc.invalidateQueries({ queryKey: ["model-groups"] })}>
            <RefreshCw size={13} /> Refresh
          </Button>
        </div>
      </div>

      {deployments.length === 0 ? (
        <Card className="p-4">
          <EmptyState>No deployments — use Edit to add some.</EmptyState>
        </Card>
      ) : (
        <Card>
          <Table head={["Provider", "Model ID", "Weight", "Ready", ""]}>
            {deployments.map((d) => {
              const ident = `${d.provider}/${d.model_id}`;
              const editing = editingWeight === ident;
              return (
                <tr key={ident}>
                  <TD className="font-medium">
                    <span className="flex items-center gap-2">
                      <Boxes size={13} className="text-[var(--admin-text-dim)]" />
                      {d.provider}
                    </span>
                  </TD>
                  <TD className="font-mono text-[12px]">{d.model_id}</TD>
                  <TD>
                    {editing ? (
                      <form
                        className="flex items-center gap-1"
                        onSubmit={(e) => {
                          e.preventDefault();
                          const w = parseInt(weightVal, 10);
                          if (Number.isFinite(w) && w >= 1) setWeight.mutate({ ident, weight: w });
                          setEditingWeight(null);
                        }}
                      >
                        <Input
                          className="h-auto w-16 text-[12px]"
                          type="number"
                          min={1}
                          value={weightVal}
                          autoFocus
                          onChange={(e) => setWeightVal(e.target.value)}
                          onBlur={() => setEditingWeight(null)}
                        />
                      </form>
                    ) : (
                      <button
                        className="rounded px-1.5 py-0.5 font-mono tabular-nums text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
                        title="Click to edit weight"
                        onClick={() => {
                          setEditingWeight(ident);
                          setWeightVal(String(d.weight));
                        }}
                      >
                        {d.weight}
                      </button>
                    )}
                  </TD>
                  <TD>
                    <Badge tone={d.available ? "green" : "amber"}>
                      {d.available ? "yes" : "cooldown"}
                    </Badge>
                  </TD>
                  <TD>
                    <Button
                      variant="ghost"
                      title={`Detach ${d.model_id} from ${props.combo.name}`}
                      aria-label={`Detach ${d.model_id}`}
                      onClick={() => remove.mutate({ provider: d.provider, model_id: d.model_id })}
                    >
                      <Unlink size={14} />
                    </Button>
                  </TD>
                </tr>
              );
            })}
          </Table>
        </Card>
      )}
    </div>
  );
}

// -- page ---------------------------------------------------------------------

const COMBO_ICON: LucideIcon = Layers;

export function CombosPage() {
  const [error, setError] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  // `editing` is the model group being edited in the dialog, or null for create.
  const [editingGroup, setEditingGroup] = useState<ModelGroup | null>(null);

  const modelsQ = useQuery({ queryKey: ["model-groups"], queryFn: getModels });
  const providersQ = useQuery({ queryKey: ["providers"], queryFn: getProviders });

  const groups = useMemo(() => modelsQ.data?.groups ?? [], [modelsQ.data]);
  const providerOptions = useMemo<ProviderOption[]>(
    () => (providersQ.data?.providers ?? []).map((p) => ({ name: p.name, type: p.provider_type })),
    [providersQ.data],
  );

  // "Registered" model ids per provider = every model_id deployed in ANY model
  // group for that provider. This is the only catalog the dialog shows.
  const registeredByProvider = useMemo(() => {
    const m: Record<string, string[]> = {};
    for (const g of groups) {
      for (const d of g.deployments) {
        (m[d.provider] ??= []).push(d.model_id);
      }
    }
    for (const k of Object.keys(m)) m[k] = Array.from(new Set(m[k])).sort();
    return m;
  }, [groups]);

  // Dialog modes: explicit create, or edit the currently selected combo.
  const openDialog = (mode: "create" | "edit") => {
    setEditingGroup(mode === "edit" ? (groups.find((g) => g.name === selectedName) ?? null) : null);
    setDialogOpen(true);
  };

  const existing = groups.find((g) => g.name === selectedName);
  const crossProvider =
    existing !== undefined &&
    new Set(existing.deployments.map((d) => d.provider)).size >= 2;

  if (modelsQ.isLoading) return <Spinner />;
  if (modelsQ.error) return <ErrorText>{modelsQ.error.message}</ErrorText>;

  return (
    <div>
      <PageHeader
        title="Combos"
        subtitle="Compose named model groups from deployments across providers. A name with 2+ providers round-robins across them."
        right={
          <Button onClick={() => openDialog("create")}>
            <Plus size={14} /> New combo
          </Button>
        }
      />
      {error && <div className="mb-3"><ErrorText>{error}</ErrorText></div>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px,1fr]">
        {/* left: combo list */}
        <Card className="p-3">
          <div className="mb-2 flex items-center gap-2 px-1">
            <COMBO_ICON size={15} className="text-[var(--admin-text-dim)]" />
            <span className="text-[12px] font-semibold text-[var(--admin-text)]">Combos</span>
          </div>
          {groups.length === 0 ? (
            <EmptyState>No combos yet.</EmptyState>
          ) : (
            <div className="space-y-0.5">
              {groups.map((g) => {
                const active = g.name === selectedName;
                const providers = new Set(g.deployments.map((d) => d.provider)).size;
                return (
                  <button
                    key={g.name}
                    type="button"
                    onClick={() => setSelectedName(g.name)}
                    className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors ${
                      active
                        ? "bg-blue-500/[0.08] text-blue-200"
                        : "text-[var(--admin-text-muted)] hover:bg-white/[0.02] hover:text-[var(--admin-text)]"
                    }`}
                  >
                    <span className="truncate font-medium">{g.name}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      {providers >= 2 && (
                        <Badge tone="blue" title="Cross-provider round-robin">
                          RR
                        </Badge>
                      )}
                      <span className="font-mono text-[10px] text-[var(--admin-text-dim)]">
                        {g.deployments.length}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </Card>

        {/* right: detail */}
        <Card className="p-4">
          {existing ? (
            <ComboDetail
              combo={existing}
              crossProvider={crossProvider}
              onError={setError}
              onEdit={() => openDialog("edit")}
            />
          ) : (
            <EmptyState>
              <div className="space-y-2 text-center">
                <p>Pick a combo on the left, or create a new one.</p>
                <Button variant="outline" onClick={() => openDialog("create")}>
                  <Plus size={14} /> New combo
                </Button>
              </div>
            </EmptyState>
          )}
        </Card>
      </div>

      <div className="mt-4">
        <Link
          to="/console/models"
          className="text-[12px] text-[var(--admin-text-dim)] hover:text-[var(--admin-text)]"
        >
          See all model groups and their health →
        </Link>
      </div>

      <ComboDialog
        open={dialogOpen}
        editing={editingGroup}
        providerOptions={providerOptions}
        registeredByProvider={registeredByProvider}
        onClose={() => setDialogOpen(false)}
        onError={setError}
      />
    </div>
  );
}
