// Usage — range-filtered totals, TPS trend, token-share donut, group-by
// summary, and a sortable per-request table with a totals footer.

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowDownToLine,
  ArrowUpFromLine,
  Brain,
  DollarSign,
  Gauge,
  Percent,
  X,
  Zap,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getOverview, getRequestLogs, getTimeseries } from "@/api/client";
import { useLiveInvalidation } from "@/api/stream";
import type { RequestLogEntry, TpsBucket } from "@/api/types";
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorText,
  LiveBadge,
  PageHeader,
  Select,
  StatCard,
  Table,
  TD,
} from "@/components/ui";
import {
  fmtDateTime,
  fmtInt,
  fmtPct,
  fmtTime,
  fmtTokens,
  fmtUsd,
  groupBy,
  mean,
} from "@/lib/format";
import { deltaVsPrevHour, hourlySeries } from "@/lib/dashboard-metrics";

const PIE_COLORS = ["#3b82f6", "#199e70", "#a855f7", "#c98500"];

const RANGE_OPTIONS = [
  { value: "15", label: "Last 15 min" },
  { value: "60", label: "Last hour" },
  { value: "360", label: "Last 6 hours" },
  { value: "1440", label: "Last 24 hours" },
  { value: "10080", label: "Last 7 days" },
  { value: "43200", label: "Last 30 days" },
  { value: "0", label: "All time" },
];

const RANGE_STORAGE_KEY = "wiwi.usage.range";

function loadStoredRange(): number {
  try {
    const raw = localStorage.getItem(RANGE_STORAGE_KEY);
    if (raw == null) return 60;
    const v = Number(raw);
    return Number.isFinite(v) && RANGE_OPTIONS.some((o) => Number(o.value) === v)
      ? v
      : 60;
  } catch {
    return 60;
  }
}

type GroupDim = "model" | "key" | "provider";
type SortDir = "asc" | "desc";
type SortKey =
  | "time"
  | "key"
  | "model"
  | "provider"
  | "status"
  | "tok_in"
  | "cached"
  | "reasoning"
  | "out"
  | "tps"
  | "ttft"
  | "latency"
  | "cost";

interface UsageTotals {
  requests: number;
  errors: number;
  tokIn: number;
  tokCached: number;
  tokReasoning: number;
  tokOut: number;
  cost: number;
  avgTps: number;
  cacheHits: number;
  cacheSavings: number;
}

interface GroupRow {
  name: string;
  requests: number;
  tokens: number;
  cost: number;
  avgTps: number;
  errs: number;
}

interface ShareSlice {
  name: string;
  value: number;
  color: string;
}

type LatencyMetric = "ttft" | "total";

const LATENCY_BUCKETS = [
  { label: "<100", lo: 0, hi: 100 },
  { label: "100–250", lo: 100, hi: 250 },
  { label: "250–500", lo: 250, hi: 500 },
  { label: "500–1K", lo: 500, hi: 1000 },
  { label: "1K–2K", lo: 1000, hi: 2000 },
  { label: "2K–4K", lo: 2000, hi: 4000 },
  { label: "4K+", lo: 4000, hi: Number.POSITIVE_INFINITY },
] as const;

const LATENCY_COLORS: Record<LatencyMetric, string> = {
  ttft: "#3b82f6",
  total: "#a855f7",
};

interface LatencyBucket {
  label: string;
  count: number;
}

function latencyBuckets(
  logs: RequestLogEntry[],
  metric: LatencyMetric,
): LatencyBucket[] {
  return LATENCY_BUCKETS.map((b) => ({
    label: b.label,
    count: logs.filter((l) => {
      const v = metric === "ttft" ? l.ttft_ms : l.latency_ms;
      return v > 0 && v >= b.lo && v < b.hi;
    }).length,
  }));
}

function groupKeyOf(l: RequestLogEntry, dim: GroupDim): string {
  if (dim === "model") return l.model_group;
  if (dim === "key") return l.key_alias;
  return l.provider;
}

function statusTone(status: number): "green" | "amber" | "red" {
  if (status < 400) return "green";
  if (status < 500) return "amber";
  return "red";
}

