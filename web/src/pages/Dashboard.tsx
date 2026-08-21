// Dashboard — live gateway overview: headline stats, an SSE-fed per-minute
// sparkline, stacked token throughput, and requests/errors per minute.

import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  Brain,
  DollarSign,
  Gauge,
  Percent,
  Timer,
  Zap,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getOverview, getRequestLogs, getTimeseries } from "@/api/client";
import { useAdminStream } from "@/api/stream";
import type { RequestLogEntry, TokenBucket } from "@/api/types";
import { Card, CardHeader, ErrorText, PageHeader, Spinner, StatCard } from "@/components/ui";
import { fmtInt, fmtPct, fmtTime, fmtTokens, fmtUsd } from "@/lib/format";

const LIVE_WINDOW = 30;
const SPARK_W = 280;
const SPARK_H = 56;

const COLORS = {
  tokIn: "#3b82f6",
  tokCached: "#199e70",
  tokReasoning: "#a855f7",
  tokOut: "#c98500",
  requests: "#3b82f6",
  errors: "#e66767",
};

const TOOLTIP_STYLE: CSSProperties = {
  backgroundColor: "rgba(10,10,10,0.96)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: "10px",
  fontSize: "12px",
  color: "#e5e7eb",
};

interface LiveBucket {
  minute: number; // epoch minutes
  reqs: number;
  errs: number;
}

interface MinuteCounts {
  t: number; // epoch seconds
  requests: number;
  errors: number;
}

function eventStatus(data: unknown): number {
  if (typeof data === "object" && data !== null && "status" in data) {
    const s = (data as { status: unknown }).status;
    if (typeof s === "number") return s;
  }
  return 0;
}

/** Bucket logs into the last 30 one-minute slots (oldest first, zero-filled). */
function bucketLiveMinutes(logs: RequestLogEntry[]): LiveBucket[] {
  const nowMin = Math.floor(Date.now() / 60_000);
  const byMinute = new Map<number, LiveBucket>();
  for (const l of logs) {
    const m = Math.floor(l.ts / 60);
    if (m < nowMin - (LIVE_WINDOW - 1)) continue;
    const b = byMinute.get(m) ?? { minute: m, reqs: 0, errs: 0 };
    b.reqs += 1;
    if (l.status >= 400) b.errs += 1;
    byMinute.set(m, b);
  }
  const out: LiveBucket[] = [];
  for (let i = LIVE_WINDOW - 1; i >= 0; i--) {
    const m = nowMin - i;
    out.push(byMinute.get(m) ?? { minute: m, reqs: 0, errs: 0 });
  }
  return out;
}

/** Slide the rolling window to "now", zero-filling minutes without events. */
function liveSeries(buckets: LiveBucket[]): LiveBucket[] {
  const nowMin = Math.floor(Date.now() / 60_000);
  const out: LiveBucket[] = [];
  for (let i = LIVE_WINDOW - 1; i >= 0; i--) {
    const m = nowMin - i;
    out.push(buckets.find((b) => b.minute === m) ?? { minute: m, reqs: 0, errs: 0 });
  }
  return out;
}

function sparkPoints(values: number[], max: number): Array<[number, number]> {
  const n = values.length;
  return values.map((v, i) => {
    const x = n <= 1 ? 0 : (i / (n - 1)) * SPARK_W;
    const y = SPARK_H - 2 - (max > 0 ? v / max : 0) * (SPARK_H - 4);
    return [x, y];
  });
}

