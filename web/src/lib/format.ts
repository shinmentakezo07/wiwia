// Formatting + small aggregation helpers shared across pages.

export function fmtInt(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return fmtInt(n);
}

export function fmtUsd(n: number): string {
  if (n !== 0 && Math.abs(n) < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

export function fmtPct(frac: number): string {
  return `${(frac * 100).toFixed(1)}%`;
}

export function fmtTime(tsSec: number): string {
  return new Date(tsSec * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function fmtDateTime(tsSec: number): string {
  return new Date(tsSec * 1000).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function fmtAgo(tsSec: number | null, nowMs = Date.now()): string {
  if (tsSec == null) return "never";
  const s = Math.max(0, (nowMs - tsSec * 1000) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Group rows by key and reduce with the given aggregations. */
export function groupBy<T>(rows: T[], keyOf: (row: T) => string): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const row of rows) {
    const k = keyOf(row) || "(none)";
    const arr = out.get(k);
    if (arr) arr.push(row);
    else out.set(k, [row]);
  }
  return out;
}

export function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}

export function p95(xs: number[]): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(s.length * 0.95))];
}
