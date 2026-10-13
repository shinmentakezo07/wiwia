"""Background + on-demand auto-refresh for Hermes (Nous Portal) OAuth accounts.

Each Nous Portal account is a separate OAuth identity with its own rotating
(single-use) refresh token and its own rate-limit bucket (~50 RPM / 50k TPM).
wiwi stores **one account per provider pool key** as a self-contained
``hermes:<json>`` credential blob (see :func:`wiwi.providers.hermes_oauth.encode_credential`),
so the existing key pool + smooth weighted round-robin spreads traffic across
accounts and beats any single account's cap (AUDIT #427). This module rotates
each account's tokens on its own.

Two refreshers cover the two failure windows:

* a background sweeper that rotates an account whose access token is expired,
  inside the lead window, or of unknown expiry (``hermes_oauth.REFRESH_LEAD_S``);
* an on-demand hook the gateway calls on a 401, rotating unconditionally
  because the upstream just rejected a token whose nominal expiry was
  irrelevant.

Both funnel through one :class:`HermesAutoRefresh` worker (via
:func:`_worker_for`) so they share one **per-account** lock and one circuit
breaker. Nous returns a fresh ``refresh_token`` per exchange, so a sweeper tick
and a client 401 landing in the same window must not both present the same
account's token: the loser's ``invalid_grant`` would mark that account dead
until a human re-authenticates (AUDIT #169). Locks and circuits are keyed on
``(provider, key_label)`` so one account's refresh never serializes or poisons
another's.

An ``invalid_grant`` / revoked / reused refresh token is terminal for that
account (``relogin_required``); only that account's circuit is marked dead, so
the rest of the pool keeps serving while the operator reconnects the one dead
credential.
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
    """Rotates each Hermes pool key's OAuth token, proactively and on 401."""

    def __init__(self, state: AppState) -> None:
        self._state = state
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        # Per-account lock: one per (provider, key_label). A lock and circuit
        # per account means refreshing one Nous account never blocks or
        # trips another (AUDIT #427).
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # Successful-rotation counter per account. The sweeper and the 401 hook
        # share one worker, so this is how the loser of a lock race learns the
        # winner already rotated (AUDIT #169).
        self._generations: dict[tuple[str, str], int] = {}
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
        """Refresh every Hermes pool key whose OAuth token is due.

        Iterates the live key pools (one account per key), not a single
        settings record, so every connected account is covered. Returns the
        number of managed accounts checked.
        """
        checked = 0
        for name, acct in list(self._state.router.providers.items()):
            if acct.provider_type != "hermes":
                continue
            for key in list(acct.keys):
                cred = hermes_oauth.decode_credential(key.secret)
                if not cred or not cred.get("refresh_token"):
                    continue  # bare token or raw key: nothing to rotate
                checked += 1
                try:
                    await self._check_account(name, key.label, cred)
                except Exception:
                    log.exception("hermes_auto_refresh_account_error",
                                  provider=name, key=key.label)
        return checked

    async def _check_account(self, name: str, label: str,
                             cred: dict[str, Any]) -> None:
        """Refresh one account's token if it is expired or inside the lead."""
        if not hermes_oauth.expires_within_lead(cred.get("expires_at"), time.time()):
            return  # not due for refresh
        await self._refresh_locked(name, label, require_due=True)

    async def refresh_now(self, name: str, label: str) -> bool:
        """On-demand refresh for the gateway's 401 hook, for one account.

        The upstream rejected the access token, so the stored expiry is
        irrelevant — rotate unconditionally, under the same lock and circuit
        the sweeper uses. Returns True when the account's token is fresh
        afterwards (rotated here, or rotated by the sweeper while this call
        waited for the lock), so the caller may retry with it.
        """
        return await self._refresh_locked(name, label, require_due=False)

    async def _refresh_locked(self, name: str, label: str, *,
                              require_due: bool, force: bool = False) -> bool:
        """Serialize a refresh for one account through its lock + circuit.

        The sweep (``require_due=True``) and the on-demand 401 hook
        (``require_due=False``) funnel through here so both share one lock and
        one circuit per account. Nous returns a fresh ``refresh_token`` per
        exchange, so the credential is re-read *under* the lock from the live
        pool key: a rotation that landed while we waited is honored instead of
        being repeated with the token it just consumed (AUDIT #169).

        ``force`` (the admin refresh path) bypasses the circuit breaker — an
        operator asking for a rotation is the documented way out of a tripped
        account (mirrors ``workbuddy_auto_refresh.refresh_key_now``).

        Returns True when the account's token is fresh afterwards (we rotated
        it, or another caller did while we waited), False when nothing was
        rotated — circuit-blocked, no stored refresh grant, not due, or the
        refresh failed.
        """
        ident = (name, label)
        lock = self._locks.setdefault(ident, asyncio.Lock())
        generation = self._generations.get(ident, 0)
        async with lock:
            # Circuit state is read under the lock so a failure that landed
            # while we waited (a sibling refresh tripping it) is honored.
            if not force and self._circuit.blocked(ident):
                return False
            if self._generations.get(ident, 0) != generation:
                # Someone rotated while we waited: the account is already
                # fresh, and the refresh_token we would present has been
                # consumed by that rotation.
                return True
            cred = self._live_credential(name, label)
            if cred is None or not cred.get("refresh_token"):
                return False
            if require_due and not hermes_oauth.expires_within_lead(
                    cred.get("expires_at"), time.time()):
                return False
            return await self._do_refresh(name, label, cred)

    def _live_credential(self, provider: str, label: str) -> dict[str, Any] | None:
        """The current credential blob for one pool key, read from the router.

        Read from the live key rather than a cached record so a rotation by
        another caller (or a manual admin refresh) is seen immediately.
        """
        acct = self._state.router.providers.get(provider)
        if acct is None:
            return None
        for k in acct.keys:
            if k.label == label:
                return hermes_oauth.decode_credential(k.secret)
        return None

    async def _do_refresh(self, name: str, label: str,
                          cred: dict[str, Any]) -> bool:
        """Exchange one account's refresh token and persist the result.

        Callers must hold ``self._locks[(name, label)]``. Returns True when the
        account's tokens were rotated and stored.
        """
        # Nous refresh is synchronous httpx (the module is shared with the
        # CLI-facing connect flow), so run it on a worker thread: the sweeper
        # and the 401 hook both run on the event loop, and an inline network
        # POST would stall every concurrent request for its duration (the
        # AUDIT #33 shape the tiktoken estimator already hit).
        ident = (name, label)
        try:
            result = await asyncio.to_thread(
                hermes_oauth.refresh_access_token, cred["refresh_token"])
        except hermes_oauth.HermesAuthError as e:
            if e.relogin_required:
                # This account's refresh grant is dead (revoked / reused /
                # invalid_grant): stop retrying THIS account until a human
                # re-authenticates it. The circuit is per-account, so sibling
                # accounts keep serving (AUDIT #427).
                self._circuit.mark_dead(ident)
                log.error("hermes_auto_refresh_unrecoverable", provider=name,
                          key=label, code=e.code)
            else:
                self._trip_circuit(ident)
                log.warning("hermes_auto_refresh_transient", provider=name,
                            key=label, code=e.code)
            return False
        access = result.get("access_token")
        if not access:
            self._trip_circuit(ident)
            log.warning("hermes_auto_refresh_no_access_token", provider=name,
                        key=label)
            return False
        # Build the new credential, preserving the account's label.
        new_refresh = result.get("refresh_token") or cred.get("refresh_token")
        expires_at = hermes_oauth.compute_expires_at(result.get("expires_in"))
        secret = hermes_oauth.encode_credential(
            access, new_refresh, expires_at, cred.get("label"))
        # Persist the new secret to the pool key BEFORE anything that can
        # raise for a YAML-defined provider, so a crash leaves the fresh token
        # on the key rather than the reverse (see the #425/#319 ordering note).
        await self._update_secret(name, label, secret)
        self._generations[ident] = self._generations.get(ident, 0) + 1
        self._circuit.clear(ident)
        log.info("hermes_auto_refreshed", provider=name, key=label)
        return True

    async def _update_secret(self, provider: str, label: str, secret: str) -> None:
        """Rotate ONE pool key's secret in memory + DB.

        Only the named account's key is touched — a rotation must never
        clobber a sibling account's credential (the single-account flow used to
        write every key; AUDIT #427).
        """
        acct = self._state.router.providers.get(provider)
        if acct is None:
            return
        for k in acct.keys:
            if k.label != label:
                continue
            k.secret = secret
            k.status = "active"
            k.cooldown_until = 0.0
            try:
                await self._state.config_store.update_key_secret(
                    provider, label, secret)
            except Exception as e:  # noqa: BLE001 — YAML-defined: no DB row
                # YAML-defined provider: no DB row to update. The in-memory
                # assignment above is still correct for this process; refusing
                # here would break OAuth refresh on YAML-only deployments.
                structlog.get_logger().warning(
                    "oauth_secret_not_persisted", provider=provider, key=label,
                    reason="provider is YAML-defined; rotation is in-memory only",
                    error=type(e).__name__)
            return

    def _trip_circuit(self, ident: tuple[str, str]) -> None:
        self._circuit.trip(ident)