function sortValue(l: RequestLogEntry, k: SortKey): number | string {
  switch (k) {
    case "time":
      return l.ts;
    case "key":
      return l.key_alias;
    case "model":
      return l.model_group;
    case "provider":
      return l.provider;
    case "status":
      return l.status;
    case "tok_in":
      return l.tok_in;
    case "cached":
      return l.tok_cached;
    case "reasoning":
      return l.tok_reasoning;
    case "out":
      return l.tok_out;
    case "tps":
      return l.tps;
    case "ttft":
      return l.ttft_ms;
    case "latency":
      return l.latency_ms;
    case "cost":
      return l.cost;
  }
}

function SortHeader(props: {
  label: string;
  k: SortKey;
  active: SortKey;
  dir: SortDir;
  onSort: (k: SortKey) => void;
}) {
  const isActive = props.k === props.active;
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 transition-colors hover:text-[var(--admin-text)]"
      onClick={() => props.onSort(props.k)}
    >
      {props.label}
      <span className={isActive ? "text-blue-400" : "opacity-30"}>
        {isActive && props.dir === "asc" ? "▲" : "▼"}
      </span>
    </button>
  );
}

function ChartTooltip(props: {
  active?: boolean;
  label?: string | number;
  payload?: Array<{ name?: string; value?: number | string; color?: string }>;
  fmt?: (v: number) => string;
}) {
  if (!props.active || !props.payload?.length) return null;
  return (
    <div className="admin-chart-tooltip">
      <div className="mb-1 text-[11px] text-[var(--admin-text-muted)]">{props.label}</div>
      {props.payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 leading-5">
          <span className="h-0.5 w-3 rounded" style={{ backgroundColor: p.color }} />
          <span className="tt-value">
            {props.fmt ? props.fmt(Number(p.value)) : String(p.value)}
          </span>
          <span className="tt-series">{p.name}</span>
        </div>
      ))}
    </div>
  );
}

const NOW_MS_FN = () => Date.now();

