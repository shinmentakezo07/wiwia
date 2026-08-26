// Shared UI kit — cloned from the Dra admin design system: admin-card
// surfaces, hairline borders, uppercase micro-labels, mono tabular values.

import { useEffect, useRef, useState } from "react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, ChevronUp, Copy, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Delta } from "@/lib/dashboard-metrics";

// -- layout ------------------------------------------------------------------

export function Card(props: { children: ReactNode; className?: string }) {
  return (
    <div className={`admin-card ${props.className ?? ""}`}>{props.children}</div>
  );
}

export function CardHeader(props: { title: ReactNode; subtitle?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-[var(--admin-border)] px-5 py-3.5">
      <div className="min-w-0">
        <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {props.title}
        </h3>
        {props.subtitle && (
          <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">{props.subtitle}</p>
        )}
      </div>
      {props.right}
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

export function Button(props: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  const { variant = "primary", className = "", ...rest } = props;
  return (
    <button className={`admin-btn ${BTN[variant]} ${className}`} {...rest} />
  );
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
}) {
  const { value, onChange, min, step, suffix, disabled, ...rest } = props;
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
}) {
  const accent = STAT_ACCENT[props.tone ?? "default"];
  const Icon = props.icon;
  return (
    <Card className={`group relative p-5 ${props.featured ? "admin-stat-highlight" : ""}`}>
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
            {props.value}
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

export function Table(props: { head: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={`admin-table ${props.className ?? ""}`}>
      <div className="admin-scroll overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr>
              {props.head.map((h, i) => (
                <th key={i}>{h}</th>
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

// -- dialog ---------------------------------------------------------------------

export function Dialog(props: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!props.open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") props.onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.open, props]);

  if (!props.open) return null;
  return createPortal(
    <div
      className="admin-overlay-enter fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 pt-[10vh] backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) props.onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className={`admin-dialog-enter w-full overflow-hidden rounded-2xl border border-white/[0.06] bg-[var(--admin-surface-elevated)] shadow-2xl shadow-black/60 ${
          props.wide ? "max-w-3xl" : "max-w-md"
        }`}
      >
        <div className="flex items-center justify-between gap-4 border-b border-white/[0.04] px-5 py-3.5">
          <h3 className="min-w-0 text-[14px] font-semibold text-[var(--admin-text)]">
            {props.title}
          </h3>
          <button
            type="button"
            aria-label="Close"
            onClick={props.onClose}
            className="shrink-0 rounded-lg p-2 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
          >
            <X size={16} />
          </button>
        </div>
        <div className="p-5">{props.children}</div>
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
