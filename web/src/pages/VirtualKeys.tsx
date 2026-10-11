// VirtualKeys page — issue and manage client credentials: budgets, rate limits,
// model allowlists, expiry. Generated plaintext keys are revealed exactly once.

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Clock,
  Gauge,
  KeyRound,
  Layers,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Upload,
  Wallet,
  X,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { deleteKey, disableKey, generateKey, listKeys, patchKey } from "@/api/client";
import type { VirtualKey } from "@/api/types";
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
  NumberInput,
  PageHeader,
  ProgressBar,
  Select,
  SortHeader,
  StatCard,
  Table,
  TD,
} from "@/components/ui";
import { fmtDateTime, fmtInt, fmtUsd } from "@/lib/format";

/** "" → null (means "leave unchanged"); unparsable → NaN; otherwise the number. */
function tryParse(s: string): number | null {
  const t = s.trim();
  if (!t) return null;
  return Number.isFinite(Number(t)) ? Number(t) : NaN;
}

function numbersValid(ns: (number | null)[]): boolean {
  return ns.every((n) => n === null || !Number.isNaN(n));
}

function isBad(n: number | null): boolean {
  return n !== null && Number.isNaN(n);
}

/** "720" → "30d", "24" → "1d", "1.5" → "1.5h", "" → "never". Used by the
 *  footer recap so the picked limits read at a glance. */
function humanTtl(hours: string): string {
  const h = hours.trim();
  if (!h) return "never";
  const n = Number(h);
  if (!Number.isFinite(n)) return "—";
  if (n >= 24 && n % 24 === 0) return `${Math.round(n / 24)}d`;
  return `${h}h`;
}

/** "100000" → "100k", "2500" → "2500". Keeps the footer recap on one line. */
function compactNum(v: string): string {
  const n = v.trim();
  if (!n) return "∞";
  if (!Number.isFinite(Number(n))) return "—";
  const num = Number(n);
  if (num >= 1_000_000) return `${Math.round(num / 1000)}k`;
  return n;
}

/** "a, b,,c" → ["a","b","c"]; "" → [] (= all models). */
function parseCsv(s: string): string[] {
  return s
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

type KeyStatus = { label: "active" | "expired" | "disabled"; tone: "green" | "amber" | "gray" };

function keyStatus(k: VirtualKey): KeyStatus {
  if (k.disabled) return { label: "disabled", tone: "gray" };
  if (k.expires_at != null && k.expires_at * 1000 < Date.now()) {
    return { label: "expired", tone: "amber" };
  }
  return { label: "active", tone: "green" };
}

/** Fraction of a key's budget cap already spent, or null when it has no cap.
 *  The single definition of "how full is this key" — the stat tile, the filter
 *  chip count and the row's progress bar all read this so they cannot disagree. */
function budgetUsed(k: VirtualKey): number | null {
  if (k.max_budget == null || k.max_budget <= 0) return null;
  return k.spend_to_date / k.max_budget;
}

/** The threshold at which a capped key counts as "near cap", matching the
 *  amber band of `ProgressBar`'s own tone scale. */
const NEAR_CAP = 0.8;

function isNearCap(k: VirtualKey): boolean {
  const used = budgetUsed(k);
  return used != null && used >= NEAR_CAP;
}

/** Status pill with a leading colored dot. The dot carries the state color at a
 *  glance even when the label is truncated on a narrow screen; the pill uses the
 *  shared admin-badge tone so it matches the rest of the console. */
const STATUS_DOT: Record<KeyStatus["tone"], string> = {
  green: "bg-emerald-400",
  amber: "bg-amber-400",
  gray: "bg-zinc-500",
};

/** Icon tint matching the status dot, for the key tile. */
const STATUS_ICON: Record<KeyStatus["tone"], string> = {
  green: "text-emerald-400/70",
  amber: "text-amber-400/70",
  gray: "text-zinc-500",
};

function StatusBadge(props: { tone: KeyStatus["tone"]; label: string }) {
  return (
    <span
      className={`admin-badge admin-badge-${
        props.tone === "green" ? "green" : props.tone === "amber" ? "amber" : "gray"
      }`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[props.tone]}`} />
      {props.label}
    </span>
  );
}

/** A single "clear this field → unlimited" toggle in the edit dialog. Rows keep
 *  a ≥44px hit area for touch and an amber tint when armed so the intent is
 *  unmistakable on a phone, not just on a checkbox tick. */
function ClearCheck(props: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label
      className={`flex min-h-11 cursor-pointer items-center gap-2.5 rounded-md px-2.5 text-[12.5px] transition-colors ${
        props.checked
          ? "bg-[#1a1408] text-amber-200"
          : "text-[var(--admin-text-muted)] hover:bg-[#141414]"
      }`}
    >
      <input
        type="checkbox"
        checked={props.checked}
        onChange={(e) => props.onChange(e.target.checked)}
        className="h-4 w-4 shrink-0 rounded border-[var(--admin-border)] accent-white"
      />
      {props.label}
    </label>
  );
}

function BudgetCell(props: { k: VirtualKey }) {
  const { k } = props;
  const used = budgetUsed(k);
  // The bar already shifts emerald → amber → red, but the reader still has to do
  // the division to learn "how close". Print the percentage, and tint the value
  // with the same band the bar is in so the two read as one signal.
  const hot = used != null && used >= NEAR_CAP;
  return (
    <div className="w-40 space-y-1">
      {used != null && <ProgressBar value={used} />}
      <span className="font-mono text-[12px] tabular-nums text-[var(--admin-text)]">
        {fmtUsd(k.spend_to_date)}
        {k.max_budget != null && (
          <span className="text-[var(--admin-text-dim)]"> / {fmtUsd(k.max_budget)}</span>
        )}
        {used != null && (
          <span className={hot ? "text-amber-400" : "text-[var(--admin-text-dim)]"}>
            {" "}
            {Math.round(used * 100)}%
          </span>
        )}
      </span>
    </div>
  );
}

/** Red validation line rendered under a Field (Field's own hint stays neutral). */
function FieldError(props: { children: ReactNode }) {
  return <p className="mt-1 text-[11px] font-medium text-red-400">{props.children}</p>;
}

/** Inset panel wrapping one logical group of the create form, numbered so the
 *  three groups read as an ordered pass over the key's shape. Solid surfaces
 *  (no transparency) keep the groups legible against the dialog body. */
function FormSection(props: { index: string; icon: LucideIcon; title: string; desc?: string; children: ReactNode }) {
  const Icon = props.icon;
  return (
    <section className="rounded-2xl border border-[var(--admin-border)] bg-[#0c0c0c] p-4">
      <div className="mb-3.5 flex items-center gap-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[#1a1a1a]">
          <Icon className="h-3.5 w-3.5 text-[var(--admin-text)]" />
        </span>
        <div className="min-w-0 leading-tight">
          <p className="admin-label">{props.title}</p>
          {props.desc && <p className="mt-0.5 text-[11px] text-[var(--admin-text-dim)]">{props.desc}</p>}
        </div>
        <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
          {props.index}
        </span>
      </div>
      <div className="space-y-3">{props.children}</div>
    </section>
  );
}

/** Two-up selectable card — used for the key-source choice. */
function KeySourceCard(props: {
  active: boolean;
  icon: LucideIcon;
  title: string;
  desc: string;
  onClick: () => void;
}) {
  const Icon = props.icon;
  return (
    <button
      type="button"
      aria-pressed={props.active}
      onClick={props.onClick}
      className={`relative flex flex-1 items-start gap-2.5 rounded-xl border p-3.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500 ${
        props.active
          ? "border-[#232323] bg-[#1a1a1a]"
          : "border-[var(--admin-border)] bg-[#0c0c0c] hover:border-[#1f1f1f] hover:bg-[#141414]"
      }`}
    >
      {props.active && (
        <span
          aria-hidden
          className="absolute right-2.5 top-2.5 flex h-4 w-4 items-center justify-center rounded-full bg-[#2a2a2a]"
        >
          <Check size={10} className="text-[var(--admin-text)]" />
        </span>
      )}
      <span
        className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
          props.active ? "bg-[#2a2a2a]" : "bg-[#1a1a1a]"
        }`}
      >
        <Icon className={`h-4 w-4 ${props.active ? "text-[var(--admin-text)]" : "text-[var(--admin-text-dim)]"}`} />
      </span>
      <div className="min-w-0 pr-5">
        <p className="text-[13px] font-medium text-[var(--admin-text)]">{props.title}</p>
        <p className="mt-0.5 text-[11px] leading-tight text-[var(--admin-text-dim)]">{props.desc}</p>
      </div>
    </button>
  );
}

