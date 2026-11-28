// VirtualKeys page — issue and manage client credentials: budgets, rate limits,
// model allowlists, expiry. Generated plaintext keys are revealed exactly once.
//
// Layout: one summary strip, one toolbar, one list of rows. A row is a single
// responsive grid (stacked below md, four tracks from md) — there is no separate
// table and card rendering to keep in sync. Create and edit share one dialog.

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronUp,
  KeyRound,
  MoreHorizontal,
  Plus,
  X,
} from "lucide-react";
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
} from "@/components/ui";
import { SearchInput, useNow } from "@/pages/logs-shared";
import { fmtDateTime, fmtInt, fmtUsd } from "@/lib/format";

/* ------------------------------------------------------------------ helpers */

/** "" → null (leave unchanged / unlimited); unparsable → NaN; otherwise the number. */
function tryParse(s: string): number | null {
  const t = s.trim();
  if (!t) return null;
  return Number.isFinite(Number(t)) ? Number(t) : NaN;
}

const isBad = (n: number | null) => n !== null && Number.isNaN(n);

/** "a, b,,c" → ["a","b","c"]; "" → [] (= all models). */
function parseCsv(s: string): string[] {
  return s
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

type Status = "active" | "expired" | "disabled";

function keyStatus(k: VirtualKey): Status {
  if (k.disabled) return "disabled";
  if (k.expires_at != null && k.expires_at * 1000 < Date.now()) return "expired";
  return "active";
}

const STATUS_DOT: Record<Status, string> = {
  active: "bg-emerald-400",
  expired: "bg-amber-400",
  disabled: "bg-zinc-500",
};

const STATUS_RAIL: Record<Status, string> = {
  active: "before:bg-emerald-400/70",
  expired: "before:bg-amber-400/80",
  disabled: "before:bg-zinc-600",
};

/** Fraction of the budget cap already spent, or null when the key has no cap. */
function budgetUsed(k: VirtualKey): number | null {
  if (k.max_budget == null || k.max_budget <= 0) return null;
  return k.spend_to_date / k.max_budget;
}

const NEAR_CAP = 0.8;
const isNearCap = (k: VirtualKey) => (budgetUsed(k) ?? 0) >= NEAR_CAP;

/** "in 3d" / "2d ago". The exact date is always available as the title. */
function fmtExpiryRelative(tsSec: number, nowMs: number): string {
  const s = (tsSec * 1000 - nowMs) / 1000;
  const a = Math.abs(s);
  const unit =
    a < 3600 ? `${Math.floor(a / 60)}m` : a < 86400 ? `${Math.floor(a / 3600)}h` : `${Math.floor(a / 86400)}d`;
  return s >= 0 ? `in ${unit}` : `${unit} ago`;
}

const EXPIRY_WARN_MS = 3 * 86400 * 1000;
const EXPIRY_SOON_MS = 7 * 86400 * 1000;

/** Stable short handle for a key id. Duplicate aliases are legal, so the row
 *  carries the id fragment; the full value lives in the tooltip and detail row. */
const shortId = (id: string) => (id.length > 10 ? `${id.slice(0, 10)}…` : id);

/** A still-live key inside the seven-day rotation window. */
function expiresSoon(k: VirtualKey, nowMs: number): boolean {
  return (
    !k.disabled &&
    k.expires_at != null &&
    k.expires_at * 1000 > nowMs &&
    k.expires_at * 1000 - nowMs <= EXPIRY_SOON_MS
  );
}

const STATUS_TEXT: Record<Status, string> = {
  active: "text-emerald-400/80",
  expired: "text-amber-400/90",
  disabled: "text-zinc-500",
};

/* ------------------------------------------------------------ filter + sort */

type Filter = "all" | "active" | "near-cap" | "expired" | "disabled";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "near-cap", label: "Near cap" },
  { id: "expired", label: "Expired" },
  { id: "disabled", label: "Disabled" },
];

function matchesFilter(k: VirtualKey, f: Filter): boolean {
  if (f === "all") return true;
  if (f === "near-cap") return isNearCap(k);
  return keyStatus(k) === f;
}

type Sort = "name" | "budget" | "rpm" | "expires";

const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: "name", label: "Name" },
  { value: "budget", label: "Budget use" },
  { value: "rpm", label: "RPM" },
  { value: "expires", label: "Expiry" },
];

