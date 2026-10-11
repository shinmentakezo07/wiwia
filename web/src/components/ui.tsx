// Shared UI kit — cloned from the Dra admin design system: admin-card
// surfaces, hairline borders, uppercase micro-labels, mono tabular values.

import { useEffect, useRef, useState } from "react";
import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Copy,
  Info,
  X,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { AnimatedNumber } from "@/components/animated";
import type { Delta } from "@/lib/dashboard-metrics";

// -- layout ------------------------------------------------------------------

export function Card(props: { children: ReactNode; className?: string }) {
  return (
    <div className={`admin-card ${props.className ?? ""}`}>{props.children}</div>
  );
}

export function CardHeader(props: { title: ReactNode; subtitle?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-2 border-b border-[var(--admin-border)] px-5 py-3.5">
      <div className="min-w-0">
        <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {props.title}
        </h3>
        {props.subtitle && (
          <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">{props.subtitle}</p>
        )}
      </div>
      {/* Wraps below the title on narrow viewports. Without it a wide set of
          controls squeezes the min-w-0 title box below its own text width and
          the title overflows into them. */}
      {props.right && <div className="flex flex-wrap items-center gap-2">{props.right}</div>}
    </div>
  );
}

export function PageHeader(props: { title: ReactNode; subtitle?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h2 className="text-2xl font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
          {props.title}
        </h2>
        {props.subtitle && (
          <p className="mt-0.5 font-mono text-[13px] tracking-wide text-[var(--admin-text-muted)]">
            {props.subtitle}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2">{props.right}</div>
    </div>
  );
}

// -- controls ------------------------------------------------------------------

type ButtonVariant = "primary" | "ghost" | "danger" | "outline";

const BTN: Record<ButtonVariant, string> = {
  primary: "admin-btn-primary",
  ghost: "admin-btn-ghost",
  danger: "admin-btn-danger",
  outline: "admin-btn-ghost",
};

type ButtonOwn = { variant?: ButtonVariant; className?: string };

/** A button, or — when `href` is set — an anchor styled identically.
 *
 *  The anchor form exists because `window.open()` is not a safe way to launch an
 *  external login: it is only permitted inside a user gesture, so calling it
 *  from an async callback is routinely popup-blocked, and its return value
 *  cannot report the block (`window.open(..., "noopener")` returns null on
 *  *success* in Chrome and Firefox, per spec). An `<a target="_blank">` is
 *  driven by a real click, so the browser never blocks it, and if something
 *  downstream swallows it the caller can still surface the URL for copying. */
export function Button(
  props: ButtonOwn &
    (
      | ({ href: string } & AnchorHTMLAttributes<HTMLAnchorElement>)
      | ({ href?: undefined } & ButtonHTMLAttributes<HTMLButtonElement>)
    ),
) {
  const cls = `admin-btn ${BTN[props.variant ?? "primary"]} ${props.className ?? ""}`;
  if (typeof props.href === "string") {
    const { variant: _v, className: _c, href, ...rest } = props;
    return <a href={href} className={cls} {...rest} />;
  }
  const { variant: _v, className: _c, ...rest } = props as ButtonOwn &
    ButtonHTMLAttributes<HTMLButtonElement>;
  return <button className={cls} {...rest} />;
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props;
  return <input className={`admin-input ${className}`} {...rest} />;
}

/** Number input with custom dark-themed −/+ stepper buttons (native browser
 *  spinners are hidden via CSS). Optional right-aligned unit suffix. */
export function NumberInput(props: {
  value: string;
  onChange: (v: string) => void;
  min?: number;
  step?: number | "any";
  suffix?: string;
  disabled?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
  /** Forwarded to the inner input — Enter/Escape handling lives on callers. */
  onKeyDown?: (e: ReactKeyboardEvent<HTMLInputElement>) => void;
}) {
  const { value, onChange, min, step, suffix, disabled, onKeyDown, ...rest } = props;
  const stepN = step === "any" || step == null ? 1 : step;
  const num = value === "" ? null : Number(value);
  const atMin = min != null && num != null && !Number.isNaN(num) && num <= min;

  function clamp(n: number) {
    if (min != null && n < min) n = min;
    return n;
  }
  function bump(dir: 1 | -1) {
    const base = num == null || Number.isNaN(num) ? (min != null ? min : 0) : num;
    onChange(String(clamp(base + dir * stepN)));
  }

  return (
    <div className="admin-number-field">
      <Input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        min={min}
        step={step}
        disabled={disabled}
        className={suffix ? "pr-16" : undefined}
        {...rest}
      />
      {suffix && (
        <span className="pointer-events-none absolute right-8 top-1/2 -translate-y-1/2 font-mono text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">
          {suffix}
        </span>
      )}
      <span className="admin-number-stepper">
        <button type="button" tabIndex={-1} disabled={disabled} onClick={() => bump(1)} aria-label="Increment">
          <ChevronUp size={11} />
        </button>
        <button type="button" tabIndex={-1} disabled={disabled || atMin} onClick={() => bump(-1)} aria-label="Decrement">
          <ChevronDown size={11} />
        </button>
      </span>
    </div>
  );
}

export function Select(props: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  className?: string;
}) {
  return (
    <select
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
      className={`admin-input w-auto ${props.className ?? ""}`}
    >
      {props.options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Field(props: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="admin-label mb-1.5 block">{props.label}</span>
      {props.children}
      {props.hint && (
        <span className="mt-1 block text-[11px] text-[var(--admin-text-dim)]">
          {props.hint}
        </span>
      )}
    </label>
  );
}

export function Toggle(props: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={props.checked}
      disabled={props.disabled}
      onClick={() => props.onChange(!props.checked)}
      className={`relative h-5 w-9 rounded-full transition-colors disabled:opacity-50 ${
        props.checked ? "bg-blue-500/40" : "bg-white/[0.06]"
      }`}
      style={props.checked ? { boxShadow: "0 0 12px -2px rgba(59,130,246,0.3)" } : undefined}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full transition-all ${
          props.checked ? "left-[1.15rem] bg-blue-400" : "left-0.5 bg-zinc-500"
        }`}
      />
    </button>
  );
}

// -- display ------------------------------------------------------------------

const BADGE_TONES: Record<string, string> = {
  green: "admin-badge-green",
  red: "admin-badge-red",
  amber: "admin-badge-amber",
  gray: "admin-badge-gray",
  blue: "admin-badge-blue",
  violet: "admin-badge-violet",
};

export function Badge(props: { children: ReactNode; tone?: keyof typeof BADGE_TONES; title?: string }) {
  return (
    <span title={props.title} className={`admin-badge ${BADGE_TONES[props.tone ?? "gray"]}`}>
      {props.children}
    </span>
  );
}

export type StatTone = "default" | "brand" | "success" | "warning" | "danger";

const STAT_ACCENT: Record<StatTone, string> = {
  default: "rgba(255,255,255,0.35)",
  brand: "var(--admin-accent)",
  success: "var(--admin-success)",
  warning: "var(--admin-warning)",
  danger: "var(--admin-danger)",
};

function TileSparkline(props: { points: number[]; accent: string }) {
  const w = 96;
  const h = 26;
  const max = Math.max(1, ...props.points);
  const pts = props.points.map((v, i) => {
    const x = props.points.length <= 1 ? 0 : (i / (props.points.length - 1)) * w;
    const y = h - 2 - (v / max) * (h - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const lastPair = pts[pts.length - 1] ?? `${w},${h - 2}`;
  const [lx, ly] = lastPair.split(",").map(Number);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-[26px] w-24 shrink-0" aria-hidden>
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke="var(--admin-text-dim)"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.55}
      />
      <circle cx={lx} cy={ly} r={2.5} fill={props.accent} />
    </svg>
  );
}

function DeltaChip(props: { delta: Delta; goodDir: "up" | "down" }) {
  if (props.delta.pct === null) {
    return <span className="admin-delta-chip admin-delta-flat">— vs prev hour</span>;
  }
  if (props.delta.dir === "flat") {
    return <span className="admin-delta-chip admin-delta-flat">±0% vs prev hour</span>;
  }
  const cls = props.delta.dir === props.goodDir ? "admin-delta-up" : "admin-delta-down";
  const arrow = props.delta.dir === "up" ? "↑" : "↓";
  return (
    <span className={`admin-delta-chip ${cls}`}>
      {arrow} {Math.abs(props.delta.pct).toFixed(0)}% vs prev hour
    </span>
  );
}

export function StatCard(props: {
  label: string;
  value: string;
  sub?: string;
  icon?: LucideIcon;
  tone?: StatTone;
  /** Hero metric: larger value + accent-tinted surface. */
  featured?: boolean;
  /** 12-point sparkline, oldest first. */
  spark?: number[];
  /** Change vs the previous hour. */
  delta?: Delta;
  /** Which direction of `delta` is good (default: down). */
  deltaGoodDir?: "up" | "down";
  /** Zero-traffic state: pulse the value instead of flat zeros. */
  waiting?: boolean;
  /**
   * Underlying numeric value. When given (together with `format`), the tile
   * counts up to it on change instead of snapping — the live-refresh cue.
   */
  numeric?: number;
  /** Formatter for `numeric`; falls back to `value`. */
  format?: (n: number) => string;
}) {
  const accent = STAT_ACCENT[props.tone ?? "default"];
  const Icon = props.icon;
  const animated =
    props.numeric != null &&
    Number.isFinite(props.numeric) &&
    props.format != null;
  return (
    <Card className={`group relative p-5 ${props.featured ? "admin-stat-highlight" : ""}`}>
      {/* Accent wash — a corner glow that warms on hover, matching the tile's
          tone, so featured tiles read as "live surfaces" not just cards. */}
      <span
        aria-hidden
        className="admin-stat-wash pointer-events-none absolute inset-0"
        style={{ background: `radial-gradient(120% 90% at 100% 0%, ${accent}0F 0%, transparent 60%)` }}
      />
      <div className="relative z-10">
        <div className="mb-3 flex items-center gap-2">
          {Icon && (
            <Icon className="h-3.5 w-3.5 shrink-0" style={{ color: accent, opacity: 0.6 }} />
          )}
          <span className="admin-label">{props.label}</span>
        </div>
        <div className="flex items-end justify-between gap-3">
          <p
            className={`admin-stat-value font-mono ${props.featured ? "text-[28px]" : "text-[22px]"} ${
              props.waiting ? "admin-waiting-pulse" : ""
            }`}
          >
            {animated ? (
              <AnimatedNumber value={props.numeric as number} format={props.format!} />
            ) : (
              props.value
            )}
          </p>
          {props.spark && props.spark.some((v) => v > 0) && (
            <TileSparkline points={props.spark} accent={accent} />
          )}
        </div>
        {(props.sub || props.delta) && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {props.sub && (
              <p className="font-mono text-[11px] text-[var(--admin-text-dim)]">{props.sub}</p>
            )}
            {props.delta && <DeltaChip delta={props.delta} goodDir={props.deltaGoodDir ?? "down"} />}
          </div>
        )}
      </div>
    </Card>
  );
}

export function ProgressBar(props: { value: number; tone?: string }) {
  const pct = Math.min(100, Math.max(0, props.value * 100));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.05]">
      <div
        className={`h-full rounded-full ${props.tone ?? (pct >= 100 ? "bg-red-400" : pct >= 80 ? "bg-amber-400" : "bg-emerald-400")}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function Spinner(_props: { className?: string }) {
  return (
    <div className="relative h-8 w-8">
      <div className="absolute inset-0 rounded-full border border-white/[0.04]" />
      <div className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-blue-400/50" />
    </div>
  );
}

/** Live-updating indicator pill: pulsing dot + "live" label when connected. */
export function LiveBadge(props: { connected: boolean }) {
  return (
    <span className="admin-live-badge">
      <span className={props.connected ? "admin-pulse-dot" : "h-1.5 w-1.5 rounded-full bg-zinc-600"} />
      {props.connected ? "live" : "offline"}
    </span>
  );
}

export function EmptyState(props: { children: ReactNode }) {
  return (
    <div className="px-4 py-12 text-center text-[13px] text-[var(--admin-text-dim)]">
      {props.children}
    </div>
  );
}

export function ErrorText(props: { children: ReactNode }) {
  return (
    <p className="rounded-[10px] border border-red-500/10 bg-red-500/[0.04] px-2.5 py-2 text-[12px] text-red-400">
      {props.children}
    </p>
  );
}

// -- table --------------------------------------------------------------------

export function Table(props: {
  head: ReactNode[];
  children: ReactNode;
  className?: string;
  /** `aria-sort` per column index — set it on the active sortable column only.
   *  The attribute belongs on the `<th>` (which carries the `columnheader` role),
   *  not on the button inside it, so it cannot live inside `head`. */
  headSort?: Record<number, "ascending" | "descending" | "none" | undefined>;
}) {
  return (
    <div className={`admin-table ${props.className ?? ""}`}>
      <div className="admin-scroll overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr>
              {props.head.map((h, i) => (
                <th key={i} aria-sort={props.headSort?.[i]}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>{props.children}</tbody>
        </table>
      </div>
    </div>
  );
}

export function TD(props: { children: ReactNode; className?: string; colSpan?: number }) {
  return (
    <td colSpan={props.colSpan} className={props.className ?? ""}>
      {props.children}
    </td>
  );
}

/** Clickable column header that sorts its table. `K` is the caller's own sort-key
 *  union, so each page keeps its own key vocabulary while sharing this affordance.
 *  The caller announces the active column through `Table`'s `headSort` —
 *  `aria-sort` belongs on the `<th>` (the columnheader), not on this button. */
export function SortHeader<K extends string>(props: {
  label: string;
  k: K;
  active: K;
  dir: "asc" | "desc";
  onSort: (k: K) => void;
}) {
  const isActive = props.k === props.active;
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 transition-colors hover:text-[var(--admin-text)] focus-visible:text-[var(--admin-text)]"
      onClick={() => props.onSort(props.k)}
    >
      {props.label}
      <span aria-hidden className={isActive ? "text-blue-400" : "opacity-30"}>
        {isActive && props.dir === "asc" ? "▲" : "▼"}
      </span>
    </button>
  );
}

// -- dialog ---------------------------------------------------------------------

export function Dialog(props: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  /** Max-width for the panel. Defaults to the `wide`-derived size, so omitting
   *  it (or passing `wide`) keeps the previous behaviour. */
  size?: "md" | "3xl" | "5xl";
  /** Cap the dialog to the viewport so a tall form never pushes its own action
   *  bar off-screen. This also stops the body scrolling, so a `contained` caller
   *  MUST provide its own `overflow-y-auto` region or its content will be
   *  clipped and unreachable. */
  contained?: boolean;
  /** Optional leading icon in the header (accented chip). */
  icon?: LucideIcon;
  /** Optional second line under the title. */
  subtitle?: ReactNode;
  /** Optional sticky footer row pinned below the (scrolling) body. Rendered
   *  right-aligned on desktop; full-width and stacked on mobile. */
  footer?: ReactNode;
  /** Hide the default bottom hairline divider on the footer. */
  footerBordered?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!props.open) return;
    // Remember the trigger so focus returns to it when the dialog closes — a
    // keyboard user who opens a modal should land back where they started, not
    // at the top of the document (A11y). Captured on open, before the move.
    const restoreTo = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const FOCUSABLE =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    // Move focus into the dialog on open (prefers the first field), so Tab
    // starts inside the panel rather than on the page behind it.
    const initial = panel?.querySelector<HTMLElement>(FOCUSABLE) ?? panel;
    initial?.focus?.();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        props.onClose();
        return;
      }
      // Focus trap: with an open modal, Tab must cycle within the panel and
      // never reach the page behind it. Wrap from the last node to the first
      // (and Shift-Tab the other way). Without this, keyboard focus escapes the
      // dialog into the inert content underneath.
      if (e.key !== "Tab" || panel == null) return;
      const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (n) => n.offsetParent !== null || n === document.activeElement,
      );
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey && (active === first || !panel.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !panel.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      restoreTo?.focus?.();
    };
  }, [props.open, props]);

  if (!props.open) return null;
  const sizeClass = props.size ?? (props.wide ? "3xl" : "md");
  const SIZE_CLASS: Record<NonNullable<typeof props.size>, string> = {
    md: "max-w-md",
    "3xl": "max-w-3xl",
    "5xl": "max-w-5xl",
  };
  const Icon = props.icon;
  return createPortal(
    <div
      className={`admin-overlay-enter fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 ${
        props.contained ? "p-3 pt-[6vh] sm:p-4 sm:pt-[8vh]" : "p-4 pt-[10vh]"
      }`}
      onClick={(e) => {
        if (e.target === e.currentTarget) props.onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className={`admin-dialog-enter w-full overflow-hidden rounded-2xl border border-white/[0.06] bg-[var(--admin-surface-elevated)] shadow-2xl shadow-black/60 ${SIZE_CLASS[sizeClass]} ${
          props.contained ? "flex max-h-[88vh] flex-col" : ""
        }`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-white/[0.06] px-5 py-4">
          <div className="flex min-w-0 items-start gap-3">
            {Icon && (
              <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/[0.08]">
                <Icon className="h-4 w-4 text-[var(--admin-text)]" />
              </span>
            )}
            <div className="min-w-0">
              <h3 className="text-[14px] font-semibold leading-tight text-[var(--admin-text)]">
                {props.title}
              </h3>
              {props.subtitle && (
                <p className="mt-1 text-[12px] leading-snug text-[var(--admin-text-muted)]">
                  {props.subtitle}
                </p>
              )}
            </div>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={props.onClose}
            className="-mr-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/30"
          >
            <X size={16} />
          </button>
        </div>
        {/* A contained body deliberately does not scroll: the panel is capped to
            the viewport, so the consumer owns the single scroll region. A nested
            scroll here is what makes a tall form feel cramped on short screens. */}
        <div
          className={
            props.contained
              ? `flex min-h-0 flex-1 flex-col overflow-hidden p-5 ${props.footer ? "pb-4" : ""}`
              : props.footer
                ? "p-5 pb-4"
                : "p-5"
          }
        >
          {props.children}
        </div>
        {props.footer && (
          <div
            className={
              props.footerBordered === false
                ? "flex flex-col-reverse gap-2.5 px-5 py-4 sm:flex-row sm:items-center sm:justify-end"
                : "flex flex-col-reverse gap-2.5 border-t border-white/[0.06] bg-[#0c0c0c] px-5 py-4 sm:flex-row sm:items-center sm:justify-end"
            }
          >
            {props.footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

// -- drawer (right-side slide-in panel) ----------------------------------------

export function Drawer(props: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  /** Pixel width of the panel (default 560). */
  width?: number;
}) {
  useEffect(() => {
    if (!props.open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") props.onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.open, props.onClose]);

  if (!props.open) return null;
  const w = props.width ?? 560;
  return createPortal(
    <div className="admin-overlay-enter fixed inset-0 z-50 overflow-hidden">
      {/* scrim */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        onClick={props.onClose}
        aria-hidden
      />
      {/* panel */}
      <div
        role="dialog"
        aria-modal="true"
        className="admin-drawer-enter absolute right-0 top-0 flex h-full flex-col border-l border-white/[0.06] bg-[var(--admin-surface-elevated)] shadow-2xl shadow-black/60"
        style={{ width: `min(${w}px, 100vw)` }}
      >
        {props.children}
      </div>
    </div>,
    document.body,
  );
}

export function CopyButton(props: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  return (
    <Button
      variant="outline"
      onClick={async () => {
        await navigator.clipboard.writeText(props.text);
        setCopied(true);
        timer.current = setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}
