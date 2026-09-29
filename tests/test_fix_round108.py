"""Round 108 — Postgres got 4-byte ``REAL`` where SQLite got 8-byte float.

The shared DDL constants spell a float column ``REAL``. SQLite maps ``REAL``
to an 8-byte float, but PostgreSQL maps it to **float4**. Every ``time.time()``
column (``created_at``/``updated_at``/``expires_at``) is ~1.79e9, where a
float4's ULP is 128 s: all rows created within the same ~2-minute block store
an *identical* timestamp, so ``ORDER BY created_at DESC`` (``expire_keys``
keep-newest, ``list_keys``/``list_users`` ordering, ``created_at`` compared
against a token's ``iat``) is arbitrary rather than newest-first. It also
floors anything compared after a SQL ``+`` into meaninglessness:
``1000.0 + 20 * 1e-6`` still reads back as ``1000.0`` on Postgres.

``tests/`` has no real-Postgres fixture, so nothing exercised these columns on
the dialect where they are narrow. These tests do, but only when a database is
offered — they skip otherwise, so the default (SQLite-only) gate stays green:

    docker run -d --name wiwi-pg -p 5433:5432 \\
        -e POSTGRES_PASSWORD=pg -e POSTGRES_USER=pg -e POSTGRES_DB=wiwi \\
        postgres:16-alpine
    WIWI_TEST_POSTGRES_URL=postgresql+asyncpg://pg:pg@127.0.0.1:5433/wiwi \\
        python3 -m pytest tests/test_fix_round108.py -q
"""

import os
import time
import uuid

import pytest
import sqlalchemy as sa
import sqlalchemy.ext.asyncio as saa

from wiwi.auth.service import AuthService
from wiwi.auth.users import UserService
from wiwi.logging_core.db_sink import DBSink
from wiwi.server.config_store import ConfigStore

PG_URL = os.environ.get("WIWI_TEST_POSTGRES_URL", "")

requires_pg = pytest.mark.skipif(
    not PG_URL, reason="set WIWI_TEST_POSTGRES_URL to run the Postgres leg")

# Every column that is a float in Python and was declared REAL in a shared DDL
# constant, with the table it lives on. request_logs/audit_logs/rollups are
# deliberately absent: their Postgres DDL variants have always used
# DOUBLE PRECISION.
NARROW_COLUMNS = {
    "users": ("created_at", "updated_at"),
    "vkeys": ("max_budget", "spend_to_date", "expires_at", "created_at",
              "updated_at"),
    "providers": ("timeout_s",),
    "model_prices": ("input_cost_per_token", "output_cost_per_token",
                     "cache_read_input_cost_per_token",
                     "cache_creation_input_cost_per_token"),
    "model_price_scopes": ("input_cost_per_token", "output_cost_per_token",
                           "cache_read_input_cost_per_token",
                           "cache_creation_input_cost_per_token"),
}


async def _engine(url: str):
    return saa.create_async_engine(url)


async def _start_all(engine):
    auth = AuthService(engine, "sk-master")
    users = UserService(engine, "session-secret")
    store = ConfigStore(engine)
    sink = DBSink(engine)
    await auth.startup()
    await users.startup()
    await store.startup()
    await sink.startup()
    return auth, users, store, sink


@requires_pg
async def test_postgres_float_columns_are_double_precision():
    """The schema itself: no float column on Postgres may be float4."""
    engine = await _engine(PG_URL)
    try:
        _, _, _, _ = await _start_all(engine)
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT table_name, column_name, data_type"
                " FROM information_schema.columns"
                " WHERE table_schema = current_schema()"))).all()
    finally:
        await engine.dispose()
    actual = {(t, c): dt for t, c, dt in rows}
    narrow = []
    for table, columns in NARROW_COLUMNS.items():
        for col in columns:
            dt = actual.get((table, col))
            if dt is not None and dt != "double precision":
                narrow.append(f"{table}.{col}={dt}")
    assert not narrow, (
        "float columns stored at postgres REAL (4-byte) precision: "
        + ", ".join(narrow))


@requires_pg
async def test_postgres_spend_accumulates_after_a_large_base():
    """``spend_to_date + 1e-6`` must not round to a no-op on Postgres.

    Budgets are sums of per-request costs, so a floor here is silent
    under-billing: the key spends forever without ever reaching its budget.
    """
    engine = await _engine(PG_URL)
    try:
        auth = AuthService(engine, "sk-master", max_keys_per_user=100)
        await auth.startup()
        _, kid = await auth.create_key(
            alias=f"spend-{uuid.uuid4().hex[:8]}", owner_id=None)
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE vkeys SET spend_to_date = 1000.0 WHERE id = :i"),
                {"i": kid})
        for _ in range(20):
            await auth.update_spend(kid, 1e-6)
        after = (await auth.get_key(kid))["spend_to_date"]
    finally:
        await engine.dispose()
    # float4 cannot represent 1000.00002 at all — it reads back as exactly
    # 1000.0, i.e. twenty recorded charges vanished.
    assert after > 1000.0, f"spend did not accumulate: {after!r}"


