// Request logs — ring-backed table with client-side filters, optional SSE live
// tail, a window overview band, and a slide-in detail drawer with sectioned
// metadata + retry chain.

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeftRight,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
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
  X,
  Zap,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getRequestLogs } from "@/api/client";
import { useAdminStream } from "@/api/stream";
import type { Attempt, RequestLogEntry } from "@/api/types";
import {
  LatencyRibbon,
  StatusMix,
  TokenRibbon,
  VisualCard,
  latencyProfile,
  statusSlices,
} from "@/components/console-visuals";
import { fmtDateTime, fmtInt, fmtPct, fmtTokens, fmtUsd, mean, p95 } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  Drawer,
  EmptyState,
  ErrorText,
  PageHeader,
  ProgressBar,
  Select,
} from "@/components/ui";
import {
  LiveTail,
  LogRow,
  LogsFooter,
  LogsSkeleton,
  LogsTable,
  LogTD,
  MonoCell,
  NumCell,
  SearchInput,
  StatusBadge,
  TimeAgo,
  useNow,
} from "./logs-shared";

type StatusFilter = "all" | "2xx" | "4xx" | "5xx";
type RangeFilter = "5m" | "15m" | "30m" | "1h" | "6h" | "24h" | "7d" | "all";

/** Lookback window per range. Defaults to 30 minutes — recent traffic is what
 *  this page is opened for, and a bounded default keeps the client-side filter
 *  (and the summary chips) meaningful on a busy gateway. */
const DEFAULT_RANGE: RangeFilter = "30m";

/** Rows rendered before the "show more" control appears. The window is applied
 *  client-side, so a wide range (24h+) can leave thousands of rows in view;
 *  every row is ~15 DOM nodes and the page stalls on each filter/poll without
 *  a cap. The summary chips still aggregate the whole filtered set. */
const ROWS_PER_PAGE = 250;

const RANGE_SECS: Record<Exclude<RangeFilter, "all">, number> = {
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1h": 3600,
  "6h": 21600,
  "24h": 86400,
  "7d": 604800,
};

const RANGE_OPTIONS: { value: RangeFilter; label: string }[] = [
  { value: "5m", label: "Last 5 minutes" },
  { value: "15m", label: "Last 15 minutes" },
  { value: "30m", label: "Last 30 minutes" },
  { value: "1h", label: "Last hour" },
  { value: "6h", label: "Last 6 hours" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "all", label: "All time" },
];

/** Range label for prose — "Last 30 minutes" → "last 30 minutes". */
function rangeLabel(range: RangeFilter): string {
  const label = RANGE_OPTIONS.find((o) => o.value === range)?.label ?? "selected range";
  return label.charAt(0).toLowerCase() + label.slice(1);
}

const CANONICAL_SURFACES = ["chat", "messages", "responses", "embeddings"];

/** Token-mix palette — the same four colours the Usage / Analytics pages use
 *  for input / cached / reasoning / output, so one strip reads the same
 *  wherever it appears. */
const TOKEN_COLORS = ["#3b82f6", "#199e70", "#a855f7", "#c98500"] as const;

const EMPTY: RequestLogEntry[] = [];

// ---------------------------------------------------------------------------
// Column layout
// ---------------------------------------------------------------------------

/** Per-column classes, shared by the `<th>` and its `<td>`s.
 *
 *  A request row carries fifteen columns — token counts, TTFT, throughput,
 *  cache state — which is roughly 1,600px wide. At a 375px viewport that is
 *  a row the reader has to scroll sideways through to find the status code,
 *  and on a 1,440px laptop the console's 300px sidebar leaves the table
 *  narrower than the full set anyway. So each column declares the narrowest
 *  viewport it still earns its width on, and everything below that is dropped
 *  rather than hidden behind a scroll: a phone shows time, model, status and
 *  latency, a laptop adds the routing and cost columns, and a wide desktop
 *  gets the token detail. The drawer always has the full picture. */
