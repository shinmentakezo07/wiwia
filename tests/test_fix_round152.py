"""Round 152 — the backup's age window was applied to the first page only.

``_read_all`` adds the ``ts >= cutoff`` predicate when it builds the *first*
page's WHERE clause and drops it for every page after. The loop still advances
by ``id > cursor``, so pages 2..n are unconstrained by ``log_days`` and rows
that are older than the requested window ride along in the export.

The direction of the failure is what makes it worth a round of its own: #454
(the same function, round 142) *lost* rows, which an operator notices as a
short file. This one *adds* rows the operator asked to exclude. A
``log_days=30`` export of a gateway that was back-filled from an archive — or
whose clock stepped — lands rows from years ago in a file the operator then
restores somewhere else, and nothing in the document says so.

Both bulk sections that carry a time column are affected: ``logs`` on ``ts``
and ``rollups`` on ``bucket_ts``. The assertions are on the exported rows, not
on the SQL, because the SQL is what a reader already believes is right.
"""

from __future__ import annotations

import contextlib
import time

import httpx
import pytest
import sqlalchemy as sa
from asgi_lifespan import LifespanManager

from wiwi.config import GeneralSettings, WiwiConfig
from wiwi.server import backup as backup_mod
from wiwi.server.app import create_app

H = {"Authorization": "Bearer sk-wiwi-master-test"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@contextlib.asynccontextmanager
async def client_for(cfg=None):
    app = create_app(cfg or _config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            c.wiwi = app.state.wiwi
            yield c


@pytest.fixture
async def client():
    async with client_for() as c:
        yield c


async def _export(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    r = await client.get(f"/admin/backup?{query}" if query
                         else "/admin/backup", headers=H)
    assert r.status_code == 200, r.text
    return r.json()


async def _seed_logs(client, count: int, *, ts: float, prefix: str) -> None:
    engine = client.wiwi.config_store.engine
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO request_logs (ts, request_id, surface, status, cost)"
            " VALUES (:ts, :rid, 'chat', 200, 0.0)"),
            [{"ts": ts, "rid": f"{prefix}{i:06d}"} for i in range(count)])


async def _seed_rollups(client, count: int, *, bucket_ts: float,
                        prefix: str) -> None:
    engine = client.wiwi.config_store.engine
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO request_rollups (bucket_ts, key_id, model_group,"
            " provider, serving_model, requests)"
            " VALUES (:b, :kid, 'gpt-4o', 'p1', 'gpt-4o', 1)"),
            [{"b": bucket_ts, "kid": f"{prefix}{i:06d}"} for i in range(count)])


# ---------------------------------------------------------------------------
# 1. ``logs`` — the age window is dropped after the first page
# ---------------------------------------------------------------------------


async def test_log_export_window_holds_past_the_first_page(client):
    """A full first page is what makes the loop advance at all (#454).

    The seed is shaped so the *only* way an ancient row reaches page 2 is the
    missing predicate: page 1 is entirely inside the window, so it arrives
    full and the cursor advances into the rows that are out of it.
    """
    now = time.time()
    ancient = backup_mod._PAGE + 500
    await _seed_logs(client, backup_mod._PAGE + 1000, ts=now, prefix="new")
    await _seed_logs(client, ancient, ts=now - 30 * 86400, prefix="old")

    doc = await _export(client, sections="logs", log_days=1)
    rows = doc["logs"]["request_logs"]
    leaked = [r for r in rows if r["request_id"].startswith("old")]

    assert not leaked, (
        f"{len(leaked)} row(s) older than the 1-day window were exported — the "
        f"cutoff is applied to the first page only, and this export asked for "
        f"1 day and shipped 30-day-old usage"
    )
    assert len(rows) == backup_mod._PAGE + 1000


# ---------------------------------------------------------------------------
# 2. ``rollups`` — same function, the other time column
# ---------------------------------------------------------------------------


async def test_rollup_export_window_holds_past_the_first_page(client):
    """Rollups outlive the raw log, so they are the usage record on a long-lived
    gateway. A window that leaks here misstates spend, not just row count."""
    now = time.time()
    await _seed_rollups(client, backup_mod._PAGE + 1000, bucket_ts=now,
                        prefix="new")
    await _seed_rollups(client, backup_mod._PAGE + 500,
                        bucket_ts=now - 30 * 86400, prefix="old")

    doc = await _export(client, sections="rollups", log_days=1)
    rows = doc["rollups"]["request_rollups"]
    leaked = [r for r in rows if r["key_id"].startswith("old")]

    assert not leaked, (
        f"{len(leaked)} rollup bucket(s) older than the 1-day window were "
        f"exported — usage stats outside the window an operator asked for"
    )
    assert len(rows) == backup_mod._PAGE + 1000
