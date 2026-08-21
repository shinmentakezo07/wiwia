// Usage — range-filtered totals, TPS trend, token-share donut, group-by
// summary, and a sortable per-request table with a totals footer.

import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getOverview, getRequestLogs, getTimeseries } from "@/api/client";
import type { RequestLogEntry, TpsBucket } from "@/api/types";
import {
  Badge,
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
import { fmtDateTime, fmtInt, fmtPct, fmtTime, fmtTokens, fmtUsd, groupBy, mean } from "@/lib/format";

const PIE_COLORS = ["#3b82f6", "#34d399", "#a855f7", "#fbbf24"];

const TOOLTIP_STYLE: CSSProperties = {
  backgroundColor: "rgba(10,10,10,0.96)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: "10px",
  fontSize: "12px",
  color: "#e5e7eb",
};

const RANGE_OPTIONS = [
  { value: "15", label: "Last 15 min" },
  { value: "60", label: "Last hour" },
  { value: "360", label: "Last 6 hours" },
  { value: "1440", label: "Last 24 hours" },
];

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

export function UsagePage() {
  const [range, setRange] = useState(60);
  const [groupDim, setGroupDim] = useState<GroupDim>("model");
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

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

  const logs = useMemo(() => {
    const all = logsQuery.data?.logs ?? [];
    const cutoff = Math.floor(Date.now() / 1000) - range * 60;
    return all.filter((l) => l.ts >= cutoff);
  }, [logsQuery.data, range]);

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
    };
    for (const l of logs) {
      t.tokIn += l.tok_in;
      t.tokCached += l.tok_cached;
      t.tokReasoning += l.tok_reasoning;
      t.tokOut += l.tok_out;
      t.cost += l.cost;
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
    return rows.sort((a, b) => b.requests - a.requests);
  }, [logs, groupDim]);

  const tpsBuckets = useMemo<TpsBucket[]>(
    () => (tpsQuery.data?.buckets ?? []) as TpsBucket[],
    [tpsQuery.data],
  );

  const share = useMemo<ShareSlice[]>(
    () =>
      [
        { name: "input", value: totals.tokIn },
        { name: "cached", value: totals.tokCached },
        { name: "reasoning", value: totals.tokReasoning },
        { name: "output", value: totals.tokOut },
      ].filter((s) => s.value > 0),
    [totals],
  );

  const totalTokens = totals.tokIn + totals.tokCached + totals.tokReasoning + totals.tokOut;
  const o = overviewQuery.data;

  const onSort = (k: SortKey) => {
    if (k === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(k);
      setSortDir("desc");
    }
  };

  return (
    <div>
      <PageHeader
        title="Usage"
        subtitle="Per-request usage detail"
        right={
          <Select
            value={String(range)}
            onChange={(v) => setRange(Number(v))}
            options={RANGE_OPTIONS}
          />
        }
      />

      {logsQuery.error && <ErrorText>{logsQuery.error.message}</ErrorText>}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="requests"
          value={fmtInt(totals.requests)}
          sub={o ? `error rate ${fmtPct(o.error_rate)}` : `${fmtInt(totals.errors)} errors`}
        />
        <StatCard label="total tokens" value={fmtTokens(totalTokens)} />
        <StatCard label="input" value={fmtTokens(totals.tokIn)} />
        <StatCard label="cached" value={fmtTokens(totals.tokCached)} />
        <StatCard label="reasoning" value={fmtTokens(totals.tokReasoning)} />
        <StatCard label="output" value={fmtTokens(totals.tokOut)} />
        <StatCard label="cost" value={fmtUsd(totals.cost)} />
        <StatCard label="avg TPS" value={totals.avgTps.toFixed(1)} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="TPS over time" />
          <div className="h-[260px] p-3">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={tpsBuckets} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff" strokeOpacity={0.08} vertical={false} />
                <XAxis
                  dataKey="t"
                  tickFormatter={(t: number) => fmtTime(t)}
                  minTickGap={48}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                />
                <YAxis width={36} tick={{ fontSize: 11, fill: "#6b7280" }} tickLine={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => Number(v).toFixed(1)} />
                <Legend wrapperStyle={{ fontSize: 12, color: "#9ca3af" }} />
                <Line
                  type="monotone"
                  dataKey="tps_avg"
                  name="avg tps"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="tps_p95"
                  name="p95 tps"
                  stroke="#a855f7"
                  strokeWidth={2}
                  strokeDasharray="4 4"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card>
          <CardHeader title="Token share" />
          <div className="h-[260px] p-3">
            {share.length === 0 ? (
              <EmptyState>No tokens recorded in this window.</EmptyState>
            ) : (
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
                    {share.map((s, i) => (
                      <Cell key={s.name} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <text
                    x="50%"
                    y="50%"
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fill="#e5e7eb"
                    className="text-sm font-semibold"
                  >
                    {fmtTokens(totalTokens)}
                  </text>
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => fmtInt(Number(v))} />
                  <Legend wrapperStyle={{ fontSize: 12, color: "#9ca3af" }} />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader
          title="Grouped summary"
          right={
            <Select
              value={groupDim}
              onChange={(v) => setGroupDim(v as GroupDim)}
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
          <Table head={[groupDim, "requests", "tokens", "cost", "avg tps", "errors"]}>
            {groupRows.map((r) => (
              <tr key={r.name}>
                <TD className="font-medium">{r.name}</TD>
                <TD className="font-mono tabular-nums">{fmtInt(r.requests)}</TD>
                <TD className="font-mono tabular-nums">{fmtTokens(r.tokens)}</TD>
                <TD className="font-mono tabular-nums">{fmtUsd(r.cost)}</TD>
                <TD className="font-mono tabular-nums">{r.avgTps.toFixed(1)}</TD>
                <TD className={`font-mono tabular-nums ${r.errs > 0 ? "text-red-400" : ""}`}>
                  {fmtInt(r.errs)}
                </TD>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card className="mt-4">
        <CardHeader
          title="Requests"
          right={
            <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
              {fmtInt(sorted.length)} rows · click a column to sort
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
              <TD colSpan={5}>Totals · {fmtInt(totals.requests)} requests</TD>
              <TD className="font-mono tabular-nums">{fmtInt(totals.tokIn)}</TD>
              <TD className="font-mono tabular-nums">{fmtInt(totals.tokCached)}</TD>
              <TD className="font-mono tabular-nums">{fmtInt(totals.tokReasoning)}</TD>
              <TD className="font-mono tabular-nums">{fmtInt(totals.tokOut)}</TD>
              <TD className="font-mono tabular-nums">{totals.avgTps.toFixed(1)}</TD>
              <TD className="font-mono tabular-nums text-[var(--admin-text-dim)]">—</TD>
              <TD className="font-mono tabular-nums text-[var(--admin-text-dim)]">—</TD>
              <TD className="font-mono tabular-nums">{fmtUsd(totals.cost)}</TD>
            </tr>
          </Table>
        )}
      </Card>
    </div>
  );
}
