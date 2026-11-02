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
// Layout is a two-pane workspace: the left rail is the roster of combos, the
// right pane is the selected combo's routing composition. The page is a
// composition surface, so the detail leads with the client-facing model string
// and the live routing split, then lists each deployment as one editable row.
//
// Health is reported with the router's own semantics (see
// `lib/deployment-health.ts`): an available deployment on probation is held
// back while a healthy sibling exists, and an unavailable one is either cooling
// or down — never a generic "not ready".
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
  AlertTriangle,
  Boxes,
  ChevronDown,
  Layers,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Unlink,
  X,
} from "lucide-react";
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
  Figure,
  Input,
  PageHeader,
  ProgressBar,
  Spinner,
} from "@/components/ui";
import {
  comboReadiness,
  deploymentState,
  DEPLOYMENT_STATE_COLOR,
  DEPLOYMENT_STATE_TITLE,
  DEPLOYMENT_STATE_WORD,
  duplicateDeploymentIdents,
  providerWeightSplit,
  routableDeployments,
} from "@/lib/deployment-health";
import { fmtInt } from "@/lib/format";
import { providerHue } from "@/lib/provider-hue";

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
// generous (44px) rows. Below `sm` the rail becomes a horizontal chip strip. The
// model list is the only *nested* scroll — the rail scrolls as a sibling — so no
// region ever scrolls inside another.

