# Public Front + User Accounts + Role Dashboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the admin-only wiwi console into a hybrid app: a public front (landing/playground/models/docs), username+password user accounts with HttpOnly session cookies, and role-based dashboards where normal users see scoped data and admins see the full console — with the dashboard moving off `/` into a guarded `/app/*` area.

**Architecture:** Backend-first. New `wiwi/auth/users.py` owns the users table + signed session cookies (stdlib only — no new deps). The `vkeys` and `request_logs` tables gain additive columns (`owner_id`, `key_id`) for ownership scoping. Existing `/admin/*` endpoints gain an `actor` concept that filters by owner for users and stays global for admins. Frontend splits into a public shell (root routes) and the existing admin shell (now `/app/*`), with role-aware nav and route guards.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async (no ORM — raw DDL), aiosqlite/asyncpg, stdlib `hashlib.pbkdf2_hmac`/`hmac`/`secrets`; React 19 + TypeScript + Vite + Tailwind 4, react-router-dom v7, built with bun.

## Global Constraints

- No new Python dependencies — use stdlib `hashlib.pbkdf2_hmac`, `hmac`, `secrets`, `hashlib` only.
- No ORM; raw DDL, dialect-portable (SQLite + PostgreSQL), idempotent migrations matching `wiwi/server/config_store.py`.
- Never add dialect/provider branches in `core/`/`router/`/`auth/` — new auth logic lives in `auth/users.py`; endpoint/handler logic in `server/app.py`.
- Async throughout; `orjson` in hot paths; never `print` from library code — use `structlog`.
- Ruff only: line-length 100, target py311.
- Tests: bare `async def test_...` (asyncio_mode=auto), no decorators. New user-account tests go in `tests/test_user_accounts.py`.
- Frontend: **bun** is authoritative for `web/` (not npm). One routed page per admin concern in `web/src/pages/*.tsx`.
- All DB migrations are additive and backward-compatible (nullable columns, default `''`/`NULL`). Master-key bearer auth and existing `/admin/*` endpoints keep working.
- Never commit `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, or anything under `.verify/`.
- Commits: imperative present tense, capitalized, no prefix tags. One logical change per commit. End messages with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**Reference spec:** `docs/superpowers/specs/2026-08-26-public-front-and-user-accounts-design.md`

---

## File Structure

### Backend (new + modified)

| File | Responsibility | Status |
|---|---|---|
| `wiwi/auth/users.py` | `users` table DDL + migrations; password hashing (pbkdf2); signed session cookie issue/verify; `UserService` CRUD; `current_user` resolver | **Create** |
| `wiwi/auth/service.py` | Add `owner_id` column + migration; thread `owner_id` through `create_key`/`_lookup_db`/`list_keys` | Modify |
| `wiwi/logging_core/events.py` | Add `key_id: str = ""` field to `LogEvent` | Modify |
| `wiwi/logging_core/db_sink.py` | Add `key_id` to DDL + `_COLS` + `_row`; add optional `key_ids` filter to `read_requests`/`read_overview`/`read_timeseries` | Modify |
| `wiwi/server/app.py` | Wire `UserService`; add `/auth/*` + `/admin/users` + `/public/models` endpoints; add `actor` resolution; scope keys/logs/stats/models endpoints by actor | Modify |
| `wiwi/server/app.py` (gateway path) | Stamp `key_id` into `LogEvent` from `AuthInfo` at log time | Modify |
| `tests/test_user_accounts.py` | All backend regression tests for the new subsystem | **Create** |

### Frontend (new + modified)

| File | Responsibility | Status |
|---|---|---|
| `web/src/api/auth.tsx` | Replace master-key-only context with `{user, loginUser, loginMaster, signup, logout, refresh}`; hydrate via `/auth/me` | Modify |
| `web/src/api/client.ts` | Add `getUsers`, `patchUser`, `getPublicModels`; cookie-aware fetch (credentials: include) | Modify |
| `web/src/api/types.ts` | Add `User`, `AdminUser`, `PublicModel` types | Modify |
| `web/src/main.tsx` | New route tree: public shell, `/auth` pages, guarded `/app/*` with role guards | Modify |
| `web/src/components/Layout.tsx` | Role-aware nav (user vs admin); paths move to `/app/*` | Modify |
| `web/src/components/PublicLayout.tsx` | Minimal top-nav shell for public pages | **Create** |
| `web/src/components/Guards.tsx` | `RequireUser`, `RequireAdmin`, `RedirectOldPaths` | **Create** |
| `web/src/pages/Landing.tsx` | Public marketing landing page | **Create** |
| `web/src/pages/ModelsCatalog.tsx` | Public model catalog (reads `/public/models`) | **Create** |
| `web/src/pages/Docs.tsx` | Custom docs page (quickstart, dialects, auth) | **Create** |
| `web/src/pages/Playground.tsx` | Chat UI: pick/create own key, model select, chat vs `/v1/chat/completions` | **Create** |
| `web/src/pages/Signup.tsx` | Username+password signup form | **Create** |
| `web/src/pages/Users.tsx` | Admin user management table (promote/demote/disable) | **Create** |
| `web/src/pages/Login.tsx` | Refactor: one form, user password + master-key toggle | Modify |
| `web/src/pages/Models.tsx` | Hide weight-edit + strategy select for non-admins (read-only) | Modify |
| `web/vite.config.ts` | Add `"/auth"` and `"/public"` to dev proxy | Modify |

---

## Task 1: `users` table + password hashing + `UserService`

**Files:**
- Create: `wiwi/auth/users.py`
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Produces: `UserService` class with `__init__(self, engine: AsyncEngine, master_key: str)`, async `startup()`, async `create_user(username, password) -> dict | None`, async `verify_user(username, password) -> dict | None`, async `get_user(user_id) -> dict | None`, async `list_users() -> list[dict]`, async `update_user(user_id, *, role=None, disabled=None) -> dict | None`, async `count_active_admins() -> int`. Module-level `hash_password(password) -> str`, `verify_password(password, stored) -> bool`, `user_id() -> str`.
- Produces: `UserInfo` dataclass: `id: str`, `username: str`, `role: str` (`"user"|"admin"` — master-key identity uses `id="master"`, `role="admin"`).
- `dict` user shape: `{"id","username","role","disabled":bool,"created_at":float}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_user_accounts.py
import pytest


def test_hash_and_verify_password_roundtrip():
    from wiwi.auth.users import hash_password, verify_password
    h = hash_password("correct horse battery staple")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong", h) is False


@pytest.mark.asyncio
async def test_create_user_persists_and_verifies(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine
    from wiwi.auth.users import UserService
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/u.db")
    svc = UserService(engine, master_key="mk")
    await svc.startup()
    u = await svc.create_user("alice", "password1")
    assert u is not None
    assert u["username"] == "alice"
    assert u["role"] == "user"
    assert u["disabled"] is False
    got = await svc.verify_user("alice", "password1")
    assert got is not None and got["id"] == u["id"]
    assert await svc.verify_user("alice", "nope") is None
    # duplicate username (case-insensitive) -> None
    assert await svc.create_user("ALICE", "x") is None
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'wiwi.auth.users'`

- [ ] **Step 3: Write minimal implementation**

```python
# wiwi/auth/users.py
"""User accounts: username+password, roles, signed session cookies.

No new dependencies — uses stdlib hashlib.pbkdf2_hmac for password
hashing and hmac/secrets for signed cookies. Follows the no-ORM raw-DDL
dialect-portable pattern of config_store.py and auth/service.py.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

_PBKDF2_ITERS = 200_000
_SESSION_TTL = 7 * 24 * 3600

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


@dataclass
class UserInfo:
    id: str
    username: str
    role: str  # "user" | "admin"


def user_id() -> str:
    return "u" + secrets.token_hex(8)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


class UserService:
    def __init__(self, engine: AsyncEngine, master_key: str) -> None:
        self.engine = engine
        self.master_key = master_key
        self._is_pg = engine.dialect.name == "postgresql"
        self._secret = session_secret(master_key)

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(USERS_DDL))
            await conn.execute(sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username"
                " ON users(username)"))

    @staticmethod
    def _norm(username: str) -> str:
        return username.strip().lower()

    async def create_user(self, username: str, password: str) -> dict | None:
        uname = self._norm(username)
        if not (3 <= len(uname) <= 32) or not all(
                c.isalnum() or c in "_-" for c in uname):
            return None
        if len(password) < 8:
            return None
        uid = user_id()
        now = time.time()
        try:
            async with self.engine.begin() as conn:
                await conn.execute(sa.text(
                    "INSERT INTO users (id, username, password_hash, role,"
                    " disabled, created_at, updated_at)"
                    " VALUES (:id,:u,:p,'user',0,:c,:c)"),
                    {"id": uid, "u": uname, "p": hash_password(password),
                     "c": now})
        except IntegrityError:
            return None
        return {"id": uid, "username": uname, "role": "user",
                "disabled": False, "created_at": now}

    async def verify_user(self, username: str, password: str) -> dict | None:
        uname = self._norm(username)
        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text(
                "SELECT id, username, password_hash, role, disabled"
                " FROM users WHERE username=:u"), {"u": uname})).first()
        if row is None or not verify_password(password, row[2]):
            return None
        if bool(row[4]):
            return None  # disabled
        return {"id": row[0], "username": row[1], "role": row[3],
                "disabled": False}

    async def get_user(self, user_id: str) -> dict | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text(
                "SELECT id, username, role, disabled, created_at"
                " FROM users WHERE id=:id"), {"id": user_id})).first()
        if row is None:
            return None
        return {"id": row[0], "username": row[1], "role": row[2],
                "disabled": bool(row[3]), "created_at": row[4]}

    async def list_users(self) -> list[dict]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT id, username, role, disabled, created_at"
                " FROM users ORDER BY created_at DESC"))).all()
        return [{"id": r[0], "username": r[1], "role": r[2],
                 "disabled": bool(r[3]), "created_at": r[4]} for r in rows]

    async def update_user(self, user_id: str, *, role: str | None = None,
                          disabled: bool | None = None) -> dict | None:
        sets: dict[str, object] = {}
        if role in ("user", "admin"):
            sets["role"] = role
        if disabled is not None:
            sets["disabled"] = int(disabled)
        if not sets:
            return await self.get_user(user_id)
        sets["updated_at"] = time.time()
        cols = ", ".join(f"{k}=:{k}" for k in sets)
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(
                f"UPDATE users SET {cols} WHERE id=:id"),
                {**sets, "id": user_id})
        return await self.get_user(user_id)

    async def count_active_admins(self) -> int:
        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text(
                "SELECT COUNT(*) FROM users WHERE role='admin'"
                " AND disabled=0"))).first()
        return int(row[0]) if row else 0


def _hkdf(ikm: bytes, length: int) -> bytes:
    """Extract-and-expand HKDF (RFC 5869) using stdlib only."""
    prk = hmac.new(b"wiwi-session-v1", ikm, hashlib.sha256).digest()
    t = b""
    okm = b""
    for i in range(1, (length + 31) // 32 + 1):
        t = hmac.new(prk, t + b"wiwi" + bytes([i]), hashlib.sha256).digest()
        okm += t
    return okm[:length]


def session_secret(master_key: str) -> bytes:
    env = os.environ.get("WIWI_SESSION_SECRET")
    if env:
        return env.encode()[:64].ljust(64, b"\x00")
    return _hkdf((master_key or "wiwi-default").encode(), 64)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS (both tests)

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/auth/users.py tests/test_user_accounts.py
git add wiwi/auth/users.py tests/test_user_accounts.py
git commit -m "Add UserService with users table and pbkdf2 password hashing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: Signed session cookie + `current_user` resolver

**Files:**
- Modify: `wiwi/auth/users.py`
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Produces: `UserService.issue_session(user_id, role) -> str` (cookie value `"user_id.role.exp.hmac"`).
- Produces: static `UserService.parse_session(value, secret) -> tuple[str,str,float] | None`.
- Produces: async `current_user(request, users) -> UserInfo | None` (cookie or bearer master key → synthetic admin).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_user_accounts.py
@pytest.mark.asyncio
async def test_session_issue_parse_roundtrip(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine
    from wiwi.auth.users import UserService
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/u2.db")
    svc = UserService(engine, master_key="mk-secret")
    await svc.startup()
    u = await svc.create_user("bob", "password1")
    val = svc.issue_session(u["id"], "user")
    parsed = UserService.parse_session(val, svc._secret)
    assert parsed is not None
    pid, prole, _exp = parsed
    assert pid == u["id"] and prole == "user"
    bad = val[:-2] + ("00" if val[-2:] != "00" else "11")
    assert UserService.parse_session(bad, svc._secret) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_current_user_from_cookie(tmp_path):
    import starlette.datastructures
    from sqlalchemy.ext.asyncio import create_async_engine
    from wiwi.auth.users import UserService, current_user
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/u3.db")
    svc = UserService(engine, master_key="mk-secret")
    await svc.startup()
    u = await svc.create_user("carol", "password1")
    val = svc.issue_session(u["id"], "user")
    req = starlette.datastructures.Request(scope={
        "type": "http",
        "headers": [(b"cookie", f"wiwi_session={val}".encode())],
        "query_string": b"", "path": "/", "method": "GET",
    })
    info = await current_user(req, svc)
    assert info is not None and info.id == u["id"] and info.role == "user"
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: FAIL — `issue_session`/`parse_session`/`current_user` undefined.

- [ ] **Step 3: Implement session cookie + resolver**

Add to `wiwi/auth/users.py` (methods on `UserService`, plus module-level `current_user`):

```python
    def issue_session(self, user_id: str, role: str) -> str:
        exp = int(time.time()) + _SESSION_TTL
        payload = f"{user_id}.{role}.{exp}"
        sig = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    @staticmethod
    def parse_session(value: str, secret: bytes) -> tuple[str, str, float] | None:
        try:
            uid, role, exp, sig = value.rsplit(".", 3)
        except ValueError:
            return None
        payload = f"{uid}.{role}.{exp}"
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if time.time() > float(exp):
            return None
        return uid, role, float(exp)


async def current_user(request, users: "UserService") -> UserInfo | None:
    """Resolve identity from cookie (session) or bearer (master key)."""
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        tok = authz[7:].strip()
        if users.master_key and hmac.compare_digest(tok.encode(),
                                                    users.master_key.encode()):
            return UserInfo(id="master", username="master", role="admin")
    cookie = request.headers.get("cookie", "")
    val = None
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "wiwi_session" and v:
            val = v
    if not val:
        return None
    parsed = UserService.parse_session(val, users._secret)
    if parsed is None:
        return None
    uid, role, _exp = parsed
    if uid == "master":
        return UserInfo(id="master", username="master", role="admin")
    row = await users.get_user(uid)
    if row is None or row["disabled"]:
        return None
    return UserInfo(id=row["id"], username=row["username"], role=row["role"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/auth/users.py tests/test_user_accounts.py
git add wiwi/auth/users.py tests/test_user_accounts.py
git commit -m "Add signed session cookie and current_user resolver

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: `vkeys.owner_id` migration + owner threading

**Files:**
- Modify: `wiwi/auth/service.py` (`CREATE_SQL`, `startup`, `create_key`, `_lookup_db`, `list_keys`)
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Produces: `AuthService.create_key(..., owner_id: str | None = None) -> tuple[str, str]`.
- Produces: `AuthService.list_keys(owner_id: str | None = None) -> list[dict]`.
- Produces: `AuthService.key_owner(key_id: str) -> str | None`.
- Adds `owner_id: str | None = None` field to `AuthInfo`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_user_accounts.py
@pytest.mark.asyncio
async def test_vkeys_owner_id_migration_and_scoping(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine
    from wiwi.auth.service import AuthService
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/k.db")
    svc = AuthService(engine, master_key="mk")
    await svc.startup()
    _pt, kid = await svc.create_key("alice-key", owner_id="u-alice")
    _pt2, kid2 = await svc.create_key("bob-key", owner_id="u-bob")
    assert [k["id"] for k in await svc.list_keys(owner_id="u-alice")] == [kid]
    assert [k["id"] for k in await svc.list_keys(owner_id="u-bob")] == [kid2]
    assert len(await svc.list_keys()) == 2
    assert await svc.key_owner(kid) == "u-alice"
    assert await svc.key_owner(kid2) == "u-bob"
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: FAIL — `create_key` doesn't accept `owner_id`.

- [ ] **Step 3: Implement migration + threading**

In `wiwi/auth/service.py`:
- Add `owner_id TEXT` to `CREATE_SQL` (after `updated_at`).
- Add `owner_id: str | None = None` to `AuthInfo` dataclass.
- In `startup()` after the index creation, add the idempotent `owner_id` migration (column check + `ALTER TABLE` + `idx_vkeys_owner`).
- `create_key`: add `owner_id: str | None = None` param; include `:o`/`owner_id` in the INSERT.
- `_lookup_db`: SELECT `owner_id`; set on `AuthInfo`.
- `list_keys(owner_id=None)`: SELECT `owner_id`; if `owner_id is not None` add `WHERE owner_id=:o`; include in returned dict.
- Add `key_owner(key_id)` returning `owner_id` or `None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/auth/service.py tests/test_user_accounts.py
git add wiwi/auth/service.py tests/test_user_accounts.py
git commit -m "Add vkeys.owner_id and owner-scoped key listing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: `request_logs.key_id` + `LogEvent.key_id` + DB sink filter

**Files:**
- Modify: `wiwi/logging_core/events.py`
- Modify: `wiwi/logging_core/db_sink.py`
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Produces: `LogEvent.key_id: str = ""`.
- Produces: `DBSink.read_requests(limit, key_ids=None)`, `read_overview(minutes, key_ids=None)`, `read_timeseries(bs, metric, minutes, key_ids=None)` — `AND key_id IN :kids` appended when `key_ids` is not None.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_user_accounts.py
@pytest.mark.asyncio
async def test_request_logs_key_id_filter(tmp_path):
    import time as _t
    from sqlalchemy.ext.asyncio import create_async_engine
    from wiwi.logging_core.db_sink import DBSink
    from wiwi.logging_core.events import LogEvent
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/r.db")
    sink = DBSink(engine)
    await sink.startup()
    now = _t.time()
    await sink.write_requests([
        LogEvent(stream="request", ts=now, request_id="a", key_id="kA"),
        LogEvent(stream="request", ts=now, request_id="b", key_id="kB"),
    ])
    only_a = await sink.read_requests(100, key_ids=["kA"])
    assert [r["request_id"] for r in only_a] == ["a"]
    allr = await sink.read_requests(100)
    assert {r["request_id"] for r in allr} == {"a", "b"}
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: FAIL — `LogEvent` has no `key_id`; `read_requests` takes no `key_ids`.

- [ ] **Step 3: Implement**

In `wiwi/logging_core/events.py`, add after `key_alias`:
```python
    key_id: str = ""
```

In `wiwi/logging_core/db_sink.py`:
- Add `key_id TEXT DEFAULT ''` to both SQLite + PG `request_logs` DDL.
- Add `"key_id"` to `_COLS` (after `"key_alias"`).
- In `_row`, add `"key_id": e.key_id`.
- In `startup()`: idempotent `key_id` column add + `idx_request_logs_key_id`.
- Add `_key_filter(key_ids) -> tuple[str, dict]` returning `("", {})` or `(" AND key_id IN :kids", {"kids": tuple(key_ids)})`. Thread it into `read_requests`, `read_overview`, `read_timeseries`, and the sample/percentile subqueries, binding `kids` with `sa.bindparam("kids", expanding=True)` for both dialects.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/logging_core/ tests/test_user_accounts.py
git add wiwi/logging_core/events.py wiwi/logging_core/db_sink.py tests/test_user_accounts.py
git commit -m "Add request_logs.key_id and key_id-scoped log reads

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: `/auth/*` endpoints (signup/login/logout/me)

**Files:**
- Modify: `wiwi/server/app.py`
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Produces endpoints: `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`.
- Cookie: `wiwi_session`, `HttpOnly`, `SameSite=Lax`, `Secure` on HTTPS, `Path=/`, `max-age=7d`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_user_accounts.py
from httpx import ASGITransport, AsyncClient


async def _app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("WIWI_MASTER_KEY", "mk-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/a.db")
    cfg_path = tmp_path / "wiwi.yaml"
    cfg_path.write_text("general_settings:\n  master_key: mk-test\n"
                        "providers: []\nmodel_list: []\n")
    from wiwi.server.app import create_app_from_config_path
    app = create_app_from_config_path(str(cfg_path))
    transport = ASGITransport(app=app)
    from asgi_lifespan import LifespanManager
    mgr = LifespanManager(app)
    await mgr.startup()
    return AsyncClient(transport=transport, base_url="http://test"), mgr


@pytest.mark.asyncio
async def test_signup_login_me_logout(tmp_path, monkeypatch):
    client, mgr = await _app_client(tmp_path, monkeypatch)
    try:
        r = await client.post("/auth/signup", json={"username": "Dave",
                                                    "password": "password1"})
        assert r.status_code == 201, r.text
        assert r.json()["user"]["role"] == "user"
        assert "wiwi_session" in r.cookies
        assert (await client.get("/auth/me")).json()["user"]["username"] == "dave"
        assert (await client.post("/auth/logout")).status_code == 200
        assert (await client.get("/auth/me")).json()["user"] is None
        li = await client.post("/auth/login", json={"username": "dave",
                                                   "password": "password1"})
        assert li.status_code == 200 and li.json()["user"]["username"] == "dave"
        assert (await client.post("/auth/login",
                json={"username": "dave", "password": "wrong"})).status_code == 401
        mk = await client.post("/auth/login", json={"master_key": "mk-test"})
        assert mk.status_code == 200 and mk.json()["user"]["role"] == "admin"
    finally:
        await client.aclose()
        await mgr.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: FAIL — `/auth/*` routes don't exist (404).

- [ ] **Step 3: Wire `UserService` + add endpoints**

In `AppState.init_db()` (`wiwi/server/app.py`, after `await self.auth.startup()`):
```python
        from wiwi.auth.users import UserService
        self.users = UserService(aengine, self.config.general_settings.master_key)
        await self.users.startup()
```

In `create_app`, after `is_admin`/`_require_admin` defs, add:

```python
    from wiwi.auth.users import current_user as _current_user

    def _set_session_cookie(resp, value: str, secure: bool) -> ORJSONResponse:
        resp.set_cookie("wiwi_session", value, max_age=7 * 24 * 3600,
                        httponly=True, samesite="lax", secure=secure, path="/")
        return resp

    @app.post("/auth/signup")
    async def auth_signup(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        u = await state.users.create_user(body.get("username", ""),
                                          body.get("password", ""))
        if u is None:
            return _err(400, "invalid_request_error",
                        "username taken or invalid (3-32 chars, [a-z0-9_-]);"
                        " password >= 8 chars", request)
        secure = request.url.scheme == "https"
        r = ORJSONResponse({"user": {"id": u["id"], "username": u["username"],
                                     "role": u["role"]}}, status_code=201)
        return _set_session_cookie(r, state.users.issue_session(u["id"], u["role"]), secure)

    @app.post("/auth/login")
    async def auth_login(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        secure = request.url.scheme == "https"
        mk = body.get("master_key")
        if mk and config.general_settings.master_key and hmac.compare_digest(
                str(mk).encode(), config.general_settings.master_key.encode()):
            r = ORJSONResponse({"user": {"id": "master", "username": "master",
                                         "role": "admin"}})
            return _set_session_cookie(r, state.users.issue_session("master", "admin"), secure)
        u = await state.users.verify_user(body.get("username", ""),
                                          body.get("password", ""))
        if u is None:
            return _err(401, "authentication_error", "invalid credentials", request)
        r = ORJSONResponse({"user": {"id": u["id"], "username": u["username"],
                                     "role": u["role"]}})
        return _set_session_cookie(r, state.users.issue_session(u["id"], u["role"]), secure)

    @app.post("/auth/logout")
    async def auth_logout(request: Request):
        r = ORJSONResponse({"ok": True})
        r.delete_cookie("wiwi_session", path="/")
        return r

    @app.get("/auth/me")
    async def auth_me(request: Request):
        info = await _current_user(request, state.users)
        if info is None:
            return ORJSONResponse({"user": None})
        return ORJSONResponse({"user": {"id": info.id, "username": info.username,
                                        "role": info.role}})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/server/app.py tests/test_user_accounts.py
git add wiwi/server/app.py tests/test_user_accounts.py
git commit -m "Add /auth signup, login, logout, me endpoints with session cookies

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6: Actor-scoped key endpoints (`/admin/keys*`)

**Files:**
- Modify: `wiwi/server/app.py` (generate/list/delete/disable/patch keys)
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Consumes: `_current_user` (Task 5), `AuthService.list_keys(owner_id)`, `key_owner`, `create_key(owner_id)` (Task 3).
- Semantics: admin → global; user → `owner_id = user.id`; user on another's key → 403.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_user_accounts.py
@pytest.mark.asyncio
async def test_keys_scoped_per_user(tmp_path, monkeypatch):
    client, mgr = await _app_client(tmp_path, monkeypatch)
    try:
        await client.post("/auth/signup", json={"username": "ann", "password": "password1"})
        await client.post("/auth/login", json={"username": "ann", "password": "password1"})
        kid_a = (await client.post("/admin/keys/generate",
                 json={"name": "ann-key"})).json()["id"]
        await client.post("/auth/signup", json={"username": "ben", "password": "password1"})
        await client.post("/auth/login", json={"username": "ben", "password": "password1"})
        kid_b = (await client.post("/admin/keys/generate",
                 json={"name": "ben-key"})).json()["id"]
        assert [k["id"] for k in (await client.get("/admin/keys")).json()["keys"]] == [kid_b]
        assert (await client.delete(f"/admin/keys/{kid_a}")).status_code == 403
        await client.post("/auth/login", json={"master_key": "mk-test"})
        assert {k["id"] for k in (await client.get("/admin/keys")).json()["keys"]} == {kid_a, kid_b}
    finally:
        await client.aclose()
        await mgr.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: FAIL — keys endpoints still master-only (401 for session users).

- [ ] **Step 3: Implement actor scoping**

Add helpers in `create_app`:

```python
    async def _require_actor(request: Request):
        info = await _current_user(request, state.users)
        if info is None:
            return None, _err(401, "authentication_error", "login required", request)
        return info, None

    async def _key_owned_by(info, key_id) -> bool:
        if info.role == "admin":
            return True
        return await state.auth.key_owner(key_id) == info.id
```

In `admin_generate_key`: replace the `is_admin` guard with `info, resp = await _require_actor(request); if resp: return resp`; pass `owner_id=None if info.role == "admin" else info.id` to `create_key`; audit `actor=info.username`.

In `admin_list_keys`: `_require_actor`; `owner_id = None if info.role == "admin" else info.id`; `list_keys(owner_id)`.

In `admin_delete_key`, `admin_disable_key`, `admin_patch_key`: `_require_actor`; `if not await _key_owned_by(info, key_id): return _err(403, "permission_error", "not your key", request)`; audit `actor=info.username`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/server/app.py tests/test_user_accounts.py
git add wiwi/server/app.py tests/test_user_accounts.py
git commit -m "Scope /admin/keys endpoints by session actor

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 7: Scope request logs + stats by actor; `/public/models`; Models PATCH admin-only

**Files:**
- Modify: `wiwi/server/app.py` (request logs, stats, models PATCH, gateway log path)
- Test: `tests/test_user_accounts.py`

**Interfaces:**
- Consumes: `_require_actor`, `AuthService.list_keys(owner_id)`, `DBSink.read_*(key_ids)`.
- Produces: `GET /public/models` (unauth, secret-free).
- Models PATCH → admin-only (403 for users).
- Gateway stamps `key_id` into `LogEvent`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_user_accounts.py
import time as _t
from wiwi.logging_core.events import LogEvent


@pytest.mark.asyncio
async def test_request_logs_and_stats_scoped(tmp_path, monkeypatch):
    client, mgr = await _app_client(tmp_path, monkeypatch)
    try:
        await client.post("/auth/signup", json={"username": "eve", "password": "password1"})
        g = (await client.post("/admin/keys/generate", json={"name": "eve-key"})).json()
        # seed logs directly via the running AppState's DB sink
        from wiwi.server import app as appmod
        st = appmod._last_state
        await st.logs.db_sink.write_requests([
            LogEvent(stream="request", ts=_t.time(), request_id="eve-r",
                     key_id=g["id"], model_group="gpt-4o"),
            LogEvent(stream="request", ts=_t.time(), request_id="other-r",
                     key_id="k-someone-else", model_group="gpt-4o"),
        ])
        logs = (await client.get("/admin/logs/requests")).json()["logs"]
        assert {l["request_id"] for l in logs} == {"eve-r"}
        ov = await client.get("/admin/stats/overview?minutes=60")
        assert ov.json()["requests"] >= 1
        await client.post("/auth/login", json={"master_key": "mk-test"})
        all_logs = (await client.get("/admin/logs/requests")).json()["logs"]
        assert {l["request_id"] for l in all_logs} == {"eve-r", "other-r"}
    finally:
        await client.aclose()
        await mgr.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: FAIL — request logs endpoint still admin-only; no scoping.

- [ ] **Step 3: Implement scoping + public models + key_id stamping**

Add:
```python
    async def _actor_key_ids(info) -> list[str] | None:
        if info is None or info.role == "admin":
            return None
        return [k["id"] for k in await state.auth.list_keys(owner_id=info.id)]
```

`admin_request_logs`: `info, resp = await _require_actor(request); if resp: return resp`; `key_ids = await _actor_key_ids(info)`; DB path → `sink.read_requests(limit, key_ids=key_ids)`; ring path → filter `public_dict(e)` by `e.key_id in key_ids`.

`admin_stats_overview` / `admin_stats_timeseries`: `_require_actor`; `key_ids = await _actor_key_ids(info)`; DB path → pass `key_ids`; ring path → filter `_request_events()` to those with `key_id in key_ids` before `stats_mod.*`.

`admin_patch_model_group`: `_require_actor`; `if info.role != "admin": return _err(403, "permission_error", "admin only", request)`.

Add `/public/models`:
```python
    @app.get("/public/models")
    async def public_models(request: Request):
        out = []
        for name, deps in sorted(state.router.model_groups().items()):
            out.append({
                "name": name,
                "providers": sorted({d.provider for d in deps}),
                "model_ids": sorted({d.model_id for d in deps}),
            })
        return ORJSONResponse({"models": out})
```

Set `_last_state` module var in `create_app`: `_last_state = state` (after `state` is built).

Stamp `key_id`: in `run_chat_like`/`complete`, where `LogEvent(...key_alias=info.alias...)` is constructed, add `key_id=info.key_id`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/server/app.py wiwi/logging_core/ tests/test_user_accounts.py
git add wiwi/server/app.py wiwi/logging_core/ tests/test_user_accounts.py
git commit -m "Scope request logs and stats by actor; add /public/models

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 8: `/admin/users` endpoints + last-admin guard

**Files:**
- Modify: `wiwi/server/app.py`
- Test: `tests/test_user_accounts.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_user_accounts.py
@pytest.mark.asyncio
async def test_admin_users_management(tmp_path, monkeypatch):
    client, mgr = await _app_client(tmp_path, monkeypatch)
    try:
        u = (await client.post("/auth/signup",
             json={"username": "frank", "password": "password1"})).json()["user"]
        await client.post("/auth/login", json={"username": "frank", "password": "password1"})
        assert (await client.get("/admin/users")).status_code == 403
        await client.post("/auth/login", json={"master_key": "mk-test"})
        lst = await client.get("/admin/users")
        assert lst.status_code == 200
        assert u["id"] in [x["id"] for x in lst.json()["users"]]
        promo = await client.patch(f"/admin/users/{u['id']}", json={"role": "admin"})
        assert promo.status_code == 200 and promo.json()["role"] == "admin"
        demote = await client.patch(f"/admin/users/{u['id']}", json={"role": "user"})
        assert demote.status_code == 400
    finally:
        await client.aclose()
        await mgr.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: FAIL — `/admin/users` routes don't exist.

- [ ] **Step 3: Implement**

```python
    @app.get("/admin/users")
    async def admin_list_users(request: Request):
        info, resp = await _require_actor(request)
        if resp:
            return resp
        if info.role != "admin":
            return _err(403, "permission_error", "admin only", request)
        return ORJSONResponse({"users": await state.users.list_users()})

    @app.patch("/admin/users/{user_id}")
    async def admin_patch_user(user_id: str, request: Request):
        info, resp = await _require_actor(request)
        if resp:
            return resp
        if info.role != "admin":
            return _err(403, "permission_error", "admin only", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        new_role = body.get("role")
        new_disabled = body.get("disabled")
        target = await state.users.get_user(user_id)
        if target is None:
            return _err(404, "not_found_error", "unknown user", request)
        will_lose_admin = ((new_role == "user" and target["role"] == "admin")
                           or (new_disabled is True and target["role"] == "admin"))
        if will_lose_admin and await state.users.count_active_admins() <= 1:
            return _err(400, "invalid_request_error",
                        "cannot demote or disable the last admin", request)
        updated = await state.users.update_user(user_id, role=new_role,
                                                disabled=new_disabled)
        await state.logs.log_audit(actor=info.username, action="user.update",
                                   target=user_id,
                                   diff={"role": new_role, "disabled": new_disabled})
        return ORJSONResponse(updated)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_user_accounts.py -q`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check wiwi/server/app.py tests/test_user_accounts.py
git add wiwi/server/app.py tests/test_user_accounts.py
git commit -m "Add /admin/users management with last-admin guard

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 9: Full backend verification

- [ ] **Step 1: Run full suite + ruff**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check wiwi/ tests/
```
Expected: all PASS, ruff clean. If pre-existing tests break, fix regressions (additive migrations should not break anything).

- [ ] **Step 2: Commit regression fixes if any**

```bash
git add -A
git commit -m "Fix backend regressions from actor scoping

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
(Skip if none.)

---

## Task 10: Frontend auth context + client + types

**Files:**
- Modify: `web/src/api/auth.tsx`, `web/src/api/client.ts`, `web/src/api/types.ts`

- [ ] **Step 1: Update types** — append `User`, `AdminUser`, `PublicModel` interfaces (see File Structure).

- [ ] **Step 2: Rewrite `auth.tsx`** — `useAuth()` → `{user, loading, loginUser, loginMaster, signup, logout, refresh}`; hydrate via `GET /auth/me` on mount; `credentials: "include"` on auth fetches.

- [ ] **Step 3: Update `client.ts`** — add `credentials: "include"` to the `fetch` in `api()`; keep bearer header from `getToken()` when present; add `getUsers`, `patchUser`, `getPublicModels`.

- [ ] **Step 4: Type-check + build**

```bash
cd web && bun run build
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/api/
git commit -m "Replace master-key auth with session user context

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 11: Route tree + guards + public layout

**Files:**
- Create: `web/src/components/Guards.tsx`, `web/src/components/PublicLayout.tsx`
- Modify: `web/src/main.tsx`, `web/vite.config.ts`

- [ ] **Step 1: Create `Guards.tsx`** — `RequireUser` (→ `/login`), `RequireAdmin` (→ `/app`), `RedirectOldPaths` (old flat → `/app/*`).

- [ ] **Step 2: Create `PublicLayout.tsx`** — top-nav (logo, Playground / Models / Docs / Sign in or Dashboard), `<Outlet/>`, footer.

- [ ] **Step 3: Rewrite `main.tsx` route tree** — public shell (`/`, `/models`, `/docs`), `/playground` (RequireUser), `/login` + `/signup` (redirect to `/app` if authed), guarded `/app/*` (RequireUser + AdminStreamProvider + AdminLayout) with admin-only pages wrapped in `RequireAdmin`, old paths via `RedirectOldPaths`, `*` → `/`. Create stub pages for pages built in later tasks.

- [ ] **Step 4: Vite proxy** — add `"/auth"` and `"/public"` to `server.proxy` in `web/vite.config.ts`.

- [ ] **Step 5: Type-check + build**

```bash
cd web && bun run build
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/Guards.tsx web/src/components/PublicLayout.tsx web/src/main.tsx web/vite.config.ts
git commit -m "Add public layout, route guards, and /app guarded tree

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 12: Role-aware AdminLayout nav

**Files:**
- Modify: `web/src/components/Layout.tsx`

- [ ] **Step 1: Update nav** — import `useAuth`; filter `NAV_SECTIONS` by `user.role` (users: Dashboard, Request Logs, Usage, Analytics, Models, Keys, Budgets; admins: all + Users); change all `to:` paths to `/app/*`; update `PAGE_META` keys; replace logout with `useAuth().logout` → `/login`; identity card shows `user.username` + role badge.

- [ ] **Step 2: Type-check + build**

```bash
cd web && bun run build
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/Layout.tsx
git commit -m "Make admin layout nav role-aware under /app

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 13: Landing page

**Files:**
- Create: `web/src/pages/Landing.tsx` (replace stub)

- [ ] **Step 1: Build the page** — hero ("One gateway, every model"), feature grid (unified dialects, virtual keys, budgets, key pools, retries, observability), how-it-works (dialect → IR → provider), CTAs → `/signup`, `/playground`, `/docs`. Reuse `Card`/`Button`/`Badge` from `ui.tsx`.

- [ ] **Step 2: Type-check + build**

```bash
cd web && bun run build
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/Landing.tsx
git commit -m "Add public landing page

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 14: Public Models catalog + Docs

**Files:**
- Create: `web/src/pages/ModelsCatalog.tsx`, `web/src/pages/Docs.tsx`

- [ ] **Step 1: Models catalog** — `useQuery(["public-models"], getPublicModels)`; grid of cards: name, provider badges, model ids. No secrets/health.

- [ ] **Step 2: Docs page** — hand-authored: quickstart (base URL `/v1`, `Authorization: Bearer <key>`), the three dialects with curl examples, virtual-key/budget notes. Static `Card` sections.

- [ ] **Step 3: Type-check + build + commit**

```bash
cd web && bun run build
git add web/src/pages/ModelsCatalog.tsx web/src/pages/Docs.tsx
git commit -m "Add public models catalog and docs pages

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 15: Signup + Login refactor

**Files:**
- Create: `web/src/pages/Signup.tsx`, Modify: `web/src/pages/Login.tsx`

- [ ] **Step 1: Signup page** — username + password + confirm; `signup(u, p)` → `/app`; link to `/login`.

- [ ] **Step 2: Refactor Login** — one form, username+password plus "or sign in with master key" toggle; `loginUser`/`loginMaster` → `/app`; link to `/signup`.

- [ ] **Step 3: Type-check + build + commit**

```bash
cd web && bun run build
git add web/src/pages/Signup.tsx web/src/pages/Login.tsx
git commit -m "Add signup page and refactor login for user + master key

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 16: Playground page

**Files:**
- Create: `web/src/pages/Playground.tsx`

- [ ] **Step 1: Build the playground** — key picker (`listKeys()` scoped by backend; "Create key" inline via `generateKey`); model selector (`getModels()`); chat composer → `POST /v1/chat/completions` with `Authorization: Bearer <key plaintext>`; render streamed (SSE) + non-streamed; show usage/cost. Reuse `ui.tsx` components.

- [ ] **Step 2: Type-check + build + commit**

```bash
cd web && bun run build
git add web/src/pages/Playground.tsx
git commit -m "Add chat playground using the user's own virtual keys

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 17: Users management page + Models read-only

**Files:**
- Create: `web/src/pages/Users.tsx`, Modify: `web/src/pages/Models.tsx`

- [ ] **Step 1: Users page** — `getUsers()` table with role `<Select>` + disable `Toggle`; `patchUser` mutation; toast on last-admin 400.

- [ ] **Step 2: Models read-only** — in `Models.tsx`, when `user.role !== "admin"` hide the strategy `Select` and render `WeightChip` read-only (no edit). Backend 403s PATCH for users (Task 7).

- [ ] **Step 3: Type-check + build + commit**

```bash
cd web && bun run build
git add web/src/pages/Users.tsx web/src/pages/Models.tsx
git commit -m "Add user management page and read-only models for users

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 18: Full verification + build + smoke

- [ ] **Step 1: Backend**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check wiwi/ tests/
```
Expected: all PASS, ruff clean.

- [ ] **Step 2: Frontend**

```bash
cd web && bun run build
```
Expected: tsc + vite build PASS; output in `wiwi/server/static/`.

- [ ] **Step 3: Manual smoke test**

```bash
# terminal 1
.venv/bin/python -m wiwi.main --config wiwi.yaml
# terminal 2
cd web && bun run dev
```
- `http://localhost:5173/` → Landing (public).
- `/signup` → create user → `/app` with the 6-page user nav.
- Create a virtual key; appears only in this user's Keys page.
- `/playground` → create/use key → chat against `/v1/chat/completions`.
- Logout; `/login` with master key → `/app` full admin nav; Users page lists the user; promote to admin.
- Old bookmark `/keys` → redirects to `/app/keys`.

- [ ] **Step 4: Final commit if any polish**

```bash
git add -A
git commit -m "Verify public front, user accounts, and role dashboards end-to-end

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- `users` table + pbkdf2 + session cookie → Tasks 1–2 ✓
- `vkeys.owner_id` + ownership → Task 3 ✓
- `request_logs.key_id` + scoping → Task 4 ✓
- `/auth/*` endpoints → Task 5 ✓
- Owner-scoped keys → Task 6 ✓
- Scoped logs/stats + `/public/models` + Models PATCH admin-only + key_id stamping → Task 7 ✓
- `/admin/users` + last-admin guard → Task 8 ✓
- Backend verify → Task 9 ✓
- Frontend auth context/client/types → Task 10 ✓
- Route tree + guards + public layout + old-path redirects → Task 11 ✓
- Role-aware admin nav → Task 12 ✓
- Landing → Task 13 ✓
- Models catalog + Docs → Task 14 ✓
- Signup + Login → Task 15 ✓
- Playground → Task 16 ✓
- Users page + Models RO → Task 17 ✓
- Full verify → Task 18 ✓

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later". Code steps contain actual code; frontend content tasks describe concrete structure reusing real `ui.tsx` components.

**3. Type consistency:** `User {id,username,role}` consistent across `types.ts`, `auth.tsx`, `/auth/me`. `UserInfo` (backend) ↔ `User` (frontend) align. `list_keys(owner_id)`, `create_key(owner_id)`, `key_owner`, `read_requests(key_ids)`, `read_overview(key_ids)`, `read_timeseries(key_ids)` referenced consistently in Tasks 3/4/6/7. `actor`/`_require_actor`/`_actor_key_ids` defined in Task 5/6 and reused in 6/7/8.
