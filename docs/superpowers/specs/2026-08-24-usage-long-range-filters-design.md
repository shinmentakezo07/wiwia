# Design: Extend Usage time-range filters to 7d / 30d / all

**Date:** 2026-08-24
**Status:** Approved (pending spec review)

## Problem

The Usage page range selector offers 15 min, 1 hour, 6 hours, and 24 hours. Users
want 7-day, 30-day, and all-time views. Two blockers prevent simply adding dropdown
entries:

1. **Backend cap:** `server/app.py` clamps `minutes` to `max(1, min(minutes, 1440))`
   — a hard 24-hour ceiling on both `/admin/stats/overview` and
   `/admin/stats/timeseries`.

2. **Data source:** The stats endpoints read from an in-memory ring buffer capped
   at 500 events (`_request_events()` → `state.logs.sse.replay("request", 0)`).
   They do not read from the SQLite `request_logs` table, which persists full
   history with no retention limit. So even raising the cap without changing the
   data source would show at most the last 500 requests, not 7 days of data.

The SQLite `request_logs` table already stores every request log (written
batched by `DBSink.write_requests`). It has all the columns the stats functions
need. The fix is to add DB-backed aggregate queries and route long ranges to them.

## Architecture

### Data routing rule

| Range | Stats source | Bucket size |
|-------|-------------|-------------|
| ≤ 24 h (1440 min) | In-memory ring buffer (existing path) | 1 min |
| 7 d (10080 min) | DB | 1 hour |
| 30 d (43200 min) | DB | 6 hours |
| all | DB | 1 day |

Short ranges keep the existing fast in-memory path (no DB query needed, and it
works even when no DB is configured). Long ranges route to DB-backed aggregate
queries that scale to full history.

When no DB sink is configured (`db_sink is None`), long ranges fall back to the
in-memory ring buffer — incomplete but functional. The UI subtitle will indicate
"(in-memory only)" in this case.

### Backend changes

#### `wiwi/server/stats.py`

Add a pure helper:

```python
def bucket_size_for(minutes: int) -> int:
    """Return bucket size in seconds appropriate for the range.
    minutes == 0 means all-time."""
    if minutes == 0:      # all-time
        return 86400
    if minutes <= 1440:   # ≤ 24h
        return 60
    if minutes <= 10080:  # ≤ 7d
        return 3600
    if minutes <= 43200:   # ≤ 30d
        return 21600
    return 86400           # > 30d
```

The existing `overview()` and `timeseries()` in-memory functions are unchanged.

#### `wiwi/server/db_sink.py`

Add a ts index in `startup()` migration:

```python
await conn.execute(sa.text(
    "CREATE INDEX IF NOT EXISTS idx_request_logs_ts ON request_logs(ts)"))
```

Add two async methods to `DBSink`:

**`read_overview(minutes: int) -> dict`**

Single aggregate query over rows with `ts >= now - minutes*60` (no cutoff when
`minutes <= 0`, meaning "all"):

```sql
SELECT COUNT(*) AS requests,
       SUM(CASE WHEN status >= 400 OR error_code != '' THEN 1 ELSE 0 END) AS errors,
       SUM(tok_in), SUM(tok_cached), SUM(tok_reasoning), SUM(tok_out),
       SUM(CASE WHEN cache_hit = 1 OR tok_cached > 0 THEN 1 ELSE 0 END) AS cache_hits,
       SUM(cost), SUM(cache_savings)
FROM request_logs
WHERE ts >= :cutoff   -- omitted when "all"
```

p95 values (tps, ttft_ms, latency_ms) are computed from a bounded sample to
avoid scanning the full table on large ranges:

```sql
SELECT tps, ttft_ms, latency_ms FROM request_logs
WHERE ts >= :cutoff AND tps > 0
ORDER BY id DESC LIMIT 5000
```

Then Python-side `_p95()` from `stats.py` is applied. This bounds work to 5000
rows regardless of table size.

Returns the same dict shape as `stats.overview()` — `window_minutes`, `generated_at`,
`requests`, `errors`, `error_rate`, `requests_per_minute`, `tok_in`, `tok_cached`,
`tok_reasoning`, `tok_out`, `cache_hits`, `cache_hit_rate`, `tps_avg`, `tps_p95`,
`ttft_p95_ms`, `latency_p95_ms`, `cost`, `cache_savings`.

