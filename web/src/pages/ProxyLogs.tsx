// Proxy logs — internal gateway log stream with level filter chips (with
// count badges), expandable message rows, and a live SSE tail (on by default).

import { useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Inbox, Terminal } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getProxyLogs } from "@/api/client";
import { useAdminStream } from "@/api/stream";
import type { ProxyLogEntry } from "@/api/types";
import { Badge, Card, EmptyState, ErrorText, PageHeader, Spinner } from "@/components/ui";
import {
  LiveTail,
  LogRow,
  LogsFooter,
  LogsTable,
  LogsToolbar,
  LogTD,
  MonoCell,
  TimeAgo,
  useNow,
} from "./logs-shared";

const LEVELS = ["debug", "info", "warn", "error"] as const;
type Level = (typeof LEVELS)[number];
type LevelFilter = "all" | Level;

const LEVEL_TONE: Record<Level, "gray" | "blue" | "amber" | "red"> = {
  debug: "gray",
  info: "blue",
  warn: "amber",
  error: "red",
};

const ACTIVE_CHIP: Record<LevelFilter, string> = {
  all: "border-white/[0.14] bg-white/[0.08] text-[var(--admin-text)]",
  debug: "border-zinc-500/30 bg-zinc-500/15 text-zinc-300",
  info: "border-blue-500/30 bg-blue-500/[0.12] text-blue-300",
  warn: "border-amber-500/30 bg-amber-500/[0.12] text-amber-300",
  error: "border-red-500/30 bg-red-500/[0.12] text-red-300",
};

const IDLE_CHIP =
  "border-[var(--admin-border)] text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text-muted)]";

/** Subtle left-border accent per level for visual scanning. */
const LEVEL_BAR: Record<Level, string> = {
  debug: "border-l-zinc-600/40",
  info: "border-l-blue-500/40",
  warn: "border-l-amber-500/40",
  error: "border-l-red-500/50",
};

const EMPTY: ProxyLogEntry[] = [];

function asProxyEntry(data: unknown): ProxyLogEntry | null {
  if (typeof data !== "object" || data === null) return null;
  const d = data as Record<string, unknown>;
  if (typeof d.ts !== "number" || typeof d.message !== "string") return null;
  const level = d.level;
  if (level !== "debug" && level !== "info" && level !== "warn" && level !== "error") return null;
  return data as ProxyLogEntry;
}

