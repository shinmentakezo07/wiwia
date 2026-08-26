# Public front + user accounts + role-based dashboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the admin-only wiwi console into a hybrid app: a public landing/catalog/docs front, username+password user accounts with DB persistence and HttpOnly session cookies, and role-based dashboards where normal users see a scoped subset and admins see the full console — with the dashboard moved off `/` into a guarded `/app/*` area.

**Architecture:** Backend-first. A new `wiwi/auth/users.py` owns a `users` table, password hashing (stdlib pbkdf2), and signed session cookies. `vkeys` and `request_logs` gain additive `owner_id`/`key_id` columns for ownership-scoped data access. Existing `/admin/*` handlers gain an `actor` concept: admin sees global data, user sees only their own keys' data. The frontend gets a `PublicLayout` for unauth pages (`/`, `/models`, `/docs`, `/playground`), `/login` + `/signup`, and a guarded `/app/*` tree where the sidebar is role-aware (6 pages for users, all pages for admins). New endpoints `/auth/*` handle signup/login/logout/me; `/admin/users` manages roles; `/public/models` is a secret-free catalog.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async (raw DDL, no ORM, SQLite + Postgres), orjson, structlog, stdlib `hashlib`/`hmac`/`secrets` (no new deps). Frontend: React 19, TypeScript, Vite, Tailwind 4, react-router-dom v7, TanStack Query, built with **bun**.

## Global Constraints

