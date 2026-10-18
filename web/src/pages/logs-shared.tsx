// Shared building blocks for the two log pages (request + proxy):
// toolbar shell, custom table markup with per-row content-visibility,
// small cell/badge helpers, and the loading skeleton.
// Kept free of page-specific logic.

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Search, X } from "lucide-react";
import { Badge, Card, Input, Toggle } from "@/components/ui";
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

/** Mono-typed time cell: shows "x ago" relative, with absolute ts on hover.
 *
 *  Below `sm` the unit word is dropped ("11m" rather than "11m ago"): on a
 *  phone every pixel of the status column has to hold the error code and the
 *  model name, and "ago" is already stated by the column header and the
 *  tooltip. */
export function TimeAgo(props: { ts: number; nowMs: number; className?: string }) {
  const [value, unit] = fmtAgo(props.ts, props.nowMs).split(" ");
  return (
    <td
      title={fmtDateTime(props.ts)}
      className={`font-mono text-[12px] text-[var(--admin-text-dim)] whitespace-nowrap ${props.className ?? ""}`}
    >
      {value}
      {unit && <span className="hidden sm:inline"> {unit}</span>}
    </td>
  );
}

// -- toolbar -----------------------------------------------------------------

/** Flex-wrap container for the filter bar above a log table. */
export function LogsToolbar(props: { children: ReactNode }) {
  return <div className="mb-3 flex flex-wrap items-center gap-2">{props.children}</div>;
}

/** Filter box with a themed clear button and a "/" jump-to shortcut.
 *
 *  Both affordances exist because the box is the page's main navigation: "/"
 *  is what a reader who lives in a terminal or a log viewer reaches for, and
 *  clearing a filter you can no longer read (a long request id) should not
 *  require selecting the text and deleting it. */
export function SearchInput(props: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  className?: string;
}) {
  const ref = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      e.preventDefault();
      ref.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className={`relative ${props.className ?? ""}`}>
      <Search
        size={13}
        aria-hidden
        className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
      />
      <Input
        ref={ref}
        type="search"
        className="h-8 pr-8 pl-8 text-[12px] pointer-coarse:h-11"
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && props.value) props.onChange("");
        }}
        placeholder={props.placeholder}
      />
      {props.value ? (
        <button
          type="button"
          onClick={() => {
            props.onChange("");
            ref.current?.focus();
          }}
          aria-label="Clear filter"
          className="absolute right-0.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.06] hover:text-[var(--admin-text)] focus-visible:bg-white/[0.06] focus-visible:text-[var(--admin-text)] focus-visible:outline-none pointer-coarse:h-10 pointer-coarse:w-10"
        >
          <X size={12} aria-hidden />
        </button>
      ) : (
        <span
          aria-hidden
          className="admin-kbd pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 pointer-coarse:hidden"
        >
          /
        </span>
      )}
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
        <Toggle label="Live tail" checked={props.live} onChange={props.onToggle} />
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
  /** Extra classes for the wrapper — `admin-table-logs` tightens the gutters on
   *  narrow viewports. */
  className?: string;
  /** Per-column class applied to the matching `<th>`, so a caller can drop a
   *  low-priority column below a breakpoint (`hidden lg:table-cell`) and hide
   *  the matching `<td>` with the same class. */
  colClass?: (string | undefined)[];
}) {
  return (
    <div className={`admin-table ${props.className ?? ""}`}>
      <div
        className="admin-scroll overflow-auto"
        style={props.maxHeight ? { maxHeight: `${props.maxHeight}px` } : undefined}
      >
        <table className="w-full text-left">
          <thead>
            <tr>
              {props.head.map((h, i) => (
                <th key={i} className={props.colClass?.[i]}>
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
      // `group` lets a row's trailing affordance appear on hover *and* on
      // keyboard focus — the hover-only reveal was invisible to a tab user.
      className={`group [content-visibility:auto] [contain-intrinsic-size:auto_37px] ${zebraCls}${
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
export function NumCell(props: { children: ReactNode; className?: string }) {
  return (
    <span className={`font-mono tabular-nums ${props.className ?? ""}`}>{props.children}</span>
  );
}

/** Status pill tinted by response class, with a dot of the same colour.
 *
 *  2xx green, 3xx blue, 4xx amber, 5xx red — a 4xx (our caller's request was
 *  wrong) and a 5xx (the upstream failed) are different incidents, and the
 *  previous all-red-over-400 pill hid that distinction in the one column a
 *  reader scans first. */
export function StatusBadge(props: { status: number; errorCode?: string }) {
  const tone: "green" | "blue" | "amber" | "red" =
    props.status >= 500
      ? "red"
      : props.status >= 400
        ? "amber"
        : props.status >= 300
          ? "blue"
          : "green";
  return (
    <span
      className="inline-flex max-w-[220px] items-center gap-1.5"
      title={props.errorCode || undefined}
    >
      <Badge tone={tone}>
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
        {props.status}
      </Badge>
      {props.status >= 400 && props.errorCode && (
        <span
          className="max-w-[32px] truncate font-mono text-[10px] tracking-wide text-[var(--admin-text-dim)] sm:max-w-[180px]"
        >
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

// -- loading skeleton ----------------------------------------------------------

/** Shimmer placeholder shaped like log rows.
 *
 *  The first paint is one fetch away. Six row-shaped placeholders say "loading"
 *  where a single spinner centred in a 640px-tall empty region reads as
 *  "nothing here" — and the row rhythm is the same one the table will have, so
 *  the page does not jump when the data lands. */
export function LogsSkeleton(props: { rows?: number; className?: string }) {
  const rows = props.rows ?? 6;
  // Deliberately uneven: identical bars read as a rendering artefact.
  const widths = ["w-[38%]", "w-[61%]", "w-[45%]", "w-[54%]", "w-[32%]", "w-[49%]"];
  return (
    <Card className={`p-4 ${props.className ?? ""}`}>
      <div className="space-y-3.5" aria-hidden>
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="flex items-center gap-4">
            <span className="admin-skeleton h-3 w-14 shrink-0" />
            <span className="admin-skeleton h-3 w-24 shrink-0" />
            <span className={`admin-skeleton h-3 ${widths[i % widths.length]}`} />
            <span className="admin-skeleton ml-auto h-3 w-12 shrink-0" />
          </div>
        ))}
      </div>
      <p role="status" className="sr-only">
        Loading logs…
      </p>
    </Card>
  );
}
