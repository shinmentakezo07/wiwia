// Request logs — ring-backed table with client-side filters, optional SSE live
// tail, and a slide-in detail drawer with sectioned metadata + retry-chain.

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowLeftRight,
  Brain,
  Clock,
  Copy,
  Check,
  Cpu,
  DollarSign,
  Gauge,
  Hash,
  Inbox,
  Layers,
  MessageSquare,
  Repeat,
  Server,
  Timer,
  Wrench,
  Zap,
  X,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getRequestLogs } from "@/api/client";
import { useAdminStream } from "@/api/stream";
import type { Attempt, RequestLogEntry } from "@/api/types";
import {
  Badge,
  Card,
  Drawer,
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

/** Human-readable latency: seconds at 1s+, milliseconds below. */
function fmtLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
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

// ---------------------------------------------------------------------------
// Detail drawer sub-components
// ---------------------------------------------------------------------------

/** A compact metric pill with icon, label, and mono value. */
function MetricPill(props: {
  icon: ReactNode;
  label: string;
  value: string;
  dim?: boolean;
}) {
  return (
    <div className={`admin-metric-pill ${props.dim ? "opacity-50" : ""}`}>
      <span className="admin-metric-pill-label">{props.label}</span>
      <span className="flex items-center gap-1.5">
        <span className="text-[var(--admin-text-dim)]" aria-hidden>
          {props.icon}
        </span>
        {props.value}
      </span>
    </div>
  );
}

/** A labelled metadata row used in the "Request details" section. */
function DetailRow(props: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 py-1.5">
      <span className="w-[130px] shrink-0 text-[11px] font-medium text-[var(--admin-text-dim)]">
        {props.label}
      </span>
      <span className="min-w-0 flex-1 break-words text-[13px] text-[var(--admin-text)]">
        {props.children}
      </span>
    </div>
  );
}

/** A sectioned block with a header and body, used inside the drawer. */
function DetailSection(props: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
  right?: ReactNode;
}) {
  return (
    <section className="admin-detail-section">
      <header className="admin-detail-section-head flex items-center justify-between">
        <span className="flex items-center gap-2 text-[12px] font-semibold text-[var(--admin-text)]">
          <span className="text-[var(--admin-text-dim)]" aria-hidden>
            {props.icon}
          </span>
          {props.title}
        </span>
        {props.right}
      </header>
      <div className="px-3.5 py-2.5">{props.children}</div>
    </section>
  );
}

/** Inline copy button — icon-only, minimal footprint inside the drawer. */
function InlineCopy(props: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(props.text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="shrink-0 rounded-md p-1 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
      aria-label="Copy"
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
    </button>
  );
}

