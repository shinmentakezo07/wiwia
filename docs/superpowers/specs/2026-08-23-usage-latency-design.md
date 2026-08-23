# Usage Page: Latency Histogram + Group Filter

**Date:** 2026-08-23
**Scope:** `web/src/pages/Usage.tsx` (single file, no backend changes)

## Goal

Enhance the Usage page with a new latency distribution histogram and add
interactivity so clicking a row in the grouped summary filters all charts and
stats to that group.

## New Visualization: Latency Distribution Histogram

A full-width card inserted between the existing charts row (TPS area chart +
token-share donut) and the grouped summary table.

### Data

Computed client-side from the filtered log subset (`logs` array). Each request
has `ttft_ms` and `latency_ms` fields.

### Buckets (logarithmic — latency is heavy-tailed)

| Bucket label | Range (ms) |
|---|---|
| `<100` | 0–100 |
| `100–250` | 100–250 |
| `250–500` | 250–500 |
| `500–1K` | 500–1000 |
| `1K–2K` | 1000–2000 |
| `2K–4K` | 2000–4000 |
| `4K+` | 4000+ |

Requests with `ttft_ms === 0` or `latency_ms === 0` (non-streaming or missing)
are excluded from the respective metric.

### Card header

- Title: "Latency distribution"
- Right side: a `Select` toggle with two options — `ttft` (default) and `total`
- The active metric drives the bar color: `#3b82f6` (blue) for TTFT, `#a855f7`
  (violet) for total latency — matching the TPS chart's existing avg/p95 pairing.

### Chart

- recharts `BarChart` with `Bar` (radius top corners), `CartesianGrid`, `XAxis`,
  `YAxis`, `Tooltip`.
- X-axis: bucket labels (categorical).
- Y-axis: request count (integer ticks).
- Tooltip uses the existing `ChartTooltip` component with a formatter showing
  the request count.
- Chart height: 260px (matches the TPS and donut charts).
- Empty state: "No latency data in this window." when no requests have nonzero
  values for the active metric.

## Interactivity: Group Filter

### State

```ts
type GroupFilter = { dim: GroupDim; name: string } | null;
const [filterGroup, setFilterGroup] = useState<GroupFilter>(null);
```

### Filter chip

When active, a removable badge appears in the PageHeader `right` area (before the
range `Select`):

```
[ model: gpt-4o ✕ ]    [ range select ]
```

Styled as a violet `admin-badge` with an X button. Clicking X clears the filter.
The badge label is `{dim}: {name}` (e.g. `model: gpt-4o`, `key: team-a`,
`provider: openai`).

### Affected components

When `filterGroup` is set, `logs` is narrowed to entries where
`groupKeyOf(l, filter.dim) === filter.name` before computing:

- Hero stats (requests, spend, cache hit rate, avg tps)
- Secondary stats (tokens in/cached/reasoning/output, errors, total tokens)
- TPS area chart data (derived from filtered logs via `mkPoints`)
- Token share donut
- Latency histogram
- Requests table (rows + totals)

### Not affected

- The grouped summary table stays computed from the **unfiltered** log set (it is
  the filter control surface). The active row gets a `ring-1 ring-violet-500/30`
  highlight and `cursor-pointer` hover on all rows.
- Range, sort, and group-dim selects are unchanged.

### Toggle behavior

- Clicking an unactive group row sets `filterGroup` to that row.
- Clicking the active group row clears the filter (toggle off).
- Changing the group-dim `Select` clears `filterGroup` (old group name no longer
  applies).

### Requests table header note

When filtered, the table header right-side text changes from
`{N} rows · click a column to sort` to `{N} rows · filtered by {dim}: {name}`.

## Layout (final)

```
Hero stats (4 cards)                    ← filtered
Secondary stats (6 cards)               ← filtered
Charts row (TPS + donut)               ← filtered
Latency distribution (new, full-width)  ← filtered
Grouped summary                         ← UNFILTERED (filter control)
Requests table                          ← filtered
```

## Non-goals

- No new API endpoints or queries — all derived client-side from existing
  `getRequestLogs` data.
- No changes to other pages, the design system CSS, or the component kit.
- No cost-per-model bars, error breakdown, or cache-savings card (out of scope
  per user selection).
