"""SQLite persistence for request + audit log streams (batched writes).

The logging subsystem's docstring promises "request -> DBSink(batched)";
this module is that sink. Audit events are written synchronously from the
admin mutation paths; request batches arrive from the log pump worker.
"""
from __future__ import annotations

import time

import orjson
import sqlalchemy as sa

from wiwi.logging_core.events import LogEvent
from wiwi.server.stats import _p95

REQUEST_DDL = """
CREATE TABLE IF NOT EXISTS request_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  request_id TEXT DEFAULT '',
  surface TEXT DEFAULT '',
  key_alias TEXT DEFAULT '',
  model_group TEXT DEFAULT '',
  provider TEXT DEFAULT '',
  provider_key_label TEXT DEFAULT '',
  status INTEGER DEFAULT 200,
  error_code TEXT DEFAULT '',
  tok_in INTEGER DEFAULT 0,
  tok_cached INTEGER DEFAULT 0,
  tok_reasoning INTEGER DEFAULT 0,
  tok_out INTEGER DEFAULT 0,
  tps REAL DEFAULT 0,
  ttft_ms REAL DEFAULT 0,
  latency_ms REAL DEFAULT 0,
  cost REAL DEFAULT 0,
  was_stream INTEGER DEFAULT 0,
  cache_hit INTEGER DEFAULT 0,
  cache_savings REAL DEFAULT 0,
  attempts TEXT DEFAULT '[]',
  request_body TEXT,
  response_body TEXT
);
"""

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  actor TEXT DEFAULT '',
  action TEXT DEFAULT '',
  target TEXT DEFAULT '',
  diff TEXT DEFAULT '{}'
);
"""

_COLS = ("ts", "request_id", "surface", "key_alias", "model_group", "provider",
         "provider_key_label", "status", "error_code", "tok_in", "tok_cached",
         "tok_reasoning", "tok_out", "tps", "ttft_ms", "latency_ms", "cost",
         "was_stream", "cache_hit", "cache_savings", "attempts",
         "request_body", "response_body")


class DBSink:
    def __init__(self, engine) -> None:
        self.engine = engine

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(REQUEST_DDL))
            await conn.execute(sa.text(AUDIT_DDL))
            # Migrate existing tables: add columns that may not exist yet.
            await self._migrate(conn)

    async def _migrate(self, conn) -> None:
        """Add columns introduced after the initial schema (idempotent)."""
        existing = {r[1] for r in (await conn.execute(
            sa.text("PRAGMA table_info(request_logs)"))).all()}
        for col, decl in [("request_body", "TEXT"), ("response_body", "TEXT")]:
            if col not in existing:
                await conn.execute(
                    sa.text(f"ALTER TABLE request_logs ADD COLUMN {col} {decl}"))

    @staticmethod
    def _row(evt: LogEvent) -> dict:
        return {
            "ts": evt.ts, "request_id": evt.request_id, "surface": evt.surface,
            "key_alias": evt.key_alias, "model_group": evt.model_group,
            "provider": evt.provider, "provider_key_label": evt.provider_key_label,
            "status": evt.status, "error_code": evt.error_code,
            "tok_in": evt.tok_in, "tok_cached": evt.tok_cached,
            "tok_reasoning": evt.tok_reasoning, "tok_out": evt.tok_out,
            "tps": evt.tps, "ttft_ms": evt.ttft_ms, "latency_ms": evt.latency_ms,
            "cost": evt.cost, "was_stream": int(evt.was_stream),
            "cache_hit": int(evt.cache_hit), "cache_savings": evt.cache_savings,
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

    async def write_audit(self, evt: LogEvent) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("INSERT INTO audit_logs (ts, actor, action, target, diff)"
                        " VALUES (:ts, :actor, :action, :target, :diff)"),
                {"ts": evt.ts, "actor": evt.actor, "action": evt.action,
                 "target": evt.target, "diff": orjson.dumps(evt.diff).decode()},
            )

    async def read_requests(self, limit: int = 200) -> list[dict]:
        """Newest-first rows shaped like public_dict(LogEvent) so the admin UI
        can treat ring-backed and DB-backed entries identically."""
        cols = ", ".join(_COLS)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                sa.text(f"SELECT {cols} FROM request_logs ORDER BY id DESC LIMIT :l"),
                {"l": int(limit)})).all()
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

    async def read_overview(self, minutes: int) -> dict:
        """DB-backed overview with the same dict shape as stats.overview().

        minutes == 0 means all-time (no ts cutoff).
        """
        now = time.time()
        cutoff = now - minutes * 60 if minutes > 0 else 0.0
        where_clause = "WHERE ts >= :cutoff" if minutes > 0 else ""
        params: dict = {}
        if minutes > 0:
            params["cutoff"] = cutoff

        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text(f"""
                SELECT COUNT(*) AS requests,
                       SUM(CASE WHEN status >= 400 OR error_code != '' THEN 1 ELSE 0 END) AS errors,
                       COALESCE(SUM(tok_in), 0) AS tok_in,
                       COALESCE(SUM(tok_cached), 0) AS tok_cached,
                       COALESCE(SUM(tok_reasoning), 0) AS tok_reasoning,
                       COALESCE(SUM(tok_out), 0) AS tok_out,
                       SUM(CASE WHEN cache_hit = 1 OR tok_cached > 0 THEN 1 ELSE 0 END) AS cache_hits,
                       COALESCE(SUM(cost), 0) AS cost,
                       COALESCE(SUM(cache_savings), 0) AS cache_savings
                FROM request_logs
                {where_clause}
            """), params)).one()

            requests = row.requests or 0
            errors = row.errors or 0
            cache_hits = row.cache_hits or 0

            # p95 from bounded sample (max 5000 rows) to avoid full-table scan
            sample_query = "SELECT tps FROM request_logs"
            sample_where = "WHERE tps > 0"
            if minutes > 0:
                sample_where += " AND ts >= :cutoff"
            sample_query = f"{sample_query} {sample_where} ORDER BY id DESC LIMIT 5000"
            tps_rows = (await conn.execute(sa.text(sample_query), params)).all()
            tps_values = [r[0] for r in tps_rows]

            ttft_query = "SELECT ttft_ms FROM request_logs"
            ttft_where = "WHERE ttft_ms > 0"
            if minutes > 0:
                ttft_where += " AND ts >= :cutoff"
            ttft_query = f"{ttft_query} {ttft_where} ORDER BY id DESC LIMIT 5000"
            ttft_rows = (await conn.execute(sa.text(ttft_query), params)).all()
            ttft_values = [r[0] for r in ttft_rows]

            lat_query = "SELECT latency_ms FROM request_logs"
            lat_where = "WHERE latency_ms > 0"
            if minutes > 0:
                lat_where += " AND ts >= :cutoff"
            lat_query = f"{lat_query} {lat_where} ORDER BY id DESC LIMIT 5000"
            lat_rows = (await conn.execute(sa.text(lat_query), params)).all()
            lat_values = [r[0] for r in lat_rows]

        minutes_norm = max(minutes, 1e-9)
        return {
            "window_minutes": minutes,
            "generated_at": now,
            "requests": requests,
            "errors": errors,
            "error_rate": round(errors / requests, 4) if requests else 0.0,
            "requests_per_minute": round(requests / minutes_norm, 2) if minutes > 0 else 0.0,
            "tok_in": row.tok_in or 0,
            "tok_cached": row.tok_cached or 0,
            "tok_reasoning": row.tok_reasoning or 0,
            "tok_out": row.tok_out or 0,
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / requests, 4) if requests else 0.0,
            "tps_avg": round(sum(tps_values) / len(tps_values), 2) if tps_values else 0.0,
            "tps_p95": round(_p95(tps_values), 2),
            "ttft_p95_ms": round(_p95(ttft_values), 1),
            "latency_p95_ms": round(_p95(lat_values), 1),
            "cost": round(row.cost or 0, 6),
            "cache_savings": round(row.cache_savings or 0, 6),
        }
