"""Background + on-demand token refresh for WorkBuddy accounts.

Mirrors :mod:`wiwi.providers.cline_auto_refresh` with one structural
difference: a Cline provider holds ONE OAuth account (all pool keys share
it, record lives in ``cline_oauth:<provider>``), while a WorkBuddy provider
holds one account PER POOL KEY — the key's secret IS the auth JSON (nested
shape, see :mod:`wiwi.providers.workbuddy_auth`). So the sweep refreshes
per key, not per provider, and a rotation writes the updated auth JSON back
into that key's secret (in-memory + DB via ``config_store``).

WorkBuddy refresh tokens rotate on use, so the sweeper only refreshes keys
whose access token is inside ``REFRESH_LEAD_S`` (5 min) of expiry. A
per-key lock prevents concurrent refreshes; a circuit breaker backs off
exponentially on repeated failures; unrecoverable failures (session dead,
missing refreshToken) stop retrying until the user replaces the secret.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

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
        self._circuit: dict[tuple[str, str], dict[str, Any]] = {}

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
        ident = (provider, label)
        cb = self._circuit.get(ident)
        if cb and time.time() < cb.get("until", 0):
            return
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
            return  # not due (or nothing to rotate)

        lock = self._locks.setdefault(ident, asyncio.Lock())
        async with lock:
            # Re-check under the lock: another caller may have rotated.
            acct = self._state.router.providers.get(provider)
            key = acct.get_key(label) if acct else None
            if key is None:
                return
            try:
                auth = parse_auth(key.secret)
            except WorkBuddyAuthError:
                return
            if auth.refresh_token == "" or not expires_within_lead(
                    auth.expires_at, REFRESH_LEAD_S):
                return
            await self._do_refresh(provider, label, auth)

    async def _do_refresh(self, provider: str, label: str,
                          auth: WorkBuddyAuth) -> None:
        ident = (provider, label)
        outcome = await refresh_token(auth)
        if not outcome.ok:
            if outcome.unrecoverable:
                self._circuit[ident] = {"streak": 99, "until": float("inf")}
                log.error("workbuddy_auto_refresh_unrecoverable",
                          provider=provider, label=label, err=outcome.error)
            else:
                self._trip_circuit(ident)
                log.warning("workbuddy_auto_refresh_transient",
                            provider=provider, label=label, err=outcome.error)
            return
        await self._write_secret(provider, label, outcome.auth)
        self._circuit.pop(ident, None)
        log.info("workbuddy_auto_refreshed", provider=provider, label=label)

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
        cb = self._circuit.get(ident, {"streak": 0, "until": 0})
        cb["streak"] = cb.get("streak", 0) + 1
        backoff = min(CIRCUIT_BASE_S * 2 ** (cb["streak"] - 1), CIRCUIT_CAP_S)
        cb["until"] = time.time() + backoff
        self._circuit[ident] = cb


def refresh_for_provider(state: AppState) -> Any:
    """Build the on-demand refresh hook the gateway calls on a 401.

    Returns an async ``hook(provider_name, key_label) -> bool``: True when
    that key's access token was rotated (caller should retry with the fresh
    secret), False otherwise. Circuit-breaker aware, mirroring the Cline
    hook's contract (see ``cline_auto_refresh.refresh_for_provider``).
    """
    worker = WorkBuddyAutoRefresh(state)

    async def hook(provider_name: str, key_label: str) -> bool:
        ident = (provider_name, key_label)
        cb = worker._circuit.get(ident)
        if cb and time.time() < cb.get("until", 0):
            return False
        acct = state.router.providers.get(provider_name)
        key = acct.get_key(key_label) if acct else None
        if key is None or state.config_store is None:
            return False
        try:
            auth = parse_auth(key.secret)
        except WorkBuddyAuthError:
            return False
        if not auth.refresh_token:
            return False
        outcome = await refresh_token(auth)
        if not outcome.ok:
            if outcome.unrecoverable:
                worker._circuit[ident] = {"streak": 99, "until": float("inf")}
            else:
                worker._trip_circuit(ident)
            return False
        await worker._write_secret(provider_name, key_label, outcome.auth)
        worker._circuit.pop(ident, None)
        return True

    return hook


async def refresh_key_now(state: AppState, provider_name: str,
                          key_label: str) -> dict[str, Any]:
    """Rotate one pool key's token on demand (admin API surface).

    Unlike :func:`refresh_for_provider` (which the gateway calls on a 401 and
    only needs a boolean), this returns the failure reason so the admin UI
    can show it. Shares the circuit breaker with the background sweeper.
    """
    ident = (provider_name, key_label)
    worker = _worker_for(state)
    acct = state.router.providers.get(provider_name)
    key = acct.get_key(key_label) if acct else None
    if key is None:
        return {"ok": False, "error": f"unknown key '{key_label}'"}
    try:
        auth = parse_auth(key.secret)
    except WorkBuddyAuthError as e:
        return {"ok": False, "error": f"secret is not WorkBuddy auth JSON: {e}"}
    if not auth.refresh_token:
        return {"ok": False, "error": "secret has no refreshToken — re-import required"}
    outcome = await refresh_token(auth)
    if not outcome.ok:
        if outcome.unrecoverable:
            worker._circuit[ident] = {"streak": 99, "until": float("inf")}
        else:
            worker._trip_circuit(ident)
        return {"ok": False, "error": outcome.error}
    await worker._write_secret(provider_name, key_label, outcome.auth)
    worker._circuit.pop(ident, None)
    return {"ok": True, "error": ""}


_workers: dict[int, WorkBuddyAutoRefresh] = {}


def _worker_for(state: AppState) -> WorkBuddyAutoRefresh:
    """Resolve the shared worker for *state*.

    The lifespan-owned ``state.workbuddy_refresh`` wins when set, so the
    background sweeper, the gateway 401 hook, and admin refresh calls all
    share one circuit breaker. Fallback (tests, pre-lifespan use): a lazily
    cached instance.
    """
    shared = getattr(state, "workbuddy_refresh", None)
    if shared is not None:
        return shared
    return _workers.setdefault(id(state), WorkBuddyAutoRefresh(state))