- No new Python dependencies. Password hashing via stdlib `hashlib.pbkdf2_hmac`; sessions signed via stdlib `hmac`+`hashlib` HKDF.
- No ORM. Raw DDL, dialect-portable (SQLite + Postgres), idempotent migrations matching `wiwi/server/config_store.py`'s pattern.
- Never add dialect/provider-specific branches in `core/`, `router/`, `auth/` core. New logic lives in `wiwi/auth/users.py` (user/session) and `wiwi/server/app.py` handlers (actor scoping).
- Async throughout; `orjson` in hot paths; never `print` — use `structlog`.
- Ruff line-length 100, target py311.
- All migrations additive + backward-compatible: nullable columns, default `''`/`NULL`. Existing master-key bearer auth and all `/admin/*` endpoints keep working.
- Tests: bare `async def test_...` (asyncio_mode=auto), no decorators. New user-account tests go in `tests/test_user_accounts.py`. Run `.venv/bin/python -m pytest tests/ -q` AND `.venv/bin/ruff check wiwi/ tests/` before claiming done or committing.
- Frontend: **bun** is authoritative (not npm). Run `cd web && bun run build` (which runs `tsc -b`) before claiming frontend work done.
- Never commit `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, `.verify/`.

---

## File Structure

### Backend (new + modified)
- **Create** `wiwi/auth/users.py` — `users` table DDL, `UserService` (signup/login/verify/list/patch), password hashing, session cookie sign/verify, `UserInfo` dataclass, `current_user(request)` resolver.
- **Modify** `wiwi/auth/service.py` — add `owner_id` column (DDL + migrate), carry `owner_id` through `create_key`/`_lookup_db`/`list_keys`; add `list_keys_for_owner(uid)` and `key_owner(key_id)`.
- **Modify** `wiwi/logging_core/events.py` — add `key_id: str = ""` to `LogEvent`.
- **Modify** `wiwi/logging_core/db_sink.py` — add `key_id` column (DDL + migrate), include in `_COLS`/`_row`/`read_requests`, add `key_id`-filtered overloads for `read_requests`/`read_overview`/`read_timeseries`.
- **Modify** `wiwi/server/app.py` — wire `UserService`, add `/auth/*` + `/admin/users` + `/public/models` endpoints, add `actor` resolution + owner-scoped filters on keys/logs/stats/models endpoints, stamp `key_id` into the request log event.
- **Create** `tests/test_user_accounts.py` — full backend regression suite.

### Frontend (new + modified)
- **Modify** `web/src/api/auth.tsx` — session/role model: `useAuth()` returns `{user, signup, loginMaster, loginUser, logout, refresh}`; hydrates via `/auth/me`.
- **Modify** `web/src/api/client.ts` — add `getUsers`, `patchUser`, `getPublicModels`, credential-omitting `apiNoAuth` fetch helper; `signupUser`/`loginUser`/`loginMaster`/`logoutSession`/`getMe`.
- **Modify** `web/src/api/types.ts` — add `User`, `PublicModelGroup` interfaces.
- **Modify** `web/src/main.tsx` — new route tree (public `/app`-less, guarded `/app/*`, `/login`, `/signup`, `/playground`, redirect map).
- **Modify** `web/src/components/Layout.tsx` — rename role-aware `AdminLayout`; filter `NAV_SECTIONS` by role; move page-meta keys to `/app/*`.
- **Create** `web/src/components/PublicLayout.tsx` — top-nav public shell.
- **Create** `web/src/components/guards.tsx` — `RequireUser`, `RequireAdmin` route guards.
- **Create** `web/src/pages/Landing.tsx`, `Signup.tsx`, `Users.tsx`, `Playground.tsx`, `ModelsCatalog.tsx`, `Docs.tsx`.
- **Modify** `web/src/pages/Login.tsx` — dual-mode (master key / username+password).
- **Modify** `web/src/pages/Models.tsx` — read-only for non-admin (hide weight/strategy controls).
- **Modify** `web/vite.config.ts` — proxy `/auth` → `localhost:4000`.

---

## Task 1: `users` table + password hashing (UserService core)

**Files:**
- Create: `wiwi/auth/users.py`
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Produces: `class UserService(engine: AsyncEngine, session_secret: str)` with `async def startup()`, `async def create_user(username, password) -> UserInfo`, `async def verify(username, password) -> UserInfo | None`, `async def get(uid) -> UserInfo | None`, `async def list_users() -> list[dict]`, `async def patch(uid, role=None, disabled=None) -> dict`. Dataclass `UserInfo(id, username, role, disabled)`. Module funcs `hash_password(pw) -> str`, `verify_password(pw, stored) -> bool`, `sign_session(secret, uid, role, expires) -> str`, `verify_session(secret, token) -> tuple[uid,role,expires] | None`.

- [ ] **Step 1: Write failing tests for hashing and user CRUD**

Create `tests/test_user_accounts.py`:

```python
"""User accounts + sessions + role-based scoping regression tests."""

import time

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.auth.users import (
    UserInfo, UserService, hash_password, verify_password,
    sign_session, verify_session,
)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.execute(sa.text("PRAGMA foreign_keys=ON"))
    return eng


async def test_password_hash_roundtrip():
    h = hash_password("hunter2long")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("hunter2long", h) is True
    assert verify_password("wrong", h) is False


async def test_session_sign_verify_roundtrip():
    secret = "s3cret"
    tok = sign_session(secret, "u1234567", "user", expires=1_000_000_000.0)
    assert verify_session(secret, tok) == ("u1234567", "user", 1_000_000_000.0)


async def test_session_rejects_tamper():
    secret = "s3cret"
    tok = sign_session(secret, "u1", "user", expires=2_000_000_000.0)
    tampered = tok[:-2] + ("00" if tok[-2:] != "00" else "11")
    assert verify_session(secret, tampered) is None


async def test_session_rejects_expired():
    secret = "s3cret"
    tok = sign_session(secret, "u1", "user", expires=1.0)
    assert verify_session(secret, tok) is None


async def test_create_user_stores_and_verifies():
    eng = await _engine()
    svc = UserService(eng, "secret")
    await svc.startup()
    u = await svc.create_user("alice", "password1")
    assert u.role == "user"
    assert u.disabled is False
    again = await svc.verify("alice", "password1")
    assert again is not None and again.id == u.id
    assert await svc.verify("alice", "nope") is None


async def test_create_user_duplicate_409():
    eng = await _engine()
    svc = UserService(eng, "secret")
    await svc.startup()
    await svc.create_user("bob", "password1")
    try:
        await svc.create_user("bob", "password1")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


async def test_list_and_patch_user():
    eng = await _engine()
    svc = UserService(eng, "secret")
    await svc.startup()
    u = await svc.create_user("carol", "password1")
    users = await svc.list_users()
    assert any(x["id"] == u.id for x in users)
    patched = await svc.patch(u.id, role="admin")
    assert patched["role"] == "admin"
    info = await svc.get(u.id)
    assert info.role == "admin"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: FAIL — `ModuleNotFoundError: wiwi.auth.users`

- [ ] **Step 3: Implement `wiwi/auth/users.py`**

```python
"""User accounts + signed session cookies.

No ORM; raw DDL (SQLite + Postgres), stdlib-only password hashing.
Sessions are stateless signed cookies; a users-row lookup per guarded
request validates the user still exists and is enabled.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets as _secrets
import time
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

PBKDF2_ITERS = 200_000
SESSION_TTL = 7 * 24 * 3600  # 7 days, seconds
USERNAME_RE = __import__("re").compile(r"^[a-zA-Z0-9_-]+$")


@dataclass
class UserInfo:
    id: str
    username: str
    role: str  # "user" | "admin"
    disabled: bool = False


USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
"""


def _user_id() -> str:
    return "u" + _secrets.token_hex(8)


def _now() -> float:
    return time.time()


# -- password hashing ---------------------------------------------------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS)
    return f"pbkdf2_sha256${PBKDF2_ITERS}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                   salt, int(iters))
    return hmac.compare_digest(computed, expected)


# -- session cookie signing ---------------------------------------------------

def _hkdf(ikm: str, length: int = 32) -> bytes:
    """Extract+expand a key via HMAC-SHA256 (RFC 5869, single-block)."""
    prk = hmac.new(b"wiwi-session-hkdf-salt", ikm.encode("utf-8"),
                   hashlib.sha256).digest()
    return hmac.new(prk, b"session", hashlib.sha256).digest()[:length]


def sign_session(secret: str, uid: str, role: str, expires: float) -> str:
    key = _hkdf(secret)
    payload = f"{uid}.{role}.{expires:.0f}"
    sig = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session(secret: str, token: str) -> tuple[str, str, float] | None:
    if not token or token.count(".") != 3:
        return None
    key = _hkdf(secret)
    uid, role, exp, sig = token.split(".")
    expected = hmac.new(key, f"{uid}.{role}.{exp}".encode("utf-8"),
                       hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        expires = float(exp)
    except ValueError:
        return None
    if expires <= _now():
        return None
    return uid, role, expires


# -- service ------------------------------------------------------------------

def _validate_username(username: str) -> str:
    u = (username or "").strip().lower()
    if not (3 <= len(u) <= 32) or not USERNAME_RE.match(u):
        raise ValueError("username must be 3-32 chars [a-zA-Z0-9_-]")
    return u


class UserService:
    def __init__(self, engine: AsyncEngine, session_secret: str) -> None:
        self.engine = engine
        self._secret = session_secret
        self._is_pg = engine.dialect.name == "postgresql"

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(USERS_DDL))
            await conn.execute(sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username"
                " ON users(username)"))

    async def create_user(self, username: str, password: str) -> UserInfo:
        uname = _validate_username(username)
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        uid = _user_id()
        now = _now()
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    sa.text("INSERT INTO users (id, username, password_hash,"
                            " role, disabled, created_at, updated_at)"
                            " VALUES (:id, :u, :h, 'user', 0, :t, :t)"),
                    {"id": uid, "u": uname, "h": hash_password(password), "t": now},
                )
        except IntegrityError as e:
            raise ValueError("username already taken") from e
        return UserInfo(id=uid, username=uname, role="user")

    async def verify(self, username: str, password: str) -> UserInfo | None:
        uname = _validate_username(username)
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT id, password_hash, role, disabled"
                        " FROM users WHERE username = :u"),
                {"u": uname},
            )).first()
        if row is None or not verify_password(password, row[1]):
            return None
        return UserInfo(id=row[0], username=uname, role=row[2],
                        disabled=bool(row[3]))

    async def get(self, uid: str) -> UserInfo | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT id, username, role, disabled FROM users WHERE id = :id"),
                {"id": uid},
            )).first()
        if row is None:
            return None
        return UserInfo(id=row[0], username=row[1], role=row[2],
                        disabled=bool(row[3]))

    async def list_users(self) -> list[dict]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                sa.text("SELECT id, username, role, disabled, created_at"
                        " FROM users ORDER BY created_at DESC"))).all()
        return [{"id": r[0], "username": r[1], "role": r[2],
                 "disabled": bool(r[3]), "created_at": r[4]} for r in rows]

    async def patch(self, uid: str, role: str | None = None,
                    disabled: bool | None = None) -> dict | None:
        sets: list[str] = []
        params: dict = {"id": uid, "t": _now()}
        if role is not None:
            if role not in ("user", "admin"):
                raise ValueError("role must be 'user' or 'admin'")
            sets.append("role = :role")
            params["role"] = role
        if disabled is not None:
            sets.append("disabled = :d")
            params["d"] = int(disabled)
        if not sets:
            return await self._one(uid)
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text(f"UPDATE users SET {', '.join(sets)}, updated_at = :t"
                        " WHERE id = :id"), params)
        return await self._one(uid)

    async def _one(self, uid: str) -> dict | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT id, username, role, disabled, created_at"
                        " FROM users WHERE id = :id"), {"id": uid})).first()
        if row is None:
            return None
        return {"id": row[0], "username": row[1], "role": row[2],
                "disabled": bool(row[3]), "created_at": row[4]}

    async def count_admins(self) -> int:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT COUNT(*) FROM users WHERE role = 'admin'"
                        " AND disabled = 0"))).one()
        return int(row[0])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/auth/users.py tests/test_user_accounts.py
git add wiwi/auth/users.py tests/test_user_accounts.py
git commit -m "Add UserService with pbkdf2 password hashing and signed sessions"
```

---

## Task 2: `vkeys.owner_id` migration + scoped key queries

**Files:**
- Modify: `wiwi/auth/service.py` (CREATE_SQL, `startup`, `create_key`, `_lookup_db`, `list_keys`; add `list_keys_for_owner`, `key_owner`)
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Consumes: `UserInfo.id` from Task 1.
- Produces: `AuthService.create_key(..., owner_id: str | None = None)`, `AuthService.list_keys_for_owner(uid) -> list[dict]`, `AuthService.key_owner(key_id) -> str | None` (returns owner_id or None for admin/system keys).

- [ ] **Step 1: Write failing tests for owner_id**

Append to `tests/test_user_accounts.py`:

```python
from wiwi.auth.service import AuthService


async def _auth_svc():
    eng = await _engine()
    svc = AuthService(eng, "master-key-plaintext")
    await svc.startup()
    return svc


async def test_create_key_stamps_owner():
    svc = await _auth_svc()
    plaintext, kid = await svc.create_key("alice-key", owner_id="u1")
    assert plaintext.startswith("sk-wiwi-")
    assert await svc.key_owner(kid) == "u1"


async def test_create_key_no_owner_is_admin():
    svc = await _auth_svc()
    _, kid = await svc.create_key("system-key")
    assert await svc.key_owner(kid) is None


async def test_list_keys_for_owner_filters():
    svc = await _auth_svc()
    _, k1 = await svc.create_key("a1", owner_id="u1")
    _, k2 = await svc.create_key("b1", owner_id="u2")
    _, k3 = await svc.create_key("sys")
    own1 = [k["id"] for k in await svc.list_keys_for_owner("u1")]
    assert own1 == [k1]
    all_ids = [k["id"] for k in await svc.list_keys()]
    assert {k1, k2, k3} <= set(all_ids)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k owner -q`
Expected: FAIL — `create_key() got unexpected keyword 'owner_id'`

- [ ] **Step 3: Modify `wiwi/auth/service.py`**

3a. In `CREATE_SQL`, add `owner_id TEXT` before `created_at`:
```python
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS vkeys (
  id TEXT PRIMARY KEY,
  key_hash TEXT UNIQUE NOT NULL,
  key_alias TEXT NOT NULL DEFAULT '',
  models TEXT NOT NULL DEFAULT '[]',
  max_budget REAL,
  spend_to_date REAL NOT NULL DEFAULT 0,
  rpm INTEGER,
  tpm INTEGER,
  expires_at REAL,
  disabled INTEGER NOT NULL DEFAULT 0,
  owner_id TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
"""
```

3b. In `startup()`, after the existing index, add idempotent migration + owner index:
```python
        # Additive migration: owner_id column + index (idempotent).
        if self._is_pg:
            cols = {r[0] for r in (await conn.execute(sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'vkeys'"))).all()}
        else:
            cols = {r[1] for r in (await conn.execute(
                sa.text("PRAGMA table_info(vkeys)"))).all()}
        if "owner_id" not in cols:
            await conn.execute(sa.text(
                "ALTER TABLE vkeys ADD COLUMN owner_id TEXT"))
        await conn.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS idx_vkeys_owner ON vkeys(owner_id)"))
```

3c. Change `create_key` signature to add `owner_id: str | None = None` and include it in the INSERT: add `"owner_id"` to the column list, `:owner` to the values, and `"owner": owner_id` to the params dict.

3d. In `_lookup_db`, add `owner_id` to the SELECT and `AuthInfo` return — extend `AuthInfo` with `owner_id: str | None = None`.

3e. In `list_keys`, add `owner_id` to the SELECT and the returned dict.

3f. Add two methods to `AuthService`:
```python
    async def list_keys_for_owner(self, owner_id: str) -> list[dict]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                sa.text("SELECT id, key_alias, models, max_budget, spend_to_date,"
                        " rpm, tpm, expires_at, disabled FROM vkeys"
                        " WHERE owner_id = :o ORDER BY created_at DESC"),
                {"o": owner_id})).all()
        import json as _json
        return [{"id": r[0], "alias": r[1], "models": _json.loads(r[2]),
                 "max_budget": r[3], "spend_to_date": r[4], "rpm": r[5],
                 "tpm": r[6], "expires_at": r[7], "disabled": bool(r[8])}
                for r in rows]

    async def key_owner(self, key_id: str) -> str | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT owner_id FROM vkeys WHERE id = :id"),
                {"id": key_id})).first()
        return row[0] if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/auth/service.py tests/test_user_accounts.py
git add wiwi/auth/service.py tests/test_user_accounts.py
git commit -m "Add vkeys.owner_id column and owner-scoped key queries"
```

---

## Task 3: `request_logs.key_id` migration + filtered reads

**Files:**
- Modify: `wiwi/logging_core/events.py` (add `key_id` field)
- Modify: `wiwi/logging_core/db_sink.py` (DDL, `_COLS`, `_row`, `read_requests`, `read_overview`, `read_timeseries` filtered overloads)
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Consumes: `AuthInfo.key_id` (existing).
- Produces: `LogEvent.key_id: str`, `DBSink.read_requests(limit, key_ids=None)`, `DBSink.read_overview(minutes, key_ids=None)`, `DBSink.read_timeseries(bs, metric, minutes, key_ids=None)` — when `key_ids` is a non-empty list, queries add `AND key_id IN (...)`; `None`/`[]` = unfiltered (admin).

- [ ] **Step 1: Write failing test for key_id filtering**

Append to `tests/test_user_accounts.py`:

```python
from wiwi.logging_core.events import LogEvent
from wiwi.logging_core.db_sink import DBSink


async def _sink():
    eng = await _engine()
    sink = DBSink(eng)
    await sink.startup()
    return eng, sink


def _req_event(key_id: str, cost: float = 0.01, ts: float | None = None) -> LogEvent:
    return LogEvent(stream="request", ts=ts if ts else time.time(),
                    key_id=key_id, cost=cost, tok_in=10, tok_out=5,
                    status=200, model_group="gpt-4o")


async def test_request_log_carries_key_id_and_filters():
    eng, sink = await _sink()
    await sink.write_requests([_req_event("k1", 0.10), _req_event("k2", 0.20)])
    all_logs = await sink.read_requests(100)
    assert {l["key_id"] for l in all_logs} == {"k1", "k2"}
    only_k1 = await sink.read_requests(100, key_ids=["k1"])
    assert {l["key_id"] for l in only_k1} == {"k1"}
    ov_all = await sink.read_overview(0)
    assert ov_all["requests"] == 2
    ov_k2 = await sink.read_overview(0, key_ids=["k2"])
    assert ov_k2["requests"] == 1
    assert round(ov_k2["cost"], 2) == 0.20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k key_id -q`
Expected: FAIL — `LogEvent has no key_id` / `unexpected keyword 'key_ids'`

- [ ] **Step 3: Implement changes**

3a. `events.py`: add `key_id: str = ""` to `LogEvent` after `key_alias`.

3b. `db_sink.py`:
- In both `request_logs` DDL strings (SQLite + PG), add `key_id TEXT DEFAULT ''` after `key_alias`.
- In `startup()`'s migration block, add idempotent `ALTER TABLE request_logs ADD COLUMN key_id TEXT DEFAULT ''` (guarded by column-existence check, same pattern as Task 2).
- Add `CREATE INDEX IF NOT EXISTS idx_request_logs_key_id ON request_logs(key_id)`.
- Add `"key_id"` to `_COLS` (insert it right after `"key_alias"`).
- In `_row()`/`write_requests`, `key_id` now flows automatically since `_row` builds from `_COLS`. Ensure `_row` includes `evt.key_id`.
- Change `read_requests`, `read_overview`, `read_timeseries` to accept `key_ids: list[str] | None = None`. Build a `key_filter` clause: when `key_ids` is a non-empty list, use `AND key_id IN :kids` with `sa.bindparams` expansion (use `sa.text(...).bindparams(sa.bindparam("kids", expanding=True))` and `{"kids": key_ids}`) so the IN-list is safe. Add the clause to every `WHERE`/`sample_*` query in those methods. When `key_ids` is `None` or empty, skip the clause (admin/unfiltered).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k key_id -q`
Expected: PASS

- [ ] **Step 5: Run full suite + lint + commit**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check wiwi/logging_core/ tests/test_user_accounts.py
git add wiwi/logging_core/events.py wiwi/logging_core/db_sink.py tests/test_user_accounts.py
git commit -m "Add request_logs.key_id and key_id-filtered read queries"
```

---

## Task 4: Stamp `key_id` into the request log event

**Files:**
- Modify: `wiwi/server/app.py` (where `LogEvent` for request stream is constructed, in the chat/messages/responses handlers)

**Interfaces:**
- Consumes: `AuthInfo.key_id` resolved in `authenticate()`.
- Produces: every request log row has `key_id` populated (the virtual key id, or `"master"` for the master key).

- [ ] **Step 1: Write failing test that a real request logs key_id**

Append to `tests/test_user_accounts.py`:

```python
import respx
from httpx import ASGITransport, AsyncClient
from asgi_lifespan import LifespanManager

from wiwi.server.app import create_app_from_config_path
from wiwi.config import load_config


async def _client_for_config(tmp_path, config_yaml: str):
    cfg_path = tmp_path / "wiwi.yaml"
    cfg_path.write_text(config_yaml)
    app = create_app_from_config_path(str(cfg_path))
    await LifespanManager(app)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


_CONFIG = """
general_settings:
  master_key: sk-master-test-123
model_list:
  - model_name: gpt-4o
    wiwi_params:
      provider: openai
      model: gpt-4o
providers:
  openai:
    api_key: sk-upstream-fake
"""


@respx.mock
async def test_request_logs_key_id_for_virtual_key(tmp_path):
    # Create a virtual key via master, then make a chat call with it.
    client = await _client_for_config(tmp_path, _CONFIG)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant",
              "content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }))
    r = await client.post("/admin/keys/generate", json={"name": "test"},
                          headers={"Authorization": "Bearer sk-master-test-123"})
    vkey = r.json()["key"]
    r2 = await client.post("/v1/chat/completions",
                           json={"model": "gpt-4o",
                                 "messages": [{"role": "user", "content": "hi"}]},
                           headers={"Authorization": f"Bearer {vkey}"})
    assert r2.status_code == 200
    logs = (await client.get("/admin/logs/requests",
             headers={"Authorization": "Bearer sk-master-test-123"})).json()["logs"]
    assert any(l["key_id"] and l["key_id"].startswith("k") for l in logs)
    await client.aclose()
```

(Note: add `import httpx` at the top of the file alongside the existing imports.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k key_id_for_virtual -q`
Expected: FAIL — `key_id` is `""` in the logged row.

- [ ] **Step 3: Stamp key_id where the request LogEvent is built**

Find the place(s) in `wiwi/server/app.py` where the request-stream `LogEvent` is constructed (search for `LogEvent(` with `stream="request"` or the `state.logs.log_request` call). Pass `key_id=info.key_id` (or `key_id=ctx.auth.key_id`) into the event. For the master key, `AuthInfo.key_id` is already `"master"`. If the event is built from a `RequestContext`, ensure `ctx.auth.key_id` is threaded to the event constructor. Do NOT branch on dialect/provider.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k key_id_for_virtual -q`
Expected: PASS

- [ ] **Step 5: Full suite + lint + commit**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check wiwi/server/app.py tests/test_user_accounts.py
git add wiwi/server/app.py tests/test_user_accounts.py
git commit -m "Stamp virtual key_id into request log events"
```

---

## Task 5: Wire `UserService` + session resolution into the app

**Files:**
- Modify: `wiwi/server/app.py` (AppState.init_db, session secret, `current_user` resolver, `actor` helpers)

**Interfaces:**
- Consumes: `UserService`, `sign_session`/`verify_session` from Task 1.
- Produces: `AppState.users: UserService`; `current_user(request: Request) -> UserInfo | None` (resolves cookie OR bearer master key → synthetic admin `UserInfo(id="master", role="admin")`); `require_user_dep`/`require_admin_dep` returning a 401/403 `ORJSONResponse | None` (matching the `_require_admin` pattern at app.py:876).

- [ ] **Step 1: Write failing test for current_user resolution**

Append to `tests/test_user_accounts.py`:

```python
async def test_master_key_resolves_to_admin_via_bearer(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    me = (await client.get("/auth/me",
            headers={"Authorization": "Bearer sk-master-test-123"})).json()
    assert me["user"]["role"] == "admin"
    assert me["user"]["id"] == "master"
    await client.aclose()


async def test_auth_me_null_when_anonymous(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    me = (await client.get("/auth/me")).json()
    assert me["user"] is None
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k "master_key_resolves or null_when_anonymous" -q`
Expected: FAIL — `/auth/me` 404 (not yet defined; that's Task 6, but the resolver is needed first) — OR a 404. Proceed to implement the resolver here; the endpoint in Task 6 will use it.

- [ ] **Step 3: Wire UserService + current_user into app.py**

3a. In `AppState.init_db()` (around app.py:228, after `self.auth = AuthService(...)` / `await self.auth.startup()`), add:
```python
        from wiwi.auth.users import UserService
        mk = self.config.general_settings.master_key or ""
        session_secret = os.environ.get("WIWI_SESSION_SECRET") or mk or "wiwi-default-session-secret"
        if not os.environ.get("WIWI_SESSION_SECRET") and mk:
            import structlog as _sl
            _sl.get_logger("wiwi.startup").info(
                "session_secret_derived_from_master_key")
        self.users = UserService(aengine, session_secret)
        await self.users.startup()
```
Add `import os` if not already present at the top of the module.

3b. Inside `create_app` (where `is_admin`, `bearer`, `_require_admin` live), add a `current_user` resolver and role guards:
```python
    def current_user(request: Request):
        """Resolve the caller from a signed session cookie OR a bearer
        master key (back-compat). Returns None when anonymous."""
        # master key via bearer → synthetic admin
        mk = config.general_settings.master_key
        if mk and hmac.compare_digest(bearer(request).encode(), mk.encode()):
            return UserInfo(id="master", username="master", role="admin")
        # session cookie
        cookie = request.cookies.get("wiwi_session")
        if not cookie:
            return None
        parsed = verify_session(state.users._secret, cookie)
        if parsed is None:
            return None
        uid, role, _exp = parsed
        if uid == "master":
            return UserInfo(id="master", username="master", role="admin")
        info = None
        if state.users is not None:
            import asyncio
            info = asyncio.get_event_loop().run_until_complete(
                state.users.get(uid)) if False else None  # placeholder
        return info
```
⚠️ The above placeholder is wrong for async context — implement `current_user` as an `async def` that `await`s `state.users.get(uid)` and checks `info is None or info.disabled` → return None. Keep `is_admin` and `bearer` as-is. Add `require_user_dep(request)` (async) returning 401 response or None, and `require_admin_dep(request)` returning 403 when present-but-not-admin. Mirror the `_require_admin` helper shape. Import `UserInfo`, `verify_session` at the top of `create_app`'s scope.

3c. Add `users: UserService | None = None` to the `AppState` dataclass/`__init__` (find the `self.auth = None` / `self.config_store = None` defaults near the top of `AppState`).

- [ ] **Step 4: Run tests** — they still 404 (endpoint is Task 6). That's expected; do NOT commit yet. Proceed to Task 6 which adds the `/auth/me` endpoint, then both tasks verify together.

---

## Task 6: `/auth/*` endpoints (signup, login, logout, me)

**Files:**
- Modify: `wiwi/server/app.py` (new `/auth/*` route handlers)
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Consumes: `UserService`, `current_user`, `sign_session`, `verify_session`.
- Produces: `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`. Login accepts `{username,password}` or `{master_key}`. Sets/clears `wiwi_session` cookie.

- [ ] **Step 1: Write failing tests for the auth flow**

Append to `tests/test_user_accounts.py`:

```python
async def test_signup_sets_cookie_and_creates_user(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/signup", json={"username": "dave",
                                                "password": "password1"})
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "user"
    assert "wiwi_session" in r.cookies
    me = (await client.get("/auth/me")).json()
    assert me["user"]["username"] == "dave"
    await client.aclose()


async def test_signup_duplicate_409(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "eve",
                                            "password": "password1"})
    r = await client.post("/auth/signup", json={"username": "eve",
                                                "password": "password2"})
    assert r.status_code == 409
    await client.aclose()


async def test_signup_short_password_400(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/signup", json={"username": "frank",
                                                "password": "short"})
    assert r.status_code == 400
    await client.aclose()


async def test_login_user_success(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "grace",
                                            "password": "password1"})
    r = await client.post("/auth/login", json={"username": "grace",
                                               "password": "password1"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "user"
    await client.aclose()


async def test_login_user_wrong_password_401(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "heidi",
                                            "password": "password1"})
    r = await client.post("/auth/login", json={"username": "heidi",
                                               "password": "nope"})
    assert r.status_code == 401
    await client.aclose()


async def test_login_master_key_sets_admin(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    assert r.status_code == 200
    assert r.json()["user"] == {"id": "master", "username": "master",
                                "role": "admin"}
    await client.aclose()


async def test_logout_clears_cookie(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "ivan",
                                            "password": "password1"})
    r = await client.post("/auth/logout")
    assert r.status_code == 200
    me = (await client.get("/auth/me")).json()
    assert me["user"] is None
    await client.aclose()


async def test_session_cookie_tamper_rejected(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "judy",
                                            "password": "password1"})
    # Tamper the cookie.
    tok = client.cookies.get("wiwi_session")
    client.cookies.set("wiwi_session", tok[:-4] + "0000",
                       domain="t", path="/")
    me = (await client.get("/auth/me")).json()
    assert me["user"] is None
    await client.aclose()


async def test_disabled_user_session_rejected(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/signup", json={"username": "karl",
                                                "password": "password1"})
    uid = r.json()["user"]["id"]
    await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    await client.patch(f"/admin/users/{uid}", json={"disabled": True})
    # New client with karl's old cookie: simulate by re-logging in as karl first
    await client.post("/auth/logout")
    await client.post("/auth/login", json={"username": "karl",
                                           "password": "password1"})
    # disabled now → me should be null
    me = (await client.get("/auth/me")).json()
    assert me["user"] is None
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k "signup or login or logout or tamper or disabled_user" -q`
Expected: FAIL — 404s on `/auth/*`.

- [ ] **Step 3: Implement `/auth/*` endpoints**

In `create_app`, add a cookie helper and the four endpoints:
```python
    def _set_session_cookie(resp, uid: str, role: str):
        import time as _t
        tok = sign_session(state.users._secret, uid, role,
                           expires=_t.time() + SESSION_TTL)
        resp.set_cookie("wiwi_session", tok, max_age=SESSION_TTL,
                        httponly=True, samesite="lax", secure=request_is_https,
                        path="/")

    def _clear_session_cookie(resp):
        resp.delete_cookie("wiwi_session", path="/")
```
(`request_is_https` is `request.url.scheme == "https"` — capture per-request inside the handler, not globally; pass `secure=request.url.scheme == "https"`.)

Endpoints:
```python
    @app.post("/auth/signup")
    async def auth_signup(request: Request):
        body, jerr = await json_body(request)
        if jerr: return jerr
        try:
            u = await state.users.create_user(
                body.get("username", ""), body.get("password", ""))
        except ValueError as e:
            import orjson as _oj
            # distinguish duplicate (409) from validation (400)
            if "already taken" in str(e):
                return _err(409, "conflict", str(e), request)
            return _err(400, "invalid_request_error", str(e), request)
        resp = ORJSONResponse({"user": {"id": u.id, "username": u.username,
                                         "role": u.role}}, status_code=201)
        _set_session_cookie(resp, u.id, u.role, request.url.scheme == "https")
        return resp

    @app.post("/auth/login")
    async def auth_login(request: Request):
        body, jerr = await json_body(request)
        if jerr: return jerr
        mk = body.get("master_key")
        if mk:
            if mk and hmac.compare_digest(str(mk).encode(),
                                          (config.general_settings.master_key or "").encode()):
                resp = ORJSONResponse({"user": {"id": "master", "username": "master",
                                                 "role": "admin"}})
                _set_session_cookie(resp, "master", "admin", request.url.scheme == "https")
                return resp
            return _err(401, "authentication_error", "invalid master key", request)
        u = await state.users.verify(body.get("username", ""), body.get("password", ""))
        if u is None or u.disabled:
            return _err(401, "authentication_error", "invalid credentials", request)
        resp = ORJSONResponse({"user": {"id": u.id, "username": u.username,
                                         "role": u.role}})
        _set_session_cookie(resp, u.id, u.role, request.url.scheme == "https")
        return resp

    @app.post("/auth/logout")
    async def auth_logout(request: Request):
        resp = ORJSONResponse({"ok": True})
        _clear_session_cookie(resp)
        return resp

    @app.get("/auth/me")
    async def auth_me(request: Request):
        u = await current_user(request)
        if u is None:
            return ORJSONResponse({"user": None})
        return ORJSONResponse({"user": {"id": u.id, "username": u.username,
                                        "role": u.role}})
```
Fix the `_set_session_cookie`/`_clear_session_cookie` signatures to take `request` for `secure=` rather than a captured global. Refactor `current_user` to `async def` and `await state.users.get(uid)` (replace the placeholder from Task 5).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS (all auth tests including the Task 5 me-resolver tests)

- [ ] **Step 5: Full suite + lint + commit**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check wiwi/server/app.py wiwi/auth/users.py tests/test_user_accounts.py
git add wiwi/server/app.py tests/test_user_accounts.py
git commit -m "Add /auth signup/login/logout/me endpoints with session cookies"
```

---

## Task 7: `/admin/users` endpoints (list + patch roles, last-admin guard)

**Files:**
- Modify: `wiwi/server/app.py`
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Consumes: `UserService.list_users`, `patch`, `count_admins`.
- Produces: `GET /admin/users` (admin), `PATCH /admin/users/{id}` (admin, `{role?, disabled?}`); rejects demoting/disabling the last enabled admin (400).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_user_accounts.py`:

```python
async def test_admin_lists_users(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "leo", "password": "password1"})
    await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    r = await client.get("/admin/users")
    assert r.status_code == 200
    assert any(u["username"] == "leo" for u in r.json()["users"])
    await client.aclose()


async def test_promote_user_to_admin(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/signup", json={"username": "mia", "password": "password1"})
    uid = r.json()["user"]["id"]
    await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    r2 = await client.patch(f"/admin/users/{uid}", json={"role": "admin"})
    assert r2.status_code == 200
    assert r2.json()["role"] == "admin"
    await client.aclose()


async def test_cannot_demote_last_admin_400(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    # promote one user to admin, then try to demote them (only admin)
    r = await client.post("/auth/signup", json={"username": "nia", "password": "password1"})
    uid = r.json()["user"]["id"]
    await client.patch(f"/admin/users/{uid}", json={"role": "admin"})
    r2 = await client.patch(f"/admin/users/{uid}", json={"role": "user"})
    assert r2.status_code == 400
    await client.aclose()


async def test_user_cannot_access_admin_users_403(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "oscar", "password": "password1"})
    r = await client.get("/admin/users")
    assert r.status_code == 403
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k "lists_users or promote or last_admin or admin_users_403" -q`
Expected: FAIL — 404s.

- [ ] **Step 3: Implement `/admin/users` endpoints**

Add a helper `_require_admin_dep(request)` returning a 403 response when `current_user` is not admin (and 401 when not authed at all). Note: existing admin endpoints use `is_admin` (bearer master key). For the new endpoints, accept EITHER a bearer master key OR an admin session:
```python
    async def _actor(request: Request):
        u = await current_user(request)
        return u  # UserInfo | None
```
The key list/logs/stats endpoints (Task 8) will use `_actor` to scope. For `/admin/users`, require admin:
```python
    @app.get("/admin/users")
    async def admin_list_users(request: Request):
        resp = await _require_admin_resp(request)
        if resp: return resp
        return ORJSONResponse({"users": await state.users.list_users()})

    @app.patch("/admin/users/{uid}")
    async def admin_patch_user(uid: str, request: Request):
        resp = await _require_admin_resp(request)
        if resp: return resp
        body, jerr = await json_body(request)
        if jerr: return jerr
        role = body.get("role")
        disabled = body.get("disabled")
        # Last-admin guard: if demoting or disabling an admin would leave zero.
        if role == "user" or disabled is True:
            cur = await state.users.get(uid)
            if cur is not None and cur.role == "admin" and not cur.disabled:
                if await state.users.count_admins() <= 1:
                    return _err(400, "invalid_request_error",
                                "cannot demote or disable the last admin", request)
        try:
            updated = await state.users.patch(uid, role=role, disabled=disabled)
        except ValueError as e:
            return _err(400, "invalid_request_error", str(e), request)
        if updated is None:
            return _err(404, "not_found_error", "user not found", request)
        await state.logs.log_audit(actor="master", action="user.update", target=uid,
                                   diff={"role": role, "disabled": disabled})
        return ORJSONResponse(updated)
```
where `_require_admin_resp` returns 401 if no actor, 403 if actor.role != "admin".

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + lint + commit**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check wiwi/server/app.py tests/test_user_accounts.py
git add wiwi/server/app.py tests/test_user_accounts.py
git commit -m "Add /admin/users endpoints with last-admin guard"
```

---

## Task 8: Owner-scoped `/admin/keys`, `/admin/logs/requests`, stats, models PATCH

**Files:**
- Modify: `wiwi/server/app.py` (keys list/generate/delete/disable/patch, request logs, stats overview/timeseries, models PATCH — add actor scoping)
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Consumes: `current_user`, `AuthService.list_keys_for_owner`/`key_owner`, `DBSink.read_requests/read_overview/read_timeseries(key_ids=...)`.
- Produces: the scoped endpoints return user-filtered data when the actor is a user (not admin); key creation stamps `owner_id`; users get 403 operating on others' keys or hitting PATCH models.

- [ ] **Step 1: Write failing tests for scoping**

Append to `tests/test_user_accounts.py`:

```python
@respx.mock
async def test_user_keys_scoped(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    # user A signup
    await client.post("/auth/signup", json={"username": "a1", "password": "password1"})
    r_a = await client.post("/admin/keys/generate", json={"name": "ka"})
    kid_a = r_a.json()["id"]
    # logout, signup B
    await client.post("/auth/logout")
    await client.post("/auth/signup", json={"username": "b1", "password": "password1"})
    r_b = await client.post("/admin/keys/generate", json={"name": "kb"})
    kid_b = r_b.json()["id"]
    # B lists keys → only kb
    ids = [k["id"] for k in (await client.get("/admin/keys")).json()["keys"]]
    assert ids == [kid_b]
    assert kid_a not in ids
    await client.aclose()


async def test_user_cannot_patch_others_key_403(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "a2", "password": "password1"})
    r_a = await client.post("/admin/keys/generate", json={"name": "ka"})
    kid_a = r_a.json()["id"]
    await client.post("/auth/logout")
    await client.post("/auth/signup", json={"username": "b2", "password": "password1"})
    r = await client.patch(f"/admin/keys/{kid_a}", json={"max_budget": 5})
    assert r.status_code == 403
    await client.aclose()


@respx.mock
async def test_request_logs_scoped_by_key_id(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "x", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant",
              "content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3}}))
    # user A
    await client.post("/auth/signup", json={"username": "la", "password": "password1"})
    ka = (await client.post("/admin/keys/generate", json={"name": "ka"})).json()["key"]
    await client.post("/v1/chat/completions", json={"model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {ka}"})
    await client.post("/auth/logout")
    # user B
    await client.post("/auth/signup", json={"username": "lb", "password": "password1"})
    kb = (await client.post("/admin/keys/generate", json={"name": "kb"})).json()["key"]
    await client.post("/v1/chat/completions", json={"model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {kb}"})
    logs = (await client.get("/admin/logs/requests")).json()["logs"]
    assert len(logs) == 1  # only B's
    await client.aclose()


async def test_models_patch_admin_only_403_for_user(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "ua", "password": "password1"})
    models = (await client.get("/admin/models")).json()
    group = models["groups"][0]["name"]
    r = await client.patch(f"/admin/model-groups/{group}",
                           json={"strategy": "least-busy"})
    assert r.status_code == 403
    await client.aclose()


async def test_admin_sees_all_keys(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "z1", "password": "password1"})
    await client.post("/admin/keys/generate", json={"name": "kz"})
    await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    ids = [k["id"] for k in (await client.get("/admin/keys")).json()["keys"]]
    assert len(ids) >= 1
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k "scoped or others_key or scoped_by_key_id or admin_only_403 or admin_sees_all" -q`
Expected: FAIL — users currently can't reach `/admin/keys` at all (only `is_admin` bearer), and no owner filtering.

- [ ] **Step 3: Implement actor scoping**

3a. Add an `_actor` helper returning `UserInfo | None` (from `current_user`). For each scoped endpoint, replace `if not is_admin(request): return _err(401...)` with:
```python
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
```
Then branch: `if actor.role == "admin": ...global... else: ...scoped...`.

3b. **Keys list** (`/admin/keys`): admin → `list_keys()`; user → `list_keys_for_owner(actor.id)`.

3c. **Keys generate** (`/admin/keys/generate`): pass `owner_id=None if actor.role == "admin" else actor.id` into `state.auth.create_key(...)`. Audit actor = `actor.username`.

3d. **Keys delete/disable/patch** (`/admin/keys/{key_id}`...): if user, check `await state.auth.key_owner(key_id)` — if not `actor.id`, return `_err(403, "permission_error", "not your key", request)`. Admin bypasses.

3e. **Request logs** (`/admin/logs/requests`): if user, compute `kids = [k["id"] for k in await state.auth.list_keys_for_owner(actor.id)]` and pass `key_ids=kids` to `sink.read_requests(limit, key_ids=kids)`; also filter the ring-fallback path by `e.key_id in kids`. Admin → unfiltered.

3f. **Stats overview/timeseries**: if user, compute `kids` and: for DB-backed path pass `key_ids=kids`; for ring path filter `_request_events()` to `[e for e in evs if e.key_id in kids]` before calling `stats_mod.overview/timeseries`.

3g. **Models PATCH** (`/admin/model-groups/{name}`): if `actor.role != "admin"`, return `_err(403, "permission_error", "admin only", request)`. **Models GET** (`/admin/models`): allow any authed actor (read).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS

- [ ] **Step 5: Full suite + lint + commit**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check wiwi/server/app.py tests/test_user_accounts.py
git add wiwi/server/app.py tests/test_user_accounts.py
git commit -m "Scope keys, request logs, stats, and models PATCH by user actor"
```

---

## Task 9: `/public/models` secret-free catalog endpoint

**Files:**
- Modify: `wiwi/server/app.py`
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Produces: `GET /public/models` (no auth) → `{groups: [{name, providers: [{provider, model_id}], context_window?, pricing?}]}`, omitting health/inflight/cooldown/weights.

- [ ] **Step 1: Write failing test**

Append:
```python
async def test_public_models_no_secrets_and_no_auth(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.get("/public/models")
    assert r.status_code == 200
    body = r.json()
    g = body["groups"][0]
    assert "name" in g
    assert "deployments" not in g or all("inflight" not in d for d in g.get("deployments", []))
    # no auth required
    assert r.status_code == 200
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails** (404)

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k public_models -q`

- [ ] **Step 3: Implement `/public/models`**

Reuse the existing `/admin/models` data builder but strip secret/health fields. Add a new unauthenticated handler that returns `{groups: [{name, deployments: [{provider, model_id}]}], aliases}` — no weights, inflight, p95, cooldown. No `is_admin`/actor check.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -k public_models -q`
Expected: PASS

- [ ] **Step 5: Full suite + lint + commit**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check wiwi/server/app.py tests/test_user_accounts.py
git add wiwi/server/app.py tests/test_user_accounts.py
git commit -m "Add /public/models secret-free model catalog endpoint"
```

---

## Task 10: Vite proxy `/auth` + frontend auth context + types

**Files:**
- Modify: `web/vite.config.ts`, `web/src/api/auth.tsx`, `web/src/api/client.ts`, `web/src/api/types.ts`

**Interfaces:**
- Produces: `useAuth()` returning `{user: User|null, signup, loginUser, loginMaster, logout, refresh}`; `User` type; `getUsers`/`patchUser`/`getPublicModels` client helpers.

- [ ] **Step 1: Add `/auth` to the dev proxy**

In `web/vite.config.ts` `server.proxy`, add `"/auth": "http://localhost:4000"` alongside `/admin`, `/v1`, `/health`.

- [ ] **Step 2: Add `User` + `PublicModelGroup` types to `web/src/api/types.ts`**

```typescript
export interface User {
  id: string;
  username: string;
  role: "user" | "admin";
}

export interface PublicModelGroup {
  name: string;
  deployments: { provider: string; model_id: string }[];
  aliases: Record<string, string>;
}
```

- [ ] **Step 3: Add client helpers to `web/src/api/client.ts`**

```typescript
export const getMe = () => api<{ user: User | null }>("/auth/me");
export const signupUser = (body: { username: string; password: string }) =>
  api<{ user: User }>("/auth/signup", { method: "POST", body: JSON.stringify(body), credentials: "include" });
export const loginUser = (body: { username: string; password: string }) =>
  api<{ user: User }>("/auth/login", { method: "POST", body: JSON.stringify(body), credentials: "include" });
export const loginMaster = (body: { master_key: string }) =>
  api<{ user: User }>("/auth/login", { method: "POST", body: JSON.stringify(body), credentials: "include" });
export const logoutSession = () => api<{ ok: true }>("/auth/logout", { method: "POST", credentials: "include" });
export const getUsers = () => api<{ users: (User & { disabled: boolean; created_at: number })[] }>("/admin/users");
export const patchUser = (id: string, body: { role?: string; disabled?: boolean }) =>
  api<User>(`/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body), credentials: "include" });
export const getPublicModels = () => api<{ groups: PublicModelGroup[]; aliases: Record<string, string> }>("/public/models");
```
Ensure the `api` fetch wrapper sends `credentials: "include"` by default for cookie support (add `credentials: "include"` to the `fetch` call in `api`).

- [ ] **Step 4: Rewrite `web/src/api/auth.tsx` for sessions**

```tsx
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { getMe, getToken, loginUser, loginMaster, logoutSession, setToken, signupUser, clearToken } from "./client";
import type { User } from "./types";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  signup: (username: string, password: string) => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  loginWithMaster: (key: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>(null!);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const { user } = await getMe();
      setUser(user);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const signup = useCallback(async (u: string, p: string) => {
    const { user } = await signupUser({ username: u, password: p });
    setUser(user);
  }, []);
  const login = useCallback(async (u: string, p: string) => {
    const { user } = await loginUser({ username: u, password: p });
    setUser(user);
  }, []);
  const loginWithMaster = useCallback(async (k: string) => {
    setToken(k); // back-compat: keep master key for any bearer-style calls
    const { user } = await loginMaster({ master_key: k });
    setUser(user);
  }, []);
  const logout = useCallback(async () => {
    try { await logoutSession(); } catch { /* ignore */ }
    clearToken();
    setUser(null);
  }, []);

  return <Ctx.Provider value={{ user, loading, signup, login, loginWithMaster, logout, refresh }}>{children}</Ctx.Provider>;
}
```

- [ ] **Step 5: Type-check + build**

```bash
cd web && bun run build
```
Expected: build passes (the new code is not yet wired into routes — that's Task 11; unused imports may warn but `tsc -b` should pass).

- [ ] **Step 6: Commit**

```bash
git add web/vite.config.ts web/src/api/
git commit -m "Add session auth context, /auth proxy, and user client helpers"
```

---

## Task 11: Route tree + layouts + guards (public vs guarded `/app/*`)

**Files:**
- Modify: `web/src/main.tsx`, `web/src/components/Layout.tsx` (rename to AdminLayout export), `web/src/components/PublicLayout.tsx` (new), `web/src/components/guards.tsx` (new)

**Interfaces:**
- Produces: public routes (`/`, `/models`, `/docs`, `/playground`), `/login`, `/signup`, guarded `/app/*`, old-path redirects. `RequireUser`/`RequireAdmin` guards. Role-aware `AdminLayout` nav.

- [ ] **Step 1: Create guards**

`web/src/components/guards.tsx`:
```tsx
import { Navigate } from "react-router-dom";
import { useAuth } from "@/api/auth";

export function RequireUser({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== "admin") return <Navigate to="/app" replace />;
  return <>{children}</>;
}
```

- [ ] **Step 2: Create `PublicLayout.tsx`**

A minimal top-nav (logo + links: Playground, Models, Docs, Sign in) rendering `<Outlet/>` over the same ambient background as `AdminLayout`. Footer: "wiwi · self-hosted LLM gateway". Use the existing admin CSS tokens.

- [ ] **Step 3: Make `AdminLayout` role-aware**

In `Layout.tsx`: export as `AdminLayout`. Build `NAV_SECTIONS` filtered by `useAuth().user?.role`:
- **user sections:** Overview (Dashboard), Traffic (Request Logs, Usage, Analytics), Configuration (Models, Virtual Keys), Admin (Budgets & Alerts)
- **admin sections:** all current sections + new `Admin → Users` item (`/app/users`).
Update `PAGE_META` keys: `"/"`→`"/app"`, `"/keys"`→`"/app/keys"`, etc. Keep the sidebar collapse, topbar, identity card (show `user.username` + role badge instead of hardcoded "master admin"; for master-key admin show "master admin").

- [ ] **Step 4: Rewrite `main.tsx` route tree**

```tsx
function AppRoutes() {
  const { user, loading } = useAuth();
  if (loading) return null;
  return (
    <Routes>
      <Route element={<PublicLayout />}>
        <Route path="/" element={<LandingPage />} />
        <Route path="/models" element={<ModelsCatalogPage />} />
        <Route path="/docs" element={<DocsPage />} />
      </Route>
      <Route path="/playground" element={<RequireUser><PlaygroundPage /></RequireUser>} />
      <Route path="/login" element={user ? <Navigate to="/app" replace /> : <LoginPage />} />
      <Route path="/signup" element={user ? <Navigate to="/app" replace /> : <SignupPage />} />
      <Route element={<RequireUser><AdminStreamProvider><AdminLayout /></RequireUser>}>
        <Route path="/app" element={<DashboardPage />} />
        <Route path="/app/keys" element={<VirtualKeysPage />} />
        <Route path="/app/models" element={<ModelsPage />} />
        <Route path="/app/request-logs" element={<RequestLogsPage />} />
        <Route path="/app/usage" element={<UsagePage />} />
        <Route path="/app/analytics" element={<AnalyticsPage />} />
        <Route path="/app/budgets" element={<BudgetsAlertsPage />} />
        <Route path="/app/providers" element={<RequireAdmin><ProvidersPage /></RequireAdmin>} />
        <Route path="/app/providers/:name" element={<RequireAdmin><ProviderDetailPage /></RequireAdmin>} />
        <Route path="/app/builtin-providers" element={<RequireAdmin><BuiltinProvidersPage /></RequireAdmin>} />
        <Route path="/app/proxy-logs" element={<RequireAdmin><ProxyLogsPage /></RequireAdmin>} />
        <Route path="/app/settings" element={<RequireAdmin><SettingsPage /></RequireAdmin>} />
        <Route path="/app/users" element={<RequireAdmin><UsersPage /></RequireAdmin>} />
      </Route>
      {/* legacy flat-path redirects */}
      <Route path="/keys" element={<Navigate to="/app/keys" replace />} />
      <Route path="/providers" element={<Navigate to="/app/providers" replace />} />
      <Route path="/models-config" element={<Navigate to="/app/models" replace />} />
      <Route path="/request-logs" element={<Navigate to="/app/request-logs" replace />} />
      <Route path="/usage" element={<Navigate to="/app/usage" replace />} />
      <Route path="/analytics" element={<Navigate to="/app/analytics" replace />} />
      <Route path="/budgets" element={<Navigate to="/app/budgets" replace />} />
      <Route path="/settings" element={<Navigate to="/app/settings" replace />} />
      <Route path="/proxy-logs" element={<Navigate to="/app/proxy-logs" replace />} />
      <Route path="/builtin-providers" element={<Navigate to="/app/builtin-providers" replace />} />
      <Route path="/dashboard" element={<Navigate to="/app" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
```
Import the new pages (`LandingPage`, `SignupPage`, `UsersPage`, `PlaygroundPage`, `ModelsCatalogPage`, `DocsPage`) — these are created in Task 12; to keep this task's build green, create thin placeholder components returning `<div>TODO</div>` now and flesh them out in Task 12. Wrap the tree in `<AuthProvider>` (replacing the old `RequireAuth`).

- [ ] **Step 5: Build**

```bash
cd web && bun run build
```
Expected: passes (with placeholder pages).

- [ ] **Step 6: Commit**

```bash
git add web/src/
git commit -m "Add public/guarded route split, role-aware AdminLayout, route guards"
```

---

## Task 12: New pages (Landing, Signup, Login dual-mode, Users, Playground, ModelsCatalog, Docs) + Models RO

**Files:**
- Create: `web/src/pages/Landing.tsx`, `Signup.tsx`, `Users.tsx`, `Playground.tsx`, `ModelsCatalog.tsx`, `Docs.tsx`
- Modify: `web/src/pages/Login.tsx`, `web/src/pages/Models.tsx`

**Interfaces:**
- Consumes: `useAuth`, `getUsers`/`patchUser`, `getPublicModels`, `getModels`, `listKeys`, `generateKey`, the existing `/v1/chat/completions` endpoint.
- Produces: the seven page components referenced by `main.tsx`; `Login` supports username/password + master-key toggle; `Models` is read-only for non-admins.

- [ ] **Step 1: Implement `Landing.tsx`**

Hero ("One gateway, every model"), feature grid (unified inbound dialects, virtual keys, budgets, key pools, retries, observability), how-it-works (dialect → IR → provider), CTAs → `/signup`, `/playground`, `/docs`. Reuse `Card`/`PageHeader` tokens, dark theme. Use `<Link to="...">` from react-router-dom.

- [ ] **Step 2: Implement `Signup.tsx`**

Form (username, password), calls `useAuth().signup`, on success `navigate("/app")`. Link to `/login`. Show validation errors. Reuse `Card`, `Input`, `Button` from `ui.tsx`.

- [ ] **Step 3: Modify `Login.tsx`** to dual mode

Add a toggle ("Sign in with master key" / "Sign in with username"). Username mode calls `login(u,p)`; master mode calls `loginWithMaster(key)` and removes the old direct `setToken`+probe flow. On success `navigate("/app")`. Link to `/signup`.

- [ ] **Step 4: Implement `Users.tsx`** (admin)

`useQuery(["users"], getUsers)`, table of users with a role `<Select>` (user/admin) and a disable `<Toggle>`, calling `patchUser` via `useMutation`. Disable the controls when editing yourself to be the last admin (the backend guards anyway). Reuse `Table`, `Badge`, `Select`.

- [ ] **Step 5: Implement `Playground.tsx`**

- `useQuery(["keys"], listKeys)` for the user's own keys (scoped by backend). Key picker `<Select>` + "Create key" inline (calls `generateKey({name: "playground"})`, invalidates `["keys"]`).
- Model `<Select>` from `getModels()` group names.
- Message list + composer. On send: `POST /v1/chat/completions` with `Authorization: Bearer <chosen key>` and `{model, messages, stream:false}`. Display the assistant message + usage/cost. Use a plain `fetch` (not `api`) so you can set the virtual-key bearer and read the body. Handle errors.
- Empty state when no keys: prompt to create one.

- [ ] **Step 6: Implement `ModelsCatalog.tsx`**

`useQuery(["public-models"], getPublicModels)` → grid of model cards (name, providers, context/pricing if available). Public, no auth. Link each card to `/playground`.

- [ ] **Step 7: Implement `Docs.tsx`**

Static content: quickstart (point a client at `http://localhost:4000/v1`), the three inbound dialects (OpenAI Chat Completions, OpenAI Responses, Anthropic Messages), auth via virtual keys, code examples (curl + Python `openai` SDK pointed at the gateway). Reuse `Card`.

- [ ] **Step 8: Make `Models.tsx` read-only for non-admins**

Gate the weight-edit `WeightChip` and the strategy `Select` behind `useAuth().user?.role === "admin"`. For users, render deployments as static chips (no click-to-edit). Keep the data fetch the same (backend returns read access to both roles).

- [ ] **Step 9: Build + type-check**

```bash
cd web && bun run build
```
Expected: passes.

- [ ] **Step 10: Commit**

```bash
git add web/src/pages/
git commit -m "Add Landing, Signup, Users, Playground, ModelsCatalog, Docs; dual-mode Login; read-only Models"
```

---

## Task 13: Dashboard role-adaptivity + full verification

**Files:**
- Modify: `web/src/pages/Dashboard.tsx` (optional role-aware copy)
- Verify: full backend + frontend

- [ ] **Step 1: Adapt Dashboard copy by role**

In `Dashboard.tsx`, read `useAuth().user?.role`. For `user`, title the page "Your dashboard" and subtitle "Usage across your virtual keys" (the data is already scoped by the backend). For `admin`, keep existing copy. No data-logic change needed.

- [ ] **Step 2: Run full backend suite**

```bash
.venv/bin/python -m pytest tests/ -q
```
Expected: all pass.

- [ ] **Step 3: Run ruff**

```bash
.venv/bin/ruff check wiwi/ tests/
```
Expected: clean.

- [ ] **Step 4: Run frontend build**

```bash
cd web && bun run build
```
Expected: passes, output into `wiwi/server/static/`.

- [ ] **Step 5: Manual smoke (document in commit body)**

With a `wiwi.yaml` present and the server running (`wiwi --config wiwi.yaml`) + `cd web && bun run dev`:
1. Open `/` → Landing renders (no auth).
2. Click Sign up → create `tester/password1` → land on `/app` with the 6-page user nav.
3. In `/app/keys`, create a key → it appears; master login shows it too (global).
4. `/app/models` shows groups read-only (no weight/strategy controls).
5. `/playground`: pick the key, send "hi" to a model → assistant reply + usage.
6. Log out, log in with master key → full nav incl. `/app/users`; promote `tester` to admin; confirm the last-admin guard rejects demoting the only admin.
7. Visit old bookmark `/keys` → redirects to `/app/keys`.

- [ ] **Step 6: Final commit**

```bash
git add web/src/pages/Dashboard.tsx
git commit -m "Adapt Dashboard copy to role and finalize public front + user accounts"
```

---

## Self-Review (run after writing, before handoff)

**Spec coverage:**
- users table + pbkdf2 + sessions → Task 1, 5, 6 ✓
- vkeys.owner_id → Task 2 ✓
- request_logs.key_id → Task 3, 4 ✓
- /auth/signup,login,logout,me → Task 6 ✓
- /admin/users + last-admin guard → Task 7 ✓
- owner-scoped keys/logs/stats/models PATCH → Task 8 ✓
- /public/models → Task 9 ✓
- frontend auth context + client + types → Task 10 ✓
- routing split + layouts + guards → Task 11 ✓
- Landing/Signup/Login/Users/Playground/ModelsCatalog/Docs + Models RO → Task 12 ✓
- Dashboard role-adaptivity + verification → Task 13 ✓

**Placeholder scan:** Task 5 Step 3 contains an explicit "⚠️ placeholder is wrong" note — that's intentional guidance to implement `current_user` as async; the real code is specified in Task 6 Step 3. Task 11 Step 4 uses placeholder pages, fully replaced in Task 12. No other TBDs.

**Type consistency:** `UserInfo(id, username, role, disabled)`, `sign_session(secret, uid, role, expires)` / `verify_session(secret, token) -> (uid, role, expires)|None` used consistently across Tasks 1/5/6. `list_keys_for_owner(uid)`, `key_owner(key_id)` consistent across 2/8. `read_requests/read_overview/read_timeseries(..., key_ids=None)` consistent across 3/8. `User` TS interface consistent across 10/11/12.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-26-public-front-and-user-accounts.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session, batch execution with checkpoints.

Which approach?
