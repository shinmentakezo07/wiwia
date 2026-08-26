// Feature detail page components — ported from the Next.js reference's
// components/features/ directory: activity-logs-demo, api-key-demo,
// cost-analytics-demo, errors-monitoring-demo, model-breakdown-demo,
// multi-provider-demo, performance-monitoring-demo.
// All self-contained with inline mock data. Recharts is used for charts (it
// is an installed dependency).

import { AlertCircle, CheckCircle2, Clock, Copy, DollarSign, Key, MoreVertical, Package, Shield, Zap, Coins, Hash, Activity, TrendingUp } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

// Inline date formatting (replaces date-fns format/parseISO).
function parseISO(s: string) { return new Date(s); }
function format(d: Date, fmt: string) {
  // Only supports "MMM d" — the single format used by the chart below.
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return fmt.replace("MMM d", `${months[d.getMonth()]} ${d.getDate()}`);
}

// ── Inline mock data ────────────────────────────────────────────────────────

const mockLogs = [
  { id: "1", model: "gpt-5", provider: "OpenAI", status: "success", latency: 987, tokens: 1240, cost: 0.0124, timestamp: "2026-06-01T12:00:00Z" },
  { id: "2", model: "claude-sonnet-4.5", provider: "Anthropic", status: "success", latency: 1123, tokens: 856, cost: 0.0086, timestamp: "2026-06-01T12:05:00Z" },
  { id: "3", model: "gemini-2.5-flash", provider: "Google", status: "error", latency: 234, tokens: 0, cost: 0, timestamp: "2026-06-01T12:10:00Z" },
];

const mockApiKeys = [
  { id: "1", name: "Production API", status: "active", keyPrefix: "wiwi-", lastFour: "ab12", usageCount: 245678, lastUsed: new Date(Date.now() - 3600_000) },
  { id: "2", name: "Dev Key", status: "active", keyPrefix: "wiwi-", lastFour: "cd34", usageCount: 1234, lastUsed: new Date(Date.now() - 86400_000) },
];

const mockCostBreakdown = {
  total: 127.63,
  providers: [
    { name: "OpenAI", cost: 45.23, percentage: 35.5 },
    { name: "Anthropic", cost: 52.18, percentage: 40.9 },
    { name: "Google", cost: 18.45, percentage: 14.5 },
    { name: "Groq", cost: 11.77, percentage: 9.1 },
  ],
};

const mockMetrics = {
  totalTokens: "625,612,300",
  totalRequests: "2,021,208",
  avgLatency: "987ms",
  cacheHitRate: "42.3%",
};

const mockModelUsage = [
  { model: "gpt-5", provider: "OpenAI", requests: 45678, tokens: 24567890, cost: 245.68, avgLatency: 987 },
  { model: "claude-sonnet-4.5", provider: "Anthropic", requests: 32145, tokens: 18765432, cost: 178.41, avgLatency: 1123 },
  { model: "gemini-2.5-flash", provider: "Google", requests: 28934, tokens: 15678901, cost: 45.32, avgLatency: 654 },
  { model: "deepseek-v3.2", provider: "DeepSeek", requests: 12345, tokens: 6789012, cost: 12.34, avgLatency: 876 },
];

const COLORS = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b"];

function generateMockActivityData() {
  const days = ["2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29", "2026-05-30", "2026-05-31", "2026-06-01"];
  return {
    activity: days.map((date) => ({
      date,
      requestCount: Math.floor(Math.random() * 50000) + 10000,
      errorCount: Math.floor(Math.random() * 500),
      cacheCount: Math.floor(Math.random() * 20000) + 5000,
    })),
  };
}

