"""Auth service: master + virtual keys, in-memory cache, budget/rpm/tpm state.

MVP storage is SQLite via SQLAlchemy async; keys are stored hashed, plaintext
shown once at creation. Cache TTL 60s; admin mutations evict actively.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from wiwi.auth.keys import generate_virtual_key, hash_key


@dataclass
class AuthInfo:
    key_id: str
    key_type: str  # "master" | "virtual"
    alias: str = ""
    models: list[str] = field(default_factory=list)  # empty = all allowed
    max_budget: float | None = None
    spend_to_date: float = 0.0
    rpm: int | None = None
    tpm: int | None = None
    expires_at: float | None = None
    disabled: bool = False

    @property
    def over_budget(self) -> bool:
        return self.max_budget is not None and self.spend_to_date >= self.max_budget


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
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
"""


class AuthService:
    def __init__(self, engine: AsyncEngine, master_key_plaintext: str):
        self.engine = engine
        self.master_hash = hash_key(master_key_plaintext)
        self._cache: dict[str, tuple[AuthInfo | None, float]] = {}
        self._ttl = 60.0

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(CREATE_SQL))

    # -- lookup ----------------------------------------------------------------
    async def authenticate(self, plaintext: str) -> AuthInfo | None:
        if hash_key(plaintext) == self.master_hash:
            return AuthInfo(key_id="master", key_type="master", alias="master")
        h = hash_key(plaintext)
        now = time.monotonic()
        hit = self._cache.get(h)
        if hit and now - hit[1] < self._ttl:
            return hit[0]
        info = await self._lookup_db(h)
        self._cache[h] = (info, now)
        return info

    async def _lookup_db(self, h: str) -> AuthInfo | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT id, key_alias, models, max_budget, spend_to_date,"
                        " rpm, tpm, expires_at, disabled FROM vkeys WHERE key_hash=:h"),
                {"h": h},
            )).first()
        if row is None:
            return None
        import json as _json
        expires = float(row[7]) if row[7] else None
        return AuthInfo(
            key_id=row[0], key_type="virtual", alias=row[1],
            models=_json.loads(row[2]), max_budget=row[3], spend_to_date=float(row[4]),
            rpm=row[5], tpm=row[6], expires_at=expires, disabled=bool(row[8]),
        )

    def evict(self, plaintext: str) -> None:
        self._cache.pop(hash_key(plaintext), None)

    # -- CRUD ------------------------------------------------------------------
    async def create_key(self, alias: str, models: list[str] | None = None,
                         max_budget: float | None = None, rpm: int | None = None,
                         tpm: int | None = None, ttl_seconds: float | None = None,
                         custom_key: str | None = None) -> tuple[str, str]:
        """Returns (plaintext, key_id). Custom keys allowed (>=16 chars)."""
        plaintext = custom_key or generate_virtual_key()
        if custom_key and len(custom_key) < 16:
            raise ValueError("custom key must be >= 16 characters")
        kid = "k" + secrets_hex()
        now = time.time()
        expires = now + ttl_seconds if ttl_seconds else None
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("INSERT INTO vkeys (id, key_hash, key_alias, models, max_budget,"
                        " spend_to_date, rpm, tpm, expires_at, disabled, created_at, updated_at)"
                        " VALUES (:id,:h,:a,:m,:b,0,:r,:t,:e,0,:c,:c)"),
                {"id": kid, "h": hash_key(plaintext), "a": alias,
                 "m": __import__("json").dumps(models or []), "b": max_budget,
                 "r": rpm, "t": tpm, "e": expires, "c": now},
            )
        return plaintext, kid

    async def update_spend(self, key_id: str, add_cost: float) -> None:
        if key_id == "master":
            return
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE vkeys SET spend_to_date = spend_to_date + :c, updated_at=:now"
                        " WHERE id=:id"),
                {"c": add_cost, "id": key_id, "now": time.time()},
            )

    async def delete_key(self, key_id: str) -> bool:
        async with self.engine.begin() as conn:
            res = await conn.execute(sa.text("DELETE FROM vkeys WHERE id=:id"), {"id": key_id})
        return res.rowcount > 0

    async def list_keys(self) -> list[dict]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                sa.text("SELECT id, key_alias, models, max_budget, spend_to_date, rpm, tpm,"
                        " expires_at, disabled FROM vkeys ORDER BY created_at DESC"))).all()
        import json as _json
        return [
            {"id": r[0], "alias": r[1], "models": _json.loads(r[2]), "max_budget": r[3],
             "spend_to_date": r[4], "rpm": r[5], "tpm": r[6],
             "expires_at": r[7], "disabled": bool(r[8])}
            for r in rows
        ]


def secrets_hex() -> str:
    import secrets as _s
    return _s.token_hex(8)
