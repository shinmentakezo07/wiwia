"""Round 141: the whole-gateway backup silently dropped ``error_message``.

``wiwi/server/backup.py`` exports ``request_logs`` through an *explicit*
column allowlist, ``_REQUEST_LOG_COLUMNS``. The writer side
(``wiwi/logging_core/db_sink.py``) uses its own list, ``_COLS``, which gained
``error_message`` when the request log started recording the caller-visible
upstream error text. Nothing connects the two tuples, and ``_read_logs``
narrows to ``[c for c in _REQUEST_LOG_COLUMNS if c in present]`` — so the
column was dropped with no error, no warning and no test.

The consequence: a backup taken by an operator is *missing the one field* that
explains why requests failed. Restore it into a new instance and every failed
request is an anonymous non-200 row. This has now happened twice (the same
drift bit the ``response_cache_hit`` / ``cache_hit`` split a few rounds back),
which is why the last test here is a **guard**: it asserts every column in
``db_sink._COLS`` is accounted for in ``backup._REQUEST_LOG_COLUMNS`` ∪
``backup.BODY_COLUMNS``, so the next column added to the writer fails the
suite instead of the export.

``error_message`` deliberately does NOT go into ``BODY_COLUMNS``: it is not
user prompt/completion text and must not require ``include_bodies`` to export.
It is already credential-scrubbed by ``_scrub_credentials`` at write time.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest
import respx
import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.logging_core import db_sink
from wiwi.server import backup as backup_mod
from wiwi.server.app import create_app

H = {"Authorization": "Bearer sk-wiwi-master-test"}

#: The upstream text that ends up inside ``error_message``. The logged value is
#: the *caller-visible* message, which the router wraps with provider context
#: (``p1 error 500: upstream said no: model overloaded``), so the assertions
#: below test for containment rather than equality — what matters is that the
#: operator's diagnostic survives the round trip.
UPSTREAM_ERROR = "upstream said no: model overloaded"


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-upstream")])],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


def _bare_config() -> WiwiConfig:
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


@pytest.fixture
async def bare_client():
    async with client_for(_bare_config()) as c:
        yield c


async def _export(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    r = await client.get(f"/admin/backup?{query}" if query else "/admin/backup",
                         headers=H)
    assert r.status_code == 200, r.text
    return r.json()


async def _rows_in(client, *, wait_for: int = 0) -> list[dict]:
    """Raw ``request_logs`` rows, straight from the target's engine.

    The log sink batches its writes, so *wait_for* polls (50 ms apart, 2 s
    ceiling) until at least that many rows have landed. Without it every
    assertion races the pump and fails for the wrong reason.
    """
    engine = client.wiwi.config_store.engine

    async def _read() -> list[dict]:
        async with engine.connect() as conn:
            result = await conn.execute(sa.text(
                "SELECT request_id, error_code, error_message"
                " FROM request_logs ORDER BY ts"))
            keys = list(result.keys())
            return [dict(zip(keys, r)) for r in result.all()]

    rows = await _read()
    for _ in range(40):
        if len(rows) >= wait_for:
            return rows
        await asyncio.sleep(0.05)
        rows = await _read()
    return rows


@respx.mock
async def _drive_one_ok_one_failed(client) -> str:
    """Two logged requests: one 200, one upstream 500. Returns the error text."""
    route = respx.post("https://api.openai.com/v1/chat/completions")
    # Success first: a 500 cools the only deployment, and the second request
    # would 503 on "no healthy deployment" instead of hitting the mock.
    route.side_effect = [
        httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 1,
            "model": "gpt-4o",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                      "total_tokens": 7}}),
        httpx.Response(500, json={"error": {
            "message": UPSTREAM_ERROR, "type": "server_error"}}),
    ]
    key = (await client.post("/admin/keys/generate", headers=H,
                             json={"alias": "ci"})).json()["key"]
    auth = {"Authorization": f"Bearer {key}"}
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}

    ok = await client.post("/v1/chat/completions", headers=auth, json=body)
    assert ok.status_code == 200, ok.text
    failed = await client.post("/v1/chat/completions", headers=auth, json=body)
    assert failed.status_code >= 400, failed.text
    return key


# ---------------------------------------------------------------------------
# 1. the export carries the column
# ---------------------------------------------------------------------------


@respx.mock
async def test_export_carries_error_message(client):
    await _drive_one_ok_one_failed(client)

    stored = {r["request_id"]: r for r in await _rows_in(client, wait_for=2)}
    failed_ids = [rid for rid, r in stored.items() if r["error_message"]]
    assert failed_ids, "the failing request must have recorded an error_message"

    doc = await _export(client, sections="logs")
    exported = {r["request_id"]: r for r in doc["logs"]["request_logs"]}
    assert set(exported) == set(stored), "every logged row must be exported"

    for rid in failed_ids:
        assert "error_message" in exported[rid], (
            "the export dropped error_message — a backup of a broken gateway "
            "cannot explain why requests failed")
        assert exported[rid]["error_message"] == stored[rid]["error_message"]
        assert UPSTREAM_ERROR in exported[rid]["error_message"]

    ok_ids = [rid for rid, r in stored.items() if not r["error_message"]]
    assert ok_ids, "the successful request must have an empty error_message"
    for rid in ok_ids:
        assert not exported[rid].get("error_message")


@respx.mock
async def test_error_message_needs_no_include_bodies_flag(client):
    """It is a diagnostic, not user content — ``include_bodies`` must not gate it."""
    await _drive_one_ok_one_failed(client)
    stored = {r["request_id"] for r in await _rows_in(client, wait_for=2)
              if r["error_message"]}
    assert stored, "the failing request must have recorded an error_message"

    doc = await _export(client, sections="logs")
    assert doc["include_bodies"] is False
    exported = {r["request_id"]: r for r in doc["logs"]["request_logs"]}
    for rid in stored:
        assert UPSTREAM_ERROR in (exported[rid].get("error_message") or "")
        assert "request_body" not in exported[rid]


# ---------------------------------------------------------------------------
# 2. export -> import round trip
# ---------------------------------------------------------------------------


@respx.mock
async def test_round_trip_preserves_error_message(client, bare_client):
    await _drive_one_ok_one_failed(client)
    await _rows_in(client, wait_for=2)  # drain the log batch before exporting
    doc = await _export(client, sections="logs")
    assert any(UPSTREAM_ERROR in (r.get("error_message") or "")
               for r in doc["logs"]["request_logs"]), (
        "the export dropped error_message before the round trip even starts")

    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    assert r.json()["logs"] == 2

    restored = {row["request_id"]: row for row in await _rows_in(bare_client)}
    messages = [row["error_message"] or "" for row in restored.values()]
    assert any(UPSTREAM_ERROR in m for m in messages), (
        "restored rows lost the error text — the backup is not a faithful copy")


# ---------------------------------------------------------------------------
# 3. older backup documents still import
# ---------------------------------------------------------------------------


@respx.mock
async def test_import_of_a_document_without_error_message_still_works(client,
                                                                       bare_client):
    """A backup taken before the column existed has no ``error_message`` key."""
    await _drive_one_ok_one_failed(client)
    await _rows_in(client, wait_for=2)  # drain the log batch before exporting
    doc = await _export(client, sections="logs")
    for row in doc["logs"]["request_logs"]:
        row.pop("error_message", None)
    assert all("error_message" not in r for r in doc["logs"]["request_logs"])

    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    assert r.json()["logs"] == 2
    restored = await _rows_in(bare_client)
    assert len(restored) == 2
    assert all(not row["error_message"] for row in restored)


@respx.mock
async def test_import_into_a_table_without_the_column_is_a_noop(client):
    """Restoring a new backup onto an *older* gateway must not blow up.

    ``_insert_rows`` narrows to the target's actual columns, so ``error_message``
    is simply not bound. Verified against a hand-built table rather than a
    doctored app, because that is the only way to get a ``request_logs`` the
    running schema does not produce.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE request_logs ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  ts REAL, request_id TEXT, error_code TEXT)"))
    try:
        written = await backup_mod._insert_rows(engine, "request_logs", [{
            "ts": 1.0, "request_id": "r1", "error_code": "upstream_error",
            "error_message": UPSTREAM_ERROR, "tok_in": 5,
        }], key="request_id")
        assert written == 1
        async with engine.connect() as conn:
            got = (await conn.execute(sa.text(
                "SELECT request_id, error_code FROM request_logs"))).all()
        assert got == [("r1", "upstream_error")], (
            "a target lacking the column must ignore it, not fail the restore")
    finally:
        await engine.dispose()


