# Dashboard Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the wiwi admin Dashboard visuals in place — validated palette, tile sparklines + delta chips, refined chart marks, zero-traffic empty states — client-side only.

**Architecture:** One page file (`web/src/pages/Dashboard.tsx`) plus a shared `StatCard` extension in `web/src/components/ui.tsx` and CSS additions in `web/src/styles.css`. A new pure helper module (`web/src/lib/dashboard-metrics.ts`) derives sparkline series and prev-hour deltas from data the page already fetches. No backend changes.

**Tech Stack:** React 19, TypeScript, Recharts 2, Tailwind 4, lucide-react. Build: `bun run build` (tsc -b + vite).

## Global Constraints

- Palette (validated, dark surface #0a0a0a): tokens-in `#3b82f6`, out `#c98500`, cached `#199e70`, reasoning `#a855f7`, requests `#3b82f6`, errors `#e66767`. Stack order **in→out→cached→reasoning**. Do not substitute other values.
- Area fills: ~10% → 2% vertical gradient washes (not the current 45%).
- Strokes 2px round join/cap. Gridlines: solid hairline rgba(255,255,255,0.06), never dashed.
- Delta chip when previous window empty: muted "—", never "∞%" or a bare "+100%".
- Refetch: hold previous render at reduced opacity — no skeleton flash.
- No backend edits; no new npm dependencies.
- Commits: imperative present tense, no prefix tags; run `bun run build` before each commit.

---

### Task 1: Metric derivation helpers (`dashboard-metrics.ts`)

**Files:**
- Create: `web/src/lib/dashboard-metrics.ts`

**Interfaces:**
- Produces:
  - `export interface Delta { pct: number | null; dir: "up" | "down" | "flat" }`
  - `export function hourlySeries(points: Array<{ t: number; v: number }>, nowMs?: number): number[]` — twelve 5-minute sums covering the last hour, oldest first.
  - `export function deltaVsPrevHour(current: number, previous: number): Delta`

- [ ] **Step 1: Create the helpers file**

```ts
// Pure derivations for dashboard tiles: hourly sparkline series + prev-hour
// deltas. Kept free of React so they stay trivially testable.

export interface Delta {
  pct: number | null;
  dir: "up" | "down" | "flat";
}

/** Sum `points` into twelve 5-minute buckets covering the last hour
 *  (oldest first). Points carry epoch-seconds; `nowMs` is injectable for tests. */
export function hourlySeries(
  points: Array<{ t: number; v: number }>,
  nowMs = Date.now(),
): number[] {
  const nowMin = Math.floor(nowMs / 60_000);
  const sums = new Array<number>(12).fill(0);
  for (const p of points) {
    const m = Math.floor(p.t / 60);
    const age = nowMin - m; // 0 = current 5-min slot
    if (age < 0 || age >= 60) continue;
    sums[11 - Math.floor(age / 5)] += p.v;
  }
  return sums;
}

/** Signed percent change vs the previous hour. Null when there is no previous
 *  value to compare against (rendered as a muted "—" chip, never ∞%). */
export function deltaVsPrevHour(current: number, previous: number): Delta {
  if (previous <= 0) return { pct: null, dir: "flat" };
  if (current === previous) return { pct: 0, dir: "flat" };
  const pct = ((current - previous) / previous) * 100;
  return { pct, dir: pct > 0 ? "up" : "down" };
}
```

Sanity-check by hand before committing: a point at `t` exactly one hour old lands in slot 0; a point at now lands in slot 11.

- [ ] **Step 2: Type-check**

Run: `cd web && bunx tsc -b --pretty false`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/dashboard-metrics.ts
git commit -m "Add dashboard metric derivation helpers"
```

---

### Task 2: Palette + CSS additions

**Files:**
- Modify: `web/src/pages/Dashboard.tsx` (COLORS constant, lines ~41–48)
- Modify: `web/src/styles.css` (append at end)

**Interfaces:**
- Produces: `COLORS` keys unchanged (`tokIn, tokCached, tokReasoning, tokOut, requests, errors`) with validated hex values; CSS classes `.admin-delta-chip`, `.admin-delta-up`, `.admin-delta-down`, `.admin-delta-flat`, `.admin-waiting-pulse`, `.admin-chart-tooltip` (+ its `.tt-value`/`.tt-series` children) used by Tasks 3–4.

- [ ] **Step 1: Update COLORS in Dashboard.tsx**

Replace the current constant with:

```ts
const COLORS = {
  tokIn: "#3b82f6",
  tokCached: "#199e70",
  tokReasoning: "#a855f7",
  tokOut: "#c98500",
  requests: "#3b82f6",
  errors: "#e66767",
};
```

- [ ] **Step 2: Append CSS to styles.css**

```css
/* ─── Dashboard polish: delta chips, waiting pulse, chart tooltip ─────────── */
.admin-delta-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border-radius: 999px;
  padding: 1px 7px;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 10px;
  line-height: 16px;
  font-weight: 500;
}
.admin-delta-up {
  color: #34d399;
  background: rgba(52, 211, 153, 0.08);
}
.admin-delta-down {
  color: #f87171;
  background: rgba(248, 113, 113, 0.08);
}
.admin-delta-flat {
  color: var(--admin-text-dim);
  background: rgba(255, 255, 255, 0.04);
}
@keyframes admin-waiting-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 0.9; }
}
.admin-waiting-pulse {
  animation: admin-waiting-pulse 2.4s ease-in-out infinite;
}
.admin-chart-tooltip {
  background: rgba(10, 10, 10, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 12px;
}
.admin-chart-tooltip .tt-value {
  font-weight: 600;
  color: #f4f4f5;
}
.admin-chart-tooltip .tt-series {
  color: #9ca3af;
}
@media (prefers-reduced-motion: reduce) {
  .admin-waiting-pulse { animation: none; }
}
```

- [ ] **Step 3: Build**

Run: `cd web && bun run build`
Expected: success (the old mint/yellow hexes no longer appear anywhere in Dashboard.tsx).

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Dashboard.tsx web/src/styles.css
git commit -m "Update dashboard palette to validated dark-band steps"
```

---

### Task 3: StatCard extension (sparkline + delta chip props)

**Files:**
- Modify: `web/src/components/ui.tsx` (StatCard, ~lines 156–187)

**Interfaces:**
- Consumes: `Delta` from `web/src/lib/dashboard-metrics.ts` (Task 1).
- Produces: `StatCard` accepts optional `spark?: number[]` (12 points, oldest first), `delta?: Delta`, `deltaGoodDir?: "up" | "down"` (default `"down"`), `waiting?: boolean`. All existing call sites compile unchanged.

- [ ] **Step 1: Extend StatCard**

Add import at top of `ui.tsx`:

```tsx
import type { Delta } from "@/lib/dashboard-metrics";
```

Add two private components above StatCard, then replace StatCard:

```tsx
function TileSparkline(props: { points: number[]; accent: string }) {
  const w = 96;
  const h = 26;
  const max = Math.max(1, ...props.points);
  const pts = props.points.map((v, i) => {
    const x = props.points.length <= 1 ? 0 : (i / (props.points.length - 1)) * w;
    const y = h - 2 - (v / max) * (h - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const lastPair = pts[pts.length - 1] ?? `${w},${h - 2}`;
  const [lx, ly] = lastPair.split(",").map(Number);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-[26px] w-24 shrink-0" aria-hidden>
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke="var(--admin-text-dim)"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.55}
      />
      <circle cx={lx} cy={ly} r={2.5} fill={props.accent} />
    </svg>
  );
}

function DeltaChip(props: { delta: Delta; goodDir: "up" | "down" }) {
  if (props.delta.pct === null) {
    return <span className="admin-delta-chip admin-delta-flat">— vs prev hour</span>;
  }
  if (props.delta.dir === "flat") {
    return <span className="admin-delta-chip admin-delta-flat">±0% vs prev hour</span>;
  }
  const cls = props.delta.dir === props.goodDir ? "admin-delta-up" : "admin-delta-down";
  const arrow = props.delta.dir === "up" ? "↑" : "↓";
  return (
    <span className={`admin-delta-chip ${cls}`}>
      {arrow} {Math.abs(props.delta.pct).toFixed(0)}% vs prev hour
    </span>
  );
}

export function StatCard(props: {
  label: string;
  value: string;
  sub?: string;
  icon?: LucideIcon;
  tone?: StatTone;
  /** Hero metric: larger value + accent-tinted surface. */
  featured?: boolean;
  /** 12-point sparkline, oldest first. */
  spark?: number[];
  /** Change vs the previous hour. */
  delta?: Delta;
  /** Which direction of `delta` is good (default: down). */
  deltaGoodDir?: "up" | "down";
  /** Zero-traffic state: pulse the value instead of flat zeros. */
  waiting?: boolean;
}) {
  const accent = STAT_ACCENT[props.tone ?? "default"];
  const Icon = props.icon;
  return (
    <Card className={`group relative p-5 ${props.featured ? "admin-stat-highlight" : ""}`}>
      <div className="relative z-10">
        <div className="mb-3 flex items-center gap-2">
          {Icon && (
            <Icon className="h-3.5 w-3.5 shrink-0" style={{ color: accent, opacity: 0.6 }} />
          )}
          <span className="admin-label">{props.label}</span>
        </div>
        <div className="flex items-end justify-between gap-3">
          <p
            className={`admin-stat-value font-mono ${props.featured ? "text-[28px]" : "text-[22px]"} ${
              props.waiting ? "admin-waiting-pulse" : ""
            }`}
          >
            {props.value}
          </p>
          {props.spark && props.spark.some((v) => v > 0) && (
            <TileSparkline points={props.spark} accent={accent} />
          )}
        </div>
        {(props.sub || props.delta) && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {props.sub && (
              <p className="font-mono text-[11px] text-[var(--admin-text-dim)]">{props.sub}</p>
            )}
            {props.delta && <DeltaChip delta={props.delta} goodDir={props.deltaGoodDir ?? "down"} />}
          </div>
        )}
      </div>
    </Card>
  );
}
```

- [ ] **Step 2: Build**

Run: `cd web && bun run build`
Expected: success — every existing StatCard call site compiles unchanged.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/ui.tsx
git commit -m "Extend StatCard with sparkline, delta chip, and waiting state"
```

---

### Task 4: Wire tiles + charts on the Dashboard

**Files:**
- Modify: `web/src/pages/Dashboard.tsx`

**Interfaces:**
- Consumes: `hourlySeries`, `deltaVsPrevHour`, `Delta` (Task 1); StatCard's `spark/delta/deltaGoodDir/waiting` props (Task 3); `COLORS` (Task 2).
- Produces: finished Dashboard page.

- [ ] **Step 1: Imports + derivations inside DashboardPage**

Add import:

```tsx
import { deltaVsPrevHour, hourlySeries, type Delta } from "@/lib/dashboard-metrics";
```

(`Delta` may be unused here depending on final shape — remove from the import list if eslint flags it.) After `const o = overviewQuery.data;` add:

```tsx
const logs = logsQuery.data?.logs ?? [];
const nowMs = Date.now();
const hasTraffic = (o?.requests ?? 0) > 0;

const mkPoints = (vOf: (l: RequestLogEntry) => number) =>
  logs.map((l) => ({ t: l.ts, v: vOf(l) }));

const reqSpark = useMemo(() => hourlySeries(mkPoints(() => 1), nowMs), [logsQuery.data]);
const costSpark = useMemo(() => hourlySeries(mkPoints((l) => l.cost), nowMs), [logsQuery.data]);
const errSpark = useMemo(
  () => hourlySeries(mkPoints((l) => (l.status >= 400 ? 1 : 0)), nowMs),
  [logsQuery.data],
);
const ttftSpark = useMemo(() => hourlySeries(mkPoints((l) => l.ttft_ms), nowMs), [logsQuery.data]);

// Current vs previous hour totals for the delta chips. The request-log ring
// holds ~500 events, enough to cover both windows at personal-gateway volume.
const hourCut = nowMs - 3_600_000;
const hourPrevStart = nowMs - 7_200_000;
const sumIn = (pts: Array<{ t: number; v: number }>, lo: number, hi: number) =>
  pts.filter((p) => p.t * 1000 >= lo && p.t * 1000 < hi).reduce((a, p) => a + p.v, 0);

const reqDelta = deltaVsPrevHour(
  sumIn(mkPoints(() => 1), hourCut, Number.POSITIVE_INFINITY),
  sumIn(mkPoints(() => 1), hourPrevStart, hourCut),
);
const costDelta = deltaVsPrevHour(
  sumIn(mkPoints((l) => l.cost), hourCut, Number.POSITIVE_INFINITY),
  sumIn(mkPoints((l) => l.cost), hourPrevStart, hourCut),
);
const errDelta = deltaVsPrevHour(
  sumIn(mkPoints((l) => (l.status >= 400 ? 1 : 0)), hourCut, Number.POSITIVE_INFINITY),
  sumIn(mkPoints((l) => (l.status >= 400 ? 1 : 0)), hourPrevStart, hourCut),
);
```

Note: `sumIn(hi)` is exclusive; current window uses `Infinity` so it includes events right up to now.

- [ ] **Step 2: Featured tiles get spark/delta/waiting**

Per tile:
- req/min: `spark={reqSpark}` `delta={reqDelta}` `deltaGoodDir="up"`
- spend: `spark={costSpark}` `delta={costDelta}` (goodDir defaults to down)
- error rate: `spark={errSpark}` `delta={errDelta}` (goodDir defaults to down)
- p95 ttft: `spark={ttftSpark}`, no delta prop (its sub line already carries p95 latency)
- all four tiles: `waiting={!hasTraffic}`

- [ ] **Step 3: ChartTooltip component + tokens chart restyle**

Define above `DashboardPage`:

```tsx
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
```

Tokens/min chart:
- Gradient stop opacities `0.45 → 0.03` become `0.10 → 0.02`.
- Reorder the four `<Area>` elements to: tok_in ("input"), tok_out ("output"), tok_cached ("cached"), tok_reasoning ("reasoning").
- Tooltip: `<Tooltip content={<ChartTooltip fmt={(v) => fmtInt(v)} />} cursor={{ stroke: "#3b82f6", strokeOpacity: 0.3 }} />`
- Grid: `<CartesianGrid stroke="#ffffff" strokeOpacity={0.06} vertical={false} />` (remove `strokeDasharray`).

- [ ] **Step 4: Requests & errors chart restyle**

- Same grid replacement as Step 3.
- Same ChartTooltip swap.
- Delete the `TOOLTIP_STYLE` constant and the `CSSProperties` import once nothing references them.

- [ ] **Step 5: Live sparkline gradient quieter**

In `LiveSparkline`: area gradient stops `0.32/0` become `0.18/0`.

- [ ] **Step 6: Refetch opacity hold**

Outer div of `DashboardPage` becomes:

```tsx
<div
  style={{
    opacity: overviewQuery.isFetching && overviewQuery.data ? 0.7 : 1,
    transition: "opacity 200ms",
  }}
>
```

- [ ] **Step 7: Build + lint**

Run: `cd web && bun run build && bun run lint`
Expected: both pass.

- [ ] **Step 8: Commit**

```bash
git add web/src/pages/Dashboard.tsx
git commit -m "Wire tile sparklines, deltas, and refined chart marks on Dashboard"
```

---

### Task 5: Visual verification against live gateway

**Files:** none (verification only)

- [ ] **Step 1: Start gateway, open admin UI**

```bash
WIWI_MASTER_KEY=dev-master .venv/bin/wiwi --config wiwi.yaml --port 4101
```

Open `http://localhost:4101/admin/ui`, log in with `dev-master`, screenshot the Dashboard.

- [ ] **Step 2: Verify checklist**

- Zero traffic: featured values pulse softly; charts render axes without data; no NaN or crash in console.
- Seeded traffic (loop of curl POSTs through a virtual key, or bench.py): tile sparklines appear; delta chips show real percentages (or muted "—" when the prior hour is empty); stack order reads input → output → cached → reasoning bottom-up; gridlines solid hairlines; tooltip rows show bold value then dim series name with a line-key swatch.
- Refetch does not flash skeletons — page dims briefly instead.

- [ ] **Step 3: Fix any defects found, rebuild, commit**

```bash
git add -A web/src
git commit -m "Polish dashboard visuals from live verification"
```
