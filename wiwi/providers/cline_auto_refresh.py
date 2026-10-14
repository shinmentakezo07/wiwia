"""Background auto-refresh for Cline OAuth tokens.

Cline uses single-use rotating refresh tokens — every refresh call consumes
the old refresh_token and returns a new one. To avoid burning rotations, we
refresh ONLY when the access token is inside the 5-minute lead window before
expiry (``REFRESH_LEAD_S``), never on a fixed interval.

The sweep runs every ``TICK_S`` seconds over all configured Cline providers
that have stored OAuth state. A per-provider ``asyncio.Lock`` prevents
concurrent refreshes for the same provider. A simple circuit breaker backs
off exponentially on repeated failures (cap 4h). Unrecoverable errors
(``invalid_grant`` / ``invalid_request``) stop further retries until the
user re-connects.

The on-demand 401 hook (:func:`refresh_for_provider`) resolves the *same*
worker instance the sweeper uses (see :func:`_worker_for`), so both paths
share one lock and one circuit — without that, a sweeper tick and a client
401 landing in the same lead window would both present the same
single-use refresh token and the loser's ``invalid_grant`` would mark the
provider permanently dead (AUDIT #169).
"""

from __future__ import annotations

import asyncio
import time
import weakref
from typing import TYPE_CHECKING, Any

import structlog

from wiwi.core.recovery import CircuitBreaker
from wiwi.providers import cline_oauth

if TYPE_CHECKING:
    from wiwi.router.router import ProviderKey
    from wiwi.server.app import AppState

log = structlog.get_logger("wiwi.cline_auto_refresh")

TICK_S = 60.0
FIRST_SWEEP_DELAY_S = 10.0
CIRCUIT_BASE_S = 5 * 60
CIRCUIT_CAP_S = 4 * 60 * 60