def test_error_message_is_not_treated_as_json_or_numeric():
    """A plain TEXT column: no JSON re-encoding, no float coercion.

    Either mistake would either corrupt the value (``"[...json...]"``) or reject
    the whole document (``"error_message must be a number"``) — and both are
    easy to reintroduce by reflex when adding a column.
    """
    assert "error_message" not in backup_mod._JSON_COLUMNS
    assert "error_message" not in backup_mod._LOG_NUMERIC
    # ...and it is a diagnostic, not body text.
    assert "error_message" not in backup_mod.BODY_COLUMNS


# ---------------------------------------------------------------------------
# 4. drift guard — the recurrence this round is really about
# ---------------------------------------------------------------------------


def test_request_log_columns_cover_every_written_column():
    """Every ``db_sink._COLS`` column must be exported.

    Two allowlists describing one table is the defect. This test is the thing
    that stops the next column from being added to the writer and forgotten
    here: a new ``_COLS`` entry with no home in the export fails the suite
    loudly instead of vanishing from every backup.
    """
    exported = set(backup_mod._REQUEST_LOG_COLUMNS) | set(backup_mod.BODY_COLUMNS)
    missing = [c for c in db_sink._COLS if c not in exported]
    assert not missing, (
        f"db_sink._COLS gained {missing} with no home in "
        f"backup._REQUEST_LOG_COLUMNS or backup.BODY_COLUMNS — the column "
        f"will be silently dropped from every backup")
    assert set(backup_mod._REQUEST_LOG_COLUMNS) <= set(db_sink._COLS), (
        "the export names a column the writer never writes — dead allowlist "
        "entry, or a typo that hides the real drift")