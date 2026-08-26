// Analytics — model-scoped metrics with pricing, 7-day hourly heatmap,
// daily trend, distribution donut, group-by breakdown with inline share
// bars, spend per key, token efficiency, and CSV export.

import { Fragment, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  ArrowDownToLine,
  ArrowUpFromLine,
  Database,
  DollarSign,
  Download,
  TrendingUp,
  Pencil,
  Plus,
  Trash2,
  Zap,
} from "lucide-react";
import {
  deletePricing,
  getRequestLogs,
  getPricing,
  upsertPricing,
} from "@/api/client";
import { useLiveInvalidation } from "@/api/stream";
import type { ModelPrice, RequestLogEntry } from "@/api/types";
import {
  Button,
  Card,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorText,
  Field,
  Input,
  LiveBadge,
  PageHeader,
  Select,
  StatCard,
  Table,
  TD,
} from "@/components/ui";
import { fmtInt, fmtTokens, fmtUsd, groupBy, mean } from "@/lib/format";
import { hourlySeries } from "@/lib/dashboard-metrics";

// -- constants --------------------------------------------------------------

const COLORS = {
  requests: "#3b82f6",
  cost: "#a855f7",
  tokens: "#22c55e",
  cached: "#34d399",
  violet: "#7c3aed",
};

const DONUT_PALETTE = [
  "#3b82f6",
  "#a855f7",
  "#22c55e",
  "#f59e0b",
  "#ec4899",
  "#06b6d4",
  "#8b5cf6",
  "#f43f5e",
  "#14b8a6",
  "#eab308",
];

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
  start: number;
  label: string;
  dateLabel: string;
  counts: number[];
  total: number;
}

interface BreakdownRow {
  name: string;
  requests: number;
  tokens: number;
  cost: number;
  avgTps: number;
  errs: number;
  share: number;
}

interface KeySpend {
  key: string;
  cost: number;
}

interface DailyPoint {
  date: string;
  label: string;
  requests: number;
  cost: number;
  tokens: number;
}

// -- helpers ----------------------------------------------------------------

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
    "time", "key", "model", "provider", "status",
    "tok_in", "cached", "reasoning", "out",
    "tps", "ttft_ms", "latency_ms", "cost",
  ];
  const lines = [header.join(",")];
  for (const l of logs) {
    lines.push(
      [
        new Date(l.ts * 1000).toISOString(),
        l.key_alias, l.model_group, l.provider, l.status,
        l.tok_in, l.tok_cached, l.tok_reasoning, l.tok_out,
        l.tps, l.ttft_ms, l.latency_ms, l.cost,
      ].map(csvEscape).join(","),
    );
  }
  return lines.join("\n");
}