class ClineAutoRefresh:
    """Background task that proactively refreshes Cline tokens before expiry."""

    def __init__(self, state: AppState) -> None:
        self._state = state
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        # Successful-rotation counter per provider. Both the sweeper and the
        # on-demand 401 hook share one worker, so this is how the loser of a
        # lock race learns the winner already rotated (AUDIT #169).
        self._generations: dict[str, int] = {}
        # Why each provider went permanently dead (the upstream error code, e.g.
        # ``invalid_grant``). The admin surface reports it verbatim so an
        # operator learns *why* a re-login is required instead of being told
        # only that the refresh failed.
        self._dead_codes: dict[str, str] = {}
        self._circuit = CircuitBreaker(base_s=CIRCUIT_BASE_S, cap_s=CIRCUIT_CAP_S,
                                       clock=time.time)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="cline-auto-refresh")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        try:
            await asyncio.sleep(FIRST_SWEEP_DELAY_S)
            while not self._stop.is_set():
                try:
                    await self._sweep()
                except Exception:
                    log.exception("cline_auto_refresh_sweep_error")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _sweep(self) -> int:
        """Iterate over all cline providers with stored OAuth state.

        Returns the number of providers checked.
        """
        cs = self._state.config_store
        if cs is None:
            return 0
        checked = 0
        for name, acct in list(self._state.router.providers.items()):
            if acct.provider_type != "cline":
                continue
            record = await cs.get_setting(f"cline_oauth:{name}")
            if not record or not record.get("refresh_token"):
                continue
            checked += 1
            try:
                await self._check_provider(name, record)
            except Exception:
                log.exception("cline_auto_refresh_provider_error", provider=name)
        return checked

    async def _check_provider(self, name: str, record: dict[str, Any]) -> None:
        """Refresh one provider's token if it's about to expire."""
        expires_epoch = cline_oauth.parse_expires_at(record.get("expires_at"))
        if expires_epoch is None or not cline_oauth.expires_within_lead(expires_epoch):
            return  # not due for refresh
        await self._refresh_locked(name, require_due=True)

    async def refresh_now(self, name: str) -> bool:
        """On-demand refresh for the gateway's 401 hook.

        The upstream rejected the access token, so the stored expiry is
        irrelevant — rotate unconditionally, under the same lock and circuit
        breaker the sweeper uses. Returns True when the stored record is
        fresh afterwards (rotated here, or rotated by the sweeper while this
        call waited for the lock), so the caller may retry with it.
        """
        return await self._refresh_locked(name, require_due=False)

    async def _refresh_locked(self, name: str, *, require_due: bool,
                              force: bool = False) -> bool:
        """Serialize a refresh for ``name`` through the shared lock + circuit.

        The sweep (``require_due=True``), the on-demand 401 hook and the admin
        refresh endpoint (both ``require_due=False``) funnel through here so
        all three share one lock and one circuit breaker. Cline's refresh tokens
        are single-use, so the record is re-read *under* the lock: a rotation
        that landed while we waited is honored instead of being repeated with
        the token it just consumed (AUDIT #169).

        ``force`` bypasses the circuit breaker's gate and is set only by the
        admin refresh endpoint: an operator explicitly asking for a rotation is
        the documented way out of a tripped (or permanently dead) provider, so
        the manual path must stay reachable while the automatic ones respect the
        backoff. It still takes the lock, and still honors the generation check
        — ``force`` relaxes *backoff*, never the single-use-grant bookkeeping.

        Returns True when the stored record is fresh afterwards (we rotated
        it, or another caller did while we waited), False when nothing was
        rotated — circuit-blocked, no stored refresh token, not due, or the
        refresh failed.
        """
        lock = self._locks.setdefault(name, asyncio.Lock())
        generation = self._generations.get(name, 0)
        async with lock:
            # Circuit state is read under the lock so a failure that landed
            # while we waited (a sibling refresh tripping it) is honored.
            if not force and self._circuit.blocked(name):
                return False
            if self._generations.get(name, 0) != generation:
                # Someone rotated while we waited for the lock: the record is
                # already fresh, and the refresh_token we would present has
                # been consumed by that rotation.
                return True
            cs = self._state.config_store
            if cs is None:
                return False
            record = await cs.get_setting(f"cline_oauth:{name}")
            if not record or not record.get("refresh_token"):
                return False
            if require_due:
                expires_epoch = cline_oauth.parse_expires_at(record.get("expires_at"))
                if expires_epoch is None or not cline_oauth.expires_within_lead(
                        expires_epoch):
                    return False
            return await self._do_refresh(name, record)

    async def _do_refresh(self, name: str, record: dict[str, Any]) -> bool:
        """Call the refresh endpoint and persist the result.

        Callers must hold ``self._locks[name]``. Returns True when the tokens
        were rotated and stored.
        """
        result = await cline_oauth.refresh_token(record["refresh_token"])
        if result is None:
            self._trip_circuit(name)
            log.warning("cline_auto_refresh_transient", provider=name)
            return False
        if result.get("error") == "unrecoverable_refresh_error":
            # Stop refreshing — the user must re-login. The code is kept so the
            # admin surface can say *why* (the handler no longer calls
            # ``cline_oauth.refresh_token`` itself and cannot see it).
            self._circuit.mark_dead(name)
            self._dead_codes[name] = str(result.get("code") or "")
            log.error("cline_auto_refresh_unrecoverable", provider=name,
                      code=result.get("code"))
            return False
        # Success — the grant is consumed and the replacement lives only in
        # memory now, so the two writes below are ordered deliberately:
        #
        # 1. the OAuth record (the new single-use grant) FIRST. It is the only
        #    copy of the replacement; if the pool-key write then fails we still
        #    hold a refreshable provider on the next run.
        # 2. the pool key(s) second.
        #
        # The reverse order (the historical one) meant a failing record write
        # left the record holding the *consumed* grant: the next tick presented
        # a dead token, got ``invalid_grant``, and marked the provider
        # permanently dead — one DB error cost the operator their login.
        #
        # Both writes are guarded so neither can escape as an exception: the
        # on-demand 401 hook is called from ``core/gateway.py`` without a
        # try/except around it, so an escaping write turned an upstream 401 into
        # a gateway 500 instead of surfacing the upstream error.
        #
        # ``owned`` is captured BEFORE the mutation above: it fingerprints the
        # token this record issued *previously*, which is what tells us which
        # pool keys the rotation is allowed to overwrite.
        owned = record.get("access_fingerprint")
        record["refresh_token"] = result["refresh_token"]
        record["expires_at"] = cline_oauth.resolve_expires_at(
            result.get("expires_in"))
        record["access_fingerprint"] = cline_oauth.access_fingerprint(
            result["access_token"])
        try:
            await self._state.config_store.set_setting(
                f"cline_oauth:{name}", record)
        except Exception as e:  # noqa: BLE001 — never break the 401 hook path
            log.error("cline_auto_refresh_record_persist_failed", provider=name,
                      error=str(e))
            return False
        try:
            await self.store_access_token(name, result["access_token"],
                                          owned=owned)
        except Exception as e:  # noqa: BLE001 — same: the record is now good
            log.error("cline_auto_refresh_key_persist_failed", provider=name,
                      error=str(e))
            return False
        self._generations[name] = self._generations.get(name, 0) + 1
        self._circuit.clear(name)
        log.info("cline_auto_refreshed", provider=name)
        return True

    def _owned_keys(self, provider: str,
                    owned: str | None) -> list[ProviderKey]:
        """The pool keys this provider's OAuth record is allowed to rotate.

        A Cline provider holds ONE OAuth account but a POOL of keys, and a
        rotation invalidates every key that caches that account's access token —
        but not a key the operator added as a raw API key. Writing the rotated
        token into every key destroyed those unrelated credentials (a raw key
        was permanently replaced by a WorkOS bearer), so ownership is tracked
        by the fingerprint the record stamped on the token it last issued:

        * a fingerprint present and matching → exactly those keys;
        * no fingerprint yet (a record written before this was tracked, or the
          very first rotation after a connect) → every key, because that is
          the only reading under which a pool whose keys all hold the same
          account's token stays consistent;
        * a fingerprint present but matching nothing → ``keys[0]`` only. The
          pool has drifted (keys edited out of band); rotating all of them
          would re-clobber the credentials the fingerprint was protecting.
        """
        acct = self._state.router.providers.get(provider)
        if acct is None or not acct.keys:
            return []
        if not owned:
            return list(acct.keys)
        matches = [k for k in acct.keys
                   if cline_oauth.access_fingerprint(k.secret) == owned]
        return matches if matches else list(acct.keys[:1])

    async def store_access_token(self, provider: str, secret: str, *,
                                 owned: str | None = None) -> int:
        """Write *secret* into every pool key this OAuth record owns.

        Returns the number of keys written (0 when the provider has no keys).

        Every rotation path funnels through here — the background sweeper, the
        gateway 401 hook, and the admin connect/refresh endpoints — because
        updating only ``keys[0]`` (the historical behavior) caused on-demand
        401-refresh retries to pick a still-stale sibling key from the
        round-robin cursor, while updating *all* keys destroyed raw
        non-OAuth credentials. See :meth:`_owned_keys`.
        """
        targets = self._owned_keys(provider, owned)
        for k in targets:
            k.secret = secret
            k.status = "active"
            k.cooldown_until = 0.0
            try:
                await self._state.config_store.update_key_secret(
                    provider, k.label, secret)
            except Exception as e:  # noqa: BLE001 — YAML/DB path varies
                # YAML-defined provider: no DB row to update. The in-memory
                # assignment above is still correct for this process; refusing
                # here would break OAuth refresh on YAML-only deployments.
                structlog.get_logger().warning(
                    "oauth_secret_not_persisted", provider=provider, key=k.label,
                    reason="provider is YAML-defined; rotation is in-memory only",
                    error=type(e).__name__)
        return len(targets)

    def _trip_circuit(self, name: str) -> None:
        self._circuit.trip(name)

    def dead_code(self, name: str) -> str | None:
        """The upstream error code that killed ``name``'s circuit, if any.

        Set by :meth:`_do_refresh` when the grant was rejected outright
        (``invalid_grant`` / ``invalid_request``). The admin refresh endpoint
        no longer calls :func:`wiwi.providers.cline_oauth.refresh_token`
        itself, so it cannot see the code directly and reads it from here.
        """
        return self._dead_codes.get(name)

    def forget(self, provider: str) -> None:
        """Drop all per-provider state for ``provider``.

        ``_locks`` / ``_generations`` / ``_dead_codes`` and the circuit breaker's
        ``_streaks`` / ``_until`` / ``_dead`` entries only ever grew: nothing
        popped a provider when the admin disconnected it or deleted the account,
        so every connect-then-disconnect cycle leaked a lock, a counter and a
        breaker entry that could never be reclaimed — and, worse, left a
        provider whose circuit is still ``dead`` looking permanently unrefreshable
        under a *reused* name.

        A refresh that is *in flight* keeps its entry. Popping it mid-refresh
        would hand the next caller a brand-new lock and a zeroed generation
        counter: the in-flight holder captured the generation *before* entering
        and re-checks it under the lock to learn that a sibling already rotated
        the single-use grant (AUDIT #169), so a zeroed counter would make it
        present that same consumed grant a second time. The entry is reclaimed on
        a later, idle call — and it is reused verbatim if the operator reconnects
        the same provider name in the meantime.
        """
        lock = self._locks.get(provider)
        if lock is not None and lock.locked():
            return
        self._locks.pop(provider, None)
        self._generations.pop(provider, None)
        self._dead_codes.pop(provider, None)
        self._circuit.clear(provider)


