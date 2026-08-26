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
