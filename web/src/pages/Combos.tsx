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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Activity,
  Boxes,
  ChevronDown,
  Layers,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Shuffle,
  Unlink,
  X,
} from "lucide-react";
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
  CopyButton,
  Dialog,
  EmptyState,
  ErrorText,
  Field,
  Input,
  PageHeader,
  ProgressBar,
  Spinner,
  StatCard,
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

// The provider rail is a vertical column from `sm` up and a horizontal chip
// strip below it. This drives both the layout and the arrow-key axis, so the two
// can never disagree. Matches Tailwind's `sm` breakpoint (640px).
const WIDE_QUERY = "(min-width: 640px)";

function useWideLayout(): boolean {
  const [wide, setWide] = useState(() =>
    typeof window !== "undefined" ? window.matchMedia(WIDE_QUERY).matches : true,
  );
  useEffect(() => {
    const mq = window.matchMedia(WIDE_QUERY);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

// -- create/edit dialog --------------------------------------------------------
//
// The dialog is the whole compose UX. It collects a combo name and a set of
// (provider, model_id) pairs — the deployments wiwi round-robins across. "Create"
// attaches everything ticked under the chosen name; "Edit" adds newly ticked ones
// and detaches the ones that are no longer ticked. The backend has no empty-group
// endpoint, so the name only becomes meaningful once at least one deployment is
// attached.
//
// Layout: a two-pane master/detail. The left rail is every provider with a
// selected/total count; the right pane is the active provider's model ids as
// generous (44px) rows. This replaces the old single dense column of per-provider
// checkbox groups, which buried a phone-sized tap target inside a scroll-inside-
// a-scroll. Below `sm` the rail becomes a horizontal chip strip. The model list is
// the only *nested* scroll — the rail scrolls as a sibling — so no region ever
// scrolls inside another.
//
// The list shows only model ids already registered for a provider (i.e. deployed
// in some model group) — read from /admin/models — plus a single "add custom model
// id" input for the *active* provider, for ids not yet registered. It does NOT
// fetch the provider's full upstream catalog.

function ComboDialog(props: {
  open: boolean;
  editing: ModelGroup | null; // null = create mode
  providerOptions: ProviderOption[];
  registeredByProvider: Record<string, string[]>;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const isEdit = props.editing !== null;

  const [name, setName] = useState("");
  const [selected, setSelected] = useState<Map<string, DeploymentPick>>(new Map());
  const [localError, setLocalError] = useState<string | null>(null);
  // Case-insensitive filter over the model ids.
  const [search, setSearch] = useState("");
  // Draft for the "add custom model id" input. Single, because the input now
  // belongs to the active provider only.
  const [customDraft, setCustomDraft] = useState("");
  // Which provider's models the right pane is showing.
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  // The selection tray is collapsed by default; the footer already reports the
  // count, so the full chip list is opt-in.
  const [trayOpen, setTrayOpen] = useState(false);

  // A provider list that is a vertical rail on wide screens becomes a horizontal
  // strip on a phone, so the arrow-key axis and ARIA orientation follow the layout.
  const wide = useWideLayout();

  // Every model id available for a provider: the registered ones, plus any custom
  // ids already picked for it (so they stay visible/tickable after being added).
  const modelsFor = useCallback(
    (pname: string): string[] => {
      const registered = props.registeredByProvider[pname] ?? [];
      const custom = Array.from(selected.values())
        .filter((s) => s.provider === pname)
        .map((s) => s.model_id);
      return Array.from(new Set([...custom, ...registered]));
    },
    [props.registeredByProvider, selected],
  );

  // Fall back to the first provider so the right pane is never empty on open.
  // A stale activeProvider (its provider was deleted in another tab while this
  // dialog was open) would leave every rail item tabIndex=-1, making the
  // radiogroup unreachable by Tab, so re-derive it against the live list.
  const active = activeProvider && props.providerOptions.some((p) => p.name === activeProvider)
    ? activeProvider
    : props.providerOptions[0]?.name ?? "";
  const searching = search.trim().length > 0;

  // Browsing shows one provider at a time; searching reaches across all of them,
  // so a model id can be found wherever it is registered. The rail stays put and
  // reports match counts during a search.
  const groups = useMemo(() => {
    const q = search.trim().toLowerCase();
    const match = (m: string) => !q || m.toLowerCase().includes(q);
    const built = props.providerOptions.map((p) => ({
      provider: p,
      ids: modelsFor(p.name).filter(match),
    }));
    return searching ? built.filter((g) => g.ids.length > 0) : built.filter((g) => g.provider.name === active);
  }, [props.providerOptions, modelsFor, search, searching, active]);

  // Rail counters: how many are picked, how many exist, how many match a search.
  const railInfo = useMemo(() => {
    const q = search.trim().toLowerCase();
    return props.providerOptions.map((p) => {
      const all = modelsFor(p.name);
      return {
        name: p.name,
        type: p.type,
        total: all.length,
        chosen: all.filter((m) => selected.has(depKey({ provider: p.name, model_id: m }))).length,
        matches: q ? all.filter((m) => m.toLowerCase().includes(q)).length : all.length,
      };
    });
  }, [props.providerOptions, modelsFor, selected, search]);

  // The save diff, shown in the footer so the effect of Save is legible before
  // committing: which models are new, and which attached ones are about to go.
  const originalKeys = useMemo(
    () => new Set((props.editing?.deployments ?? []).map(depKey)),
    [props.editing],
  );
  const added = useMemo(
    () => Array.from(selected.values()).filter((s) => !originalKeys.has(depKey(s))).length,
    [selected, originalKeys],
  );
  const removed = useMemo(
    () => (props.editing?.deployments ?? []).filter((d) => !selected.has(depKey(d))).length,
    [selected, props.editing],
  );
  const providersSpanned = useMemo(
    () => new Set(Array.from(selected.values()).map((s) => s.provider)).size,
    [selected],
  );

  // Reset state whenever the dialog (re)opens for a given target. Land on the
  // first provider that already has a pick, so Edit opens on live content.
  //
  // `providerOptions` is read through a ref rather than listed as a dependency:
  // it is a useMemo over the providers query, so ANY refetch returning changed
  // data hands it a new identity. As a direct dependency it would re-run this
  // effect and wipe half-typed names and ticked models on a background refetch
  // (refetchOnWindowFocus is on by default) — which is exactly what the page
  // comment above promises cannot happen. The ref keeps the effect dependent on
  // the dialog actually opening, and on the edit target, and nothing else.
  const providerOptionsRef = useRef(props.providerOptions);
  providerOptionsRef.current = props.providerOptions;

  useEffect(() => {
    if (!props.open) return;
    if (props.editing) {
      setName(props.editing.name);
      setSelected(
        new Map(
          props.editing.deployments.map((d) => [depKey(d), { provider: d.provider, model_id: d.model_id }]),
        ),
      );
      setActiveProvider(
        providerOptionsRef.current.find((p) =>
          props.editing!.deployments.some((d) => d.provider === p.name),
        )?.name ?? null,
      );
    } else {
      setName("");
      setSelected(new Map());
      setActiveProvider(null);
    }
    setSearch("");
    setCustomDraft("");
    setTrayOpen(false);
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

  // Tick/untick every model id in one provider's currently visible list.
  const setAll = (pname: string, ids: string[]) =>
    setSelected((s) => {
      const n = new Map(s);
      const allOn = ids.length > 0 && ids.every((m) => n.has(depKey({ provider: pname, model_id: m })));
      for (const m of ids) {
        const k = depKey({ provider: pname, model_id: m });
        if (allOn) {
          n.delete(k);
        } else {
          n.set(k, { provider: pname, model_id: m });
        }
      }
      return n;
    });

  // Add a custom model id for the active provider straight into the selection
  // (registered on save via addDeployment) and keep it visible.
  const selectCustom = () => {
    const mid = customDraft.trim();
    if (!mid || !active) return;
    if (/\s/.test(mid)) {
      setLocalError("model id cannot contain spaces");
      return;
    }
    setSelected((s) => {
      const n = new Map(s);
      n.set(depKey({ provider: active, model_id: mid }), { provider: active, model_id: mid });
      return n;
    });
    setCustomDraft("");
    setLocalError(null);
  };

  // Arrow-key roving focus across the provider rail. The active provider is the
  // only rail item in the tab order; arrows move selection, Tab moves out.
  const railRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const onRailKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const n = railInfo.length;
    if (n === 0) return;
    const i = railInfo.findIndex((r) => r.name === active);
    const next =
      e.key === (wide ? "ArrowDown" : "ArrowRight")
        ? (i + 1) % n
        : e.key === (wide ? "ArrowUp" : "ArrowLeft")
          ? (i - 1 + n) % n
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? n - 1
              : -1;
    if (next < 0) return;
    e.preventDefault();
    setActiveProvider(railInfo[next].name);
    railRefs.current[next]?.focus();
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
      // Attach only the ticked deployments that are not already on the combo.
      // POST is not idempotent — an already-attached pair answers 409, which
      // aborted the loop after the detaches above had already run.
      const attached = new Set(props.editing?.deployments.map(depKey) ?? []);
      for (const e of entries) {
        if (attached.has(depKey(e))) continue;
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

  const activeIds = groups.find((g) => g.provider.name === active)?.ids ?? [];
  const error = localError ?? (save.error ? save.error.message : "");

  // While a search is active the list spans every provider, so the pane header
  // and its "select all" must describe the whole result set — not the provider
  // that merely happens to be active in the rail. Otherwise the header reads
  // "22" above a list of 14 models from 14 different providers.
  //
  // Keys are the full (provider, model_id) pair, never a bare model_id: the
  // same model_id is registered under several providers in this data
  // ("glm-5.3-flash" under civ/cov/pai), so a bare-id `every()` reports
  // "all selected" the moment ANY one of those providers is ticked.
  const resultIds = searching ? groups.flatMap((g) => g.ids) : activeIds;
  const resultKeys = searching
    ? groups.flatMap((g) => g.ids.map((m) => depKey({ provider: g.provider.name, model_id: m })))
    : activeIds.map((m) => depKey({ provider: active, model_id: m }));
  const allOn = resultKeys.length > 0 && resultKeys.every((k) => selected.has(k));
  const setAllVisible = () => {
    if (!searching) return setAll(active, activeIds);
    setSelected((s) => {
      const n = new Map(s);
      for (const g of groups) {
        for (const m of g.ids) {
          const k = depKey({ provider: g.provider.name, model_id: m });
          if (allOn) n.delete(k);
          else n.set(k, { provider: g.provider.name, model_id: m });
        }
      }
      return n;
    });
  };

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      title={isEdit ? `Edit combo · ${props.editing!.name}` : "Create combo"}
      wide
      size="5xl"
      contained
    >
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
        <Field
          label="Combo name"
          hint={
            isEdit
              ? "The model string clients send. Fixed while the group exists — detach the last model and create a new combo to rename it."
              : "The model string clients send. Names a group; spaces are not allowed."
          }
        >
          <Input
            value={name}
            placeholder="e.g. shin"
            onChange={(e) => setName(e.target.value)}
            disabled={isEdit}
          />
        </Field>

        {/* search — filters model ids; with a clear button. On a phone it has
            room to breathe, on desktop it spans the detail pane. */}
        <div className="relative shrink-0">
          <Search
            size={14}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
          />
          <Input
            value={search}
            placeholder="Search model ids… (e.g. gpt, glm, 5.2)"
            aria-label="Search model ids"
            onChange={(e) => setSearch(e.target.value)}
            className="min-h-11 pl-9 pr-9"
          />
          {search && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* two panes: provider rail (master) + model list (detail). On a phone
            the rail is a horizontal chip strip above the list. `min-w-0` is
            load-bearing: without it these flex items keep the default
            min-width:auto and the pane grows to its content, pushing the whole
            dialog into horizontal overflow on a narrow screen. */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 sm:flex-row">
          {/*
            The rail is a mutually-exclusive picker ("show me this provider"),
            so it is a radiogroup, not a tablist. A tablist obliges every tab to
            reference a tabpanel via aria-controls; there isn't one, because
            during a search the pane shows ALL providers rather than one tab's
            content, so a tablist would announce tabs controlling nothing.
            radiogroup carries the same single-select + roving-tabindex meaning
            with no dangling panel reference.
          */}
          <div
            role="radiogroup"
            aria-label="Provider"
            onKeyDown={onRailKeyDown}
            className="admin-scroll flex w-full shrink-0 gap-1.5 overflow-x-auto overscroll-x-contain sm:w-48 sm:flex-col sm:gap-0.5 sm:overflow-y-auto sm:overflow-x-visible sm:pr-1"
          >
            {railInfo.map((r, i) => {
              const on = r.name === active;
              return (
                <button
                  key={r.name}
                  ref={(el) => {
                    railRefs.current[i] = el;
                  }}
                  role="radio"
                  aria-checked={on}
                  tabIndex={on ? 0 : -1}
                  onClick={() => setActiveProvider(r.name)}
                  className={`flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded-lg px-2.5 text-left transition-colors sm:w-full ${
                    on
                      ? "bg-blue-500/10 text-blue-200 shadow-[inset_2px_0_0_0_rgba(59,130,246,0.55)]"
                      : "text-[var(--admin-text-muted)] hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
                  }`}
                >
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-[12px] font-medium">{r.name}</span>
                    <span className="truncate font-mono text-[10px] text-[var(--admin-text-dim)]">
                      {r.type}
                    </span>
                  </span>
                  <span
                    className={`shrink-0 font-mono text-[10px] tabular-nums ${
                      r.chosen > 0 ? "text-blue-300" : "text-[var(--admin-text-dim)]"
                    }`}
                    title={`${r.chosen} of ${r.total} selected`}
                  >
                    {r.chosen}/{r.total}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="flex min-h-0 min-w-0 flex-1 flex-col rounded-xl border border-white/[0.05] bg-white/[0.01]">
            <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-white/[0.05] px-3 py-2.5">
              <Boxes size={14} className="shrink-0 text-[var(--admin-text-dim)]" />
              <span className="truncate text-[13px] font-semibold text-[var(--admin-text)]">
                {searching ? "All providers" : active}
              </span>
              <span className="font-mono text-[10px] tabular-nums text-[var(--admin-text-dim)]">
                {searching
                  ? `${resultIds.length} match${resultIds.length === 1 ? "" : "es"}`
                  : `${resultIds.length} model${resultIds.length === 1 ? "" : "s"}`}
              </span>
              {allOn && (
                <Badge tone="blue" title="Every visible model is selected">
                  all selected
                </Badge>
              )}
              {resultIds.length > 0 && (
                <button
                  type="button"
                  aria-label={
                    allOn
                      ? `Deselect all ${searching ? "matching" : active} models`
                      : `Select all ${searching ? "matching" : active} models`
                  }
                  onClick={setAllVisible}
                  className="ml-auto min-h-11 shrink-0 rounded-lg border border-white/[0.08] px-3 text-[11px] text-[var(--admin-text-dim)] transition-colors hover:border-blue-500/30 hover:text-blue-300"
                >
                  {allOn ? "clear" : "select all"}
                </button>
              )}
            </div>

            {/* the single scroll region */}
            <div className="admin-scroll min-h-0 flex-1 overflow-y-auto p-2">
              {groups.length === 0 ? (
                <div className="px-2 py-8 text-center">
                  <Boxes size={20} className="mx-auto mb-2 opacity-40" />
                  <p className="text-[12px] text-[var(--admin-text-dim)]">
                    {props.providerOptions.length === 0
                      ? "No providers configured yet. Add one first, then come back."
                      : searching
                        ? `No model id matches “${search.trim()}”.`
                        : "No models to show."}
                  </p>
                </div>
              ) : (
                groups.map((g) => (
                  <div key={g.provider.name} className="mb-1 last:mb-0">
                    {searching && (
                      <div className="flex items-center gap-2 px-2 pb-1 pt-2">
                        <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--admin-text-dim)]">
                          {g.provider.name}
                        </span>
                        <span className="font-mono text-[10px] text-[var(--admin-text-dim)]">
                          {g.ids.length}
                        </span>
                      </div>
                    )}
                    <div className="space-y-0.5">
                      {g.ids.map((mid) => {
                        const pick = { provider: g.provider.name, model_id: mid };
                        const on = selected.has(depKey(pick));
                        return (
                          <label
                            key={depKey(pick)}
                            className={`flex min-h-11 w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 font-mono text-[12px] transition-colors ${
                              on
                                ? "bg-blue-500/10 text-blue-200"
                                : "text-[var(--admin-text-muted)] hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
                            }`}
                          >
                            <input
                              type="checkbox"
                              className="h-3.5 w-3.5 shrink-0 accent-blue-500"
                              checked={on}
                              onChange={() => toggle(pick)}
                              aria-label={`${g.provider.name} · ${mid}`}
                            />
                            <span className="truncate">{mid}</span>
                            <span className="ml-auto shrink-0 text-[10px] text-[var(--admin-text-dim)]">
                              {g.provider.name}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* add a not-yet-registered model id for the active provider. It stays
                scoped to that provider even during a cross-provider search,
                where the pane header above says "All providers". Hidden entirely
                when there is no provider, since selectCustom early-returns on an
                empty `active` and the field would be a visible no-op. */}
            {active && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  selectCustom();
                }}
                className="flex shrink-0 items-center gap-2 border-t border-white/[0.05] p-2"
              >
                <Input
                  value={customDraft}
                  placeholder={`add custom model id to ${active}`}
                  aria-label={`Add custom model id for ${active}`}
                  onChange={(e) => setCustomDraft(e.target.value)}
                  className="min-h-11 font-mono text-[12px]"
                />
                <Button
                  type="submit"
                  variant="outline"
                  disabled={!customDraft.trim()}
                  aria-label={`Add custom model id to ${active}`}
                  className="min-h-11 shrink-0"
                >
                  <Plus size={14} /> Add
                </Button>
              </form>
            )}
          </div>
        </div>

        {/* selection tray — collapsed chips, every ticked pick removable, so the
            full selection stays inspectable even when scrolled out of the list */}
        {selected.size > 0 && (
          <div className="shrink-0 rounded-lg border border-white/[0.04] bg-white/[0.015]">
            <button
              type="button"
              onClick={() => setTrayOpen((v) => !v)}
              aria-expanded={trayOpen}
              className="flex w-full items-center justify-between px-2.5 py-1.5"
            >
              <span className="admin-label">
                Selection · <span className="text-blue-300">{selected.size}</span>
              </span>
              <ChevronDown
                size={13}
                className={`text-[var(--admin-text-dim)] transition-transform ${trayOpen ? "rotate-180" : ""}`}
              />
            </button>
            {trayOpen && (
              /* Capped: the tray is shrink-0 inside a max-h-[88vh] panel whose
                 body does not scroll, so an unbounded chip list would absorb all
                 the space, collapse the model list, and push the action bar past
                 the clipped bottom. */
              <div className="admin-scroll flex max-h-40 flex-wrap gap-1 overflow-y-auto px-2.5 pb-2">
                {Array.from(selected.values()).map((s) => (
                  <span
                    key={depKey(s)}
                    className="inline-flex max-w-full min-w-0 items-center gap-1 rounded-md border border-blue-500/20 bg-blue-500/10 py-0.5 pl-2 pr-1 font-mono text-[11px] text-blue-200"
                  >
                    {/* the cap is a max-width on a shrinkable span, so a long
                        provider/model pair ellipsises instead of forcing the
                        chip — and the dialog — wider than a phone viewport */}
                    <span className="min-w-0 max-w-[46vw] truncate sm:max-w-[220px]">
                      {s.provider}/{s.model_id}
                    </span>
                    <button
                      type="button"
                      aria-label={`Remove ${s.model_id} from ${s.provider}`}
                      onClick={() => toggle(s)}
                      className="rounded p-1 text-blue-300/70 transition-colors hover:bg-blue-500/20 hover:text-blue-100"
                    >
                      <X size={11} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* action bar */}
        <div className="flex shrink-0 flex-col gap-2 border-t border-white/[0.06] pt-3 sm:flex-row sm:items-center">
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-[var(--admin-text-dim)]">
            <span>
              <span className="font-medium text-[var(--admin-text)]">{selected.size}</span> model
              {selected.size === 1 ? "" : "s"}
            </span>
            <span className="text-[var(--admin-border)]">·</span>
            <span>
              <span className="font-medium text-[var(--admin-text)]">{providersSpanned}</span> provider
              {providersSpanned === 1 ? "" : "s"}
            </span>
            {providersSpanned >= 2 && (
              <Badge tone="blue" title="2+ providers — wiwi will weighted round-robin across them">
                cross-provider RR
              </Badge>
            )}
            {isEdit && (added > 0 || removed > 0) && (
              <span className="flex items-center gap-1.5">
                {added > 0 && <Badge tone="green">+{added}</Badge>}
                {removed > 0 && <Badge tone="red">−{removed}</Badge>}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 sm:ml-auto">
            <Button variant="ghost" type="button" onClick={props.onClose} className="min-h-11">
              Cancel
            </Button>
            <Button
              disabled={save.isPending || !name.trim() || selected.size === 0}
              onClick={() => save.mutate()}
              className="min-h-11 flex-1 sm:flex-none"
            >
              {save.isPending ? "Applying…" : isEdit ? "Save changes" : "Create combo"}
            </Button>
          </div>
        </div>

        {error && (
          <p role="alert" className="shrink-0 text-[12px] text-red-400">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}

// -- detail pane ---------------------------------------------------------------
//
// Read-only roster of a combo's deployments with weight editing + detach. All
// add/remove is handled by the dialog (its "Edit" button). Health data
// (available / inflight / p95) comes from the polled /admin/models query.

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
  const readyCount = deployments.filter((d) => d.available).length;
  const totalWeight = deployments.reduce((a, d) => a + d.weight, 0);

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
          <CopyButton text={props.combo.name} />
          <Button variant="outline" onClick={props.onEdit}>
            <Pencil size={13} /> Edit
          </Button>
          <Button variant="outline" onClick={() => void qc.invalidateQueries({ queryKey: ["model-groups"] })}>
            <RefreshCw size={13} /> Refresh
          </Button>
        </div>
      </div>

      {/* client-facing model string + live readiness */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div
          className="flex items-center gap-2 rounded-lg border border-white/[0.05] bg-white/[0.015] px-2.5 py-1.5"
          title="The model string clients send to route onto this combo"
        >
          <span className="admin-label">model</span>
          <code className="font-mono text-[12px] text-blue-200">{props.combo.name}</code>
        </div>
        {deployments.length > 0 && (
          <div
            className="ml-auto flex items-center gap-2"
            title="Deployments currently out of cooldown"
          >
            <div className="w-24">
              <ProgressBar
                value={readyCount / deployments.length}
                // Readiness semantics, inverted vs the default quota coloring:
                // all ready = green, partial = amber, none = red.
                tone={
                  readyCount === deployments.length
                    ? "bg-emerald-400"
                    : readyCount > 0
                      ? "bg-amber-400"
                      : "bg-red-400"
                }
              />
            </div>
            <span className="font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
              {readyCount}/{deployments.length} ready
            </span>
          </div>
        )}
      </div>

      {deployments.length === 0 ? (
        <Card className="p-4">
          <EmptyState>No deployments — use Edit to add some.</EmptyState>
        </Card>
      ) : (
        <Card>
          <Table head={["Provider", "Model ID", "Weight", "Inflight", "p95", "Ready", ""]}>
            {deployments.map((d) => {
              const ident = `${d.provider}/${d.model_id}`;
              const editing = editingWeight === ident;
              return (
                <tr key={ident}>
                  <TD className="font-medium">
                    <Link
                      to={`/console/providers/${encodeURIComponent(d.provider)}`}
                      className="flex items-center gap-2 transition-colors hover:text-blue-300"
                      title={`Open ${d.provider}`}
                    >
                      <Boxes size={13} className="text-[var(--admin-text-dim)]" />
                      {d.provider}
                    </Link>
                  </TD>
                  <TD className="font-mono text-[12px]">{d.model_id}</TD>
                  <TD>
                    <div className="flex items-center gap-2">
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
                      {deployments.length > 1 && totalWeight > 0 && !editing && (
                        <div className="w-14" title={`${d.weight} of ${totalWeight} total weight`}>
                          <ProgressBar value={d.weight / totalWeight} tone="bg-blue-400/60" />
                        </div>
                      )}
                    </div>
                  </TD>
                  <TD className="font-mono text-[12px] tabular-nums text-[var(--admin-text-dim)]">
                    {d.inflight}
                  </TD>
                  <TD className="font-mono text-[12px] tabular-nums text-[var(--admin-text-dim)]">
                    {d.available ? `${Math.round(d.p95_latency_ms)}ms` : "—"}
                  </TD>
                  <TD>
                    <Badge
                      tone={d.available ? "green" : "amber"}
                      title={
                        d.available
                          ? `inflight ${d.inflight}`
                          : d.cooldown_remaining_s > 0
                            ? `cooling ${Math.ceil(d.cooldown_remaining_s)}s remaining`
                            : "unavailable"
                      }
                    >
                      {d.available ? "ready" : "cooldown"}
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

  // Polled so health (available / inflight / p95) stays live, matching the
  // Models page. The dialog edits a snapshot captured at open time, so
  // background refetches never clobber in-dialog selections.
  const modelsQ = useQuery({ queryKey: ["model-groups"], queryFn: getModels, refetchInterval: 10_000 });
  const providersQ = useQuery({ queryKey: ["providers"], queryFn: getProviders });

  const groups = useMemo(() => modelsQ.data?.groups ?? [], [modelsQ.data]);
  // Alphabetical roster for the list rail; detail lookups stay by name.
  const sortedGroups = useMemo(() => [...groups].sort((a, b) => a.name.localeCompare(b.name)), [groups]);
  const providerOptions = useMemo<ProviderOption[]>(
    () => (providersQ.data?.providers ?? []).map((p) => ({ name: p.name, type: p.provider_type })),
    [providersQ.data],
  );

  // Summary tiles: composition + fleet health across every combo.
  const stats = useMemo(() => {
    const totalDeps = groups.reduce((a, g) => a + g.deployments.length, 0);
    const readyDeps = groups.reduce((a, g) => a + g.deployments.filter((d) => d.available).length, 0);
    const providersSpanned = new Set(groups.flatMap((g) => g.deployments.map((d) => d.provider))).size;
    const crossCount = groups.filter((g) => new Set(g.deployments.map((d) => d.provider)).size >= 2).length;
    return { totalDeps, readyDeps, providersSpanned, crossCount };
  }, [groups]);

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

      {/* summary tiles */}
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard icon={Layers} label="combos" value={String(groups.length)} sub="named model groups" />
        <StatCard
          icon={Boxes}
          label="deployments"
          value={String(stats.totalDeps)}
          sub={`${stats.providersSpanned} providers spanned`}
        />
        <StatCard
          icon={Activity}
          label="ready"
          tone="success"
          value={stats.totalDeps > 0 ? `${stats.readyDeps}/${stats.totalDeps}` : "—"}
          sub={stats.totalDeps > 0 ? `${Math.round((stats.readyDeps / stats.totalDeps) * 100)}% out of cooldown` : undefined}
        />
        <StatCard
          icon={Shuffle}
          label="cross-provider"
          tone="brand"
          value={String(stats.crossCount)}
          sub="weighted RR active"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px,1fr]">
        {/* left: combo list */}
        <Card className="p-3">
          <div className="mb-2 flex items-center gap-2 px-1">
            <COMBO_ICON size={15} className="text-[var(--admin-text-dim)]" />
            <span className="text-[12px] font-semibold text-[var(--admin-text)]">Combos</span>
            <span className="ml-auto font-mono text-[10px] text-[var(--admin-text-dim)]">
              {groups.length}
            </span>
          </div>
          {groups.length === 0 ? (
            <EmptyState>
              <div className="space-y-1 text-center">
                <p>No combos yet.</p>
                <p className="text-[11px]">
                  Create one to start round-robining across providers.
                </p>
              </div>
            </EmptyState>
          ) : (
            <div className="space-y-0.5">
              {sortedGroups.map((g) => {
                const active = g.name === selectedName;
                const providers = new Set(g.deployments.map((d) => d.provider)).size;
                const allReady = g.deployments.length > 0 && g.deployments.every((d) => d.available);
                const someReady = g.deployments.some((d) => d.available);
                return (
                  <button
                    key={g.name}
                    type="button"
                    onClick={() => setSelectedName(g.name)}
                    title={allReady ? "All deployments ready" : someReady ? "Some deployments cooling" : "No deployments"}
                    className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors ${
                      active
                        ? "bg-blue-500/[0.08] text-blue-200 shadow-[inset_2px_0_0_0_rgba(59,130,246,0.55)]"
                        : "text-[var(--admin-text-muted)] hover:bg-white/[0.02] hover:text-[var(--admin-text)]"
                    }`}
                  >
                    <span
                      aria-hidden
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        allReady ? "bg-emerald-400" : someReady ? "bg-amber-400" : "bg-zinc-600"
                      }`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{g.name}</span>
                      <span className="mt-0.5 block font-mono text-[10px] text-[var(--admin-text-dim)]">
                        {g.deployments.length} deploys · {providers} provider{providers === 1 ? "" : "s"}
                      </span>
                    </span>
                    {providers >= 2 && (
                      <span className="shrink-0">
                        <Badge tone="blue" title="Cross-provider round-robin">
                          RR
                        </Badge>
                      </span>
                    )}
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
              <div className="space-y-3 text-center">
                <Layers size={20} className="mx-auto opacity-40" />
                <div className="space-y-1">
                  <p>Pick a combo on the left, or create a new one.</p>
                  <p className="text-[11px]">
                    A combo name is the model string your clients send.
                  </p>
                </div>
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
          className="text-[12px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
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
      />
    </div>
  );
}