@requires_pg
async def test_postgres_key_timestamps_are_distinct_and_ordering_is_exact():
    """Keys minted seconds apart must differ, and keep-newest must keep them.

    ``expire_keys`` protects a per-owner cap by expiring the oldest keys; with
    a coarse ``created_at`` every key looks equally old and the wrong ones are
    retired — including the one the owner is actively using.
    """
    engine = await _engine(PG_URL)
    try:
        auth = AuthService(engine, "sk-master", max_keys_per_user=100)
        await auth.startup()
        alias = f"pg-{uuid.uuid4().hex[:8]}"
        made = []
        for _ in range(5):
            _, kid = await auth.create_key(alias=alias, owner_id=None)
            made.append(kid)
        async with engine.connect() as conn:
            stamps = [r[0] for r in (await conn.execute(sa.text(
                "SELECT created_at FROM vkeys WHERE key_alias = :a"),
                {"a": alias})).all()]
        assert len(set(stamps)) == len(stamps), (
            f"only {len(set(stamps))}/{len(stamps)} distinct created_at values "
            "for keys minted within the same second")

        await auth.expire_keys(owner_id=None, alias=alias, keep_newest=2)
        async with engine.connect() as conn:
            live = {r[0] for r in (await conn.execute(sa.text(
                "SELECT id FROM vkeys WHERE key_alias = :a"
                " AND (expires_at IS NULL OR expires_at > :now)"),
                {"a": alias, "now": time.time()})).all()}
        assert live == set(made[-2:]), (
            f"kept {sorted(live)}, expected the two newest {sorted(made[-2:])}")
    finally:
        await engine.dispose()


@requires_pg
async def test_postgres_expiry_survives_the_second_it_is_written():
    """A future expiry must stay in the future after a round-trip to the DB.

    Read through a *second* service so the answer comes from the column, not
    from the first service's in-memory cache — a float4 rounds a 0.5 s TTL
    down by up to 64 s, i.e. into the past, and the key is born expired.
    """
    engine = await _engine(PG_URL)
    try:
        auth = AuthService(engine, "sk-master", max_keys_per_user=100)
        await auth.startup()
        plaintext, kid = await auth.create_key(
            alias=f"ttl-{uuid.uuid4().hex[:8]}", owner_id=None, ttl_seconds=0.5)
        # Fresh instance => empty cache => the next lookup reads the column.
        reader = AuthService(engine, "sk-master", max_keys_per_user=100)
        await reader.startup()
        assert await reader.authenticate(plaintext) is not None, (
            "a 0.5 s TTL did not survive the round-trip: the stored expires_at "
            "was rounded into the past, so the key is born dead")
        await auth.delete_key(kid)
    finally:
        await engine.dispose()


@requires_pg
async def test_migration_ignores_a_same_named_table_in_another_schema():
    """A same-named table in another schema must not mask a missing column.

    The catalog queries filtered on ``table_name`` alone, so the column set was
    taken from *every* schema holding a table of that name. With a second
    deployment or another app in the same Postgres, a legacy ``vkeys`` in one
    schema was treated as already having ``owner_id`` because a *different*
    schema's ``vkeys`` had it — so the column was never added, and the request
    path then failed on the missing column. In the other direction (the decoy
    carrying a column the real table lacks) the ALTER raised
    ``UndefinedColumnError`` and startup died.

    Isolated schemas so it cannot disturb the other tests: the engine's
    ``search_path`` puts ``app_schema`` first, so ``current_schema()`` is the
    one the migration must trust, while the decoy lives in ``decoy_schema`` —
    which an unfiltered ``table_name`` query still sees.
    """
    app_schema, decoy_schema = "wiwi_r108_app", "wiwi_r108_decoy"
    setup = await _engine(PG_URL)
    try:
        async with setup.begin() as conn:
            for schema in (app_schema, decoy_schema):
                await conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
                await conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
        # The real table: a legacy vkeys with no owner_id (pre-migration shape).
        async with setup.begin() as conn:
            await conn.execute(sa.text(f"""
                CREATE TABLE {app_schema}.vkeys (
                  id TEXT PRIMARY KEY, key_hash TEXT UNIQUE NOT NULL,
                  key_alias TEXT NOT NULL DEFAULT '', models TEXT NOT NULL DEFAULT '[]',
                  max_budget DOUBLE PRECISION, spend_to_date DOUBLE PRECISION NOT NULL DEFAULT 0,
                  rpm INTEGER, tpm INTEGER, expires_at DOUBLE PRECISION,
                  disabled INTEGER NOT NULL DEFAULT 0,
                  created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL)
            """))
        # The decoy: same table name, other schema, and it HAS owner_id.
        async with setup.begin() as conn:
            await conn.execute(sa.text(
                f"CREATE TABLE {decoy_schema}.vkeys (id TEXT PRIMARY KEY,"
                " owner_id TEXT, expires_at REAL)"))
    finally:
        await setup.dispose()

    isolated = saa.create_async_engine(
        PG_URL, connect_args={"server_settings": {
            "search_path": f"{app_schema},public"}})
    try:
        # Pre-fix this either skipped the ALTER (owner_id present in the union)
        # or raised on a column the real table does not have.
        await _start_all(isolated)
        async with isolated.connect() as conn:
            cols = {r[0] for r in (await conn.execute(sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = :s AND table_name = 'vkeys'"),
                {"s": app_schema})).all()}
            decoy_float = (await conn.execute(sa.text(
                "SELECT data_type FROM information_schema.columns"
                " WHERE table_schema = :s AND table_name = 'vkeys'"
                " AND column_name = 'expires_at'"),
                {"s": decoy_schema})).scalar()
        assert "owner_id" in cols, (
            "owner_id was never added: the migration read the column list from "
            "another schema's vkeys")
        assert decoy_float == "real", "the other schema's table was altered"
    finally:
        await isolated.dispose()
        cleanup = await _engine(PG_URL)
        try:
            async with cleanup.begin() as conn:
                for schema in (app_schema, decoy_schema):
                    await conn.execute(sa.text(
                        f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        finally:
            await cleanup.dispose()
