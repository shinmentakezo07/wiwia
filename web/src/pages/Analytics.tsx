// Analytics — 7-day hourly heatmap, group-by breakdown, spend per key,
// cache savings, and CSV export of the request log.

import { Fragment, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Download } from "lucide-react";
import { getRequestLogs } from "@/api/client";
import type { RequestLogEntry } from "@/api/types";
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorText,
  PageHeader,
  Select,
  StatCard,
  Table,
  TD,
} from "@/components/ui";
import { fmtInt, fmtTokens, fmtUsd, groupBy, mean } from "@/lib/format";

const TOOLTIP_STYLE: CSSProperties = {
  backgroundColor: "rgba(10,10,10,0.96)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: "10px",
  fontSize: "12px",
  color: "#e5e7eb",
};

const GROUP_OPTIONS = [
  { value: "model", label: "by model" },
  { value: "key", label: "by key" },
  { value: "provider", label: "by provider" },
];

const METRIC_OPTIONS = [
  { value: "requests", label: "requests" },
  { value: "tokens", label: "tokens" },
  { value: "cost", label: "cost" },
  { value: "avg_tps", label: "avg tps" },
];

const HOURS = Array.from({ length: 24 }, (_, i) => i);

type GroupDim = "model" | "key" | "provider";
type Metric = "requests" | "tokens" | "cost" | "avg_tps";

interface HeatDay {
  start: number; // epoch sec of local midnight
  label: string; // short weekday
  counts: number[]; // 24 hourly buckets
}

interface BreakdownRow {
  name: string;
  requests: number;
  tokens: number;
  cost: number;
  avgTps: number;
  errs: number;
}

interface KeySpend {
  key: string;
  cost: number;
}

function groupKeyOf(l: RequestLogEntry, dim: GroupDim): string {
  if (dim === "model") return l.model_group;
  if (dim === "key") return l.key_alias;
  return l.provider;
}

