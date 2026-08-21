// Request logs — ring-backed table with client-side filters, optional SSE live
// tail, and a detail dialog with full metadata + retry-chain breakdown.

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getRequestLogs } from "@/api/client";
import { useAdminStream } from "@/api/stream";
import type { Attempt, RequestLogEntry } from "@/api/types";
import {
  Badge,
  Card,
  Dialog,
  EmptyState,
  ErrorText,
  PageHeader,
  Select,
  Spinner,
} from "@/components/ui";
import { fmtDateTime, fmtTime, fmtTokens, fmtUsd } from "@/lib/format";
import {
  LiveTail,
  LogRow,
  LogsFooter,
  LogsTable,
  LogsToolbar,
  LogTD,
  MonoCell,
  NumCell,
  SearchInput,
  StatusBadge,
} from "./logs-shared";

type StatusFilter = "all" | "2xx" | "4xx" | "5xx";
type RangeFilter = "all" | "5m" | "15m" | "1h";

const RANGE_SECS: Record<Exclude<RangeFilter, "all">, number> = {
  "5m": 300,
  "15m": 900,
  "1h": 3600,
};

const CANONICAL_SURFACES = ["chat", "messages", "responses", "embeddings"];

const EMPTY: RequestLogEntry[] = [];

function asRequestEntry(data: unknown): RequestLogEntry | null {
  if (typeof data !== "object" || data === null) return null;
  const d = data as Record<string, unknown>;
  return typeof d.request_id === "string" && typeof d.ts === "number"
    ? (data as RequestLogEntry)
    : null;
}

function statusIn(status: number, filter: StatusFilter): boolean {
  if (filter === "2xx") return status >= 200 && status < 300;
  if (filter === "4xx") return status >= 400 && status < 500;
  if (filter === "5xx") return status >= 500;
  return true;
}

/** Distinct values in stable order; `preferred` pins canonical entries first. */
function distinctOptions(
  logs: RequestLogEntry[],
  keyOf: (l: RequestLogEntry) => string,
  allLabel: string,
  preferred?: string[],
): { value: string; label: string }[] {
  const present = new Set<string>();
  for (const l of logs) {
    const v = keyOf(l);
    if (v) present.add(v);
  }
  const ordered = preferred
    ? [
        ...preferred.filter((v) => present.has(v)),
        ...[...present].filter((v) => !preferred.includes(v)).sort(),
      ]
    : [...present].sort();
  return [{ value: "all", label: allLabel }, ...ordered.map((v) => ({ value: v, label: v }))];
}

