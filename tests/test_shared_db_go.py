"""Shared-database interop between the Python backend and wiwi-go.

Both backends must be able to open one DATABASE_URL and read each other's
rows. This suite is the proof: Python seeds a SQLite file through its own DDL
and services, hands the SAME URL to a Go helper binary (built straight from
``wiwi-go/cmd/shared-db-interop``), and each side then verifies what the other
wrote.

Flow (one temp DB file throughout):

1. Python creates the schema and seeds key/user/price rows (AuthService,
   UserService, ConfigStore, DBSink — each runs its own DDL).
2. Go migrates (must be a no-op on Python's schema), reads Python's rows
   through Go's services, and writes a user + a key.
3. Python reads Go's rows through Python services, charges Go's key
   (update_spend), and disables the shared owner (owner revocation).
4. Go re-reads: spend visible, the revoked owner's key refused.

Skipped (not silently passed) when the Go toolchain is unavailable.
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.auth.service import AuthService
from wiwi.auth.users import UserService
from wiwi.logging_core.db_sink import DBSink
from wiwi.server.config_store import ConfigStore

REPO = Path(__file__).resolve().parent.parent
GO_DIR = REPO / "wiwi-go"
HELPER_DIR = GO_DIR / "cmd" / "shared-db-interop"

PY_USER = "pyinterop"
PY_PASSWORD = "interop-pass-1"
PY_KEY_ALIAS = "py-seeded"
GO_KEY_ALIAS = "go-minted"
PRICE_MODEL = "gpt-4o-interop"
# Every table Python's startup creates; the Go-first DB must carry them all.
PY_TABLES = {
    "vkeys", "users", "provider_keys", "deployments", "model_prices",
    "model_price_scopes", "settings", "request_logs", "audit_logs",
    "request_rollups", "sweep_locks", "providers", "stored_responses",
}


def _go_available() -> bool:
    return shutil.which("go") is not None


def _run(cmd, **kw):
    """Subprocess is blocking: run it on a worker thread when called async."""
    return asyncio.to_thread(subprocess.run, cmd, **kw)


async def _build_helper(tmp_path) -> tuple[Path, "subprocess.CompletedProcess"]:
    binary = tmp_path / "shared-db-interop"
    build = await asyncio.to_thread(
        subprocess.run,
        ["go", "build", "-o", str(binary), str(HELPER_DIR)],
        cwd=GO_DIR, capture_output=True, text=True, check=False)
    return binary, build


async def _seed_python_side(engine) -> dict:
    """Create the schema + seed rows exactly as a booted Python backend would."""
    auth = AuthService(engine, master_key_plaintext="sk-master-interop")
    await auth.startup()
    users = UserService(engine, session_secret="interop-secret")
    await users.startup()
    sink = DBSink(engine)
    await sink.startup()

    # A key Python mints, with a budget the Go side must read back.
    key_plain, _key_id = await auth.create_key(
        alias=PY_KEY_ALIAS, max_budget=5.0, models=[PRICE_MODEL])

    # A user Python creates (pbkdf2_sha256 hex, 200_000 iters) — Go must be
    # able to verify this record.
    user = await users.create_user(PY_USER, PY_PASSWORD)

    # A second key *owned* by that user, for the revocation phase.
    owned_plain, owned_id = await auth.create_key(
        alias="py-owned", owner_id=user.id, max_budget=5.0)
    assert await auth.authenticate(key_plain) is not None
    assert await auth.authenticate(owned_plain) is not None

    cfg = ConfigStore(engine)
    await cfg.startup()
    await cfg.upsert_price(PRICE_MODEL, {
        "input_cost_per_token": 1.5e-6,
        "output_cost_per_token": 6e-6,
    })
    return {"key_plain": key_plain, "owned_plain": owned_plain,
            "owned_id": owned_id, "user_id": user.id}


class TestSharedDbGoInterop:
    async def test_python_and_go_share_one_database(self, tmp_path):
        if not _go_available():
            pytest.skip("go toolchain not available")

        db_path = tmp_path / "wiwi-shared.db"
        # The exact SQLAlchemy URL shape the config would carry — the Go
        # store.Open() must resolve it to the same file.
        url = f"sqlite+aiosqlite:///{db_path}"

        engine = create_async_engine(url)
        try:
            seeded = await _seed_python_side(engine)

            # Mist a request-log row so the logging schema is live too.
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO request_logs (ts, status, tok_in, tok_out)"
                    " VALUES (:t, 200, 1, 1)"), {"t": 1.0})

            binary, build_t = await _build_helper(tmp_path)
            assert build_t.returncode == 0, build_t.stderr

            # Phase 1: Go reads Python's rows, writes its own user + key.
            env = {**os.environ,
                   "WIWI_INTEROP_KEY": seeded["key_plain"],
                   "WIWI_INTEROP_OWNED_KEY": seeded["owned_plain"]}
            r1 = await _run([str(binary), "seed-read", url],
                            capture_output=True, text=True, env=env)
            assert r1.returncode == 0, r1.stderr
            go_vals = dict(
                line.split("=", 1)
                for line in r1.stdout.splitlines() if "=" in line)
            go_key_plain = go_vals["GO_KEY_PLAINTEXT"]
            go_key_id = go_vals["GO_KEY_ID"]

            # Phase 2: Python reads Go's rows through its own services.
            auth = AuthService(engine, master_key_plaintext="sk-master-interop")
            info = await auth.authenticate(go_key_plain)
            assert info is not None, "Go-minted key does not authenticate on Python"
            assert info.alias == GO_KEY_ALIAS
            assert info.max_budget == 10.0
            assert await auth.get_key(go_key_id) is not None

            users = UserService(engine, session_secret="interop-secret")
            go_user = await users.verify("gointerop", "interop-pass-2")
            assert go_user is not None, "user created by Go cannot log in on Python"

            # Go's password row must be in Python's hash format.
            async with engine.connect() as conn:
                ph = (await conn.execute(text(
                    "SELECT password_hash FROM users WHERE username = :u"),
                    {"u": "gointerop"})).scalar_one()
            assert ph.startswith("pbkdf2_sha256$200000$"), ph

            # Python mutates Go's key (spend) and revokes the shared owner by
            # disabling the USER (the admin PATCH lever, AUDIT #148): the
            # key row stays, but _lookup_db's fail-closed owner check must
            # refuse it on both backends.
            assert await auth.update_spend(go_key_id, 1.25) is True
            users = UserService(engine, session_secret="interop-secret")
            updated = await users.patch(seeded["user_id"], disabled=True)
            assert updated is not None and updated["disabled"] is True
            assert await auth.authenticate(seeded["owned_plain"]) is None

            # Phase 3: Go re-reads — spend visible through Go's own SQL, and
            # the disabled owner's key is refused (fail-closed, AUDIT #148).
            env3 = {**os.environ,
                    "WIWI_INTEROP_OWNED_KEY": seeded["owned_plain"],
                    "WIWI_INTEROP_KEY_ID": go_key_id}
            r3 = await _run([str(binary), "go-verify", url],
                            capture_output=True, text=True, env=env3)
            assert r3.returncode == 0, r3.stderr
        finally:
            await engine.dispose()

    async def test_go_first_database_boots_python_services(self, tmp_path):
        """A DB Go creates and migrates must boot Python's services whole.

        Wiwi-go's migrate.go is a copy of the canonical DDL in wiwi/; if the
        copy drifts, Python's startup would fail (missing column/table).
        """
        if not _go_available():
            pytest.skip("go toolchain not available")

        fresh = tmp_path / "go-first.db"
        fresh_url = f"sqlite+aiosqlite:///{fresh}"

        binary, build_t = await _build_helper(tmp_path)
        assert build_t.returncode == 0, build_t.stderr
        ctx = await _run([str(binary), "go-verify", fresh_url],
                         capture_output=True, text=True,
                         env={"WIWI_INTEROP_OWNED_KEY": "missing",
                              "WIWI_INTEROP_KEY_ID": "missing"},
                         check=False)
        # The phase FAILS on the empty DB (no rows) — but it must have
        # migrated first. Only the tables matter here.
        assert ctx.returncode != 0

        import sqlite3
        go_tables = {r[0] for r in sqlite3.connect(fresh).execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = PY_TABLES - go_tables
        assert not missing, f"Go-first DB lacks tables Python needs: {missing}"

        # Python services must fully start on the Go-created file.
        engine2 = create_async_engine(fresh_url)
        try:
            auth = AuthService(engine2, master_key_plaintext="sk-x")
            await auth.startup()
            plain, _kid = await auth.create_key(alias="after-go")
            assert await auth.authenticate(plain) is not None
            users = UserService(engine2, session_secret="s")
            await users.startup()
            u = await users.create_user("aliceinterop", "alice-pass-1")
            assert await users.verify("aliceinterop", "alice-pass-1") is not None
            sink = DBSink(engine2)
            await sink.startup()
            cfg = ConfigStore(engine2)
            await cfg.startup()
            assert u.id
        finally:
            await engine2.dispose()

    def test_go_helper_source_is_wired_into_ci(self):
        """Guard: CI must actually run this suite; a dropped job would leave
        the interop untested on the main branch."""
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        assert "test_shared_db_go" in ci, (
            "shared-db interop suite is not wired into CI")
