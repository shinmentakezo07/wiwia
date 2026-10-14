"""Round 137: the request log must show the exact error the caller received.

``error_code`` is only the coarse ``etype`` (``rate_limit_error``, ``api_error``).
The upstream message the caller actually saw — "openai-main rate limited: You
exceeded your current quota…" — was thrown away by ``build_log_event``, so
``/console/request-logs`` could never reproduce a failure's cause and an
operator had to chase the same error through the provider's own console.

``LogEvent.error_message`` now carries ``ctx.error.message``, credential-scrubbed,
persisted to ``request_logs.error_message``, and surfaced in the request-log
detail drawer. These tests pin that chain end to end.
"""

from __future__ import annotations

import time

import pytest
import sqlalchemy as saa
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.core.context import RequestContext
from wiwi.core.gateway import build_log_event
from wiwi.ir.types import Request
from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent
from wiwi.logging_core.subsystem import public_dict
from wiwi.providers.base import WiwiError


def _ctx(**kw) -> RequestContext:
    return RequestContext(surface="chat", ir_req=Request(model="m", messages=[]), **kw)


def _event(error_message: str = "", **kw) -> LogEvent:
    return LogEvent(stream="request", ts=time.time(), request_id="r1", **kw,
                    error_message=error_message)


# -- build_log_event ------------------------------------------------------------


def test_build_log_event_carries_upstream_error_message():
    evt = build_log_event(_ctx(error=WiwiError(
        429, "rate_limit_error",
        "openai-main rate limited: You exceeded your current quota")))
    assert evt.error_code == "rate_limit_error"
    assert evt.error_message == "openai-main rate limited: You exceeded your current quota"


def test_build_log_event_error_message_empty_on_success():
    evt = build_log_event(_ctx(status=200, error=None))
    assert evt.error_message == ""


def test_build_log_event_scrubs_credentials_from_error_message():
    """A 401 upstream quotes the rejected key; the log row must not echo it."""
    evt = build_log_event(_ctx(error=WiwiError(
        401, "authentication_error",
        "openai-main rejected credentials (401): Invalid API key sk-live-abcdef123456")))
    assert "sk-live-abcdef123456" not in evt.error_message
    assert "sk-***" in evt.error_message


# -- public_dict / SSE shape ----------------------------------------------------


def test_public_dict_exposes_error_message():
    d = public_dict(_event(error_message="boom"))
    assert d["error_message"] == "boom"


# -- DB persistence -------------------------------------------------------------


@pytest.fixture
async def db():
    """Temp in-memory SQLite DBSink."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sink = DBSink(engine)
    await sink.startup()
    yield sink
    await engine.dispose()


async def test_db_roundtrip_preserves_error_message(db):
    await db.write_requests([_event(error_message="upstream 529 overloaded")])
    rows = await db.read_requests(limit=10)
    assert len(rows) == 1
    assert rows[0]["error_message"] == "upstream 529 overloaded"
    assert rows[0]["error_code"] == ""


async def test_db_read_by_id_preserves_error_message(db):
    await db.write_requests([_event(error_message="stream died: reset by peer")])
    row = await db.read_request_by_id("r1")
    assert row is not None
    assert row["error_message"] == "stream died: reset by peer"


async def test_db_migration_adds_error_message_to_legacy_table():
    """A request_logs table created before the column gains it via ALTER.

    Exercises ``DBSink._migrate``: an existing schema without ``error_message``
    is widened in place on startup, and the new column then round-trips.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # Build the pre-column schema by taking the current DDL and dropping the
    # error_message line, so every other column matches a real legacy table.
    import wiwi.logging_core.db_sink as mod
    legacy_ddl = "\n".join(
        line for line in mod._REQUEST_DDL_SQLITE.splitlines()
        if "error_message" not in line)
    assert "error_message" not in legacy_ddl
    async with engine.begin() as conn:
        await conn.execute(saa.text(legacy_ddl))
        # The sibling tables must match their real DDL: _migrate builds audit
        # and rollup indexes whose statements reference their columns.
        await conn.execute(saa.text(mod._AUDIT_DDL_SQLITE))
        await conn.execute(saa.text(mod._ROLLUP_DDL_SQLITE))

    sink = DBSink(engine)
    await sink.startup()  # runs _migrate -> ALTER TABLE ADD COLUMN

    # The column now exists and a write with an error message round-trips.
    await sink.write_requests([_event(error_message="legacy table upgraded")])
    rows = await sink.read_requests(limit=10)
    assert len(rows) == 1
    assert rows[0]["error_message"] == "legacy table upgraded"
    await engine.dispose()

