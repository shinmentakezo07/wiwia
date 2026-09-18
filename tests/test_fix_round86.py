"""Regression tests for the server-side time window + offset on the
request-logs read path (round 86).

Before this change ``read_requests`` always returned the newest ``limit`` rows
regardless of age: the console fetched 10 000 of them and threw away
everything outside the selected range client-side. The sink now takes
``minutes`` (``0`` = all-time, mirroring ``read_overview``) and ``offset``, the
endpoint forwards and clamps them, and the query cache keys on both — without
that last part a bounded read and an all-time read of the same
``(limit, key_ids)`` would share a cache entry and serve each other's rows.

RED evidence (what fails on the pre-change code, before the fix):
- ``test_minutes_zero_returns_same_rows_as_pre_change_call`` and
  ``test_minutes_window_excludes_old_and_includes_recent`` and
  ``test_offset_is_strict_suffix_of_unlimited_query``: ``read_requests``
  rejected the ``minutes``/``offset`` keyword arguments (TypeError).
- ``test_window_and_key_ids_compose``: same TypeError.
- ``test_empty_key_ids_returns_empty_without_a_query``: the bare
  ``key_ids=[]`` assertion already held, but the new-parameter call raised
  TypeError.
- ``test_cache_does_not_serve_one_window_for_another``: the old cache key was
  ``("read_requests", limit, key_ids)`` — no window — so the second window's
  read returned the first window's cached rows.
- ``test_endpoint_passes_window_through``: the endpoint ignored the params, so
  a windowed request returned every row.
- ``test_endpoint_clamps_negative_minutes_and_offset`` is a boundary guard
  rather than a RED test: pre-change the endpoint ignored the params entirely,
  and clamping a negative to its no-op default is observationally identical to
  ignoring it. It pins the clamp so a later refactor cannot drop it.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

import wiwi.server.app as app_mod
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent

MASTER = "sk-wiwi-master-round86"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _evt(ts: float, **kw) -> LogEvent:
    defaults = {"stream": "request", "ts": ts}
    defaults.update(kw)
    return LogEvent(**defaults)


@pytest.fixture
async def db():
    """Temp in-memory SQLite DBSink with schema initialized."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sink = DBSink(engine)
    await sink.startup()
    yield sink
    await engine.dispose()


async def _seed(sink: DBSink, events: list[LogEvent]) -> None:
    await sink.write_requests(events)


# ============================================================================
# Sink: minutes (0 = all-time) + offset
# ============================================================================

async def test_minutes_zero_returns_same_rows_as_pre_change_call(db):
    """The control: ``minutes=0`` must be byte-for-byte the old behaviour.

    ``minutes == 0`` means "no ts cutoff" (all-time), the same convention
    ``read_overview`` uses, so the pre-change call and the explicit
    ``minutes=0, offset=0`` call return the same rows in the same order.
    """
    now = time.time()
    await _seed(db, [
        _evt(now - 10, request_id="newest"),
        _evt(now - 20, request_id="middle"),
        _evt(now - 100_000, request_id="oldest"),
    ])
    legacy = await db.read_requests(100)
    explicit = await db.read_requests(100, minutes=0, offset=0)
    assert [r["request_id"] for r in explicit] == [r["request_id"] for r in legacy]
    # And the control actually has all three, newest-first.
    assert [r["request_id"] for r in legacy] == ["newest", "middle", "oldest"]


async def test_minutes_window_excludes_old_and_includes_recent(db):
    """A positive ``minutes`` is a ``ts >= cutoff`` lookback window."""
    now = time.time()
    await _seed(db, [
        _evt(now - 30, request_id="in-window"),
        _evt(now - 60 * 90, request_id="out-of-window"),  # 90 minutes ago
    ])
    rows = await db.read_requests(100, minutes=60)
    assert [r["request_id"] for r in rows] == ["in-window"]
    # All-time still sees both, so the window — not the seed — did the hiding.
    all_rows = await db.read_requests(100, minutes=0)
    assert {r["request_id"] for r in all_rows} == {"in-window", "out-of-window"}


