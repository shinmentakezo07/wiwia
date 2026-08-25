// Shared building blocks for the two log pages (request + proxy):
// toolbar shell, custom table markup with per-row content-visibility,
// and small cell/badge helpers. Kept free of page-specific logic.

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Search } from "lucide-react";
import { Badge, Input, Toggle } from "@/components/ui";
import { fmtAgo, fmtDateTime } from "@/lib/format";

// -- live clock hook ---------------------------------------------------------
// Re-renders every `interval` ms so relative "x ago" labels stay fresh.

export function useNow(interval = 15_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(id);
  }, [interval]);
  return now;
}

// -- relative time cell -------------------------------------------------------

/** Mono-typed time cell: shows "x ago" relative, with absolute ts on hover. */
export function TimeAgo(props: { ts: number; nowMs: number; className?: string }) {
  return (
    <td
      title={fmtDateTime(props.ts)}
      className={`font-mono text-[12px] text-[var(--admin-text-dim)] whitespace-nowrap ${props.className ?? ""}`}
    >
      {fmtAgo(props.ts, props.nowMs)}
    </td>
  );
}

// -- summary chips -----------------------------------------------------------

/** Small inline summary chip for the area above a log table. */
export function SummaryChip(props: {
  label: string;
  value: string;
  tone?: "default" | "success" | "warning" | "danger";
  icon?: ReactNode;
}) {
  const toneCls = {
    default: "border-[var(--admin-border)] text-[var(--admin-text-muted)]",
    success: "border-emerald-500/15 bg-emerald-500/[0.03] text-emerald-400",
    warning: "border-amber-500/15 bg-amber-500/[0.03] text-amber-400",
    danger: "border-red-500/15 bg-red-500/[0.03] text-red-400",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[12px] ${toneCls[props.tone ?? "default"]}`}
    >
      {props.icon && (
        <span className="text-[var(--admin-text-dim)]" aria-hidden>
          {props.icon}
        </span>
      )}
      <span className="font-mono tabular-nums font-semibold">{props.value}</span>
      <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--admin-text-dim)]">
        {props.label}
      </span>
    </span>
  );
}

/** Thin horizontal distribution bar showing relative proportions (e.g. ok vs
 * errors). Renders nothing when the total is zero. */
export function SummaryBar(props: {
  segments: { value: number; tone: string; title?: string }[];
}) {
  const total = props.segments.reduce((s, seg) => s + seg.value, 0);
  if (total <= 0) return null;
  return (
    <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-white/[0.03]">
      {props.segments.map((seg, i) =>
        seg.value > 0 ? (
          <div
            key={i}
            className={seg.tone}
            style={{ width: `${(seg.value / total) * 100}%` }}
            title={seg.title}
          />
        ) : null,
      )}
    </div>
  );
}
// -- toolbar -----------------------------------------------------------------

/** Flex-wrap container for the filter bar above a log table. */
export function LogsToolbar(props: { children: ReactNode }) {
  return <div className="mb-3 flex flex-wrap items-center gap-2">{props.children}</div>;
}

export function SearchInput(props: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <div className="relative">
      <Search
        size={13}
        aria-hidden
        className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
      />
      <Input
        className="h-8 w-72 pl-8 text-[12px]"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        placeholder={props.placeholder}
      />
    </div>
  );
}

/** Live-tail switch with an SSE connection indicator. */
export function LiveTail(props: { live: boolean; onToggle: (v: boolean) => void; connected?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      {props.live && (
        <span className="flex items-center gap-1.5 text-[11px] text-[var(--admin-text-dim)]">
          <span
            className={props.connected ? "admin-pulse-dot" : "h-1.5 w-1.5 rounded-full bg-zinc-600"}
          />
          {props.connected ? "streaming" : "connecting…"}
        </span>
      )}
      <span className="flex items-center gap-2 text-[13px] text-[var(--admin-text-muted)]">
        Live tail
        <Toggle checked={props.live} onChange={props.onToggle} />
      </span>
    </div>
  );
}

// -- table shell ---------------------------------------------------------------
// Custom markup (instead of ui.Table) so each row can opt into
// content-visibility for cheap virtualization-feel on long rings.
// Cell chrome (padding, borders, uppercase headers) comes from the global
// .admin-table styles; only semantic classes live on individual cells.

export function LogsTable(props: {
  head: ReactNode[];
  children: ReactNode;
  /** Optional max-height (px) enabling a sticky header inside a scrollable body. */
  maxHeight?: number;
}) {
  return (
    <div className="admin-table">
      <div
        className="admin-scroll overflow-auto"
        style={props.maxHeight ? { maxHeight: `${props.maxHeight}px` } : undefined}
      >
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

export function LogRow(props: {
  children: ReactNode;
  onClick?: () => void;
  ariaLabel?: string;
  /** Index used for subtle zebra striping. */
  zebra?: number;
  /** Disable the hover highlight (e.g. an expanded detail row). */
  flat?: boolean;
}) {
  const zebraCls = props.zebra != null && props.zebra % 2 === 1 ? "bg-white/[0.012]" : "";
  return (
    <tr
      aria-label={props.ariaLabel}
      onClick={props.onClick}
      onKeyDown={
        props.onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                props.onClick?.();
              }
            }
          : undefined
      }
      tabIndex={props.onClick ? 0 : undefined}
      className={`[content-visibility:auto] [contain-intrinsic-size:auto_37px] ${zebraCls}${
        props.onClick && !props.flat
          ? " cursor-pointer outline-none hover:bg-white/[0.04] focus-visible:bg-white/[0.04]"
          : ""
      }`}
    >
      {props.children}
    </tr>
  );
}

export function LogTD(props: { children?: ReactNode; className?: string; title?: string; colSpan?: number }) {
  return (
    <td title={props.title} colSpan={props.colSpan} className={props.className ?? ""}>
      {props.children}
    </td>
  );
}

// -- cells ---------------------------------------------------------------------

export function MonoCell(props: { children: ReactNode; className?: string }) {
  return (
    <span className={`font-mono text-[12px] ${props.className ?? ""}`}>{props.children}</span>
  );
}

/** Right-aligned tabular number content (wrap in a td with text-right). */
export function NumCell(props: { children: ReactNode }) {
  return <span className="font-mono tabular-nums">{props.children}</span>;
}

/** Green below 400, red otherwise; appends the error code on failures. */
export function StatusBadge(props: { status: number; errorCode?: string }) {
  const ok = props.status < 400;
  return (
    <span className="inline-flex max-w-[220px] items-center gap-1.5" title={props.errorCode || undefined}>
      <Badge tone={ok ? "green" : "red"}>{props.status}</Badge>
      {!ok && props.errorCode && (
        <span className="truncate font-mono text-[10px] tracking-wide text-[var(--admin-danger)] opacity-70">
          {props.errorCode}
        </span>
      )}
    </span>
  );
}

// -- footer ----------------------------------------------------------------------

export function LogsFooter(props: { shown: number; total: number }) {
  return (
      <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
        showing <span className="text-zinc-300">{props.shown}</span> of{" "}
        <span className="text-zinc-300">{props.total}</span> events
        <span className="mx-1.5 opacity-40">·</span> ring holds ~500
      </span>
  );
}