function ComboDialog(props: {
  open: boolean;
  editing: ModelGroup | null; // null = create mode
  providerOptions: ProviderOption[];
  registeredByProvider: Record<string, string[]>;
  // Every live combo name. Create mode must reject a taken name: POST would
  // otherwise *merge* the ticked models into the existing combo and report
  // success, which silently edits a routing group the operator did not open.
  existingNames: string[];
  onClose: () => void;
  onSaved: (name: string) => void;
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
  const active =
    activeProvider && props.providerOptions.some((p) => p.name === activeProvider)
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
    return searching
      ? built.filter((g) => g.ids.length > 0)
      : built.filter((g) => g.provider.name === active);
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
          props.editing.deployments.map((d) => [
            depKey(d),
            { provider: d.provider, model_id: d.model_id },
          ]),
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
      const allOn =
        ids.length > 0 && ids.every((m) => n.has(depKey({ provider: pname, model_id: m })));
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

  // Bring the active provider into view when the dialog opens (or the active
  // provider changes). Without this, Edit opens on the first provider in the
  // rail's order while the detail pane shows a provider scrolled far below —
  // the two panes disagree about what is selected. `block/inline: nearest`
  // makes this a no-op when the item is already visible.
  useEffect(() => {
    if (!props.open) return;
    const i = props.providerOptions.findIndex((p) => p.name === active);
    if (i >= 0) {
      railRefs.current[i]?.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }, [props.open, active, props.providerOptions]);

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

  const gname = name.trim();
  const noopEdit = isEdit && added === 0 && removed === 0;

  const save = useMutation({
    mutationFn: async () => {
      if (!gname) throw new Error("enter a combo name");
      if (/\s/.test(gname)) throw new Error("combo name cannot contain spaces");
      if (!isEdit && props.existingNames.includes(gname)) {
        throw new Error(
          `a combo named “${gname}” already exists — open it and use Edit, or pick another name`,
        );
      }
      if (selected.size === 0) throw new Error("tick at least one model");
      const entries = Array.from(selected.values());
      // Attach first, detach second. POST is not idempotent — an
      // already-attached pair answers 409 — so skip the ones already on the
      // combo. Ordering matters: if an attach fails, the old routes are still
      // attached and the combo keeps routing; detaching first could leave it
      // with fewer routes than it started with.
      const attached = new Set(props.editing?.deployments.map(depKey) ?? []);
      for (const e of entries) {
        if (attached.has(depKey(e))) continue;
        await addDeployment(gname, { provider: e.provider, model_id: e.model_id, weight: 1 });
      }
      if (isEdit) {
        // Detach the ones that were there but are no longer ticked.
        const unticked = props.editing!.deployments
          .filter((d) => !selected.has(depKey(d)))
          .map((d) => ({ provider: d.provider, model_id: d.model_id }));
        for (const e of unticked) await deleteDeployment(gname, e.provider, e.model_id);
      }
    },
    onSuccess: () => {
      invalidateModels(qc);
      setLocalError(null);
      props.onSaved(gname);
      props.onClose();
    },
    onError: (e) => setLocalError(e.message),
  });

  const resultIds = searching ? groups.flatMap((g) => g.ids) : (groups[0]?.ids ?? []);
  const resultKeys = searching
    ? groups.flatMap((g) => g.ids.map((m) => depKey({ provider: g.provider.name, model_id: m })))
    : (groups[0]?.ids ?? []).map((m) => depKey({ provider: active, model_id: m }));
  const allOn = resultKeys.length > 0 && resultKeys.every((k) => selected.has(k));
  const setAllVisible = () => {
    if (!searching) return setAll(active, resultIds);
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

  const customRef = useRef<HTMLInputElement | null>(null);
  const error = localError ?? (save.error ? save.error.message : "");

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      title={isEdit ? `Edit combo · ${props.editing!.name}` : "Create combo"}
      subtitle={
        isEdit
          ? "Add or remove deployments. The combo name is fixed while the group exists."
          : "Name the model string clients will send, then tick the deployments it should route across."
      }
      icon={Layers}
      wide
      size="5xl"
      contained
      footer={
        <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center">
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-[var(--admin-text-dim)]">
            <span>
              <span className="font-medium text-[var(--admin-text)]">{selected.size}</span> model
              {selected.size === 1 ? "" : "s"}
            </span>
            <span className="text-[var(--admin-border)]">·</span>
            <span>
              <span className="font-medium text-[var(--admin-text)]">{providersSpanned}</span>{" "}
              provider
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
            <Button
              variant="ghost"
              type="button"
              onClick={props.onClose}
              disabled={save.isPending}
              className="min-h-11"
            >
              Cancel
            </Button>
            <Button
              disabled={save.isPending || !gname || selected.size === 0 || noopEdit}
              onClick={() => save.mutate()}
              className="min-h-11 flex-1 sm:flex-none"
            >
              {save.isPending ? "Applying…" : isEdit ? "Save changes" : "Create combo"}
            </Button>
          </div>
        </div>
      }
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
            className="min-h-11 pl-9 pr-14"
          />
          {search && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setSearch("")}
              className="absolute right-1 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
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
            aria-orientation={wide ? "vertical" : "horizontal"}
            onKeyDown={onRailKeyDown}
            className="admin-scroll flex w-full shrink-0 gap-1.5 overflow-x-auto overscroll-x-contain sm:w-52 sm:flex-col sm:gap-0.5 sm:overflow-y-auto sm:overflow-x-visible sm:pr-1"
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
                  className={`flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded-lg px-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 sm:w-full ${
                    on
                      ? "bg-blue-500/10 text-blue-200 shadow-[inset_2px_0_0_0_rgba(59,130,246,0.55)]"
                      : "text-[var(--admin-text-muted)] hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
                  }`}
                >
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ backgroundColor: providerHue(r.name) }}
                  />
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
                    title={
                      searching
                        ? `${r.matches} of ${r.total} model ids match`
                        : `${r.chosen} of ${r.total} selected`
                    }
                  >
                    {searching ? r.matches : `${r.chosen}/${r.total}`}
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
                  className="ml-auto min-h-11 shrink-0 rounded-lg border border-white/[0.08] px-3 text-[11px] text-[var(--admin-text-dim)] transition-colors hover:border-blue-500/30 hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                >
                  {allOn ? "clear" : "select all"}
                </button>
              )}
            </div>

            {/* the single scroll region */}
            <div className="admin-scroll min-h-0 flex-1 overflow-y-auto p-2">
              {resultIds.length === 0 ? (
                <div className="px-3 py-8 text-center">
                  <Boxes size={20} className="mx-auto mb-2 opacity-40" />
                  <p className="text-[12px] text-[var(--admin-text-dim)]">
                    {props.providerOptions.length === 0
                      ? "No providers configured yet. Add one first, then come back."
                      : searching
                        ? `No model id matches “${search.trim()}”.`
                        : "This provider has no registered model ids yet."}
                  </p>
                  {searching ? (
                    <Button
                      variant="ghost"
                      className="mt-3 min-h-11"
                      onClick={() => setSearch("")}
                    >
                      <X size={13} /> Clear search
                    </Button>
                  ) : active ? (
                    <Button
                      variant="ghost"
                      className="mt-3 min-h-11"
                      onClick={() => customRef.current?.focus()}
                    >
                      <Plus size={13} /> Add a custom model id
                    </Button>
                  ) : null}
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
                            className={`flex min-h-11 w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 font-mono text-[12px] transition-colors focus-within:ring-2 focus-within:ring-blue-400/50 ${
                              on
                                ? "bg-blue-500/10 text-blue-200"
                                : "text-[var(--admin-text-muted)] hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
                            }`}
                          >
                            <input
                              type="checkbox"
                              className="h-4 w-4 shrink-0 accent-blue-500"
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
                  ref={customRef}
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
              className="flex min-h-11 w-full items-center justify-between px-2.5 py-1.5"
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
              /* Capped: the tray is shrink-0 inside a viewport-capped panel whose
                 body does not scroll, so an unbounded chip list would absorb all
                 the space, collapse the model list, and push the action bar past
                 the clipped bottom. */
              <div className="admin-scroll flex max-h-40 flex-wrap gap-1 overflow-y-auto px-2.5 pb-2">
                {Array.from(selected.values()).map((s) => (
                  <span
                    key={depKey(s)}
                    className="inline-flex min-h-11 max-w-full min-w-0 items-center gap-1 rounded-md border border-blue-500/20 bg-blue-500/10 pl-2 pr-1 font-mono text-[11px] text-blue-200"
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
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded text-blue-300/70 transition-colors hover:bg-blue-500/20 hover:text-blue-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                    >
                      <X size={11} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

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
// The selected combo's routing composition: the client-facing model string,
// the live routability band, and one editable row per deployment. All
// add/remove is handled by the dialog (its "Edit" button); this pane edits
// weights and detaches a single route.

/** Inline weight editor. Commits on Enter or blur; Escape restores the value. */
function WeightControl(props: {
  value: number;
  pending: boolean;
  onCommit: (weight: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(props.value));
  const [invalid, setInvalid] = useState(false);
  // Enter and blur both commit. Without this a single edit PATCHed twice.
  const committedRef = useRef(false);

  // Track the server value while idle so a refetch corrects the field, but
  // never yank the box out from under someone mid-edit.
  useEffect(() => {
    if (!editing) {
      setDraft(String(props.value));
      setInvalid(false);
    }
  }, [props.value, editing]);

  const begin = () => {
    committedRef.current = false;
    setDraft(String(props.value));
    setInvalid(false);
    setEditing(true);
  };

  const commit = () => {
    if (committedRef.current) return;
    const n = parseInt(draft, 10);
    if (!Number.isFinite(n) || n < 1) {
      setInvalid(true);
      return;
    }
    committedRef.current = true;
    if (n !== props.value) props.onCommit(n);
    setEditing(false);
  };

  if (editing) {
    return (
      <form
        onSubmit={(e) => {
          e.preventDefault();
          commit();
        }}
        className="flex items-center gap-1"
      >
        <Input
          type="number"
          min={1}
          step={1}
          autoFocus
          value={draft}
          aria-label="Deployment weight"
          aria-invalid={invalid}
          onChange={(e) => {
            setDraft(e.target.value);
            setInvalid(false);
          }}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              committedRef.current = true;
              setEditing(false);
            }
          }}
          className={`h-11 w-16 text-center font-mono text-[12px] ${
            invalid ? "border-red-400/60" : ""
          }`}
        />
        <span className="font-mono text-[10px] text-[var(--admin-text-dim)]">w</span>
      </form>
    );
  }

  return (
    <button
      type="button"
      onClick={begin}
      className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-lg px-2 font-mono text-[12px] tabular-nums text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
      title="Edit weight (minimum 1)"
      aria-label={`Deployment weight ${props.value}. Activate to edit.`}
    >
      <span className="text-[var(--admin-text-dim)]">w</span>
      {props.value}
      {props.pending && <Spinner className="h-3 w-3" />}
    </button>
  );
}

function ComboDetail(props: {
  combo: ModelGroup;
  crossProvider: boolean;
  error: string | null;
  onError: (m: string) => void;
  onEdit: () => void;
  onEmptied: () => void;
}) {
  const qc = useQueryClient();
  const [pendingWeight, setPendingWeight] = useState<string | null>(null);
  const [confirmDetach, setConfirmDetach] = useState<string | null>(null);

  // Stable display order, so a poll that returns the same routes in a
  // different order does not make the rows jump.
  const deployments = useMemo(
    () =>
      [...props.combo.deployments].sort(
        (a, b) => a.provider.localeCompare(b.provider) || a.model_id.localeCompare(b.model_id),
      ),
    [props.combo.deployments],
  );
  const routable = useMemo(() => routableDeployments(deployments), [deployments]);
  const readiness = useMemo(() => comboReadiness(deployments), [deployments]);
  const duplicates = useMemo(() => duplicateDeploymentIdents(deployments), [deployments]);
  const split = useMemo(() => providerWeightSplit(deployments), [deployments]);
  const splitTotal = split.reduce((a, s) => a + s.weight, 0);
  const totalWeight = deployments.reduce((a, d) => a + Math.max(1, d.weight), 0);

  const remove = useMutation({
    mutationFn: (t: { provider: string; model_id: string }) =>
      deleteDeployment(props.combo.name, t.provider, t.model_id),
    onSuccess: (res) => {
      props.onError("");
      if (res.group_emptied) props.onEmptied();
      invalidateModels(qc);
    },
    onError: (e) => props.onError(e.message),
  });

  const setWeight = useMutation({
    mutationFn: (t: { ident: string; weight: number }) =>
      patchModelGroup(props.combo.name, { weights: { [t.ident]: t.weight } }),
    onSuccess: () => {
      props.onError("");
      invalidateModels(qc);
    },
    onError: (e) => props.onError(e.message),
    onSettled: () => setPendingWeight(null),
  });

  const readyTone =
    readiness.total === 0
      ? "bg-zinc-600"
      : readiness.routable === readiness.total
        ? "bg-emerald-400"
        : readiness.routable > 0
          ? "bg-amber-400"
          : "bg-red-400";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="min-w-0 truncate text-[17px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
              {props.combo.name}
            </h3>
            {props.crossProvider && (
              <Badge
                tone="blue"
                title="Deployments span 2+ providers — cross-provider weighted round-robin"
              >
                cross-provider RR
              </Badge>
            )}
            <Badge tone="gray">
              {deployments.length} deployment{deployments.length === 1 ? "" : "s"}
            </Badge>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="admin-label">client model</span>
            <code className="min-w-0 truncate font-mono text-[12px] text-blue-200">
              {props.combo.name}
            </code>
            <CopyButton text={props.combo.name} className="min-h-11" />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={props.onEdit} className="min-h-11">
            <Pencil size={13} /> Edit
          </Button>
          <Button
            variant="outline"
            onClick={() => invalidateModels(qc)}
            className="min-h-11"
            title="Refetch deployments and their live health"
          >
            <RefreshCw size={13} /> Refresh
          </Button>
        </div>
      </div>

      {/* Live routability, stated the way the router picks. */}
      {props.error && <ErrorText>{props.error}</ErrorText>}

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-y border-[var(--admin-border)] py-3">
        <div className="flex items-center gap-2.5">
          <span className="admin-label">routing</span>
          <span className="font-mono text-[13px] tabular-nums text-[var(--admin-text)]">
            {readiness.routable}
            <span className="text-[var(--admin-text-dim)]">/{readiness.total}</span>
          </span>
          <div className="w-24">
            <ProgressBar
              value={readiness.total > 0 ? readiness.routable / readiness.total : 0}
              tone={readyTone}
            />
          </div>
        </div>
        {readiness.probation > 0 && (
          <span
            className="text-[12px]"
            style={{ color: DEPLOYMENT_STATE_COLOR.probation }}
            title="Restored by the healer and held back while a healthy sibling is available"
          >
            {readiness.probation} on probation
          </span>
        )}
        {readiness.cooling > 0 && (
          <span className="text-[12px]" style={{ color: DEPLOYMENT_STATE_COLOR.cooling }}>
            {readiness.cooling} cooling
          </span>
        )}
        {readiness.down > 0 && (
          <span className="text-[12px]" style={{ color: DEPLOYMENT_STATE_COLOR.down }}>
            {readiness.down} down
          </span>
        )}

        {split.length > 1 && splitTotal > 0 && (
          <div className="ml-auto flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
            <div
              className="wl-split w-32 shrink-0"
              role="img"
              aria-label={`Routable weight by provider — ${split
                .map((s) => `${s.provider} ${Math.round((s.weight / splitTotal) * 100)}%`)
                .join(", ")}`}
            >
              {split.map((s) => (
                <span
                  key={s.provider}
                  title={`${s.provider} — ${Math.round((s.weight / splitTotal) * 100)}% of routable weight`}
                  style={{
                    width: `${(s.weight / splitTotal) * 100}%`,
                    backgroundColor: providerHue(s.provider),
                  }}
                />
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {split.map((s) => (
                <span key={s.provider} className="flex items-center gap-1.5">
                  <span
                    className="wl-hue"
                    style={{ backgroundColor: providerHue(s.provider) }}
                  />
                  <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
                    {s.provider}
                  </span>
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {duplicates.length > 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/20 bg-amber-500/[0.06] px-3 py-2 text-[12px] text-amber-200">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <p>
            Duplicate routes: <span className="font-mono">{duplicates.join(", ")}</span>. The
            admin API addresses a deployment by provider + model_id, so these cannot be weighted
            or detached individually.
          </p>
        </div>
      )}

      {deployments.length === 0 ? (
        <Card className="p-4">
          <EmptyState
            icon={Boxes}
            title="No deployments"
            action={
              <Button variant="outline" onClick={props.onEdit} className="min-h-11">
                <Plus size={14} /> Add deployments
              </Button>
            }
          >
            This combo cannot route traffic until at least one deployment is attached.
          </EmptyState>
        </Card>
      ) : (
        <Card className="p-0">
          <ul className="divide-y divide-white/[0.04]">
            {deployments.map((d, i) => {
              const ident = `${d.provider}/${d.model_id}`;
              const rowKey = `${ident}#${i}`;
              const state = deploymentState(d, routable);
              const share = totalWeight > 0 ? Math.max(1, d.weight) / totalWeight : 0;
              const confirming = confirmDetach === rowKey;
              return (
                <li
                  key={rowKey}
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 sm:px-4"
                >
                  <div className="flex min-w-0 flex-1 basis-52 items-center gap-2.5">
                    <span
                      aria-hidden
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ backgroundColor: providerHue(d.provider) }}
                    />
                    <div className="min-w-0">
                      <Link
                        to={`/console/providers/${encodeURIComponent(d.provider)}`}
                        className="block truncate text-[12.5px] font-medium text-[var(--admin-text)] transition-colors hover:text-blue-300"
                        title={`Open ${d.provider}`}
                      >
                        {d.provider}
                      </Link>
                      <span className="block truncate font-mono text-[11px] text-[var(--admin-text-dim)]">
                        {d.model_id}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <WeightControl
                      value={d.weight}
                      pending={pendingWeight === ident}
                      onCommit={(weight) => {
                        setPendingWeight(ident);
                        setWeight.mutate({ ident, weight });
                      }}
                    />
                    {deployments.length > 1 && totalWeight > 0 && (
                      <span
                        className="hidden w-16 sm:block"
                        title={`${Math.round(share * 100)}% of configured weight`}
                      >
                        <ProgressBar value={share} tone="bg-blue-400/60" />
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-3 font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
                    <span title="p95 latency">
                      {d.available && d.p95_latency_ms > 0
                        ? `${Math.round(d.p95_latency_ms)}ms p95`
                        : "— p95"}
                    </span>
                    <span title="in-flight requests">{fmtInt(d.inflight)} in flight</span>
                  </div>

                  <div className="ml-auto flex items-center gap-2">
                    <span
                      className="wl-state"
                      style={{ color: DEPLOYMENT_STATE_COLOR[state] }}
                      title={DEPLOYMENT_STATE_TITLE[state]}
                    >
                      {DEPLOYMENT_STATE_WORD[state]}
                    </span>
                    {confirming ? (
                      <span className="flex items-center gap-1">
                        <Button
                          variant="danger"
                          className="min-h-11"
                          onClick={() => {
                            setConfirmDetach(null);
                            remove.mutate({ provider: d.provider, model_id: d.model_id });
                          }}
                        >
                          Detach
                        </Button>
                        <Button
                          variant="ghost"
                          className="min-h-11"
                          onClick={() => setConfirmDetach(null)}
                        >
                          Cancel
                        </Button>
                      </span>
                    ) : (
                      <Button
                        variant="ghost"
                        className="min-h-11 min-w-11"
                        title={
                          deployments.length === 1
                            ? "Detaching the last deployment removes this combo"
                            : `Detach ${d.model_id} from ${props.combo.name}`
                        }
                        aria-label={`Detach ${d.model_id} from ${props.combo.name}`}
                        onClick={() => {
                          // The last deployment empties (and therefore deletes)
                          // the group — make that consequence explicit.
                          if (deployments.length === 1) setConfirmDetach(rowKey);
                          else remove.mutate({ provider: d.provider, model_id: d.model_id });
                        }}
                      >
                        <Unlink size={14} />
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </div>
  );
}

// -- page ---------------------------------------------------------------------

export function CombosPage() {
  const [error, setError] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  // `editing` is the model group being edited in the dialog, or null for create.
  const [editingGroup, setEditingGroup] = useState<ModelGroup | null>(null);
  const [comboQuery, setComboQuery] = useState("");

  // Polled so health (available / inflight / p95) stays live, matching the
  // Models page. The dialog edits a snapshot captured at open time, so
  // background refetches never clobber in-dialog selections.
  const modelsQ = useQuery({
    queryKey: ["model-groups"],
    queryFn: getModels,
    refetchInterval: 10_000,
  });
  const providersQ = useQuery({ queryKey: ["providers"], queryFn: getProviders });

  const groups = useMemo(() => modelsQ.data?.groups ?? [], [modelsQ.data]);
  // Alphabetical roster for the list rail; detail lookups stay by name.
  const sortedGroups = useMemo(
    () => [...groups].sort((a, b) => a.name.localeCompare(b.name)),
    [groups],
  );
  const providerOptions = useMemo<ProviderOption[]>(
    () => (providersQ.data?.providers ?? []).map((p) => ({ name: p.name, type: p.provider_type })),
    [providersQ.data],
  );

  const visibleGroups = useMemo(() => {
    const q = comboQuery.trim().toLowerCase();
    if (!q) return sortedGroups;
    return sortedGroups.filter((g) => {
      const hay = [g.name, ...g.deployments.flatMap((d) => [d.provider, d.model_id])]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [sortedGroups, comboQuery]);

  // Summary figures across every combo. Routing / probation / cooling / down
  // use the router's own semantics, so the tiles agree with the detail pane.
  const stats = useMemo(() => {
    let totalDeps = 0;
    let routable = 0;
    let probation = 0;
    let cooling = 0;
    let down = 0;
    for (const g of groups) {
      const r = comboReadiness(g.deployments);
      totalDeps += r.total;
      routable += r.routable;
      probation += r.probation;
      cooling += r.cooling;
      down += r.down;
    }
    const providersSpanned = new Set(
      groups.flatMap((g) => g.deployments.map((d) => d.provider)),
    ).size;
    const crossCount = groups.filter(
      (g) => new Set(g.deployments.map((d) => d.provider)).size >= 2,
    ).length;
    return { totalDeps, routable, probation, cooling, down, providersSpanned, crossCount };
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

  // Land on a combo instead of an empty right pane on first load. Only fills a
  // null selection, so a newly created combo (set by `onSaved` before the
  // refetch lands) is never overwritten by a stale roster.
  useEffect(() => {
    if (selectedName === null && sortedGroups.length > 0) {
      setSelectedName(sortedGroups[0].name);
    }
  }, [selectedName, sortedGroups]);

  // A stale error from one combo must not follow the operator to another.
  useEffect(() => {
    setError(null);
  }, [selectedName]);

  // Dialog modes: explicit create, or edit the currently selected combo.
  const openDialog = (mode: "create" | "edit") => {
    setError(null);
    setEditingGroup(mode === "edit" ? (groups.find((g) => g.name === selectedName) ?? null) : null);
    setDialogOpen(true);
  };

  const existing = groups.find((g) => g.name === selectedName);
  const crossProvider =
    existing !== undefined &&
    new Set(existing.deployments.map((d) => d.provider)).size >= 2;

  if (modelsQ.isLoading) return <Spinner />;
  if (modelsQ.error) return <ErrorText>{modelsQ.error.message}</ErrorText>;

  const unavailable = stats.cooling + stats.down;
  const allRoutable = stats.totalDeps > 0 && stats.routable === stats.totalDeps;

  return (
    <div>
      <PageHeader
        title="Combos"
        subtitle="Compose named model groups from deployments across providers. A name with 2+ providers round-robins across them."
        right={
          <Button onClick={() => openDialog("create")} className="min-h-11">
            <Plus size={14} /> New combo
          </Button>
        }
      />
      {/* Detail-level errors are rendered inside the detail card, next to the
          control that failed — a banner at the top of the page is out of view
          on a phone, where the detail sits below the roster. */}

      {/* Summary: one ruled line of figures, matching the Models ledger — not
          four generic stat cards. The operator needs the composition and its
          health at a glance; the figures carry both without four boxes. */}
      <div className="mb-5 flex flex-wrap items-center gap-x-7 gap-y-4 border-y border-[var(--admin-border)] py-4">
        <Figure label="Combos" value={fmtInt(groups.length)} />
        <Figure label="Deployments" value={fmtInt(stats.totalDeps)} />
        <Figure
          label="Routing"
          value={`${fmtInt(stats.routable)} of ${fmtInt(stats.totalDeps)}`}
          tone={
            stats.totalDeps === 0
              ? undefined
              : allRoutable
                ? "var(--admin-success)"
                : stats.routable > 0
                  ? "var(--admin-warning)"
                  : "var(--admin-danger)"
          }
        />
        <Figure
          label="Held back"
          value={fmtInt(stats.probation)}
          tone={stats.probation > 0 ? "var(--admin-accent)" : undefined}
        />
        <Figure
          label="Unavailable"
          value={fmtInt(unavailable)}
          tone={unavailable > 0 ? "var(--admin-warning)" : undefined}
        />
        <Figure label="Cross-provider" value={fmtInt(stats.crossCount)} />
        {stats.providersSpanned > 0 && (
          <span className="ml-auto hidden font-mono text-[11px] text-[var(--admin-text-dim)] sm:inline">
            {fmtInt(stats.providersSpanned)} provider
            {stats.providersSpanned === 1 ? "" : "s"} spanned
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[300px_minmax(0,1fr)] lg:items-start">
        {/* left: combo roster. Sticky on desktop so the list stays reachable
            while the deployment roster scrolls. */}
        <Card className="flex min-h-0 flex-col p-0 lg:sticky lg:top-4 lg:max-h-[calc(100vh-10rem)] lg:self-start">
          <div className="flex shrink-0 flex-col gap-2 border-b border-[var(--admin-border)] p-3">
            <div className="flex items-center gap-2 px-1">
              <Layers size={15} className="text-[var(--admin-text-dim)]" />
              <span className="text-[12px] font-semibold text-[var(--admin-text)]">Combos</span>
              <span className="ml-auto font-mono text-[10px] tabular-nums text-[var(--admin-text-dim)]">
                {comboQuery ? `${visibleGroups.length}/${groups.length}` : fmtInt(groups.length)}
              </span>
            </div>
            <div className="relative">
              <Search
                size={14}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
              />
              <Input
                type="search"
                value={comboQuery}
                onChange={(e) => setComboQuery(e.target.value)}
                placeholder="Filter combos…"
                aria-label="Filter combos"
                className="min-h-11 pl-9 pr-12"
              />
              {comboQuery && (
                <button
                  type="button"
                  aria-label="Clear combo filter"
                  onClick={() => setComboQuery("")}
                  className="absolute right-1 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                >
                  <X size={14} />
                </button>
              )}
            </div>
          </div>

          <div className="admin-scroll max-h-[40vh] min-h-0 overflow-y-auto p-2 lg:max-h-none lg:flex-1">
            {groups.length === 0 ? (
              <EmptyState
                icon={Layers}
                title="No combos yet"
                action={
                  <Button
                    variant="outline"
                    onClick={() => openDialog("create")}
                    className="min-h-11"
                  >
                    <Plus size={14} /> New combo
                  </Button>
                }
              >
                Create one to start round-robining across providers.
              </EmptyState>
            ) : visibleGroups.length === 0 ? (
              <EmptyState icon={Search} title="No combos match">
                <button
                  type="button"
                  onClick={() => setComboQuery("")}
                  className="inline-flex min-h-11 items-center text-[var(--admin-text-muted)] underline-offset-2 transition-colors hover:text-[var(--admin-text)] hover:underline"
                >
                  Clear the filter
                </button>
              </EmptyState>
            ) : (
              <div className="space-y-0.5">
                {visibleGroups.map((g) => {
                  const active = g.name === selectedName;
                  const providers = new Set(g.deployments.map((d) => d.provider)).size;
                  const readiness = comboReadiness(g.deployments);
                  const dot =
                    readiness.total === 0
                      ? "bg-zinc-600"
                      : readiness.routable === readiness.total
                        ? "bg-emerald-400"
                        : readiness.routable > 0
                          ? "bg-amber-400"
                          : "bg-red-400";
                  return (
                    <button
                      key={g.name}
                      type="button"
                      onClick={() => setSelectedName(g.name)}
                      aria-current={active ? "true" : undefined}
                      title={
                        readiness.total === 0
                          ? "No deployments — this combo cannot route"
                          : readiness.routable === readiness.total
                            ? "Every deployment is routing"
                            : `${readiness.routable} of ${readiness.total} deployments routing`
                      }
                      className={`flex min-h-11 w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 ${
                        active
                          ? "bg-blue-500/[0.08] text-blue-200 shadow-[inset_2px_0_0_0_rgba(59,130,246,0.55)]"
                          : "text-[var(--admin-text-muted)] hover:bg-white/[0.02] hover:text-[var(--admin-text)]"
                      }`}
                    >
                      <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="min-w-0 truncate font-medium">{g.name}</span>
                          {providers >= 2 && (
                            <Badge tone="blue" title="Cross-provider round-robin">
                              RR
                            </Badge>
                          )}
                        </span>
                        <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 font-mono text-[10px] text-[var(--admin-text-dim)]">
                          <span>
                            {g.deployments.length} deploy
                            {g.deployments.length === 1 ? "" : "s"}
                          </span>
                          <span aria-hidden>·</span>
                          <span>
                            {providers} provider{providers === 1 ? "" : "s"}
                          </span>
                          <span aria-hidden>·</span>
                          <span
                            style={{
                              color:
                                readiness.routable === readiness.total && readiness.total > 0
                                  ? "var(--admin-success)"
                                  : readiness.routable > 0
                                    ? "var(--admin-warning)"
                                    : readiness.total > 0
                                      ? "var(--admin-danger)"
                                      : undefined,
                            }}
                          >
                            {readiness.routable}/{readiness.total} routing
                          </span>
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </Card>

        {/* right: detail */}
        <Card className="min-w-0 p-4 sm:p-5">
          {existing ? (
            <ComboDetail
              combo={existing}
              crossProvider={crossProvider}
              error={error}
              onError={setError}
              onEdit={() => openDialog("edit")}
              onEmptied={() =>
                setSelectedName(
                  sortedGroups.find((g) => g.name !== existing.name)?.name ?? null,
                )
              }
            />
          ) : selectedName !== null && modelsQ.isFetching ? (
            <div className="flex justify-center py-16">
              <Spinner />
            </div>
          ) : (
            <EmptyState
              icon={Layers}
              title="Pick a combo"
              action={
                <Button variant="outline" onClick={() => openDialog("create")} className="min-h-11">
                  <Plus size={14} /> New combo
                </Button>
              }
            >
              A combo name is the model string your clients send.
            </EmptyState>
          )}
        </Card>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <Link
          to="/console/models"
          className="text-[12px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
        >
          See all model groups and their health →
        </Link>
        <Link
          to="/console/providers"
          className="text-[12px] text-[var(--admin-text-dim)] transition-colors hover:text-[var(--admin-text)]"
        >
          Manage providers →
        </Link>
      </div>

      <ComboDialog
        open={dialogOpen}
        editing={editingGroup}
        providerOptions={providerOptions}
        registeredByProvider={registeredByProvider}
        existingNames={sortedGroups.map((g) => g.name)}
        onSaved={(n) => setSelectedName(n)}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  );
}