function csvEscape(v: string | number): string {
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

function toCsv(logs: RequestLogEntry[]): string {
  const header = [
    "time",
    "key",
    "model",
    "provider",
    "status",
    "tok_in",
    "cached",
    "reasoning",
    "out",
    "tps",
    "ttft_ms",
    "latency_ms",
    "cost",
  ];
  const lines = [header.join(",")];
  for (const l of logs) {
    lines.push(
      [
        new Date(l.ts * 1000).toISOString(),
        l.key_alias,
        l.model_group,
        l.provider,
        l.status,
        l.tok_in,
        l.tok_cached,
        l.tok_reasoning,
        l.tok_out,
        l.tps,
        l.ttft_ms,
        l.latency_ms,
        l.cost,
      ]
        .map(csvEscape)
        .join(","),
    );
  }
  return lines.join("\n");
}

export function AnalyticsPage() {
  const [groupDim, setGroupDim] = useState<GroupDim>("model");
  const [metric, setMetric] = useState<Metric>("requests");

  const logsQuery = useQuery({
    queryKey: ["request-logs"],
    queryFn: getRequestLogs,
    refetchInterval: 15_000,
  });
  const logs = useMemo(() => logsQuery.data?.logs ?? [], [logsQuery.data]);

  // 7 rows (days, oldest first) x 24 cols (local hours) request-count grid.
  const heat = useMemo<HeatDay[]>(() => {
    const today = new Date();
    const days: HeatDay[] = [];
    const rowIndex = new Map<number, number>();
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
      const start = Math.floor(d.getTime() / 1000);
      rowIndex.set(start, days.length);
      days.push({
        start,
        label: d.toLocaleDateString([], { weekday: "short" }),
        counts: new Array<number>(24).fill(0),
      });
    }
    for (const l of logs) {
      const d = new Date(l.ts * 1000);
      const start = Math.floor(new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000);
      const row = rowIndex.get(start);
      if (row === undefined) continue;
      days[row].counts[d.getHours()] += 1;
    }
    return days;
  }, [logs]);

  const heatMax = Math.max(1, ...heat.flatMap((d) => d.counts));

  const breakdown = useMemo<BreakdownRow[]>(() => {
    const rows: BreakdownRow[] = [];
    for (const [name, rs] of groupBy(logs, (l) => groupKeyOf(l, groupDim))) {
      rows.push({
        name,
        requests: rs.length,
        tokens: rs.reduce((a, r) => a + r.tok_in + r.tok_cached + r.tok_reasoning + r.tok_out, 0),
        cost: rs.reduce((a, r) => a + r.cost, 0),
        avgTps: mean(rs.filter((r) => r.tps > 0).map((r) => r.tps)),
        errs: rs.filter((r) => r.status >= 400).length,
      });
    }
    const metricOf = (r: BreakdownRow): number =>
      metric === "requests"
        ? r.requests
        : metric === "tokens"
          ? r.tokens
          : metric === "cost"
            ? r.cost
            : r.avgTps;
    return rows.sort((a, b) => metricOf(b) - metricOf(a));
  }, [logs, groupDim, metric]);

  const spend = useMemo<KeySpend[]>(() => {
    const rows: KeySpend[] = [];
    for (const [name, rs] of groupBy(logs, (l) => l.key_alias || "(none)")) {
      rows.push({ key: name, cost: rs.reduce((a, r) => a + r.cost, 0) });
    }
    return rows.sort((a, b) => b.cost - a.cost);
  }, [logs]);

  const stats = useMemo(() => {
    return {
      cost: logs.reduce((a, l) => a + l.cost, 0),
      avgTps: mean(logs.filter((l) => l.tps > 0).map((l) => l.tps)),
      savings: logs.reduce((a, l) => a + l.cache_savings, 0),
    };
  }, [logs]);

  const exportCsv = () => {
    const blob = new Blob([toCsv(logs)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `wiwi-analytics-${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const hotCol = "font-semibold text-blue-400";

  return (
    <div>
      <PageHeader
        title="Analytics"
        subtitle="Historical patterns across the request log"
        right={
          <Button onClick={exportCsv} disabled={logs.length === 0}>
            <Download size={14} /> Export CSV
          </Button>
        }
      />

      {logsQuery.error && <ErrorText>{logsQuery.error.message}</ErrorText>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="requests" value={fmtInt(logs.length)} sub="logged requests" />
        <StatCard label="spend" value={fmtUsd(stats.cost)} />
        <StatCard label="avg TPS" value={stats.avgTps.toFixed(1)} />
        <StatCard label="cache savings" value={fmtUsd(stats.savings)} sub="estimated vs uncached read" />
      </div>

      <Card className="mt-4">
        <CardHeader title="Requests by hour · last 7 days" />
        <div className="p-4">
          {logs.length === 0 ? (
            <EmptyState>No requests logged yet.</EmptyState>
          ) : (
            <>
              <div
                className="grid items-center gap-1 text-[10px] text-[var(--admin-text-dim)]"
                style={{ gridTemplateColumns: "2.75rem repeat(24, minmax(0, 1fr))" }}
              >
                <div />
                {HOURS.map((h) => (
                  <div key={h} className="text-center leading-none">
                    {h % 6 === 0 ? String(h).padStart(2, "0") : ""}
                  </div>
                ))}
                {heat.map((d) => (
                  <Fragment key={d.start}>
                    <div className="pr-2 text-right font-mono text-[11px] leading-6 text-[var(--admin-text-dim)]">
                      {d.label}
                    </div>
                    {d.counts.map((c, h) => (
                      <div
                        key={h}
                        title={`${d.label} ${String(h).padStart(2, "0")}:00 · ${fmtInt(c)} requests`}
                        className={`aspect-square rounded-sm ${c === 0 ? "bg-white/[0.03]" : ""}`}
                        style={
                          c > 0 ? { backgroundColor: `rgba(59,130,246,${c / heatMax})` } : undefined
                        }
                      />
                    ))}
                  </Fragment>
                ))}
              </div>
              <div className="mt-3 flex items-center justify-end gap-1.5 text-[11px] text-[var(--admin-text-dim)]">
                <span>less</span>
                <span className="h-3 w-3 rounded-sm bg-white/[0.03]" />
                {[0.25, 0.5, 0.75, 1].map((alpha) => (
                  <span
                    key={alpha}
                    className="h-3 w-3 rounded-sm"
                    style={{ backgroundColor: `rgba(59,130,246,${alpha})` }}
                  />
                ))}
                <span>more</span>
              </div>
            </>
          )}
        </div>
      </Card>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Breakdown"
            right={
              <div className="flex items-center gap-2">
                <Select value={groupDim} onChange={(v) => setGroupDim(v as GroupDim)} options={GROUP_OPTIONS} />
                <Select value={metric} onChange={(v) => setMetric(v as Metric)} options={METRIC_OPTIONS} />
              </div>
            }
          />
          {breakdown.length === 0 ? (
            <EmptyState>No requests logged yet.</EmptyState>
          ) : (
            <Table head={[groupDim, "requests", "tokens", "cost", "avg tps", "errors"]}>
              {breakdown.map((r) => (
                <tr key={r.name}>
                  <TD className="font-medium">{r.name}</TD>
                  <TD className={`font-mono tabular-nums ${metric === "requests" ? hotCol : ""}`}>
                    {fmtInt(r.requests)}
                  </TD>
                  <TD className={`font-mono tabular-nums ${metric === "tokens" ? hotCol : ""}`}>
                    {fmtTokens(r.tokens)}
                  </TD>
                  <TD className={`font-mono tabular-nums ${metric === "cost" ? hotCol : ""}`}>
                    {fmtUsd(r.cost)}
                  </TD>
                  <TD className={`font-mono tabular-nums ${metric === "avg_tps" ? hotCol : ""}`}>
                    {r.avgTps.toFixed(1)}
                  </TD>
                  <TD className={`font-mono tabular-nums ${r.errs > 0 ? "text-red-400" : ""}`}>
                    {fmtInt(r.errs)}
                  </TD>
                </tr>
              ))}
            </Table>
          )}
        </Card>

        <Card>
          <CardHeader title="Spend per key" />
          <div className="h-[260px] p-3">
            {spend.length === 0 ? (
              <EmptyState>No spend recorded.</EmptyState>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={spend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#ffffff" strokeOpacity={0.08} vertical={false} />
                  <XAxis
                    dataKey="key"
                    tick={{ fontSize: 11, fill: "#6b7280" }}
                    tickLine={false}
                    tickFormatter={(v: string) => (v.length > 12 ? `${v.slice(0, 11)}…` : v)}
                  />
                  <YAxis
                    width={56}
                    tickFormatter={(v: number) => fmtUsd(v)}
                    tick={{ fontSize: 11, fill: "#6b7280" }}
                    tickLine={false}
                  />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(v) => fmtUsd(Number(v))}
                    cursor={{ fill: "rgba(59,130,246,0.08)" }}
                  />
                  <Bar dataKey="cost" name="spend" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
