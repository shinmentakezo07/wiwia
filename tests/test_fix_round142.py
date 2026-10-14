"""Round 142 — the whole-gateway backup exported the *oldest page and only that*.

Six defects in ``wiwi/server/backup.py``, all of the same species: the code
reads as if it were careful, and each one fails *silently* — a backup that
claims to be a faithful copy of the gateway and is not.

1. **Keyset pagination never paged.** ``_read_all`` advanced its cursor with
   ``usable.index("id")`` while ``usable`` came from the *caller's* column list,
   and neither ``_REQUEST_LOG_COLUMNS`` nor ``_ROLLUP_COLUMNS`` contains ``id``.
   So ``cursor`` was always ``None`` and the loop returned after the first full
   page. ``GET /admin/backup?sections=logs`` on a gateway holding 400 000 log
   rows shipped the OLDEST 2 000, with no error and no ``truncated`` marker.
2. **``truncated`` was initialised and never incremented.** ``?log_limit=N``
   silently dropped the remainder while the document asserted completeness.
3. **Deployments exported five limits the ``deployments`` table cannot store.**
   ``rpm``/``tpm``/``timeout``/``max_tokens``/``prompt_cache`` were set on the
   in-memory ``Deployment`` and then dropped on the floor by
   ``store.add_deployment``; the restart path rebuilds them at their defaults.
4. **``_insert_rows`` read its column set off ``rows[0]`` alone.** A field only
   the second row carried was written as the column *default* — verified:
   ``error_code='api_error'`` on row 1 became ``''`` on both rows.
5. **The restore had no rollback.** Every 500-row page was its own
   transaction, so a malformed row 1 200 committed providers, keys, users,
   vkeys and the first 1 000 log rows, then 500'd.
6. **Per-row failures in ``rollups`` were swallowed into a log warning and
   ``responses`` had no guard at all** — a target where every insert fails
   answers ``200 {"rollups": 0}`` and the operator concludes there was nothing
   to restore.

The export/restore is the one place in wiwi where a bug costs an operator their
whole history, so each test below asserts on the *observable* consequence (what
lands in the target database), not on an internal return value.
"""

from __future__ import annotations

import contextlib
import time

import httpx
import pytest
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
from wiwi.server import backup as backup_mod
from wiwi.server.app import create_app

H = {"Authorization": "Bearer sk-wiwi-master-test"}


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


