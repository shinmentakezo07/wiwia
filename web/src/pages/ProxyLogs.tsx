// Proxy logs — internal gateway log stream with level filter chips and a
// live SSE tail (on by default).

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getProxyLogs } from "@/api/client";
import { useAdminStream } from "@/api/stream";
import type { ProxyLogEntry } from "@/api/types";
import { Badge, Card, EmptyState, ErrorText, PageHeader, Spinner } from "@/components/ui";
import { fmtTime } from "@/lib/format";
import {
  LiveTail,
  LogRow,
  LogsFooter,
  LogsTable,
  LogsToolbar,
  LogTD,
  MonoCell,
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

  const query = useQuery({
    queryKey: ["proxy-logs"],
    queryFn: async () => {
      const { logs } = await getProxyLogs();
      return [...logs].reverse(); // API returns oldest→newest; keep newest-first for display
    },
    refetchInterval: 15_000,
  });

  const connected = useAdminStream("proxy.log", (data) => {
    if (!live) return;
    const evt = asProxyEntry(data);
    if (!evt) return;
    qc.setQueryData<ProxyLogEntry[]>(["proxy-logs"], (old) => {
      if (!old) return old;
      const first = old[0];
      // dedupe: the poll will deliver this event anyway
      if (
        first &&
        first.ts === evt.ts &&
        first.level === evt.level &&
        first.message === evt.message
      ) {
        return old;
      }
      return [evt, ...old].slice(0, 500);
    });
  });

  const all = query.data ?? EMPTY;

  const filtered = useMemo(
    () => (level === "all" ? all : all.filter((l) => l.level === level)),
    [all, level],
  );

  const chipCls = (lv: LevelFilter) =>
    `h-7 rounded-full border px-3 text-xs font-medium transition-colors ${
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
          {(["all", ...LEVELS] as LevelFilter[]).map((lv) => (
            <button key={lv} type="button" className={chipCls(lv)} onClick={() => setLevel(lv)}>
              {lv}
            </button>
          ))}
        </div>
      </LogsToolbar>

      {query.isLoading && (
        <div className="py-8">
          <Spinner />
        </div>
      )}
      {query.error && <ErrorText>{query.error.message}</ErrorText>}

      {!query.isLoading && !query.error && filtered.length === 0 && (
        <Card>
          <EmptyState>
            {all.length === 0 ? "No proxy events yet." : "No events at this level."}
          </EmptyState>
        </Card>
      )}

      {filtered.length > 0 && (
        <Card>
          <LogsTable head={["Time", "Level", "Message", "Request ID"]}>
            {filtered.map((l) => (
              <LogRow key={`${l.ts}:${l.message}`} ariaLabel={`proxy log ${l.level}`}>
                <LogTD className="font-mono text-[12px] text-[var(--admin-text-dim)]">{fmtTime(l.ts)}</LogTD>
                <LogTD>
                  <Badge tone={LEVEL_TONE[l.level]}>{l.level}</Badge>
                </LogTD>
                <LogTD className="max-w-[520px]" title={l.message}>
                  <div className="truncate">{l.message.replace(/\s+/g, " ").trim()}</div>
                </LogTD>
                <LogTD>
                  {l.request_id ? (
                    <MonoCell className="text-[var(--admin-text-dim)]">{l.request_id}</MonoCell>
                  ) : (
                    <span className="font-mono text-[var(--admin-text-dim)]">—</span>
                  )}
                </LogTD>
              </LogRow>
            ))}
          </LogsTable>
        </Card>
      )}

      <div className="mt-2 px-1 font-mono text-[11px] text-[var(--admin-text-dim)]">
        <LogsFooter shown={filtered.length} total={all.length} />
      </div>
    </div>
  );
}