/** Selectable pill — the one pill affordance on this page, used both for the
 *  list's filter chips and for the create dialog's limit presets. The icon is
 *  optional so a filter chip can lead with its own glyph and a preset with the
 *  lightning bolt. */
function Pill(props: {
  active: boolean;
  label: string;
  onClick: () => void;
  icon?: LucideIcon;
  /** Trailing count, e.g. how many keys a filter would show. */
  count?: number;
}) {
  const Icon = props.icon;
  return (
    <button
      type="button"
      aria-pressed={props.active}
      onClick={props.onClick}
      className={`inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3.5 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500 ${
        props.active
          ? "border-[#232323] bg-[#1c1c1c] text-[var(--admin-text)]"
          : "border-[var(--admin-border)] bg-[#0c0c0c] text-[var(--admin-text-muted)] hover:border-[#1f1f1f] hover:bg-[#141414] hover:text-[var(--admin-text)]"
      }`}
    >
      {Icon && (
        <Icon
          size={11}
          className={props.active ? "text-[var(--admin-text)]" : "text-[var(--admin-text-dim)]"}
        />
      )}
      {props.label}
      {props.count != null && (
        <span
          className={`font-mono text-[10.5px] tabular-nums ${
            props.active ? "text-[var(--admin-text-muted)]" : "text-[var(--admin-text-dim)]"
          }`}
        >
          {props.count}
        </span>
      )}
    </button>
  );
}

/** Chip input for the model allowlist. Enter/comma commits a model, Backspace
 *  on an empty input drops the last chip, and an empty list means "all models".
 *  Remove buttons keep a 44px hit area via negative margins so the chips stay
 *  visually compact on touch. */
