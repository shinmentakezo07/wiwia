"""Auth service: master + virtual keys, in-memory cache, budget/rpm/tpm state.

Storage is SQLite or PostgreSQL via SQLAlchemy async; keys are stored
hashed, plaintext shown once at creation. Cache TTL 60s; admin mutations
evict actively.
"""

import hmac
import time
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
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
    owner_id: str | None = None

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
  owner_id TEXT,
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
        self._is_pg = engine.dialect.name == "postgresql"

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(CREATE_SQL))
            # Index for ORDER BY created_at DESC in list_keys()
            await conn.execute(sa.text(
                "CREATE INDEX IF NOT EXISTS idx_vkeys_created_at"
                " ON vkeys(created_at DESC)"))
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

    # -- lookup ----------------------------------------------------------------
    async def authenticate(self, plaintext: str) -> AuthInfo | None:
        if hmac.compare_digest(hash_key(plaintext), self.master_hash):
            return AuthInfo(key_id="master", key_type="master", alias="master")
        h = hash_key(plaintext)
        now = time.monotonic()
        hit = self._cache.get(h)
        if hit and now - hit[1] < self._ttl:
            # Budget-bound keys must always reflect the latest spend so a
            # concurrent update_spend can immediately reject further use;
            # other keys (no max_budget) keep the TTL cache for speed.
            info, _ts = hit
            if info is None or info.max_budget is None:
                return info
        info = await self._lookup_db(h)
        self._cache[h] = (info, now)
        return info

    async def _lookup_db(self, h: str) -> AuthInfo | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT id, key_alias, models, max_budget, spend_to_date,"
                        " rpm, tpm, expires_at, disabled, owner_id FROM vkeys"
                        " WHERE key_hash=:h"),
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
            owner_id=row[9],
        )

    def evict(self, plaintext: str) -> None:
        self._cache.pop(hash_key(plaintext), None)

    # -- CRUD ------------------------------------------------------------------
    async def create_key(self, alias: str, models: list[str] | None = None,
                         max_budget: float | None = None, rpm: int | None = None,
                         tpm: int | None = None, ttl_seconds: float | None = None,
                         custom_key: str | None = None,
                         owner_id: str | None = None) -> tuple[str, str]:
        """Returns (plaintext, key_id). Custom keys allowed (>=16 chars)."""
        plaintext = custom_key or generate_virtual_key()
        if custom_key and len(custom_key) < 16:
            raise ValueError("custom key must be >= 16 characters")
        kid = "k" + secrets_hex()
        now = time.time()
        expires = now + ttl_seconds if ttl_seconds else None
        async with self.engine.begin() as conn:
            try:
                await conn.execute(
                    sa.text("INSERT INTO vkeys (id, key_hash, key_alias, models, max_budget,"
                            " spend_to_date, rpm, tpm, expires_at, disabled, owner_id,"
                            " created_at, updated_at)"
                            " VALUES (:id,:h,:a,:m,:b,0,:r,:t,:e,0,:owner,:c,:c)"),
                    {"id": kid, "h": hash_key(plaintext), "a": alias,
                     "m": __import__("json").dumps(models or []), "b": max_budget,
                     "r": rpm, "t": tpm, "e": expires, "owner": owner_id, "c": now},
                )
            except IntegrityError as e:
                raise ValueError("custom key already exists") from e
        # a failed guess of this plaintext may sit in the negative cache for the
        # TTL; evict so the freshly created key authenticates immediately
        self._cache.pop(hash_key(plaintext), None)
        return plaintext, kid

    async def delete_key(self, key_id: str) -> bool:
        # fetch the hash first so the cache entry can be evicted; a deleted key
        # must stop authenticating immediately, not after the TTL lapses
        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text("SELECT key_hash FROM vkeys WHERE id=:id"),
                                      {"id": key_id})).first()
        async with self.engine.begin() as conn:
            res = await conn.execute(sa.text("DELETE FROM vkeys WHERE id=:id"), {"id": key_id})
        if row is not None:
            self._cache.pop(row[0], None)
        return res.rowcount > 0

    async def get_key(self, key_id: str) -> dict | None:
        for k in await self.list_keys():
            if k["id"] == key_id:
                return k
        return None

    UPDATABLE_FIELDS = ("max_budget", "rpm", "tpm", "models", "expires_at",
                        "ttl_seconds")

    async def update_key(self, key_id: str, fields: dict) -> dict | None:
        """Patch editable fields (absent = unchanged; explicit null = clear).

        ``ttl_seconds`` is a relative duration (seconds from now); it is
        converted to an absolute ``expires_at`` epoch. ``expires_at`` (absolute
        epoch) is still accepted for backward compatibility. When both are
        present, ``ttl_seconds`` wins.

        Returns the updated key dict, or None when the id is unknown. Cache is
        evicted so the new limits apply immediately.
        """
        sets: dict[str, object] = {}
        for name in self.UPDATABLE_FIELDS:
            if name not in fields:
                continue
            val = fields[name]
            if name == "models":
                val = __import__("json").dumps(list(val or []))
            elif name == "ttl_seconds":
                # Relative duration -> absolute epoch; ttl_seconds is not a
                # DB column, it maps to expires_at.
                if val is not None:
                    sets["expires_at"] = time.time() + float(val)
                else:
                    sets["expires_at"] = None
                continue
            elif val is not None:
                val = float(val) if name in ("max_budget", "expires_at") else int(val)
            sets[name] = val
        # ttl_seconds maps to expires_at; don't emit it as a column.
        sets.pop("ttl_seconds", None)
        if not sets:
            return await self.get_key(key_id)
        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text("SELECT key_hash FROM vkeys WHERE id=:id"),
                                      {"id": key_id})).first()
        if row is None:
            return None
        cols = ", ".join(f"{k}=:{k}" for k in sets)
        params = {**sets, "now": time.time(), "id": key_id}
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(f"UPDATE vkeys SET {cols}, updated_at=:now"
                                       " WHERE id=:id"), params)
        self._cache.pop(row[0], None)
        return await self.get_key(key_id)

    async def set_disabled(self, key_id: str, disabled: bool) -> None:
        """Disable/enable a key and evict its cached auth info immediately."""
        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text("SELECT key_hash FROM vkeys WHERE id=:id"),
                                      {"id": key_id})).first()
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE vkeys SET disabled=:d, updated_at=:now WHERE id=:id"),
                {"d": int(disabled), "id": key_id, "now": time.time()},
            )
        if row is not None:
            self._cache.pop(row[0], None)

    async def update_spend(self, key_id: str, add_cost: float) -> bool:
        """Add *add_cost* to the key's spend_to_date.

        Uses a conditional UPDATE: the row is only incremented when the
        resulting total would not exceed max_budget.  Returns True on a
        successful spend, False when the update was rejected (over-budget
        or unknown key).  Master is a no-op (always True) so callers don't
        branch on key type.
        """
        if key_id == "master":
            return True
        if add_cost <= 0:
            return True
        async with self.engine.begin() as conn:
            res = await conn.execute(
                sa.text("UPDATE vkeys SET spend_to_date = spend_to_date + :c,"
                        " updated_at = :now"
                        " WHERE id = :id"
                        " AND (max_budget IS NULL OR spend_to_date + :c <= max_budget)"),
                {"c": add_cost, "id": key_id, "now": time.time()},
            )
        if res.rowcount == 0:
            return False
        # keep cached budget state fresh so budget limits are enforced promptly;
        # adjust the cached AuthInfo in place instead of a full re-lookup
        for info, _ts in self._cache.values():
            if info is not None and info.key_id == key_id:
                info.spend_to_date += add_cost
        return True

    async def list_keys(self) -> list[dict]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                sa.text("SELECT id, key_alias, models, max_budget, spend_to_date, rpm, tpm,"
                        " expires_at, disabled, owner_id FROM vkeys ORDER BY created_at DESC"))).all()
        import json as _json
        return [
            {"id": r[0], "alias": r[1], "models": _json.loads(r[2]), "max_budget": r[3],
             "spend_to_date": r[4], "rpm": r[5], "tpm": r[6],
             "expires_at": r[7], "disabled": bool(r[8]), "owner_id": r[9]}
            for r in rows
        ]

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


def secrets_hex() -> str:
    import secrets as _s
    return _s.token_hex(8)