function fmtAgo(date: Date) {
  const s = Math.floor((Date.now() - date.getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// ── MetricCard (inline) ─────────────────────────────────────────────────────

function MetricCard({ label, value, subtitle, icon, accent }: { label: string; value: string; subtitle: string; icon: React.ReactNode; accent: "green" | "blue" | "purple" }) {
  const colors = { green: "text-green-400", blue: "text-blue-400", purple: "text-purple-400" };
  return (
    <div className="admin-card flex items-center gap-3 p-4">
      <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-white/[0.04] ${colors[accent]}`}>{icon}</div>
      <div className="min-w-0">
        <p className="text-xs font-medium uppercase tracking-wide text-[var(--admin-text-muted)]">{label}</p>
        <p className="truncate text-2xl font-semibold tabular-nums text-[var(--admin-text)]">{value}</p>
        <p className="text-xs text-[var(--admin-text-dim)]">{subtitle}</p>
      </div>
    </div>
  );
}

// ── ActivityLogsDemo ────────────────────────────────────────────────────────

export function ActivityLogsDemo() {
  return (
    <div className="admin-card">
      <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
        <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">Recent Activity</h3>
        <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Real-time request logs with detailed metadata</p>
      </div>
      <div className="space-y-3 p-4">
        {mockLogs.map((log) => (
          <div key={log.id} className="rounded-lg border border-[var(--admin-border)] p-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${log.status === "success" ? "bg-green-400" : "bg-red-400"}`} />
                <span className="font-mono text-sm text-[var(--admin-text)]">{log.model}</span>
                <span className="text-xs text-[var(--admin-text-muted)]">via {log.provider}</span>
              </div>
              <span className="text-xs text-[var(--admin-text-dim)]">{log.latency}ms · {log.tokens} tokens · ${log.cost.toFixed(4)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── ApiKeyDemo ──────────────────────────────────────────────────────────────

export function ApiKeyDemo() {
  return (
    <div className="admin-card">
      <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
        <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">API Key Management</h3>
        <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Securely manage and monitor your API keys</p>
      </div>
      <div className="space-y-4 p-4">
        {mockApiKeys.map((apiKey) => (
          <div key={apiKey.id} className="flex items-center justify-between rounded-lg border border-[var(--admin-border)] p-4">
            <div className="flex items-center gap-4">
              <div className="rounded-lg bg-blue-500/10 p-2">
                <Key className="h-5 w-5 text-blue-400" />
              </div>
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <p className="font-semibold text-[var(--admin-text)]">{apiKey.name}</p>
                  <span className="admin-badge admin-badge-green">{apiKey.status}</span>
                </div>
                <div className="flex items-center gap-3 text-sm text-[var(--admin-text-muted)]">
                  <code className="rounded bg-white/[0.04] px-2 py-0.5 text-xs">{apiKey.keyPrefix}••••{apiKey.lastFour}</code>
                  <span>•</span>
                  <span>{apiKey.usageCount.toLocaleString()} requests</span>
                  <span>•</span>
                  <span>Last used {fmtAgo(apiKey.lastUsed)}</span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button className="rounded p-2 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"><Copy className="h-4 w-4" /></button>
              <button className="rounded p-2 text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.04] hover:text-[var(--admin-text)]"><MoreVertical className="h-4 w-4" /></button>
            </div>
          </div>
        ))}
        <div className="mt-6 rounded-lg border border-dashed border-[var(--admin-border)] bg-white/[0.01] p-4">
          <div className="flex items-start gap-3">
            <Shield className="mt-0.5 h-5 w-5 text-[var(--admin-text-muted)]" />
            <div className="space-y-1">
              <p className="font-medium text-[var(--admin-text)]">Secure Key Storage</p>
              <p className="text-sm text-[var(--admin-text-muted)]">All API keys are encrypted at rest and in transit. Keys are only shown once during creation and cannot be retrieved later.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── CostAnalyticsDemo ───────────────────────────────────────────────────────

function PieTooltipContent({ payload }: { payload?: Array<{ name?: string; value?: number }> }) {
  if (payload && payload.length) {
    return (
      <div className="rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] p-2 text-sm shadow-xl">
        <p className="font-medium text-[var(--admin-text)]">{payload[0].name}</p>
        <p className="text-[var(--admin-text-muted)]">${Number(payload[0].value).toFixed(2)}</p>
      </div>
    );
  }
  return null;
}

export function CostAnalyticsDemo() {
  const pieData = mockCostBreakdown.providers.map((p) => ({ name: p.name, value: p.cost }));
  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-3">
        <MetricCard label="Total Spend" value={`$${mockCostBreakdown.total.toFixed(2)}`} subtitle="Last 7 days" icon={<DollarSign className="h-4 w-4" />} accent="green" />
        <MetricCard label="Avg Cost per 1K Tokens" value="$0.106" subtitle="Blended rate" icon={<Coins className="h-4 w-4" />} accent="blue" />
        <MetricCard label="Total Tokens" value={mockMetrics.totalTokens} subtitle="Input + Output" icon={<TrendingUp className="h-4 w-4" />} accent="purple" />
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        <div className="admin-card">
          <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
            <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">Cost by Provider</h3>
            <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Distribution of spending across providers</p>
          </div>
          <div className="p-4">
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie data={pieData} cx="50%" cy="50%" labelLine={false} label={(props: { name?: string; percent?: number }) => `${props.name} ${(((props.percent ?? 0)) * 100).toFixed(0)}%`} outerRadius={80} fill="#8884d8" dataKey="value">
                  {pieData.map((_, index) => <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />)}
                </Pie>
                <Tooltip content={<PieTooltipContent />} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="admin-card">
          <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
            <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">Provider Breakdown</h3>
            <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Detailed cost analysis by provider</p>
          </div>
          <div className="space-y-3 p-4">
            {mockCostBreakdown.providers.map((provider, index) => (
              <div key={provider.name} className="flex items-center justify-between rounded-lg border border-[var(--admin-border)] p-3">
                <div className="flex items-center gap-3">
                  <div className="h-3 w-3 rounded-full" style={{ backgroundColor: COLORS[index % COLORS.length] }} />
                  <div>
                    <p className="font-medium text-[var(--admin-text)]">{provider.name}</p>
                    <p className="text-sm text-[var(--admin-text-muted)]">{provider.percentage.toFixed(1)}% of total</p>
                  </div>
                </div>
                <p className="font-semibold text-[var(--admin-text)]">${provider.cost.toFixed(2)}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── ErrorsMonitoringDemo ────────────────────────────────────────────────────

export function ErrorsMonitoringDemo() {
  const data = generateMockActivityData();
  const totalRequests = data.activity.reduce((s, d) => s + d.requestCount, 0);
  const totalErrors = data.activity.reduce((s, d) => s + d.errorCount, 0);
  const totalCached = data.activity.reduce((s, d) => s + d.cacheCount, 0);
  const errorRate = totalRequests > 0 ? ((totalErrors / totalRequests) * 100).toFixed(2) : "0";
  const cacheRate = totalRequests > 0 ? ((totalCached / totalRequests) * 100).toFixed(2) : "0";

  return (
    <div className="grid gap-6 md:grid-cols-2">
      <div className="admin-card">
        <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
          <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">Error Rate</h3>
          <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Track reliability and identify issues early</p>
        </div>
        <div className="space-y-4 p-4">
          <div className="flex items-center justify-between rounded-lg border border-red-500/20 bg-red-500/[0.04] p-4">
            <div className="flex items-center gap-3">
              <AlertCircle className="h-10 w-10 text-red-500" />
              <div>
                <p className="text-3xl font-bold text-red-400">{errorRate}%</p>
                <p className="text-sm text-[var(--admin-text-muted)]">Error Rate</p>
              </div>
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex justify-between text-sm"><span className="text-[var(--admin-text-muted)]">Failed Requests</span><span className="font-medium text-[var(--admin-text)]">{totalErrors.toLocaleString()}</span></div>
            <div className="flex justify-between text-sm"><span className="text-[var(--admin-text-muted)]">Successful Requests</span><span className="font-medium text-[var(--admin-text)]">{(totalRequests - totalErrors).toLocaleString()}</span></div>
            <div className="flex justify-between border-t border-[var(--admin-border)] pt-2 text-sm"><span className="text-[var(--admin-text-muted)]">Total Requests</span><span className="font-semibold text-[var(--admin-text)]">{totalRequests.toLocaleString()}</span></div>
          </div>
        </div>
      </div>
      <div className="admin-card">
        <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
          <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">Cache Performance</h3>
          <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Monitor cache efficiency and cost savings</p>
        </div>
        <div className="space-y-4 p-4">
          <div className="flex items-center justify-between rounded-lg border border-green-500/20 bg-green-500/[0.04] p-4">
            <div className="flex items-center gap-3">
              <Zap className="h-10 w-10 text-green-500" />
              <div>
                <p className="text-3xl font-bold text-green-400">{cacheRate}%</p>
                <p className="text-sm text-[var(--admin-text-muted)]">Cache Hit Rate</p>
              </div>
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex justify-between text-sm"><span className="text-[var(--admin-text-muted)]">Cached Responses</span><span className="font-medium text-[var(--admin-text)]">{totalCached.toLocaleString()}</span></div>
            <div className="flex justify-between text-sm"><span className="text-[var(--admin-text-muted)]">Estimated Savings</span><span className="font-medium text-green-400">${(totalCached * 0.005).toFixed(2)}</span></div>
            <div className="flex justify-between border-t border-[var(--admin-border)] pt-2 text-sm"><span className="text-[var(--admin-text-muted)]">Cache Efficiency</span><span className="font-semibold text-[var(--admin-text)]">Excellent</span></div>
          </div>
        </div>
      </div>
      <div className="admin-card md:col-span-2">
        <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
          <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">Reliability Summary</h3>
          <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Overall system health metrics</p>
        </div>
        <div className="grid gap-4 p-4 md:grid-cols-4">
          <div className="flex flex-col items-center rounded-lg border border-[var(--admin-border)] p-4">
            <CheckCircle2 className="mb-2 h-8 w-8 text-green-500" />
            <p className="text-2xl font-bold text-[var(--admin-text)]">{((1 - totalErrors / totalRequests) * 100).toFixed(2)}%</p>
            <p className="text-center text-sm text-[var(--admin-text-muted)]">Success Rate</p>
          </div>
          <div className="flex flex-col items-center rounded-lg border border-[var(--admin-border)] p-4">
            <Zap className="mb-2 h-8 w-8 text-blue-500" />
            <p className="text-2xl font-bold text-[var(--admin-text)]">{mockMetrics.avgLatency}</p>
            <p className="text-center text-sm text-[var(--admin-text-muted)]">Avg Latency</p>
          </div>
          <div className="flex flex-col items-center rounded-lg border border-[var(--admin-border)] p-4">
            <AlertCircle className="mb-2 h-8 w-8 text-orange-500" />
            <p className="text-2xl font-bold text-[var(--admin-text)]">0</p>
            <p className="text-center text-sm text-[var(--admin-text-muted)]">Critical Errors</p>
          </div>
          <div className="flex flex-col items-center rounded-lg border border-[var(--admin-border)] p-4">
            <CheckCircle2 className="mb-2 h-8 w-8 text-purple-500" />
            <p className="text-2xl font-bold text-[var(--admin-text)]">99.2%</p>
            <p className="text-center text-sm text-[var(--admin-text-muted)]">Uptime</p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── ModelBreakdownDemo ──────────────────────────────────────────────────────

export function ModelBreakdownDemo() {
  return (
    <div className="admin-card">
      <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
        <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">Model Usage Breakdown</h3>
        <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Detailed performance and cost metrics by model</p>
      </div>
      <div className="p-4">
        <div className="admin-table overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr>
                <th>Model</th><th>Provider</th><th className="text-right">Requests</th><th className="text-right">Tokens</th><th className="text-right">Cost</th><th className="text-right">Avg Latency</th>
              </tr>
            </thead>
            <tbody>
              {mockModelUsage.map((m) => (
                <tr key={m.model}>
                  <td className="font-mono text-sm text-[var(--admin-text)]">{m.model}</td>
                  <td><span className="admin-badge admin-badge-gray">{m.provider}</span></td>
                  <td className="text-right font-medium text-[var(--admin-text)]">{m.requests.toLocaleString()}</td>
                  <td className="text-right text-[var(--admin-text-muted)]">{m.tokens.toLocaleString()}</td>
                  <td className="text-right font-semibold text-[var(--admin-text)]">${m.cost.toFixed(2)}</td>
                  <td className="text-right text-[var(--admin-text-muted)]">{m.avgLatency}ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-6 grid gap-4 md:grid-cols-4">
          <div className="flex items-center gap-3 rounded-lg border border-[var(--admin-border)] p-4">
            <Package className="h-8 w-8 text-blue-500" />
            <div><p className="text-sm text-[var(--admin-text-muted)]">Total Models</p><p className="text-2xl font-bold text-[var(--admin-text)]">{mockModelUsage.length}</p></div>
          </div>
          <div className="flex items-center gap-3 rounded-lg border border-[var(--admin-border)] p-4">
            <Activity className="h-8 w-8 text-green-500" />
            <div><p className="text-sm text-[var(--admin-text-muted)]">Total Requests</p><p className="text-2xl font-bold text-[var(--admin-text)]">{mockModelUsage.reduce((s, m) => s + m.requests, 0).toLocaleString()}</p></div>
          </div>
          <div className="flex items-center gap-3 rounded-lg border border-[var(--admin-border)] p-4">
            <DollarSign className="h-8 w-8 text-orange-500" />
            <div><p className="text-sm text-[var(--admin-text-muted)]">Total Cost</p><p className="text-2xl font-bold text-[var(--admin-text)]">${mockModelUsage.reduce((s, m) => s + m.cost, 0).toFixed(2)}</p></div>
          </div>
          <div className="flex items-center gap-3 rounded-lg border border-[var(--admin-border)] p-4">
            <Clock className="h-8 w-8 text-purple-500" />
            <div><p className="text-sm text-[var(--admin-text-muted)]">Avg Latency</p><p className="text-2xl font-bold text-[var(--admin-text)]">{Math.round(mockModelUsage.reduce((s, m) => s + m.avgLatency, 0) / mockModelUsage.length)}ms</p></div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── MultiProviderDemo ───────────────────────────────────────────────────────

const providers = [
  { name: "OpenAI", models: 15, status: "active", latency: 987, cost: "$45.23" },
  { name: "Anthropic", models: 8, status: "active", latency: 1123, cost: "$52.18" },
  { name: "Google", models: 12, status: "active", latency: 1456, cost: "$18.45" },
  { name: "Groq", models: 5, status: "active", latency: 432, cost: "$3.42" },
  { name: "xAI", models: 2, status: "active", latency: 876, cost: "$8.21" },
  { name: "DeepSeek", models: 4, status: "active", latency: 654, cost: "$11.57" },
];

export function MultiProviderDemo() {
  return (
    <div className="space-y-6">
      <div className="admin-card">
        <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
          <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">Connected Providers</h3>
          <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Access 200+ models from 40+ providers through a single API</p>
        </div>
        <div className="grid gap-4 p-4 md:grid-cols-2 lg:grid-cols-3">
          {providers.map((p) => (
            <div key={p.name} className="rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4">
              <div className="mb-3 flex items-start justify-between">
                <div><p className="font-semibold text-[var(--admin-text)]">{p.name}</p><p className="text-sm text-[var(--admin-text-muted)]">{p.models} models</p></div>
                <span className="admin-badge admin-badge-green flex items-center gap-1"><CheckCircle2 className="h-3 w-3" />{p.status}</span>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between"><span className="flex items-center gap-1 text-[var(--admin-text-muted)]"><Clock className="h-3 w-3" />Avg Latency</span><span className="font-medium text-[var(--admin-text)]">{p.latency}ms</span></div>
                <div className="flex items-center justify-between"><span className="flex items-center gap-1 text-[var(--admin-text-muted)]"><DollarSign className="h-3 w-3" />Total Spend</span><span className="font-medium text-[var(--admin-text)]">{p.cost}</span></div>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <div className="admin-card"><div className="p-4 text-center"><p className="text-4xl font-bold text-[var(--admin-text)]">40+</p><p className="text-[var(--admin-text-muted)]">Provider Integrations</p></div></div>
        <div className="admin-card"><div className="p-4 text-center"><p className="text-4xl font-bold text-[var(--admin-text)]">200+</p><p className="text-[var(--admin-text-muted)]">Available Models</p></div></div>
        <div className="admin-card"><div className="p-4 text-center"><p className="text-4xl font-bold text-[var(--admin-text)]">1</p><p className="text-[var(--admin-text-muted)]">Unified API</p></div></div>
      </div>
    </div>
  );
}

// ── PerformanceMonitoringDemo ────────────────────────────────────────────────

function ChartTooltipContent({ active, payload }: { active?: boolean; payload?: Array<{ value?: number; payload?: { formattedDate?: string } }> }) {
  if (active && payload && payload.length) {
    return (
      <div className="rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface-elevated)] p-2 text-sm shadow-xl">
        <p className="font-medium text-[var(--admin-text)]">{payload[0].payload?.formattedDate}</p>
        <p className="text-[var(--admin-text-muted)]"><span className="font-medium">{payload[0].value}</span> requests</p>
      </div>
    );
  }
  return null;
}

export function PerformanceMonitoringDemo() {
  const data = generateMockActivityData();
  const chartData = data.activity.map((day) => ({ ...day, formattedDate: format(parseISO(day.date), "MMM d") }));
  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-3">
        <MetricCard label="Total Requests" value={mockMetrics.totalRequests} subtitle="Last 7 days" icon={<Activity className="h-4 w-4" />} accent="blue" />
        <MetricCard label="Avg Latency" value={mockMetrics.avgLatency} subtitle="Mean response time" icon={<Clock className="h-4 w-4" />} accent="purple" />
        <MetricCard label="Cache Hit Rate" value={mockMetrics.cacheHitRate} subtitle="Cached responses" icon={<Zap className="h-4 w-4" />} accent="green" />
      </div>
      <div className="admin-card">
        <div className="border-b border-[var(--admin-border)] px-5 py-3.5">
          <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">Request Activity</h3>
          <p className="mt-0.5 text-[11px] text-[var(--admin-text-muted)]">Daily request volume over the last 7 days</p>
        </div>
        <div className="p-4">
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey="formattedDate" stroke="#888" fontSize={12} tickLine={false} axisLine={false} />
              <YAxis stroke="#888" fontSize={12} tickLine={false} axisLine={false} />
              <Tooltip content={<ChartTooltipContent />} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
              <Bar dataKey="requestCount" fill="#3b82f6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

// re-export hash icon for parity with original import set
export { Hash };