function ModelChips(props: { value: string; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState("");
  const chips = parseCsv(props.value);

  function commit(raw: string) {
    const parts = raw
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    if (parts.length === 0) return;
    const next = [...chips, ...parts.filter((p) => !chips.includes(p))];
    props.onChange(next.join(", "));
    setDraft("");
  }

  return (
    <div className="rounded-lg border border-[var(--admin-border)] bg-[#0c0c0c] p-1.5 transition-colors focus-within:border-[#232323]">
      {chips.length > 0 && (
        <div className="mb-1.5 flex flex-wrap gap-1.5">
          {chips.map((m) => (
            <span
              key={m}
              className="inline-flex items-center gap-1 rounded-md bg-[#1a1a1a] py-1 pl-2 pr-1 text-[12px] font-medium text-[var(--admin-text)]"
            >
              <Layers size={10} className="text-[var(--admin-text-muted)]" />
              <span className="font-mono">{m}</span>
              <button
                type="button"
                aria-label={`Remove ${m}`}
                onClick={() => props.onChange(chips.filter((c) => c !== m).join(", "))}
                className="-my-2.5 -mr-1.5 flex h-11 w-11 items-center justify-center rounded-md text-[var(--admin-text-dim)] transition-colors hover:bg-[#232323] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500"
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        value={draft}
        onChange={(e) => {
          const v = e.target.value;
          if (v.includes(",")) commit(v);
          else setDraft(v);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(draft);
          } else if (e.key === "Backspace" && draft === "" && chips.length > 0) {
            props.onChange(chips.slice(0, -1).join(", "));
          }
        }}
        onBlur={() => commit(draft)}
        placeholder={chips.length === 0 ? "model-a, model-b — empty = all models" : "Add model…"}
        className="w-full min-w-0 bg-transparent px-1.5 py-1 text-[13px] text-[var(--admin-text)] outline-none placeholder:text-[var(--admin-text-dim)]"
      />
    </div>
  );
}

/** Quick limit profiles: budget (USD), rpm, tpm, expiry (hours). */
const LIMIT_PRESETS: { id: string; label: string; values: { budget: string; rpm: string; tpm: string; ttl: string } }[] = [
  { id: "open", label: "Unrestricted", values: { budget: "", rpm: "", tpm: "", ttl: "" } },
  { id: "standard", label: "Standard", values: { budget: "25", rpm: "60", tpm: "100000", ttl: "" } },
  { id: "strict", label: "Strict", values: { budget: "5", rpm: "20", tpm: "20000", ttl: "24" } },
];

/** Expiry durations offered as one-click choices; "custom" reveals an hours input. */
const EXPIRY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Never expires" },
  { value: "24", label: "24 hours" },
  { value: "168", label: "7 days" },
  { value: "720", label: "30 days" },
  { value: "2160", label: "90 days" },
  { value: "custom", label: "Custom…" },
];

/** The list's filter chips. Each predicate is applied over the FULL key set, so
 *  a chip's count always describes the whole account rather than the filtered
 *  view — the same rule the stat tiles follow. */
type KeyFilter = "all" | "active" | "near-cap" | "expired" | "disabled";

const FILTERS: { id: KeyFilter; label: string; icon: LucideIcon }[] = [
  { id: "all", label: "All", icon: Layers },
  { id: "active", label: "Active", icon: ShieldCheck },
  { id: "near-cap", label: "Near cap", icon: Gauge },
  { id: "expired", label: "Expired", icon: Clock },
  { id: "disabled", label: "Disabled", icon: X },
];

function matchesFilter(k: VirtualKey, f: KeyFilter): boolean {
  switch (f) {
    case "all":
      return true;
    case "active":
      return keyStatus(k).label === "active";
    case "near-cap":
      return isNearCap(k);
    case "expired":
      return keyStatus(k).label === "expired";
    case "disabled":
      return keyStatus(k).label === "disabled";
  }
}

/** Sortable columns. `budget` sorts by fraction-of-cap consumed (uncapped keys
 *  last); `expires` puts never-expiring keys last rather than at epoch 0. */
type KeySort = "name" | "budget" | "rpm" | "expires";
type SortDir = "asc" | "desc";

/** Column index of each sortable column within the table's `head` array. Needed
 *  because `aria-sort` must sit on the `<th>`, and the sortable columns are not
 *  contiguous — Status and Models sit between Budget and RPM. */
const SORT_COLUMN_INDEX: Record<KeySort, number> = {
  name: 0,
  budget: 3,
  rpm: 4,
  expires: 6,
};

/** The same sort choice for narrow screens, where the table (and its sortable
 *  headers) is hidden but the cards still honour the active sort. */
const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: "name", label: "Name" },
  { value: "budget", label: "Budget use" },
  { value: "rpm", label: "RPM" },
  { value: "expires", label: "Expires soon" },
];

function sortValue(k: VirtualKey, s: KeySort): number | string {
  switch (s) {
    case "name":
      return k.alias.toLowerCase();
    case "budget":
      return budgetUsed(k) ?? Number.POSITIVE_INFINITY;
    case "rpm":
      return k.rpm ?? Number.POSITIVE_INFINITY;
    case "expires":
      return k.expires_at ?? Number.POSITIVE_INFINITY;
  }
}

function compareKeys(a: VirtualKey, b: VirtualKey, s: KeySort, dir: SortDir): number {
  const av = sortValue(a, s);
  const bv = sortValue(b, s);
  const cmp =
    typeof av === "number" && typeof bv === "number"
      ? av - bv
      : String(av).localeCompare(String(bv));
  return dir === "asc" ? cmp : -cmp;
}

/** Disable/enable + the two-step revoke, shared by the desktop row and the
 *  mobile card. Extracted rather than inlined twice so the revoke's auto-disarm
 *  timer exists in exactly one place — two copies would be free to drift. */
function useKeyActions(k: VirtualKey, onError: (m: string) => void) {
  const qc = useQueryClient();
  const [confirmingRevoke, setConfirmingRevoke] = useState(false);
  // Disarm the two-step confirm a few seconds after arming, so an abandoned
  // "Revoke" never stays live where a later stray click could fire it.
  useEffect(() => {
    if (!confirmingRevoke) return;
    const t = setTimeout(() => setConfirmingRevoke(false), 6000);
    return () => clearTimeout(t);
  }, [confirmingRevoke]);
  const disable = useMutation({
    mutationFn: () => disableKey(k.id, !k.disabled),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["keys"] }),
    onError: (e) => onError(e.message),
  });
  const revoke = useMutation({
    mutationFn: () => deleteKey(k.id),
    onSuccess: () => {
      setConfirmingRevoke(false);
      void qc.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (e) => onError(e.message),
  });
  return { confirmingRevoke, setConfirmingRevoke, disable, revoke };
}

type KeyActionProps = { k: VirtualKey; onEdit: (k: VirtualKey) => void; onError: (m: string) => void };

/** The Edit / Disable / Revoke group. `stretch` gives each button the full
 *  column width inside a mobile card; the table row right-aligns them. */
function KeyActions(props: KeyActionProps & { stretch?: boolean }) {
  const { confirmingRevoke, setConfirmingRevoke, disable, revoke } = useKeyActions(
    props.k,
    props.onError,
  );
  return (
    <div
      className={`flex flex-wrap items-center gap-1.5 ${props.stretch ? "w-full" : "justify-end"}`}
    >
      <Button
        variant="outline"
        className={props.stretch ? "flex-1" : undefined}
        onClick={() => props.onEdit(props.k)}
      >
        Edit
      </Button>
      <Button
        variant="outline"
        className={props.stretch ? "flex-1" : undefined}
        disabled={disable.isPending}
        onClick={() => disable.mutate()}
      >
        {props.k.disabled ? "Enable" : "Disable"}
      </Button>
      {/* Two-step destructive action: the first click arms a short confirm
          inline instead of a native window.confirm, so the intent is clear
          and the button gives undo-style feedback. Auto-cancels after a
          few seconds so an abandoned confirm never lingers armed. */}
      {confirmingRevoke ? (
        <>
          <Button
            variant="danger"
            className={props.stretch ? "flex-1" : undefined}
            disabled={revoke.isPending}
            onClick={() => revoke.mutate()}
          >
            {revoke.isPending ? "Revoking…" : "Confirm revoke"}
          </Button>
          <Button
            variant="ghost"
            className={props.stretch ? "flex-1" : undefined}
            disabled={revoke.isPending}
            onClick={() => setConfirmingRevoke(false)}
          >
            Cancel
          </Button>
        </>
      ) : (
        <Button
          variant="danger"
          className={props.stretch ? "flex-1" : undefined}
          onClick={() => setConfirmingRevoke(true)}
        >
          Revoke
        </Button>
      )}
    </div>
  );
}

/** The model allowlist, rendered identically in the table cell and the card. */
function ModelList(props: { models: string[] }) {
  if (props.models.length === 0) {
    return (
      <span
        className="text-[var(--admin-text-dim)]"
        title="No allowlist: every model group is reachable"
      >
        all
      </span>
    );
  }
  return (
    <span className="flex flex-wrap gap-1">
      {props.models.map((m) => (
        <Badge key={m} tone="blue">
          {m}
        </Badge>
      ))}
    </span>
  );
}

/** One key as a stacked card — the whole list below `md`, where an 8-column
 *  table would only be reachable by scrolling sideways. Shows exactly the
 *  fields the row shows, wrapped instead of truncated. */
function KeyCard(props: KeyActionProps) {
  const { k } = props;
  const status = keyStatus(k);
  const meta: { label: string; value: string }[] = [
    { label: "rpm", value: k.rpm != null ? fmtInt(k.rpm) : "∞" },
    { label: "tpm", value: k.tpm != null ? fmtInt(k.tpm) : "∞" },
    { label: "expires", value: k.expires_at != null ? fmtDateTime(k.expires_at) : "never" },
  ];
  return (
    <article
      aria-label={`${k.alias}, ${status.label}`}
      className="rounded-xl border border-[var(--admin-border)] bg-[#0c0c0c] p-3.5"
    >
      <div className="flex items-start gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02]">
          <KeyRound size={14} className={STATUS_ICON[status.tone]} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13.5px] font-medium text-[var(--admin-text)]">{k.alias}</p>
          <div className="mt-1.5">
            <StatusBadge tone={status.tone} label={status.label} />
          </div>
        </div>
      </div>

      <div className="mt-3">
        <ModelList models={k.models} />
      </div>

      <div className="mt-3">
        <BudgetCell k={k} />
      </div>

      <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 border-t border-[var(--admin-border)] pt-2.5">
        {meta.map((m) => (
          <div key={m.label} className="flex items-baseline gap-1.5">
            <dt className="admin-label">{m.label}</dt>
            <dd className="font-mono text-[11.5px] tabular-nums text-[var(--admin-text)]">
              {m.value}
            </dd>
          </div>
        ))}
      </dl>

      <div className="mt-3 border-t border-[var(--admin-border)] pt-3">
        <KeyActions {...props} stretch />
      </div>
    </article>
  );
}