const COL = {
  key: "hidden sm:table-cell",
  provider: "hidden md:table-cell",
  tokIn: "hidden lg:table-cell text-right",
  tokCached: "hidden 2xl:table-cell text-right",
  tokReasoning: "hidden 2xl:table-cell text-right",
  tokOut: "hidden lg:table-cell text-right",
  tps: "hidden 2xl:table-cell text-right",
  ttft: "hidden md:table-cell text-right",
  latency: "text-right",
  cost: "hidden sm:table-cell text-right",
  cache: "hidden xl:table-cell",
  open: "hidden sm:table-cell",
} as const;
const HEAD: { label: string; cls?: string }[] = [
  { label: "Time" },
  { label: "Key", cls: COL.key },
  { label: "Model" },
  { label: "Provider / key", cls: COL.provider },
  { label: "Status" },
  { label: "Tok in", cls: COL.tokIn },
  { label: "Tok cached", cls: COL.tokCached },
  { label: "Tok reasoning", cls: COL.tokReasoning },
  { label: "Tok out", cls: COL.tokOut },
  { label: "Gen TPS", cls: COL.tps },
  { label: "TTFT", cls: COL.ttft },
  { label: "Latency", cls: COL.latency },
  { label: "Cost", cls: COL.cost },
  { label: "Cache", cls: COL.cache },
  { label: "", cls: COL.open },
];

/** Left-edge accent that mirrors the status tone, so a failing request is
 *  findable from the edge of the table without reading the badge. */
function accentBar(l: RequestLogEntry): string {
  if (l.status >= 500) return "border-l-red-500/60";
  if (l.status >= 400) return "border-l-amber-500/60";
  if (l.status < 300) return "border-l-emerald-500/45";
  return "border-l-transparent";
}

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
// Overview band
// ---------------------------------------------------------------------------

/** Segmented pill control — the status and range filters are flipped between
 *  constantly, so they read as one-click options rather than dropdown menus.
 *  Full labels in `title` tooltips; the pills themselves stay short. */