def refresh_for_provider(state) -> callable:
    """Build the async hook the gateway calls when a Cline request 401s.

    Returns ``hook(provider_name, key_label) -> bool``: True when the stored
    access token was rotated and the caller should retry, False when nothing
    was rotated (caller surfaces the original 401).

    The hook delegates to :func:`_worker_for` — the *same* worker the
    background sweeper uses — so both paths share one per-provider lock and
    one circuit breaker. Cline's refresh tokens are single-use, so a sweeper
    tick and a client 401 landing in the same lead window must not both POST
    the same token: the loser's ``invalid_grant`` would mark the provider
    permanently dead until a human re-authenticates (AUDIT #169).

    The hook is injected into ``Gateway._on_demand_cline_refresh`` at app
    startup so the gateway can call it without depending on the full
    AppState — see ``wiwi.server.app`` for the wiring.
    """
    async def hook(provider_name: str, key_label: str) -> bool:
        return await _worker_for(state).refresh_now(provider_name)

    return hook


async def refresh_key_now(state: AppState, provider: str) -> dict[str, Any]:
    """Operator-triggered rotation — the admin refresh endpoint's entry point.

    Mirrors ``wiwi.providers.workbuddy_auto_refresh.refresh_key_now`` and the
    Hermes equivalent: the handler must not call
    :func:`wiwi.providers.cline_oauth.refresh_token` itself. It used to, and
    that put a fourth refresh path next to the sweeper, the 401 hook and the
    worker's own rotation — with its own no-lock, no-circuit execution. A
    60 s sweeper tick and an operator clicking "Refresh" inside the same
    5-minute lead window both POSTed the same single-use ``refreshToken``; the
    loser's ``invalid_grant`` marked the provider permanently dead (and the
    admin path's own ``invalid_grant`` did not even mark it dead — it failed
    silently while the sweeper kept burning grants).

    Returns ``{"ok", "relogin_required", "error"}``. ``relogin_required`` is
    True only when the grant is permanently rejected, so the handler can map
    it to 401 and a transient failure to 502.
    """
    worker = _worker_for(state)
    if worker._circuit.dead(provider):
        return {"ok": False, "relogin_required": True,
                "error": _dead_message(worker, provider)}
    cs = state.config_store
    record = await cs.get_setting(f"cline_oauth:{provider}") if cs else None
    if not record or not record.get("refresh_token"):
        return {"ok": False, "relogin_required": False,
                "error": f"provider '{provider}' has no stored Cline tokens; "
                         "connect first"}
    rotated = await worker._refresh_locked(provider, require_due=False, force=True)
    if rotated:
        return {"ok": True, "relogin_required": False, "error": ""}
    if worker._circuit.dead(provider):
        return {"ok": False, "relogin_required": True,
                "error": _dead_message(worker, provider)}
    return {"ok": False, "relogin_required": False,
            "error": "Cline token refresh failed (transient); retry later"}


