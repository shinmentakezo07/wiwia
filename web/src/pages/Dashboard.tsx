// Dashboard — live gateway overview: headline stats, an SSE-fed per-second
// pulse meter, a per-minute sparkline, stacked token throughput, requests and
// errors per minute, plus gauge/share/health breakdowns of where the traffic
// and the money actually go.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  Brain,
  DollarSign,
  Gauge,
  KeyRound,
  Percent,
  PieChart,
  Server,
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
import { getOverview, getProviders, getRequestLogs, getTimeseries } from "@/api/client";
import { useAuth } from "@/api/auth";
import { useAdminStream, useLiveInvalidation } from "@/api/stream";
import type { RequestLogEntry, TokenBucket } from "@/api/types";
import { Card, CardHeader, ErrorText, LiveBadge, PageHeader, Spinner, StatCard } from "@/components/ui";
import {
  GaugeRing,
  HealthGrid,
  LivePulseMeter,
  ShareBars,
  StatusMix,
  TokenRibbon,
  LatencyRibbon,
  VisualCard,
  latencyProfile,
  statusSlices,
} from "@/components/console-visuals";
import type { HealthItem, PulseEvent, ShareRow } from "@/components/console-visuals";
import { fmtInt, fmtPct, fmtTime, fmtTokens, fmtUsd, groupBy, mean } from "@/lib/format";
import { deltaVsPrevHour, hourlySeries } from "@/lib/dashboard-metrics";

const LIVE_WINDOW = 30;
/** Trailing window the per-second pulse meter keeps in memory. */
const PULSE_KEEP_S = 180;
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
              <stop offset="0%" stopColor={COLORS.requests} stopOpacity={0.18} />
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

/** Map request logs to {t, v} points for hourlySeries and window sums. */
function mkPoints(logs: RequestLogEntry[], vOf: (l: RequestLogEntry) => number) {
  return logs.map((l) => ({ t: l.ts, v: vOf(l) }));
}

/**
 * Rank request logs by a dimension into share-bar rows, valued by volume.
 * `fmt` receives the rows in a group and returns the right-hand label.
 */
