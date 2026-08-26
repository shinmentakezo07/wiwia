// Apps — apps using the gateway, ranked by tokens. Adapted from the
// llmgateway.io apps page with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

interface AppStat {
  source: string;
  totalTokens: number;
  totalRequests: number;
}

const APPS: AppStat[] = [
  { source: "claude-code", totalTokens: 8_400_000_000, totalRequests: 12_400_000 },
  { source: "cursor", totalTokens: 6_200_000_000, totalRequests: 9_800_000 },
  { source: "cline", totalTokens: 3_100_000_000, totalRequests: 5_200_000 },
  { source: "opencode", totalTokens: 2_400_000_000, totalRequests: 3_900_000 },
  { source: "devpass-code", totalTokens: 1_800_000_000, totalRequests: 2_700_000 },
  { source: "aider", totalTokens: 1_200_000_000, totalRequests: 1_900_000 },
  { source: "github-copilot", totalTokens: 980_000_000, totalRequests: 1_500_000 },
  { source: "continue", totalTokens: 620_000_000, totalRequests: 890_000 },
  { source: "codex-cli", totalTokens: 410_000_000, totalRequests: 520_000 },
  { source: "kimi-code", totalTokens: 280_000_000, totalRequests: 340_000 },
  { source: "crush", totalTokens: 190_000_000, totalRequests: 210_000 },
  { source: "kilo-code", totalTokens: 120_000_000, totalRequests: 150_000 },
];

const TOTAL_APPS = APPS.length;
const TOTAL_TOKENS = APPS.reduce((a, b) => a + b.totalTokens, 0);
const TOTAL_REQUESTS = APPS.reduce((a, b) => a + b.totalRequests, 0);

const numberFormatter = new Intl.NumberFormat("en-US");

function formatBigNumber(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return numberFormatter.format(n);
}

function formatTokens(n: number): string {
  return formatBigNumber(n);
}

function HeroStat({ value, label, accent }: { value: string; label: string; accent?: boolean }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <span
        className={`font-mono text-[28px] font-bold leading-none tracking-tighter tabular-nums md:text-[32px] ${
          accent ? "text-blue-400" : "text-[var(--admin-text)]"
        }`}
        style={{ fontFamily: MONO }}
      >
        {value}
      </span>
      <span className="text-[11px] uppercase tracking-[0.2em] text-[var(--admin-text-muted)]">{label}</span>
    </div>
  );
}

export function AppsPage() {
  const maxTokens = APPS[0]?.totalTokens ?? 0;

  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="relative overflow-hidden pt-8">
        <div
          aria-hidden
          className="pointer-events-none absolute left-1/2 top-8 h-80 w-[600px] -translate-x-1/2 rounded-full bg-blue-500/[0.05] blur-3xl"
        />
        <div className="relative text-center">
          <div className="mb-6 flex justify-center">
            <div className="inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-white/[0.02] px-4 py-1.5 text-[12px] text-[var(--admin-text-muted)]">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
              Live · refreshed every 5 minutes
            </div>
          </div>
          <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
            Apps shipping with the{" "}
            <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
              gateway
            </span>
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            Real coding agents, real traffic. Every tool below routes through one API,
            ranked by tokens processed.
          </p>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-x-10 gap-y-6">
            <HeroStat value={numberFormatter.format(TOTAL_APPS)} label="apps tracked" />
            <span aria-hidden className="hidden h-12 w-px bg-[var(--admin-border)] md:block" />
            <HeroStat value={formatBigNumber(TOTAL_TOKENS)} label="tokens processed" accent />
            <span aria-hidden className="hidden h-12 w-px bg-[var(--admin-border)] md:block" />
            <HeroStat value={formatBigNumber(TOTAL_REQUESTS)} label="requests routed" />
          </div>
        </div>
      </section>

      {/* ── banner ── */}
      <section>
        <Card className="flex flex-col items-start justify-between gap-3 px-5 py-4 sm:flex-row sm:items-center">
          <p className="text-[13px] text-[var(--admin-text-muted)]">
            Want your app on this list? Set{" "}
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[12px] text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
              x-source: your-app.com
            </code>{" "}
            on requests.
          </p>
          <Link
            to="/docs"
            className="group inline-flex items-center gap-1.5 text-[13px] font-medium text-[var(--admin-text)] hover:text-blue-400"
          >
            Read the docs
            <ArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
          </Link>
        </Card>
      </section>

      {/* ── apps grid ── */}
      <section>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {APPS.map((app, index) => (
            <Card key={app.source} className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--admin-border)] bg-white/[0.02] text-[14px] font-semibold text-[var(--admin-text)]">
                    {app.source.charAt(0).toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <h3 className="truncate text-[14px] font-semibold text-[var(--admin-text)]">{app.source}</h3>
                  </div>
                </div>
                <span className="font-mono text-[20px] font-bold leading-none tracking-tighter tabular-nums text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
                  {String(index + 1).padStart(2, "0")}
                </span>
              </div>
              <div className="mt-4 space-y-2">
                <div className="flex items-baseline justify-between">
                  <span className="font-mono text-[20px] font-bold tabular-nums text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
                    {formatTokens(app.totalTokens)}
                  </span>
                  <span className="text-[10px] uppercase tracking-widest text-[var(--admin-text-muted)]">tokens</span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.05]">
                  <div
                    className="h-full rounded-full bg-blue-400/60 transition-all duration-700"
                    style={{ width: `${Math.max(2, (app.totalTokens / maxTokens) * 100)}%` }}
                  />
                </div>
                <div className="text-[11px] text-[var(--admin-text-muted)]">
                  {numberFormatter.format(app.totalRequests)} requests
                </div>
              </div>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