async def test_offset_is_strict_suffix_of_unlimited_query(db):
    """``offset`` skips the first N of the same newest-first ordering."""
    now = time.time()
    await _seed(db, [_evt(now - i, request_id=f"r{i}") for i in range(1, 6)])
    full = [r["request_id"] for r in await db.read_requests(100)]
    assert full == ["r1", "r2", "r3", "r4", "r5"]
    for off in (1, 2, 3):
        page = [r["request_id"] for r in await db.read_requests(100, offset=off)]
        assert page == full[off:]
        assert page, "a suffix within range must not be empty"
    # Past the end is an empty page, not an error.
    assert await db.read_requests(100, offset=99) == []
    # offset composes with the window: skip the newest in-window row.
    windowed = [r["request_id"] for r in await db.read_requests(100, minutes=60, offset=1)]
    assert windowed == full[1:]


async def test_window_and_key_ids_compose(db):
    """The ts cutoff and the key filter land in the same WHERE clause."""
    now = time.time()
    await _seed(db, [
        _evt(now - 10, key_id="k1", request_id="k1-in-window"),
        _evt(now - 10, key_id="k2", request_id="k2-in-window"),
        _evt(now - 60 * 90, key_id="k1", request_id="k1-out-of-window"),
    ])
    rows = await db.read_requests(100, key_ids=["k1"], minutes=60)
    # k2 is in-window but not k1; k1-out-of-window is k1 but out of window.
    assert [r["request_id"] for r in rows] == ["k1-in-window"]
    both = await db.read_requests(100, key_ids=["k1"], minutes=0)
    assert {r["request_id"] for r in both} == {"k1-in-window", "k1-out-of-window"}
    # A wider window still excludes the other key.
    assert {r["request_id"] for r in await db.read_requests(100, key_ids=["k1"],
                                                           minutes=10080)} == {
        "k1-in-window", "k1-out-of-window"}


async def test_empty_key_ids_returns_empty_without_a_query(db):
    """``key_ids=[]`` (owns no keys) returns [] with no DB access at all.

    An empty list is NOT "unfiltered" — treating it as such leaks every other
    user's rows — so it must short-circuit before the query, with or without
    the new window/offset parameters.
    """
    now = time.time()
    await _seed(db, [_evt(now - 5, key_id="k1", request_id="x")])

    calls = {"n": 0}

    async def _boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("empty key_ids must not query the DB")

    db._read_requests_uncached = _boom  # type: ignore[method-assign]
    assert await db.read_requests(100, key_ids=[]) == []
    assert await db.read_requests(100, key_ids=[], minutes=60, offset=5) == []
    assert calls["n"] == 0
    assert not any(k[0] == "read_requests" for k in db._query_cache)


async def test_cache_does_not_serve_one_window_for_another(db):
    """The query cache keys on the window and offset, not just (limit, key_ids).

    The old key was ``("read_requests", limit, key_ids)``, so two different
    windows collided: the first cached result was served for the second window
    (and vice versa). Assert the observable rows instead of the key layout.
    """
    now = time.time()
    await _seed(db, [
        _evt(now - 10, request_id="recent"),
        _evt(now - 60 * 120, request_id="ancient"),  # 2 hours ago
    ])

    narrow = await db.read_requests(100, minutes=60)
    assert [r["request_id"] for r in narrow] == ["recent"]
    # Same (limit, key_ids) but a different window — must NOT reuse `narrow`.
    wide = await db.read_requests(100, minutes=0)
    assert {r["request_id"] for r in wide} == {"recent", "ancient"}
    assert wide is not narrow

    # Reverse insertion order: all-time cached first, narrow window second.
    db.invalidate_cache()
    wide2 = await db.read_requests(100, minutes=0)
    narrow2 = await db.read_requests(100, minutes=60)
    assert {r["request_id"] for r in wide2} == {"recent", "ancient"}
    assert [r["request_id"] for r in narrow2] == ["recent"]
    assert narrow2 is not wide2

    # The offset is part of the key too: a later page must not be served the
    # first page's rows.
    first = await db.read_requests(100, minutes=0, offset=0)
    second = await db.read_requests(100, minutes=0, offset=1)
    assert [r["request_id"] for r in first] == ["recent", "ancient"]
    assert [r["request_id"] for r in second] == ["ancient"]


