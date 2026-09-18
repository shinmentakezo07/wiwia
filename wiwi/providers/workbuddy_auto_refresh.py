"""Background + on-demand token refresh for WorkBuddy accounts.

Mirrors :mod:`wiwi.providers.cline_auto_refresh` with one structural
difference: a Cline provider holds ONE OAuth account (all pool keys share
it, record lives in ``cline_oauth:<provider>``), while a WorkBuddy provider
holds one account PER POOL KEY — the key's secret IS the auth JSON (nested
shape, see :mod:`wiwi.providers.workbuddy_auth`). So the sweep refreshes
per key, not per provider, and a rotation writes the updated auth JSON back
into that key's secret (in-memory + DB via ``config_store``).

WorkBuddy refresh tokens rotate on use, so the sweeper only refreshes keys
whose access token is inside ``REFRESH_LEAD_S`` (5 min) of expiry. A key
whose expiry is *unknown* (no/non-numeric ``expiresAt``) is never due — see
:func:`wiwi.providers.workbuddy_auth.expires_within_lead`; treating it as due
would burn one rotation plus one DB write per tick forever and could call
``mark_dead`` on a key that never expired (AUDIT #170). A per-key lock
prevents concurrent refreshes; a circuit breaker backs off exponentially on
repeated failures; unrecoverable failures (session dead, missing
refreshToken) stop retrying until the user replaces the secret.

All three rotation paths — the sweeper, the gateway's on-demand 401 hook, and
the admin refresh endpoint — resolve one worker via :func:`_worker_for`, so
they share a single per-key lock and circuit breaker.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

from wiwi.core.recovery import CircuitBreaker
from wiwi.providers.workbuddy_auth import (
    REFRESH_LEAD_S,
    WorkBuddyAuth,
    WorkBuddyAuthError,
    expires_within_lead,
    parse_auth,
    refresh_token,
)

if TYPE_CHECKING:
    from wiwi.server.app import AppState

log = structlog.get_logger("wiwi.workbuddy_auto_refresh")

TICK_S = 60.0
FIRST_SWEEP_DELAY_S = 10.0
CIRCUIT_BASE_S = 5 * 60
CIRCUIT_CAP_S = 4 * 60 * 60


class WorkBuddyAutoRefresh:
    """Background task that proactively refreshes WorkBuddy tokens."""

    def __init__(self, state: AppState) -> None:
        self._state = state
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # Successful-rotation counter per key. The sweeper, the gateway 401
        # hook, and the admin refresh call all share one worker, so this is
        # how the loser of a lock race learns the winner already rotated.
        self._generations: dict[tuple[str, str], int] = {}
        self._circuit = CircuitBreaker(base_s=CIRCUIT_BASE_S, cap_s=CIRCUIT_CAP_S,
                                       clock=time.time)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="workbuddy-auto-refresh")

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
                    log.exception("workbuddy_auto_refresh_sweep_error")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _sweep(self) -> int:
        """Iterate over every workbuddy pool key. Returns keys checked."""
        if self._state.config_store is None:
            return 0
        checked = 0
        for name, acct in list(self._state.router.providers.items()):
            if acct.provider_type != "workbuddy":
                continue
            for key in list(acct.keys):
                checked += 1
                try:
                    await self._check_key(name, key.label)
                except Exception:
                    log.exception("workbuddy_auto_refresh_key_error",
                                  provider=name, label=key.label)
        return checked

    async def _check_key(self, provider: str, label: str) -> None:
        """Refresh one pool key if its known expiry is inside the lead window."""
        acct = self._state.router.providers.get(provider)
        key = acct.get_key(label) if acct else None
        if key is None:
            return
        try:
            auth = parse_auth(key.secret)
        except WorkBuddyAuthError:
            return  # bare-token secret or garbage: nothing to refresh
        if auth.refresh_token == "" or not expires_within_lead(
                auth.expires_at, REFRESH_LEAD_S):
            return  # not due (unknown expiry, or nothing to rotate)
        await self.refresh_now(provider, label, require_due=True)

    async def refresh_now(self, provider: str, label: str, *,
                          require_due: bool) -> bool:
        """Rotate one pool key, serialized through the shared lock + circuit.

        Every rotation path — the sweeper (``require_due=True``), the gateway
        401 hook, and the admin refresh call (both ``require_due=False``) —
        funnels through :meth:`_rotate_locked`, so they share one per-key
        lock and one circuit breaker. WorkBuddy refresh tokens rotate on use,
        so the key secret is re-read *under* the lock: a rotation that landed
        while we waited is honored instead of being repeated with the token
        it just consumed.

        Returns True when the key holds a fresh token afterwards (we rotated
        it, or another caller did while we waited).
        """
        fresh, _ = await self._rotate_locked(provider, label,
                                             require_due=require_due)
        return fresh

    async def _rotate_locked(self, provider: str, label: str, *,
                             require_due: bool,
                             force: bool = False) -> tuple[bool, str]:
        """The single lock-guarded rotation path. Returns ``(fresh, error)``.

        ``fresh`` is True when the key holds a fresh token afterwards —
        rotated here, or rotated by another caller while this one waited for
        the lock (in which case ``error`` is empty). When ``fresh`` is False,
        ``error`` explains why, for the admin surface.

        ``force`` bypasses the circuit breaker's gate. Only the admin
        refresh endpoint sets it: an operator explicitly asking to rotate is
        the documented way out of a tripped (or permanently dead) circuit,
        so the manual path must stay reachable while the automatic ones
        respect the backoff. It still takes the lock.
        """
        ident = (provider, label)
        lock = self._locks.setdefault(ident, asyncio.Lock())
        generation = self._generations.get(ident, 0)
        async with lock:
            # Circuit state is read under the lock so a failure that landed
            # while we waited (a sibling refresh tripping it) is honored.
            if not force and self._circuit.blocked(ident):
                return False, "blocked by the refresh circuit breaker"
            if self._generations.get(ident, 0) != generation:
                # Someone rotated while we waited for the lock: the secret is
                # already fresh and the refreshToken we would present has
                # been consumed by that rotation.
                return True, ""
            acct = self._state.router.providers.get(provider)
            key = acct.get_key(label) if acct else None
            if key is None:
                return False, f"unknown key '{label}'"
            try:
                auth = parse_auth(key.secret)
            except WorkBuddyAuthError as e:
                return False, f"secret is not WorkBuddy auth JSON: {e}"
            if not auth.refresh_token:
                return False, "secret has no refreshToken — re-import required"
            if require_due and not expires_within_lead(auth.expires_at,
                                                       REFRESH_LEAD_S):
                return False, "not due for refresh"
            outcome = await refresh_token(auth)
            if not outcome.ok:
                if outcome.unrecoverable:
                    self._circuit.mark_dead(ident)
                    log.error("workbuddy_auto_refresh_unrecoverable",
                              provider=provider, label=label, err=outcome.error)
                else:
                    self._trip_circuit(ident)
                    log.warning("workbuddy_auto_refresh_transient",
                                provider=provider, label=label, err=outcome.error)
                return False, outcome.error
            await self._write_secret(provider, label, outcome.auth)
            self._generations[ident] = self._generations.get(ident, 0) + 1
            self._circuit.clear(ident)
            log.info("workbuddy_auto_refreshed", provider=provider, label=label)
            return True, ""

    async def _write_secret(self, provider: str, label: str,
                            auth: WorkBuddyAuth) -> None:
        """Persist a rotated auth: in-memory key secret + DB config store."""
        acct = self._state.router.providers.get(provider)
        key = acct.get_key(label) if acct else None
        if key is None:
            return
        secret = auth.to_secret()
        key.secret = secret
        key.status = "active"
        key.cooldown_until = 0.0
        if self._state.config_store is not None:
            await self._state.config_store.update_key_secret(provider, label, secret)

    def _trip_circuit(self, ident: tuple[str, str]) -> None:
        self._circuit.trip(ident)


def refresh_for_provider(state: AppState) -> Any:
    """Build the on-demand refresh hook the gateway calls on a 401.

    Returns an async ``hook(provider_name, key_label) -> bool``: True when
    that key's access token was rotated (caller should retry with the fresh
    secret), False otherwise. Delegates to :func:`_worker_for` — the *same*
    worker the sweeper uses — so both share one per-key lock and one circuit
    breaker; WorkBuddy refresh tokens are single-use, so a sweeper tick and a
    client 401 landing together must not both POST the same token. Mirrors
    the Cline hook's contract (see
    ``cline_auto_refresh.refresh_for_provider``).
    """
    async def hook(provider_name: str, key_label: str) -> bool:
        # A 401 means the access token was rejected upstream, so the stored
        # expiry is irrelevant: rotate unconditionally, under the lock.
        return await _worker_for(state).refresh_now(provider_name, key_label,
                                                    require_due=False)

    return hook


async def refresh_key_now(state: AppState, provider_name: str,
                          key_label: str) -> dict[str, Any]:
    """Rotate one pool key's token on demand (admin API surface).

    Unlike :func:`refresh_for_provider` (which the gateway calls on a 401 and
    only needs a boolean), this returns the failure reason so the admin UI
    can show it. Shares the per-key lock with the background sweeper and the
    401 hook, and deliberately overrides the circuit breaker — an operator
    asking for a rotation is the documented way out of a tripped provider.
    """
    fresh, error = await _worker_for(state)._rotate_locked(
        provider_name, key_label, require_due=False, force=True)
    if not fresh:
        return {"ok": False, "error": error}
    return {"ok": True, "error": ""}


_workers: dict[int, WorkBuddyAutoRefresh] = {}


def _worker_for(state: AppState) -> WorkBuddyAutoRefresh:
    """Resolve the shared worker for *state*.

    The lifespan-owned ``state.workbuddy_refresh`` wins when set, so the
    background sweeper, the gateway 401 hook, and admin refresh calls all
    share one per-key lock and one circuit breaker. Fallback (tests,
    pre-lifespan use): a lazily cached instance.
    """
    shared = getattr(state, "workbuddy_refresh", None)
    if shared is not None:
        return shared
    return _workers.setdefault(id(state), WorkBuddyAutoRefresh(state))
