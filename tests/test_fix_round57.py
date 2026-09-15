"""Regression tests for the rollup-schema migration on existing databases.

The bug: when ``serving_model`` and the ``unpriced_*`` columns were added to
``request_rollups`` (commit "Carry the serving model and unpriced tokens into
the rollup"), ``_migrate`` was not extended to widen a pre-existing rollups
table. ``CREATE TABLE IF NOT EXISTS`` is a no-op on an existing table, so on
any database created by the previous version the new columns never appeared
and the recreated ``idx_rollup_unique`` (which now covers ``serving_model``)
failed with ``UndefinedColumnError`` — the server died in a startup loop.
"""
from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.logging_core.db_sink import (
    _AUDIT_DDL_SQLITE,
    _REQUEST_DDL_SQLITE,
    DBSink,
)

# The request_rollups shape BEFORE serving_model / unpriced_* were introduced
# (the four-dimension unique index era).
_OLD_ROLLUP_DDL = """
CREATE TABLE IF NOT EXISTS request_rollups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bucket_ts REAL NOT NULL,
  key_id TEXT DEFAULT '',
  model_group TEXT DEFAULT '',
  provider TEXT DEFAULT '',
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
  latency_p95_ms REAL DEFAULT 0
);
"""

# Columns added to request_rollups after its first release; all must be
# backfilled by _migrate on an existing table.
_NEW_ROLLUP_COLS = (
    "serving_model",
    "unpriced_requests",
    "unpriced_tok_in",
    "unpriced_tok_cached",
    "unpriced_tok_cache_creation",
    "unpriced_tok_out",
)


async def _old_shape_engine(tmp_path, name: str):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    async with eng.begin() as conn:
        await conn.execute(text(_REQUEST_DDL_SQLITE))
        await conn.execute(text(_AUDIT_DDL_SQLITE))
        await conn.execute(text(_OLD_ROLLUP_DDL))
    return eng


async def test_startup_migrates_existing_rollups_table(tmp_path):
    """startup() on a database with the old rollup shape must widen the
    table in place — not crash on the recreated unique index."""
    eng = await _old_shape_engine(tmp_path, "migrate.db")
    try:
        sink = DBSink(eng)
        await sink.startup()  # crashed with UndefinedColumnError before the fix

        async with eng.connect() as conn:
            cols = {r[1] for r in (await conn.execute(
                text("PRAGMA table_info(request_rollups)"))).all()}
        for col in _NEW_ROLLUP_COLS:
            assert col in cols, f"existing request_rollups was not widened: {col}"
    finally:
        await eng.dispose()


async def test_rollup_upsert_works_after_migration(tmp_path):
    """After migrating an old-shaped database, the rollup path must actually
    run: enforce the cap, land aggregates in request_rollups, and keep the
    dashboard numbers intact."""
    eng = await _old_shape_engine(tmp_path, "upsert.db")
    try:
        sink = DBSink(eng)
        await sink.startup()

        now = time.time()
        rows = [{
            "ts": now - (i * 3600), "request_id": f"r{i}", "key_id": f"k{i % 2}",
            "key_alias": f"a{i % 2}", "model_group": f"m{i % 2}",
            "provider": f"p{i % 2}", "provider_key_label": "L",
            "surface": "chat", "status": 200, "error_code": "",
            "tok_in": 100, "tok_out": 50, "tok_cached": 0, "tok_reasoning": 0,
            "tok_cache_creation": 0, "cost": 0.0 if i % 2 else 0.001,
            "cache_hit": 0, "cache_savings": 0.0, "tps": 40.0,
            "ttft_ms": 20.0, "latency_ms": 100.0, "was_stream": 1,
            "response_cache_hit": 0, "usage_estimated": 0,
            # a serving attempt so the rollup records a serving_model
            "attempts": '[{"deployment": "m0/retro-gpt", "provider": "p0",'
                        ' "status": "ok"}]' if i % 2 == 0 else "[]",
        } for i in range(20)]
        cols = ", ".join(rows[0].keys())
        ph = ", ".join(f":{k}" for k in rows[0])
        async with eng.begin() as conn:
            await conn.execute(
                text(f"INSERT INTO request_logs ({cols}) VALUES ({ph})"), rows)

        before = await sink.read_overview(0)
        assert before["requests"] == 20

        deleted = await sink.enforce_log_cap(5)
        assert deleted == 15, "the cap must delete the rows beyond it"
        async with eng.connect() as conn:
            count = (await conn.execute(
                text("SELECT COUNT(*) FROM request_rollups"))).scalar() or 0
            assert count > 0, "deleted rows must be rolled up, not dropped"
            smodels = {r[0] for r in (await conn.execute(
                text("SELECT DISTINCT serving_model FROM request_rollups"))).all()}
        assert "retro-gpt" in smodels, "serving_model must be recorded per rollup row"

        after = await sink.read_overview(0)
        assert after["requests"] == before["requests"]
        assert after["tok_in"] == before["tok_in"]

        # The unpriced token split must survive the rollup: rows 5..19 were
        # deleted, of which the odd ones (cost 0) are unpriced → 8 rows ×
        # tok_in 100 backfilled into the widened columns.
        async with eng.connect() as conn:
            unpriced = (await conn.execute(text(
                "SELECT COALESCE(SUM(unpriced_requests), 0),"
                " COALESCE(SUM(unpriced_tok_in), 0) FROM request_rollups"
            ))).one()
        assert tuple(unpriced) == (8, 800), (
            "the unpriced token split must be carried into the rollup")
    finally:
        await eng.dispose()