function LiveSparkline(props: { series: LiveBucket[]; connected: boolean }) {
  const reqs = props.series.map((b) => b.reqs);
  const errs = props.series.map((b) => b.errs);
  const maxReqs = Math.max(1, ...reqs);
  const maxErrs = Math.max(1, ...errs);
  const reqPts = sparkPoints(reqs, maxReqs);
  const errPts = sparkPoints(errs, maxErrs);
  const toStr = (pts: Array<[number, number]>) =>
    pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const areaPoly = [`0,${SPARK_H}`, toStr(reqPts), `${SPARK_W},${SPARK_H}`].join(" ");
  const last = reqPts[reqPts.length - 1] ?? [SPARK_W, SPARK_H - 2];
  return (
    <Card>
      <CardHeader
        title="live · last 30 min"
        right={
          <span className="admin-live-badge">
            <span
              className={
                props.connected ? "admin-pulse-dot" : "h-1.5 w-1.5 rounded-full bg-zinc-600"
              }
            />
            {props.connected ? "streaming" : "offline"}
          </span>
        }
      />
      <div className="relative px-4 pb-3 pt-4">
        <svg viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} preserveAspectRatio="none" className="block h-16 w-full">
          <defs>
            <linearGradient id="wiwi-spark-req" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLORS.requests} stopOpacity={0.32} />
              <stop offset="100%" stopColor={COLORS.requests} stopOpacity={0} />
            </linearGradient>
          </defs>
          <polygon points={areaPoly} fill="url(#wiwi-spark-req)" />
          <polyline
            points={toStr(errPts)}
            fill="none"
            stroke={COLORS.errors}
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity={0.9}
            vectorEffect="non-scaling-stroke"
          />
          <polyline
            points={toStr(reqPts)}
            fill="none"
            stroke={COLORS.requests}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
        <span
          aria-hidden
          className={`pointer-events-none absolute right-[12px] h-2 w-2 -translate-y-1/2 rounded-full ring-4 ${
            props.connected ? "bg-blue-500 ring-blue-500/20" : "bg-zinc-600 ring-zinc-600/20"
          }`}
          style={{ top: `calc(1rem + ${(last[1] / SPARK_H) * 4}rem)` }}
        />
        <div className="mt-2 flex gap-4 text-xs text-[var(--admin-text-dim)]">
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded" style={{ backgroundColor: COLORS.requests }} /> requests
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded" style={{ backgroundColor: COLORS.errors }} /> errors
          </span>
        </div>
      </div>
    </Card>
  );
}