def _dead_message(worker: ClineAutoRefresh, provider: str) -> str:
    code = worker.dead_code(provider)
    return (f"Cline refresh token rejected (code: {code or 'invalid_grant'}) "
            "— re-login required")


def forget_provider(state: AppState, provider: str) -> None:
    """Drop ``provider``'s refresh state from the shared worker (if any).

    The app-facing wrapper around :meth:`ClineAutoRefresh.forget`, so callers
    never touch the worker's privates. Used by the disconnect / delete paths:
    a dead Cline account must become refreshable again the moment its
    credential is replaced, and ``CircuitBreaker._dead`` otherwise survives
    forever.
    """
    _worker_for(state).forget(provider)


# The fallback worker cache, keyed on ``id(state)``. ``id()`` is NOT unique for
# the lifetime of the process — CPython recycles the address of a collected
# object, so a re-created AppState could otherwise be handed a worker still
# bound to the *dead* state (with its dead circuit and stale generation
# counters). Each entry therefore carries a weakref to its state: dead
# referents are pruned, and a stale id is rebuilt rather than reused.
_workers: dict[int, tuple[weakref.ref, ClineAutoRefresh]] = {}


def _worker_for(state: AppState) -> ClineAutoRefresh:
    """Resolve the shared worker for *state*.

    The lifespan-owned ``state.cline_refresh`` wins when set, so the
    background sweeper, the gateway 401 hook, the admin refresh endpoint and
    any other caller share one lock and one circuit breaker. Fallback (tests,
    pre-lifespan use): a lazily cached instance.
    """
    shared = getattr(state, "cline_refresh", None)
    if shared is not None:
        return shared
    ident = id(state)
    # Prune entries whose state has been collected (bounded: only runs on the
    # fallback path, and only over this tiny dict).
    for key, (ref, _worker) in list(_workers.items()):
        if ref() is None:
            del _workers[key]
    entry = _workers.get(ident)
    if entry is not None and entry[0]() is state:
        return entry[1]
    worker = ClineAutoRefresh(state)
    _workers[ident] = (weakref.ref(state), worker)
    return worker