function KeyRow(props: KeyActionProps) {
  const { k } = props;
  const status = keyStatus(k);
  return (
    <tr className="group">
      {/* Sticky identity cell so horizontal scroll keeps the row's name — and
          now its status, via the tinted tile — pinned. */}
      <TD className="sticky left-0 z-10 bg-[var(--admin-surface)] after:absolute after:inset-y-0 after:right-0 after:w-px after:bg-[var(--admin-border)] after:content-['']">
        <span className="flex items-center gap-2.5">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] transition-colors group-hover:border-[var(--admin-border-hover)]">
            <KeyRound size={13} className={STATUS_ICON[status.tone]} />
          </span>
          <span className="min-w-0 truncate font-medium text-[var(--admin-text)]">{k.alias}</span>
        </span>
      </TD>
      <TD>
        <StatusBadge tone={status.tone} label={status.label} />
      </TD>
      <TD>
        <ModelList models={k.models} />
      </TD>
      <TD>
        <BudgetCell k={k} />
      </TD>
      <TD className="font-mono tabular-nums">{k.rpm != null ? fmtInt(k.rpm) : "—"}</TD>
      <TD className="hidden font-mono tabular-nums lg:table-cell">{k.tpm != null ? fmtInt(k.tpm) : "—"}</TD>
      <TD className="hidden font-mono text-[12px] text-[var(--admin-text-dim)] xl:table-cell">
        {k.expires_at != null ? fmtDateTime(k.expires_at) : "—"}
      </TD>
      <TD>
        <KeyActions {...props} />
      </TD>
    </tr>
  );
}