function sortValue(k: VirtualKey, s: Sort): number | string {
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

function compareKeys(a: VirtualKey, b: VirtualKey, s: Sort, dir: "asc" | "desc"): number {
  const av = sortValue(a, s);
  const bv = sortValue(b, s);
  const cmp =
    typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
  return dir === "asc" ? cmp : -cmp;
}

/* ------------------------------------------------------------------ summary */

/** One strip instead of four tiles: the account at a glance, divided by hairlines. */
function SummaryStrip(props: { keys: VirtualKey[]; nowMs: number }) {
  const { keys, nowMs } = props;
  const active = keys.filter((k) => keyStatus(k) === "active").length;
  const spend = keys.reduce((s, k) => s + (k.spend_to_date || 0), 0);
  const capped = keys.filter((k) => k.max_budget != null && k.max_budget > 0);
  const caps = capped.reduce((s, k) => s + (k.max_budget ?? 0), 0);
  const near = keys.filter(isNearCap).length;
  const soon = keys.filter((k) => expiresSoon(k, nowMs)).length;
  const never = keys.filter((k) => k.expires_at == null).length;
  // Who is spending: the four biggest keys as shares of total spend, the rest pooled.
  const ranked = [...keys].filter((k) => k.spend_to_date > 0).sort((a, b) => b.spend_to_date - a.spend_to_date);
  const top = ranked.slice(0, 4);
  const other = spend - top.reduce((s, k) => s + k.spend_to_date, 0);
  const SHADE = ["bg-[var(--admin-accent)]", "bg-[var(--admin-accent)]/70", "bg-[var(--admin-accent)]/45", "bg-[var(--admin-accent)]/25"];
  const side: { label: string; value: string; sub: string; warn?: boolean }[] = [
    { label: "Active", value: `${fmtInt(active)}/${fmtInt(keys.length)}`, sub: "keys in service" },
    { label: "Near cap", value: fmtInt(near), sub: near > 0 ? "at 80% or more" : "all within budget", warn: near > 0 },
    {
      label: "Expiring ≤7d",
      value: fmtInt(soon),
      sub: soon > 0 ? "rotate before they lapse" : "none this week",
      warn: soon > 0,
    },
    { label: "No expiry", value: fmtInt(never), sub: never > 0 ? "live until revoked" : "all expire" },
  ];
  return (
    <section
      aria-label="Key summary"
      className="mb-4 grid overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]"
    >
      <div className="min-w-0 p-5">
        <div className="flex items-center justify-between gap-3">
          <p className="admin-label">Lifetime spend</p>
          <span className="font-mono text-[10.5px] tabular-nums text-[var(--admin-text-dim)]">
            {fmtInt(keys.length)} keys
          </span>
        </div>
        <p className="mt-2 flex flex-wrap items-baseline gap-x-3 font-mono tabular-nums">
          <span className="text-[2rem] font-semibold leading-none tracking-tight text-[var(--admin-text)]">
            {fmtUsd(spend)}
          </span>
          <span className="text-[12px] text-[var(--admin-text-dim)]">
            {caps > 0
              ? `of ${fmtUsd(caps)} across ${fmtInt(capped.length)} capped ${capped.length === 1 ? "key" : "keys"}`
              : "no caps set"}
          </span>
        </p>
        {spend > 0 ? (
          <>
            <div
              role="img"
              aria-label={`Spend split: ${top.map((k) => `${k.alias} ${fmtUsd(k.spend_to_date)}`).join(", ")}`}
              className="mt-4 flex h-2 gap-0.5 overflow-hidden rounded-full"
            >
              {top.map((k, i) => (
                <span key={k.id} className={SHADE[i]} style={{ flexGrow: k.spend_to_date, flexBasis: 0 }} />
              ))}
              {other > 0 && <span className="bg-white/15" style={{ flexGrow: other, flexBasis: 0 }} />}
            </div>
            <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[11.5px] text-[var(--admin-text-muted)]">
              {top.map((k, i) => (
                <li key={k.id} className="flex min-w-0 items-center gap-1.5">
                  <span aria-hidden className={`h-2 w-2 shrink-0 rounded-sm ${SHADE[i]}`} />
                  <span className="max-w-[9rem] truncate">{k.alias}</span>
                  <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
                    {Math.round((k.spend_to_date / spend) * 100)}%
                  </span>
                </li>
              ))}
              {other > 0 && (
                <li className="flex items-center gap-1.5">
                  <span aria-hidden className="h-2 w-2 shrink-0 rounded-sm bg-white/15" />
                  Others
                </li>
              )}
            </ul>
          </>
        ) : (
          <p className="mt-4 text-[12px] text-[var(--admin-text-dim)]">No spend recorded yet.</p>
        )}
      </div>
      <dl className="grid grid-cols-2 border-t border-[var(--admin-border)] lg:grid-cols-1 lg:border-l lg:border-t-0">
        {side.map((it, i) => (
          <div
            key={it.label}
            className={`flex min-w-0 flex-col justify-center border-[var(--admin-border)] px-4 py-3 lg:flex-row lg:items-baseline lg:justify-between lg:gap-3 ${
              i % 2 === 1 ? "border-l" : ""
            } ${i >= 2 ? "border-t" : ""} ${i > 0 ? "lg:border-t" : "lg:border-t-0"} lg:border-l-0`}
          >
            <div className="min-w-0">
              <dt className="admin-label">{it.label}</dt>
              <dd className="mt-1 hidden truncate text-[11px] text-[var(--admin-text-dim)] sm:block">{it.sub}</dd>
            </div>
            <dd
              className={`mt-1 font-mono text-[20px] font-semibold tabular-nums leading-none lg:mt-0 ${
                it.warn ? "text-amber-400" : "text-[var(--admin-text)]"
              }`}
            >
              {it.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/* --------------------------------------------------------------------- rows */

function useKeyActions(k: VirtualKey, onError: (m: string) => void) {
  const qc = useQueryClient();
  const [armed, setArmed] = useState(false);
  // An abandoned "Revoke" disarms itself so a later stray click cannot fire it.
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 6000);
    return () => clearTimeout(t);
  }, [armed]);
  const toggle = useMutation({
    mutationFn: () => disableKey(k.id, !k.disabled),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["keys"] }),
    onError: (e) => onError(e.message),
  });
  const revoke = useMutation({
    mutationFn: () => deleteKey(k.id),
    onSuccess: () => {
      setArmed(false);
      void qc.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (e) => onError(e.message),
  });
  return { armed, setArmed, toggle, revoke };
}

function Meter(props: { k: VirtualKey }) {
  const { k } = props;
  const used = budgetUsed(k);
  const pct = used != null ? Math.round(used * 100) : null;
  const tone = used != null && used >= 1 ? "text-red-400" : used != null && used >= NEAR_CAP ? "text-amber-400" : "";
  const held = k.budget_reserved ?? 0;
  return (
    <div className="min-w-0">
      {used != null ? (
        <ProgressBar value={used} />
      ) : (
        <div aria-hidden className="h-1.5 rounded-full border border-dashed border-[var(--admin-border)]" />
      )}
      <p className="mt-1.5 flex items-baseline gap-2 font-mono text-[12px] tabular-nums">
        <span className={`min-w-0 truncate ${tone || "text-[var(--admin-text)]"}`}>
          {fmtUsd(k.spend_to_date)}
          {k.max_budget != null && (
            <span className="text-[var(--admin-text-dim)]"> / {fmtUsd(k.max_budget)}</span>
          )}
        </span>
        <span className="ml-auto shrink-0 text-[10.5px] text-[var(--admin-text-dim)]">
          {pct != null ? `${pct}%` : "no cap"}
        </span>
      </p>
      {held > 0 && (
        <p className="mt-0.5 font-mono text-[10.5px] tabular-nums text-[var(--admin-text-dim)]">
          {fmtUsd(held)} held for in-flight requests
        </p>
      )}
    </div>
  );
}

function KeyRow(props: {
  k: VirtualKey;
  nowMs: number;
  onEdit: (k: VirtualKey) => void;
  onError: (m: string) => void;
}) {
  const { k, nowMs } = props;
  const status = keyStatus(k);
  const [open, setOpen] = useState(false);
  const { armed, setArmed, toggle, revoke } = useKeyActions(k, props.onError);
  const soon = k.expires_at != null && !k.disabled && k.expires_at * 1000 - nowMs <= EXPIRY_WARN_MS;
  const shown = k.models.slice(0, 3);
  const rest = k.models.length - shown.length;
  const panelId = `key-actions-${k.id}`;

  return (
    <li
      className={`relative px-4 py-4 pl-5 transition-colors hover:bg-white/[0.015] before:absolute before:inset-y-3 before:left-0 before:w-0.5 before:rounded-r-full before:content-[''] ${STATUS_RAIL[status]} ${
        status === "disabled" ? "opacity-70" : ""
      }`}
    >
      <div className="grid items-center gap-x-6 gap-y-3 md:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,0.9fr)_auto]">
        {/* identity */}
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[status]}`} />
            <h3 className="min-w-0 truncate text-[13.5px] font-medium text-[var(--admin-text)]">{k.alias}</h3>
            <span
              className={`shrink-0 text-[10px] font-semibold uppercase tracking-[0.08em] ${STATUS_TEXT[status]}`}
            >
              {status}
            </span>
            <span
              className="ml-auto hidden shrink-0 font-mono text-[10px] text-[var(--admin-text-dim)] sm:inline"
              title={k.id}
            >
              {shortId(k.id)}
            </span>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {k.models.length === 0 ? (
              <span className="text-[11.5px] text-[var(--admin-text-dim)]">all models</span>
            ) : (
              <>
                {shown.map((m) => (
                  <Badge key={m} tone="blue">
                    {m}
                  </Badge>
                ))}
                {rest > 0 && (
                  <Badge tone="gray" title={k.models.slice(3).join(", ")}>
                    +{rest}
                  </Badge>
                )}
              </>
            )}
            {k.priority && (
              <Badge tone="violet" title="Priority lane">
                {k.priority}
              </Badge>
            )}
          </div>
        </div>

        {/* budget */}
        <Meter k={k} />

        {/* limits + expiry */}
        <div className="min-w-0 space-y-1 font-mono text-[12px] tabular-nums text-[var(--admin-text-muted)]">
          <p>
            <span className="text-[var(--admin-text)]">{k.rpm != null ? fmtInt(k.rpm) : "∞"}</span> rpm
            <span className="px-1 text-[var(--admin-text-dim)]">·</span>
            <span className="text-[var(--admin-text)]">{k.tpm != null ? fmtInt(k.tpm) : "∞"}</span> tpm
          </p>
          <p
            title={k.expires_at != null ? fmtDateTime(k.expires_at) : undefined}
            className={soon ? "text-amber-400" : "text-[var(--admin-text-dim)]"}
          >
            {k.expires_at != null ? `expires ${fmtExpiryRelative(k.expires_at, nowMs)}` : "never expires"}
          </p>
        </div>

        {/* actions */}
        <div className="flex items-center gap-1.5 md:justify-end">
          <Button variant="outline" className="flex-1 md:flex-none" onClick={() => props.onEdit(k)}>
            Edit
          </Button>
          <Button
            variant="ghost"
            className="px-3"
            aria-expanded={open}
            aria-controls={panelId}
            aria-label={`${open ? "Hide" : "Show"} actions for ${k.alias}`}
            onClick={() => {
              setOpen((v) => !v);
              setArmed(false);
            }}
          >
            {open ? <ChevronUp size={15} aria-hidden /> : <MoreHorizontal size={15} aria-hidden />}
          </Button>
        </div>
      </div>

      {open && (
        <div id={panelId} className="mt-3 border-t border-[var(--admin-border)] pt-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 items-center gap-2">
              <span className="admin-label shrink-0">Key ID</span>
              <code
                className="min-w-0 truncate font-mono text-[11.5px] text-[var(--admin-text-muted)]"
                title={k.id}
              >
                {k.id}
              </code>
              <CopyButton text={k.id} className="shrink-0 px-2.5 py-1 text-[11px]" />
            </div>
            <div className="flex flex-wrap items-center gap-1.5 sm:justify-end">
              <Button variant="outline" disabled={toggle.isPending} onClick={() => toggle.mutate()}>
                {k.disabled ? "Enable key" : "Disable key"}
              </Button>
              {armed ? (
                <>
                  <Button variant="danger" disabled={revoke.isPending} onClick={() => revoke.mutate()}>
                    {revoke.isPending ? "Revoking…" : "Confirm revoke"}
                  </Button>
                  <Button variant="ghost" disabled={revoke.isPending} onClick={() => setArmed(false)}>
                    Cancel
                  </Button>
                </>
              ) : (
                <Button variant="danger" onClick={() => setArmed(true)}>
                  Revoke
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------ toolbar */

/** Desktop column guide for the ledger list. Rows are one responsive grid, so
 *  the labels reuse the same tracks and gutters; below md the grid stacks and
 *  the labels would lie, so they are hidden. */
function KeyListHeader() {
  return (
    <div
      aria-hidden="true"
      className="hidden gap-x-6 border-b border-[var(--admin-border)] px-4 py-2 pl-5 md:grid md:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,0.9fr)_auto] md:items-center"
    >
      <span className="admin-label">Key</span>
      <span className="admin-label">Budget use</span>
      <span className="admin-label">Limits &amp; expiry</span>
      <span className="admin-label text-right">Actions</span>
    </div>
  );
}

function FilterTabs(props: { value: Filter; onChange: (f: Filter) => void; counts: Record<Filter, number> }) {
  return (
    <div role="group" aria-label="Filter keys" className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-0.5">
      {FILTERS.map((f) => {
        const on = props.value === f.id;
        return (
          <button
            key={f.id}
            type="button"
            aria-pressed={on}
            onClick={() => props.onChange(f.id)}
            className={`inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg px-3 text-[12.5px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500 ${
              on
                ? "bg-[#1a1a1a] text-[var(--admin-text)]"
                : "text-[var(--admin-text-muted)] hover:bg-[#141414] hover:text-[var(--admin-text)]"
            }`}
          >
            {f.label}
            <span className="font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
              {props.counts[f.id]}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* --------------------------------------------------------------- form pieces */

function FieldError(props: { children: ReactNode }) {
  return <p className="mt-1 text-[11px] font-medium text-red-400">{props.children}</p>;
}

/** Chip input for the model allowlist: Enter/comma commits, Backspace on an
 *  empty input drops the last chip. Empty list = all models. */
function ModelChips(props: { value: string; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState("");
  const chips = parseCsv(props.value);

  function commit(raw: string) {
    const parts = parseCsv(raw);
    if (parts.length === 0) return;
    props.onChange([...chips, ...parts.filter((p) => !chips.includes(p))].join(", "));
    setDraft("");
  }

  return (
    <div className="rounded-lg border border-white/[0.08] bg-black/40 p-1.5 focus-within:border-indigo-400/40 focus-within:bg-black/55">
      {chips.length > 0 && (
        <ul className="mb-1.5 flex flex-wrap gap-1.5">
          {chips.map((m) => (
            <li
              key={m}
              className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.08] py-1 pl-2 pr-1 font-mono text-[12px] text-[var(--admin-text)]"
            >
              {m}
              <button
                type="button"
                aria-label={`Remove ${m}`}
                onClick={() => props.onChange(chips.filter((c) => c !== m).join(", "))}
                className="-my-2.5 -mr-1.5 flex h-11 w-11 items-center justify-center rounded-md text-[var(--admin-text-dim)] hover:bg-[#232323] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500"
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        value={draft}
        aria-label="Add model"
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
        placeholder={chips.length === 0 ? "model-a, model-b" : "Add model…"}
        className="min-h-9 w-full min-w-0 bg-transparent px-1.5 text-[13px] text-[var(--admin-text)] outline-none placeholder:text-[var(--admin-text-dim)]"
      />
    </div>
  );
}

/** A numeric limit. In edit mode a "Remove limit" toggle sits under the input, so
 *  clearing a limit is explicit. */
function LimitField(props: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  suffix?: string;
  step?: number | "any";
  clear?: { checked: boolean; onChange: (v: boolean) => void };
}) {
  const n = tryParse(props.value);
  return (
    <div>
      <Field label={props.label} hint={props.hint}>
        <NumberInput
          min={0}
          step={props.step}
          value={props.value}
          onChange={props.onChange}
          placeholder={props.placeholder}
          suffix={props.suffix}
        />
      </Field>
      {isBad(n) && <FieldError>Not a number</FieldError>}
      {props.clear && (
        <label className="mt-1 flex min-h-11 cursor-pointer items-center gap-2 text-[12px] text-[var(--admin-text-muted)]">
          <input
            type="checkbox"
            checked={props.clear.checked}
            onChange={(e) => props.clear?.onChange(e.target.checked)}
            className="h-4 w-4 accent-white"
          />
          Remove limit (unlimited)
        </label>
      )}
    </div>
  );
}

const PRESETS: { id: string; label: string; budget: string; rpm: string; tpm: string; ttl: string }[] = [
  { id: "open", label: "Unrestricted", budget: "", rpm: "", tpm: "", ttl: "" },
  { id: "standard", label: "Standard", budget: "25", rpm: "60", tpm: "100000", ttl: "" },
  { id: "strict", label: "Strict", budget: "5", rpm: "20", tpm: "20000", ttl: "24" },
];

const EXPIRY_PRESETS: { value: string; label: string }[] = [
  { value: "24", label: "24 hours" },
  { value: "168", label: "7 days" },
  { value: "720", label: "30 days" },
  { value: "2160", label: "90 days" },
  { value: "custom", label: "Custom…" },
];

/** Labelled group inside the dialog; groups are separated by a hairline. */
function Group(props: { title: string; desc?: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-t border-white/[0.06] pt-5 first:border-t-0 first:pt-0">
      <div>
        <h4 className="admin-label">{props.title}</h4>
        {props.desc && <p className="mt-0.5 text-[11.5px] text-[var(--admin-text-dim)]">{props.desc}</p>}
      </div>
      {props.children}
    </section>
  );
}

/** Segmented single-choice control (radio semantics, 44px tap targets). */
function Segmented<T extends string>(props: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div
      role="radiogroup"
      aria-label={props.label}
      className="inline-flex w-full gap-0.5 rounded-xl border border-white/[0.08] bg-black/40 p-0.5 sm:w-auto"
    >
      {props.options.map((o) => {
        const on = props.value === o.value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => props.onChange(o.value)}
            className={`min-h-11 flex-1 rounded-[10px] px-3.5 text-[12.5px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500 sm:flex-none ${
              on
                ? "bg-white/[0.09] text-[var(--admin-text)] shadow-sm shadow-black/40 ring-1 ring-inset ring-white/[0.10]"
                : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------- dialog */

/** One dialog for both create and edit. Mounted only while open, so its state is
 *  seeded fresh from `target` every time and needs no reset logic. */
function KeyDialog(props: { target: VirtualKey | "new"; lanes: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const edit = props.target === "new" ? null : props.target;

  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [custom, setCustom] = useState(false);
  const [customKey, setCustomKey] = useState("");
  const [models, setModels] = useState(edit ? edit.models.join(", ") : "");
  const [budget, setBudget] = useState(edit?.max_budget != null ? String(edit.max_budget) : "");
  const [rpm, setRpm] = useState(edit?.rpm != null ? String(edit.rpm) : "");
  const [tpm, setTpm] = useState(edit?.tpm != null ? String(edit.tpm) : "");
  // Expiry select: "" = never (create) / keep current (edit); "never" = remove (edit only).
  const [expiry, setExpiry] = useState("");
  const [hours, setHours] = useState("");
  // Lane select: "" = the gateway's default lane; only offered when the
  // operator configured priority lanes at all.
  const [priority, setPriority] = useState(edit?.priority ?? "");
  const [clr, setClr] = useState({ budget: false, rpm: false, tpm: false, models: false });
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ key: string; name: string } | null>(null);

  // In edit mode the limit inputs are seeded with the current values; only
  // fields that differ are sent, so an untouched field can never overwrite.
  const budgetN = tryParse(budget);
  const rpmN = tryParse(rpm);
  const tpmN = tryParse(tpm);
  const hoursN = expiry === "custom" ? tryParse(hours) : expiry && expiry !== "never" ? Number(expiry) : null;
  const numsOk = ![budgetN, rpmN, tpmN, expiry === "custom" ? hoursN : null].some(isBad);
  const customShort = !edit && custom && customKey.trim().length < 16;
  const customHoursMissing = expiry === "custom" && hoursN == null;

  const why =
    !edit && !name.trim()
      ? "Name is required"
      : !numsOk
        ? "A limit holds something that isn't a number"
        : customShort
          ? "A custom key must be at least 16 characters"
          : customHoursMissing
            ? "Enter the custom expiry in hours, or pick a duration"
            : null;

  const create = useMutation({
    mutationFn: (body: Parameters<typeof generateKey>[0]) => generateKey(body),
    onSuccess: (d) => {
      setCreated({ key: d.key, name: name.trim() });
      void qc.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (e) => setError(e.message),
  });
  const save = useMutation({
    mutationFn: (a: { id: string; patch: Parameters<typeof patchKey>[1] }) => patchKey(a.id, a.patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["keys"] });
      props.onClose();
    },
    onError: (e) => setError(e.message),
  });
  const pending = create.isPending || save.isPending;

  function submit() {
    setNameTouched(true);
    if (why || pending) return;
    setError(null);
    const list = parseCsv(models);
    if (!edit) {
      create.mutate({
        name: name.trim(),
        custom_key: custom ? customKey.trim() : undefined,
        ...(list.length > 0 ? { models: list } : {}),
        max_budget: budgetN ?? undefined,
        rpm: rpmN ?? undefined,
        tpm: tpmN ?? undefined,
        ttl_seconds: hoursN != null ? Math.round(hoursN * 3600) : undefined,
        priority: priority || undefined,
      });
      return;
    }
    const patch: Parameters<typeof patchKey>[1] = {};
    if (clr.budget) patch.max_budget = null;
    else if (budgetN != null && budgetN !== edit.max_budget) patch.max_budget = budgetN;
    if (clr.rpm) patch.rpm = null;
    else if (rpmN != null && rpmN !== edit.rpm) patch.rpm = rpmN;
    if (clr.tpm) patch.tpm = null;
    else if (tpmN != null && tpmN !== edit.tpm) patch.tpm = tpmN;
    if (clr.models) patch.models = [];
    else if (list.length > 0 && list.join(",") !== edit.models.join(",")) patch.models = list;
    if (expiry === "never") patch.expires_at = null;
    else if (hoursN != null) patch.expires_at = Math.round(Date.now() / 1000 + hoursN * 3600);
    if (priority !== (edit.priority ?? "")) patch.priority = priority || null;
    save.mutate({ id: edit.id, patch });
  }

  /* reveal-once */
  if (created) {
    return (
      <Dialog
      glass
        open
        contained
        icon={Check}
        title="Key created"
        subtitle={`${created.name || "Key"} is live. Copy the plaintext now; it is never shown again.`}
        onClose={props.onClose}
      >
        <div className="space-y-4">
          <div className="rounded-xl border border-white/[0.08] bg-black/45 p-4">
            <p className="admin-label mb-1.5">Secret key</p>
            <p className="break-all font-mono text-[14px] leading-relaxed text-[var(--admin-text)]">{created.key}</p>
          </div>
          <p className="flex items-start gap-2.5 rounded-lg border border-amber-500/30 bg-[#1a1408] px-3.5 py-3 text-[12px] leading-relaxed text-amber-200">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400" />
            Only a SHA-256 hash is stored. Keep this key in your secret manager.
          </p>
          <p className="break-all font-mono text-[12px] text-[var(--admin-text-muted)]">
            Authorization: Bearer <span className="text-[var(--admin-text)]">{created.key}</span>
          </p>
          <div className="flex flex-col-reverse gap-2.5 border-t border-[var(--admin-border)] pt-4 sm:flex-row sm:justify-end">
            <CopyButton text={created.key} />
            <Button onClick={props.onClose}>Done</Button>
          </div>
        </div>
      </Dialog>
    );
  }

  const expiryOptions = edit
    ? [
        { value: "", label: edit.expires_at != null ? `Keep (${fmtDateTime(edit.expires_at)})` : "Keep (never expires)" },
        { value: "never", label: "Remove expiry" },
        ...EXPIRY_PRESETS,
      ]
    : [{ value: "", label: "Never expires" }, ...EXPIRY_PRESETS];

  return (
    <Dialog
      glass
      open
      contained
      wide
      icon={KeyRound}
      title={edit ? `Edit ${edit.alias}` : "New virtual key"}
      subtitle={edit ? "Only the fields you change are saved." : "Every limit is optional and can be changed later."}
      onClose={props.onClose}
      footer={
        <>
          {!edit && (
            <p className="hidden min-w-0 flex-1 truncate font-mono text-[11.5px] tabular-nums text-[var(--admin-text-dim)] sm:block">
              {budget ? `$${budget}` : "no cap"} · {rpm || "∞"} rpm · {tpm || "∞"} tpm ·{" "}
              {expiry === "custom" ? (hours ? `${hours}h` : "custom") : (expiryOptions.find((o) => o.value === expiry)?.label ?? "").toLowerCase()}
            </p>
          )}
          <Button variant="ghost" type="button" onClick={props.onClose}>
            Cancel
          </Button>
          <Button type="submit" form="key-form" disabled={!!why || pending} title={why ?? undefined}>
            {pending ? "Saving…" : edit ? "Save changes" : "Create key"}
          </Button>
        </>
      }
    >
      <form
        id="key-form"
        className="-mr-2 min-h-0 flex-1 space-y-5 overflow-y-auto pr-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {!edit && (
          <Group title="Identity" desc="Who this key is for and how its secret is made.">
            <Field label="Name" hint="A label for where this key is used.">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                onBlur={() => setNameTouched(true)}
                placeholder="ci-pipeline"
                autoFocus
              />
            </Field>
            {nameTouched && !name.trim() && <FieldError>Name is required</FieldError>}
            <Segmented
              label="Secret source"
              value={custom ? "own" : "generated"}
              onChange={(v) => setCustom(v === "own")}
              options={[
                { value: "generated", label: "Generate for me" },
                { value: "own", label: "Bring my own" },
              ]}
            />
            {custom && (
              <div>
                <Field label="Custom key" hint="At least 16 characters; stored hashed.">
                  <Input
                    value={customKey}
                    onChange={(e) => setCustomKey(e.target.value)}
                    placeholder="sk-my-own-value…"
                    className="font-mono"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </Field>
                {customKey.trim().length > 0 && customShort && (
                  <FieldError>Custom keys must be at least 16 characters</FieldError>
                )}
              </div>
            )}
          </Group>
        )}

        <Group title="Limits" desc="Leave a field empty for no limit.">
          {!edit && (
            <div role="group" aria-label="Limit presets" className="flex flex-wrap items-center gap-1.5">
              <span className="admin-label mr-1">Start from</span>
              {PRESETS.map((p) => {
                const on =
                  p.budget === budget &&
                  p.rpm === rpm &&
                  p.tpm === tpm &&
                  p.ttl === (expiry === "custom" ? hours : expiry);
                return (
                  <button
                    key={p.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => {
                      setBudget(p.budget);
                      setRpm(p.rpm);
                      setTpm(p.tpm);
                      setExpiry(p.ttl);
                      setHours("");
                    }}
                    className={`min-h-11 rounded-full border px-3.5 text-[12.5px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500 ${
                      on
                        ? "border-indigo-400/25 bg-[var(--admin-accent-soft)] text-[var(--admin-text)]"
                        : "border-white/[0.08] text-[var(--admin-text-muted)] hover:bg-white/[0.05] hover:text-[var(--admin-text)]"
                    }`}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          )}
          <div className="grid gap-x-3 gap-y-2 rounded-xl border border-white/[0.07] bg-black/30 p-3.5 sm:grid-cols-3">
            <LimitField
              label="Budget"
              hint="Lifetime spend cap."
              value={budget}
              onChange={setBudget}
              placeholder="unlimited"
              suffix="USD"
              step="any"
              clear={edit ? { checked: clr.budget, onChange: (v) => setClr((c) => ({ ...c, budget: v })) } : undefined}
            />
            <LimitField
              label="RPM"
              hint="Requests per minute."
              value={rpm}
              onChange={setRpm}
              placeholder="unlimited"
              clear={edit ? { checked: clr.rpm, onChange: (v) => setClr((c) => ({ ...c, rpm: v })) } : undefined}
            />
            <LimitField
              label="TPM"
              hint="Tokens per minute."
              value={tpm}
              onChange={setTpm}
              placeholder="unlimited"
              clear={edit ? { checked: clr.tpm, onChange: (v) => setClr((c) => ({ ...c, tpm: v })) } : undefined}
            />
          </div>
        </Group>

        <Group title="Access" desc="When the key stops working and which models it can reach.">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Expires in" hint={edit ? "Counted from now." : "Counted from creation."}>
              <Select value={expiry} onChange={setExpiry} options={expiryOptions} className="w-full" />
            </Field>
            {expiry === "custom" && (
              <div>
                <Field label="Custom hours" hint="Hours from now.">
                  <NumberInput min={0} step="any" value={hours} onChange={setHours} placeholder="720" suffix="hrs" />
                </Field>
                {isBad(hoursN) && <FieldError>Not a number</FieldError>}
              </div>
            )}
            {props.lanes.length > 0 && (
              <Field label="Priority lane" hint="Reserved router concurrency; (default) shares the common lane.">
                <Select
                  value={priority}
                  onChange={setPriority}
                  options={[{ value: "", label: "(default)" }, ...props.lanes.map((l) => ({ value: l, label: l }))]}
                  className="w-full"
                />
              </Field>
            )}
          </div>
          <div>
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <span className="admin-label">Model allowlist</span>
              <span className="text-[11px] text-[var(--admin-text-dim)]">
                {parseCsv(models).length === 0 ? "empty = all models" : `${parseCsv(models).length} allowed`}
              </span>
            </div>
            <ModelChips value={models} onChange={setModels} />
            {edit && (
              <label className="mt-1 flex min-h-11 cursor-pointer items-center gap-2 text-[12px] text-[var(--admin-text-muted)]">
                <input
                  type="checkbox"
                  checked={clr.models}
                  onChange={(e) => setClr((c) => ({ ...c, models: e.target.checked }))}
                  className="h-4 w-4 accent-white"
                />
                Remove allowlist (all models)
              </label>
            )}
          </div>
        </Group>

        {why && (nameTouched || edit) && (
          <p role="alert" className="flex items-center gap-1.5 text-[11.5px] text-amber-400">
            <AlertTriangle size={12} className="shrink-0" />
            {why}
          </p>
        )}
        {error && <ErrorText>{error}</ErrorText>}
      </form>
    </Dialog>
  );
}

/* --------------------------------------------------------------------- page */

export function VirtualKeysPage() {
  const query = useQuery({ queryKey: ["keys"], queryFn: listKeys, refetchInterval: 30_000 });
  const nowMs = useNow();
  const [pageError, setPageError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("name");
  const [dir, setDir] = useState<"asc" | "desc">("asc");
  const [dialog, setDialog] = useState<VirtualKey | "new" | null>(null);

  const all = query.data?.keys ?? [];
  // Lane names ride the same response; empty when none are configured, in
  // which case the dialog's lane select is not offered at all.
  const lanes = query.data?.priority_lanes ?? [];
  const needle = search.trim().toLowerCase();
  const searched = needle
    ? all.filter((k) => k.alias.toLowerCase().includes(needle) || k.models.some((m) => m.toLowerCase().includes(needle)))
    : all;
  const visible = searched
    .filter((k) => matchesFilter(k, filter))
    .sort((a, b) => compareKeys(a, b, sort, dir) || a.alias.localeCompare(b.alias));
  // Counts describe the whole account, whatever the search says.
  const counts = Object.fromEntries(FILTERS.map((f) => [f.id, all.filter((k) => matchesFilter(k, f.id)).length])) as Record<
    Filter,
    number
  >;

  return (
    <div>
      <PageHeader
        title="Virtual Keys"
        subtitle="Client credentials callers authenticate with: per-key budgets, rate limits and model access."
        right={
          <Button onClick={() => setDialog("new")}>
            <Plus size={14} /> New key
          </Button>
        }
      />

      {pageError && (
        <div className="mb-3">
          <ErrorText>{pageError}</ErrorText>
        </div>
      )}

      {all.length > 0 && <SummaryStrip keys={all} nowMs={nowMs} />}

      <Card>
        {query.isLoading && (
          <div className="space-y-px p-4">
            {[0, 1, 2].map((i) => (
              <div key={i} className="admin-skeleton h-[68px] w-full rounded-lg" />
            ))}
          </div>
        )}
        {query.error && (
          <div className="p-4">
            <ErrorText>{query.error.message}</ErrorText>
          </div>
        )}

        {query.data && all.length === 0 && (
          <div className="flex flex-col items-center gap-3 px-4 py-14 text-center">
            <KeyRound size={20} className="text-[var(--admin-text-dim)]" />
            <div>
              <p className="text-[13.5px] font-medium text-[var(--admin-text)]">No virtual keys yet</p>
              <p className="mt-1 text-[12px] text-[var(--admin-text-dim)]">
                Issue a credential to give an app its own budget, rate limits and model access.
              </p>
            </div>
            <Button onClick={() => setDialog("new")}>
              <Plus size={14} /> New key
            </Button>
          </div>
        )}

        {all.length > 0 && (
          <>
            <div className="space-y-2 border-b border-[var(--admin-border)] px-4 py-3">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <SearchInput
                  value={search}
                  onChange={setSearch}
                  placeholder="Search by name or model…"
                  className="min-w-0 flex-1"
                />
                <div className="flex items-center gap-2">
                  <Select
                    value={sort}
                    onChange={(v) => setSort(v as Sort)}
                    options={SORT_OPTIONS}
                    className="min-w-0 flex-1 sm:flex-none"
                  />
                  <Button
                    variant="outline"
                    className="shrink-0"
                    aria-label={dir === "asc" ? "Sort ascending" : "Sort descending"}
                    onClick={() => setDir((d) => (d === "asc" ? "desc" : "asc"))}
                  >
                    {dir === "asc" ? <ArrowUp size={14} /> : <ArrowDown size={14} />}
                  </Button>
                </div>
              </div>
              <FilterTabs value={filter} onChange={setFilter} counts={counts} />
            </div>

            {visible.length === 0 ? (
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
                <KeyListHeader />
                <ul className="divide-y divide-[var(--admin-border)]">
                  {visible.map((k) => (
                    <KeyRow key={k.id} k={k} nowMs={nowMs} onEdit={setDialog} onError={setPageError} />
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </Card>

      {dialog != null && (
        <KeyDialog key={dialog === "new" ? "new" : dialog.id} target={dialog} lanes={lanes} onClose={() => setDialog(null)} />
      )}
    </div>
  );
}