@pytest.fixture
async def raw_engine():
    """A bare engine for the unit-level tests, with no gateway around it."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        yield engine
    finally:
        await engine.dispose()


async def _export(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    r = await client.get(f"/admin/backup?{query}" if query else "/admin/backup",
                         headers=H)
    assert r.status_code == 200, r.text
    return r.json()


async def _seed_logs(client, count: int, *, prefix: str = "r") -> None:
    """Write *count* log rows straight into the sink's table.

    Driving real requests through the gateway would take minutes at this
    volume and couple the paging test to the request path; what is under test
    is the exporter's cursor, and a seeded table is what it reads either way.
    """
    engine = client.wiwi.config_store.engine
    now = time.time()
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO request_logs (ts, request_id, surface, status, cost)"
            " VALUES (:ts, :rid, 'chat', 200, 0.0)"),
            [{"ts": now + i, "rid": f"{prefix}{i:06d}"} for i in range(count)])


async def _count(client, table: str, *, where: str = "", params=None) -> int:
    engine = client.wiwi.config_store.engine
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    async with engine.connect() as conn:
        return (await conn.execute(sa.text(sql), params or {})).scalar()


async def _request_ids(client) -> list[str]:
    engine = client.wiwi.config_store.engine
    async with engine.connect() as conn:
        return [r[0] for r in (await conn.execute(sa.text(
            "SELECT request_id FROM request_logs ORDER BY id"))).all()]


# ---------------------------------------------------------------------------
# 1. keyset pagination aborted after page 1
# ---------------------------------------------------------------------------


async def test_read_all_pages_past_the_first_page(raw_engine):
    """The unit-level proof: more than ``_PAGE`` rows must all come back.

    ``_read_all`` cannot keyset on a column the caller did not project, so the
    cursor was always ``None`` and the loop stopped after one full page — the
    oldest 2000 rows, sorted ascending.
    """
    async with raw_engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE probe ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  ts REAL NOT NULL, request_id TEXT, cost REAL)"))
        await conn.execute(sa.text(
            "INSERT INTO probe (ts, request_id, cost)"
            " VALUES (:ts, :rid, 1.0)"),
            [{"ts": 1.0 + i, "rid": f"p{i:05d}"}
             for i in range(backup_mod._PAGE + 500)])

    rows, truncated = await backup_mod._read_all(
        raw_engine, "probe", ["ts", "request_id", "cost"])
    assert truncated == 0
    assert len(rows) == backup_mod._PAGE + 500, (
        f"_read_all returned {len(rows)} of {backup_mod._PAGE + 500} rows — "
        "the keyset cursor never advanced past the first page")
    assert rows[0]["request_id"] == "p00000"
    assert rows[-1]["request_id"] == f"p{backup_mod._PAGE + 499:05d}"
    assert "id" not in rows[0], "the internal keyset column must be stripped"


async def test_read_all_strips_the_key_column_only_when_it_added_it(raw_engine):
    """``audit``/``responses`` ask for ``id`` on purpose — that one must stay.

    ``stored_responses.id`` is the table's own primary key and the merge key for
    the Responses section; ``audit_logs.id`` is exported for readability.
    """
    async with raw_engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE probe2 ("
            "  id TEXT PRIMARY KEY, ts REAL NOT NULL, actor TEXT)"))
        await conn.execute(sa.text(
            "INSERT INTO probe2 (id, ts, actor) VALUES ('r1', 1.0, 'admin')"))

    rows, _ = await backup_mod._read_all(
        raw_engine, "probe2", ["id", "ts", "actor"])
    assert rows == [{"id": "r1", "ts": 1.0, "actor": "admin"}]

    rows, _ = await backup_mod._read_all(raw_engine, "probe2", ["ts", "actor"])
    assert rows == [{"ts": 1.0, "actor": "admin"}]


async def test_export_returns_every_log_row_beyond_one_page(client):
    """End to end: a two-page log table exports all of it, oldest first."""
    total = backup_mod._PAGE + 100
    await _seed_logs(client, total)

    doc = await _export(client, sections="logs", log_days=0)
    rows = doc["logs"]["request_logs"]
    assert doc["counts"]["logs"]["request_logs"] == total, (
        "the export silently truncated the log at one page")
    assert len(rows) == total
    assert rows[0]["request_id"] == "r000000"
    assert rows[-1]["request_id"] == f"r{total - 1:06d}"
    assert "truncated" not in doc, "a complete export must not claim truncation"


async def test_export_pages_the_rollups_section_too(client):
    """The same cursor bug, the other bulk section with the same missing column."""
    engine = client.wiwi.config_store.engine
    total = backup_mod._PAGE + 25
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO request_rollups (bucket_ts, key_id, model_group,"
            " provider, serving_model, requests)"
            " VALUES (:b, 'k1', 'gpt-4o', 'p1', 'gpt-4o', 1)"),
            [{"b": 1_700_000_000.0 + i} for i in range(total)])

    doc = await _export(client, sections="rollups", log_days=0)
    assert len(doc["rollups"]["request_rollups"]) == total, (
        "the rollups section paged exactly like the log section did — same "
        "missing keyset column")


# ---------------------------------------------------------------------------
# 2. ``truncated`` could never fire
# ---------------------------------------------------------------------------


async def test_truncated_is_emitted_when_a_limit_bites(client):
    await _seed_logs(client, 5, prefix="lim")

    doc = await _export(client, sections="logs", log_days=0, log_limit=2)
    assert len(doc["logs"]["request_logs"]) == 2
    assert doc.get("truncated"), (
        "a capped export must say so — otherwise the operator believes a "
        "partial file is a complete one")
    assert doc["truncated"]["request_logs"] == 3
    assert isinstance(doc["truncated"]["request_logs"], int)


async def test_no_truncated_key_when_the_limit_does_not_bite(client):
    await _seed_logs(client, 5, prefix="lim")

    doc = await _export(client, sections="logs", log_days=0, log_limit=500)
    assert len(doc["logs"]["request_logs"]) == 5
    assert "truncated" not in doc

    doc = await _export(client, sections="logs", log_days=0, log_limit=0)
    assert len(doc["logs"]["request_logs"]) == 5
    assert "truncated" not in doc


async def test_truncated_matches_the_document_type_contract():
    """``BackupDocument.truncated`` is ``Record<string, number>``.

    ``build_backup`` annotated its local as ``dict[str, int]`` while assigning
    it an ``int`` from ``_read_all``, so a capping export emitted a bare number
    where the console expects a per-table map.
    """
    import inspect

    src = inspect.getsource(backup_mod.build_backup)
    assert 'truncated["request_logs"]' in src or (
        'truncated = {"request_logs"' in src), (
        "build_backup must key the truncation report by table name")


# ---------------------------------------------------------------------------
# 3. deployments advertised five limits the table cannot store
# ---------------------------------------------------------------------------


async def test_deployment_limits_the_table_cannot_store_are_not_exported(client):
    """``deployments`` has (group, provider, model, weight, detached) only.

    Exporting ``rpm``/``tpm``/``timeout``/``max_tokens``/``prompt_cache``
    advertises persistence the restore does not deliver: they reach the
    in-memory ``Deployment`` and are dropped by ``add_deployment``, so the next
    restart serves the defaults. They are YAML/``wiwi_params``-owned.
    """
    doc = await _export(client, sections="deployments")
    assert doc["deployments"], "the fixture must define one deployment"
    for entry in doc["deployments"]:
        for field in ("rpm", "tpm", "timeout", "max_tokens", "prompt_cache"):
            assert field not in entry, (
                f"deployments exported {field!r}, which the deployments table "
                "cannot persist — it would be restored and then silently "
                "reverted on the next restart")


async def test_import_leaves_target_deployment_limits_alone_when_absent(client):
    """Absent means *unknown*, not *reset*.

    With the fields gone from the export, an import must not write ``None`` over
    limits the target already has.
    """
    target = client.wiwi.router.groups["gpt-4o"][0]
    target.rpm, target.max_tokens = 7, 1234

    doc = await _export(client, sections="providers,deployments")
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text

    assert client.wiwi.router.groups["gpt-4o"][0].rpm == 7
    assert client.wiwi.router.groups["gpt-4o"][0].max_tokens == 1234


# ---------------------------------------------------------------------------
# 4. ``_insert_rows`` read its columns off rows[0] alone
# ---------------------------------------------------------------------------


async def test_a_field_present_on_a_later_row_is_not_written_as_the_default(
        raw_engine):
    """The exact two-row heterogeneous case.

    ``cols = [c for c in rows[0] if c in present]`` — so ``error_code`` was not
    in the column list at all and row 1's ``"api_error"`` was stored as the
    column default ``''``. Any hand-merged or stitched document loses data with
    no warning.
    """
    async with raw_engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE request_logs ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  ts REAL NOT NULL, request_id TEXT, error_code TEXT DEFAULT '')"))

    written = await backup_mod._insert_rows(raw_engine, "request_logs", [
        {"ts": 1.0, "request_id": "a"},                      # no error_code
        {"ts": 2.0, "request_id": "b", "error_code": "api_error"},
    ], key="request_id")
    assert written == 2

    async with raw_engine.connect() as conn:
        rows = dict((await conn.execute(sa.text(
            "SELECT request_id, error_code FROM request_logs ORDER BY id")
        )).all())
    assert rows["b"] == "api_error", (
        "the field present only on the second row was written as the column "
        "default — the union of all rows' keys must drive the column set")
    assert rows["a"] is None, (
        "a row that simply lacks the field must be stored as NULL, not as the "
        "column default '' — the two are indistinguishable afterwards")


async def test_the_column_union_also_covers_rollups_and_responses(client):
    """Same defect, same fix, in the two per-row sections."""
    engine = client.wiwi.config_store.engine
    rows = [
        {"bucket_ts": 1.0, "key_id": "k", "model_group": "g", "provider": "p",
         "serving_model": "m", "requests": 1},
        {"bucket_ts": 2.0, "key_id": "k", "model_group": "g", "provider": "p",
         "serving_model": "m", "requests": 2, "errors": 5},
    ]
    written = await backup_mod._apply_rollups(client.wiwi,
                                              {"request_rollups": rows})
    assert written == {"written": 2, "failed": 0}

    async with engine.connect() as conn:
        got = (await conn.execute(sa.text(
            "SELECT bucket_ts, errors FROM request_rollups ORDER BY bucket_ts")
        )).all()
    assert got == [(1.0, None), (2.0, 5)], (
        "errors was written as the column default on the row that lacks it, "
        "which is indistinguishable from a real zero count")


# ---------------------------------------------------------------------------
# 5. no rollback — earlier pages and earlier sections stayed committed
# ---------------------------------------------------------------------------


async def test_a_failure_mid_import_applies_nothing(client, bare_client):
    """Row 1 200 has no ``ts``; everything before it must be rolled back.

    With a transaction per 500-row page and per-section commits, this import
    left p1, its key, the generated virtual key, and the first 1 000 log rows
    durable and then 500'd — a gateway the operator has to reconcile by hand.
    """
    await client.post("/admin/providers", headers=H,
                      json={"name": "p2", "provider_type": "openai",
                            "key": "sk-two"})
    await client.post("/admin/keys/generate", headers=H, json={"alias": "ci"})
    doc = await _export(client, include_secrets=1,
                        sections="providers,keys,deployments,vkeys,logs")

    rows = [{"ts": 1.0, "request_id": f"x{i:04d}", "surface": "chat",
             "status": 200} for i in range(600)]
    rows[-1].pop("ts")                     # NOT NULL constraint failed
    doc["logs"] = {"request_logs": rows}

    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 500, r.text
    assert "rolled back" in r.json()["error"]["message"].lower()

    providers = (await bare_client.get("/admin/providers", headers=H)).json()
    assert [p["name"] for p in providers["providers"]] == [], (
        "providers were committed before the log section failed — the import "
        "is not atomic")
    keys = (await bare_client.get("/admin/keys", headers=H)).json()["keys"]
    assert keys == [], "virtual keys were committed before the failure"
    assert await _count(bare_client, "request_logs") == 0, (
        "the log rows written before the bad page stayed committed")
    assert await _count(bare_client, "providers") == 0
    assert await _count(bare_client, "vkeys") == 0


async def test_a_failure_in_an_early_page_rolls_back_the_later_ones(client,
                                                                   bare_client):
    """The page-level half of the same bug: page 1 durable, page 2 raises."""
    await _seed_logs(client, 700, prefix="p")
    doc = await _export(client, sections="logs", log_days=0)
    assert doc["counts"]["logs"]["request_logs"] == 700

    rows = doc["logs"]["request_logs"]
    rows[300].pop("ts")                    # inside the FIRST 500-row page
    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 500, r.text
    assert await _count(bare_client, "request_logs") == 0


# ---------------------------------------------------------------------------
# 6. per-row failures were invisible
# ---------------------------------------------------------------------------


async def test_a_failed_rollup_row_is_counted_and_the_rest_survives(client,
                                                                    bare_client):
    """``_apply_rollups`` logged every failure to stdout and reported ``written``.

    A target whose ``request_rollups`` rejected every insert answered ``200
    {"rollups": 0}`` and the operator concluded there was nothing to restore.
    The good bucket must still land — a bad row rolls back to its own
    savepoint, not the whole import.
    """
    doc = await _export(client, sections="logs")
    doc["logs"] = {"request_logs": [{"ts": 1.0, "request_id": "ok1",
                                     "surface": "chat", "status": 200}]}
    good = {"bucket_ts": 1000.0, "key_id": "k1", "model_group": "gpt-4o",
            "provider": "p1", "serving_model": "gpt-4o", "requests": 3}
    # A value the target's INTEGER column cannot take. Every rollup column is
    # nullable except bucket_ts (which the validator requires), so an
    # unbindable value is how a real rollup insert fails.
    bad = {"bucket_ts": 2000.0, "key_id": "k1", "model_group": "gpt-4o",
           "provider": "p1", "serving_model": "gpt-4o",
           "requests": {"bogus": 1}}
    doc["rollups"] = {"request_rollups": [good, bad]}

    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    rollups = r.json()["rollups"]
    assert isinstance(rollups, dict) and rollups.get("failed") == 1, (
        "a per-row failure must be counted, not swallowed — the operator sees "
        f"'0 written' and concludes there was nothing to restore: {rollups!r}")
    assert rollups["written"] == 1

    assert await _count(bare_client, "request_rollups", where="bucket_ts = 1000.0") == 1
    assert await _count(bare_client, "request_logs") == 1, (
        "one bad rollup row must not take the rest of the import down with it")


async def test_a_failed_stored_response_row_is_counted_and_the_rest_survives(
        client, bare_client):
    """``_apply_responses`` had no guard at all — one bad row 500'd the import
    after ``logs`` and ``rollups`` were already committed."""
    doc = await _export(client, sections="responses")
    doc["responses"] = {"stored_responses": [
        {"id": "resp_good", "created_at": time.time(), "key_id": "k1",
         "surface": "responses", "model_group": "gpt-4o",
         "input_json": "[]", "output_json": "{}"},
        {"id": "resp_bad", "key_id": "k1", "surface": "responses",
         "model_group": "gpt-4o", "input_json": "[]", "output_json": "{}"},
    ]}

    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    responses = r.json()["responses"]
    assert isinstance(responses, dict) and responses.get("failed") == 1, (
        f"responses must report its per-row failures: {responses!r}")
    assert responses["written"] == 1, (
        "``INSERT OR IGNORE`` drops a row that breaks a NOT NULL constraint "
        "without raising — counting that as written hides the loss")
    assert await _count(bare_client, "stored_responses",
                        where="id = :i", params={"i": "resp_good"}) == 1
    assert await _count(bare_client, "stored_responses",
                        where="id = :i", params={"i": "resp_bad"}) == 0
