"""SQLite persistence for request + audit log streams (batched writes).

The logging subsystem's docstring promises "request -> DBSink(batched)";
this module is that sink. Audit events are written synchronously from the
admin mutation paths; request batches arrive from the log pump worker.
"""
from __future__ import annotations

import orjson
import sqlalchemy as sa

from wiwi.logging_core.events import LogEvent

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
  attempts TEXT DEFAULT '[]'
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
         "was_stream", "cache_hit", "cache_savings", "attempts")


class DBSink:
    def __init__(self, engine) -> None:
        self.engine = engine

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(REQUEST_DDL))
            await conn.execute(sa.text(AUDIT_DDL))

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
            out.append(d)
        return out