/** Interpolate a color between blue and violet by t in [0,1]. */
function heatColor(t: number): string {
  const r1 = 0x3b, g1 = 0x82, b1 = 0xf6;
  const r2 = 0xa8, g2 = 0x55, b2 = 0xf7;
  const r = Math.round(r1 + (r2 - r1) * t);
  const g = Math.round(g1 + (g2 - g1) * t);
  const b = Math.round(b1 + (b2 - b1) * t);
  return `rgba(${r},${g},${b},1)`;
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

function fmtPerMm(v: number): string {
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

// -- pricing card ------------------------------------------------------------

function PricingCard({ price, onEdit }: { price: ModelPrice; onEdit?: () => void }) {
  return (
    <Card className="admin-stat-highlight">
      <CardHeader
        title="Model pricing"
        subtitle={price.model_id}
        right={
          onEdit && (
            <Button variant="outline" onClick={onEdit} className="!px-2.5 !py-1.5 text-[12px]">
              <Pencil size={12} /> Edit
            </Button>
          )
        }
      />
      <div className="grid grid-cols-3 gap-px bg-[var(--admin-border)]">
        <div className="bg-[var(--admin-surface)] p-4">
          <div className="mb-2 flex items-center gap-1.5">
            <ArrowDownToLine size={12} style={{ color: COLORS.requests, opacity: 0.7 }} />
            <span className="admin-label">input</span>
          </div>
          <p className="admin-stat-value font-mono text-[20px]">{fmtPerMm(price.input_per_1m)}</p>
          <p className="mt-1 font-mono text-[10px] text-[var(--admin-text-dim)]">per 1M tokens</p>
        </div>
        <div className="bg-[var(--admin-surface)] p-4">
          <div className="mb-2 flex items-center gap-1.5">
            <ArrowUpFromLine size={12} style={{ color: COLORS.tokens, opacity: 0.7 }} />
            <span className="admin-label">output</span>
          </div>
          <p className="admin-stat-value font-mono text-[20px]">{fmtPerMm(price.output_per_1m)}</p>
          <p className="mt-1 font-mono text-[10px] text-[var(--admin-text-dim)]">per 1M tokens</p>
        </div>
        <div className="bg-[var(--admin-surface)] p-4">
          <div className="mb-2 flex items-center gap-1.5">
            <Database size={12} style={{ color: COLORS.cached, opacity: 0.7 }} />
            <span className="admin-label">cache read</span>
          </div>
          <p className="admin-stat-value font-mono text-[20px]">
            {price.cache_read_per_1m != null ? fmtPerMm(price.cache_read_per_1m) : "—"}
          </p>
          <p className="mt-1 font-mono text-[10px] text-[var(--admin-text-dim)]">per 1M tokens</p>
        </div>
      </div>
      {(price.max_input_tokens || price.max_output_tokens || price.mode) && (
        <div className="flex flex-wrap items-center gap-3 border-t border-[var(--admin-border)] px-4 py-2.5 text-[11px] text-[var(--admin-text-dim)]">
          {price.max_input_tokens && (
            <span className="flex items-center gap-1">
              <span className="admin-label">ctx</span>
              <span className="font-mono">{fmtInt(price.max_input_tokens)}</span>
            </span>
          )}
          {price.max_output_tokens && (
            <span className="flex items-center gap-1">
              <span className="admin-label">max out</span>
              <span className="font-mono">{fmtInt(price.max_output_tokens)}</span>
            </span>
          )}
          {price.mode && (
            <span className="flex items-center gap-1">
              <span className="admin-label">mode</span>
              <span className="font-mono">{price.mode}</span>
            </span>
          )}
        </div>
      )}
    </Card>
  );
}

// -- token efficiency card ---------------------------------------------------

interface TokenEfficiency {
  tokIn: number;
  tokOut: number;
  tokCached: number;
  tokReasoning: number;
  totalTokens: number;
  cost: number;
  actualCostPer1mIn: number;
  actualCostPer1mOut: number;
  cacheSavings: number;
  cacheHitRate: number;
}

function TokenEfficiencyCard({ eff }: { eff: TokenEfficiency }) {
  const rows = [
    { label: "input", value: eff.tokIn, color: COLORS.requests, icon: ArrowDownToLine },
    { label: "output", value: eff.tokOut, color: COLORS.tokens, icon: ArrowUpFromLine },
    { label: "cached", value: eff.tokCached, color: COLORS.cached, icon: Database },
    { label: "reasoning", value: eff.tokReasoning, color: COLORS.violet, icon: Brain },
  ];
  const maxTok = Math.max(1, ...rows.map((r) => r.value));
  return (
    <Card>
      <CardHeader title="Token efficiency" subtitle="Volume and effective cost per 1M tokens" />
      <div className="p-4">
        <div className="space-y-3">
          {rows.map((r) => {
            const Icon = r.icon;
            return (
              <div key={r.label} className="flex items-center gap-3">
                <div className="flex w-24 shrink-0 items-center gap-1.5">
                  <Icon size={11} style={{ color: r.color, opacity: 0.7 }} />
                  <span className="text-[11px] text-[var(--admin-text-muted)]">{r.label}</span>
                </div>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.05]">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{ width: `${(r.value / maxTok) * 100}%`, backgroundColor: r.color }}
                  />
                </div>
                <span className="w-16 text-right font-mono text-[11px] tabular-nums text-[var(--admin-text)]">
                  {fmtTokens(r.value)}
                </span>
              </div>
            );
          })}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-[var(--admin-border)] pt-3">
          <div>
            <span className="admin-label">actual in $/1M</span>
            <p className="mt-0.5 font-mono text-[15px] font-semibold text-[var(--admin-text)]">
              {eff.tokIn > 0 ? fmtPerMm(eff.actualCostPer1mIn) : "—"}
            </p>
          </div>
          <div>
            <span className="admin-label">actual out $/1M</span>
            <p className="mt-0.5 font-mono text-[15px] font-semibold text-[var(--admin-text)]">
              {eff.tokOut > 0 ? fmtPerMm(eff.actualCostPer1mOut) : "—"}
            </p>
          </div>
          <div>
            <span className="admin-label">cache hit rate</span>
            <p className="mt-0.5 font-mono text-[15px] font-semibold text-emerald-400">
              {(eff.cacheHitRate * 100).toFixed(1)}%
            </p>
          </div>
          <div>
            <span className="admin-label">savings</span>
            <p className="mt-0.5 font-mono text-[15px] font-semibold text-emerald-400">
              {fmtUsd(eff.cacheSavings)}
            </p>
          </div>
        </div>
      </div>
    </Card>
  );
}

// small Brain icon inline (lucide doesn't export it in all versions)
function Brain(props: { size?: number; className?: string; style?: CSSProperties }) {
  const s = props.size ?? 12;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={s}
      height={s}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={props.className}
      style={props.style}
    >
      <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" />
      <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" />
    </svg>
  );
}

