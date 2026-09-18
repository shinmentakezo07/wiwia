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
from typing import TYPE_CHECKING, Any

import structlog

from wiwi.core.recovery import CircuitBreaker
from wiwi.providers import cline_oauth

if TYPE_CHECKING:
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

    async def _refresh_locked(self, name: str, *, require_due: bool) -> bool:
        """Serialize a refresh for ``name`` through the shared lock + circuit.

        The sweep (``require_due=True``) and the on-demand 401 hook
        (``require_due=False``) funnel through here so both share one lock and
        one circuit breaker. Cline's refresh tokens are single-use, so the
        record is re-read *under* the lock: a rotation that landed while we
        waited is honored instead of being repeated with the token it just
        consumed (AUDIT #169).

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
            if self._circuit.blocked(name):
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
            # Stop refreshing — the user must re-login.
            self._circuit.mark_dead(name)
            log.error("cline_auto_refresh_unrecoverable", provider=name,
                      code=result.get("code"))
            return False
        # Success — write new tokens.
        await self._update_secret(name, result["access_token"])
        record["refresh_token"] = result["refresh_token"]
        if result.get("expires_at"):
            record["expires_at"] = result["expires_at"]
        await self._state.config_store.set_setting(f"cline_oauth:{name}", record)
        self._generations[name] = self._generations.get(name, 0) + 1
        self._circuit.clear(name)
        log.info("cline_auto_refreshed", provider=name)
        return True

    async def _update_secret(self, provider: str, secret: str) -> None:
        """Update every pool key's secret in memory + DB for a Cline provider.

        A Cline provider's key pool entries all authenticate with the same
        OAuth account (WorkOS), so a rotation invalidates every entry's
        cached access token.  Updating only ``keys[0]`` (the historical
        behavior) caused on-demand 401-refresh retries to pick a still-stale
        sibling key from the round-robin cursor.
        """
        acct = self._state.router.providers.get(provider)
        if acct is None or not acct.keys:
            return
        for k in acct.keys:
            k.secret = secret
            k.status = "active"
            k.cooldown_until = 0.0
            await self._state.config_store.update_key_secret(provider, k.label, secret)

    def _trip_circuit(self, name: str) -> None:
        self._circuit.trip(name)


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


_workers: dict[int, ClineAutoRefresh] = {}


def _worker_for(state: AppState) -> ClineAutoRefresh:
    """Resolve the shared worker for *state*.

    The lifespan-owned ``state.cline_refresh`` wins when set, so the
    background sweeper, the gateway 401 hook, and any other caller share one
    lock and one circuit breaker. Fallback (tests, pre-lifespan use): a
    lazily cached instance.
    """
    shared = getattr(state, "cline_refresh", None)
    if shared is not None:
        return shared
    return _workers.setdefault(id(state), ClineAutoRefresh(state))
