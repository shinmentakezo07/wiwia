"""User accounts + signed session cookies.

No ORM; raw DDL (SQLite + Postgres), stdlib-only password hashing.
Sessions are stateless signed cookies; a users-row lookup per guarded
request validates the user still exists and is enabled.
"""
from __future__ import annotations

import asyncio
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
# Cap password length so PBKDF2 cost stays bounded (see create_user).
_MAX_PASSWORD_LEN = 1024
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
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL
);
"""


async def widen_pg_floats(conn, table: str, *columns: str) -> None:
    """Widen 4-byte ``REAL`` columns to ``DOUBLE PRECISION`` on Postgres.

    A DDL shared by both dialects cannot spell a float column ``REAL``: SQLite
    maps that to an 8-byte float, but Postgres maps it to **float4**. Every
    ``time.time()`` column is ~1.79e9, where a float4's ULP is 128 s, so all
    rows created within the same ~2-minute block shared one timestamp and
    ``ORDER BY created_at DESC`` was arbitrary — ``expire_keys`` could retire
    the key an owner was actively using. Sums stored in a float4 also floor:
    ``1000.0 + 20 * 1e-6`` still read back as ``1000.0`` (round 108).

    Only Postgres needs this (SQLite's REAL is already 8 bytes) and only for
    databases created before the DDL said ``DOUBLE PRECISION`` — the type is
    read from the catalog first, so an up-to-date table is left alone.
    """
    rows = (await conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = :t"
        " AND data_type = 'real'"), {"t": table})).all()
    wanted = set(columns)
    for (col,) in rows:
        # `col` comes from the catalog, not from caller input.
        if col in wanted:
            await conn.execute(sa.text(
                f"ALTER TABLE {table} ALTER COLUMN {col} TYPE double precision"))


def _user_id() -> str:
    return "u" + _secrets.token_hex(8)


def _now() -> float:
    return time.time()


# -- password hashing ---------------------------------------------------------

def hash_password(password: str) -> str:
    if not isinstance(password, str):
        # ValueError, not TypeError: the auth handlers translate ValueError
        # into a client error; TypeError would escape as a 500 (AUDIT #80).
        raise ValueError("password must be a string")  # noqa: TRY004
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS)
    return f"pbkdf2_sha256${PBKDF2_ITERS}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
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


# A real PBKDF2 record at the production cost, used to burn the same work for
# a username that does not exist. Without it ``UserService.verify`` short-
# circuited on the missing row and returned after one indexed SELECT, while an
# existing user paid 200k PBKDF2 iterations first — a ~46 ms / 30x difference
# that let an unauthenticated attacker enumerate the username space and aim
# credential stuffing at confirmed accounts (AUDIT #223). The password is
# random and never matches, so the result is always False; only the *cost* is
# equalized.
_DUMMY_PASSWORD_HASH = hash_password(_secrets.token_urlsafe(32))


def burn_dummy_verify(password: str) -> None:
    """Spend the same work as a real verify, for a non-existent account."""
    verify_password(password if isinstance(password, str) else "", _DUMMY_PASSWORD_HASH)


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
    expected = hmac.new(key, f"{uid}.{role}.{exp}".encode(),
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
    # json_body only guarantees a JSON object, so a nested scalar can reach
    # here. `(non_str or "").strip()` raised AttributeError previously and
    # surfaced as a 500; reject it as a client error instead (AUDIT #80).
    if not isinstance(username, str):
        raise ValueError("username must be a string")  # noqa: TRY004
    u = username.strip().lower()
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
            if self._is_pg:
                # Postgres-only: databases created before USERS_DDL said
                # DOUBLE PRECISION have these as 4-byte REAL (round 108).
                await widen_pg_floats(conn, "users", "created_at", "updated_at")

    async def create_user(self, username: str, password: str) -> UserInfo:
        uname = _validate_username(username)
        if not isinstance(password, str):
            raise ValueError("password must be a string")  # noqa: TRY004
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        # The upper bound matters as much as the lower one: PBKDF2 cost scales
        # with input length, so an unbounded password is a CPU-exhaustion
        # vector on an endpoint that needs no authentication.
        if len(password) > _MAX_PASSWORD_LEN:
            raise ValueError(
                f"password must be at most {_MAX_PASSWORD_LEN} characters")
        uid = _user_id()
        now = _now()
        # Same ~75 ms of blocking CPU as verify(), and public signup needs no
        # authentication at all (AUDIT #362). Hashed before the transaction
        # opens so the loop is free for the DB round trip too.
        pw_hash = await asyncio.to_thread(hash_password, password)
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    sa.text("INSERT INTO users (id, username, password_hash,"
                            " role, disabled, created_at, updated_at)"
                            " VALUES (:id, :u, :h, 'user', 0, :t, :t)"),
                    {"id": uid, "u": uname, "h": pw_hash, "t": now},
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
        if row is None:
            # Burn the same PBKDF2 work a real account would, so the response
            # time does not reveal whether the username exists (AUDIT #223).
            # Off-thread for the same reason as the real verify below.
            await asyncio.to_thread(burn_dummy_verify, password)
            return None
        # 200k PBKDF2 iterations is ~75 ms of pure CPU. Called inline it froze
        # the event loop for that whole window, stalling every other request in
        # flight — including in-progress streaming completions — on two
        # unauthenticated endpoints (AUDIT #362). Same remedy as
        # estimate_tokens_async (AUDIT #33) and the journal FS I/O (AUDIT #105).
        if not await asyncio.to_thread(verify_password, password, row[1]):
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