function SegmentControl(props: {
  value: string;
  options: { value: string; label: string; title?: string }[];
  onChange: (v: string) => void;
  ariaLabel: string;
}) {
  return (
    <div className="admin-segment" role="group" aria-label={props.ariaLabel}>
      {props.options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={props.value === o.value}
          title={o.title ?? o.label}
          onClick={() => props.onChange(o.value)}
          className="admin-segment-btn"
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

interface WindowStats {
  total: number;
  errors: number;
  errorRate: number;
  latencies: number[];
  maxLatency: number;
  avgLatency: number;
  p95Latency: number;
  avgTps: number;
  streamed: number;
  tokIn: number;
  tokCached: number;
  tokReasoning: number;
  tokOut: number;
  cost: number;
  savings: number;
  /** HTTP statuses in the window, for the response-class split. */
  statuses: number[];
}

/** Six headline numbers for the filtered window, plus the response-class split.
 *
 *  A reader who opens this page is usually asking "is it healthy?" before
 *  "show me the rows" — the band answers that without scrolling to the
 *  dashboard, and it aggregates the same filtered set the table shows, so the
 *  chips and the rows can never disagree. */
function WindowOverview(props: {
  stats: WindowStats;
  range: RangeFilter;
  insightsOpen: boolean;
  onToggleInsights: () => void;
}) {
  const s = props.stats;
  const errTone =
    s.errorRate === 0 ? "var(--admin-success)" : s.errorRate < 0.05 ? "var(--admin-warning)" : "var(--admin-danger)";

  const tiles: {
    label: string;
    value: string;
    sub?: string;
    icon: typeof Activity;
    tone: string;
  }[] = [
    {
      label: "requests",
      value: fmtInt(s.total),
      sub: `${fmtInt(s.streamed)} streaming`,
      icon: Activity,
      tone: "var(--admin-accent)",
    },
    {
      label: "error rate",
      value: fmtPct(s.errorRate),
      sub: `${fmtInt(s.errors)} failed`,
      icon: AlertTriangle,
      tone: errTone,
    },
    {
      label: "p95 latency",
      value: s.p95Latency > 0 ? fmtLatency(s.p95Latency) : "—",
      sub: `avg ${fmtLatency(s.avgLatency)}`,
      icon: Timer,
      tone: "var(--admin-accent-purple)",
    },
    {
      label: "throughput",
      value: s.avgTps > 0 ? s.avgTps.toFixed(1) : "—",
      sub: "tok/s avg",
      icon: Gauge,
      tone: "var(--admin-accent-purple)",
    },
    {
      label: "tokens",
      value: fmtTokens(s.tokIn + s.tokOut),
      sub: `${fmtTokens(s.tokIn)} in · ${fmtTokens(s.tokOut)} out`,
      icon: Layers,
      tone: "var(--admin-accent)",
    },
    {
      label: "spend",
      value: fmtUsd(s.cost),
      sub: s.savings > 0 ? `saved ${fmtUsd(s.savings)}` : "no cache savings",
      icon: DollarSign,
      tone: "var(--admin-success)",
    },
  ];

  return (
    <Card className="mb-3">
      {/* `gap-px` over a border-coloured plane draws the hairlines between the
          cells, so the band reads as one instrument panel rather than six
          floating cards. */}
      <div className="grid gap-px bg-[var(--admin-border)] lg:grid-cols-[minmax(0,1fr)_248px]">
        <div className="grid grid-cols-2 gap-px bg-[var(--admin-border)] sm:grid-cols-3 xl:grid-cols-6">
          {tiles.map((t) => {
            const Icon = t.icon;
            return (
              <div key={t.label} className="bg-[var(--admin-surface)] px-4 py-3">
                <div className="flex items-center gap-1.5">
                  <Icon size={12} style={{ color: t.tone }} aria-hidden />
                  <span className="admin-label truncate">{t.label}</span>
                </div>
                <p className="admin-stat-value mt-1.5 text-[19px]">{t.value}</p>
                <p className="mt-0.5 truncate font-mono text-[10px] text-[var(--admin-text-dim)]">
                  {t.sub}
                </p>
              </div>
            );
          })}
        </div>
        <div className="bg-[var(--admin-surface)]">
          <StatusMix slices={statusSlices(props.stats.statuses)} />
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--admin-border)] px-4 py-2">
        <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
          {rangeLabel(props.range)} · slowest{" "}
          <span className="text-[var(--admin-text-muted)]">{fmtLatency(props.stats.maxLatency)}</span>
        </span>
        <button
          type="button"
          onClick={props.onToggleInsights}
          aria-expanded={props.insightsOpen}
          className="flex min-h-11 items-center gap-1.5 rounded-md px-2 text-[12px] text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)] focus-visible:bg-white/[0.04] focus-visible:text-[var(--admin-text)] focus-visible:outline-none"
        >
          Window analytics
          <ChevronDown
            size={13}
            aria-hidden
            className={`transition-transform ${props.insightsOpen ? "rotate-180" : ""}`}
          />
        </button>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Row cells
// ---------------------------------------------------------------------------

/** Latency with a share bar scaled to the slowest request in the window.
 *
 *  The number alone does not answer "is this slow?" — 800ms is unremarkable
 *  next to a 1ms provider error and terrible on a 400ms gateway. The fill is
 *  relative to the window's own maximum and anything past the window's p95 is
 *  tinted amber, so the column scans as a shape first and numbers second. */
function LatencyCell(props: { ms: number; p95: number; max: number }) {
  const slow = props.p95 > 0 && props.ms > props.p95;
  const frac = props.max > 0 ? Math.min(1, props.ms / props.max) : 0;
  return (
    <div className="flex flex-col items-end gap-1">
      <NumCell className={slow ? "text-amber-300" : undefined}>{fmtLatency(props.ms)}</NumCell>
      <span className="block w-8 sm:w-12">
        <ProgressBar
          value={frac}
          tone={slow ? "bg-amber-400/70" : "bg-blue-400/55"}
        />
      </span>
    </div>  );
}

/** Cache column — the two cache flags are different systems and are labelled
 *  as such (see the `cache_hit` / `response_cache_hit` note in the API types):
 *  a green bar is the provider's prompt cache, a violet one is wiwi's own
 *  exact-match response cache. */
function CacheCell(props: { l: RequestLogEntry }) {
  const l = props.l;
  if (l.response_cache_hit) return <Badge tone="violet">response</Badge>;
  if (l.cache_hit) {
    const pct = l.tok_in > 0 ? Math.min(100, Math.round((l.tok_cached / l.tok_in) * 100)) : 0;
    return (
      <Badge tone="green" title={`${fmtTokens(l.tok_cached)} prompt tokens read from cache`}>
        {pct > 0 ? `${pct}% cached` : "cached"}
      </Badge>
    );
  }
  return <span className="text-[var(--admin-text-dim)]">—</span>;
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
  const max = Math.max(1, ...steps.map((a) => a.latency_ms));
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
                {/* Each attempt's latency as a share of the slowest one, so a
                    failover chain shows where the time actually went. */}
                <span className="mt-1.5 block w-full max-w-[220px]">
                  <ProgressBar value={a.latency_ms / max} tone={ok ? "bg-blue-400/50" : "bg-red-400/60"} />
                </span>
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
          <p className="text-[12px] text-[var(--admin-text-muted)]">Prompt logging is off</p>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
            Set <code className="rounded bg-white/[0.04] px-1 py-0.5 font-mono text-[10px]">store_prompts_in_spend_logs</code>{" "}
            in wiwi.yaml to capture the request and response bodies.
          </p>
        </div>
      </DetailSection>
    );
  }

  const activeTab = tab === "request" && !hasRequest ? "response" : tab;
  // The JSON view is what the copy button copies — keep them on one value.
  const raw = activeTab === "request" ? log.request_body : log.response_body;

  return (
    <DetailSection
      title="Request & Response"
      icon={<ArrowLeftRight size={13} />}
      right={
        <div className="flex items-center gap-1">
          {mode === "json" && (
            <InlineCopy text={JSON.stringify(raw, null, 2)} />
          )}
          {hasRequest && hasResponse && (
            <div className="flex rounded-md border border-[var(--admin-border)] p-0.5">
              <button
                type="button"
                onClick={() => setTab("request")}
                className={`min-h-8 rounded px-2 text-[11px] font-medium transition-colors ${
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
                className={`min-h-8 rounded px-2 text-[11px] font-medium transition-colors ${
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
              className={`min-h-8 rounded px-2 text-[11px] font-medium transition-colors ${
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
              className={`min-h-8 rounded px-2 text-[11px] font-medium transition-colors ${
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
  const [range, setRange] = useState<RangeFilter>(DEFAULT_RANGE);
  const [selected, setSelected] = useState<RequestLogEntry | null>(null);
  const [insights, setInsights] = useState(false);
  const now = useNow(15_000);

  const query = useQuery({
    queryKey: ["request-logs"],
    queryFn: getRequestLogs,
    refetchInterval: 15_000,
  });

  const connected = useAdminStream("log.created", (data) => {
    if (!live) return;
    const evt = asRequestEntry(data);
    if (!evt) return;
    // Write to a page-private key, never the shared ["request-logs"] one.
    // That key is read by Dashboard, Analytics, Budgets and Providers, and
    // getRequestLogs requests limit 10000 — so truncating it to 500 here made
    // every other mounted page recompute its aggregates over 500 events until
    // the next poll, silently under-reporting (AUDIT #255).
    qc.setQueryData<{ logs: RequestLogEntry[] }>(["request-logs", "live"], (old) => {
      if (!old) return { logs: [evt] };
      // Dedupe by request_id — the poll will replace this eventually, but
      // avoid showing the same event twice in the meantime.
      if (old.logs.some((l) => l.request_id === evt.request_id)) return old;
      return { logs: [evt, ...old.logs].slice(0, 500) };
    });
  });

  // Live events are prepended to the polled page so a new request appears
  // immediately without the 500-row ring ever touching the shared cache.
  const liveQuery = useQuery<{ logs: RequestLogEntry[] }>({
    queryKey: ["request-logs", "live"],
    queryFn: () => ({ logs: [] }),
    staleTime: Infinity,
    enabled: false,
  });

  const all = useMemo(() => {
    const polled = query.data?.logs ?? EMPTY;
    const liveRows = liveQuery.data?.logs ?? EMPTY;
    if (!liveRows.length) return polled;
    const seen = new Set(polled.map((l) => l.request_id));
    const fresh = liveRows.filter((l) => !seen.has(l.request_id));
    return fresh.length ? [...fresh, ...polled] : polled;
  }, [query.data, liveQuery.data]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    // `now` (not Date.now()) keeps the window sliding with the 15s clock tick,
    // so rows age out of the view even when the poll returns identical data
    // and react-query's structural sharing leaves `all` referentially stable.
    const minTs = range === "all" ? 0 : now / 1000 - RANGE_SECS[range];
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
  }, [all, q, model, providerSel, status, surface, range, now]);

  // Rows inside the time window but before the other filters — lets the empty
  // state tell "nothing happened in this window" apart from "your filters hide
  // everything", which need different advice.
  const inRange = useMemo(() => {
    if (range === "all") return all.length;
    const minTs = now / 1000 - RANGE_SECS[range];
    return all.filter((l) => l.ts >= minTs).length;
  }, [all, range, now]);

  // Cap the rendered rows (see ROWS_PER_PAGE). Reset whenever the result set
  // changes shape, so switching 24h → 5m and back does not leave a stale
  // "show more" expansion in place.
  const [visible, setVisible] = useState(ROWS_PER_PAGE);
  useEffect(() => {
    setVisible(ROWS_PER_PAGE);
  }, [range, q, model, providerSel, status, surface]);
  const visibleRows = useMemo(
    () => (filtered.length > visible ? filtered.slice(0, visible) : filtered),
    [filtered, visible],
  );

  const modelOpts = useMemo(() => distinctOptions(all, (l) => l.model_group, "All models"), [all]);
  const providerOpts = useMemo(() => distinctOptions(all, (l) => l.provider, "All providers"), [all]);
  const surfaceOpts = useMemo(
    () => distinctOptions(all, (l) => l.surface, "All surfaces", CANONICAL_SURFACES),
    [all],
  );

  // Aggregates for the overview band, over the same filtered set the table
  // renders — so the band and the rows can never tell different stories.
  const stats = useMemo<WindowStats>(() => {
    const total = filtered.length;
    let errors = 0;
    let streamed = 0;
    let tokIn = 0;
    let tokCached = 0;
    let tokReasoning = 0;
    let tokOut = 0;
    let cost = 0;
    let savings = 0;
    let maxLatency = 0;
    const latencies: number[] = [];
    const tps: number[] = [];
    for (const l of filtered) {
      if (l.status >= 400) errors += 1;
      if (l.was_stream) streamed += 1;
      tokIn += l.tok_in;
      tokCached += l.tok_cached;
      tokReasoning += l.tok_reasoning;
      tokOut += l.tok_out;
      cost += l.cost;
      savings += l.cache_savings;
      if (l.latency_ms > 0) {
        latencies.push(l.latency_ms);
        if (l.latency_ms > maxLatency) maxLatency = l.latency_ms;
      }
      if (l.tps > 0) tps.push(l.tps);
    }
    return {
      total,
      errors,
      errorRate: total > 0 ? errors / total : 0,
      latencies,
      maxLatency,
      avgLatency: mean(latencies),
      p95Latency: p95(latencies),
      avgTps: mean(tps),
      streamed,
      tokIn,
      tokCached,
      tokReasoning,
      tokOut,
      cost,
      savings,
      statuses: filtered.map((l) => l.status),
    };
  }, [filtered]);

  const tokenParts = useMemo(
    () => [
      { label: "input", value: stats.tokIn, color: TOKEN_COLORS[0] },
      { label: "cached", value: stats.tokCached, color: TOKEN_COLORS[1] },
      { label: "reasoning", value: stats.tokReasoning, color: TOKEN_COLORS[2] },
      { label: "output", value: stats.tokOut, color: TOKEN_COLORS[3] },
    ],
    [stats],
  );

  const filtersActive =
    q.trim() !== "" ||
    model !== "all" ||
    providerSel !== "all" ||
    status !== "all" ||
    surface !== "all";

  const resetFilters = () => {
    setQ("");
    setModel("all");
    setProviderSel("all");
    setStatus("all");
    setSurface("all");
  };

  return (
    <div>
      <PageHeader
        title="Request logs"
        subtitle="Gateway requests, newest first"
        right={<LiveTail live={live} onToggle={setLive} connected={connected} />}
      />

      {/* Filter deck — one instrument band instead of free-floating widgets.
          Status and range are segmented pills (flipped between constantly);
          model / provider / surface stay dropdowns because their option sets
          are unbounded. */}
      <div className="admin-filter-deck mb-3" role="search">
        <SearchInput
          value={q}
          onChange={setQ}
          placeholder="filter key / model / provider / request id…"
          className="w-full sm:w-64"
        />
        <span aria-hidden className="admin-filter-deck-rule hidden lg:block" />
        <Select value={model} onChange={setModel} options={modelOpts} className="h-8 w-[150px] text-[12px] pointer-coarse:h-11" />
        <Select
          value={providerSel}
          onChange={setProviderSel}
          options={providerOpts}
          className="h-8 w-[160px] text-[12px] pointer-coarse:h-11"
        />
        <Select
          value={surface}
          onChange={setSurface}
          options={surfaceOpts}
          className="h-8 w-[145px] text-[12px] pointer-coarse:h-11"
        />
        <span aria-hidden className="admin-filter-deck-rule hidden md:block" />
        <SegmentControl
          ariaLabel="Filter by status"
          value={status}
          onChange={(v) => setStatus(v as StatusFilter)}
          options={[
            { value: "all", label: "All", title: "All statuses" },
            { value: "2xx", label: "2xx" },
            { value: "4xx", label: "4xx" },
            { value: "5xx", label: "5xx" },
          ]}
        />
        <SegmentControl
          ariaLabel="Filter by time range"
          value={range}
          onChange={(v) => setRange(v as RangeFilter)}
          options={RANGE_OPTIONS.map((o) => ({
            value: o.value,
            label:
              o.value === "all"
                ? "All"
                : o.label.replace("Last ", "").replace(" minutes", "m").replace(" hours", "h").replace(" days", "d"),
            title: o.label,
          }))}
        />
        {filtersActive && (
          <Button
            variant="ghost"
            onClick={resetFilters}
            className="h-8 px-2.5 text-[12px] pointer-coarse:h-11"
            title="Clear the search box and every filter"
          >
            <X size={12} aria-hidden />
            Reset
          </Button>
        )}
      </div>

      {stats.total > 0 && (
        <WindowOverview
          stats={stats}
          range={range}
          insightsOpen={insights}
          onToggleInsights={() => setInsights((v) => !v)}
        />
      )}

      {insights && stats.total > 0 && (
        <div className="mb-3 grid gap-3 lg:grid-cols-2">
          <VisualCard
            title="Latency distribution"
            subtitle={`${fmtInt(stats.latencies.length)} timed requests in ${rangeLabel(range)}`}
            icon={Timer}
          >
            <LatencyRibbon profile={latencyProfile(stats.latencies)} />
          </VisualCard>
          <VisualCard
            title="Token mix"
            subtitle="input · cached · reasoning · output"
            icon={Layers}
          >
            <TokenRibbon parts={tokenParts} />
          </VisualCard>
        </div>
      )}

      {query.isLoading && <LogsSkeleton rows={8} />}
      {query.error && <ErrorText>{query.error.message}</ErrorText>}

      {!query.isLoading && !query.error && filtered.length === 0 && (
        <Card>
          {all.length === 0 ? (
            <EmptyState icon={Inbox} title="No requests logged yet">
              Requests sent through <code className="font-mono text-[12px]">/v1/chat/completions</code>,{" "}
              <code className="font-mono text-[12px]">/v1/messages</code> or{" "}
              <code className="font-mono text-[12px]">/v1/responses</code> appear here as they
              complete.
            </EmptyState>
          ) : inRange === 0 ? (
            <EmptyState
              icon={Timer}
              title={`Nothing in the ${rangeLabel(range)}`}
              action={
                <Button variant="ghost" onClick={() => setRange("all")}>
                  Show all time
                </Button>
              }
            >
              The gateway still holds {fmtInt(all.length)} older events in its ring — the traffic
              you are looking for may simply predate this window.
            </EmptyState>
          ) : (
            <EmptyState
              icon={Inbox}
              title="No events match these filters"
              action={
                <Button variant="ghost" onClick={resetFilters}>
                  Reset filters
                </Button>
              }
            >
              {fmtInt(inRange)} requests fall inside this window; the search box and the dropdowns
              above are excluding all of them.
            </EmptyState>
          )}
        </Card>
      )}

      {filtered.length > 0 && (
        <Card>
          <LogsTable
            className="admin-table-logs"
            maxHeight={640}
            head={HEAD.map((h) => h.label)}
            colClass={HEAD.map((h) => h.cls)}
          >
            {visibleRows.map((l, i) => (
              <LogRow
                key={`${l.request_id}:${l.ts}`}
                zebra={i}
                ariaLabel={`request ${l.request_id}`}
                onClick={() => setSelected(l)}
              >
                <TimeAgo
                  ts={l.ts}
                  nowMs={now}
                  className={`border-l-2 pl-2 ${accentBar(l)}`}
                />
                <LogTD className={`${COL.key} font-medium`} title={l.key_alias}>
                  {l.key_alias || "—"}
                </LogTD>
                <LogTD className="max-w-[92px] truncate sm:max-w-[220px]" title={l.model_group}>
                  {l.model_group || "—"}
                </LogTD>
                <LogTD
                  className={`${COL.provider} text-[var(--admin-text-muted)]`}
                  title={`${l.provider} / ${l.provider_key_label}`}
                >
                  {l.provider || "—"} / {l.provider_key_label || "—"}
                </LogTD>
                <LogTD>
                  <StatusBadge status={l.status} errorCode={l.error_code} />
                </LogTD>
                <LogTD className={COL.tokIn}>
                  <NumCell className={dimZero(l.tok_in)}>{fmtTokens(l.tok_in)}</NumCell>
                </LogTD>
                <LogTD className={COL.tokCached}>
                  <NumCell className={dimZero(l.tok_cached)}>{fmtTokens(l.tok_cached)}</NumCell>
                </LogTD>
                <LogTD className={COL.tokReasoning}>
                  <NumCell className={dimZero(l.tok_reasoning)}>{fmtTokens(l.tok_reasoning)}</NumCell>
                </LogTD>
                <LogTD className={COL.tokOut}>
                  <NumCell className={dimZero(l.tok_out)}>{fmtTokens(l.tok_out)}</NumCell>
                </LogTD>
                <LogTD className={COL.tps}>
                  {l.tps > 0 ? (
                    <NumCell>{l.tps.toFixed(1)}</NumCell>
                  ) : (
                    <span className="font-mono text-[var(--admin-text-dim)]">—</span>
                  )}
                </LogTD>
                <LogTD className={COL.ttft}>
                  {l.ttft_ms > 0 ? (
                    <NumCell>{fmtLatency(l.ttft_ms)}</NumCell>
                  ) : (
                    <span className="font-mono text-[var(--admin-text-dim)]">—</span>
                  )}
                </LogTD>
                <LogTD className={COL.latency}>
                  <LatencyCell ms={l.latency_ms} p95={stats.p95Latency} max={stats.maxLatency} />
                </LogTD>
                <LogTD className={COL.cost}>
                  <span
                    className={`font-mono tabular-nums ${
                      l.cost > 0 ? "" : "text-[var(--admin-text-dim)]"
                    }`}
                  >
                    {fmtUsd(l.cost)}
                  </span>
                </LogTD>
                <LogTD className={COL.cache}>
                  <CacheCell l={l} />
                </LogTD>
                <LogTD className={COL.open}>
                  <ChevronRight
                    size={14}
                    aria-hidden
                    className="ml-auto text-[var(--admin-text-dim)] opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 pointer-coarse:opacity-60"
                  />
                </LogTD>
              </LogRow>
            ))}
          </LogsTable>
        </Card>
      )}

      <div className="mt-2 flex flex-wrap items-center justify-between gap-3 px-1">
        <LogsFooter shown={filtered.length} total={all.length} />
        {filtered.length > visibleRows.length && (
          <div className="flex items-center gap-3">
            <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
              showing {fmtInt(visibleRows.length)} of {fmtInt(filtered.length)}
            </span>
            <button
              type="button"
              onClick={() => setVisible((v) => v + ROWS_PER_PAGE)}
              className="min-h-11 rounded-md border border-[var(--admin-border)] px-4 text-[12px] font-medium transition-colors hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              Show {fmtInt(Math.min(ROWS_PER_PAGE, filtered.length - visibleRows.length))} more
            </button>
          </div>
        )}
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

/** Zero token counts are real data, but in a wall of numbers they are the
 *  loudest thing on the row. Dimming them keeps the eye on the values that
 *  differ. */
function dimZero(n: number): string | undefined {
  return n === 0 ? "text-[var(--admin-text-dim)]" : undefined;
}

// ---------------------------------------------------------------------------
// LogDetail — the full content of the slide-in drawer
// ---------------------------------------------------------------------------

function LogDetail(props: { log: RequestLogEntry; onClose: () => void }) {
  const { log: l } = props;
  const ok = l.status < 400;
  // Time to first token plus everything after it is the generation phase — the
  // split is what tells a reader whether a slow request stalled before the
  // model started talking or streamed too slowly.
  const genMs = Math.max(0, l.latency_ms - l.ttft_ms);
  const totalTokens = l.tok_in + l.tok_out;

  return (
    <>
      {/* Drawer header */}
      <header className="flex items-start justify-between gap-3 border-b border-white/[0.06] px-5 py-4">
        <div className="min-w-0">
          <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
            <StatusBadge status={l.status} errorCode={l.error_code} />
            {l.surface && <Badge tone="gray">{l.surface}</Badge>}
            {l.was_stream && <Badge tone="blue">stream</Badge>}
            {l.response_cache_hit && <Badge tone="violet">response cache</Badge>}
            {l.cache_hit && <Badge tone="green">prompt cache</Badge>}
          </div>
          <h3 className="truncate text-[16px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            {l.model_group || "—"}
          </h3>
          <p className="mt-0.5 truncate font-mono text-[11px] text-[var(--admin-text-dim)]">
            {fmtDateTime(l.ts)} · {l.key_alias || "no key"} · {l.provider || "—"} /{" "}
            {l.provider_key_label || "—"}
          </p>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={props.onClose}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
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
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <MetricPill
              icon={<Zap size={13} />}
              label="TTFT"
              value={l.ttft_ms > 0 ? fmtLatency(l.ttft_ms) : "—"}
              dim={l.ttft_ms === 0}
            />
            <MetricPill
              icon={<Timer size={13} />}
              label="Generation"
              value={fmtLatency(genMs)}
              dim={l.ttft_ms === 0}
            />
            <MetricPill
              icon={<Gauge size={13} />}
              label="Total"
              value={fmtLatency(l.latency_ms)}
            />
            <MetricPill
              icon={<Cpu size={13} />}
              label="Tokens/sec"
              value={l.tps > 0 ? l.tps.toFixed(1) : "—"}
              dim={l.tps === 0}
            />
          </div>
          {!l.was_stream && (
            <p className="mt-2 text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
              Non-streaming response — there is no first-token boundary, so the whole duration sits
              in generation time.
            </p>
          )}
        </DetailSection>

        {/* Token usage */}
        <DetailSection title="Token usage" icon={<Layers size={13} />}>
          <TokenRibbon
            parts={[
              { label: "input", value: l.tok_in, color: TOKEN_COLORS[0] },
              { label: "cached", value: l.tok_cached, color: TOKEN_COLORS[1] },
              { label: "reasoning", value: l.tok_reasoning, color: TOKEN_COLORS[2] },
              { label: "output", value: l.tok_out, color: TOKEN_COLORS[3] },
            ]}
          />
          {l.usage_estimated && (
            <p className="px-4 pb-3 text-[11px] text-[var(--admin-text-dim)]">
              Token counts are estimated — the provider did not report usage for this response.
            </p>
          )}
        </DetailSection>

        {/* Cost */}
        <DetailSection title="Cost" icon={<DollarSign size={13} />}>
          <div className="grid grid-cols-3 gap-2">
            <MetricPill icon={<DollarSign size={13} />} label="Request cost" value={fmtUsd(l.cost)} dim={l.cost === 0} />
            <MetricPill
              icon={<DollarSign size={13} />}
              label="Cache savings"
              value={fmtUsd(l.cache_savings)}
              dim={l.cache_savings === 0}
            />
            <MetricPill
              icon={<Layers size={13} />}
              label="Per 1M tokens"
              value={
                totalTokens > 0 && l.cost > 0
                  ? `$${((l.cost / totalTokens) * 1_000_000).toFixed(2)}`
                  : "—"
              }
              dim={totalTokens === 0 || l.cost === 0}
            />
          </div>
        </DetailSection>

        {/* Request & Response content */}
        <PromptResponseSection log={l} />

        {/* Request details */}
        <DetailSection title="Request details" icon={<Server size={13} />}>
          <DetailRow label="Virtual key">{l.key_alias || "—"}</DetailRow>
          <DetailRow label="Provider">
            {l.provider || "—"} / {l.provider_key_label || "—"}
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
          {l.error_message && (
            <DetailRow label="Error message">
              <span className="flex items-start gap-1.5">
                <span className="min-w-0 flex-1 whitespace-pre-wrap break-words font-mono text-[12px] text-[var(--admin-danger)]">
                  {l.error_message}
                </span>
                <span className="mt-0.5">
                  <InlineCopy text={l.error_message} />
                </span>
              </span>
            </DetailRow>
          )}
          {l.translation_warnings?.length > 0 && (
            <DetailRow label="Translation">
              <span className="text-amber-300">{l.translation_warnings.join(" · ")}</span>
            </DetailRow>
          )}
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
