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
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

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
        self._circuit: dict[str, dict[str, Any]] = {}

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
        # Circuit breaker: skip if in backoff window.
        cb = self._circuit.get(name)
        if cb and time.time() < cb.get("until", 0):
            return

        expires_epoch = cline_oauth.parse_expires_at(record.get("expires_at"))
        if expires_epoch is None or not cline_oauth.expires_within_lead(expires_epoch):
            return  # not due for refresh

        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            # Re-check expiry under the lock (another caller may have refreshed).
            record = await self._state.config_store.get_setting(f"cline_oauth:{name}")
            if not record or not record.get("refresh_token"):
                return
            expires_epoch = cline_oauth.parse_expires_at(record.get("expires_at"))
            if expires_epoch is not None and not cline_oauth.expires_within_lead(expires_epoch):
                return
            await self._do_refresh(name, record)

    async def _do_refresh(self, name: str, record: dict[str, Any]) -> None:
        """Call the refresh endpoint and persist the result."""
        result = await cline_oauth.refresh_token(record["refresh_token"])
        if result is None:
            self._trip_circuit(name)
            log.warning("cline_auto_refresh_transient", provider=name)
            return
        if result.get("error") == "unrecoverable_refresh_error":
            # Stop refreshing — the user must re-login.
            self._circuit[name] = {"streak": 99, "until": float("inf")}
            log.error("cline_auto_refresh_unrecoverable", provider=name,
                      code=result.get("code"))
            return
        # Success — write new tokens.
        await self._update_secret(name, result["access_token"])
        record["refresh_token"] = result["refresh_token"]
        if result.get("expires_at"):
            record["expires_at"] = result["expires_at"]
        await self._state.config_store.set_setting(f"cline_oauth:{name}", record)
        self._circuit.pop(name, None)
        log.info("cline_auto_refreshed", provider=name)

    async def _update_secret(self, provider: str, secret: str) -> None:
        """Update the first pool key's secret in memory + DB."""
        acct = self._state.router.providers.get(provider)
        if acct is None or not acct.keys:
            return
        key0 = acct.keys[0]
        key0.secret = secret
        key0.status = "active"
        key0.cooldown_until = 0.0
        await self._state.config_store.update_key_secret(provider, key0.label, secret)

    def _trip_circuit(self, name: str) -> None:
        cb = self._circuit.get(name, {"streak": 0, "until": 0})
        cb["streak"] = cb.get("streak", 0) + 1
        backoff = min(CIRCUIT_BASE_S * 2 ** (cb["streak"] - 1), CIRCUIT_CAP_S)
        cb["until"] = time.time() + backoff
        self._circuit[name] = cb
