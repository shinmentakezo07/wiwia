"""Round 209 — idle background sweepers must not keep a serverless database awake.

Neon (and any scale-to-zero Postgres) suspends its compute only after a window
with **zero connections and zero queries**. Two wiwi background loops issued
unconditional periodic SQL even on a gateway serving no traffic:

* ``ResponseStore.sweep_forever`` ran ``DELETE FROM stored_responses`` every
  300 s whether or not the table had any rows — a 5-minute query cadence
  against a ~5-minute suspend window, so the endpoint could never sleep
  (~720 compute-hours/month for an idle gateway).
* ``LogRetention`` took the cross-process sweep lock and ran four prune/cap
  queries every ``log_prune_interval_s`` even when nothing had been written
  since the previous pass.

Properties pinned here:

1. An idle ``ResponseStore`` (no writes since the last sweep) issues no SQL on
   a sweep tick; a store that served traffic still expires its rows, and the
   first tick after startup always sweeps (a restart may inherit expired rows).
2. An idle ``LogRetention`` issues no SQL — not even the lock — while a pass
   after real writes still rolls up and prunes, and an out-of-band writer
   (shared database) is still picked up by a low-frequency forced pass.
3. The responses sweep cadence is operator-configurable
   (``response_store_sweep_interval_s``), defaults to 300 s, and 0 disables
   the background loop (expiry stays enforced on read).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
import sqlalchemy.ext.asyncio as saa
from asgi_lifespan import LifespanManager
from pydantic import ValidationError

from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent
from wiwi.logging_core.retention import LogRetention
from wiwi.server.response_store import ResponseStore


def _count_sql(engine) -> list[str]:
    """Record every SQL statement executed on *engine*; return the live list."""
    seen: list[str] = []

    @sa.event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    return seen


# ---------------------------------------------------------------------------
# 1. ResponseStore: idle ticks issue no SQL
# ---------------------------------------------------------------------------


async def test_response_sweep_skips_sql_when_idle():
    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    store = ResponseStore(engine, ttl_s=3600.0)
    await store.startup()
    try:
        assert await store.sweep() == 0  # first pass after startup always runs
        seen = _count_sql(engine)
        assert await store.sweep() == 0
        assert seen == [], f"idle sweep issued SQL: {seen}"
    finally:
        await engine.dispose()


async def test_response_sweep_still_expires_rows_after_a_put():
    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    store = ResponseStore(engine, ttl_s=3600.0)
    await store.startup()
    try:
        await store.sweep()  # drain the startup pass
        await store.put("resp_old", "keyA", "grp", [], {"id": "resp_old"})
        assert await store.sweep(now=time.time() + 7200) == 1
        assert await store.get("resp_old", "keyA") is None
        seen = _count_sql(engine)
        assert await store.sweep() == 0  # idle again
        assert seen == []
    finally:
        await engine.dispose()


async def test_response_sweep_forever_ticks_are_silent_while_idle():
    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    store = ResponseStore(engine, ttl_s=3600.0)
    await store.startup()
    task = asyncio.create_task(store.sweep_forever(interval_s=0.01))
    try:
        await asyncio.sleep(0.05)  # let the first (startup) pass run
        seen = _count_sql(engine)
        await asyncio.sleep(0.1)  # ~10 idle ticks
        assert seen == [], f"idle sweeper issued SQL: {seen}"
        # Traffic resumes: the very next tick must expire the row.
        await store.put("resp_x", "keyA", "grp", [], {"id": "resp_x"})
        store.ttl_s = 0.01  # tiny positive TTL: 0 would mean "keep forever"
        await asyncio.sleep(0.1)
        async with engine.connect() as conn:
            n = (await conn.execute(sa.text(
                "SELECT COUNT(*) FROM stored_responses"))).scalar()
        assert n == 0, "a put must re-arm the sweeper"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await engine.dispose()


# ---------------------------------------------------------------------------
# 2. LogRetention: no writes since the last pass -> no SQL, not even the lock
# ---------------------------------------------------------------------------


async def _retention(tmp_path):
    engine = saa.create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ret.db")
    sink = DBSink(engine)
    await sink.startup()
    ws = SimpleNamespace(log_retention_days=30, log_max_rows=10000)
    state = SimpleNamespace(logs=SimpleNamespace(db_sink=sink),
                            config=SimpleNamespace(wiwi_settings=ws))
    return engine, sink, LogRetention(state)


def _request_event(i: int, ts: float) -> LogEvent:
    return LogEvent(stream="request", ts=ts, request_id=f"r{i}", surface="chat",
                    key_id="k1", model_group="g", provider="p", status=200,
                    tok_in=10, tok_out=5, cost=0.001)


async def test_retention_sweep_is_silent_when_nothing_was_written(tmp_path):
    engine, _sink, ret = await _retention(tmp_path)
    try:
        await ret.sweep()  # first pass after startup always runs
        seen = _count_sql(engine)
        await ret.sweep()
        await ret.sweep()
        assert seen == [], f"idle retention sweep issued SQL: {seen}"
    finally:
        await engine.dispose()


async def test_retention_sweep_after_writes_still_prunes_and_caps(tmp_path):
    engine, sink, ret = await _retention(tmp_path)
    try:
        await ret.sweep()  # drain the startup pass
        now = time.time()
        old = [_request_event(i, now - 40 * 86400) for i in range(5)]
        fresh = [_request_event(100 + i, now) for i in range(5)]
        await sink.write_requests(old + fresh)
        await sink.write_audit(LogEvent(stream="audit", ts=now - 40 * 86400,
                                        actor="a", action="x", target="t"))
        await ret.sweep()
        async with engine.connect() as conn:
            raw = (await conn.execute(sa.text(
                "SELECT COUNT(*) FROM request_logs"))).scalar()
            rolled = (await conn.execute(sa.text(
                "SELECT COALESCE(SUM(requests), 0) FROM request_rollups"))).scalar()
            audit = (await conn.execute(sa.text(
                "SELECT COUNT(*) FROM audit_logs"))).scalar()
        assert raw == 5, "the age pass must still prune after real writes"
        assert rolled == 5, "pruned rows must still be rolled up"
        assert audit == 0, "the audit age pass must still prune"
    finally:
        await engine.dispose()


async def test_retention_forced_pass_picks_up_an_out_of_band_writer(tmp_path):
    """Shared database: a sibling process's writes never touch our sink, so a
    low-frequency forced pass must still prune them."""
    engine, _sink, ret = await _retention(tmp_path)
    try:
        await ret.sweep()  # drain the startup pass; sink is now idle
        async with engine.begin() as conn:  # bypass the sink entirely
            await conn.execute(sa.text(
                "INSERT INTO request_logs (ts, request_id, surface, key_id,"
                " model_group, provider, status, tok_in, tok_out, cost)"
                " VALUES (:ts, 'sib', 'chat', 'k1', 'g', 'p', 200, 1, 1, 0.0)"),
                {"ts": time.time() - 40 * 86400})
        await ret.sweep()  # idle skip: our sink saw no writes
        async with engine.connect() as conn:
            n = (await conn.execute(sa.text(
                "SELECT COUNT(*) FROM request_logs"))).scalar()
        assert n == 1
        await ret.sweep(force=True)
        async with engine.connect() as conn:
            n = (await conn.execute(sa.text(
                "SELECT COUNT(*) FROM request_logs"))).scalar()
        assert n == 0, "the forced pass must prune the sibling's rows"
    finally:
        await engine.dispose()


async def test_retention_run_forces_a_pass_every_24_ticks(tmp_path, monkeypatch):
    """The background loop must not skip forever on a shared database: every
    24th tick is a forced pass even with no local writes."""
    engine, _sink, ret = await _retention(tmp_path)
    calls: list[bool] = []

    async def fake_sweep(*, force: bool = False):
        calls.append(force)

    monkeypatch.setattr(ret, "sweep", fake_sweep)
    ret._state.config.wiwi_settings.log_prune_interval_s = 0.005
    monkeypatch.setattr("wiwi.logging_core.retention.FIRST_SWEEP_DELAY_S", 0.01)
    ret.start()
    try:
        deadline = time.monotonic() + 5.0
        while len(calls) < 25 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    finally:
        await ret.stop()
        await engine.dispose()
    assert len(calls) >= 25, calls
    assert calls[0] is True, "the first pass after startup must be forced"
    assert calls[24] is True, "tick 24 must be forced again"
    assert calls[1] is False and calls[23] is False


# ---------------------------------------------------------------------------
# 3. response_store_sweep_interval_s: configurable cadence, 0 disables
# ---------------------------------------------------------------------------


def test_response_sweep_interval_defaults_to_300():
    from wiwi.config import WiwiSettings

    assert WiwiSettings().response_store_sweep_interval_s == 300.0


def test_response_sweep_interval_rejects_negative():
    from wiwi.config import WiwiSettings

    with pytest.raises(ValidationError, match="response_store_sweep_interval_s"):
        WiwiSettings(response_store_sweep_interval_s=-1.0)


async def test_zero_interval_disables_the_background_sweeper(tmp_path):
    """ttl may expire rows on read, but no background task may issue SQL."""
    from wiwi.config import (
        GeneralSettings,
        WiwiConfig,
        WiwiSettings,
    )
    from wiwi.server.app import create_app

    cfg = WiwiConfig(
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url=f"sqlite+aiosqlite:///{tmp_path}/gw.db"),
        wiwi_settings=WiwiSettings(store_responses=True,
                                   response_store_sweep_interval_s=0.0))
    app = create_app(cfg)
    async with LifespanManager(app):
        store = app.state.wiwi.response_store
        assert store is not None
        assert store._sweeper is None, "interval 0 must not start a sweeper task"