export function VirtualKeysPage() {
  const qc = useQueryClient();

  // -- list -------------------------------------------------------------------
  const [pageError, setPageError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<KeyFilter>("all");
  const [sortKey, setSortKey] = useState<KeySort>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const query = useQuery({ queryKey: ["keys"], queryFn: listKeys, refetchInterval: 15_000 });

  // -- create dialog ------------------------------------------------------------
  const [createOpen, setCreateOpen] = useState(false);
  const [created, setCreated] = useState<{ key: string; name: string } | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [authMode, setAuthMode] = useState<"random" | "custom">("random");
  const [customKey, setCustomKey] = useState("");
  const [modelsCsv, setModelsCsv] = useState("");
  const [budget, setBudget] = useState("");
  const [rpm, setRpm] = useState("");
  const [tpm, setTpm] = useState("");
  const [ttlHours, setTtlHours] = useState("");
  const [expiryCustom, setExpiryCustom] = useState(false);

  const budgetN = tryParse(budget);
  const rpmN = tryParse(rpm);
  const tpmN = tryParse(tpm);
  const ttlHN = tryParse(ttlHours);
  const numsOk = numbersValid([budgetN, rpmN, tpmN, ttlHN]);
  const customTooShort = authMode === "custom" && customKey.trim().length < 16;

  function openCreate() {
    setName("");
    setNameTouched(false);
    setAuthMode("random");
    setCustomKey("");
    setModelsCsv("");
    setBudget("");
    setRpm("");
    setTpm("");
    setTtlHours("");
    setExpiryCustom(false);
    setCreated(null);
    setCreateError(null);
    setCreateOpen(true);
  }

  function closeCreate() {
    setCreateOpen(false);
    setCreated(null);
  }

  const create = useMutation({
    mutationFn: (body: Parameters<typeof generateKey>[0]) => generateKey(body),
    onSuccess: (data) => setCreated({ key: data.key, name: name.trim() }),
    onError: (e) => setCreateError(e.message),
  });

  // -- edit dialog --------------------------------------------------------------
  const [editTarget, setEditTarget] = useState<VirtualKey | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [editBudget, setEditBudget] = useState("");
  const [editRpm, setEditRpm] = useState("");
  const [editTpm, setEditTpm] = useState("");
  const [editModels, setEditModels] = useState("");
  const [editExpiryCustom, setEditExpiryCustom] = useState(false);
  const [editHours, setEditHours] = useState("");
  const [clearExpiry, setClearExpiry] = useState(false);
  const [clearBudget, setClearBudget] = useState(false);
  const [clearRpm, setClearRpm] = useState(false);
  const [clearTpm, setClearTpm] = useState(false);
  const [clearModels, setClearModels] = useState(false);

  const editBudgetN = tryParse(editBudget);
  const editRpmN = tryParse(editRpm);
  const editTpmN = tryParse(editTpm);
  const editHoursN = tryParse(editHours);
  const editNumsOk = numbersValid([editBudgetN, editRpmN, editTpmN, editHoursN]);
  // Mirrors the create form: "custom" wins, otherwise a value that is not one
  // of the listed durations (because it was typed in) also means custom.
  const editExpiryPicked =
    editExpiryCustom ||
    (editHours !== "" &&
      !EXPIRY_OPTIONS.some((o) => o.value !== "custom" && o.value === editHours))
      ? "custom"
      : editHours;

  function pickEditExpiry(v: string) {
    if (v === "custom") setEditExpiryCustom(true);
    else {
      setEditExpiryCustom(false);
      setEditHours(v);
    }
  }

  function openEdit(k: VirtualKey) {
    setEditTarget(k);
    // Seed every editable field from the key being edited. The inputs are the
    // only source the patch is built from, so an unseeded field would either
    // send a stale value from the previously edited key or read as "empty"
    // and overwrite the stored one (models = [] silently means all-allowed).
    setEditBudget(k.max_budget != null ? String(k.max_budget) : "");
    setEditRpm(k.rpm != null ? String(k.rpm) : "");
    setEditTpm(k.tpm != null ? String(k.tpm) : "");
    setEditModels(k.models.join(", "));
    // Expiry is deliberately left blank: an untouched field must mean "leave
    // unchanged", and pre-filling a duration would silently reschedule the key
    // every time the dialog is opened. The current value is shown as a hint and
    // can be cleared explicitly.
    setEditExpiryCustom(false);
    setEditHours("");
    setClearExpiry(false);
    setClearBudget(false);
    setClearRpm(false);
    setClearTpm(false);
    setClearModels(false);
    setEditError(null);
  }

  function closeEdit() {
    setEditTarget(null);
    setEditError(null);
  }

  const editSave = useMutation({
    mutationFn: (args: { id: string; patch: Parameters<typeof patchKey>[1] }) =>
      patchKey(args.id, args.patch),
    onSuccess: () => {
      closeEdit();
      void qc.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (e) => setEditError(e.message),
  });

  // Picking "Custom…" without typing hours used to be a silent no-op: the field
  // read as set, but the patch carried no expiry at all. Block the save and say
  // why inline instead — hover-only feedback is invisible on touch.
  const editExpiryMissing = editExpiryPicked !== "" && editHoursN == null;
  const editBlockedWhy = !editNumsOk
    ? "A limit field holds something that isn't a number"
    : editExpiryMissing
      ? "Enter the custom expiry in hours, or pick a duration"
      : editSave.isPending
        ? "Saving…"
        : null;
  const canSaveEdit = editBlockedWhy == null;

  // -- create-form derived state ------------------------------------------------
  const models = parseCsv(modelsCsv);
  const activePreset = LIMIT_PRESETS.find(
    (p) =>
      p.values.budget === budget &&
      p.values.rpm === rpm &&
      p.values.tpm === tpm &&
      p.values.ttl === ttlHours,
  );
  const expiryPicked =
    expiryCustom || (ttlHours !== "" && !EXPIRY_OPTIONS.some((o) => o.value !== "custom" && o.value === ttlHours))
      ? "custom"
      : ttlHours;
  const canCreate = name.trim() !== "" && numsOk && !customTooShort && !create.isPending;
  const blockedWhy = !name.trim()
    ? "Name is required"
    : !numsOk
      ? "A limit field holds something that isn't a number"
      : customTooShort
        ? "A custom key must be at least 16 characters"
        : create.isPending
          ? "Creating…"
          : null;

  function pickExpiry(v: string) {
    if (v === "custom") {
      setExpiryCustom(true);
    } else {
      setExpiryCustom(false);
      setTtlHours(v);
    }
  }

  // -- list view model ---------------------------------------------------------
  // Search matches the key alias or any model on its allowlist, so an operator
  // can find a key by the model it was scoped to, not just by name. The
  // summaries are computed over the FULL set (not the filtered view) so the
  // counts always describe the whole account, whatever the current search.
  const allKeys = query.data?.keys ?? [];
  const needle = search.trim().toLowerCase();
  const searchedKeys = needle
    ? allKeys.filter(
        (k) =>
          k.alias.toLowerCase().includes(needle) ||
          k.models.some((m) => m.toLowerCase().includes(needle)),
      )
    : allKeys;
  // Search narrows, then the filter chip narrows further. Sorting is stable and
  // applied last, so the card list below `md` and the table above it can never
  // disagree about order.
  const visibleKeys = [...searchedKeys]
    .filter((k) => matchesFilter(k, filter))
    .sort((a, b) => compareKeys(a, b, sortKey, sortDir) || a.alias.localeCompare(b.alias));

  function onSort(k: KeySort) {
    if (k === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(k);
      // Names read best A→Z; every measure is most useful highest-first.
      setSortDir(k === "name" ? "asc" : "desc");
    }
  }

  const activeCount = allKeys.filter((k) => !k.disabled).length;
  const totalSpend = allKeys.reduce((s, k) => s + (k.spend_to_date || 0), 0);
  const nearCap = allKeys.filter(isNearCap).length;

  // `aria-sort` lives on the <th>, so mark the active column by its index.
  const headSort = {
    [SORT_COLUMN_INDEX[sortKey]]: sortDir === "asc" ? ("ascending" as const) : ("descending" as const),
  };

  return (
    <div>
      <PageHeader
        title="Virtual Keys"
        subtitle="Client credentials callers authenticate with — per-key budgets, rate limits, and model access."
        right={
          <Button onClick={openCreate}>
            <Plus size={14} /> New key
          </Button>
        }
      />

      {pageError && (
        <div className="mb-3">
          <ErrorText>{pageError}</ErrorText>
        </div>
      )}

      {/* Account-level summary — the shared StatCard (accent glow + icon) so
          these read as the same "live surface" language the Dashboard uses,
          rather than a flat one-off box. */}
      {query.data && query.data.keys.length > 0 && (
        <div className="mb-3 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <StatCard
            icon={KeyRound}
            label="Keys"
            value={fmtInt(query.data.keys.length)}
            numeric={query.data.keys.length}
            format={fmtInt}
            sub={`${activeCount} active`}
          />
          <StatCard
            icon={ShieldCheck}
            label="Active"
            value={fmtInt(activeCount)}
            numeric={activeCount}
            format={fmtInt}
            tone="success"
            sub="in rotation"
          />
          <StatCard
            icon={Wallet}
            label="Spend to date"
            value={fmtUsd(totalSpend)}
            numeric={totalSpend}
            format={fmtUsd}
            tone="brand"
          />
          <StatCard
            icon={AlertTriangle}
            label="Near cap"
            value={fmtInt(nearCap)}
            numeric={nearCap}
            format={fmtInt}
            tone={nearCap > 0 ? "warning" : "default"}
            sub={nearCap > 0 ? "≥80% of budget" : "all clear"}
          />
        </div>
      )}

      <Card>
        {query.isLoading && (
          /* Skeleton rows rather than a lone spinner, so the first paint has
             the shape of the list that is about to arrive. */
          <div className="space-y-2.5 p-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="admin-skeleton h-[74px] w-full rounded-xl" />
            ))}
          </div>
        )}
        {query.error && (
          <div className="p-4">
            <ErrorText>{query.error.message}</ErrorText>
          </div>
        )}
        {query.data && query.data.keys.length > 0 && (
          <div className="space-y-2.5 border-b border-[var(--admin-border)] px-4 py-3">
            <div className="flex items-center gap-3">
              <label className="relative min-w-0 flex-1">
                <span className="sr-only">Search keys by name or model</span>
                <Search
                  size={14}
                  aria-hidden
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
                />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search by name or model…"
                  className="pl-9 pr-11"
                />
                {search && (
                  <button
                    type="button"
                    aria-label="Clear search"
                    onClick={() => setSearch("")}
                    className="absolute right-0.5 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-md text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/30"
                  >
                    <X size={13} />
                  </button>
                )}
              </label>
              {/* The sortable headers live inside the table, which is hidden
                  below md — so the cards need the same control up here. */}
              <Select
                className="md:hidden"
                value={sortKey}
                onChange={(v) => setSortKey(v as KeySort)}
                options={SORT_OPTIONS}
              />
              <span className="hidden shrink-0 font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)] sm:block">
                {needle || filter !== "all"
                  ? `${visibleKeys.length}/${allKeys.length}`
                  : `${allKeys.length}`}
              </span>
            </div>
            <div className="-mx-1 flex flex-wrap gap-2 px-1">
              {FILTERS.map((f) => (
                <Pill
                  key={f.id}
                  label={f.label}
                  icon={f.icon}
                  active={filter === f.id}
                  count={allKeys.filter((k) => matchesFilter(k, f.id)).length}
                  onClick={() => setFilter(f.id)}
                />
              ))}
            </div>
          </div>
        )}
        {query.data &&
          (query.data.keys.length === 0 ? (
            /* The zero state should offer the action it is describing. */
            <div className="admin-empty-state flex flex-col items-center gap-3 px-4 py-12 text-center">
              <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white/[0.04]">
                <KeyRound size={18} className="text-[var(--admin-text-dim)]" />
              </span>
              <div>
                <p className="text-[13.5px] font-medium text-[var(--admin-text)]">
                  No virtual keys yet
                </p>
                <p className="mt-1 text-[12px] text-[var(--admin-text-dim)]">
                  Issue a client credential to give an app its own budget, rate limits and
                  model access.
                </p>
              </div>
              <Button onClick={openCreate}>
                <Plus size={14} /> New key
              </Button>
            </div>
          ) : visibleKeys.length === 0 ? (
            <EmptyState>
              No keys match.{" "}
              <button
                type="button"
                onClick={() => {
                  setSearch("");
                  setFilter("all");
                }}
                className="font-medium text-[var(--admin-accent)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:underline"
              >
                Clear search and filters
              </button>
            </EmptyState>
          ) : (
            <>
              <div className="hidden md:block">
                <Table
                  head={[
                    <SortHeader
                      key="name"
                      label="Name"
                      k="name"
                      active={sortKey}
                      dir={sortDir}
                      onSort={onSort}
                    />,
                    "Status",
                    "Models",
                    <SortHeader
                      key="budget"
                      label="Budget"
                      k="budget"
                      active={sortKey}
                      dir={sortDir}
                      onSort={onSort}
                    />,
                    <SortHeader
                      key="rpm"
                      label="RPM"
                      k="rpm"
                      active={sortKey}
                      dir={sortDir}
                      onSort={onSort}
                    />,
                    "TPM",
                    <SortHeader
                      key="expires"
                      label="Expires"
                      k="expires"
                      active={sortKey}
                      dir={sortDir}
                      onSort={onSort}
                    />,
                    "",
                  ]}
                  headSort={headSort}
                >
                  {visibleKeys.map((k) => (
                    <KeyRow key={k.id} k={k} onEdit={openEdit} onError={setPageError} />
                  ))}
                </Table>
              </div>
              <div className="space-y-2.5 p-2.5 md:hidden">
                {visibleKeys.map((k) => (
                  <KeyCard key={k.id} k={k} onEdit={openEdit} onError={setPageError} />
                ))}
              </div>
            </>
          ))}
      </Card>

      {/* -- create / reveal-once ------------------------------------------- */}
      <Dialog
        open={createOpen}
        wide
        contained
        icon={created ? Check : KeyRound}
        title={created ? "Key created" : "New virtual key"}
        subtitle={
          created
            ? `${created.name || "Key"} is live — copy the plaintext now, it's never shown again.`
            : "Issue a client credential. Every limit is optional and editable later."
        }
        onClose={closeCreate}
      >
        {created ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="-mr-1 min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
              {/* Reveal-once secret panel — elevated neutral surface so the
                  plaintext reads as the single focal value, no color, no
                  transparency. */}
              <div className="rounded-2xl border border-[#232323] bg-[#141414] p-4">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#1a1a1a]">
                    <Check className="h-4.5 w-4.5 text-emerald-400" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-[14px] font-semibold text-[var(--admin-text)]">
                      {created.name || "Key"} created
                    </p>
                    <p className="mt-0.5 text-[12px] text-[var(--admin-text-muted)]">
                      Copy it now — the plaintext is never shown again.
                    </p>
                  </div>
                </div>
                <div className="mt-3.5">
                  <p className="admin-label mb-1.5">Secret key</p>
                  <p className="break-all font-mono text-[15px] leading-relaxed tracking-wide text-[var(--admin-text)]">
                    {created.key}
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-2.5 rounded-lg border border-amber-500/30 bg-[#1a1408] px-3.5 py-3">
                <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400" />
                <p className="text-[12px] leading-relaxed text-amber-200">
                  This is the only time the plaintext is shown. wiwi stores a SHA-256 hash — keep
                  the key in your secret manager.
                </p>
              </div>

              <div className="rounded-lg border border-[var(--admin-border)] bg-[#0c0c0c] px-3.5 py-3">
                <p className="admin-label mb-1.5">Authenticate with</p>
                <p className="break-all font-mono text-[12px] text-[var(--admin-text-muted)]">
                  Authorization: Bearer <span className="text-[var(--admin-text)]">{created.key}</span>
                </p>
                <p className="mt-1.5 text-[11px] text-[var(--admin-text-dim)]">
                  Works on /v1/chat/completions, /v1/responses and /v1/messages (x-api-key is also
                  accepted on the Anthropic surface).
                </p>
              </div>
            </div>

            <div className="mt-5 flex flex-col-reverse gap-2.5 border-t border-[#171717] pt-4 sm:flex-row sm:items-center sm:justify-end">
              <CopyButton text={created.key} />
              <Button
                className="min-w-0 sm:w-auto"
                onClick={() => {
                  closeCreate();
                  void qc.invalidateQueries({ queryKey: ["keys"] });
                }}
              >
                Done
              </Button>
            </div>
          </div>
        ) : (
          <form
            className="flex min-h-0 flex-1 flex-col"
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim() || !numsOk || customTooShort) return;
              setCreateError(null);
              const modelList = parseCsv(modelsCsv);
              create.mutate({
                name: name.trim(),
                custom_key: authMode === "custom" ? customKey.trim() : undefined,
                ...(modelList.length > 0 ? { models: modelList } : {}),
                max_budget: budgetN ?? undefined,
                rpm: rpmN ?? undefined,
                tpm: tpmN ?? undefined,
                ttl_seconds: ttlHN != null ? Math.round(ttlHN * 3600) : undefined,
              });
            }}
          >
            <div className="-mr-1 min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
              <p className="flex items-start gap-2.5 rounded-lg border border-[var(--admin-border)] bg-[#0c0c0c] px-3.5 py-3 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                <ShieldCheck size={14} className="mt-0.5 shrink-0 text-[var(--admin-text-dim)]" />
                <span>
                  Limits are optional — leave any of them empty for unrestricted access. Every limit
                  can be changed later from the key&apos;s Edit action.
                </span>
              </p>

              <FormSection index="01" icon={KeyRound} title="Identity" desc="Name this key and choose how it's generated.">
                <div>
                  <Field label="Name" hint="A label for where this key will be used.">
                    <Input
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      onBlur={() => setNameTouched(true)}
                      placeholder="ci-pipeline"
                      autoFocus
                    />
                  </Field>
                  {nameTouched && !name.trim() && <FieldError>Name is required</FieldError>}
                </div>
                <div>
                  <span className="admin-label mb-1.5 block">Key source</span>
                  <div className="flex flex-col gap-3 sm:flex-row">
                    <KeySourceCard
                      active={authMode === "random"}
                      icon={Sparkles}
                      title="Generate random"
                      desc="Wiwi creates a strong key."
                      onClick={() => setAuthMode("random")}
                    />
                    <KeySourceCard
                      active={authMode === "custom"}
                      icon={Upload}
                      title="Bring your own"
                      desc="Use an existing secret."
                      onClick={() => setAuthMode("custom")}
                    />
                  </div>
                </div>
                {authMode === "custom" && (
                  <div>
                    <Field label="Custom key" hint="At least 16 characters; stored hashed.">
                      <Input
                        value={customKey}
                        onChange={(e) => setCustomKey(e.target.value)}
                        placeholder="sk-my-own-value…"
                        className="font-mono"
                      />
                    </Field>
                    {customKey.trim().length > 0 && customTooShort && (
                      <FieldError>Custom keys must be at least 16 characters</FieldError>
                    )}
                  </div>
                )}
              </FormSection>

              <FormSection
                index="02"
                icon={ShieldCheck}
                title="Access & limits"
                desc="Constrain which models this key can reach and how much it can spend."
              >
                <div>
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="admin-label">Model allowlist</span>
                    {models.length === 0 && (
                      <span className="text-[11px] text-[var(--admin-text-dim)]">empty = all models</span>
                    )}
                  </div>
                  <ModelChips value={modelsCsv} onChange={setModelsCsv} />
                </div>

                <div>
                  <span className="admin-label mb-1.5 block">Quick presets</span>
                  <div className="flex flex-wrap gap-2">
                    {LIMIT_PRESETS.map((p) => (
                      <Pill
                        key={p.id}
                        label={p.label}
                        icon={Zap}
                        active={activePreset?.id === p.id}
                        onClick={() => {
                          setBudget(p.values.budget);
                          setRpm(p.values.rpm);
                          setTpm(p.values.tpm);
                          setTtlHours(p.values.ttl);
                          setExpiryCustom(false);
                        }}
                      />
                    ))}
                  </div>
                </div>

                <div className="grid gap-3 sm:grid-cols-3">
                  <div>
                    <Field label="Budget" hint="Lifetime spend cap; empty = unlimited.">
                      <NumberInput
                        min={0}
                        step="any"
                        value={budget}
                        onChange={setBudget}
                        placeholder="25"
                        suffix="USD"
                      />
                    </Field>
                    {isBad(budgetN) && <FieldError>Not a number</FieldError>}
                  </div>
                  <div>
                    <Field label="RPM" hint="Requests/min; empty = unlimited.">
                      <NumberInput
                        min={0}
                        value={rpm}
                        onChange={setRpm}
                        placeholder="60"
                      />
                    </Field>
                    {isBad(rpmN) && <FieldError>Not a number</FieldError>}
                  </div>
                  <div>
                    <Field label="TPM" hint="Tokens/min; empty = unlimited.">
                      <NumberInput
                        min={0}
                        value={tpm}
                        onChange={setTpm}
                        placeholder="100000"
                      />
                    </Field>
                    {isBad(tpmN) && <FieldError>Not a number</FieldError>}
                  </div>
                </div>
              </FormSection>

              <FormSection index="03" icon={Clock} title="Lifetime" desc="When this key stops working.">
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Expires in" hint="Pick a duration or set a custom one.">
                    <Select
                      value={expiryPicked}
                      onChange={pickExpiry}
                      options={EXPIRY_OPTIONS}
                    />
                  </Field>
                  {expiryPicked === "custom" && (
                    <div>
                      <Field label="Custom hours" hint="Hours from now; empty = never expires.">
                        <NumberInput
                          min={0}
                          step="any"
                          value={ttlHours}
                          onChange={setTtlHours}
                          placeholder="720"
                          suffix="hrs"
                        />
                      </Field>
                      {isBad(ttlHN) && <FieldError>Not a number</FieldError>}
                    </div>
                  )}
                </div>
              </FormSection>

              {createError && <ErrorText>{createError}</ErrorText>}
            </div>

            {/* Sticky summary + actions. Kept OUT of the scrolling body so the
                live recap and the primary CTA are always reachable, whatever
                the form height — on a short phone the action bar never scrolls
                out from under the user's thumb. */}
            <div className="mt-5 shrink-0 border-t border-[#171717] pt-4">
              {/* Live recap — every picked limit reads at a glance so the user
                  can commit without scrolling back up to check each field. */}
              <div className="mb-3.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[var(--admin-text-dim)]">
                <span className="admin-label">Summary</span>
                <span className="font-mono tabular-nums text-[var(--admin-text-muted)]">
                  {models.length > 0 ? `${models.length} model${models.length > 1 ? "s" : ""}` : "all models"}
                </span>
                <span aria-hidden>·</span>
                <span className="font-mono tabular-nums text-[var(--admin-text-muted)]">
                  {budget.trim() ? `$${budget.trim()} budget` : "unlimited budget"}
                </span>
                <span aria-hidden>·</span>
                <span className="font-mono tabular-nums text-[var(--admin-text-muted)]">
                  {compactNum(rpm)} rpm
                </span>
                <span aria-hidden>·</span>
                <span className="font-mono tabular-nums text-[var(--admin-text-muted)]">
                  {compactNum(tpm)} tpm
                </span>
                <span aria-hidden>·</span>
                <span className="font-mono tabular-nums text-[var(--admin-text-muted)]">
                  expires {humanTtl(ttlHours)}
                </span>
              </div>

              {/* Readiness: the CTA explains *why* it is disabled inline, rather
                  than only on hover — hover-only feedback is invisible on touch. */}
              {!canCreate && blockedWhy && (
                <p className="mb-2.5 flex items-center gap-1.5 text-[11px] text-amber-400">
                  <AlertTriangle size={12} className="shrink-0" />
                  {blockedWhy}
                </p>
              )}

              <div className="flex flex-col-reverse gap-2.5 sm:flex-row sm:items-center sm:justify-between">
                <p className="hidden text-[11px] text-[var(--admin-text-dim)] sm:block">
                  The plaintext key is shown once, after creation.
                </p>
                <div className="flex flex-col-reverse gap-2.5 sm:flex-row">
                  <Button variant="ghost" type="button" onClick={closeCreate}>
                    Cancel
                  </Button>
                  {/* Neutral primary CTA — solid white button, matching the
                      dialog's no-color, no-transparency treatment. */}
                  <Button
                    type="submit"
                    disabled={!canCreate}
                    title={blockedWhy ?? undefined}
                    className="border-[#e5e5e5] bg-white text-black hover:bg-[#e5e5e5]"
                  >
                    {create.isPending ? (
                      <>
                        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                        Creating…
                      </>
                    ) : (
                      <>
                        <Sparkles size={14} /> Create key
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </div>
          </form>
        )}
      </Dialog>

      {/* -- edit ------------------------------------------------------------ */}
      <Dialog
        open={editTarget != null}
        contained
        wide
        icon={ShieldCheck}
        title={editTarget ? `Edit ${editTarget.alias}` : "Edit key"}
        subtitle="Every field is left as-is unless you change it here."
        onClose={closeEdit}
        footer={
          <>
            <Button variant="ghost" type="button" onClick={closeEdit}>
              Cancel
            </Button>
            {/* `form=` binds this footer button to the scrolling form above it,
                so the primary action can live in the dialog's own footer
                instead of trailing the fields. */}
            <Button type="submit" form="edit-key-form" disabled={!canSaveEdit} title={editBlockedWhy ?? undefined}>
              {editSave.isPending ? "Saving…" : "Save changes"}
            </Button>
          </>
        }
      >
        {editTarget && (
          <form
            id="edit-key-form"
            className="flex min-h-0 flex-1 flex-col"
            onSubmit={(e) => {
              e.preventDefault();
              if (!canSaveEdit) return;
              setEditError(null);
              const patch: Parameters<typeof patchKey>[1] = {};
              // Empty input = leave unchanged (field omitted); a value overwrites;
              // clearing happens only via the explicit checkboxes/nulls below.
              if (clearBudget) patch.max_budget = null;
              else if (editBudgetN != null) patch.max_budget = editBudgetN;
              if (clearRpm) patch.rpm = null;
              else if (editRpmN != null) patch.rpm = editRpmN;
              if (clearTpm) patch.tpm = null;
              else if (editTpmN != null) patch.tpm = editTpmN;
              // The allowlist follows the same rule as the numeric fields: a
              // non-empty list overwrites, an emptied input means "leave
              // unchanged" and only the checkbox sends the all-models [].
              if (clearModels) patch.models = [];
              else if (editModels.trim()) patch.models = parseCsv(editModels);
              if (clearExpiry) patch.expires_at = null;
              // `patchKey` takes an absolute timestamp, so a chosen duration is
              // resolved to one here. An untouched expiry field is "" and sends
              // nothing — opening the dialog must never reschedule a key.
              else if (editExpiryPicked !== "" && editHoursN != null) {
                patch.expires_at = Math.round(Date.now() / 1000 + editHoursN * 3600);
              }
              editSave.mutate({ id: editTarget.id, patch });
            }}
          >
            <div className="-mr-1 min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
              <FormSection
                index="01"
                icon={ShieldCheck}
                title="Access & limits"
                desc="Empty = leave unchanged. Tick a box to clear it."
              >
                <div className="grid gap-3 sm:grid-cols-3">
                  <div>
                    <Field
                      label="Budget"
                      hint={`Currently ${
                        editTarget.max_budget != null
                          ? fmtUsd(editTarget.max_budget)
                          : "unlimited"
                      }.`}
                    >
                      <NumberInput
                        min={0}
                        step="any"
                        value={editBudget}
                        onChange={setEditBudget}
                        placeholder="unchanged"
                        suffix="USD"
                      />
                    </Field>
                    {isBad(editBudgetN) && <FieldError>Not a number</FieldError>}
                  </div>
                  <div>
                    <Field label="RPM" hint="Requests per minute.">
                      <NumberInput
                        min={0}
                        value={editRpm}
                        onChange={setEditRpm}
                        placeholder="unchanged"
                      />
                    </Field>
                    {isBad(editRpmN) && <FieldError>Not a number</FieldError>}
                  </div>
                  <div>
                    <Field label="TPM" hint="Tokens per minute.">
                      <NumberInput
                        min={0}
                        value={editTpm}
                        onChange={setEditTpm}
                        placeholder="unchanged"
                      />
                    </Field>
                    {isBad(editTpmN) && <FieldError>Not a number</FieldError>}
                  </div>
                </div>
                <div className="flex flex-col gap-1 rounded-lg border border-[var(--admin-border)] bg-[#0a0a0a] p-1">
                  <ClearCheck
                    label="Clear budget → unlimited"
                    checked={clearBudget}
                    onChange={setClearBudget}
                  />
                  <ClearCheck label="Clear RPM → unlimited" checked={clearRpm} onChange={setClearRpm} />
                  <ClearCheck label="Clear TPM → unlimited" checked={clearTpm} onChange={setClearTpm} />
                </div>
              </FormSection>

              <FormSection
                index="02"
                icon={Layers}
                title="Model access"
                desc="Which model groups this key can reach."
              >
                <div>
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="admin-label">Model allowlist</span>
                    {parseCsv(editModels).length === 0 && (
                      <span className="text-[11px] text-[var(--admin-text-dim)]">
                        empty = leave unchanged
                      </span>
                    )}
                  </div>
                  <ModelChips value={editModels} onChange={setEditModels} />
                </div>
                <div className="flex flex-col gap-1 rounded-lg border border-[var(--admin-border)] bg-[#0a0a0a] p-1">
                  <ClearCheck
                    label="Clear allowlist → all models"
                    checked={clearModels}
                    onChange={setClearModels}
                  />
                </div>
              </FormSection>

              <FormSection index="03" icon={Clock} title="Lifetime" desc="When this key stops working.">
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field
                    label="Expires in"
                    hint={
                      editTarget.expires_at != null
                        ? `Currently ${fmtDateTime(editTarget.expires_at)}. Leave as-is to keep it.`
                        : "This key does not expire."
                    }
                  >
                    <Select
                      value={editExpiryPicked}
                      onChange={pickEditExpiry}
                      options={EXPIRY_OPTIONS}
                      className="w-full"
                    />
                  </Field>
                  {editExpiryPicked === "custom" && (
                    <div>
                      <Field label="Custom hours" hint="Counted from now.">
                        <NumberInput
                          min={0}
                          step="any"
                          value={editHours}
                          onChange={setEditHours}
                          placeholder="720"
                          suffix="hrs"
                        />
                      </Field>
                      {isBad(editHoursN) && <FieldError>Not a number</FieldError>}
                    </div>
                  )}
                </div>
                <div className="flex flex-col gap-1 rounded-lg border border-[var(--admin-border)] bg-[#0a0a0a] p-1">
                  <ClearCheck
                    label="Clear expiry → never expires"
                    checked={clearExpiry}
                    onChange={setClearExpiry}
                  />
                </div>
              </FormSection>

              {editError && <ErrorText>{editError}</ErrorText>}
            </div>
          </form>
        )}
      </Dialog>
    </div>
  );
}