export function DashboardPage() {
  const overviewQuery = useQuery({
    queryKey: ["overview", 60],
    queryFn: () => getOverview(60),
    refetchInterval: 10_000,
  });
  const tokensQuery = useQuery({
    queryKey: ["tokens-ts"],
    queryFn: () => getTimeseries("tokens", 60),
    refetchInterval: 10_000,
  });
  const logsQuery = useQuery({
    queryKey: ["request-logs"],
    queryFn: getRequestLogs,
    refetchInterval: 10_000,
  });

  // Rolling per-minute counts, seeded once from the request-log poll and then
  // kept live by SSE "log.created" events. A counter state forces re-renders.
  const liveRef = useRef<LiveBucket[]>([]);
  const liveSeededRef = useRef(false);
  const [, bumpLive] = useState(0);

  useEffect(() => {
    if (liveSeededRef.current || !logsQuery.data) return;
    liveSeededRef.current = true;
    liveRef.current = bucketLiveMinutes(logsQuery.data.logs);
  }, [logsQuery.data]);

  const connected = useAdminStream("log.created", (data) => {
    const arr = liveRef.current;
    const nowMin = Math.floor(Date.now() / 60_000);
    let last = arr[arr.length - 1];
    if (!last || last.minute < nowMin) {
      last = { minute: nowMin, reqs: 0, errs: 0 };
      arr.push(last);
      while (arr.length > LIVE_WINDOW) arr.shift();
    }
    if (last.minute === nowMin) {
      last.reqs += 1;
      if (eventStatus(data) >= 400) last.errs += 1;
    }
    bumpLive((t) => t + 1);
  });

  const live = liveSeries(liveRef.current);

  const tokenBuckets = useMemo<TokenBucket[]>(
    () => (tokensQuery.data?.buckets ?? []) as TokenBucket[],
    [tokensQuery.data],
  );

  const reqSeries = useMemo<MinuteCounts[]>(() => {
    const nowMin = Math.floor(Date.now() / 60_000);
    const counts = new Map<number, { requests: number; errors: number }>();
    for (const l of logsQuery.data?.logs ?? []) {
      const m = Math.floor(l.ts / 60);
      if (m < nowMin - (LIVE_WINDOW - 1)) continue;
      const c = counts.get(m) ?? { requests: 0, errors: 0 };
      c.requests += 1;
      if (l.status >= 400) c.errors += 1;
      counts.set(m, c);
    }
    const out: MinuteCounts[] = [];
    for (let i = LIVE_WINDOW - 1; i >= 0; i--) {
      const m = nowMin - i;
      const c = counts.get(m) ?? { requests: 0, errors: 0 };
      out.push({ t: m * 60, requests: c.requests, errors: c.errors });
    }
    return out;
  }, [logsQuery.data]);

  const o = overviewQuery.data;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle={o ? `Gateway activity · last ${o.window_minutes} min` : "Gateway activity"}
      />
      {overviewQuery.isLoading && (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      )}
      {overviewQuery.error && <ErrorText>{overviewQuery.error.message}</ErrorText>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          featured
          icon={Activity}
          tone="brand"
          label="req / min"
          value={o ? o.requests_per_minute.toFixed(1) : "—"}
          sub={o ? `${fmtInt(o.requests)} requests` : undefined}
        />
        <StatCard
          featured
          icon={DollarSign}
          label="spend"
          value={o ? fmtUsd(o.cost) : "—"}
          sub={o ? `saved ${fmtUsd(o.cache_savings)}` : undefined}
        />
        <StatCard
          featured
          icon={AlertTriangle}
          tone={o && o.error_rate > 0 ? "danger" : "success"}
          label="error rate"
          value={o ? fmtPct(o.error_rate) : "—"}
          sub={o ? `${fmtInt(o.errors)} errors` : undefined}
        />
        <StatCard
          featured
          icon={Timer}
          label="p95 ttft"
          value={o ? `${Math.round(o.ttft_p95_ms)} ms` : "—"}
          sub={o ? `p95 latency ${fmtInt(o.latency_p95_ms)} ms` : undefined}
        />
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatCard icon={ArrowDownToLine} label="tokens in" value={o ? fmtTokens(o.tok_in) : "—"} />
        <StatCard
          icon={Zap}
          label="cached"
          value={o ? fmtTokens(o.tok_cached) : "—"}
          sub={o ? `${fmtInt(o.cache_hits)} hits` : undefined}
        />
        <StatCard icon={Brain} label="reasoning" value={o ? fmtTokens(o.tok_reasoning) : "—"} />
        <StatCard icon={ArrowUpFromLine} label="out" value={o ? fmtTokens(o.tok_out) : "—"} />
        <StatCard icon={Percent} label="cache-hit %" value={o ? fmtPct(o.cache_hit_rate) : "—"} />
        <StatCard
          icon={Gauge}
          label="avg tps"
          value={o ? o.tps_avg.toFixed(1) : "—"}
          sub={o ? `p95 ${o.tps_p95.toFixed(1)}` : undefined}
        />
      </div>

      <div className="mt-4">
        <LiveSparkline series={live} connected={connected} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Tokens / min" />
          <div className="h-[260px] p-3">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={tokenBuckets} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  {(
                    [
                      ["grad-tok-in", COLORS.tokIn],
                      ["grad-tok-cached", COLORS.tokCached],
                      ["grad-tok-reasoning", COLORS.tokReasoning],
                      ["grad-tok-out", COLORS.tokOut],
                    ] as const
                  ).map(([id, color]) => (
                    <linearGradient key={id} id={id} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={color} stopOpacity={0.45} />
                      <stop offset="100%" stopColor={color} stopOpacity={0.03} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff" strokeOpacity={0.08} vertical={false} />
                <XAxis
                  dataKey="t"
                  tickFormatter={(t: number) => fmtTime(t)}
                  minTickGap={48}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                />
                <YAxis
                  width={44}
                  tickFormatter={(v: number) => fmtTokens(v)}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(v) => fmtInt(Number(v))}
                  cursor={{ stroke: "#3b82f6", strokeOpacity: 0.3, strokeDasharray: "4 4" }}
                />
                <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, color: "#9ca3af" }} />
                <Area
                  type="monotone"
                  dataKey="tok_in"
                  name="input"
                  stackId="1"
                  stroke={COLORS.tokIn}
                  fill="url(#grad-tok-in)"
                  fillOpacity={1}
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="tok_cached"
                  name="cached"
                  stackId="1"
                  stroke={COLORS.tokCached}
                  fill="url(#grad-tok-cached)"
                  fillOpacity={1}
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="tok_reasoning"
                  name="reasoning"
                  stackId="1"
                  stroke={COLORS.tokReasoning}
                  fill="url(#grad-tok-reasoning)"
                  fillOpacity={1}
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="tok_out"
                  name="output"
                  stackId="1"
                  stroke={COLORS.tokOut}
                  fill="url(#grad-tok-out)"
                  fillOpacity={1}
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card>
          <CardHeader title="Requests & errors / min" />
          <div className="h-[260px] p-3">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={reqSeries} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff" strokeOpacity={0.08} vertical={false} />
                <XAxis
                  dataKey="t"
                  tickFormatter={(t: number) => fmtTime(t)}
                  minTickGap={48}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                />
                <YAxis
                  width={28}
                  allowDecimals={false}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  cursor={{ stroke: "#3b82f6", strokeOpacity: 0.3, strokeDasharray: "4 4" }}
                />
                <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, color: "#9ca3af" }} />
                <Line
                  type="monotone"
                  dataKey="requests"
                  stroke={COLORS.requests}
                  strokeWidth={2.5}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                />
                <Line
                  type="monotone"
                  dataKey="errors"
                  stroke={COLORS.errors}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>
    </div>
  );
}
