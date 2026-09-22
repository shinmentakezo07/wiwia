"""End-to-end: the capped request log still answers the admin API correctly.

These drive the REAL app over HTTP (LifespanManager + ASGITransport), with the
real ``LoggingSubsystem`` -> ``DBSink`` pipeline and a real SQLite file, then
exercise the very endpoints an operator uses. They exist to prove the two sink
fixes hold through the whole stack, not just at the unit boundary:

* a ts-tie cap must actually bound ``request_logs`` while ``/admin/stats/overview``
  keeps reporting the pre-cap totals (aggregates moved into ``request_rollups``);
* a batch holding one bad row must still persist its good rows, so the
  dashboard shows them.
"""
from __future__ import annotations

import time

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import text

import wiwi.server.app as app_mod
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.logging_core.events import LogEvent

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _config(db_url: str) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER, database_url=db_url),
    )


async def _client(db_url: str):
    app = app_mod.create_app(_config(db_url))
    lm = LifespanManager(app)
    await lm.__aenter__()
    transport = httpx.ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    await c.__aenter__()
    return app, lm, c


async def _seed_tied(app, n: int, *, tok_in: int = 10) -> None:
    """Insert *n* rows all sharing one ts — the shape that defeated the cap."""
    sink = app.state.wiwi.logs.db_sink
    now = time.time()
    events = [LogEvent(stream="request", ts=now, request_id=f"t{i}",
                       surface="chat", key_id="k1", model_group="gpt-4o",
                       provider="p1", status=200, tok_in=tok_in, tok_out=5,
                       cost=0.001) for i in range(n)]
    await sink.write_requests(events)


async def test_e2e_ts_tie_cap_bounds_table_and_overview_survives(tmp_path):
    """Cap under a ts tie through the real app + real HTTP endpoints."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/e2e_tie.db"
    app, lm, c = await _client(db_url)
    try:
        await _seed_tied(app, 40)
        sink = app.state.wiwi.logs.db_sink

        # The dashboard BEFORE the cap.
        before = (await c.get("/admin/stats/overview?minutes=0",
                              headers=AUTH)).json()
        assert before["requests"] == 40, before

        # Force the tie-defeating cap directly at the sink (this is what
        # LogRetention.sweep drives on its interval).
        deleted = await sink.enforce_log_cap(10)
        assert deleted == 30, f"cap must remove 30 of 40, removed {deleted}"

        async with sink.engine.connect() as conn:
            raw = (await conn.execute(
                text("SELECT COUNT(*) FROM request_logs"))).scalar()
        assert raw == 10, "the raw table must be at its bound after the cap"

        # The API must report the same totals — capping moves detail into the
        # rollup, it does not lose history.
        after = (await c.get("/admin/stats/overview?minutes=0",
                             headers=AUTH)).json()
        assert after["requests"] == before["requests"] == 40
        assert after["tok_in"] == before["tok_in"] == 400
        assert after["cost"] == pytest.approx(before["cost"])

        # And the log endpoint still serves the surviving raw rows.
        logs = (await c.get("/admin/logs/requests?limit=1000", headers=AUTH)).json()["logs"]
        assert len(logs) == 10
    finally:
        await c.__aexit__(None, None, None)
        await lm.__aexit__(None, None, None)


async def test_e2e_bad_row_keeps_siblings_visible_in_the_api(tmp_path):
    """One malformed row in a drain must not hide its good siblings."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/e2e_batch.db"
    app, lm, c = await _client(db_url)
    try:
        sink = app.state.wiwi.logs.db_sink
        good = [LogEvent(stream="request", ts=1000.0 + i, request_id=f"g{i}",
                         surface="chat", key_id="k1", model_group="gpt-4o",
                         provider="p1", status=200, tok_in=7, tok_out=3,
                         cost=0.001) for i in range(25)]
        bad = LogEvent(stream="request", ts=None, request_id="bad",
                       surface="chat", key_id="k1")

        await sink.write_requests(good + [bad])  # must not blow up the pump

        ov = (await c.get("/admin/stats/overview?minutes=0", headers=AUTH)).json()
        assert ov["requests"] == 25, ov
        assert ov["tok_in"] == 25 * 7

        logs = (await c.get("/admin/logs/requests?limit=1000", headers=AUTH)).json()["logs"]
        ids = {row["request_id"] for row in logs}
        assert "bad" not in ids
        assert len([i for i in ids if i.startswith("g")]) == 25
    finally:
        await c.__aexit__(None, None, None)
        await lm.__aexit__(None, None, None)


async def test_e2e_cap_survives_age_prune_then_cap(tmp_path):
    """The real sweep order (age, then cap) still bounds a tied table."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/e2e_sweep.db"
    app, lm, c = await _client(db_url)
    try:
        sink = app.state.wiwi.logs.db_sink
        now = time.time()
        # 20 ancient rows (age-prunable) + 30 sharing one recent ts (cap-bound).
        ancient = [LogEvent(stream="request", ts=now - 100 * 86400,
                            request_id=f"a{i}", surface="chat", key_id="k1",
                            model_group="gpt-4o", provider="p1", status=200,
                            tok_in=10, cost=0.001) for i in range(20)]
        await sink.write_requests(ancient)
        await _seed_tied(app, 30)

        before = (await c.get("/admin/stats/overview?minutes=0",
                              headers=AUTH)).json()
        assert before["requests"] == 50

        by_age = await sink.prune_old_requests(30)
        by_cap = await sink.enforce_log_cap(10)

        assert by_age == 20, by_age
        assert by_cap == 20, f"30 tied rows capped to 10 -> 20 removed, got {by_cap}"
        async with sink.engine.connect() as conn:
            raw = (await conn.execute(
                text("SELECT COUNT(*) FROM request_logs"))).scalar()
        assert raw == 10

        after = (await c.get("/admin/stats/overview?minutes=0",
                             headers=AUTH)).json()
        assert after["requests"] == before["requests"] == 50
        assert after["tok_in"] == before["tok_in"] == 500
    finally:
        await c.__aexit__(None, None, None)
        await lm.__aexit__(None, None, None)
