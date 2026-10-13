"""Background auto-refresh for Hermes (Nous Portal) OAuth tokens.

Mirrors :mod:`wiwi.providers.cline_auto_refresh` and
:mod:`wiwi.providers.workbuddy_auto_refresh`, which both refresh proactively
(before expiry) *and* on demand (a gateway 401 mid-session). Hermes shipped with
neither: :mod:`wiwi.providers.hermes_oauth` exposes a working
``refresh_access_token`` and the admin API calls it from
``/admin/hermes/oauth/refresh``, but nothing rotates a token automatically. An
access token that expires (or is invalidated upstream) mid-session produced a
hard 401 to the caller, and under the default ``failover_mode == "any_error"``
that 401 charged the key's error streak — so a routine expiry could retire an
otherwise healthy key (AUDIT #423).

Two refreshers exist so the two failure windows are covered:

* a background sweeper that rotates tokens inside the lead window before
  expiry (``hermes_oauth.REFRESH_LEAD_S``), and
* an on-demand hook the gateway calls on a 401, rotating unconditionally
  because the upstream just rejected a token whose nominal expiry was
  irrelevant.

Both funnel through one :class:`HermesAutoRefresh` worker (via
:func:`_worker_for`) so they share one per-provider lock and one circuit
breaker — the same discipline Cline's rotating-token refresher needs (AUDIT
#169). Nous returns a fresh ``refresh_token`` on each exchange, so a sweeper
tick and a client 401 landing in the same window must not both present the
same token: the loser's ``invalid_grant`` would mark the provider dead until a
human re-authenticates.

An ``invalid_grant`` / revoked / reused refresh token is terminal
(``relogin_required``); the circuit is marked dead so the sweeper stops
retrying and the operator is told to reconnect.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

from wiwi.core.recovery import CircuitBreaker
from wiwi.providers import hermes_oauth

if TYPE_CHECKING:
    from wiwi.server.app import AppState

log = structlog.get_logger("wiwi.hermes_auto_refresh")

TICK_S = 60.0
FIRST_SWEEP_DELAY_S = 10.0
CIRCUIT_BASE_S = 5 * 60
CIRCUIT_CAP_S = 4 * 60 * 60


class HermesAutoRefresh:
    """Background task that proactively refreshes Hermes tokens before expiry."""

    def __init__(self, state: AppState) -> None:
        self._state = state
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        # Successful-rotation counter per provider. The sweeper and the
        # on-demand 401 hook share one worker, so this is how the loser of a
        # lock race learns the winner already rotated (AUDIT #169).
        self._generations: dict[str, int] = {}
        self._circuit = CircuitBreaker(base_s=CIRCUIT_BASE_S, cap_s=CIRCUIT_CAP_S,
                                       clock=time.time)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="hermes-auto-refresh")

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
                    log.exception("hermes_auto_refresh_sweep_error")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _sweep(self) -> int:
        """Iterate over all hermes providers with stored OAuth state.

        Returns the number of providers checked.
        """
        cs = self._state.config_store
        if cs is None:
            return 0
        checked = 0
        for name, acct in list(self._state.router.providers.items()):
            if acct.provider_type != "hermes":
                continue
            record = await cs.get_setting(f"hermes_oauth:{name}")
            if not record or not record.get("refresh_token"):
                continue
            checked += 1
            try:
                await self._check_provider(name, record)
            except Exception:
                log.exception("hermes_auto_refresh_provider_error", provider=name)
        return checked

    async def _check_provider(self, name: str, record: dict[str, Any]) -> None:
        """Refresh one provider's token if it is expired or inside the lead."""
        if not hermes_oauth.expires_within_lead(record.get("expires_at"),
                                                time.time()):
            return  # not due for refresh
        await self._refresh_locked(name, require_due=True)

    async def refresh_now(self, name: str) -> bool:
        """On-demand refresh for the gateway's 401 hook.

        The upstream rejected the access token, so the stored expiry is
        irrelevant — rotate unconditionally, under the same lock and circuit
        breaker the sweeper uses. Returns True when the stored record is fresh
        afterwards (rotated here, or rotated by the sweeper while this call
        waited for the lock), so the caller may retry with it.
        """
        return await self._refresh_locked(name, require_due=False)

    async def _refresh_locked(self, name: str, *, require_due: bool) -> bool:
        """Serialize a refresh for ``name`` through the shared lock + circuit.

        The sweep (``require_due=True``) and the on-demand 401 hook
        (``require_due=False``) funnel through here so both share one lock and
        one circuit breaker. Nous returns a fresh ``refresh_token`` per
        exchange, so the record is re-read *under* the lock: a rotation that
        landed while we waited is honored instead of being repeated with the
        token it just consumed (AUDIT #169).

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
            record = await cs.get_setting(f"hermes_oauth:{name}")
            if not record or not record.get("refresh_token"):
                return False
            if require_due and not hermes_oauth.expires_within_lead(
                    record.get("expires_at"), time.time()):
                return False
            return await self._do_refresh(name, record)

    async def _do_refresh(self, name: str, record: dict[str, Any]) -> bool:
        """Call the Nous refresh endpoint and persist the result.

        Callers must hold ``self._locks[name]``. Returns True when the tokens
        were rotated and stored.
        """
        # Nous refresh is synchronous httpx (the module is shared with the
        # CLI-facing connect flow), so run it on a worker thread: the sweeper
        # and the 401 hook both run on the event loop, and an inline network
        # POST would stall every concurrent request for its duration (the
        # AUDIT #33 shape the tiktoken estimator already hit).
        try:
            result = await asyncio.to_thread(
                hermes_oauth.refresh_access_token, record["refresh_token"])
        except hermes_oauth.HermesAuthError as e:
            if e.relogin_required:
                # The refresh grant is dead (revoked / reused / invalid_grant):
                # stop retrying until a human re-authenticates.
                self._circuit.mark_dead(name)
                log.error("hermes_auto_refresh_unrecoverable", provider=name,
                          code=e.code)
            else:
                self._trip_circuit(name)
                log.warning("hermes_auto_refresh_transient", provider=name,
                            code=e.code)
            return False
        access = result.get("access_token")
        if not access:
            self._trip_circuit(name)
            log.warning("hermes_auto_refresh_no_access_token", provider=name)
            return False
        # Persist the OAuth record FIRST (new refresh token + a forward
        # expires_at), then rotate the pool key. Order matters for two reasons:
        #   1. The record write is what stops the #425 spin — if the pool-key
        #      write below raises for a YAML-defined provider (no DB key row →
        #      ConfigStoreNotFound), an out-of-order version never reached the
        #      set_setting at all, so expires_at stayed in the past and the
        #      sweeper re-refreshed the single-use grant every tick.
        #   2. A crash between the two leaves a fresh refresh token on disk with
        #      the old access token still live — recoverable on the next tick —
        #      rather than the reverse, where a rotated key has no persisted
        #      record to point the next refresh at.
        if result.get("refresh_token"):
            record["refresh_token"] = result["refresh_token"]
        record["expires_at"] = hermes_oauth.compute_expires_at(
            result.get("expires_in"))
        try:
            await self._state.config_store.set_setting(
                f"hermes_oauth:{name}", record)
        except Exception as e:  # noqa: BLE001 — a settings outage must not strand the key
            # The rotation happened upstream; failing to persist the record is
            # loud (it will re-refresh next tick) but must not abort the key
            # update below — the live access token is still the one in use.
            log.error("hermes_auto_refresh_record_persist_failed", provider=name,
                      error=type(e).__name__, detail=str(e))
        await self._update_secret(name, f"hermes:{access}")
        self._generations[name] = self._generations.get(name, 0) + 1
        self._circuit.clear(name)
        log.info("hermes_auto_refreshed", provider=name)
        return True

    async def _update_secret(self, provider: str, secret: str) -> None:
        """Rotate the provider's pool-key secret in memory + DB.

        The access token lands on the key pool prefixed ``hermes:`` (the
        adapter strips the prefix for the bearer), matching the connect flow.
        """
        acct = self._state.router.providers.get(provider)
        if acct is None or not acct.keys:
            return
        for k in acct.keys:
            k.secret = secret
            k.status = "active"
            k.cooldown_until = 0.0
            try:
                await self._state.config_store.update_key_secret(
                    provider, k.label, secret)
            except Exception as e:  # noqa: BLE001 — YAML-defined: no DB row
                # YAML-defined provider: no DB row to update. The in-memory
                # assignment above is still correct for this process; refusing
                # here would break OAuth refresh on YAML-only deployments.
                structlog.get_logger().warning(
                    "oauth_secret_not_persisted", provider=provider,
                    reason="provider is YAML-defined; rotation is in-memory only",
                    error=type(e).__name__)

    def _trip_circuit(self, name: str) -> None:
        self._circuit.trip(name)


def refresh_for_provider(state) -> callable:
    """Build the async hook the gateway calls when a Hermes request 401s.

    Returns ``hook(provider_name, key_label) -> bool``: True when the stored
    access token was rotated and the caller should retry, False when nothing
    was rotated (caller surfaces the original 401).

    The hook delegates to :func:`_worker_for` — the *same* worker the
    background sweeper uses — so both paths share one per-provider lock and
    one circuit breaker. Nous returns a fresh refresh token per exchange, so a
    sweeper tick and a client 401 landing in the same window must not both
    POST the same token: the loser's ``invalid_grant`` would mark the provider
    dead until a human re-authenticates (AUDIT #169).

    The hook is injected into ``Gateway._on_demand_refresh_hooks["hermes"]``
    at app startup so the gateway can call it without depending on the full
    AppState — see ``wiwi.server.app`` for the wiring.
    """
    async def hook(provider_name: str, key_label: str) -> bool:
        return await _worker_for(state).refresh_now(provider_name)

    return hook


_workers: dict[int, HermesAutoRefresh] = {}


def _worker_for(state: AppState) -> HermesAutoRefresh:
    """Resolve the shared worker for *state*.

    The lifespan-owned ``state.hermes_refresh`` wins when set, so the
    background sweeper, the gateway 401 hook, and any other caller share one
    lock and one circuit breaker. Fallback (tests, pre-lifespan use): a
    lazily cached instance.
    """
    shared = getattr(state, "hermes_refresh", None)
    if shared is not None:
        return shared
    return _workers.setdefault(id(state), HermesAutoRefresh(state))