# ============================================================================
# Endpoint: pass-through, clamping, and the ring fallback
# ============================================================================

def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def seeded_client():
    """App whose DB holds two recent rows and one three hours old."""
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        sink: DBSink = app.state.wiwi.logs.db_sink
        now = time.time()
        await sink.write_requests([
            _evt(now - 30, request_id="recent-1", key_alias="k"),
            _evt(now - 60, request_id="recent-2", key_alias="k"),
            _evt(now - 60 * 60 * 3, request_id="old-1", key_alias="k"),
        ])
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c, sink


async def test_endpoint_passes_window_through(seeded_client):
    """GET /admin/logs/requests?minutes= filters server-side."""
    c, _ = seeded_client
    r = await c.get("/admin/logs/requests?minutes=60", headers=AUTH)
    assert r.status_code == 200
    assert [row["request_id"] for row in r.json()["logs"]] == ["recent-1", "recent-2"]
    # minutes=0 stays all-time.
    r2 = await c.get("/admin/logs/requests?minutes=0", headers=AUTH)
    assert {row["request_id"] for row in r2.json()["logs"]} == {
        "recent-1", "recent-2", "old-1"}


async def test_endpoint_passes_offset_through(seeded_client):
    """GET /admin/logs/requests?offset= skips the first N of the ordered set."""
    c, _ = seeded_client
    r = await c.get("/admin/logs/requests?offset=1", headers=AUTH)
    assert r.status_code == 200
    assert [row["request_id"] for row in r.json()["logs"]] == ["recent-2", "old-1"]


async def test_endpoint_clamps_negative_minutes_and_offset(seeded_client):
    """Negative window/offset clamp to their no-op defaults.

    ``minutes < 0`` would push the cutoff into the future (hiding every row)
    and a negative ``OFFSET`` is invalid SQL, so both clamp to all-time /
    first-page instead of truncating or erroring.
    """
    c, _ = seeded_client
    r = await c.get("/admin/logs/requests?minutes=-5&offset=-3", headers=AUTH)
    assert r.status_code == 200
    assert [row["request_id"] for row in r.json()["logs"]] == [
        "recent-1", "recent-2", "old-1"]


async def test_endpoint_ring_fallback_applies_window_and_offset():
    """The no-DB ring path agrees with the DB path on the same query.

    The ring is oldest→newest, so the window is applied to the events and the
    result reversed to newest-first before the offset is taken.
    """
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        logs = app.state.wiwi.logs
        logs.set_db_sink(None)
        now = time.time()
        logs.log_request(_evt(now - 60 * 60 * 3, request_id="old"))
        logs.log_request(_evt(now - 30, request_id="recent-1"))
        logs.log_request(_evt(now - 10, request_id="recent-2"))
        # The pump is async; wait until all three land in the ring.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if len(await logs.sse.replay("request", 0)) >= 3:
                break
            await asyncio.sleep(0.02)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.get("/admin/logs/requests?minutes=60", headers=AUTH)
            assert [row["request_id"] for row in r.json()["logs"]] == [
                "recent-2", "recent-1"]
            r2 = await c.get("/admin/logs/requests?minutes=60&offset=1", headers=AUTH)
            assert [row["request_id"] for row in r2.json()["logs"]] == ["recent-1"]
            r3 = await c.get("/admin/logs/requests?minutes=0", headers=AUTH)
            assert [row["request_id"] for row in r3.json()["logs"]] == [
                "recent-2", "recent-1", "old"]