def refresh_for_provider(state) -> callable:
    """Build the async hook the gateway calls when a Hermes request 401s.

    Returns ``hook(provider_name, key_label) -> bool``: True when that
    account's access token was rotated and the caller should retry, False when
    nothing was rotated (caller surfaces the original 401).

    The hook delegates to :func:`_worker_for` — the *same* worker the
    background sweeper uses — so both paths share one per-account lock and one
    circuit breaker. Nous returns a fresh refresh token per exchange, so a
    sweeper tick and a client 401 landing in the same window must not both
    POST the same account's token: the loser's ``invalid_grant`` would mark
    that account dead until a human re-authenticates (AUDIT #169).
    """
    async def hook(provider_name: str, key_label: str) -> bool:
        return await _worker_for(state).refresh_now(provider_name, key_label)

    return hook


_workers: dict[int, HermesAutoRefresh] = {}


def _worker_for(state: AppState) -> HermesAutoRefresh:
    """Resolve the shared worker for *state*.

    The lifespan-owned ``state.hermes_refresh`` wins when set, so the
    background sweeper, the gateway 401 hook, and any other caller share one
    lock and one circuit breaker per account. Fallback (tests, pre-lifespan
    use): a lazily cached instance.
    """
    shared = getattr(state, "hermes_refresh", None)
    if shared is not None:
        return shared
    return _workers.setdefault(id(state), HermesAutoRefresh(state))