**`read_timeseries(bucket_seconds: int, metric: str, minutes: int) -> dict`**

GROUP BY time bucket:

```sql
SELECT :bucket_start + (CAST((ts - :bucket_start) / :bucket_seconds AS INTEGER)) * :bucket_seconds AS bucket_t,
       SUM(tok_in), SUM(tok_cached), SUM(tok_reasoning), SUM(tok_out),
       -- for tps metric:
       COUNT(CASE WHEN tps > 0 THEN 1 END) AS tps_count,
       SUM(CASE WHEN tps > 0 THEN tps ELSE 0 END) AS tps_sum
FROM request_logs
WHERE ts >= :cutoff
GROUP BY bucket_t
ORDER BY bucket_t
```

For `metric == "tps"`, `tps_avg = tps_sum / tps_count` and `tps_p95` is
approximated as the max tps in the bucket (true p95 per bucket would require a
window function or per-bucket subquery; max is a reasonable approximation for a
trend chart and avoids expensive per-bucket subqueries).

Returns `{"bucket_seconds": bucket_seconds, "metric": metric, "buckets": [...]}` —
same shape as `stats.timeseries()`.

#### `wiwi/server/app.py`

1. Raise the `minutes` cap from 1440 to 43200 (30 days) on both stats endpoints.
2. Accept `minutes=0` (or a dedicated `range=all` query param) as "all time" —
   no `ts` cutoff. Implementation: treat `minutes <= 0` as all-time.
3. Route logic in both `/admin/stats/overview` and `/admin/stats/timeseries`:

```python
minutes = max(0, min(minutes, 43200))  # 0 = all, max 30d explicit
sink = state.logs.db_sink
use_db = sink is not None and (minutes == 0 or minutes > 1440)
if use_db:
    # DB-backed path
    result = await sink.read_overview(minutes)  # or read_timeseries(...)
else:
    # Existing in-memory path
    minutes_ring = minutes if minutes > 0 else 1440
    result = stats_mod.overview(_request_events(), minutes_ring)
```

4. For `timeseries`, when using the DB path, compute the bucket size via
   `stats_mod.bucket_size_for(minutes)` and pass it to
   `sink.read_timeseries(bucket_size, metric, minutes)`. When using the
   in-memory path, keep `bucket="minute"` (60s) as today.

### Frontend changes

#### `web/src/pages/Usage.tsx`

Extend `RANGE_OPTIONS`:

```typescript
const RANGE_OPTIONS = [
  { value: "15", label: "Last 15 min" },
  { value: "60", label: "Last hour" },
  { value: "360", label: "Last 6 hours" },
  { value: "1440", label: "Last 24 hours" },
  { value: "10080", label: "Last 7 days" },
  { value: "43200", label: "Last 30 days" },
  { value: "all", label: "All time" },
];
```

Change `range` state from `number` to `number | "all"` (or use `-1` internally
to represent "all"). The `allLogs` cutoff calculation:

```typescript
const allLogs = useMemo(() => {
  const all = logsQuery.data?.logs ?? [];
  if (range === "all") return all;  // no cutoff
  const cutoff = Math.floor(Date.now() / 1000) - (range as number) * 60;
  return all.filter((l) => l.ts >= cutoff);
}, [logsQuery.data, range]);
```

The per-request table (`getRequestLogs`) is capped at 200 most-recent rows by the
backend. For 7d/30d/all ranges this shows only the latest 200 requests. The table
header already shows row count; no change needed beyond keeping the count label
accurate.

X-axis tick formatter for the TPS chart: switch from `fmtTime` (time only) to
`fmtDateTime` (date + time) when the range exceeds 24 hours, so multi-day ranges
show distinguishable labels.

#### `web/src/api/client.ts`

`getOverview` and `getTimeseries` already pass `minutes` as a query param.
Handle the "all" case:

```typescript
export const getOverview = (minutes: number | "all") =>
  api<OverviewStats>(`/admin/stats/overview?minutes=${minutes}`);

export const getTimeseries = (metric: TimeseriesMetric, minutes: number | "all") =>
  api<TimeseriesResponse>(
    `/admin/stats/timeseries?bucket=minute&metric=${metric}&minutes=${minutes}`,
  );
```

The backend treats `minutes=0` or `minutes=all` as all-time. To keep it simple,
the frontend sends `minutes=0` for "all" (the backend already accepts `int` and
the routing logic treats `<= 0` as all-time).

