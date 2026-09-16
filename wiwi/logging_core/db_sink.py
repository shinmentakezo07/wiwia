"""DB persistence for request + audit log streams (batched writes).

Works with both SQLite (aiosqlite) and PostgreSQL (asyncpg).  DDL and
queries are written in a dialect-portable way: ``AUTOINCREMENT`` (SQLite)
vs ``SERIAL`` (Postgres) is resolved via a startup dialect check;
``PRAGMA`` is replaced with ``information_schema`` for column migration
detection on Postgres; bucket math uses ``FLOOR()`` instead of
``CAST(... AS INTEGER)`` for correct truncation on both engines.
"""
from __future__ import annotations

import time

import orjson
import sqlalchemy as sa

from wiwi.logging_core.events import LogEvent
from wiwi.server.stats import VALID_METRICS, _p95

# DDL is shared between SQLite and Postgres.  The only difference is the
# auto-increment syntax, which is resolved at startup time.
_REQUEST_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS request_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  request_id TEXT DEFAULT '',
  surface TEXT DEFAULT '',
  key_alias TEXT DEFAULT '',
  key_id TEXT DEFAULT '',
  model_group TEXT DEFAULT '',
  provider TEXT DEFAULT '',
  provider_key_label TEXT DEFAULT '',
  status INTEGER DEFAULT 200,
  error_code TEXT DEFAULT '',
  tok_in INTEGER DEFAULT 0,
  tok_cached INTEGER DEFAULT 0,
  tok_cache_creation INTEGER DEFAULT 0,
  tok_reasoning INTEGER DEFAULT 0,
  tok_out INTEGER DEFAULT 0,
  usage_estimated INTEGER DEFAULT 0,
  tps REAL DEFAULT 0,
  ttft_ms REAL DEFAULT 0,
  latency_ms REAL DEFAULT 0,
  cost REAL DEFAULT 0,
  was_stream INTEGER DEFAULT 0,
  cache_hit INTEGER DEFAULT 0,
  cache_savings REAL DEFAULT 0,
  response_cache_hit INTEGER DEFAULT 0,
  attempts TEXT DEFAULT '[]',
  request_body TEXT,
  response_body TEXT
);
"""

_REQUEST_DDL_PG = """
CREATE TABLE IF NOT EXISTS request_logs (
  id SERIAL PRIMARY KEY,
  ts DOUBLE PRECISION NOT NULL,
  request_id TEXT DEFAULT '',
  surface TEXT DEFAULT '',
  key_alias TEXT DEFAULT '',
  key_id TEXT DEFAULT '',
  model_group TEXT DEFAULT '',
  provider TEXT DEFAULT '',
  provider_key_label TEXT DEFAULT '',
  status INTEGER DEFAULT 200,
  error_code TEXT DEFAULT '',
  tok_in INTEGER DEFAULT 0,
  tok_cached INTEGER DEFAULT 0,
  tok_cache_creation INTEGER DEFAULT 0,
  tok_reasoning INTEGER DEFAULT 0,
  tok_out INTEGER DEFAULT 0,
  usage_estimated INTEGER DEFAULT 0,
  tps DOUBLE PRECISION DEFAULT 0,
  ttft_ms DOUBLE PRECISION DEFAULT 0,
  latency_ms DOUBLE PRECISION DEFAULT 0,
  cost DOUBLE PRECISION DEFAULT 0,
  was_stream INTEGER DEFAULT 0,
  cache_hit INTEGER DEFAULT 0,
  cache_savings DOUBLE PRECISION DEFAULT 0,
  response_cache_hit INTEGER DEFAULT 0,
  attempts TEXT DEFAULT '[]',
  request_body TEXT,
  response_body TEXT
);
"""

_AUDIT_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  actor TEXT DEFAULT '',
  action TEXT DEFAULT '',
  target TEXT DEFAULT '',
  diff TEXT DEFAULT '{}'
);
"""

_AUDIT_DDL_PG = """
CREATE TABLE IF NOT EXISTS audit_logs (
  id SERIAL PRIMARY KEY,
  ts DOUBLE PRECISION NOT NULL,
  actor TEXT DEFAULT '',
  action TEXT DEFAULT '',
  target TEXT DEFAULT '',
  diff TEXT DEFAULT '{}'
);
"""

