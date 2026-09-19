"""Auth service: master + virtual keys, in-memory cache, budget/rpm/tpm state.

Storage is SQLite or PostgreSQL via SQLAlchemy async; keys are stored
hashed, plaintext shown once at creation. Cache TTL 60s; admin mutations
evict actively.
"""

import asyncio
import hmac
import time
import weakref
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from wiwi.auth.keys import generate_virtual_key, hash_key
from wiwi.auth.users import USERS_DDL

# Scalar key limits where 0 must be REJECTED rather than stored. Kept as one
# named set so the rule cannot drift between the create and update paths, and
# so the fields that legitimately accept 0 stay visibly outside it.
_ZERO_IS_NOT_A_CAP = frozenset({"rpm", "tpm", "ttl_seconds"})


def _coerce_limit(value: object, name: str) -> float | None:
    """Validate a numeric key limit.

    Rejects negatives and non-numeric input up front. A stored negative rpm/tpm
    made the rate limiter read an empty window and raise IndexError, returning
    HTTP 500 for every request on that key — and a negative budget would let
    spend run backwards. Names in :data:`_ZERO_IS_NOT_A_CAP` also reject 0,
    because every consumer treats a falsy limit as "no limit at all".
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # ValueError (not TypeError) on purpose: every caller translates
        # ValueError into a 400 for the client, and these are input problems.
        raise ValueError(f"{name} must be a number, not a boolean")  # noqa: TRY004
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    # NaN is the only value that is not equal to itself.
    if num != num or num in (float("inf"), float("-inf")):  # noqa: PLR0124
        raise ValueError(f"{name} must be a finite number")
    if num < 0:
        raise ValueError(f"{name} must be >= 0")
    if num == 0 and name in _ZERO_IS_NOT_A_CAP:
        # A zero scalar cap on a virtual key is not a cap, it is a *skipped*
        # cap: the rate limiter guards each scope with ``if key_rpm:`` /
        # ``if key_tpm:`` and the caller's truthy guard does the same, so a
        # stored 0 means "unlimited" — the opposite of what an operator
        # parking a key by setting it to zero intends (AUDIT #163).
        # ``DeploymentParams`` rejects ``rpm <= 0`` for exactly this reason;
        # the two boundaries now agree. ``ttl_seconds`` joins them for the
        # same shape of ambiguity: 0 is falsy on create (no expiry) and
        # non-None on update (expire now), so no single meaning is safe
        # (AUDIT #202). ``max_budget`` is deliberately NOT here — a budget of
        # 0 is a meaningful "spend nothing" cap that the limiter's budget
        # check reads with ``is not None``, and ``expires_at`` is an absolute
        # epoch where 0 already means "long expired".
        raise ValueError(f"{name} must be > 0")
    return num


def _coerce_models(value: object) -> list[str] | None:
    """Normalize the models allowlist.

    A bare string was being iterated into per-character entries, so
    ``models: "abc"`` silently became ``["a", "b", "c"]``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                # ValueError: callers map it to a 400 for the client.
                raise ValueError("models must be a list of strings")  # noqa: TRY004
            out.append(item)
        return out
    raise ValueError("models must be a list of strings")


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
    def __init__(self, engine: AsyncEngine, master_key_plaintext: str,
                 max_keys_per_user: int = 50):
        self.engine = engine
        self.master_hash = hash_key(master_key_plaintext)
        self._cache: dict[str, tuple[AuthInfo | None, float]] = {}
        self._ttl = 60.0
        # Bumped by every cache eviction. authenticate() compares the value it
        # read before its DB lookup against the current one, so a revocation
        # landing mid-lookup cannot be undone by the late store (AUDIT #162).
        self._evict_gen = 0
        self._is_pg = engine.dialect.name == "postgresql"
        # Ceiling on live keys per owner. Admins mint keys with owner_id=None
        # and are exempt; the check below only fires for a real owner.
        self.max_keys_per_user = max_keys_per_user
        # authenticate()'s lookup→store pair is made safe against a concurrent
        # revocation by ``_evict_gen``, not by a lock — see that method.
        #
        # Serializes create_key's count→insert pair per owner. count_keys
        # awaits, so concurrent creates all read the same pre-insert count and
        # every one of them passed the cap (5 concurrent creates against
        # max_keys_per_user=1 minted 3-5 keys). The cap is what stops a user
        # rotating around per-key budgets and rate limits (AUDIT #198).
        #
        # Per owner, not global: two users creating keys concurrently must not
        # serialize. Weak references so an unbounded stream of owner ids
        # cannot grow the map — the lock is only needed while someone holds it.
        self._owner_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary())
        # Hard ceiling on cached lookups. Entries are removed on create/
        # delete/update, but a scan of nonexistent keys inserts negative
        # entries that nothing ever removes, so the read path also bounds it.
        self._max_cache_entries = 10_000

    def _sweep_cache(self, now: float) -> None:
        """Drop expired entries; if still over the cap, drop the oldest.

        Called on cache insertion, so the dict stays bounded no matter how
        many distinct (possibly bogus) keys are presented.
        """
        if len(self._cache) < self._max_cache_entries:
            return
        for h, (_info, ts) in list(self._cache.items()):
            if now - ts >= self._ttl:
                del self._cache[h]
        if len(self._cache) < self._max_cache_entries:
            return
        # Still full: everything is fresh, so evict by insertion age. dicts
        # preserve insertion order and refreshed entries are re-inserted
        # below, so the front is the least recently written.
        for h in list(self._cache)[:len(self._cache) // 2]:
            del self._cache[h]

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            # _lookup_db joins `users` to enforce owner revocation (AUDIT
            # #148), so the table must exist even when UserService.startup()
            # has not run — standalone AuthService uses (tests, tools) and
            # any startup ordering. Reuses UserService's own DDL constant so
            # the two can never drift into different shapes.
            await conn.execute(sa.text(USERS_DDL))
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
    @staticmethod
    def _expired(info: AuthInfo) -> bool:
        """True when *info* carries an expiry that has already passed.

        Wall clock, matching how ``expires_at`` is written (``time.time()``)
        and how the request path compares it.
        """
        return info.expires_at is not None and time.time() > info.expires_at

    async def authenticate(self, plaintext: str) -> AuthInfo | None:
        if hmac.compare_digest(hash_key(plaintext), self.master_hash):
            return AuthInfo(key_id="master", key_type="master", alias="master")
        h = hash_key(plaintext)
        now = time.monotonic()
        # Snapshot the eviction generation BEFORE the DB read, so an eviction
        # that lands after this point is detectable at the store below.
        gen = self._evict_gen
        hit = self._cache.get(h)
        if hit and now - hit[1] < self._ttl:
            # Budget-bound keys must always reflect the latest spend so a
            # concurrent update_spend can immediately reject further use;
            # other keys (no max_budget) keep the TTL cache for speed.
            #
            # Owner-bound keys deliberately keep the cache too (AUDIT #148).
            # Revocation does not rely on this read path: disabling a user
            # expires their keys in place and evicts each one from the cache
            # (admin PATCH -> expire_keys), so the next authenticate() misses
            # and hits the owner check in _lookup_db. Forcing a DB round-trip
            # here instead would also silently weaken H9's regression test,
            # which guards that eviction by asserting an expired key stops
            # authenticating immediately.
            info, _ts = hit
            if info is None:
                return None
            if info.max_budget is None and not self._expired(info):
                return info
        # The cached hit above mutates nothing, so it is served with no
        # synchronisation at all — the miss path below is the only one that can
        # race a revocation.
        info = await self._lookup_db(h)
        if info is not None and self._expired(info):
            # An expired credential must not authenticate no matter how it
            # reached the cache. expire_keys rotates rows in place, so the
            # AuthInfo a caller cached beforehand still carries the old,
            # still-future expiry; refusing here keeps that rotation
            # authoritative for every caller instead of only for the request
            # path that happens to re-check expires_at itself.
            info = None
        # Only the miss path can race a revocation, and the generation counter
        # is what closes it. A revocation landing between this lookup and the
        # store below used to be erased by that store: every revocation path
        # *evicts*, which is a no-op on an entry that is absent — exactly the
        # state an in-flight lookup leaves behind (AUDIT #162). Comparing the
        # generation read above with the current one turns that eviction into a
        # signal to re-read, so the store can only ever publish info read after
        # the last eviction. The loop (rather than a single re-read) covers a
        # revocation whose commit lands while the re-read's query is already
        # executing: that iteration's result predates the revocation, so it is
        # discarded and retried.
        #
        # No lock is needed. asyncio runs one task at a time and there is no
        # await between the generation check and the dict store, so an
        # eviction either happened before the check (and is retried) or lands
        # after the store (and removes what was stored). A lock here would only
        # add contention to a path the cached branch deliberately keeps free of
        # round trips.
        while self._evict_gen != gen:
            gen = self._evict_gen
            info = await self._lookup_db(h)
            if info is not None and self._expired(info):
                info = None
        self._sweep_cache(now)
        self._cache[h] = (info, now)
        return info

    async def _lookup_db(self, h: str) -> AuthInfo | None:
        # The owner check is the authoritative half of revocation (AUDIT
        # #148): a key is only as live as the account that owns it. Disabling
        # a user also expires their keys in place (see the admin PATCH
        # handler), but enforcing it here means the invariant holds even when
        # that action is bypassed — a direct DB edit, or a partial failure
        # mid-revocation.
        #
        # EXISTS rather than a LEFT JOIN on purpose: this fails CLOSED. A key
        # with no owner (owner_id IS NULL — the admin-minted ones) still
        # authenticates, but a key whose owner row is *missing* does not. An
        # outer join would read a dangling owner as "not disabled" and let the
        # credential through, which is the wrong default for an auth check.
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT v.id, v.key_alias, v.models, v.max_budget,"
                        " v.spend_to_date, v.rpm, v.tpm, v.expires_at,"
                        " v.disabled, v.owner_id FROM vkeys v"
                        " WHERE v.key_hash=:h"
                        " AND (v.owner_id IS NULL OR EXISTS ("
                        "   SELECT 1 FROM users u WHERE u.id = v.owner_id"
                        "   AND COALESCE(u.disabled, 0) = 0))"),
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
        self._drop_cached(hash_key(plaintext))

    def _drop_cached(self, h: str) -> None:
        """Drop *h*'s cached entry and record that an eviction happened.

        The generation bump is what makes eviction meaningful against an
        in-flight :meth:`authenticate` (AUDIT #162): the lookup compares the
        generation it started with against the current one before storing, so
        an eviction that lands mid-lookup invalidates the info that lookup
        read. Bumped even when *h* was not cached — an absent entry is exactly
        the state an in-flight lookup leaves behind.
        """
        self._cache.pop(h, None)
        self._evict_gen += 1

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
        models = _coerce_models(models)
        rpm = _coerce_limit(rpm, "rpm")
        tpm = _coerce_limit(tpm, "tpm")
        max_budget = _coerce_limit(max_budget, "max_budget")
        ttl_seconds = _coerce_limit(ttl_seconds, "ttl_seconds")
        kid = "k" + secrets_hex()
        now = time.time()
        # ``ttl_seconds is not None`` (not a truthy test): 0 is rejected by
        # _coerce_limit, and None is the documented "no expiry" value, so the
        # create path and the update path now spell the same rule the same way
        # (AUDIT #202).
        expires = now + ttl_seconds if ttl_seconds is not None else None
        if owner_id is None:
            # Admins mint unowned keys and are exempt from the cap.
            await self._insert_key(kid, plaintext, alias, models, max_budget,
                                   rpm, tpm, expires, owner_id, now)
        else:
            # Cap live keys per owner. Without this a user mints unbounded keys
            # and rotates around any per-key budget or rate limit.
            #
            # The count and the insert must not be separable: count_keys
            # awaits, so concurrent creates all read the same pre-insert count
            # and every one of them passed the cap — 5 concurrent creates
            # against max_keys_per_user=1 minted 3 (AUDIT #198). The lock is
            # held across both, so the row a holder inserts is visible to the
            # next holder's count. Per owner rather than per service, so two
            # users creating keys at the same time still run concurrently.
            async with self._owner_lock(owner_id):
                if await self.count_keys(owner_id) >= self.max_keys_per_user:
                    raise ValueError(
                        f"key limit reached ({self.max_keys_per_user} live keys); "
                        f"delete or expire an existing key first")
                await self._insert_key(kid, plaintext, alias, models, max_budget,
                                       rpm, tpm, expires, owner_id, now)
        # a failed guess of this plaintext may sit in the negative cache for the
        # TTL; evict so the freshly created key authenticates immediately
        self._drop_cached(hash_key(plaintext))
        return plaintext, kid

    async def _insert_key(self, kid: str, plaintext: str, alias: str,
                          models: list[str] | None, max_budget: float | None,
                          rpm: float | None, tpm: float | None,
                          expires: float | None, owner_id: str | None,
                          now: float) -> None:
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

    def _owner_lock(self, owner_id: str) -> asyncio.Lock:
        """The per-owner create lock, created on first use.

        Weak-valued so a long-lived process serving many distinct owner ids
        does not accumulate a lock per id: the only strong reference is the one
        the create holding it keeps. The dict cannot hand out a *different*
        lock to a concurrent caller, because a lock is only dropped once no
        create holds a reference to it.
        """
        lock = self._owner_locks.get(owner_id)
        if lock is None:
            lock = self._owner_locks[owner_id] = asyncio.Lock()
        return lock

    async def delete_key(self, key_id: str) -> bool:
        # fetch the hash first so the cache entry can be evicted; a deleted key
        # must stop authenticating immediately, not after the TTL lapses
        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text("SELECT key_hash FROM vkeys WHERE id=:id"),
                                      {"id": key_id})).first()
        async with self.engine.begin() as conn:
            res = await conn.execute(sa.text("DELETE FROM vkeys WHERE id=:id"), {"id": key_id})
        if row is not None:
            self._drop_cached(row[0])
        return res.rowcount > 0

    async def get_key(self, key_id: str) -> dict | None:
        for k in await self.list_keys():
            if k["id"] == key_id:
                return k
        return None

    UPDATABLE_FIELDS = ("max_budget", "rpm", "tpm", "models", "expires_at",
                        "ttl_seconds")

    #: The subset of ``UPDATABLE_FIELDS`` a key's *owner* may change on their
    #: own key. Every other field is an operator control: ``max_budget``,
    #: ``rpm``, ``tpm`` and ``models`` are exactly how an operator caps a
    #: tenant, so letting the tenant clear them defeats the cap (AUDIT #220).
    #: ``expires_at``/``ttl_seconds`` are excluded for the same reason — an
    #: owner extending their own expiry defeats a time-boxed grant.
    OWNER_FACING_FIELDS = ()

    async def update_key(self, key_id: str, fields: dict,
                         allow: tuple[str, ...] | None = None) -> dict | None:
        """Patch editable fields (absent = unchanged; explicit null = clear).

        ``ttl_seconds`` is a relative duration (seconds from now); it is
        converted to an absolute ``expires_at`` epoch. ``expires_at`` (absolute
        epoch) is still accepted for backward compatibility. When both are
        present, ``ttl_seconds`` wins.

        ``allow`` restricts the writable set. ``None`` (the admin path) permits
        every field in ``UPDATABLE_FIELDS``; a tuple permits only those names
        and *raises* on anything else, so a second call site cannot silently
        forget the owner/admin boundary (AUDIT #220).

        Returns the updated key dict, or None when the id is unknown. Cache is
        evicted so the new limits apply immediately.
        """
        if allow is not None:
            refused = [k for k in fields if k not in allow]
            if refused:
                raise ValueError(
                    f"field '{refused[0]}' may not be set by this actor")
        sets: dict[str, object] = {}
        for name in self.UPDATABLE_FIELDS:
            if name not in fields:
                continue
            val = fields[name]
            if name == "models":
                val = __import__("json").dumps(_coerce_models(val) or [])
            elif name == "ttl_seconds":
                # Relative duration -> absolute epoch; ttl_seconds is not a
                # DB column, it maps to expires_at. ``is not None`` matches
                # create_key, and 0 never reaches here — _coerce_limit rejects
                # it, because 0 could only mean "expire now" on this path while
                # it meant "never expires" on create (AUDIT #202). None remains
                # the documented way to clear an expiry.
                if val is not None:
                    sets["expires_at"] = time.time() + _coerce_limit(val, "ttl_seconds")
                else:
                    sets["expires_at"] = None
                continue
            elif val is not None:
                # Validate rather than letting float()/int() raise: a malformed
                # value used to escape as an uncaught ValueError/TypeError and
                # surface as HTTP 500, and int(1.5) silently truncated.
                num = _coerce_limit(val, name)
                if num is not None and name not in ("max_budget", "expires_at"):
                    if num != int(num):
                        raise ValueError(f"{name} must be a whole number")
                    num = int(num)
                val = num
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
        self._drop_cached(row[0])
        return await self.get_key(key_id)

    async def set_disabled(self, key_id: str, disabled: bool) -> bool:
        """Disable/enable a key and evict its cached auth info immediately.

        Returns False when the id is unknown, so the caller can answer 404
        instead of reporting success for a row that was never touched.
        """
        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text("SELECT key_hash FROM vkeys WHERE id=:id"),
                                      {"id": key_id})).first()
        if row is None:
            return False
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE vkeys SET disabled=:d, updated_at=:now WHERE id=:id"),
                {"d": int(disabled), "id": key_id, "now": time.time()},
            )
        self._drop_cached(row[0])
        return True

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

    async def apply_spend_trueup(self, key_id: str, add_cost: float) -> None:
        """Retroactive spend correction (pricing true-up).

        Unlike :meth:`update_spend` this is UNCONDITIONAL: the spend being
        applied already happened upstream, so a max_budget cap must not reject
        it — an over-budget key stays over budget and is simply blocked from
        FUTURE requests. No-op for master or non-positive deltas.
        """
        if key_id == "master" or add_cost <= 0:
            return
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE vkeys SET spend_to_date = spend_to_date + :c,"
                        " updated_at = :now WHERE id = :id"),
                {"c": add_cost, "id": key_id, "now": time.time()},
            )
        for info, _ts in self._cache.values():
            if info is not None and info.key_id == key_id:
                info.spend_to_date += add_cost

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

    async def count_keys(self, owner_id: str | None, alias: str | None = None) -> int:
        """Count live (non-expired, non-revoked) keys for an owner.

        ``owner_id=None`` selects *unowned* keys — the ones minted for admin
        sessions, which have a NULL owner. Those are otherwise invisible to
        both this query and the per-user cap in :meth:`create_key`, so they
        accumulated without bound.
        """
        now = time.time()
        # `= NULL` never matches, so unowned keys need an explicit IS NULL.
        owner_clause = "owner_id IS NULL" if owner_id is None else "owner_id = :o"
        sql = (f"SELECT COUNT(*) FROM vkeys WHERE {owner_clause}"
               " AND (expires_at IS NULL OR expires_at > :now)"
               " AND COALESCE(disabled, 0) = 0")
        params: dict[str, object] = {"now": now}
        if owner_id is not None:
            params["o"] = owner_id
        if alias is not None:
            sql += " AND key_alias = :a"
            params["a"] = alias
        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text(sql), params)).first()
        return int(row[0]) if row else 0

    async def expire_keys(self, owner_id: str | None, alias: str | None = None,
                          keep_newest: int = 0) -> int:
        """Expire an owner's oldest keys, keeping the newest ``keep_newest``.

        Caps how many live credentials one account can accumulate (e.g.
        playground keys minted on every login). Expiring rather than deleting
        preserves audit history. Returns the number expired.

        ``owner_id=None`` targets unowned keys (admin sessions), which need
        ``IS NULL`` — see :meth:`count_keys`.
        """
        now = time.time()
        owner_clause = "owner_id IS NULL" if owner_id is None else "owner_id = :o"
        # key_hash is selected alongside id so the expired credentials can be
        # evicted from the auth cache below; without that, the row reads
        # expired while a cached AuthInfo keeps authenticating for the whole
        # TTL (playground keys have no max_budget, so they take exactly the
        # cached branch in authenticate()).
        sql = (f"SELECT id, key_hash FROM vkeys WHERE {owner_clause}"
               " AND (expires_at IS NULL OR expires_at > :now)"
               " AND COALESCE(disabled, 0) = 0")
        params: dict[str, object] = {"now": now}
        if owner_id is not None:
            params["o"] = owner_id
        if alias is not None:
            sql += " AND key_alias = :a"
            params["a"] = alias
        sql += " ORDER BY created_at DESC"
        async with self.engine.begin() as conn:
            rows = (await conn.execute(sa.text(sql), params)).all()
            stale = rows[keep_newest:]
            for row in stale:
                await conn.execute(
                    sa.text("UPDATE vkeys SET expires_at = :now,"
                            " updated_at = :now WHERE id = :id"),
                    {"now": now, "id": row[0]})
        for row in stale:
            self._drop_cached(row[1])
        return len(stale)

    async def key_owner(self, key_id: str) -> str | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT owner_id FROM vkeys WHERE id = :id"),
                {"id": key_id})).first()
        return row[0] if row else None


def secrets_hex() -> str:
    import secrets as _s
    return _s.token_hex(8)