// -- pricing editor dialog ---------------------------------------------------

interface PriceForm {
  model_id: string;
  input_per_1m: string;
  output_per_1m: string;
  cache_read_per_1m: string;
  max_input_tokens: string;
  max_output_tokens: string;
  mode: string;
}

function emptyForm(): PriceForm {
  return {
    model_id: "",
    input_per_1m: "",
    output_per_1m: "",
    cache_read_per_1m: "",
    max_input_tokens: "",
    max_output_tokens: "",
    mode: "",
  };
}

function formFromPrice(p: ModelPrice): PriceForm {
  return {
    model_id: p.model_id,
    input_per_1m: String(p.input_per_1m),
    output_per_1m: String(p.output_per_1m),
    cache_read_per_1m: p.cache_read_per_1m != null ? String(p.cache_read_per_1m) : "",
    max_input_tokens: p.max_input_tokens != null ? String(p.max_input_tokens) : "",
    max_output_tokens: p.max_output_tokens != null ? String(p.max_output_tokens) : "",
    mode: p.mode ?? "",
  };
}

function tryNum(s: string): number | undefined {
  const t = s.trim();
  if (!t) return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

function PricingDialog(props: {
  open: boolean;
  initial: PriceForm | null;
  isNew: boolean;
  existingIds: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<PriceForm>(emptyForm());
  const [error, setError] = useState<string | null>(null);

  // Sync form whenever the dialog opens with new initial data.
  useEffect(() => {
    if (props.open) setForm(props.initial ?? emptyForm());
    setError(null);
  }, [props.open, props.initial]);

  const save = useMutation({
    mutationFn: (f: PriceForm) =>
      upsertPricing(f.model_id.trim(), {
        input_per_1m: Number(f.input_per_1m) || 0,
        output_per_1m: Number(f.output_per_1m) || 0,
        cache_read_per_1m: tryNum(f.cache_read_per_1m),
        max_input_tokens: tryNum(f.max_input_tokens),
        max_output_tokens: tryNum(f.max_output_tokens),
        mode: f.mode.trim() || undefined,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["pricing"] });
      props.onSaved();
    },
    onError: (e) => setError(e.message),
  });

  const modelId = form.model_id.trim();
  const idValid = props.isNew
    ? modelId.length > 0 && !props.existingIds.includes(modelId)
    : modelId.length > 0;
  const inputValid = tryNum(form.input_per_1m) != null;
  const outputValid = tryNum(form.output_per_1m) != null;
  const canSave = idValid && inputValid && outputValid && !save.isPending;

  const set = (k: keyof PriceForm, v: string) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <Dialog
      open={props.open}
      title={props.isNew ? "Add model pricing" : `Edit · ${props.initial?.model_id ?? ""}`}
      onClose={props.onClose}
      wide
    >
      <div className="space-y-4">
        {props.isNew && (
          <Field label="Model ID" hint="The bare model id, no provider prefix.">
            <Input
              value={form.model_id}
              onChange={(e) => set("model_id", e.target.value)}
              placeholder="model-name"
            />
          </Field>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Input $ / 1M tokens">
            <Input
              value={form.input_per_1m}
              onChange={(e) => set("input_per_1m", e.target.value)}
              placeholder="3.00"
              type="number"
              step="any"
            />
          </Field>
          <Field label="Output $ / 1M tokens">
            <Input
              value={form.output_per_1m}
              onChange={(e) => set("output_per_1m", e.target.value)}
              placeholder="15.00"
              type="number"
              step="any"
            />
          </Field>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Cache read $ / 1M" hint="Optional">
            <Input
              value={form.cache_read_per_1m}
              onChange={(e) => set("cache_read_per_1m", e.target.value)}
              placeholder="0.30"
              type="number"
              step="any"
            />
          </Field>
          <Field label="Max input tokens" hint="Optional">
            <Input
              value={form.max_input_tokens}
              onChange={(e) => set("max_input_tokens", e.target.value)}
              placeholder="200000"
              type="number"
            />
          </Field>
          <Field label="Max output tokens" hint="Optional">
            <Input
              value={form.max_output_tokens}
              onChange={(e) => set("max_output_tokens", e.target.value)}
              placeholder="8192"
              type="number"
            />
          </Field>
        </div>
        <Field label="Mode" hint="Optional, e.g. chat or embedding">
          <Input
            value={form.mode}
            onChange={(e) => set("mode", e.target.value)}
            placeholder="chat"
          />
        </Field>

        {error && <ErrorText>{error}</ErrorText>}

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={props.onClose}>Cancel</Button>
          <Button
            disabled={!canSave}
            onClick={() => save.mutate(form)}
          >
            {save.isPending ? "Saving…" : props.isNew ? "Add pricing" : "Save changes"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// -- pricing management table ------------------------------------------------

function PricingManager(props: {
  models: ModelPrice[];
  onEdit: (p: ModelPrice) => void;
  onAdd: () => void;
}) {
  const qc = useQueryClient();
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const del = useMutation({
    mutationFn: (modelId: string) => deletePricing(modelId),
    onSuccess: () => {
      setConfirmId(null);
      void qc.invalidateQueries({ queryKey: ["pricing"] });
    },
    onError: (e) => {
      setErr(e.message);
      setConfirmId(null);
    },
  });

  return (
    <Card className="mt-4">
      <CardHeader
        title="Model pricing"
        subtitle="Input, output, and cache-read costs per 1M tokens — editable, persisted in the database"
        right={
          <Button onClick={props.onAdd} className="!py-1.5 text-[12px]">
            <Plus size={14} /> Add model
          </Button>
        }
      />
      {err && (
        <div className="px-4 pt-3">
          <ErrorText>{err}</ErrorText>
        </div>
      )}
      {props.models.length === 0 ? (
        <EmptyState>No pricing entries yet. Click “Add model” to define one.</EmptyState>
      ) : (
        <Table head={["Model", "Input $/1M", "Output $/1M", "Cache read $/1M", "Context", "Mode", ""]}>
          {props.models.map((p) => (
            <tr key={p.model_id}>
              <TD className="font-medium text-[var(--admin-text)]">{p.model_id}</TD>
              <TD className="font-mono tabular-nums">{fmtPerMm(p.input_per_1m)}</TD>
              <TD className="font-mono tabular-nums">{fmtPerMm(p.output_per_1m)}</TD>
              <TD className="font-mono tabular-nums">
                {p.cache_read_per_1m != null ? fmtPerMm(p.cache_read_per_1m) : "—"}
              </TD>
              <TD className="font-mono tabular-nums text-[var(--admin-text-dim)]">
                {p.max_input_tokens ? fmtInt(p.max_input_tokens) : "—"}
              </TD>
              <TD className="text-[var(--admin-text-dim)]">{p.mode ?? "—"}</TD>
              <TD>
                {confirmId === p.model_id ? (
                  <div className="flex items-center justify-end gap-2">
                    <span className="text-[11px] text-red-400">Delete?</span>
                    <Button
                      variant="danger"
                      className="!px-2 !py-1 text-[11px]"
                      onClick={() => del.mutate(p.model_id)}
                    >
                      Confirm
                    </Button>
                    <Button
                      variant="ghost"
                      className="!px-2 !py-1 text-[11px]"
                      onClick={() => setConfirmId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      title="Edit"
                      onClick={() => props.onEdit(p)}
                      className="rounded p-1.5 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      title="Delete"
                      onClick={() => setConfirmId(p.model_id)}
                      className="rounded p-1.5 text-[var(--admin-text-dim)] transition-colors hover:bg-red-500/10 hover:text-red-400"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                )}
              </TD>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

// -- main component ---------------------------------------------------------

export function AnalyticsPage() {
  const [groupDim, setGroupDim] = useState<GroupDim>("model");
  const [metric, setMetric] = useState<Metric>("requests");
  const [selectedModel, setSelectedModel] = useState<string>("all");

  const logsQuery = useQuery({
    queryKey: ["request-logs"],
    queryFn: getRequestLogs,
    refetchInterval: 15_000,
  });
  const pricingQuery = useQuery({
    queryKey: ["pricing"],
    queryFn: getPricing,
    staleTime: 5 * 60 * 1000,
  });

  // Live SSE invalidation: refresh request logs immediately when a new request
  // lands, instead of waiting up to 15s for the next poll.
  const connected = useLiveInvalidation(["request-logs"]);

  const allLogs = useMemo(() => logsQuery.data?.logs ?? [], [logsQuery.data]);
  const pricingMap = useMemo(() => {
    const m = new Map<string, ModelPrice>();
    for (const p of pricingQuery.data?.models ?? []) m.set(p.model_id, p);
    return m;
  }, [pricingQuery.data]);

  // Pricing editor dialog state
  const [pricingOpen, setPricingOpen] = useState(false);
  const [pricingInitial, setPricingInitial] = useState<PriceForm | null>(null);
  const [pricingIsNew, setPricingIsNew] = useState(false);
  const allPricing = useMemo(
    () => pricingQuery.data?.models ?? [],
    [pricingQuery.data],
  );
  function openAddPricing() {
    setPricingInitial(emptyForm());
    setPricingIsNew(true);
    setPricingOpen(true);
  }
  function openEditPricing(p: ModelPrice) {
    setPricingInitial(formFromPrice(p));
    setPricingIsNew(false);
    setPricingOpen(true);
  }
  function closePricing() {
    setPricingOpen(false);
    setPricingInitial(null);
  }

  // All model IDs seen in logs + pricing table, for the selector.
  const modelOptions = useMemo(() => {
    const set = new Set<string>();
    for (const l of allLogs) set.add(l.model_group);
    for (const p of pricingQuery.data?.models ?? []) set.add(p.model_id);
    return Array.from(set).sort();
  }, [allLogs, pricingQuery.data]);

  // Filter logs by selected model.
  const logs = useMemo(() => {
    if (selectedModel === "all") return allLogs;
    return allLogs.filter((l) => l.model_group === selectedModel);
  }, [allLogs, selectedModel]);

  // Selected model's pricing entry.
  const selectedPrice = useMemo(() => {
    if (selectedModel === "all") return undefined;
    // Try exact match, then suffix match (e.g. "gpt-4o" matches provider/model id)
    return (
      pricingMap.get(selectedModel) ??
      pricingQuery.data?.models?.find(
        (p) => p.model_id === selectedModel || p.model_id.endsWith(selectedModel),
      )
    );
  }, [selectedModel, pricingMap, pricingQuery.data]);

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
        dateLabel: d.toLocaleDateString([], { month: "short", day: "numeric" }),
        counts: new Array<number>(24).fill(0),
        total: 0,
      });
    }
    for (const l of logs) {
      const d = new Date(l.ts * 1000);
      const start = Math.floor(new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000);
      const row = rowIndex.get(start);
      if (row === undefined) continue;
      days[row].counts[d.getHours()] += 1;
      days[row].total += 1;
    }
    return days;
  }, [logs]);

  const heatMax = Math.max(1, ...heat.flatMap((d) => d.counts));

  // Daily totals for the 7-day trend chart.
  const daily = useMemo<DailyPoint[]>(() => {
    return heat.map((d) => {
      const dayLogs = logs.filter((l) => {
        const ld = new Date(l.ts * 1000);
        const lStart = Math.floor(
          new Date(ld.getFullYear(), ld.getMonth(), ld.getDate()).getTime() / 1000,
        );
        return lStart === d.start;
      });
      return {
        date: d.dateLabel,
        label: d.label,
        requests: dayLogs.length,
        cost: dayLogs.reduce((a, l) => a + l.cost, 0),
        tokens: dayLogs.reduce((a, l) => a + l.tok_in + l.tok_cached + l.tok_reasoning + l.tok_out, 0),
      };
    });
  }, [heat, logs]);

  const breakdown = useMemo<BreakdownRow[]>(() => {
    const rows: BreakdownRow[] = [];
    const totalReqs = logs.length || 1;
    for (const [name, rs] of groupBy(logs, (l) => groupKeyOf(l, groupDim))) {
      rows.push({
        name,
        requests: rs.length,
        tokens: rs.reduce((a, r) => a + r.tok_in + r.tok_cached + r.tok_reasoning + r.tok_out, 0),
        cost: rs.reduce((a, r) => a + r.cost, 0),
        avgTps: mean(rs.filter((r) => r.tps > 0).map((r) => r.tps)),
        errs: rs.filter((r) => r.status >= 400).length,
        share: rs.length / totalReqs,
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

  const donutData = useMemo(() => {
    const metricOf = (r: BreakdownRow): number =>
      metric === "requests"
        ? r.requests
        : metric === "tokens"
          ? r.tokens
          : metric === "cost"
            ? r.cost
            : r.avgTps;
    return breakdown.slice(0, 8).map((r, i) => ({
      name: r.name,
      value: metricOf(r),
      color: DONUT_PALETTE[i % DONUT_PALETTE.length],
    }));
  }, [breakdown, metric]);

  const donutTotal = useMemo(
    () => donutData.reduce((a, d) => a + d.value, 0),
    [donutData],
  );

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
      tokens: logs.reduce((a, l) => a + l.tok_in + l.tok_cached + l.tok_reasoning + l.tok_out, 0),
      errors: logs.filter((l) => l.status >= 400).length,
      errorRate: logs.length ? logs.filter((l) => l.status >= 400).length / logs.length : 0,
    };
  }, [logs]);

  // Token efficiency for the selected model (or all).
  const efficiency = useMemo<TokenEfficiency>(() => {
    const tokIn = logs.reduce((a, l) => a + l.tok_in, 0);
    const tokOut = logs.reduce((a, l) => a + l.tok_out, 0);
    const tokCached = logs.reduce((a, l) => a + l.tok_cached, 0);
    const tokReasoning = logs.reduce((a, l) => a + l.tok_reasoning, 0);
    const cost = logs.reduce((a, l) => a + l.cost, 0);
    const cacheSavings = logs.reduce((a, l) => a + l.cache_savings, 0);
    const cacheHits = logs.filter((l) => l.cache_hit).length;
    return {
      tokIn,
      tokOut,
      tokCached,
      tokReasoning,
      totalTokens: tokIn + tokCached + tokReasoning + tokOut,
      cost,
      actualCostPer1mIn: tokIn > 0 ? (cost / tokIn) * 1_000_000 : 0,
      actualCostPer1mOut: tokOut > 0 ? (cost / tokOut) * 1_000_000 : 0,
      cacheSavings,
      cacheHitRate: logs.length ? cacheHits / logs.length : 0,
    };
  }, [logs]);

  const reqSpark = useMemo(
    () => hourlySeries(logs.map((l) => ({ t: l.ts, v: 1 })), Date.now()),
    [logs],
  );
  const costSpark = useMemo(
    () => hourlySeries(logs.map((l) => ({ t: l.ts, v: l.cost })), Date.now()),
    [logs],
  );
  const tpsSpark = useMemo(
    () => hourlySeries(logs.filter((l) => l.tps > 0).map((l) => ({ t: l.ts, v: l.tps })), Date.now()),
    [logs],
  );
  const savingsSpark = useMemo(
    () => hourlySeries(logs.map((l) => ({ t: l.ts, v: l.cache_savings })), Date.now()),
    [logs],
  );

  const exportCsv = () => {
    const blob = new Blob([toCsv(logs)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `wiwi-analytics-${selectedModel}-${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const hotCol = "font-semibold text-blue-400";

  const metricFmt = (m: Metric) =>
    m === "requests"
      ? fmtInt
      : m === "tokens"
        ? fmtTokens
        : m === "cost"
          ? fmtUsd
          : (v: number) => v.toFixed(1);

  const donutFmt = metricFmt(metric);

  const modelSelectOptions = [
    { value: "all", label: "all models" },
    ...modelOptions.map((m) => ({ value: m, label: m })),
  ];

  return (
    <div>
      <PageHeader
        title="Analytics"
        subtitle="Historical patterns across the request log"
        right={
          <div className="flex items-center gap-2">
            <LiveBadge connected={connected} />
            <Select
              value={selectedModel}
              onChange={setSelectedModel}
              options={modelSelectOptions}
            />
            <Button onClick={exportCsv} disabled={logs.length === 0}>
              <Download size={14} /> Export CSV
            </Button>
          </div>
        }
      />

      {logsQuery.error && <ErrorText>{logsQuery.error.message}</ErrorText>}

      {/* ── Stat cards ─────────────────────────────────────────────────── */}
      <div className="admin-stagger grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          featured
          icon={Activity}
          tone="brand"
          label="requests"
          value={fmtInt(logs.length)}
          sub={selectedModel === "all" ? "all models" : selectedModel}
          spark={reqSpark}
          waiting={logs.length === 0}
        />
        <StatCard
          featured
          icon={DollarSign}
          tone="brand"
          label="spend"
          value={fmtUsd(stats.cost)}
          spark={costSpark}
          waiting={logs.length === 0}
        />
        <StatCard
          featured
          icon={Zap}
          tone="warning"
          label="avg TPS"
          value={stats.avgTps.toFixed(1)}
          spark={tpsSpark}
          waiting={logs.length === 0}
        />
        <StatCard
          featured
          icon={Database}
          tone="success"
          label="cache savings"
          value={fmtUsd(stats.savings)}
          sub="estimated vs uncached read"
          spark={savingsSpark}
          waiting={logs.length === 0}
        />
      </div>

      {/* ── Pricing + Token efficiency (only when a specific model is selected) ── */}
      {selectedPrice && (
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <PricingCard price={selectedPrice} onEdit={() => openEditPricing(selectedPrice)} />
          <TokenEfficiencyCard eff={efficiency} />
        </div>
      )}

      {/* ── When "all models" is selected, show token efficiency for all ── */}
      {selectedModel === "all" && logs.length > 0 && (
        <div className="mt-4">
          <TokenEfficiencyCard eff={efficiency} />
        </div>
      )}

      {/* ── Pricing management (add / edit / delete, persisted in DB) ──── */}
      <PricingManager
        models={allPricing}
        onEdit={openEditPricing}
        onAdd={openAddPricing}
      />
      <PricingDialog
        open={pricingOpen}
        initial={pricingInitial}
        isNew={pricingIsNew}
        existingIds={allPricing.map((p) => p.model_id)}
        onClose={closePricing}
        onSaved={closePricing}
      />

      {/* ── Daily trend ─────────────────────────────────────────────────── */}
      <Card className="mt-4">
        <CardHeader
          title="7-day trend"
          subtitle="Daily requests and cost over the last week"
          right={
            <div className="flex items-center gap-4 text-[11px] text-[var(--admin-text-dim)]">
              <span className="flex items-center gap-1.5">
                <span className="h-0.5 w-4 rounded" style={{ backgroundColor: COLORS.requests }} />
                requests
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-0.5 w-4 rounded" style={{ backgroundColor: COLORS.cost }} />
                cost
              </span>
            </div>
          }
        />
        <div className="h-[220px] p-3">
          {logs.length === 0 ? (
            <EmptyState>No requests logged yet.</EmptyState>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={daily} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="grad-req" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={COLORS.requests} stopOpacity={0.25} />
                    <stop offset="100%" stopColor={COLORS.requests} stopOpacity={0.01} />
                  </linearGradient>
                  <linearGradient id="grad-cost" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={COLORS.cost} stopOpacity={0.18} />
                    <stop offset="100%" stopColor={COLORS.cost} stopOpacity={0.01} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#ffffff" strokeOpacity={0.05} vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  yAxisId="left"
                  width={44}
                  allowDecimals={false}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  width={56}
                  tickFormatter={(v: number) => fmtUsd(v)}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  content={<ChartTooltip fmt={(v) => fmtInt(v)} />}
                  cursor={{ stroke: "#3b82f6", strokeOpacity: 0.2 }}
                />
                <Area
                  yAxisId="left"
                  type="monotone"
                  dataKey="requests"
                  name="requests"
                  stroke={COLORS.requests}
                  strokeWidth={2.5}
                  fill="url(#grad-req)"
                  fillOpacity={1}
                  dot={{ r: 3, fill: COLORS.requests, strokeWidth: 0 }}
                  activeDot={{ r: 5, strokeWidth: 0 }}
                />
                <Area
                  yAxisId="right"
                  type="monotone"
                  dataKey="cost"
                  name="cost"
                  stroke={COLORS.cost}
                  strokeWidth={2}
                  fill="url(#grad-cost)"
                  fillOpacity={1}
                  dot={{ r: 3, fill: COLORS.cost, strokeWidth: 0 }}
                  activeDot={{ r: 5, strokeWidth: 0 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </Card>

      {/* ── Heatmap ─────────────────────────────────────────────────────── */}
      <Card className="mt-4">
        <CardHeader
          title="Requests by hour · last 7 days"
          subtitle="Each cell is one hour; color intensity shows volume"
        />
        <div className="p-4">
          {logs.length === 0 ? (
            <EmptyState>No requests logged yet.</EmptyState>
          ) : (
            <>
              <div
                className="grid items-center gap-1 text-[10px] text-[var(--admin-text-dim)]"
                style={{ gridTemplateColumns: "3.25rem repeat(24, minmax(0, 1fr))" }}
              >
                <div />
                {HOURS.map((h) => (
                  <div key={h} className="text-center leading-none">
                    {h % 6 === 0 ? String(h).padStart(2, "0") : ""}
                  </div>
                ))}
                {heat.map((d) => (
                  <Fragment key={d.start}>
                    <div
                      className="pr-2 text-right font-mono text-[11px] leading-7 text-[var(--admin-text-dim)]"
                      title={d.dateLabel}
                    >
                      {d.label}
                    </div>
                    {d.counts.map((c, h) => (
                      <div
                        key={h}
                        title={`${d.dateLabel} ${String(h).padStart(2, "0")}:00 · ${fmtInt(c)} requests`}
                        className="aspect-square rounded-[3px] transition-transform duration-150 hover:scale-110 hover:ring-1 hover:ring-white/20"
                        style={
                          c > 0
                            ? {
                                backgroundColor: heatColor(c / heatMax),
                                opacity: 0.25 + 0.75 * (c / heatMax),
                              }
                            : { backgroundColor: "rgba(255,255,255,0.025)" }
                        }
                      />
                    ))}
                  </Fragment>
                ))}
              </div>
              {/* gradient legend */}
              <div className="mt-4 flex items-center justify-between gap-3">
                <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
                  {fmtInt(heat.reduce((a, d) => a + d.total, 0))} total · 7 days
                </span>
                <div className="flex items-center gap-2 text-[11px] text-[var(--admin-text-dim)]">
                  <span>less</span>
                  <div
                    className="h-3 w-28 rounded-sm"
                    style={{
                      background: `linear-gradient(90deg, rgba(255,255,255,0.03), ${heatColor(0.3)}, ${heatColor(0.6)}, ${heatColor(1)})`,
                    }}
                  />
                  <span>more</span>
                </div>
              </div>
            </>
          )}
        </div>
      </Card>

      {/* ── Breakdown + Donut ───────────────────────────────────────────── */}
      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_360px]">
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
            <Table head={[groupDim, "requests", "tokens", "cost", "avg tps", "errors", "share"]}>
              {breakdown.map((r) => {
                const metricVal =
                  metric === "requests"
                    ? r.requests
                    : metric === "tokens"
                      ? r.tokens
                      : metric === "cost"
                        ? r.cost
                        : r.avgTps;
                const maxMetric = Math.max(
                  ...breakdown.map((b) =>
                    metric === "requests"
                      ? b.requests
                      : metric === "tokens"
                        ? b.tokens
                        : metric === "cost"
                          ? b.cost
                          : b.avgTps,
                  ),
                );
                return (
                  <tr key={r.name}>
                    <TD className="font-medium">
                      <div className="flex items-center gap-2.5">
                        <span
                          className="h-1.5 w-1.5 shrink-0 rounded-full"
                          style={{
                            backgroundColor:
                              DONUT_PALETTE[breakdown.indexOf(r) % DONUT_PALETTE.length],
                          }}
                        />
                        <span className="truncate max-w-[140px]">{r.name}</span>
                      </div>
                    </TD>
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
                    <TD className={`font-mono tabular-nums ${r.errs > 0 ? "text-red-400" : "text-[var(--admin-text-dim)]"}`}>
                      {fmtInt(r.errs)}
                    </TD>
                    <TD>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-white/[0.05]">
                          <div
                            className="h-full rounded-full transition-all"
                            style={{
                              width: `${(metricVal / (maxMetric || 1)) * 100}%`,
                              backgroundColor: COLORS.requests,
                            }}
                          />
                        </div>
                        <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
                          {(r.share * 100).toFixed(0)}%
                        </span>
                      </div>
                    </TD>
                  </tr>
                );
              })}
            </Table>
          )}
        </Card>

        {/* Distribution donut */}
        <Card>
          <CardHeader title="Distribution" subtitle={`${metric} by ${groupDim}`} />
          <div className="p-4">
            {donutData.length === 0 || donutTotal === 0 ? (
              <EmptyState>No data to display.</EmptyState>
            ) : (
              <>
                <div className="relative h-[200px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={donutData}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={58}
                        outerRadius={82}
                        paddingAngle={2}
                        stroke="none"
                      >
                        {donutData.map((entry, i) => (
                          <Cell key={i} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={TOOLTIP_STYLE}
                        formatter={(v: number) => donutFmt(v)}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                    <span className="text-[10px] uppercase tracking-wider text-[var(--admin-text-dim)]">
                      total
                    </span>
                    <span className="font-mono text-lg font-semibold text-[var(--admin-text)]">
                      {donutFmt(donutTotal)}
                    </span>
                  </div>
                </div>
                <div className="mt-4 space-y-1.5">
                  {donutData.slice(0, 5).map((d) => (
                    <div key={d.name} className="flex items-center justify-between text-[12px]">
                      <div className="flex min-w-0 items-center gap-2">
                        <span
                          className="h-2 w-2 shrink-0 rounded-full"
                          style={{ backgroundColor: d.color }}
                        />
                        <span className="truncate text-[var(--admin-text-muted)]">{d.name}</span>
                      </div>
                      <span className="font-mono text-[var(--admin-text-dim)]">
                        {((d.value / donutTotal) * 100).toFixed(1)}%
                      </span>
                    </div>
                  ))}
                  {donutData.length > 5 && (
                    <div className="text-[11px] text-[var(--admin-text-dim)]">
                      +{donutData.length - 5} more
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </Card>
      </div>

      {/* ── Spend per key ────────────────────────────────────────────────── */}
      <Card className="mt-4">
        <CardHeader
          title="Spend per key"
          subtitle="Cost distribution across virtual keys"
          right={
            <span className="flex items-center gap-1.5 text-[11px] text-[var(--admin-text-dim)]">
              <TrendingUp size={12} />
              {fmtUsd(stats.cost)} total
            </span>
          }
        />
        <div className="h-[280px] p-3">
          {spend.length === 0 ? (
            <EmptyState>No spend recorded.</EmptyState>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={spend}
                layout="vertical"
                margin={{ top: 4, right: 16, left: 0, bottom: 0 }}
              >
                <defs>
                  <linearGradient id="grad-bar" x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor={COLORS.requests} stopOpacity={0.9} />
                    <stop offset="100%" stopColor={COLORS.cost} stopOpacity={0.9} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff" strokeOpacity={0.05} horizontal={false} />
                <XAxis
                  type="number"
                  tickFormatter={(v: number) => fmtUsd(v)}
                  tick={{ fontSize: 11, fill: "#6b7280" }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="key"
                  width={120}
                  tick={{ fontSize: 11, fill: "#9ca3af" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: string) => (v.length > 16 ? `${v.slice(0, 15)}…` : v)}
                />
                <Tooltip
                  content={<ChartTooltip fmt={(v) => fmtUsd(v)} />}
                  cursor={{ fill: "rgba(59,130,246,0.05)" }}
                />
                <Bar
                  dataKey="cost"
                  name="spend"
                  fill="url(#grad-bar)"
                  radius={[0, 4, 4, 0]}
                  barSize={20}
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </Card>
    </div>
  );
}