The `bucket=minute` query param on `getTimeseries` becomes a hint that the
backend may override when routing to the DB path (the DB path computes its own
bucket size via `bucket_size_for()`). The response's `bucket_seconds` field tells
the frontend the actual bucket size used.

### Data flow

```
Usage.tsx (range = 7d)
  → getOverview(10080) → /admin/stats/overview?minutes=10080
     → app.py: minutes=10080 > 1440, db_sink exists
       → DBSink.read_overview(10080)
         → SELECT COUNT, SUM(...) WHERE ts >= now - 7d
         → p95 from bounded 5000-row sample
       → returns OverviewStats (same shape)
  → getTimeseries("tps", 10080) → /admin/stats/timeseries?...&minutes=10080
     → app.py: → bucket_size_for(10080) = 3600
       → DBSink.read_timeseries(3600, "tps", 10080)
         → GROUP BY (ts / 3600) bucket
       → returns {bucket_seconds: 3600, buckets: [...]}
  → getRequestLogs() → /admin/logs/requests (unchanged, limit=200)
     → table shows latest 200 requests within range
```

## Error handling & edge cases

- **No DB configured:** `db_sink is None`. Long ranges fall back to the
  in-memory ring buffer (limited to 500 events). The UI subtitle shows
  "(in-memory only)" to set expectations.
- **Empty DB / no data in range:** overview returns zeros, timeseries returns
  empty buckets. Both already handled by the existing frontend empty states.
- **Very large "all" query:** bounded by the p95 sample (5000 rows). SUM/COUNT
  queries are O(rows) but the `idx_request_logs_ts` index makes the WHERE clause
  an index range scan. SQLite handles millions of rows of aggregate queries in
  well under a second with the index.
- **`hourlySeries` sparkline mismatch:** stat-card sparklines always reflect the
  last hour of activity (the helper buckets into 5-min slots over the last 60
  min). For 7d+ ranges this means the sparkline shows only the most recent hour,
  not the full range. This is a known cosmetic limitation; the primary trend chart
  (TPS over time) does reflect the full selected range. Not blocking.
- **Per-request table for long ranges:** capped at 200 rows (backend `limit`
  param). The table remains useful as a "recent requests" view; full-range
  aggregate data lives in the stat cards and charts. The header already shows
  row count.

## Testing

### Backend tests — `tests/test_stats_db.py` (new file)

Table-driven tests with a temp SQLite engine:

1. Insert synthetic `LogEvent` rows via `DBSink.write_requests()` spanning
   multiple days.
2. Assert `read_overview(minutes)` produces correct request counts, token sums,
   cost sums, error counts, cache hit counts for ranges: 60 min, 1440 min,
   10080 min, 43200 min, and all-time.
3. Assert `read_timeseries(bucket_size, metric, minutes)` produces the correct
   number of buckets and correct per-bucket aggregates for each range.
4. Assert p95 values are within expected bounds given known input data.
5. Assert the `ts` index is created on startup (idempotent re-run).

### Existing tests

Run `.venv/bin/python -m pytest tests/ -q` and `.venv/bin/ruff check wiwi/ tests/`
before committing. All must pass.

### Frontend

No automated UI tests exist for the Usage page. Manual verification via the
running admin panel: select each new range, confirm charts and stat cards update,
confirm axis labels show dates for multi-day ranges.

## Scope summary

| Layer | File | Change |
|-------|------|--------|
| Backend | `wiwi/server/db_sink.py` | Add `read_overview()`, `read_timeseries()`, `idx_request_logs_ts` |
| Backend | `wiwi/server/stats.py` | Add `bucket_size_for()` helper |
| Backend | `wiwi/server/app.py` | Raise cap to 43200, route long ranges to DB, handle all-time |
| Frontend | `web/src/pages/Usage.tsx` | Add 7d/30d/all options, axis label formatting for multi-day |
| Frontend | `web/src/api/client.ts` | Accept `number \| "all"` for minutes param |
| Tests | `tests/test_stats_db.py` | DB-backed stats tests (table-driven) |

## Non-goals

- Paginated per-request table for long ranges (200-row cap stays).
- True per-bucket p95 in timeseries (approximated as max for performance).
- DB-backed stats for short ranges (≤ 24h keeps the fast in-memory path).
- Frontend sparkline changes for long ranges (sparklines stay last-hour scoped).
- DB retention/pruning policy (full history retained; future work).