_ROLLUP_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS request_rollups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bucket_ts REAL NOT NULL,
  key_id TEXT DEFAULT '',
  model_group TEXT DEFAULT '',
  provider TEXT DEFAULT '',
  serving_model TEXT DEFAULT '',
  requests INTEGER DEFAULT 0,
  errors INTEGER DEFAULT 0,
  estimated_requests INTEGER DEFAULT 0,
  cache_hits INTEGER DEFAULT 0,
  tok_in INTEGER DEFAULT 0,
  tok_cached INTEGER DEFAULT 0,
  tok_cache_creation INTEGER DEFAULT 0,
  tok_reasoning INTEGER DEFAULT 0,
  tok_out INTEGER DEFAULT 0,
  cost REAL DEFAULT 0,
  cache_savings REAL DEFAULT 0,
  tps_sum REAL DEFAULT 0,
  tps_count INTEGER DEFAULT 0,
  tps_p95 REAL DEFAULT 0,
  ttft_p95_ms REAL DEFAULT 0,
  latency_p95_ms REAL DEFAULT 0,
  unpriced_requests INTEGER DEFAULT 0,
  unpriced_tok_in INTEGER DEFAULT 0,
  unpriced_tok_cached INTEGER DEFAULT 0,
  unpriced_tok_cache_creation INTEGER DEFAULT 0,
  unpriced_tok_out INTEGER DEFAULT 0
);
"""

_ROLLUP_DDL_PG = """
CREATE TABLE IF NOT EXISTS request_rollups (
  id SERIAL PRIMARY KEY,
  bucket_ts DOUBLE PRECISION NOT NULL,
  key_id TEXT DEFAULT '',
  model_group TEXT DEFAULT '',
  provider TEXT DEFAULT '',
  serving_model TEXT DEFAULT '',
  requests INTEGER DEFAULT 0,
  errors INTEGER DEFAULT 0,
  estimated_requests INTEGER DEFAULT 0,
  cache_hits INTEGER DEFAULT 0,
  tok_in INTEGER DEFAULT 0,
  tok_cached INTEGER DEFAULT 0,
  tok_cache_creation INTEGER DEFAULT 0,
  tok_reasoning INTEGER DEFAULT 0,
  tok_out INTEGER DEFAULT 0,
  cost DOUBLE PRECISION DEFAULT 0,
  cache_savings DOUBLE PRECISION DEFAULT 0,
  tps_sum DOUBLE PRECISION DEFAULT 0,
  tps_count INTEGER DEFAULT 0,
  tps_p95 DOUBLE PRECISION DEFAULT 0,
  ttft_p95_ms DOUBLE PRECISION DEFAULT 0,
  latency_p95_ms DOUBLE PRECISION DEFAULT 0,
  unpriced_requests INTEGER DEFAULT 0,
  unpriced_tok_in INTEGER DEFAULT 0,
  unpriced_tok_cached INTEGER DEFAULT 0,
  unpriced_tok_cache_creation INTEGER DEFAULT 0,
  unpriced_tok_out INTEGER DEFAULT 0
);
"""

# Columns written per rollup row. The unique index over the five leading
# columns makes the upsert idempotent. ``serving_model`` is the deployment's
# model id (the tail of "<group>/<model_id>"), kept so retroactive pricing can
# still find and correct these rows after the raw ones are gone.
_ROLLUP_KEY_COLS = ("bucket_ts", "key_id", "model_group", "provider",
                    "serving_model")
_ROLLUP_ADDITIVE = ("requests", "errors", "estimated_requests", "cache_hits",
                    "tok_in", "tok_cached", "tok_cache_creation",
                    "tok_reasoning", "tok_out", "cost", "cache_savings",
                    "tps_sum", "tps_count",
                    "unpriced_requests", "unpriced_tok_in",
                    "unpriced_tok_cached", "unpriced_tok_cache_creation",
                    "unpriced_tok_out")
_ROLLUP_COLS = _ROLLUP_KEY_COLS + _ROLLUP_ADDITIVE + (
    "tps_p95", "ttft_p95_ms", "latency_p95_ms")

# Columns added to request_rollups after its first release. An existing table
# (CREATE TABLE IF NOT EXISTS is a no-op on it) must be widened in place by
# _migrate BEFORE idx_rollup_unique is recreated — the unique index covers
# serving_model, so a database created by the earlier four-dimension version
# would otherwise die at startup with UndefinedColumnError. Declarations are
# portable: TEXT/INTEGER mean the same on SQLite and Postgres. Migrated rows
# read as serving_model = '' / unpriced counts 0 — retroactive pricing still
# rescans the raw request_logs rows, so only already-rolled-up history keeps
# its old zero cost.
_ROLLUP_MIGRATE_COLUMNS = (
    ("serving_model", "TEXT DEFAULT ''"),
    ("unpriced_requests", "INTEGER DEFAULT 0"),
    ("unpriced_tok_in", "INTEGER DEFAULT 0"),
    ("unpriced_tok_cached", "INTEGER DEFAULT 0"),
    ("unpriced_tok_cache_creation", "INTEGER DEFAULT 0"),
    ("unpriced_tok_out", "INTEGER DEFAULT 0"),
)


class _BucketSum:
    """Merged view over a raw bucket row plus its rolled-up counterpart.

    Duck-types the column attributes the timeseries reader touches
    (``tok_*``, ``tps_sum``, ``tps_count``, ``tps_max``), so merging two
    sources needs no change at the read site.

    Every member is a sum except ``tps_max``, which is a maximum on both
    sides and so is maxed rather than added (AUDIT #150).
    """

    __slots__ = ("tok_cache_creation", "tok_cached", "tok_in", "tok_out",
                 "tok_reasoning", "tps_count", "tps_max", "tps_sum")

    def __init__(self, a, b) -> None:
        for f in self.__slots__:
            if f == "tps_max":
                continue
            setattr(self, f, (getattr(a, f, 0) or 0) + (getattr(b, f, 0) or 0))
        # tps_max is the one non-additive member: both sides already reduced
        # their rows to a peak (MAX(CASE WHEN tps > 0 ...) over the raw rows,
        # MAX(tps_p95) over the rolled-up hour) and the timeseries reader
        # publishes it as the bucket's tps_p95. Summing two maxima reports a
        # throughput that never occurred — take the larger, as _merge_p95
        # already does for the percentiles.
        self.tps_max = max(getattr(a, "tps_max", 0) or 0,
                           getattr(b, "tps_max", 0) or 0)


def _merge_p95(samples: list[float], pairs: list[tuple[float, int]]) -> float:
    """Combine raw samples with pre-aggregated ``(p95, weight)`` pairs.

    Exact when only one side is populated — the common cases of a window that
    is entirely raw (recent) or entirely rolled up (old). When both are
    present, weighting each side's p95 by its sample count and taking the
    larger is an approximation that is monotone in the data and never reports
    a percentile below the true median of the combined set; it cannot
    reconstruct the true p95 without the original samples, which is precisely
    what the rollup discarded.
    """
    weighted: list[tuple[float, int]] = []
    if samples:
        weighted.append((_p95(samples), len(samples)))
    weighted.extend((p, n) for p, n in pairs if n > 0)
    if not weighted:
        return 0.0
    if len(weighted) == 1:
        return weighted[0][0]
    total = sum(n for _, n in weighted)
    return sum(p * n for p, n in weighted) / total if total else 0.0

_COLS = ("ts", "request_id", "surface", "key_alias", "key_id", "model_group",
         "provider", "provider_key_label", "status", "error_code", "tok_in",
         "tok_cached", "tok_cache_creation", "tok_reasoning", "tok_out",
         "usage_estimated", "tps",
         "ttft_ms", "latency_ms", "cost", "was_stream", "cache_hit",
         "cache_savings", "response_cache_hit", "attempts", "request_body",
         "response_body")


class DBSink:
    # TTL cache coalesces concurrent log/stat queries so a burst of dashboard
    # refreshes hits the DB once instead of running N copies of the same heavy
    # SQL. Matches the AuthService caching pattern: dict of (value, ts) tuples
    # with a short monotonic-clock TTL.
    #
    # Every write clears the cache (see ``invalidate_cache`` callers below):
    # the 5 s TTL alone meant a request logged a moment ago stayed invisible
    # to /admin/stats/* and /admin/logs/* until it expired. The batch pump
    # writes at most once per drain, so the clear costs a handful of extra
    # queries under load — the price of a dashboard that shows the request you
    # just made. Only non-empty results are cached, so a clear cannot be
    # followed by a re-cache of a stale empty page.
    _CACHE_TTL = 5.0
    # Bound on cached query results. Every distinct (limit, key_ids) tuple a
    # caller presents is its own entry, so without a cap the dict grew with
    # the number of distinct queries ever served. Matches the ceiling
    # AuthService puts on its own auth-info cache.
    _CACHE_MAX_ENTRIES = 4096

    def __init__(self, engine) -> None:
        self.engine = engine
        self._is_pg = engine.dialect.name == "postgresql"
        self._query_cache: dict[tuple, tuple] = {}

    def _cache_get(self, key: tuple):
        hit = self._query_cache.get(key)
        if hit is None:
            return None
        if time.monotonic() - hit[1] < self._CACHE_TTL:
            return hit[0]
        # Expired — evict so the dict can't grow without bound (the key
        # space includes per-user key_ids tuples, so stale entries would
        # otherwise linger forever as a slow memory leak).
        self._query_cache.pop(key, None)
        return None

    def _cache_put(self, key: tuple, value) -> None:
        self._sweep_cache()
        self._query_cache[key] = (value, time.monotonic())

    def _sweep_cache(self) -> None:
        """Drop expired entries once the cache grows past a bound.

        ``_cache_get`` evicts only the key being read, so a key that is
        written once and never re-read would otherwise live forever — the
        cache grew with the number of *distinct* queries ever served, not
        with the live data, and the key space is multiplied by each caller's
        key_ids tuple. Same two-phase shape as AuthService._sweep_cache:
        expired entries first, then the oldest half if everything is fresh,
        so the dict is bounded even under sustained distinct-key traffic.
        """
        if len(self._query_cache) < self._CACHE_MAX_ENTRIES:
            return
        now = time.monotonic()
        for k, (_value, ts) in list(self._query_cache.items()):
            if now - ts >= self._CACHE_TTL:
                del self._query_cache[k]
        if len(self._query_cache) < self._CACHE_MAX_ENTRIES:
            return
        # Still full and everything is fresh: evict by insertion age. dicts
        # preserve insertion order, so the front is the least recently written.
        for k in list(self._query_cache)[:len(self._query_cache) // 2]:
            del self._query_cache[k]

    def invalidate_cache(self) -> None:
        """Drop all cached query results.

        Called by both write paths (``write_requests``/``write_audit``) so a
        row is never hidden from the next read by the ``_CACHE_TTL`` window.
        Also safe to call manually after out-of-band data changes.
        """
        self._query_cache.clear()

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            request_ddl = _REQUEST_DDL_PG if self._is_pg else _REQUEST_DDL_SQLITE
            audit_ddl = _AUDIT_DDL_PG if self._is_pg else _AUDIT_DDL_SQLITE
            rollup_ddl = _ROLLUP_DDL_PG if self._is_pg else _ROLLUP_DDL_SQLITE
            await conn.execute(sa.text(request_ddl))
            await conn.execute(sa.text(audit_ddl))
            await conn.execute(sa.text(rollup_ddl))
            await self._migrate(conn)

    async def prune_old_requests(self, retention_days: int) -> int:
        """Roll old rows into ``request_rollups``, then delete them.

        Returns the number of raw rows deleted. Aggregates for every deleted
        row survive permanently in ``request_rollups``, so the dashboard's
        totals, token counts, cost and percentiles stay complete no matter how
        far back the window reaches — only the per-request detail is dropped.

        ``retention_days <= 0`` means "keep everything" and is a no-op.
        """
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        return await self.rollup_and_prune(cutoff)

    async def rollup_and_prune(self, cutoff_ts: float) -> int:
        """Aggregate ``request_logs`` rows older than *cutoff_ts*, then delete them.

        Rows are grouped by ``(hour, key_id, model_group, provider,
        serving_model)`` — the dimensions the console slices by, plus the model
        id so retroactive pricing can still find these rows after the raw ones
        are gone. One row per distinct group per hour, instead of one per
        request.

        The aggregate and the delete share ONE transaction: a crash between
        them would otherwise either lose the rows or double-count them on the
        next run.

        Percentiles cannot be summed, so each row stores the p95 of its own
        bucket (``tps_p95``, ``ttft_p95_ms``, ``latency_p95_ms``) and reads
        reconstruct a weighted mean. That is exact when a window is entirely
        raw or entirely rolled up (the normal cases) and approximate in the
        mixed band; the alternative is keeping every sample forever, which is
        the growth this exists to stop.

        Returns the number of raw rows deleted.
        """
        bucket_s = 3600
        # Per-group accumulators. Keyed by the full five-tuple.
        groups: dict[tuple, dict] = {}
        # Bounded per-group percentile samples, mirroring the max-5000 window
        # the overview read uses — unbounded lists here would reintroduce the
        # memory growth this method removes.
        samples: dict[tuple, dict[str, list[float]]] = {}
        max_samples = 5000
        batch = 2000
        last_id = -1

        async with self.engine.begin() as conn:
            # Keyset pagination: immune to offset drift, and bounded memory
            # because only the accumulators grow, not the row list.
            while True:
                rows = (await conn.execute(sa.text("""
                    SELECT id, ts, key_id, model_group, provider, status,
                           error_code, attempts, tok_in, tok_cached,
                           tok_cache_creation, tok_reasoning, tok_out, cost,
                           cache_savings, cache_hit, usage_estimated, tps,
                           ttft_ms, latency_ms
                    FROM request_logs
                    WHERE ts < :cutoff AND id > :last
                    ORDER BY id LIMIT :b
                """), {"cutoff": cutoff_ts, "last": last_id, "b": batch})).all()
                if not rows:
                    break
                last_id = rows[-1][0]
                for r in rows:
                    serving = self._serving_attempt(r.attempts, None) or {}
                    model_id = self._model_id_from_attempt(serving, r.model_group)
                    k = (int(r.ts // bucket_s) * bucket_s, r.key_id,
                         r.model_group, r.provider, model_id)
                    acc = groups.get(k)
                    if acc is None:
                        acc = groups[k] = {
                            "requests": 0, "errors": 0, "estimated_requests": 0,
                            "cache_hits": 0, "tok_in": 0, "tok_cached": 0,
                            "tok_cache_creation": 0, "tok_reasoning": 0,
                            "tok_out": 0, "cost": 0.0, "cache_savings": 0.0,
                            "tps_sum": 0.0, "tps_count": 0,
                            "unpriced_requests": 0, "unpriced_tok_in": 0,
                            "unpriced_tok_cached": 0,
                            "unpriced_tok_cache_creation": 0,
                            "unpriced_tok_out": 0,
                        }
                        samples[k] = {"tps": [], "ttft_ms": [], "latency_ms": []}
                    acc["requests"] += 1
                    if (r.status or 0) >= 400 or r.error_code:
                        acc["errors"] += 1
                    if r.usage_estimated:
                        acc["estimated_requests"] += 1
                    if r.cache_hit or r.tok_cached:
                        acc["cache_hits"] += 1
                    acc["tok_in"] += r.tok_in or 0
                    acc["tok_cached"] += r.tok_cached or 0
                    acc["tok_cache_creation"] += r.tok_cache_creation or 0
                    acc["tok_reasoning"] += r.tok_reasoning or 0
                    acc["tok_out"] += r.tok_out or 0
                    acc["cost"] += r.cost or 0.0
                    acc["cache_savings"] += r.cache_savings or 0.0
                    if r.tps:
                        acc["tps_sum"] += r.tps
                        acc["tps_count"] += 1
                    # Unpriced rows (cost 0) keep their token split so a price
                    # added later can still be applied to this group.
                    if not r.cost:
                        acc["unpriced_requests"] += 1
                        acc["unpriced_tok_in"] += r.tok_in or 0
                        acc["unpriced_tok_cached"] += r.tok_cached or 0
                        acc["unpriced_tok_cache_creation"] += r.tok_cache_creation or 0
                        acc["unpriced_tok_out"] += r.tok_out or 0
                    s = samples[k]
                    for col in ("tps", "ttft_ms", "latency_ms"):
                        v = getattr(r, col)
                        if v and len(s[col]) < max_samples:
                            s[col].append(v)

            if not groups:
                return 0

            set_add = ", ".join(f"{c} = request_rollups.{c} + excluded.{c}"
                                for c in _ROLLUP_ADDITIVE)
            set_pct = ", ".join(
                f"{c} = excluded.{c}" for c in
                ("tps_p95", "ttft_p95_ms", "latency_p95_ms"))
            placeholders = ", ".join(f":{c}" for c in _ROLLUP_COLS)
            cols = ", ".join(_ROLLUP_COLS)
            upsert = sa.text(
                f"INSERT INTO request_rollups ({cols}) VALUES ({placeholders}) "
                "ON CONFLICT(bucket_ts, key_id, model_group, provider, "
                "serving_model) DO UPDATE SET "
                f"{set_add}, {set_pct}"
            )
            payload = []
            for k, acc in groups.items():
                s = samples[k]
                payload.append({
                    "bucket_ts": k[0], "key_id": k[1], "model_group": k[2],
                    "provider": k[3], "serving_model": k[4],
                    "tps_p95": _p95(s["tps"]),
                    "ttft_p95_ms": _p95(s["ttft_ms"]),
                    "latency_p95_ms": _p95(s["latency_ms"]),
                    **acc,
                })
            for i in range(0, len(payload), 500):
                await conn.execute(upsert, payload[i:i + 500])

            result = await conn.execute(
                sa.text("DELETE FROM request_logs WHERE ts < :cutoff"),
                {"cutoff": cutoff_ts})
            deleted = result.rowcount or 0
        self.invalidate_cache()
        return deleted


    async def enforce_log_cap(self, max_rows: int) -> int:
        """Keep at most *max_rows* raw rows, rolling the rest up first.

        Caps storage by row count rather than age, which is what an operator
        actually wants: a busy gateway stops growing while a quiet one keeps
        its full history. Returns the number of rows deleted.
        """
        if max_rows <= 0:
            return 0
        async with self.engine.connect() as conn:
            total = (await conn.execute(
                sa.text("SELECT COUNT(*) FROM request_logs"))).scalar() or 0
            if total <= max_rows:
                return 0
            # The cutoff is the ts of the oldest row we intend to KEEP — index
            # max_rows-1 in newest-first order — so `ts < cutoff` deletes
            # exactly the rows beyond the cap. Using offset max_rows would make
            # the boundary row its own cutoff and keep one row too many.
            # Ties on ts keep a few extra rows rather than splitting a
            # same-timestamp group; the next sweep trims them.
            row = (await conn.execute(sa.text(
                "SELECT ts FROM request_logs ORDER BY ts DESC, id DESC "
                "LIMIT 1 OFFSET :off"), {"off": max_rows - 1})).first()
            if row is None:
                return 0
            cutoff = row[0]
        return await self.rollup_and_prune(cutoff)

    async def _migrate(self, conn) -> None:
        """Add columns and indexes introduced after the initial schema (idempotent)."""
        if self._is_pg:
            cols = {r[0] for r in (await conn.execute(sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'request_logs'"))).all()}
        else:
            cols = {r[1] for r in (await conn.execute(
                sa.text("PRAGMA table_info(request_logs)"))).all()}
        for col, decl in [("request_body", "TEXT"), ("response_body", "TEXT"),
                          ("key_id", "TEXT DEFAULT ''"),
                          ("tok_cache_creation", "INTEGER DEFAULT 0"),
                          ("response_cache_hit", "INTEGER DEFAULT 0"),
                          ("usage_estimated", "INTEGER DEFAULT 0")]:
            if col not in cols:
                await conn.execute(
                    sa.text(f"ALTER TABLE request_logs ADD COLUMN {col} {decl}"))

        # Same treatment for request_rollups: it too gained columns after its
        # first release (serving_model for retroactive pricing, the unpriced
        # token split), and the recreated unique index below covers
        # serving_model — so the widening MUST happen before the index
        # statements or a pre-existing database crashes at startup.
        if self._is_pg:
            rcols = {r[0] for r in (await conn.execute(sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'request_rollups'"))).all()}
        else:
            rcols = {r[1] for r in (await conn.execute(
                sa.text("PRAGMA table_info(request_rollups)"))).all()}
        for col, decl in _ROLLUP_MIGRATE_COLUMNS:
            if col not in rcols:
                await conn.execute(
                    sa.text(f"ALTER TABLE request_rollups ADD COLUMN {col} {decl}"))

        # Indexes for query hot paths:
        # - ts: time-range filters in overview, timeseries, and log reads
        # - key_id: per-user filtering (scoping request logs by the key ids a
        #   user owns — key_alias is not unique in vkeys, so scope by key_id)
        # - audit_logs.ts: time-range queries on audit log
        # The rollup unique index gained `serving_model` as a dimension so
        # retroactive pricing can target a model. CREATE UNIQUE INDEX IF NOT
        # EXISTS is additive, so a database created by the earlier 4-column
        # version would keep the narrower index and every ON CONFLICT target
        # would fail to match. Drop and recreate unconditionally — both
        # statements are idempotent and the recreate is cheap.
        await conn.execute(sa.text("DROP INDEX IF EXISTS idx_rollup_unique"))

        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_request_logs_ts ON request_logs(ts)",
            "CREATE INDEX IF NOT EXISTS idx_request_logs_key_id ON request_logs(key_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_ts ON audit_logs(ts)",
            "CREATE INDEX IF NOT EXISTS idx_rollup_bucket ON request_rollups(bucket_ts)",
            ("CREATE UNIQUE INDEX IF NOT EXISTS idx_rollup_unique ON "
             "request_rollups(bucket_ts, key_id, model_group, provider, "
             "serving_model)"),
        ]:
            await conn.execute(sa.text(idx))

        # Drop indexes an earlier version created for queries that were never
        # written (no SQL filters key_alias, model_group or request_id — the
        # only predicates are on ts, key_id, cost and id). CREATE INDEX IF NOT
        # EXISTS is additive, so an existing database keeps them forever
        # without this. Idempotent, and a no-op on databases that never had
        # them.
        for idx in [
            "DROP INDEX IF EXISTS idx_request_logs_key_alias",
            "DROP INDEX IF EXISTS idx_request_logs_model_group",
            "DROP INDEX IF EXISTS idx_request_logs_request_id",
        ]:
            await conn.execute(sa.text(idx))

    @staticmethod
    def _row(evt: LogEvent) -> dict:
        return {
            "ts": evt.ts, "request_id": evt.request_id, "surface": evt.surface,
            "key_alias": evt.key_alias, "key_id": evt.key_id,
            "model_group": evt.model_group,
            "provider": evt.provider, "provider_key_label": evt.provider_key_label,
            "status": evt.status, "error_code": evt.error_code,
            "tok_in": evt.tok_in, "tok_cached": evt.tok_cached,
            "tok_cache_creation": evt.tok_cache_creation,
            "tok_reasoning": evt.tok_reasoning, "tok_out": evt.tok_out,
            "usage_estimated": int(evt.usage_estimated),
            "tps": evt.tps, "ttft_ms": evt.ttft_ms, "latency_ms": evt.latency_ms,
            "cost": evt.cost, "was_stream": int(evt.was_stream),
            "cache_hit": int(evt.cache_hit), "cache_savings": evt.cache_savings,
            "response_cache_hit": int(evt.response_cache_hit),
            "attempts": orjson.dumps(evt.attempts).decode(),
            "request_body": (orjson.dumps(evt.request_body).decode()
                              if evt.request_body is not None else None),
            "response_body": (orjson.dumps(evt.response_body).decode()
                               if evt.response_body is not None else None),
        }

    async def write_requests(self, batch: list[LogEvent]) -> None:
        if not batch:
            return
        cols = ", ".join(_COLS)
        vals = ", ".join(f":{c}" for c in _COLS)
        rows = [self._row(e) for e in batch]
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text(f"INSERT INTO request_logs ({cols}) VALUES ({vals})"), rows)
        # The rows just written must be visible to the very next read; the
        # 5 s TTL would otherwise hide a fresh request from the dashboard.
        self.invalidate_cache()

    async def reprice_unpriced_history(self, match_tail: str,
                                       rate_for) -> dict[str, float]:
        """Retroactive pricing true-up.

        When a model is priced only AFTER it served traffic, its logged rows
        carry cost ≈ 0 (the cost engine prices unpriced models at $0). This
        recomputes the cost of rows that were logged unpriced and returns the
        per-key spend delta so the caller can true up virtual-key budgets.

        Row matching uses the recorded attempts JSON: an attempt deployment is
        ``"<group>/<model_id>"`` and the cost engine's lookup key is
        ``"<provider_type>/<model_id>"``. *match_tail* (the bare model id) is
        matched against the SERVING attempt's "/"-boundary suffix — the same
        tail rule ``CostEngine._lookup`` applies, so "openai/retro-gpt" matches
        "retro-gpt" but "gpt-4o-mini" never matches "gpt-4o". The serving
        attempt is the last one with a 2xx status (the deployment whose
        response produced the row's usage); earlier failed attempts on other
        models are ignored, and rows with no successful attempt fall back to
        their last attempt.

        *rate_for* is called with the serving attempt's provider name (the
        account, e.g. ``"openai-main"``) and returns that provider's effective
        rate dict, or None to skip the row. Passing a callable rather than one
        fixed entry is what lets a row be priced at ITS OWN provider's scoped
        rate: two rows on the same model served by two accounts get two prices.
        This module stays free of any cost/router import that way.

        Only unpriced rows are repriced — rows with cost > 0 were logged at an
        already-known rate and keep their historical value (a later rate EDIT
        must not rewrite history or double-charge).

        Returns ``{key_id: spend_delta}`` summed across the updated rows.
        """
        key_deltas: dict[str, float] = {}
        batch = 500
        last_id = -1  # keyset pagination — immune to offset drift/loops
        while True:
            async with self.engine.connect() as conn:
                rows = (await conn.execute(sa.text(
                    "SELECT id, key_id, tok_in, tok_cached, tok_cache_creation,"
                    " tok_out, cost, attempts FROM request_logs"
                    " WHERE cost = 0 AND id > :last ORDER BY id LIMIT :b"),
                    {"last": last_id, "b": batch})).all()
            if not rows:
                break
            last_id = rows[-1][0]
            updates: list[dict] = []
            for rid, key_id, tok_in, tok_cached, tok_cc, tok_out, cost, attempts_json in rows:
                serving = self._serving_attempt(attempts_json, match_tail)
                if serving is None:
                    continue
                # Price the row at the rate of the provider that actually
                # served it, so a scoped price applies to its own rows only.
                entry = rate_for(serving.get("provider") or "")
                if not entry:
                    continue
                per_token_in = entry["input_cost_per_token"]
                per_token_out = entry["output_cost_per_token"]
                per_token_cached = entry.get("cache_read_input_cost_per_token",
                                             per_token_in)
                per_token_cache_creation = entry.get(
                    "cache_creation_input_cost_per_token", per_token_in)
                uncached_prompt = max(0, tok_in - tok_cached)
                new_cost = round(
                    uncached_prompt * per_token_in
                    + tok_cached * per_token_cached
                    + tok_cc * per_token_cache_creation
                    + tok_out * per_token_out, 8)
                if new_cost <= 0:
                    continue
                updates.append({"id": rid, "c": new_cost})
                if key_id:
                    key_deltas[key_id] = (key_deltas.get(key_id, 0.0)
                                          + new_cost - cost)
            if updates:
                async with self.engine.begin() as conn:
                    await conn.execute(sa.text(
                        "UPDATE request_logs SET cost = :c WHERE id = :id"
                        " AND cost = 0"), updates)
                # Same reason as the rollup pass below: cached reads must not
                # keep serving the pre-reprice cost.
                self.invalidate_cache()

        # Rolled-up rows carry the same unpriced traffic in aggregate form.
        # Without this pass a price added later would only correct the rows
        # still in request_logs, so a key whose history had been rolled up
        # silently under-charged — the raw rows are gone by then.
        rollup_deltas = await self._reprice_rolled_up(match_tail, rate_for)
        for key_id, delta in rollup_deltas.items():
            key_deltas[key_id] = key_deltas.get(key_id, 0.0) + delta
        return key_deltas

    async def _reprice_rolled_up(self, match_tail: str,
                                 rate_for) -> dict[str, float]:
        """Reprice ``request_rollups`` rows whose model now has a price.

        A rollup row is unpriced when it still holds ``unpriced_requests`` with
        token counts — those columns are populated at rollup time from rows
        whose cost was 0, and are zeroed once priced. The model is identified by
        ``serving_model``, so the match is exact rather than a suffix guess.

        Returns ``{key_id: spend_delta}``. Idempotent: the token columns are
        zeroed as the cost is written, so a second call finds nothing to do.
        """
        key_deltas: dict[str, float] = {}
        # A rollup row is repriced when the priced key is the served model id or
        # any of its slash-tails (the CostEngine._lookup convention), so
        # pricing "claude-sonnet-4" still matches "anthropic/claude-sonnet-4".
        # The comparison is on WHOLE remaining path segments: the previous
        # exact match required the caller to pre-truncate the key to its last
        # segment, which made two models sharing that segment indistinguishable
        # and billed one at the other's rate (round 65).
        like = "%/" + (match_tail.replace("\\", "\\\\")
                       .replace("%", "\\%").replace("_", "\\_"))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(sa.text("""
                SELECT id, key_id, provider, serving_model, unpriced_requests,
                       unpriced_tok_in, unpriced_tok_cached,
                       unpriced_tok_cache_creation, unpriced_tok_out
                FROM request_rollups
                WHERE unpriced_requests > 0
                  AND (serving_model = :tail
                       OR serving_model LIKE :like ESCAPE '\\')
            """), {"tail": match_tail, "like": like})).all()
            # The LIKE is a prefilter (it cannot express the segment
            # boundary); the tail rule decides, exactly as in the cost engine.
            rows = [r for r in rows
                    if match_tail in {"/".join((r.serving_model or "").split("/")[i:])
                                      for i in range(len((r.serving_model or "").split("/")))}]
        if not rows:
            return key_deltas

        updates: list[dict] = []
        for r in rows:
            entry = rate_for(r.provider or "")
            if not entry:
                continue
            per_token_in = entry["input_cost_per_token"]
            per_token_out = entry["output_cost_per_token"]
            per_token_cached = entry.get("cache_read_input_cost_per_token",
                                         per_token_in)
            per_token_cc = entry.get("cache_creation_input_cost_per_token",
                                     per_token_in)
            uncached_prompt = max(0, r.unpriced_tok_in - r.unpriced_tok_cached)
            new_cost = round(
                uncached_prompt * per_token_in
                + r.unpriced_tok_cached * per_token_cached
                + r.unpriced_tok_cache_creation * per_token_cc
                + r.unpriced_tok_out * per_token_out, 8)
            if new_cost <= 0:
                continue
            updates.append({"id": r.id, "c": new_cost, "n": r.unpriced_requests})
            if r.key_id:
                key_deltas[r.key_id] = key_deltas.get(r.key_id, 0.0) + new_cost

        if updates:
            async with self.engine.begin() as conn:
                # Add the cost and clear the unpriced counters in one statement
                # per row, so a concurrent reader never sees the cost applied
                # twice.
                for u in updates:
                    await conn.execute(sa.text(
                        "UPDATE request_rollups SET cost = cost + :c,"
                        " unpriced_requests = 0, unpriced_tok_in = 0,"
                        " unpriced_tok_cached = 0,"
                        " unpriced_tok_cache_creation = 0,"
                        " unpriced_tok_out = 0"
                        " WHERE id = :id AND unpriced_requests > 0"), u)
            # The cost just changed under any cached overview/timeseries read;
            # without this the dashboard keeps serving the pre-reprice numbers
            # for the TTL and the true-up looks like it did nothing.
            self.invalidate_cache()
        return key_deltas

    @staticmethod
    def _is_2xx(status) -> bool:
        """True when an attempt's recorded status means the upstream answered.

        The gateway records ``AttemptRecord.status`` as a *string* — "ok",
        "ok_after_refresh", "http_429", "TimeoutException", "encode_error" —
        and serializes it verbatim. Only the integer form (used by hand-built
        fixtures) was recognised before, so the 2xx rule never fired on real
        data. ``bool`` is an ``int`` subclass, so it is excluded explicitly.
        """
        if isinstance(status, bool):
            return False
        if isinstance(status, int):
            return 200 <= status < 300
        return status in ("ok", "ok_after_refresh")

    @staticmethod
    def _model_id_from_attempt(serving: dict, group: str | None) -> str:
        """The provider-native model id an attempt was sent to.

        Prefers the ``model_id`` the attempt recorded. For rows written before
        that field existed, recovers it from the ``"<group>/<model_id>"``
        deployment string by stripping the row's own group prefix — exact, and
        correct even when the model id contains "/". Falls back to the last
        path segment only when the group is unknown, which is the lossy case
        that conflated ``stealth/ox-alpha`` with ``vendor/ox-alpha``
        (round 65).
        """
        recorded = serving.get("model_id")
        if isinstance(recorded, str) and recorded:
            return recorded
        dep = serving.get("deployment") or ""
        if not isinstance(dep, str) or not dep:
            return ""
        if group and dep.startswith(f"{group}/"):
            return dep[len(group) + 1:]
        return dep.split("/")[-1]

    @staticmethod
    def _serving_attempt(attempts_json: str | None,
                         match_tail: str | None) -> dict | None:
        """The attempt whose response produced the row's usage, or None.

        Attempts store ``"<group>/<model_id>"`` plus the status; the serving
        attempt is the LAST 2xx entry (the deployment whose response produced
        the row's usage and cost). Last, not first: a 200 whose body fails to
        decode is recorded "ok" and then retried, so a later attempt is the one
        that actually delivered. The model match must be a full path segment
        (boundary "/"), never a bare string suffix. Rows with no successful
        attempt (pure failures) fall back to the last attempt.

        ``match_tail=None`` skips the model test and returns the serving
        attempt whatever model it names — the rollup uses that to learn which
        model a row was served by.
        """
        if not attempts_json:
            return None
        try:
            attempts = orjson.loads(attempts_json)
        except Exception:  # noqa: BLE001 — malformed rows just don't match
            return None
        if not isinstance(attempts, list) or not attempts:
            return None
        entries = [a if isinstance(a, dict) else {}
                   for a in attempts]
        serving = next((a for a in reversed(entries)
                        if DBSink._is_2xx(a.get("status"))), entries[-1])
        if match_tail is None:
            return serving
        # Match on the model id the attempt recorded. The deployment string is
        # "<group>/<model_id>" and both halves may contain "/", so
        # `endswith("/" + tail)` cannot tell `stealth/ox-alpha` from
        # `vendor/ox-alpha` when the tail is the truncated last segment — the
        # collision that mispriced one model's history at another's rate
        # (round 65). The suffix rule survives only as a fallback for rows
        # logged before the field existed.
        recorded = serving.get("model_id")
        if isinstance(recorded, str) and recorded:
            # Mirror CostEngine._lookup: a price registered under any
            # slash-tail of the served model id applies to it, so pricing
            # "claude-sonnet-4" still matches a row served by
            # "anthropic/claude-sonnet-4". Crucially this compares the
            # *whole* remaining path, so "vendor/ox-alpha" no longer matches a
            # row served by "stealth/ox-alpha" (round 65).
            parts = recorded.split("/")
            candidates = {"/".join(parts[i:]) for i in range(len(parts))}
            return serving if match_tail in candidates else None
        dep = serving.get("deployment", "")
        if not (isinstance(dep, str) and dep.endswith(f"/{match_tail}")):
            return None
        return serving

    async def write_audit(self, evt: LogEvent) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("INSERT INTO audit_logs (ts, actor, action, target, diff)"
                        " VALUES (:ts, :actor, :action, :target, :diff)"),
                {"ts": evt.ts, "actor": evt.actor, "action": evt.action,
                 "target": evt.target, "diff": orjson.dumps(evt.diff).decode()},
            )
        self.invalidate_cache()

    async def read_audit(self, limit: int = 200) -> list[dict]:
        """Newest-first ``audit_logs`` rows shaped like ``public_dict(LogEvent)``.

        The audit trail's only reader. ``log_audit`` writes each admin mutation
        to this table AND to the audit SSE ring; without this accessor both
        copies accumulated unread (the ring is capped, the table grew forever).

        Same row shape and ordering contract as :meth:`read_requests` —
        ``ts DESC`` with an ``id DESC`` tiebreak, so several mutations in one
        second still come back deterministically. Keys match
        ``public_dict(LogEvent)`` (request-only fields at their empty defaults)
        plus ``diff``, which ``public_dict`` strips because it is noise on the
        request/proxy streams but is the entire payload of an audit row.

        ``Cache-Control: no-store`` on the response is the caller's job.
        """
        ckey = ("read_audit", int(limit))
        cached = self._cache_get(ckey)
        if cached is not None:
            return cached
        rows = await self._read_audit_uncached(limit)
        if rows:
            self._cache_put(ckey, rows)
        return rows

    async def _read_audit_uncached(self, limit: int) -> list[dict]:
        # `id` orders the result (a tiebreak for same-second mutations) but is
        # not part of the row contract: read_requests/public_dict expose no id,
        # so neither does this.
        cols = ("ts", "actor", "action", "target", "diff")
        async with self.engine.connect() as conn:
            result = (await conn.execute(sa.text(
                f"SELECT {', '.join(cols)} FROM audit_logs"
                " ORDER BY ts DESC, id DESC LIMIT :l"), {"l": int(limit)})).all()
        out: list[dict] = []
        for r in result:
            d = dict(zip(cols, r))
            raw = d.pop("diff", None)
            try:
                diff = orjson.loads(raw) if raw else {}
            except orjson.JSONDecodeError:
                # A malformed diff must not hide the audit row itself.
                diff = {}
            d["stream"] = "audit"
            # Same key set as public_dict(LogEvent) so the admin UI can render
            # DB-backed and ring-backed entries with one code path.
            d.update({"request_id": "", "surface": "", "key_alias": "",
                      "key_id": "", "model_group": "", "provider": "",
                      "provider_key_label": "", "status": 200, "error_code": "",
                      "tok_in": 0, "tok_cached": 0, "tok_cache_creation": 0,
                      "tok_reasoning": 0, "tok_out": 0, "usage_estimated": False,
                      "tps": 0.0, "ttft_ms": 0.0, "latency_ms": 0.0, "cost": 0.0,
                      "was_stream": False, "cache_hit": False,
                      "cache_savings": 0.0, "response_cache_hit": False,
                      "attempts": [], "request_body": None, "response_body": None,
                      "level": "info", "message": "", "diff": diff})
            out.append(d)
        return out

    async def read_requests(self, limit: int = 200,
                            key_ids: list[str] | None = None) -> list[dict]:
        """Newest-first rows shaped like public_dict(LogEvent) so the admin UI
        can treat ring-backed and DB-backed entries identically.

        Ordered by ts DESC (id DESC tiebreak) so the result is deterministic by
        event time regardless of insertion order — the frontend renders this
        array directly without re-sorting.

        *key_ids* scoping semantics:
        - ``None`` → admin / unfiltered (all rows).
        - non-empty list → rows whose ``key_id`` is in the list (per-user).
        - empty list ``[]`` → this caller owns no keys → return [] with no
          DB query. (An empty list is NOT "no filter": it is the normal
          first-visit state for a user who signed up but generated no key,
          and treating it as unfiltered leaks every other user's rows.)
        """
        if key_ids is not None and not key_ids:
            return []
        ckey = ("read_requests", int(limit), tuple(key_ids) if key_ids else None)
        cached = self._cache_get(ckey)
        if cached is not None:
            return cached
        result = await self._read_requests_uncached(limit, key_ids)
        if result:
            self._cache_put(ckey, result)
        return result

    async def _read_requests_uncached(self, limit: int,
                                      key_ids: list[str] | None) -> list[dict]:
        if key_ids is not None and not key_ids:
            return []
        cols = ", ".join(_COLS)
        params: dict = {"l": int(limit)}
        where = ""
        if key_ids:
            where = "WHERE key_id IN :kids"
            params["kids"] = key_ids
        stmt = sa.text(f"SELECT {cols} FROM request_logs {where}"
                       " ORDER BY ts DESC, id DESC LIMIT :l")
        if key_ids:
            stmt = stmt.bindparams(sa.bindparam("kids", expanding=True))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(stmt, params)).all()
        out: list[dict] = []
        for r in rows:
            d = dict(zip(_COLS, r))
            d["stream"] = "request"
            d["level"] = ""
            d["message"] = ""
            d["actor"] = ""
            d["action"] = ""
            d["target"] = ""
            d["diff"] = {}
            d["was_stream"] = bool(d["was_stream"])
            d["cache_hit"] = bool(d["cache_hit"])
            d["attempts"] = orjson.loads(d["attempts"])
            rb = d.get("request_body")
            d["request_body"] = orjson.loads(rb) if rb else None
            rsb = d.get("response_body")
            d["response_body"] = orjson.loads(rsb) if rsb else None
            out.append(d)
        return out

    async def read_overview(self, minutes: int,
                            key_ids: list[str] | None = None) -> dict:
        """DB-backed overview with the same dict shape as stats.overview().

        minutes == 0 means all-time (no ts cutoff).

        *key_ids* scoping semantics (see ``read_requests``):
        - ``None`` → admin / unfiltered.
        - non-empty list → aggregates restricted to those ``key_id``s.
        - empty list ``[]`` → caller owns no keys → return the zero-aggregate
          overview shape (the same dict this method returns when the matching
          row set is empty) with no DB query. Not "no filter": an empty list
          is the normal first-visit state for a user without a key, and
          treating it as unfiltered leaks every other user's aggregates.
        """
        now = time.time()
        if key_ids is not None and not key_ids:
            # Zero-row overview: same shape the aggregate below returns when
            # no rows match, so the frontend's empty state works unchanged.
            return {
                "window_minutes": minutes,
                "generated_at": now,
                "requests": 0,
                "errors": 0,
                "error_rate": 0.0,
                "requests_per_minute": 0.0,
                "tok_in": 0,
                "tok_cached": 0,
                "tok_cache_creation": 0,
                "tok_reasoning": 0,
                "tok_out": 0,
                "estimated_requests": 0,
                "cache_hits": 0,
                "cache_hit_rate": 0.0,
                "tps_avg": 0.0,
                "tps_p95": 0.0,
                "ttft_p95_ms": 0.0,
                "latency_p95_ms": 0.0,
                "cost": 0.0,
                "cache_savings": 0.0,
            }
        ckey = ("read_overview", int(minutes), tuple(key_ids) if key_ids else None)
        cached = self._cache_get(ckey)
        if cached is not None:
            return cached
        result = await self._read_overview_uncached(minutes, key_ids)
        if result.get("requests"):
            self._cache_put(ckey, result)
        return result

    async def _read_overview_uncached(self, minutes: int,
                                      key_ids: list[str] | None) -> dict:
        now = time.time()
        cutoff = now - minutes * 60 if minutes > 0 else 0.0
        params: dict = {}
        if minutes > 0:
            params["cutoff"] = cutoff

        # key_id IN :kids clause — applied to the aggregate WHERE and to every
        # p95 sample subquery.  When key_ids is None/empty, no filtering.
        key_filter = bool(key_ids)
        if key_filter:
            params["kids"] = key_ids
        # For the aggregate, which may have no ts WHERE at all (all-time):
        if minutes > 0:
            where_clause = "WHERE ts >= :cutoff" + (" AND key_id IN :kids" if key_filter else "")
        else:
            where_clause = "WHERE key_id IN :kids" if key_filter else ""
        # For sample subqueries, which always begin "WHERE <col> > 0":
        key_and = " AND key_id IN :kids" if key_filter else ""

        async with self.engine.connect() as conn:
            agg_stmt = (sa.text(f"""
                SELECT COUNT(*) AS requests,
                       SUM(CASE WHEN status >= 400 OR error_code != '' THEN 1 ELSE 0 END) AS errors,
                       COALESCE(SUM(tok_in), 0) AS tok_in,
                       COALESCE(SUM(tok_cached), 0) AS tok_cached,
                       COALESCE(SUM(tok_cache_creation), 0) AS tok_cache_creation,
                       COALESCE(SUM(tok_reasoning), 0) AS tok_reasoning,
                       COALESCE(SUM(tok_out), 0) AS tok_out,
                       SUM(CASE WHEN usage_estimated = 1 THEN 1 ELSE 0 END) AS estimated_requests,
                       SUM(CASE WHEN cache_hit = 1 OR tok_cached > 0 THEN 1 ELSE 0 END) AS cache_hits,
                       COALESCE(SUM(cost), 0) AS cost,
                       COALESCE(SUM(cache_savings), 0) AS cache_savings
                FROM request_logs
                {where_clause}
            """))
            if key_filter:
                agg_stmt = agg_stmt.bindparams(sa.bindparam("kids", expanding=True))
            row = (await conn.execute(agg_stmt, params)).one()

            requests = row.requests or 0
            errors = row.errors or 0
            cache_hits = row.cache_hits or 0

            # Rows already rolled up and deleted must still count: without this
            # the dashboard would silently shrink every time the cap pruned.
            roll = await self._rollup_overview(conn, minutes, key_ids)
            requests += roll["requests"]
            errors += roll["errors"]
            cache_hits += roll["cache_hits"]

            # p95 from bounded sample (max 5000 rows) to avoid full-table scan.
            # Each sample subquery always has a WHERE (<col> > 0), so the
            # key_id filter is appended as another AND term.
            def _sample_stmt(col: str) -> str:
                where = f"WHERE {col} > 0"
                if minutes > 0:
                    where += " AND ts >= :cutoff"
                where += key_and
                return f"SELECT {col} FROM request_logs {where} ORDER BY id DESC LIMIT 5000"

            tps_stmt = sa.text(_sample_stmt("tps"))
            ttft_stmt = sa.text(_sample_stmt("ttft_ms"))
            lat_stmt = sa.text(_sample_stmt("latency_ms"))
            if key_filter:
                tps_stmt = tps_stmt.bindparams(sa.bindparam("kids", expanding=True))
                ttft_stmt = ttft_stmt.bindparams(sa.bindparam("kids", expanding=True))
                lat_stmt = lat_stmt.bindparams(sa.bindparam("kids", expanding=True))
            tps_rows = (await conn.execute(tps_stmt, params)).all()
            tps_values = [r[0] for r in tps_rows]
            ttft_rows = (await conn.execute(ttft_stmt, params)).all()
            ttft_values = [r[0] for r in ttft_rows]
            lat_rows = (await conn.execute(lat_stmt, params)).all()
            lat_values = [r[0] for r in lat_rows]

        minutes_norm = max(minutes, 1e-9)
        # Rolled-up buckets contribute their exact sample sum/count for the
        # mean and their stored p95 for the percentile. Mixing raw samples and
        # bucket-level p95s is an approximation (see rollup_and_prune); it is
        # exact when the window is entirely rolled up or entirely raw.
        tps_sum = sum(tps_values) + roll["tps_sum"]
        tps_n = len(tps_values) + roll["tps_count"]
        return {
            "window_minutes": minutes,
            "generated_at": now,
            "requests": requests,
            "errors": errors,
            "error_rate": round(errors / requests, 4) if requests else 0.0,
            "requests_per_minute": round(requests / minutes_norm, 2) if minutes > 0 else 0.0,
            "tok_in": (row.tok_in or 0) + roll["tok_in"],
            "tok_cached": (row.tok_cached or 0) + roll["tok_cached"],
            "tok_cache_creation": (row.tok_cache_creation or 0) + roll["tok_cache_creation"],
            "tok_reasoning": (row.tok_reasoning or 0) + roll["tok_reasoning"],
            "tok_out": (row.tok_out or 0) + roll["tok_out"],
            "estimated_requests": (row.estimated_requests or 0) + roll["estimated_requests"],
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / requests, 4) if requests else 0.0,
            "tps_avg": round(tps_sum / tps_n, 2) if tps_n else 0.0,
            "tps_p95": round(_merge_p95(tps_values, roll["tps_p95_pairs"]), 2),
            "ttft_p95_ms": round(_merge_p95(ttft_values, roll["ttft_p95_pairs"]), 1),
            "latency_p95_ms": round(_merge_p95(lat_values, roll["latency_p95_pairs"]), 1),
            "cost": round((row.cost or 0) + roll["cost"], 6),
            "cache_savings": round((row.cache_savings or 0) + roll["cache_savings"], 6),
        }

    async def _rollup_overview(self, conn, minutes: int,
                               key_ids: list[str] | None) -> dict:
        """Aggregate ``request_rollups`` over the same window as the raw read.

        Returns the additive totals plus the ``(p95, sample_count)`` pairs each
        percentile needs, so the caller can merge them with the raw samples.
        """
        params: dict = {}
        where = ""
        if minutes > 0:
            params["cutoff"] = time.time() - minutes * 60
            where = "WHERE bucket_ts >= :cutoff"
        if key_ids:
            params["kids"] = key_ids
            where += (" AND key_id IN :kids" if where else "WHERE key_id IN :kids")
        stmt = sa.text(f"""
            SELECT COALESCE(SUM(requests), 0) AS requests,
                   COALESCE(SUM(errors), 0) AS errors,
                   COALESCE(SUM(estimated_requests), 0) AS estimated_requests,
                   COALESCE(SUM(cache_hits), 0) AS cache_hits,
                   COALESCE(SUM(tok_in), 0) AS tok_in,
                   COALESCE(SUM(tok_cached), 0) AS tok_cached,
                   COALESCE(SUM(tok_cache_creation), 0) AS tok_cache_creation,
                   COALESCE(SUM(tok_reasoning), 0) AS tok_reasoning,
                   COALESCE(SUM(tok_out), 0) AS tok_out,
                   COALESCE(SUM(cost), 0) AS cost,
                   COALESCE(SUM(cache_savings), 0) AS cache_savings,
                   COALESCE(SUM(tps_sum), 0) AS tps_sum,
                   COALESCE(SUM(tps_count), 0) AS tps_count
            FROM request_rollups
            {where}
        """)
        if key_ids:
            stmt = stmt.bindparams(sa.bindparam("kids", expanding=True))
        agg = (await conn.execute(stmt, params)).one()

        pairs: dict[str, list[tuple[float, int]]] = {
            "tps_p95_pairs": [], "ttft_p95_pairs": [], "latency_p95_pairs": []}
        for col, out_key in (("tps_p95", "tps_p95_pairs"),
                             ("ttft_p95_ms", "ttft_p95_pairs"),
                             ("latency_p95_ms", "latency_p95_pairs")):
            # Weight by the bucket's sample count so a dense hour counts more
            # than a sparse one. tps_count covers the tps column; the latency
            # columns have no per-column count, so requests is the weight.
            weight_col = "tps_count" if col == "tps_p95" else "requests"
            pstmt = sa.text(f"""
                SELECT {col} AS p, {weight_col} AS n FROM request_rollups
                {where} AND {col} > 0
            """ if where else f"""
                SELECT {col} AS p, {weight_col} AS n FROM request_rollups
                WHERE {col} > 0
            """)
            if key_ids:
                pstmt = pstmt.bindparams(sa.bindparam("kids", expanding=True))
            for r in (await conn.execute(pstmt, params)).all():
                pairs[out_key].append((r.p, r.n or 0))

        return {
            "requests": agg.requests or 0, "errors": agg.errors or 0,
            "estimated_requests": agg.estimated_requests or 0,
            "cache_hits": agg.cache_hits or 0,
            "tok_in": agg.tok_in or 0, "tok_cached": agg.tok_cached or 0,
            "tok_cache_creation": agg.tok_cache_creation or 0,
            "tok_reasoning": agg.tok_reasoning or 0, "tok_out": agg.tok_out or 0,
            "cost": agg.cost or 0, "cache_savings": agg.cache_savings or 0,
            "tps_sum": agg.tps_sum or 0, "tps_count": agg.tps_count or 0,
            **pairs,
        }

    async def read_timeseries(self, bucket_seconds: int, metric: str,
                              minutes: int, key_ids: list[str] | None = None) -> dict:
        """DB-backed timeseries with the same dict shape as stats.timeseries().

        minutes == 0 means all-time (no ts cutoff).
        For metric="tps", tps_p95 is approximated as max(tps) in the bucket.

        *key_ids* scoping semantics (see ``read_requests``):
        - ``None`` → admin / unfiltered.
        - non-empty list → buckets restricted to those ``key_id``s.
        - empty list ``[]`` → caller owns no keys → return the empty-buckets
          shape (the same dict this method returns when no rows match) with
          no DB query. Not "no filter": an empty list is the normal first-visit
          state for a user without a key, and treating it as unfiltered leaks
          every other user's timeseries buckets.
        """
        if metric not in VALID_METRICS:
            raise ValueError(f"unsupported metric {metric!r}")
        if key_ids is not None and not key_ids:
            # Zero-row timeseries: match the bucket loop below exactly. A
            # bounded window (minutes > 0) always zero-fills the fixed grid,
            # so it must do so here too — returning [] while the no-rows path
            # returns n_buckets broke the shape contract for the first-visit
            # state (AUDIT #86).
            return self._read_timeseries_empty(bucket_seconds, metric, minutes)
        ckey = ("read_timeseries", int(bucket_seconds), metric, int(minutes),
                tuple(key_ids) if key_ids else None)
        cached = self._cache_get(ckey)
        if cached is not None:
            return cached
        result = await self._read_timeseries_uncached(bucket_seconds, metric,
                                                       minutes, key_ids)
        if result.get("buckets"):
            self._cache_put(ckey, result)
        return result

    @staticmethod
    def _read_timeseries_empty(bucket_seconds: int, metric: str,
                               minutes: int) -> dict:
        """Zero-row timeseries with the same bucket grid as the DB path.

        Mirrors the zero-fill logic in :meth:`_read_timeseries_uncached` so a
        caller with no key scope (``key_ids=[]``) sees the same bucket count as
        the all-admin path with no matching rows (AUDIT #86).
        """
        n_fill = max(1, minutes * 60 // bucket_seconds) if minutes > 0 else 0
        bucket_start = int(time.time() // bucket_seconds) * bucket_seconds
        if minutes > 0:
            bucket_start = bucket_start - (n_fill - 1) * bucket_seconds
        if metric == "tokens":
            buckets = [
                {"t": bucket_start + i * bucket_seconds, "tok_in": 0,
                 "tok_cached": 0, "tok_cache_creation": 0,
                 "tok_reasoning": 0, "tok_out": 0}
                for i in range(n_fill)
            ]
        else:
            buckets = [
                {"t": bucket_start + i * bucket_seconds,
                 "tps_avg": 0.0, "tps_p95": 0.0}
                for i in range(n_fill)
            ]
        return {"bucket_seconds": bucket_seconds, "metric": metric, "buckets": buckets}

    async def _read_timeseries_uncached(self, bucket_seconds: int, metric: str,
                                        minutes: int,
                                        key_ids: list[str] | None) -> dict:
        now = time.time()
        params: dict = {}
        where_ts = ""
        if minutes > 0:
            cutoff = now - minutes * 60
            params["cutoff"] = cutoff
            where_ts = "WHERE ts >= :cutoff"

        # key_id IN :kids clause appended to the bucket aggregate.  When
        # key_ids is None/empty, no filtering.
        key_filter = bool(key_ids)
        if key_filter:
            params["kids"] = key_ids
            if where_ts:
                where_ts += " AND key_id IN :kids"
            else:
                where_ts = "WHERE key_id IN :kids"

        # bucket_start aligns the in-memory n_buckets grid; the SQL bucket
        # boundary is computed from each event's own ts using FLOOR() which
        # truncates toward negative infinity (correct for all ts values).
        bucket_start = int(now // bucket_seconds) * bucket_seconds
        if minutes > 0:
            n_buckets = max(1, minutes * 60 // bucket_seconds)
            bucket_start = bucket_start - (n_buckets - 1) * bucket_seconds

        async with self.engine.connect() as conn:
            ts_stmt = (sa.text(f"""
                SELECT FLOOR(ts / :bs) * :bs AS bucket_t,
                       SUM(tok_in) AS tok_in,
                       SUM(tok_cached) AS tok_cached,
                       SUM(tok_cache_creation) AS tok_cache_creation,
                       SUM(tok_reasoning) AS tok_reasoning,
                       SUM(tok_out) AS tok_out,
                       SUM(CASE WHEN tps > 0 THEN tps ELSE 0 END) AS tps_sum,
                       COUNT(CASE WHEN tps > 0 THEN 1 END) AS tps_count,
                       MAX(CASE WHEN tps > 0 THEN tps ELSE 0 END) AS tps_max
                FROM request_logs
                {where_ts}
                GROUP BY bucket_t
                ORDER BY bucket_t
            """))
            if key_filter:
                ts_stmt = ts_stmt.bindparams(sa.bindparam("kids", expanding=True))
            rows = (await conn.execute(ts_stmt, {**params, "bs": bucket_seconds})).all()

            # Rolled-up buckets cover rows the cap already deleted. Re-bucket
            # them onto the same grid so a chart over a long window keeps its
            # history instead of collapsing to whatever raw rows survived.
            # Rollup rows are hourly; on a finer grid (bucket_seconds < 3600)
            # an hour lands in the bucket its start falls in, so the series
            # total stays exact while the sub-hour placement is approximate.
            roll_where = ""
            if minutes > 0:
                roll_where = "WHERE bucket_ts >= :cutoff"
            if key_filter:
                roll_where += (" AND key_id IN :kids" if roll_where
                               else "WHERE key_id IN :kids")
            roll_stmt = sa.text(f"""
                SELECT FLOOR(bucket_ts / :bs) * :bs AS bucket_t,
                       SUM(tok_in) AS tok_in,
                       SUM(tok_cached) AS tok_cached,
                       SUM(tok_cache_creation) AS tok_cache_creation,
                       SUM(tok_reasoning) AS tok_reasoning,
                       SUM(tok_out) AS tok_out,
                       SUM(tps_sum) AS tps_sum,
                       SUM(tps_count) AS tps_count,
                       MAX(tps_p95) AS tps_max
                FROM request_rollups
                {roll_where}
                GROUP BY bucket_t
                ORDER BY bucket_t
            """)
            if key_filter:
                roll_stmt = roll_stmt.bindparams(sa.bindparam("kids", expanding=True))
            roll_rows = (await conn.execute(
                roll_stmt, {**params, "bs": bucket_seconds})).all()

        # GROUP BY skips empty buckets; zero-fill to a dense array so the
        # chart has no gaps (matching the in-memory stats.timeseries() shape).
        # Bounded windows (minutes > 0) always zero-fill the fixed grid — even
        # with zero rows — so the shape contract (exactly n_buckets buckets)
        # holds right after a restart when the DB path serves small windows.
        #
        # Rolled-up rows are summed into the same buckets as the raw ones, so
        # a chart spanning both regions (recent raw + older rolled up) shows
        # the true total rather than only the surviving raw rows.
        by_t: dict[int, object] = {int(r.bucket_t): r for r in rows}
        for rr in roll_rows:
            t = int(rr.bucket_t)
            raw = by_t.get(t)
            if raw is None:
                by_t[t] = rr
                continue
            by_t[t] = _BucketSum(raw, rr)
        if minutes > 0:
            first_t = bucket_start
            n_fill = n_buckets
        elif by_t:
            # All-time: span the oldest..newest populated buckets.
            first_t = min(by_t)
            n_fill = (max(by_t) - first_t) // bucket_seconds + 1
        else:
            n_fill = 0
        for i in range(n_fill):
            t = first_t + i * bucket_seconds
            if t not in by_t:
                by_t[t] = None  # placeholder for a zero-valued bucket

        if metric == "tokens":
            buckets = [
                {"t": t, "tok_in": getattr(r, "tok_in", 0) or 0,
                 "tok_cached": getattr(r, "tok_cached", 0) or 0,
                 "tok_cache_creation": getattr(r, "tok_cache_creation", 0) or 0,
                 "tok_reasoning": getattr(r, "tok_reasoning", 0) or 0,
                 "tok_out": getattr(r, "tok_out", 0) or 0}
                for t, r in sorted(by_t.items())
            ]
        else:
            buckets = [
                {"t": t,
                 "tps_avg": round(r.tps_sum / r.tps_count, 2) if r and r.tps_count else 0.0,
                 "tps_p95": round(r.tps_max or 0.0, 2) if r else 0.0}
                for t, r in sorted(by_t.items())
            ]
        return {"bucket_seconds": bucket_seconds, "metric": metric, "buckets": buckets}