function RetryChain(props: { attempts: Attempt[] }) {
  const steps = props.attempts;
  return (
    <div className="mt-4 border-t border-[var(--admin-border)] pt-3">
      <h4 className="admin-label mb-2">Retry chain</h4>
      {steps.length <= 1 ? (
        <p className="text-[13px] text-[var(--admin-text-dim)]">no retries (single attempt)</p>
      ) : (
        <ol className="space-y-1.5">
          {steps.map((a, i) => {
            const ok = a.status === "ok";
            return (
              <li key={i} className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className="w-4 text-right font-mono tabular-nums text-[var(--admin-text-dim)]">
                  {i + 1}.
                </span>
                <span className="font-medium text-[var(--admin-text)]">{a.deployment}</span>
                <MonoCell className="text-[var(--admin-text-dim)]">
                  {a.provider} · {a.key}
                </MonoCell>
                <Badge tone={ok ? "green" : "red"}>{a.status}</Badge>
                <NumCell>
                  <span className="text-[11px] text-[var(--admin-text-dim)]">{a.latency_ms}ms</span>
                </NumCell>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

function Meta(props: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="admin-label pt-1">{props.label}</dt>
      <dd className="min-w-0 break-words text-[13px] text-[var(--admin-text)]">{props.children}</dd>
    </>
  );
}

export function RequestLogsPage() {
  const qc = useQueryClient();
  const [live, setLive] = useState(false);
  const [q, setQ] = useState("");
  const [model, setModel] = useState("all");
  const [providerSel, setProviderSel] = useState("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [surface, setSurface] = useState("all");
  const [range, setRange] = useState<RangeFilter>("all");
  const [selected, setSelected] = useState<RequestLogEntry | null>(null);

  const query = useQuery({
    queryKey: ["request-logs"],
    queryFn: async () => {
      const { logs } = await getRequestLogs();
      return [...logs].reverse(); // API returns oldest→newest; keep newest-first for display
    },
    refetchInterval: 15_000,
  });

  const connected = useAdminStream("log.created", (data) => {
    if (!live) return;
    const evt = asRequestEntry(data);
    if (!evt) return;
    qc.setQueryData<RequestLogEntry[]>(["request-logs"], (old) => {
      if (!old) return old;
      const first = old[0];
      if (first && first.request_id === evt.request_id && first.ts === evt.ts) return old; // poll will bring it
      return [evt, ...old].slice(0, 500);
    });
  });

  const all = query.data ?? EMPTY;

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const minTs = range === "all" ? 0 : Date.now() / 1000 - RANGE_SECS[range];
    return all.filter((l) => {
      if (l.ts < minTs) return false;
      if (model !== "all" && l.model_group !== model) return false;
      if (providerSel !== "all" && l.provider !== providerSel) return false;
      if (surface !== "all" && l.surface !== surface) return false;
      if (!statusIn(l.status, status)) return false;
      if (needle) {
        const hay =
          `${l.key_alias}\n${l.model_group}\n${l.provider}\n${l.request_id}`.toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [all, q, model, providerSel, status, surface, range]);

  const modelOpts = useMemo(() => distinctOptions(all, (l) => l.model_group, "All models"), [all]);
  const providerOpts = useMemo(() => distinctOptions(all, (l) => l.provider, "All providers"), [all]);
  const surfaceOpts = useMemo(
    () => distinctOptions(all, (l) => l.surface, "All surfaces", CANONICAL_SURFACES),
    [all],
  );

  return (
    <div>
      <PageHeader
        title="Request logs"
        subtitle="Gateway requests, newest first"
        right={<LiveTail live={live} onToggle={setLive} connected={connected} />}
      />

      <LogsToolbar>
        <SearchInput
          value={q}
          onChange={setQ}
          placeholder="filter key / model / provider / request id…"
        />
        <Select value={model} onChange={setModel} options={modelOpts} />
        <Select value={providerSel} onChange={setProviderSel} options={providerOpts} />
        <Select
          value={status}
          onChange={(v) => setStatus(v as StatusFilter)}
          options={[
            { value: "all", label: "All statuses" },
            { value: "2xx", label: "2xx" },
            { value: "4xx", label: "4xx" },
            { value: "5xx", label: "5xx" },
          ]}
        />
        <Select value={surface} onChange={setSurface} options={surfaceOpts} />
        <Select
          value={range}
          onChange={(v) => setRange(v as RangeFilter)}
          options={[
            { value: "all", label: "All time" },
            { value: "5m", label: "Last 5 minutes" },
            { value: "15m", label: "Last 15 minutes" },
            { value: "1h", label: "Last hour" },
          ]}
        />
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
            {all.length === 0 ? "No requests logged yet." : "No events match the current filters."}
          </EmptyState>
        </Card>
      )}

      {filtered.length > 0 && (
        <Card>
          <LogsTable
            head={[
              "Time",
              "Key",
              "Model",
              "Provider / key",
              "Status",
              <span key="ti" className="block text-right">Tok in</span>,
              <span key="tc" className="block text-right">Tok cached</span>,
              <span key="tr" className="block text-right">Tok reasoning</span>,
              <span key="to" className="block text-right">Tok out</span>,
              <span key="tps" className="block text-right">TPS</span>,
              <span key="ttft" className="block text-right">TTFT</span>,
              <span key="lat" className="block text-right">Latency</span>,
              <span key="cost" className="block text-right">Cost</span>,
              "Cache",
            ]}
          >
            {filtered.map((l) => (
              <LogRow
                key={`${l.request_id}:${l.ts}`}
                ariaLabel={`request ${l.request_id}`}
                onClick={() => setSelected(l)}
              >
                <LogTD className="font-mono text-[12px] text-[var(--admin-text-dim)]">{fmtTime(l.ts)}</LogTD>
                <LogTD className="font-medium">{l.key_alias || "—"}</LogTD>
                <LogTD>{l.model_group || "—"}</LogTD>
                <LogTD className="text-[var(--admin-text-muted)]">
                  {l.provider} / {l.provider_key_label}
                </LogTD>
                <LogTD>
                  <StatusBadge status={l.status} errorCode={l.error_code} />
                </LogTD>
                <LogTD className="text-right">
                  <NumCell>{fmtTokens(l.tok_in)}</NumCell>
                </LogTD>
                <LogTD className="text-right">
                  <NumCell>{fmtTokens(l.tok_cached)}</NumCell>
                </LogTD>
                <LogTD className="text-right">
                  <NumCell>{fmtTokens(l.tok_reasoning)}</NumCell>
                </LogTD>
                <LogTD className="text-right">
                  <NumCell>{fmtTokens(l.tok_out)}</NumCell>
                </LogTD>
                <LogTD className="text-right">
                  {l.tps > 0 ? (
                    <NumCell>{l.tps.toFixed(1)}</NumCell>
                  ) : (
                    <span className="font-mono text-[var(--admin-text-dim)]">—</span>
                  )}
                </LogTD>
                <LogTD className="text-right">
                  {l.ttft_ms > 0 ? (
                    <NumCell>{Math.round(l.ttft_ms)}</NumCell>
                  ) : (
                    <span className="font-mono text-[var(--admin-text-dim)]">—</span>
                  )}
                </LogTD>
                <LogTD className="text-right">
                  <NumCell>{Math.round(l.latency_ms)}</NumCell>
                </LogTD>
                <LogTD className="text-right">
                  <NumCell>{fmtUsd(l.cost)}</NumCell>
                </LogTD>
                <LogTD>{l.cache_hit && <Badge tone="green">cached</Badge>}</LogTD>
              </LogRow>
            ))}
          </LogsTable>
        </Card>
      )}

      <div className="mt-2 px-1 font-mono text-[11px] text-[var(--admin-text-dim)]">
        <LogsFooter shown={filtered.length} total={all.length} />
      </div>

      <Dialog
        open={selected !== null}
        title={`Request ${selected?.request_id ?? ""}`}
        onClose={() => setSelected(null)}
        wide
      >
        {selected && (
          <>
            <dl className="grid grid-cols-[130px_1fr] items-baseline gap-x-3 gap-y-1.5">
              <Meta label="Received">{fmtDateTime(selected.ts)}</Meta>
              <Meta label="Key">{selected.key_alias || "—"}</Meta>
              <Meta label="Model">{selected.model_group || "—"}</Meta>
              <Meta label="Provider">
                {selected.provider} / {selected.provider_key_label}
              </Meta>
              <Meta label="Status">
                <StatusBadge status={selected.status} errorCode={selected.error_code} />
              </Meta>
              <Meta label="Error code">
                {selected.error_code || "—"}
              </Meta>
              <Meta label="Surface">
                {selected.surface}
                {selected.was_stream ? " (stream)" : ""}
              </Meta>
              <Meta label="Stream">{selected.was_stream ? "yes" : "no"}</Meta>
              <Meta label="Request ID">
                <MonoCell>{selected.request_id}</MonoCell>
              </Meta>
              <Meta label="TTFT">{Math.round(selected.ttft_ms)}ms</Meta>
              <Meta label="Latency">{Math.round(selected.latency_ms)}ms</Meta>
              <Meta label="TPS">{selected.tps > 0 ? selected.tps.toFixed(1) : "—"}</Meta>
              <Meta label="Tokens">
                {fmtTokens(selected.tok_in)} in · {fmtTokens(selected.tok_cached)} cached ·{" "}
                {fmtTokens(selected.tok_reasoning)} reasoning · {fmtTokens(selected.tok_out)} out
              </Meta>
              <Meta label="Cost">{fmtUsd(selected.cost)}</Meta>
              <Meta label="Cache savings">{fmtUsd(selected.cache_savings)}</Meta>
            </dl>
            <RetryChain attempts={selected.attempts} />
          </>
        )}
      </Dialog>
    </div>
  );
}