export function ProxyLogsPage() {
  const qc = useQueryClient();
  const [live, setLive] = useState(true); // live tail on by default for proxy logs
  const [level, setLevel] = useState<LevelFilter>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const now = useNow(10_000);

  const query = useQuery({
    queryKey: ["proxy-logs"],
    queryFn: async () => {
      const { logs } = await getProxyLogs();
      // Server already returns newest-first (reversed ring);
      // do NOT re-reverse — that was the root cause of stale/old logs display.
      return logs;
    },
    refetchInterval: 15_000,
  });

  const connected = useAdminStream("proxy.log", (data) => {
    if (!live) return;
    const evt = asProxyEntry(data);
    if (!evt) return;
    qc.setQueryData<ProxyLogEntry[]>(["proxy-logs"], (old) => {
      if (!old) return [evt];
      // Dedupe by composite key on the first few entries to avoid
      // prepending an event the poll already delivered.
      const recent = old.slice(0, 5);
      const dup = recent.some(
        (l) =>
          l.ts === evt.ts &&
          l.level === evt.level &&
          l.message === evt.message &&
          (l.request_id ?? "") === (evt.request_id ?? ""),
      );
      if (dup) return old;
      return [evt, ...old].slice(0, 500);
    });
  });

  const all = query.data ?? EMPTY;

  const filtered = useMemo(
    () => (level === "all" ? all : all.filter((l) => l.level === level)),
    [all, level],
  );

  // Count per level for the filter chip badges.
  const levelCounts = useMemo(() => {
    const counts: Record<Level, number> = { debug: 0, info: 0, warn: 0, error: 0 };
    for (const l of all) counts[l.level]++;
    return counts;
  }, [all]);

  const toggleExpand = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const chipCls = (lv: LevelFilter) =>
    `h-7 rounded-full border px-3 text-xs font-medium transition-colors inline-flex items-center gap-1.5 ${
      level === lv ? ACTIVE_CHIP[lv] : IDLE_CHIP
    }`;

  return (
    <div>
      <PageHeader
        title="Proxy logs"
        subtitle="Internal gateway log stream, newest first"
        right={<LiveTail live={live} onToggle={setLive} connected={connected} />}
      />

      <LogsToolbar>
        <div className="flex flex-wrap items-center gap-1.5">
          {(["all", ...LEVELS] as LevelFilter[]).map((lv) => {
            const count = lv === "all" ? all.length : levelCounts[lv as Level];
            return (
              <button key={lv} type="button" className={chipCls(lv)} onClick={() => setLevel(lv)}>
                {lv}
                {count > 0 && (
                  <span
                    className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${
                      level === lv
                        ? "bg-white/[0.12] text-current"
                        : "bg-white/[0.04] text-[var(--admin-text-dim)]"
                    }`}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </LogsToolbar>

      {query.isLoading && (
        <div className="py-12">
          <Spinner />
        </div>
      )}
      {query.error && <ErrorText>{query.error.message}</ErrorText>}

      {!query.isLoading && !query.error && filtered.length === 0 && (
        <Card>
          <EmptyState>
            <span className="mb-2 flex justify-center text-[var(--admin-text-dim)] opacity-50">
              {all.length === 0 ? <Terminal size={20} aria-hidden /> : <Inbox size={20} aria-hidden />}
            </span>
            {all.length === 0 ? "No proxy events yet." : "No events at this level."}
          </EmptyState>
        </Card>
      )}

      {filtered.length > 0 && (
        <Card>
          <LogsTable head={["Time", "Level", "Message", "Request ID"]} maxHeight={640}>
            {filtered.map((l, i) => {
              const key = `${l.ts}:${l.message}:${i}`;
              const isOpen = expanded.has(key);
              const hasExtra = !!(l.request_id || l.message.length > 120);
              return (
                <ProxyRow
                  key={key}
                  log={l}
                  zebra={i}
                  isOpen={isOpen}
                  hasExtra={hasExtra}
                  nowMs={now}
                  onToggle={() => toggleExpand(key)}
                />
              );
            })}
          </LogsTable>
        </Card>
      )}

      <div className="mt-2 px-1 font-mono text-[11px] text-[var(--admin-text-dim)]">
        <LogsFooter shown={filtered.length} total={all.length} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ProxyRow — a single proxy log row with optional expandable detail.
// ---------------------------------------------------------------------------

function ProxyRow(props: {
  log: ProxyLogEntry;
  zebra: number;
  isOpen: boolean;
  hasExtra: boolean;
  nowMs: number;
  onToggle: () => void;
}) {
  const { log: l, zebra, isOpen, hasExtra, nowMs, onToggle } = props;
  return (
    <>
      <LogRow
        zebra={zebra}
        ariaLabel={`proxy log ${l.level}`}
        onClick={hasExtra ? onToggle : undefined}
      >
        {/* Level accent bar */}
        <LogTD className={`border-l-2 ${LEVEL_BAR[l.level]} pl-3`} />
        <TimeAgo ts={l.ts} nowMs={nowMs} />
        <LogTD>
          <Badge tone={LEVEL_TONE[l.level]}>{l.level}</Badge>
        </LogTD>
        <LogTD className="max-w-[520px]">
          <div className="flex items-start gap-1">
            {hasExtra && (
              <span
                className="mt-0.5 shrink-0 text-[var(--admin-text-dim)]"
                aria-hidden
              >
                {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              </span>
            )}
            <div className={`truncate ${l.level === "error" ? "text-red-300" : ""}`} title={l.message}>
              {l.message.replace(/\s+/g, " ").trim()}
            </div>
          </div>
        </LogTD>
        <LogTD>
          {l.request_id ? (
            <MonoCell className="text-[var(--admin-text-dim)]">{l.request_id}</MonoCell>
          ) : (
            <span className="font-mono text-[var(--admin-text-dim)]">—</span>
          )}
        </LogTD>
      </LogRow>
      {isOpen && hasExtra && (
        <tr className={`bg-white/[0.02] ${zebra % 2 === 1 ? "bg-white/[0.012]" : ""}`}>
          <LogTD colSpan={5} className="py-3 pl-8">
            <div className="space-y-2">
              {l.message.length > 120 && (
                <div>
                  <span className="admin-label mb-1 block">Full message</span>
                  <pre className="whitespace-pre-wrap break-words rounded-lg border border-[var(--admin-border)] bg-black/30 p-3 font-mono text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                    {l.message}
                  </pre>
                </div>
              )}
              {l.request_id && (
                <div className="flex items-center gap-2">
                  <span className="admin-label">Request ID</span>
                  <MonoCell className="text-[var(--admin-text-muted)]">{l.request_id}</MonoCell>
                </div>
              )}
              {l.level === "error" && (
                <div className="flex items-center gap-1.5 text-[12px] text-red-400">
                  <AlertTriangle size={12} />
                  Error level event
                </div>
              )}
            </div>
          </LogTD>
        </tr>
      )}
    </>
  );
}