function RetryChain(props: { attempts: Attempt[] }) {
  const steps = props.attempts;
  return (
    <div className="px-3.5 py-3">
      {steps.length <= 1 ? (
        <p className="text-[12px] text-[var(--admin-text-dim)]">Single attempt · no retries</p>
      ) : (
        <ol className="ml-1 space-y-3 border-l border-[var(--admin-border)] pl-4">
          {steps.map((a, i) => {
            const ok = a.status === "ok";
            return (
              <li key={i} className="relative">
                <span
                  aria-hidden
                  className={`absolute -left-[21px] top-[5px] h-2 w-2 rounded-full ${
                    ok ? "bg-emerald-400" : "bg-red-400"
                  }`}
                />
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[13px]">
                  <span className="font-medium text-[var(--admin-text)]">{a.deployment}</span>
                  <MonoCell className="text-[11px] text-[var(--admin-text-dim)]">
                    {a.provider} · {a.key}
                  </MonoCell>
                  <Badge tone={ok ? "green" : "red"}>{a.status}</Badge>
                  <span className="ml-auto font-mono text-[11px] tabular-nums text-[var(--admin-text-dim)]">
                    {Math.round(a.latency_ms)}ms
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/** Pretty-printed messages from a request body (OpenAI/Anthropic-shaped). */
function PrettyMessages(props: { body: RequestLogEntry["request_body"] }) {
  const body = props.body;
  if (!body || typeof body !== "object") return null;
  const messages = (body as Record<string, unknown>).messages;
  if (!Array.isArray(messages)) return null;
  return (
    <div className="space-y-2">
      {messages.map((msg, i) => {
        if (typeof msg !== "object" || msg === null) return null;
        const m = msg as Record<string, unknown>;
        const role = String(m.role ?? "unknown");
        let content = "";
        const c = m.content;
        if (typeof c === "string") {
          content = c;
        } else if (Array.isArray(c)) {
          content = c
            .map((p) => {
              if (typeof p === "string") return p;
              if (typeof p === "object" && p !== null) {
                const part = p as Record<string, unknown>;
                if (typeof part.text === "string") return part.text;
                if (part.type === "image_url") return "[image]";
                return `[${String(part.type ?? "part")}]`;
              }
              return "";
            })
            .join("\n");
        }
        const roleTone: Record<string, string> = {
          user: "text-blue-400",
          assistant: "text-emerald-400",
          system: "text-amber-400",
          tool: "text-violet-400",
        };
        return (
          <div
            key={i}
            className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-2.5"
          >
            <div className="mb-1 flex items-center gap-1.5">
              <span className={`text-[11px] font-semibold uppercase tracking-wide ${roleTone[role] ?? "text-zinc-400"}`}>
                {role}
              </span>
            </div>
            <p className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
              {content || "(empty)"}
            </p>
          </div>
        );
      })}
    </div>
  );
}

/** Pretty-printed response content (text, thinking, tool calls). */
function PrettyResponse(props: { resp: Record<string, unknown> }) {
  const r = props.resp;
  const text = typeof r.text === "string" ? r.text : "";
  const thinking = Array.isArray(r.thinking) ? r.thinking : [];
  const toolCalls = Array.isArray(r.tool_calls) ? r.tool_calls : [];
  return (
    <div className="space-y-2">
      {thinking.length > 0 && (
        <div className="rounded-lg border border-violet-500/15 bg-violet-500/[0.04] p-2.5">
          <div className="mb-1 flex items-center gap-1.5">
            <Brain size={12} className="text-violet-400" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-violet-400">Thinking</span>
          </div>
          <p className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-violet-300/80">
            {thinking.map((t) => (typeof t === "object" && t !== null ? String((t as Record<string, unknown>).text ?? "") : "")).join("\n")}
          </p>
        </div>
      )}
      {text && (
        <div className="rounded-lg border border-emerald-500/15 bg-emerald-500/[0.04] p-2.5">
          <div className="mb-1 flex items-center gap-1.5">
            <MessageSquare size={12} className="text-emerald-400" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-emerald-400">Response</span>
          </div>
          <p className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
            {text}
          </p>
        </div>
      )}
      {toolCalls.length > 0 && (
        <div className="space-y-1.5">
          {toolCalls.map((tc, i) => {
            const call = typeof tc === "object" && tc !== null ? tc as Record<string, unknown> : {};
            const args = call.arguments;
            const argsStr = typeof args === "string" ? args : JSON.stringify(args, null, 2);
            return (
              <div key={i} className="rounded-lg border border-blue-500/15 bg-blue-500/[0.04] p-2.5">
                <div className="mb-1 flex items-center gap-1.5">
                  <Wrench size={12} className="text-blue-400" />
                  <span className="font-mono text-[11px] font-semibold text-blue-400">{String(call.name ?? "tool")}</span>
                  <span className="font-mono text-[10px] text-[var(--admin-text-dim)]">({String(call.id ?? "")})</span>
                </div>
                <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-[var(--admin-text-muted)]">
                  {argsStr}
                </pre>
              </div>
            );
          })}
        </div>
      )}
      {!text && thinking.length === 0 && toolCalls.length === 0 && (
        <p className="py-3 text-center text-[12px] italic text-[var(--admin-text-dim)]">
          No response content captured
        </p>
      )}
    </div>
  );
}

/** Tabbed Request/Response viewer with pretty and JSON modes. */
function PromptResponseSection(props: { log: RequestLogEntry }) {
  const { log } = props;
  const [tab, setTab] = useState<"request" | "response">("request");
  const [mode, setMode] = useState<"pretty" | "json">("pretty");

  const hasRequest = log.request_body != null;
  const hasResponse = log.response_body != null;

  if (!hasRequest && !hasResponse) {
    return (
      <DetailSection title="Request & Response" icon={<ArrowLeftRight size={13} />}>
        <div className="py-4 text-center">
          <p className="text-[12px] text-[var(--admin-text-dim)]">
            Prompt logging is disabled.
          </p>
          <p className="mt-1 text-[11px] text-[var(--admin-text-dim)]">
            Enable <code className="rounded bg-white/[0.04] px-1 py-0.5 font-mono text-[10px]">store_prompts_in_spend_logs</code> in wiwi.yaml to capture request/response content.
          </p>
        </div>
      </DetailSection>
    );
  }

  const activeTab = tab === "request" && !hasRequest ? "response" : tab;

  return (
    <DetailSection
      title="Request & Response"
      icon={<ArrowLeftRight size={13} />}
      right={
        <div className="flex items-center gap-1">
          {hasRequest && hasResponse && (
            <div className="flex rounded-md border border-[var(--admin-border)] p-0.5">
              <button
                type="button"
                onClick={() => setTab("request")}
                className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${
                  activeTab === "request"
                    ? "bg-white/[0.06] text-[var(--admin-text)]"
                    : "text-[var(--admin-text-dim)] hover:text-[var(--admin-text-muted)]"
                }`}
              >
                Request
              </button>
              <button
                type="button"
                onClick={() => setTab("response")}
                className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${
                  activeTab === "response"
                    ? "bg-white/[0.06] text-[var(--admin-text)]"
                    : "text-[var(--admin-text-dim)] hover:text-[var(--admin-text-muted)]"
                }`}
              >
                Response
              </button>
            </div>
          )}
          <div className="flex rounded-md border border-[var(--admin-border)] p-0.5">
            <button
              type="button"
              onClick={() => setMode("pretty")}
              className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${
                mode === "pretty"
                  ? "bg-white/[0.06] text-[var(--admin-text)]"
                  : "text-[var(--admin-text-dim)] hover:text-[var(--admin-text-muted)]"
              }`}
            >
              Pretty
            </button>
            <button
              type="button"
              onClick={() => setMode("json")}
              className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${
                mode === "json"
                  ? "bg-white/[0.06] text-[var(--admin-text)]"
                  : "text-[var(--admin-text-dim)] hover:text-[var(--admin-text-muted)]"
              }`}
            >
              JSON
            </button>
          </div>
        </div>
      }
    >
      <div className="py-1">
        {activeTab === "request" && hasRequest && (
          mode === "pretty" ? (
            <PrettyMessages body={log.request_body} />
          ) : (
            <pre className="max-h-[400px] overflow-auto rounded-lg border border-[var(--admin-border)] bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-[var(--admin-text-muted)]">
              {JSON.stringify(log.request_body, null, 2)}
            </pre>
          )
        )}
        {activeTab === "response" && hasResponse && (() => {
          const rb = log.response_body;
          if (mode === "pretty" && rb !== null && typeof rb === "object") {
            return <PrettyResponse resp={rb as Record<string, unknown>} />;
          }
          return (
            <pre className="max-h-[400px] overflow-auto rounded-lg border border-[var(--admin-border)] bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-[var(--admin-text-muted)]">
              {JSON.stringify(rb, null, 2)}
            </pre>
          );
        })()}
      </div>
    </DetailSection>
  );
}

// ---------------------------------------------------------------------------
// The main page
// ---------------------------------------------------------------------------

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
        <Select value={model} onChange={setModel} options={modelOpts} className="h-8 w-[150px] text-[12px]" />
        <Select
          value={providerSel}
          onChange={setProviderSel}
          options={providerOpts}
          className="h-8 w-[160px] text-[12px]"
        />
        <Select
          value={status}
          onChange={(v) => setStatus(v as StatusFilter)}
          options={[
            { value: "all", label: "All statuses" },
            { value: "2xx", label: "2xx" },
            { value: "4xx", label: "4xx" },
            { value: "5xx", label: "5xx" },
          ]}
          className="h-8 w-[135px] text-[12px]"
        />
        <Select
          value={surface}
          onChange={setSurface}
          options={surfaceOpts}
          className="h-8 w-[145px] text-[12px]"
        />
        <Select
          value={range}
          onChange={(v) => setRange(v as RangeFilter)}
          options={[
            { value: "all", label: "All time" },
            { value: "5m", label: "Last 5 minutes" },
            { value: "15m", label: "Last 15 minutes" },
            { value: "1h", label: "Last hour" },
          ]}
          className="h-8 w-[155px] text-[12px]"
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
            <span className="mb-2 flex justify-center text-[var(--admin-text-dim)] opacity-50">
              <Inbox size={20} aria-hidden />
            </span>
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
                <LogTD className="font-mono text-[12px] text-[var(--admin-text-dim)]" title={fmtDateTime(l.ts)}>{fmtTime(l.ts)}</LogTD>
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
                    <NumCell>{fmtLatency(l.ttft_ms)}</NumCell>
                  ) : (
                    <span className="font-mono text-[var(--admin-text-dim)]">—</span>
                  )}
                </LogTD>
                <LogTD className="text-right">
                  <NumCell>{fmtLatency(l.latency_ms)}</NumCell>
                </LogTD>
                <LogTD className="text-right">
                  <span
                    className={`font-mono tabular-nums ${
                      l.cost > 0 ? "" : "text-[var(--admin-text-dim)]"
                    }`}
                  >
                    {fmtUsd(l.cost)}
                  </span>
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

      {/* ── Slide-in detail drawer ── */}
      <Drawer open={selected !== null} onClose={() => setSelected(null)} width={620}>
        {selected && (
          <LogDetail log={selected} onClose={() => setSelected(null)} />
        )}
      </Drawer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// LogDetail — the full content of the slide-in drawer
// ---------------------------------------------------------------------------

function LogDetail(props: { log: RequestLogEntry; onClose: () => void }) {
  const { log: l } = props;
  const ok = l.status < 400;

  return (
    <>
      {/* Drawer header */}
      <header className="flex items-start justify-between gap-3 border-b border-white/[0.06] px-5 py-4">
        <div className="min-w-0">
          <div className="mb-1.5 flex items-center gap-2">
            <StatusBadge status={l.status} errorCode={l.error_code} />
            {l.was_stream && <Badge tone="blue">stream</Badge>}
            {l.cache_hit && <Badge tone="green">cached</Badge>}
          </div>
          <h3 className="truncate text-[16px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            {l.model_group || "—"}
          </h3>
          <p className="mt-0.5 font-mono text-[11px] text-[var(--admin-text-dim)]">
            {fmtDateTime(l.ts)}
          </p>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={props.onClose}
          className="shrink-0 rounded-lg p-2 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
        >
          <X size={16} />
        </button>
      </header>

      {/* Scrollable body */}
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {/* Request ID row */}
        <div className="flex items-center gap-2 rounded-lg border border-[var(--admin-border)] bg-white/[0.02] px-3 py-2">
          <Hash size={13} className="shrink-0 text-[var(--admin-text-dim)]" />
          <span className="admin-label shrink-0">Request ID</span>
          <MonoCell className="min-w-0 flex-1 truncate text-[12px] text-[var(--admin-text-muted)]">
            {l.request_id}
          </MonoCell>
          <InlineCopy text={l.request_id} />
        </div>

        {/* Performance metrics */}
        <DetailSection title="Performance" icon={<Gauge size={13} />}>
          <div className="grid grid-cols-3 gap-2">
            <MetricPill
              icon={<Zap size={13} />}
              label="TTFT"
              value={l.ttft_ms > 0 ? fmtLatency(l.ttft_ms) : "—"}
              dim={l.ttft_ms === 0}
            />
            <MetricPill
              icon={<Timer size={13} />}
              label="Latency"
              value={fmtLatency(l.latency_ms)}
            />
            <MetricPill
              icon={<Cpu size={13} />}
              label="Tokens/sec"
              value={l.tps > 0 ? l.tps.toFixed(1) : "—"}
              dim={l.tps === 0}
            />
          </div>
        </DetailSection>

        {/* Token usage */}
        <DetailSection title="Token usage" icon={<Layers size={13} />}>
          <div className="grid grid-cols-4 gap-2">
            <MetricPill icon={<Layers size={13} />} label="Input" value={fmtTokens(l.tok_in)} />
            <MetricPill icon={<Clock size={13} />} label="Cached" value={fmtTokens(l.tok_cached)} dim={l.tok_cached === 0} />
            <MetricPill icon={<Cpu size={13} />} label="Reasoning" value={fmtTokens(l.tok_reasoning)} dim={l.tok_reasoning === 0} />
            <MetricPill icon={<Layers size={13} />} label="Output" value={fmtTokens(l.tok_out)} />
          </div>
        </DetailSection>

        {/* Cost */}
        <DetailSection title="Cost" icon={<DollarSign size={13} />}>
          <div className="grid grid-cols-2 gap-2">
            <MetricPill icon={<DollarSign size={13} />} label="Request cost" value={fmtUsd(l.cost)} dim={l.cost === 0} />
            <MetricPill icon={<DollarSign size={13} />} label="Cache savings" value={fmtUsd(l.cache_savings)} dim={l.cache_savings === 0} />
          </div>
        </DetailSection>

        {/* Request & Response content */}
        <PromptResponseSection log={l} />

        {/* Request details */}
        <DetailSection title="Request details" icon={<Server size={13} />}>
          <DetailRow label="Virtual key">{l.key_alias || "—"}</DetailRow>
          <DetailRow label="Provider">
            {l.provider} / {l.provider_key_label}
          </DetailRow>
          <DetailRow label="Surface">
            {l.surface}
            {l.was_stream ? " (stream)" : ""}
          </DetailRow>
          <DetailRow label="Error code">
            {l.error_code ? (
              <span className="font-mono text-[12px] text-[var(--admin-danger)]">{l.error_code}</span>
            ) : (
              <span className="text-[var(--admin-text-dim)]">—</span>
            )}
          </DetailRow>
        </DetailSection>

        {/* Retry chain */}
        <DetailSection
          title="Retry chain"
          icon={<Repeat size={13} />}
          right={
            l.attempts.length > 1 ? (
              <Badge tone={ok ? "green" : "red"}>{l.attempts.length} attempts</Badge>
            ) : undefined
          }
        >
          <RetryChain attempts={l.attempts} />
        </DetailSection>
      </div>
    </>
  );
}