function groupOf(
  logs: RequestLogEntry[],
  keyOf: (l: RequestLogEntry) => string,
  fmt: (rows: RequestLogEntry[]) => { display: string; sub?: string },
): ShareRow[] {
  const rows: ShareRow[] = [];
  for (const [name, rs] of groupBy(logs, keyOf)) {
    rows.push({ name, value: rs.length, ...fmt(rs) });
  }
  return rows.sort((a, b) => b.value - a.value);
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

export function DashboardPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
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

  // Live SSE invalidation: refresh overview/timeseries/logs the moment a
  // request lands, instead of waiting up to 10s for the next poll.
  useLiveInvalidation(["overview", "tokens-ts", "request-logs"]);

  // Rolling per-minute counts, seeded once from the request-log poll and then
  // kept live by SSE "log.created" events. A counter state forces re-renders.
  const liveRef = useRef<LiveBucket[]>([]);
  const liveSeededRef = useRef(false);
  const lastDataRef = useRef<typeof logsQuery.data>(undefined);
  const [, bumpLive] = useState(0);

  useEffect(() => {
    if (!logsQuery.data) return;
    // Seed from the request-log poll. Re-seed whenever a fresh poll arrives
    // and the live buckets are entirely stale (no SSE activity since the last
    // seed), so an SSE disconnect doesn't freeze the sparkline at old data.
    const dataChanged = lastDataRef.current !== logsQuery.data;
    lastDataRef.current = logsQuery.data;
    if (!dataChanged && liveSeededRef.current) return;
    const nowMin = Math.floor(Date.now() / 60_000);
    const arr = liveRef.current;
    const last = arr[arr.length - 1];
    const stale = !last || last.minute < nowMin;
    if (stale) {
      liveSeededRef.current = true;
      liveRef.current = bucketLiveMinutes(logsQuery.data.logs);
    }
  }, [logsQuery.data]);

  // Per-second ring for the pulse meter: every SSE event is appended with its
  // own timestamp, and the ring is trimmed to the trailing PULSE_KEEP_S. Held
  // in state (not a ref) so the memo below recomputes when the tail grows.
  const [pulseLive, setPulseLive] = useState<PulseEvent[]>([]);

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
    const nowSec = Math.floor(Date.now() / 1000);
    setPulseLive((prev) => {
      const next = [...prev, { ts: nowSec, failed: eventStatus(data) >= 400 }];
      return next.length > 512 ? next.filter((e) => e.ts >= nowSec - PULSE_KEEP_S) : next;
    });
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
  const logs = useMemo(() => logsQuery.data?.logs ?? [], [logsQuery.data]);
  const nowMs = Date.now();
  const hasTraffic = (o?.requests ?? 0) > 0;

  const reqSpark = useMemo(() => hourlySeries(mkPoints(logs, () => 1), Date.now()), [logs]);
  const costSpark = useMemo(() => hourlySeries(mkPoints(logs, (l) => l.cost), Date.now()), [logs]);
  const errSpark = useMemo(
    () => hourlySeries(mkPoints(logs, (l) => (l.status >= 400 ? 1 : 0)), Date.now()),
    [logs],
  );
  const ttftSpark = useMemo(() => hourlySeries(mkPoints(logs, (l) => l.ttft_ms), Date.now()), [logs]);

  // Current vs previous hour totals for the delta chips. The request-log ring
  // holds ~500 events, enough to cover both windows at personal-gateway volume.
  const hourCut = nowMs - 3_600_000;
  const hourPrevStart = nowMs - 7_200_000;
  const sumIn = (pts: Array<{ t: number; v: number }>, lo: number, hi: number) =>
    pts.filter((p) => p.t * 1000 >= lo && p.t * 1000 < hi).reduce((a, p) => a + p.v, 0);

  const reqDelta = deltaVsPrevHour(
    sumIn(mkPoints(logs, () => 1), hourCut, Number.POSITIVE_INFINITY),
    sumIn(mkPoints(logs, () => 1), hourPrevStart, hourCut),
  );
  const costDelta = deltaVsPrevHour(
    sumIn(mkPoints(logs, (l) => l.cost), hourCut, Number.POSITIVE_INFINITY),
    sumIn(mkPoints(logs, (l) => l.cost), hourPrevStart, hourCut),
  );
  const errDelta = deltaVsPrevHour(
    sumIn(mkPoints(logs, (l) => (l.status >= 400 ? 1 : 0)), hourCut, Number.POSITIVE_INFINITY),
    sumIn(mkPoints(logs, (l) => (l.status >= 400 ? 1 : 0)), hourPrevStart, hourCut),
  );

  // ── Breakdowns for the visual panels ────────────────────────────────────
  // All derived from the poll-backed log window already in memory; the SSE
  // pulse ring covers the sub-minute view instead.

  const tokIn = o?.tok_in ?? 0;
  const tokCached = o?.tok_cached ?? 0;
  const tokReasoning = o?.tok_reasoning ?? 0;
  const tokOut = o?.tok_out ?? 0;
  const totalTokens = tokIn + tokCached + tokReasoning + tokOut;

  const tokenParts = useMemo(
    () => [
      { label: "input", value: tokIn, color: COLORS.tokIn },
      { label: "cached", value: tokCached, color: COLORS.tokCached },
      { label: "reasoning", value: tokReasoning, color: COLORS.tokReasoning },
      { label: "output", value: tokOut, color: COLORS.tokOut },
    ],
    [tokIn, tokCached, tokReasoning, tokOut],
  );

  const statusMix = useMemo(
    // Scope the strip to the trailing hour so the legend lines up with the
    // `error rate` headline tile above (which reads from the DB-backed
    // overview, not the entire request-log ring).
    () => {
      const cutoff = Math.floor(Date.now() / 1000) - 3600;
      return statusSlices(logs.filter((l) => l.ts >= cutoff).map((l) => l.status));
    },
    [logs],
  );

  const modelRows = useMemo<ShareRow[]>(
    () =>
      groupOf(logs, (l) => l.model_group, (rs) => ({
        display: `${fmtInt(rs.length)} req`,
        sub: fmtUsd(rs.reduce((a, r) => a + r.cost, 0)),
      })),
    [logs],
  );

  const keyRows = useMemo<ShareRow[]>(
    () =>
      groupOf(logs, (l) => l.key_alias || "(none)", (rs) => ({
        display: fmtUsd(rs.reduce((a, r) => a + r.cost, 0)),
        sub: `${fmtInt(rs.length)} req`,
      })),
    [logs],
  );

  const providerRows = useMemo<ShareRow[]>(
    () =>
      groupOf(logs, (l) => l.provider, (rs) => ({
        display: `${fmtInt(rs.length)} req`,
        sub: `${fmtPct(rs.filter((r) => r.status >= 400).length / rs.length)} err`,
      })),
    [logs],
  );

  const ttftProfile = useMemo(
    () => latencyProfile(logs.map((l) => l.ttft_ms)),
    [logs],
  );

  const pulseEvents = useMemo(() => {
    // Poll-seeded events make the meter readable right after a page load; SSE
    // appends the live tail on top of these (deduped by second so a request
    // counted by both the poll and the stream is not double-drawn).
    const nowSec = Math.floor(Date.now() / 1000);
    const seeded = logs
      .filter((l) => l.ts >= nowSec - 60)
      .map((l) => ({ ts: l.ts, failed: l.status >= 400 }));
    const seen = new Set(seeded.map((e) => e.ts));
    const live = pulseLive.filter((e) => e.ts >= nowSec - 60 && !seen.has(e.ts));
    return [...seeded, ...live];
  }, [logs, pulseLive]);

  // Providers (admin-only endpoint): health grid + key/deployment counters.
  const providersQuery = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
    refetchInterval: 30_000,
    enabled: isAdmin,
  });

  const healthItems = useMemo<HealthItem[]>(
    () =>
      (providersQuery.data?.providers ?? []).map((p) => {
        const keys = p.keys ?? [];
        const cooling = keys.filter((k) => k.status === "cooling").length;
        const enabled = keys.filter((k) => k.enabled).length;
        return {
          name: p.name,
          ok: p.healthy,
          warn: p.healthy && cooling > 0,
          meta: `${enabled}/${keys.length} keys${cooling ? ` · ${cooling} cooling` : ""}`,
          sub: p.provider_type,
        };
      }),
    [providersQuery.data],
  );

  const avgTps = o?.tps_avg ?? mean(logs.filter((l) => l.tps > 0).map((l) => l.tps));
  const streamingShare = logs.length
    ? logs.filter((l) => l.was_stream).length / logs.length
    : 0;
  const cacheHitRate = o?.cache_hit_rate ?? 0;

  return (
    <div
      style={{
        opacity: overviewQuery.isFetching && overviewQuery.data ? 0.7 : 1,
        transition: "opacity 200ms",
      }}
    >
      <PageHeader
        title={isAdmin ? "Dashboard" : "Your dashboard"}
        subtitle={
          isAdmin
            ? o
              ? `Gateway activity · last ${o.window_minutes} min`
              : "Gateway activity"
            : "Usage across your virtual keys"
        }
        right={<LiveBadge connected={connected} />}
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
          numeric={o?.requests_per_minute}
          format={(v) => v.toFixed(1)}
          sub={o ? `${fmtInt(o.requests)} requests` : undefined}
          spark={reqSpark}
          delta={reqDelta}
          deltaGoodDir="up"
          waiting={!hasTraffic}
        />
        <StatCard
          featured
          icon={DollarSign}
          label="spend"
          value={o ? fmtUsd(o.cost) : "—"}
          numeric={o?.cost}
          format={fmtUsd}
          sub={o ? `saved ${fmtUsd(o.cache_savings)}` : undefined}
          spark={costSpark}
          delta={costDelta}
          waiting={!hasTraffic}
        />
        <StatCard
          featured
          icon={AlertTriangle}
          tone={o && o.error_rate > 0 ? "danger" : "success"}
          label="error rate"
          value={o ? fmtPct(o.error_rate) : "—"}
          numeric={o?.error_rate}
          format={fmtPct}
          sub={o ? `${fmtInt(o.errors)} errors` : undefined}
          spark={errSpark}
          delta={errDelta}
          waiting={!hasTraffic}
        />
        <StatCard
          featured
          icon={Timer}
          label="p95 ttft"
          value={o ? `${Math.round(o.ttft_p95_ms)} ms` : "—"}
          numeric={o?.ttft_p95_ms}
          format={(v) => `${Math.round(v)} ms`}
          sub={o ? `p95 latency ${fmtInt(o.latency_p95_ms)} ms` : undefined}
          spark={ttftSpark}
          waiting={!hasTraffic}
        />
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatCard
          icon={ArrowDownToLine}
          label="tokens in"
          value={o ? fmtTokens(o.tok_in) : "—"}
          numeric={o?.tok_in}
          format={fmtTokens}
        />
        <StatCard
          icon={Zap}
          label="cached"
          value={o ? fmtTokens(o.tok_cached) : "—"}
          numeric={o?.tok_cached}
          format={fmtTokens}
          sub={o ? `${fmtInt(o.cache_hits)} hits` : undefined}
        />
        <StatCard
          icon={Brain}
          label="reasoning"
          value={o ? fmtTokens(o.tok_reasoning) : "—"}
          numeric={o?.tok_reasoning}
          format={fmtTokens}
        />
        <StatCard
          icon={ArrowUpFromLine}
          label="out"
          value={o ? fmtTokens(o.tok_out) : "—"}
          numeric={o?.tok_out}
          format={fmtTokens}
        />
        <StatCard
          icon={Percent}
          label="cache-hit %"
          value={o ? fmtPct(o.cache_hit_rate) : "—"}
          numeric={o?.cache_hit_rate}
          format={fmtPct}
        />
        <StatCard
          icon={Gauge}
          label="avg tps"
          value={o ? o.tps_avg.toFixed(1) : "—"}
          numeric={o?.tps_avg}
          format={(v) => v.toFixed(1)}
          sub={o ? `p95 ${o.tps_p95.toFixed(1)}` : undefined}
        />
      </div>

      {/* Live pulse meter: one bar per 2s across the trailing minute. SSE
          events land here directly, so the meter moves between polls. */}
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <VisualCard
          title="live pulse"
          subtitle="requests per 2s · last 60s"
          icon={Activity}
          className="xl:col-span-2"
          right={
            <span className="admin-live-badge">
              <span
                className={
                  connected ? "admin-pulse-dot" : "h-1.5 w-1.5 rounded-full bg-zinc-600"
                }
              />
              {connected ? "streaming" : "offline"}
            </span>
          }
        >
          <LivePulseMeter
            events={pulseEvents}
            connected={connected}
            seconds={60}
            slotSeconds={2}
          />
        </VisualCard>

        <VisualCard title="system gauges" subtitle="current window health" icon={Gauge}>
          <div className="grid grid-cols-2 divide-x divide-[var(--admin-border)]">
            <GaugeRing
              value={cacheHitRate}
              label="cache hit"
              center={fmtPct(cacheHitRate)}
              sub={`${fmtInt(o?.cache_hits ?? 0)} hits`}
              color="var(--admin-success)"
              icon={Zap}
              size={124}
            />
            <GaugeRing
              value={streamingShare}
              label="streamed"
              center={fmtPct(streamingShare)}
              sub={`${fmtTokens(totalTokens)} tokens`}
              color="var(--admin-accent-purple)"
              icon={Percent}
              size={124}
            />
          </div>
        </VisualCard>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <VisualCard
          title="token mix"
          subtitle={o ? `last ${o.window_minutes} min` : "current window"}
          icon={PieChart}
        >
          <TokenRibbon parts={tokenParts} total={totalTokens} />
        </VisualCard>

        <VisualCard title="status mix" subtitle="response class share" icon={AlertTriangle}>
          <StatusMix slices={statusMix} total={logs.length} />
        </VisualCard>

        <VisualCard title="ttft profile" subtitle="time to first token" icon={Timer}>
          <LatencyRibbon profile={ttftProfile} color={COLORS.tokReasoning} />
        </VisualCard>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <VisualCard
          title="top models"
          subtitle="requests · spend"
          icon={Server}
          right={
            <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
              {fmtInt(modelRows.length)} groups
            </span>
          }
        >
          <ShareBars
            rows={modelRows}
            limit={6}
            showRank
            emptyLabel="No requests in this window."
          />
        </VisualCard>

        <VisualCard
          title="spend by key"
          subtitle="cost per virtual key"
          icon={KeyRound}
        >
          <ShareBars
            rows={keyRows}
            limit={6}
            barColor="rgba(168,85,247,0.55)"
            emptyLabel="No spend in this window."
          />
        </VisualCard>

      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <VisualCard
          title="provider traffic"
          subtitle="requests · error share"
          icon={Server}
          right={
            <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
              {fmtInt(providerRows.length)} upstreams
            </span>
          }
        >
          <ShareBars
            rows={providerRows}
            limit={6}
            barColor="rgba(52,211,153,0.5)"
            emptyLabel="No requests in this window."
          />
        </VisualCard>

        <VisualCard
          title="provider health"
          subtitle="configured upstreams"
          icon={Server}
          right={
            <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
              avg {avgTps.toFixed(1)} tok/s
            </span>
          }
        >
          <HealthGrid
            items={healthItems}
            emptyLabel={isAdmin ? "No providers configured." : "Admin-only view."}
          />
        </VisualCard>
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
                      <stop offset="0%" stopColor={color} stopOpacity={0.10} />
                      <stop offset="100%" stopColor={color} stopOpacity={0.02} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid stroke="#ffffff" strokeOpacity={0.06} vertical={false} />
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
                  content={<ChartTooltip fmt={(v) => fmtInt(v)} />}
                  cursor={{ stroke: "#3b82f6", strokeOpacity: 0.3 }}
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
                  dataKey="tok_out"
                  name="output"
                  stackId="1"
                  stroke={COLORS.tokOut}
                  fill="url(#grad-tok-out)"
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
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card>
          <CardHeader title="Requests & errors / min" />
          <div className="h-[260px] p-3">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={reqSeries} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#ffffff" strokeOpacity={0.06} vertical={false} />
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
                  content={<ChartTooltip fmt={(v) => fmtInt(v)} />}
                  cursor={{ stroke: "#3b82f6", strokeOpacity: 0.3 }}
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