export function UsagePage() {
  const [range, setRange] = useState<number>(loadStoredRange);
  const [groupDim, setGroupDim] = useState<GroupDim>("model");
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [latencyMetric, setLatencyMetric] = useState<LatencyMetric>("ttft");
  const [filterGroup, setFilterGroup] = useState<{ dim: GroupDim; name: string } | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(RANGE_STORAGE_KEY, String(range));
    } catch {
      // localStorage may be unavailable (private mode); in-memory state still works
    }
  }, [range]);

  const overviewQuery = useQuery({
    queryKey: ["overview", range],
    queryFn: () => getOverview(range),
    refetchInterval: 10_000,
  });
  const tpsQuery = useQuery({
    queryKey: ["tps-ts", range],
    queryFn: () => getTimeseries("tps", range),
    refetchInterval: 10_000,
  });
  const logsQuery = useQuery({
    queryKey: ["usage-logs"],
    queryFn: getRequestLogs,
    refetchInterval: 15_000,
  });

  // Live SSE invalidation: refresh all three queries immediately when a new
  // request lands, instead of waiting up to 15s for the next poll.
  const connected = useLiveInvalidation(["overview", "tps-ts", "usage-logs"]);

  const allLogs = useMemo(() => {
    const all = logsQuery.data?.logs ?? [];
    if (range === 0) return all; // all-time: no cutoff
    const cutoff = Math.floor(Date.now() / 1000) - range * 60;
    return all.filter((l) => l.ts >= cutoff);
  }, [logsQuery.data, range]);

  // The grouped summary is computed from the unfiltered set (it is the filter
  // control surface); everything else uses this filtered view.
  const logs = useMemo(() => {
    if (!filterGroup) return allLogs;
    return allLogs.filter((l) => groupKeyOf(l, filterGroup.dim) === filterGroup.name);
  }, [allLogs, filterGroup]);

  const mkPoints = useMemo(
    () => (vOf: (l: RequestLogEntry) => number) =>
      logs.map((l) => ({ t: l.ts, v: vOf(l) })),
    [logs],
  );

  const totals = useMemo<UsageTotals>(() => {
    const tpsValues: number[] = [];
    const t: UsageTotals = {
      requests: logs.length,
      errors: 0,
      tokIn: 0,
      tokCached: 0,
      tokReasoning: 0,
      tokOut: 0,
      cost: 0,
      avgTps: 0,
      cacheHits: 0,
      cacheSavings: 0,
    };
    for (const l of logs) {
      t.tokIn += l.tok_in;
      t.tokCached += l.tok_cached;
      t.tokReasoning += l.tok_reasoning;
      t.tokOut += l.tok_out;
      t.cost += l.cost;
      t.cacheSavings += l.cache_savings;
      if (l.cache_hit) t.cacheHits += 1;
      if (l.status >= 400) t.errors += 1;
      if (l.tps > 0) tpsValues.push(l.tps);
    }
    t.avgTps = mean(tpsValues);
    return t;
  }, [logs]);

  const sorted = useMemo(() => {
    const rows = [...logs];
    rows.sort((a, b) => {
      const va = sortValue(a, sortKey);
      const vb = sortValue(b, sortKey);
      const cmp =
        typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb));
      return sortDir === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [logs, sortKey, sortDir]);

  const groupRows = useMemo<GroupRow[]>(() => {
    const rows: GroupRow[] = [];
    for (const [name, rs] of groupBy(allLogs, (l) => groupKeyOf(l, groupDim))) {
      rows.push({
        name,
        requests: rs.length,
        tokens: rs.reduce((a, r) => a + r.tok_in + r.tok_cached + r.tok_reasoning + r.tok_out, 0),
        cost: rs.reduce((a, r) => a + r.cost, 0),
        avgTps: mean(rs.filter((r) => r.tps > 0).map((r) => r.tps)),
        errs: rs.filter((r) => r.status >= 400).length,
      });
    }
    return rows.sort((a, b) => b.requests - a.requests);
  }, [allLogs, groupDim]);

  const tpsBuckets = useMemo<TpsBucket[]>(
    () => (tpsQuery.data?.buckets ?? []) as TpsBucket[],
    [tpsQuery.data],
  );

  const o = overviewQuery.data;

  // Display totals prefer the DB-backed overview (real COUNT(*)/SUM) so the
  // page never lies when more than `limit` rows were requested. The per-row
  // totals are a fallback while the overview is still loading.
  const requests = o?.requests ?? totals.requests;
  const errors = o?.errors ?? totals.errors;
  const cacheHits = o?.cache_hits ?? totals.cacheHits;
  const cost = o?.cost ?? totals.cost;
  const cacheSavings = o?.cache_savings ?? totals.cacheSavings;
  const tokIn = o?.tok_in ?? totals.tokIn;
  const tokCached = o?.tok_cached ?? totals.tokCached;
  const tokReasoning = o?.tok_reasoning ?? totals.tokReasoning;
  const tokOut = o?.tok_out ?? totals.tokOut;
  const avgTps = o?.tps_avg ?? totals.avgTps;
  const totalTokens = tokIn + tokCached + tokReasoning + tokOut;

  const share = useMemo<ShareSlice[]>(
    () =>
      [
        { name: "input", value: tokIn, color: PIE_COLORS[0] },
        { name: "cached", value: tokCached, color: PIE_COLORS[1] },
        { name: "reasoning", value: tokReasoning, color: PIE_COLORS[2] },
        { name: "output", value: tokOut, color: PIE_COLORS[3] },
      ].filter((s) => s.value > 0),
    [tokIn, tokCached, tokReasoning, tokOut],
  );

  const latBuckets = useMemo(
    () => latencyBuckets(logs, latencyMetric),
    [logs, latencyMetric],
  );

  // prev-hour deltas + sparklines (same approach as Dashboard)
  const nowMs = NOW_MS_FN();
  const hourCut = nowMs - 3_600_000;
  const hourPrevStart = nowMs - 7_200_000;
  const sumIn = (pts: Array<{ t: number; v: number }>, lo: number, hi: number) =>
    pts.filter((p) => p.t * 1000 >= lo && p.t * 1000 < hi).reduce((a, p) => a + p.v, 0);

  const reqPts = mkPoints(() => 1);
  const costPts = mkPoints((l) => l.cost);
  const errPts = mkPoints((l) => (l.status >= 400 ? 1 : 0));
  const outPts = mkPoints((l) => l.tok_out);
  const cachedPts = mkPoints((l) => l.tok_cached);
  const reasonPts = mkPoints((l) => l.tok_reasoning);
  const inPts = mkPoints((l) => l.tok_in);
  const tpsPts = mkPoints((l) => l.tps);

  const reqDelta = deltaVsPrevHour(
    sumIn(reqPts, hourCut, Number.POSITIVE_INFINITY),
    sumIn(reqPts, hourPrevStart, hourCut),
  );
  const costDelta = deltaVsPrevHour(
    sumIn(costPts, hourCut, Number.POSITIVE_INFINITY),
    sumIn(costPts, hourPrevStart, hourCut),
  );
  const errDelta = deltaVsPrevHour(
    sumIn(errPts, hourCut, Number.POSITIVE_INFINITY),
    sumIn(errPts, hourPrevStart, hourCut),
  );

  const hasTraffic = requests > 0;
  const cacheHitRate = hasTraffic ? cacheHits / requests : 0;
  const errorRate = hasTraffic ? errors / requests : 0;

  const onSort = (k: SortKey) => {
    if (k === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(k);
      setSortDir("desc");
    }
  };

  const toggleGroupFilter = (name: string) => {
    setFilterGroup((cur) =>
      cur && cur.dim === groupDim && cur.name === name ? null : { dim: groupDim, name },
    );
  };

  const onGroupDimChange = (v: string) => {
    setGroupDim(v as GroupDim);
    setFilterGroup(null);
  };

  const maxGroupReqs = Math.max(1, ...groupRows.map((r) => r.requests));
  const latColor = LATENCY_COLORS[latencyMetric];
  const hasLatency = latBuckets.some((b) => b.count > 0);

  return (
    <div
      style={{
        opacity: overviewQuery.isFetching && overviewQuery.data ? 0.7 : 1,
        transition: "opacity 200ms",
      }}
    >
      <PageHeader
        title="Usage"
        subtitle={o ? `Per-request detail · ${o.window_minutes === 0 ? 'all time' : `last ${o.window_minutes} min`}` : "Per-request usage detail"}
        right={
          <div className="flex items-center gap-2">
            <LiveBadge connected={connected} />
            {filterGroup && (
              <span className="admin-badge admin-badge-violet">
                {filterGroup.dim}: {filterGroup.name}
                <button
                  type="button"
                  onClick={() => setFilterGroup(null)}
                  className="ml-0.5 -mr-0.5 rounded p-0.5 transition-colors hover:bg-white/10"
                  aria-label="Clear group filter"
                >
                  <X size={11} />
                </button>
              </span>
            )}
            <Select
              value={String(range)}
              onChange={(v) => setRange(Number(v))}
              options={RANGE_OPTIONS}
            />
          </div>
        }
      />

      {logsQuery.error && <ErrorText>{logsQuery.error.message}</ErrorText>}

      {/* Hero row: 4 featured stat cards with icons, sparklines, deltas */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          featured
          icon={Activity}
          tone="brand"
          label="requests"
          value={fmtInt(requests)}
          sub={o ? `error rate ${fmtPct(o.error_rate)}` : `${fmtInt(errors)} errors`}
          spark={hourlySeries(reqPts, nowMs)}
          delta={reqDelta}
          deltaGoodDir="up"
          waiting={!hasTraffic}
        />
        <StatCard
          featured
          icon={DollarSign}
          label="spend"
          value={fmtUsd(cost)}
          sub={`saved ${fmtUsd(cacheSavings)}`}
          spark={hourlySeries(costPts, nowMs)}
          delta={costDelta}
          waiting={!hasTraffic}
        />
        <StatCard
          featured
          icon={Percent}
          tone={cacheHitRate > 0.1 ? "success" : "default"}
          label="cache hit rate"
          value={fmtPct(cacheHitRate)}
          sub={`${fmtInt(cacheHits)} of ${fmtInt(requests)}`}
          spark={hourlySeries(cachedPts, nowMs)}
          waiting={!hasTraffic}
        />
        <StatCard
          featured
          icon={Gauge}
          label="avg tps"
          value={avgTps.toFixed(1)}
          sub={o ? `p95 ${o.tps_p95.toFixed(1)}` : undefined}
          spark={hourlySeries(tpsPts, nowMs)}
          waiting={!hasTraffic}
        />
      </div>

      {/* Secondary stat row: token breakdown */}
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatCard
          icon={ArrowDownToLine}
          label="tokens in"
          value={fmtTokens(tokIn)}
          spark={hourlySeries(inPts, nowMs)}
          waiting={!hasTraffic}
        />
        <StatCard
          icon={Zap}
          tone={tokCached > 0 ? "success" : "default"}
          label="cached"
          value={fmtTokens(tokCached)}
          sub={`${fmtInt(cacheHits)} hits`}
          spark={hourlySeries(cachedPts, nowMs)}
          waiting={!hasTraffic}
        />
        <StatCard
          icon={Brain}
          label="reasoning"
          value={fmtTokens(tokReasoning)}
          spark={hourlySeries(reasonPts, nowMs)}
          waiting={!hasTraffic}
        />
        <StatCard
          icon={ArrowUpFromLine}
          label="output"
          value={fmtTokens(tokOut)}
          spark={hourlySeries(outPts, nowMs)}
          waiting={!hasTraffic}
        />
        <StatCard
          icon={Activity}
          tone={errorRate > 0.05 ? "danger" : "success"}
          label="errors"
          value={fmtInt(errors)}
          sub={fmtPct(errorRate)}
          spark={hourlySeries(errPts, nowMs)}
          delta={errDelta}
          waiting={!hasTraffic}
        />
        <StatCard
          icon={Gauge}
          label="total tokens"
          value={fmtTokens(totalTokens)}
          waiting={!hasTraffic}
        />
      </div>

      {/* Charts: TPS area chart + token share donut */}
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="TPS over time"
            right={
              <span className="flex items-center gap-3 text-[11px] text-[var(--admin-text-dim)]">
                <span className="flex items-center gap-1.5">
                  <span className="h-0.5 w-3 rounded bg-[#3b82f6]" /> avg
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-0.5 w-3 rounded bg-[#a855f7]" /> p95
                </span>
              </span>
            }
          />
          <div className="h-[260px] p-3">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={tpsBuckets} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="grad-tps-avg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.20} />
                    <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.01} />
                  </linearGradient>
                  <linearGradient id="grad-tps-p95" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#a855f7" stopOpacity={0.10} />
                    <stop offset="100%" stopColor="#a855f7" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff" strokeOpacity={0.06} vertical={false} />
                <XAxis
                  dataKey="t"
                  tickFormatter={(t: number) =>
                    range === 0 || range > 1440 ? fmtDateTime(t) : fmtTime(t)
                  }
                  minTickGap={48}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                />
                <YAxis width={36} tick={{ fontSize: 11, fill: "#6b7280" }} tickLine={false} />
                <Tooltip
                  content={<ChartTooltip fmt={(v) => `${Number(v).toFixed(1)} tok/s`} />}
                  cursor={{ stroke: "#3b82f6", strokeOpacity: 0.3 }}
                />
                <Area
                  type="monotone"
                  dataKey="tps_p95"
                  name="p95"
                  stroke="#a855f7"
                  strokeWidth={2}
                  strokeDasharray="4 4"
                  fill="url(#grad-tps-p95)"
                  fillOpacity={1}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                />
                <Area
                  type="monotone"
                  dataKey="tps_avg"
                  name="avg"
                  stroke="#3b82f6"
                  strokeWidth={2.5}
                  fill="url(#grad-tps-avg)"
                  fillOpacity={1}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card>
          <CardHeader title="Token share" />
          <div className="relative h-[260px] p-3">
            {share.length === 0 ? (
              <EmptyState>No tokens recorded in this window.</EmptyState>
            ) : (
              <>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={share}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={55}
                      outerRadius={85}
                      paddingAngle={2}
                      stroke="none"
                    >
                      {share.map((s) => (
                        <Cell key={s.name} fill={s.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      content={<ChartTooltip fmt={(v) => fmtInt(Number(v))} />}
                    />
                  </PieChart>
                </ResponsiveContainer>
                {/* center label overlay */}
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                  <span className="font-mono text-[18px] font-bold tabular-nums text-[var(--admin-text)]">
                    {fmtTokens(totalTokens)}
                  </span>
                  <span className="admin-label mt-1">total tokens</span>
                </div>
                {/* custom legend with percentages */}
                <div className="absolute bottom-3 left-0 right-0 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 px-4">
                  {share.map((s) => (
                    <span key={s.name} className="flex items-center gap-1.5 text-[11px] text-[var(--admin-text-muted)]">
                      <span className="h-0.5 w-3 rounded" style={{ backgroundColor: s.color }} />
                      {s.name}
                      <span className="font-mono tabular-nums text-[var(--admin-text-dim)]">
                        {fmtPct(s.value / totalTokens)}
                      </span>
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
        </Card>
      </div>

      {/* Latency distribution histogram */}
      <Card className="mt-4">
        <CardHeader
          title="Latency distribution"
          right={
            <span className="flex items-center gap-2 text-[11px] text-[var(--admin-text-dim)]">
              <span className="flex items-center gap-1.5">
                <span
                  className="h-0.5 w-3 rounded"
                  style={{ backgroundColor: LATENCY_COLORS.ttft }}
                />
                ttft
              </span>
              <span className="flex items-center gap-1.5">
                <span
                  className="h-0.5 w-3 rounded"
                  style={{ backgroundColor: LATENCY_COLORS.total }}
                />
                total
              </span>
              <Select
                value={latencyMetric}
                onChange={(v) => setLatencyMetric(v as LatencyMetric)}
                options={[
                  { value: "ttft", label: "ttft" },
                  { value: "total", label: "total latency" },
                ]}
              />
            </span>
          }
        />
        <div className="h-[260px] p-3">
          {!hasLatency ? (
            <EmptyState>No latency data in this window.</EmptyState>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={latBuckets} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="grad-latency" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={latColor} stopOpacity={0.5} />
                    <stop offset="100%" stopColor={latColor} stopOpacity={0.08} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff" strokeOpacity={0.06} vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                />
                <YAxis
                  width={36}
                  allowDecimals={false}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                />
                <Tooltip
                  content={<ChartTooltip fmt={(v) => `${fmtInt(Number(v))} requests`} />}
                  cursor={{ fill: `${latColor}0D` }}
                />
                <Bar
                  dataKey="count"
                  name={latencyMetric === "ttft" ? "ttft" : "latency"}
                  fill="url(#grad-latency)"
                  stroke={latColor}
                  strokeWidth={1}
                  radius={[4, 4, 0, 0]}
                  maxBarSize={80}
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </Card>

      {/* Grouped summary with inline bar indicators */}
      <Card className="mt-4">
        <CardHeader
          title="Grouped summary"
          subtitle="Click a row to filter the charts above"
          right={
            <Select
              value={groupDim}
              onChange={onGroupDimChange}
              options={[
                { value: "model", label: "by model" },
                { value: "key", label: "by key" },
                { value: "provider", label: "by provider" },
              ]}
            />
          }
        />
        {groupRows.length === 0 ? (
          <EmptyState>No requests in this window.</EmptyState>
        ) : (
          <div className="admin-table">
            <div className="admin-scroll overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr>
                    <th>{groupDim}</th>
                    <th>distribution</th>
                    <th>requests</th>
                    <th>tokens</th>
                    <th>cost</th>
                    <th>avg tps</th>
                    <th>errors</th>
                  </tr>
                </thead>
                <tbody>
                  {groupRows.map((r) => {
                    const isActive =
                      filterGroup?.dim === groupDim && filterGroup.name === r.name;
                    return (
                      <tr
                        key={r.name}
                        onClick={() => toggleGroupFilter(r.name)}
                        className={`cursor-pointer ${
                          isActive ? "ring-1 ring-inset ring-violet-500/30" : ""
                        }`}
                      >
                        <td className="font-medium">{r.name}</td>
                        <td style={{ width: "30%", minWidth: "120px" }}>
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.05]">
                              <div
                                className={`h-full rounded-full ${
                                  isActive
                                    ? "bg-gradient-to-r from-violet-500/70 to-fuchsia-500/70"
                                    : "bg-gradient-to-r from-blue-500/60 to-violet-500/60"
                                }`}
                                style={{ width: `${(r.requests / maxGroupReqs) * 100}%` }}
                              />
                            </div>
                          </div>
                        </td>
                        <td className="font-mono tabular-nums">{fmtInt(r.requests)}</td>
                        <td className="font-mono tabular-nums">{fmtTokens(r.tokens)}</td>
                        <td className="font-mono tabular-nums">{fmtUsd(r.cost)}</td>
                        <td className="font-mono tabular-nums">{r.avgTps.toFixed(1)}</td>
                        <td className={`font-mono tabular-nums ${r.errs > 0 ? "text-red-400" : ""}`}>
                          {fmtInt(r.errs)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      {/* Requests table */}
      <Card className="mt-4">
        <CardHeader
          title="Requests"
          right={
            <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
              {fmtInt(sorted.length)} rows · {filterGroup
                ? `filtered by ${filterGroup.dim}: ${filterGroup.name}`
                : "click a column to sort"}
            </span>
          }
        />
        {sorted.length === 0 ? (
          <EmptyState>No requests in this window.</EmptyState>
        ) : (
          <Table
            head={[
              <SortHeader key="time" label="time" k="time" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="key" label="key" k="key" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="model" label="model" k="model" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="provider" label="provider" k="provider" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="status" label="status" k="status" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="tok_in" label="tok in" k="tok_in" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="cached" label="cached" k="cached" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="reasoning" label="reasoning" k="reasoning" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="out" label="out" k="out" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="tps" label="tps" k="tps" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="ttft" label="ttft" k="ttft" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="latency" label="latency" k="latency" active={sortKey} dir={sortDir} onSort={onSort} />,
              <SortHeader key="cost" label="cost" k="cost" active={sortKey} dir={sortDir} onSort={onSort} />,
            ]}
          >
            {sorted.map((l) => (
              <tr key={l.request_id}>
                <TD className="font-mono text-[12px] text-[var(--admin-text-dim)]">{fmtDateTime(l.ts)}</TD>
                <TD>{l.key_alias || "(none)"}</TD>
                <TD>{l.model_group}</TD>
                <TD className="text-[var(--admin-text-muted)]">{l.provider}</TD>
                <TD>
                  <Badge tone={statusTone(l.status)}>{l.status}</Badge>
                </TD>
                <TD className="font-mono tabular-nums">{fmtInt(l.tok_in)}</TD>
                <TD className="font-mono tabular-nums">{fmtInt(l.tok_cached)}</TD>
                <TD className="font-mono tabular-nums">{fmtInt(l.tok_reasoning)}</TD>
                <TD className="font-mono tabular-nums">{fmtInt(l.tok_out)}</TD>
                <TD className="font-mono tabular-nums">{l.tps > 0 ? l.tps.toFixed(1) : "—"}</TD>
                <TD className="font-mono tabular-nums">{l.ttft_ms > 0 ? `${Math.round(l.ttft_ms)} ms` : "—"}</TD>
                <TD className="font-mono tabular-nums">{fmtInt(l.latency_ms)} ms</TD>
                <TD className="font-mono tabular-nums">{fmtUsd(l.cost)}</TD>
              </tr>
            ))}
            <tr className="border-t border-[var(--admin-border)] bg-white/[0.02] font-medium">
              <TD colSpan={5}>Totals · {fmtInt(requests)} requests</TD>
              <TD className="font-mono tabular-nums">{fmtInt(tokIn)}</TD>
              <TD className="font-mono tabular-nums">{fmtInt(tokCached)}</TD>
              <TD className="font-mono tabular-nums">{fmtInt(tokReasoning)}</TD>
              <TD className="font-mono tabular-nums">{fmtInt(tokOut)}</TD>
              <TD className="font-mono tabular-nums">{avgTps.toFixed(1)}</TD>
              <TD className="font-mono tabular-nums text-[var(--admin-text-dim)]">—</TD>
              <TD className="font-mono tabular-nums text-[var(--admin-text-dim)]">—</TD>
              <TD className="font-mono tabular-nums">{fmtUsd(cost)}</TD>
            </tr>
          </Table>
        )}
      </Card>
    </div>
  );
}
