"""Router: model groups of deployments, provider key pools with smooth weighted
round-robin, cooldowns, retries, fallbacks (docs/ADMIN.md §2, ARCHITECTURE.md §4.3)."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog

from wiwi.config import PROVIDER_TYPES, ModelAliasEntry, RouterSettings, WiwiConfig
from wiwi.core.context import RequestContext
from wiwi.core.recovery import Backoff
from wiwi.providers.base import (
    ProviderKeyRef,
    WiwiError,
    status_for_key_pool,
)
from wiwi.server.stats import percentile

log = structlog.get_logger("wiwi.router")


@dataclass
class ProviderKey:
    label: str
    secret: str
    weight: int = 1
    enabled: bool = True
    status: str = "active"          # active | cooling | invalid | disabled | probation
    cooldown_until: float = 0.0
    last_used: float = 0.0
    current_weight: float = 0.0     # smooth WRR state
    req_count: int = 0
    err_count: int = 0

    @property
    def available(self) -> bool:
        if not self.enabled or self.status not in (
                "active", "cooling", "invalid", "probation"):
            return False
        if self.status in ("cooling", "invalid"):
            # A timed cooldown (bounded retirement, AUDIT #69) revives itself
            # once the window elapses. A terminal ``invalid`` —
            # ``mark_invalid(None)`` — has ``cooldown_until == 0.0`` and stays
            # out of rotation until the healer or an admin resets it. The
            # pre-#115 shape excluded ``invalid`` unconditionally, so an
            # expired bounded retirement was never available and
            # ``pick_key``/``recover()`` was unreachable: the revival the #69
            # fix documented could not fire on the live path.
            return self.cooldown_until > 0.0 and time.monotonic() >= self.cooldown_until
        return True

    def mark_cooling(self, seconds: float) -> None:
        self.status = "cooling"
        self.cooldown_until = time.monotonic() + seconds

    def mark_invalid(self, seconds: float | None = None) -> None:
        """Retire a key after a failure streak.

        ``invalid`` used to be terminal: ``recover()`` only revived ``cooling``
        keys, so a transient provider-side 5xx storm permanently removed a key
        from rotation with no recovery path (AUDIT #69). The retirement is now
        a timed cooldown — when *seconds* is given the key revives itself once
        the window elapses, exactly like a cooling key. Passing ``None`` keeps
        the historical terminal behaviour for genuinely dead credentials (the
        healer / admin reset paths still handle those).
        """
        self.status = "invalid"
        if seconds is not None and seconds > 0:
            self.cooldown_until = time.monotonic() + seconds
        else:
            self.cooldown_until = 0.0

    def recover(self, force: bool = False) -> None:
        """Revive a key whose timed cooldown window has elapsed.

        A terminal ``invalid`` — ``mark_invalid(None)``, ``cooldown_until ==
        0.0`` — is deliberately NOT revived here: genuinely dead credentials
        return to service only through the healer or an admin reset, per
        :meth:`mark_invalid`'s contract (AUDIT #115: pick_key's sweep used to
        resurrect them unconditionally).

        ``force`` is that admin reset — the operator's explicit "this key is
        fine now". It skips the cooldown gate entirely (reviving a terminal
        ``invalid`` too) and always clears the failure streak and WRR deficit.
        Without the streak reset the reset is a no-op in practice: the admin
        path used to set ``status``/``cooldown_until`` by hand, leaving
        ``err_count`` at its retirement value, so the very next non-200
        re-retired the key through ``on_result``'s
        ``err_count >= key_max_consecutive_fails`` (AUDIT #199).
        """
        if force:
            self.status = "active"
            self.cooldown_until = 0.0
            self.err_count = 0
            self.current_weight = 0.0
            return
        expired = time.monotonic() >= self.cooldown_until
        timed = (self.status == "cooling"
                 or (self.status == "invalid" and self.cooldown_until > 0.0))
        if not (timed and expired):
            return
        self.status = "active"
        # Reset the streak so a recovered key isn't immediately retired
        # again by the failure count that put it here.
        if self.err_count:
            self.err_count = 0
        # Reset WRR weight so the recovered key isn't starved by the
        # deficit it accumulated while cooling.
        self.current_weight = 0.0

    def mark_recovered(self) -> None:
        """Healer restore: enter probation with a fresh slate (cooldown cleared,
        fail streak and WRR deficit reset). Graduates to active on the first
        credited success; a failure demotes via the normal cooldown path."""
        self.status = "probation"
        self.cooldown_until = 0.0
        self.err_count = 0
        self.current_weight = 0.0

@dataclass
class ProviderAccount:
    name: str
    provider_type: str
    base_url: str
    timeout_s: float = 120.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    round_robin: bool = True
    keys: list[ProviderKey] = field(default_factory=list)
    # Optional caller-facing alias id.  Mirrors ProviderDef.alias_id from
    # wiwi.yaml; admin API mutations keep it in sync.  Used by the router's
    # alias-to-provider registry so request bodies may name the alias.
    alias_id: str | None = None
    _rr_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _seq_idx: int = 0  # sequential key cursor (when round_robin=False)

    @property
    def healthy(self) -> bool:
        return any(k.available for k in self.keys)

    def get_key(self, label: str) -> ProviderKey | None:
        """Resolve the live pool entry for a key reference (by label)."""
        for k in self.keys:
            if k.label == label:
                return k
        return None

    async def pick_key(self, exclude_labels: set[str] | None = None,
                       probation_weight: float = 1.0) -> tuple[ProviderKey | None, float]:
        """Pick the next key to use.

        When ``round_robin`` is True (default): smooth weighted round-robin
        over available keys (nginx algorithm).
        When ``round_robin`` is False: sequential selection — the first
        available key in list order, advancing the cursor only when the
        current key is unavailable (cooldown/disabled).

        ``exclude_labels`` lets cycle-3 / any-error failover skip a specific
        key (e.g. the one that just served N consecutive requests) without
        having to temporarily mark it unavailable.
        ``probation_weight`` scales the effective WRR weight of keys in
        ``probation`` status so a just-healed key earns back traffic gradually.
        """
        async with self._rr_lock:
            for k in self.keys:
                k.recover()
            exclude_labels = exclude_labels or set()
            avail = [k for k in self.keys if k.available and k.label not in exclude_labels]
            if not avail:
                # fall back to the full available list if every key is excluded
                avail = [k for k in self.keys if k.available]
                if not avail:
                    soonest = min((k.cooldown_until for k in self.keys
                                   if k.status in ("cooling", "invalid")
                                   and k.cooldown_until > time.monotonic()),
                                  default=None)
                    return None, (soonest - time.monotonic() if soonest else 5.0)

            if not self.round_robin:
                # Sequential: find the first available key at/after the cursor.
                # This keeps using the same key until it becomes unavailable,
                # then advances to the next one in list order.
                n = len(self.keys)
                for offset in range(n):
                    idx = (self._seq_idx + offset) % n
                    k = self.keys[idx]
                    if k.available and k.label not in exclude_labels:
                        self._seq_idx = idx
                        k.last_used = time.monotonic()
                        return k, 0.0
                # Fallback (should not reach here since avail is non-empty)
                k = avail[0]
                k.last_used = time.monotonic()
                return k, 0.0

            # Smooth WRR (nginx algorithm) over the (possibly excluded) avail
            # list.  Probation keys carry a reduced effective weight so a
            # just-healed key earns back traffic gradually.
            weights = [k.weight * (probation_weight if k.status == "probation" else 1.0)
                       for k in avail]
            total = sum(weights)
            for k, w in zip(avail, weights):
                k.current_weight += w
            best = max(avail, key=lambda k: k.current_weight)
            best.current_weight -= total
            best.last_used = time.monotonic()
            return best, 0.0

    def on_result(self, key: ProviderKey | None, status: int | None,
                  retry_after: float | None,
                  failover_mode: str = "any_error",
                  key_max_consecutive_fails: int = 5) -> None:
        """Update a key's health counters after a request completes.

        Synchronous callers (e.g. retry paths inside the same task) use this
        directly.  Async paths that can race with :meth:`pick_key` should
        call :meth:`on_result_locked` so the mutation serializes under the
        same lock.

        ``failover_mode`` is one of:

        - "standard": historical behaviour: 429 -> cooldown, 5xx -> let
          ``Deployment.record_fail`` handle it, 401/403 -> mark_invalid.
        - "any_error": every non-200 applies a short cooling window so the
          next pick rotates to a different key.  Auth errors (401/403)
          count as 2 consecutive failures; only when err_count reaches
          ``key_max_consecutive_fails`` is the key permanently retired.
        """
        if key is None or status is None:
            return
        if status == 200:
            key.req_count += 1
            # any consecutive-fail streak is broken on success
            key.err_count = 0
            if key.status == "probation":
                key.status = "active"
                log.info("healer_probation_graduated", kind="key",
                         provider=self.name, key=key.label)
            return
        if failover_mode == "any_error":
            key.err_count += 2 if status in (401, 403) else 1
            if key.err_count >= key_max_consecutive_fails:
                # Retire for a bounded window rather than forever: a transient
                # 5xx storm must not permanently remove a provider's only key
                # from rotation (AUDIT #69). Auth errors keep a longer window
                # because a bad credential is less likely to self-heal, but
                # still recover so a rotated/repaired key returns to service.
                key.mark_invalid(min(60.0 * (2 if status in (401, 403) else 1),
                                     600.0))
            else:
                # short cooldown so the next request rotates to a different key
                # (5xx retry-after honored when present, else a default).
                ra = retry_after if (retry_after and retry_after > 0) else 5.0
                key.mark_cooling(min(ra, 30.0))
            return
        # standard mode
        if status == 429:
            key.err_count += 1
            key.mark_cooling(retry_after if retry_after and retry_after > 0 else 30.0)
        elif status in (401, 403):
            key.err_count += 1
            key.mark_invalid(600.0)

    async def on_result_locked(self, key: ProviderKey | None, status: int | None,
                               retry_after: float | None,
                               failover_mode: str = "any_error",
                               key_max_consecutive_fails: int = 5) -> None:
        """Async variant that takes ``_rr_lock`` so it cannot interleave with
        :meth:`pick_key`'s read of the same state.  Use this from any path
        that may run concurrently with a fresh pick_key for the same account.
        """
        async with self._rr_lock:
            self.on_result(key, status, retry_after,
                           failover_mode=failover_mode,
                           key_max_consecutive_fails=key_max_consecutive_fails)

#: Per-deployment rpm/tpm windows are 60s sliding windows, matching the
#: virtual-key limiter (wiwi/ratelimit/memory.py) so both caps describe the
#: same "per minute" interval.
_WINDOW_S = 60.0


@dataclass
class _DepEvent:
    """One reservation inside a deployment's sliding window."""
    ts: float
    tokens: int
    estimated: bool = False
    request_id: str = ""


@dataclass
class _DepWindow:
    """60s sliding window over ``_DepEvent``s with an O(1) running total.

    ``rpm`` windows hold one event per admitted request (``tokens == 1``);
    ``tpm`` windows hold one event per request carrying its token cost.
    """
    events: deque = field(default_factory=deque)
    total: int = 0

    def prune(self, now: float) -> None:
        cutoff = now - _WINDOW_S
        while self.events and self.events[0].ts < cutoff:
            self.total -= self.events.popleft().tokens

    def add(self, event: _DepEvent) -> None:
        self.events.append(event)
        self.total += event.tokens

    def newest_estimated(self) -> _DepEvent | None:
        """Newest *estimated* reservation — the fallback for id-less callers.

        Deliberately takes no request id. The previous helper here was
        ``find_estimated(request_id)``, which "preferred an exact request-id
        match" but silently fell back to the newest estimated event when the
        id was not found; ``settle_tokens`` called it *first*, so a request
        whose own reservation had aged out of the window while it was still
        streaming adopted **another live request's** reservation — overwriting
        that request's token count and flipping it to confirmed. The window
        then under-counted (admitting past the cap) and the rightful owner
        became unrefundable (AUDIT #190). Callers that carry an id must resolve
        it with :meth:`find_event` and, when that misses, *append* — never
        reach for another request's estimate. Mirrors
        :meth:`wiwi.ratelimit.memory.RateLimiter._newest_estimated`.
        """
        for e in reversed(self.events):
            if e.estimated:
                return e
        return None

    def find_event(self, request_id: str) -> _DepEvent | None:
        """Newest event for *request_id*, estimated or already settled.

        Used by :meth:`Deployment.settle_tokens` so a second settle for the
        same request *adjusts* the existing charge instead of appending a
        duplicate. That path is real: the pump prices a completed stream and
        can then be cancelled while blocking on the output queue, and its
        cancellation handler prices the same request again.
        """
        if request_id:
            for e in reversed(self.events):
                if e.request_id == request_id:
                    return e
        return None

    def drop_matching(self, request_id: str, estimated_only: bool = False) -> bool:
        """Remove this request's reservation from the window.

        With a ``request_id`` the match is strict — an id that has no event
        (already refunded, or a request whose usage was confirmed) removes
        nothing. That strictness is what makes a double release harmless: the
        second call cannot fall through and evict an unrelated request's slot.
        ``estimated_only`` additionally refuses to refund confirmed usage, so a
        stream that delivered tokens and then died stays billed.
        """
        if request_id:
            for e in reversed(self.events):
                if e.request_id == request_id and (e.estimated or not estimated_only):
                    self.events.remove(e)
                    self.total = max(0, self.total - e.tokens)
                    return True
            return False
        # No id to match on: fall back to the newest eligible event.
        for e in reversed(self.events):
            if e.estimated or not estimated_only:
                self.events.remove(e)
                self.total = max(0, self.total - e.tokens)
                return True
        return False


@dataclass
class Deployment:
    group: str
    provider: ProviderAccount
    model_id: str
    weight: int = 1
    rpm: int | None = None
    tpm: int | None = None
    timeout: float | None = None
    max_tokens: int | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    # Anthropic prompt caching: opt-in injection of cache breakpoints on the
    # stable prefix (tools + system). Off by default.
    prompt_cache: bool = False
    prompt_cache_min_tokens: int | None = None
    # cooldown / health
    fails: list[float] = field(default_factory=list)
    cooldown_until: float = 0.0
    inflight: int = 0
    # Probation: set by the HealthHealer on restore. pick_deployment prefers
    # non-probation deployments; execute_with_retries graduates on success,
    # record_fail demotes.
    probation: bool = False
    latencies: deque = field(default_factory=lambda: deque(maxlen=50))
    # Per-deployment sliding windows for the optional rpm/tpm caps
    # (AUDIT #101). Created lazily on first reservation so an uncapped
    # deployment pays nothing. Every window operation below is synchronous, so
    # in asyncio's cooperative model a check-and-reserve in ``pick_deployment``
    # cannot be interleaved by another coroutine — no lock is needed.
    _rpm_window: _DepWindow | None = field(default=None, repr=False, compare=False)
    _tpm_window: _DepWindow | None = field(default=None, repr=False, compare=False)

    @property
    def limited(self) -> bool:
        """Whether this deployment declares any per-deployment cap."""
        return bool(self.rpm or self.tpm)

    def _window(self, is_token: bool) -> _DepWindow:
        if is_token:
            if self._tpm_window is None:
                self._tpm_window = _DepWindow()
            return self._tpm_window
        if self._rpm_window is None:
            self._rpm_window = _DepWindow()
        return self._rpm_window

    def rate_limited(self, now: float | None = None, est_tokens: int = 0) -> bool:
        """Whether admitting *est_tokens* would cross an rpm/tpm cap.

        Admission-shaped: ``rpm`` costs one event, ``tpm`` costs *est_tokens*.
        Returns False immediately for an uncapped deployment, so the routing
        hot path is unchanged unless an operator set a cap.
        """
        if not self.limited:
            return False
        now = time.monotonic() if now is None else now
        if self.rpm:
            w = self._window(False)
            w.prune(now)
            if w.total + 1 > self.rpm:
                return True
        if self.tpm:
            w = self._window(True)
            w.prune(now)
            if w.total + max(0, est_tokens) > self.tpm:
                return True
        return False

    def reserve_slot(self, request_id: str, est_tokens: int = 0) -> None:
        """Take this deployment's rpm/tpm slot for an admitted request.

        Both events start ``estimated``: the rpm charge is a request that has
        not completed yet, and the tpm charge is an *estimate*.
        :meth:`settle_tokens` clears the flag once actual usage is known, and
        :meth:`release_slot` refunds only still-estimated events — so a
        completed request can never have its slots reclaimed by a late refund.
        """
        if not self.limited:
            return
        now = time.monotonic()
        if self.rpm:
            self._window(False).add(_DepEvent(ts=now, tokens=1,
                                              estimated=True,
                                              request_id=request_id))
        if self.tpm:
            self._window(True).add(_DepEvent(ts=now,
                                             tokens=max(0, est_tokens),
                                             estimated=True,
                                             request_id=request_id))

    def settle_tokens(self, request_id: str, tokens: int) -> None:
        """Mark this request complete: replace the tpm estimate with actual
        usage and stop either slot from being refundable.

        The reservation is resolved strictly by identity — ``id`` → any event
        carrying that id → **append** — so a request whose own reservation has
        already aged out of the 60 s window while it was still streaming writes
        its real usage as a new event instead of adopting a different in-flight
        request's estimate. The lenient newest-estimated arm is reachable only
        for id-less callers, where at most one request per deployment can be in
        flight (AUDIT #190; mirrors
        :meth:`wiwi.ratelimit.memory.RateLimiter.record_tokens`).

        Idempotent for repeated settles of the same request: the pump prices a
        completed stream and can then be cancelled while blocking on the
        output queue, and its cancellation handler prices the same request
        again. Appending a second event would double-charge the cap, so an
        existing settled event is *adjusted* to the new value instead.
        """
        if not self.limited:
            return
        now = time.monotonic()
        if self.tpm:
            w = self._window(True)
            w.prune(now)
            target = w.find_event(request_id) if request_id else None
            if target is None and not request_id:
                target = w.newest_estimated()
            if target is not None:
                w.total += max(0, tokens) - target.tokens
                target.tokens = max(0, tokens)
                target.estimated = False
            else:
                # No reservation for this id (its own aged out of the window,
                # or this is a resume attempt on a deployment that never
                # admitted the original request): record the actual usage as a
                # new event so the window still accounts for tokens the
                # provider really billed.
                w.add(_DepEvent(ts=now, tokens=max(0, tokens),
                                request_id=request_id))
        if self.rpm:
            # Settle the rpm event too, so the completed request holds its
            # slot until it ages out rather than being refundable.
            w = self._window(False)
            w.prune(now)
            for e in reversed(w.events):
                if e.request_id == request_id:
                    e.estimated = False
                    break

    def release_slot(self, request_id: str) -> None:
        """Refund an admitted-but-unpriced request's rpm/tpm slots.

        Only *estimated* events are refunded, so a request that completed (or
        a stream that delivered tokens and then died) keeps its slot and stays
        billed against the cap — mirroring
        :meth:`wiwi.ratelimit.memory.RateLimiter.release`.
        """
        if not self.limited:
            return
        now = time.monotonic()
        if self.tpm:
            w = self._window(True)
            w.prune(now)
            w.drop_matching(request_id, estimated_only=True)
        if self.rpm:
            w = self._window(False)
            w.prune(now)
            w.drop_matching(request_id, estimated_only=True)

    def retry_after_s(self, now: float | None = None) -> int:
        """Seconds until the next slot frees, clamped to the window (1..60)."""
        now = time.monotonic() if now is None else now
        oldest: float | None = None
        for w, limit in ((self._rpm_window, self.rpm), (self._tpm_window, self.tpm)):
            if not limit or w is None:
                continue
            w.prune(now)
            if w.events and (oldest is None or w.events[0].ts < oldest):
                oldest = w.events[0].ts
        if oldest is None:
            return 1
        return max(1, min(60, int(_WINDOW_S - (now - oldest)) + 1))

    @property
    def available(self) -> bool:
        return self.provider.healthy and time.monotonic() >= self.cooldown_until

    def record_fail(self, allowed_fails: int, cooldown_time: float) -> None:
        self.probation = False  # demoted: the cooldown path re-arms from here
        now = time.monotonic()
        self.fails.append(now)
        # The window must outlast the interval at which a chronically failing
        # deployment fails, otherwise each failure is pruned before the next
        # arrives and `allowed_fails` is never reached. At the shipped
        # cooldown_time of 30s the old max(60, 2*30) == 60s window was
        # identical to the original 60s, so anything failing less often than
        # once a minute never cooled down at all. The floor keeps a
        # near-zero cooldown from making the window uselessly small; the
        # ceiling bounds how many timestamps we retain.
        window = max(300.0, min(6.0 * max(cooldown_time, 1.0), 3600.0))
        recent = [t for t in self.fails if now - t < window]
        self.fails = recent
        if len(recent) >= allowed_fails:
            self.cooldown_until = now + cooldown_time
            self.fails.clear()

    def record_success(self) -> None:
        """Decay the failure streak after a clean completion.

        Without this, ``fails`` only ever grows: a high-volume deployment with
        a tiny error rate accumulates ``allowed_fails`` timestamps over its
        window and is cooled continuously even though the overwhelming
        majority of requests succeed (AUDIT #93). A success prunes the oldest
        failure so the streak reflects the *recent* failure rate. One prune per
        success (rather than a full clear) means a genuinely flapping
        deployment still crosses the threshold, while a healthy one decays
        back to zero.
        """
        if self.fails:
            self.fails.pop(0)

    def p95_latency(self) -> float:
        return percentile(self.latencies, 0.95)

    def mark_recovered(self) -> None:
        """Healer restore: clear the cooldown and enter probation."""
        self.probation = True
        self.cooldown_until = 0.0
        self.fails.clear()


def _alias_target(v: str | ModelAliasEntry) -> str:
    """Extract the next-hop group name from a ``model_group_alias`` value.

    Plain-string values pass through; rich entries (shinway-style
    ``ModelAliasEntry``) expose their ``target`` field. This lets the alias
    chain walk in ``Router.resolve_group`` stay agnostic to the value form.
    """
    if isinstance(v, ModelAliasEntry):
        return v.target
    return v


class Router:
    def __init__(self, config: WiwiConfig):
        self.settings: RouterSettings = config.router_settings
        # WRR multiplier for keys in probation status (from HealerSettings).
        self.probation_weight = config.healer.probation_weight
        self.providers: dict[str, ProviderAccount] = {}
        self.groups: dict[str, list[Deployment]] = {}
        # Proxy-log emitter for gateway-op events (upstream 5xx, key cooldown,
        # retries, fallback switches). Wired to the LoggingSubsystem by the app
        # at startup. No-op by default so Router works standalone (tests/fakes).
        self.log_proxy = self._noop_log_proxy
        # alias_id -> provider_name (per-provider alias registry, distinct
        # from router_settings.model_group_alias which is a string->string
        # group name rewrite).  Built from config.providers[*].alias_id
        # so admins can expose a provider account under a stable alias.
        self.alias_to_provider: dict[str, str] = {}
        # group_name -> per-provider WRR cursor for cross-provider rotation.
        # Only populated for groups whose deployments span 2+ providers;
        # single-provider groups keep their original shuffle semantics.
        self._group_provider_rr: dict[str, _CrossProviderWRR] = {}
        # Consecutive-success counters for ``cycle_every_n``. These MUST live on
        # the router, not in ``ctx.metadata``: the context is per-request, so
        # counters kept there reset to 0 before every pick and the cadence
        # could never fire (AUDIT #78). Keyed by provider name and by
        # (provider name, key label).
        self._provider_consec: dict[str, int] = {}
        self._key_consec: dict[tuple[str, str], int] = {}
        self._build(config)

    def _noop_log_proxy(self, level: str, message: str, request_id: str = "",
                        **kw: object) -> None:
        """Default proxy-log emitter: does nothing. Overridden at startup."""
        return

    def _build(self, config: WiwiConfig) -> None:
        for p in config.providers:
            acct = ProviderAccount(
                name=p.name, provider_type=p.provider,
                base_url=p.base_url or _default_base_url(p.provider),
                timeout_s=p.timeout_s, extra_headers=dict(p.extra_headers),
                round_robin=p.round_robin,
                keys=[ProviderKey(label=k.label, secret=k.key, weight=k.weight,
                                  enabled=k.enabled) for k in p.keys],
                alias_id=p.alias_id,
            )
            self.providers[p.name] = acct
            if p.alias_id:
                # Pre-existing alias_id wins; config validator already rejects
                # duplicates so this assignment is unambiguous.
                self.alias_to_provider.setdefault(p.alias_id, p.name)
        for entry in config.model_list:
            wp = entry.wiwi_params
            acct = self.providers.get(wp.provider)
            if acct is None:
                raise ValueError(f"model {entry.model_name!r} references unknown provider"
                                 f" {wp.provider!r}")
            dep = Deployment(group=entry.model_name, provider=acct, model_id=wp.model,
                             weight=wp.weight, rpm=wp.rpm, tpm=wp.tpm,
                             timeout=wp.timeout, max_tokens=wp.max_tokens,
                             extra_headers=dict(wp.extra_headers),
                             extra_body=dict(wp.extra_body),
                             prompt_cache=wp.prompt_cache,
                             prompt_cache_min_tokens=wp.prompt_cache_min_tokens)
            self.groups.setdefault(entry.model_name, []).append(dep)
        # Cross-provider WRR is only meaningful for groups whose deployments
        # span at least two distinct provider accounts.  Single-provider
        # groups keep their original pick_deployment shuffle semantics.
        self.rebuild_cross_provider_pools()
        # alias resolution happens at route(); aliases may point to any group name

    def resolve_group(self, requested: str) -> tuple[str | None, list[Deployment]]:
        # First, see if `requested` is a provider alias_id.  If so, the call
        # is asking "give me a model that this provider can serve" — return
        # every deployment whose provider matches.  Empty list means the
        # alias exists but the provider serves no models in model_list, and
        # the gateway surface treats that as 404 like any other unknown group.
        pname = self.alias_to_provider.get(requested)
        if pname is not None:
            deps = [d for d in self.groups.values() for d in d
                    if d.provider.name == pname]
            return (requested, deps) if deps else (None, [])
        name = requested
        seen: set[str] = {name}
        for _ in range(8):  # bounded walk: aliases may chain, never cycle
            nxt = _alias_target(self.settings.model_group_alias.get(name))
            if nxt is None or nxt == name:
                break
            if nxt in seen:
                # Alias cycle (a -> b -> a): fail closed instead of resolving
                # arbitrarily to whichever intermediate group the hop budget
                # happened to land on (AUDIT #109).
                log.warning("model_group_alias_cycle", requested=requested,
                            group=nxt, chain=sorted(seen))
                return None, []
            seen.add(nxt)
            name = nxt
        deps = self.groups.get(name, [])
        return (name, deps) if deps else (None, [])

    def pick_deployment(self, deps: list[Deployment], ctx: RequestContext,
                        exclude: set[int] | None = None) -> Deployment | None:
        """Pick a healthy deployment. `exclude` holds id()s of deployments that
        already failed this request, so retries land on a *different* deployment
        when one exists (LiteLLM semantics).

        A deployment whose per-deployment ``rpm``/``tpm`` window is full is
        skipped so traffic diverts to a sibling (AUDIT #101). When every
        candidate is saturated this returns ``None`` — the caller turns that
        into a 429 rather than silently overrunning the configured cap.
        The picked deployment's slot is reserved here, at admission, because
        this is the only point that can both see the cap and choose to avoid it.
        """
        exclude = exclude or set()
        avail = [d for d in deps if d.available and id(d) not in exclude]
        if not avail:
            avail = [d for d in deps if d.available]  # nothing fresh left: reuse allowed
        if not avail:
            return None
        est = getattr(ctx, "est_tokens", 0)
        uncapped = [d for d in avail if not d.rate_limited(est_tokens=est)]
        if not uncapped:
            # Every candidate is at its cap: refuse rather than exceed it.
            return None
        avail = uncapped
        # Prefer fully-healthy deployments; probation ones only serve when no
        # fresh sibling exists (the healer restored them on a trial basis).
        fresh = [d for d in avail if not d.probation]
        if fresh:
            avail = fresh
        strategy = self.settings.routing_strategy
        chosen = self._choose(avail, deps, strategy)
        # Reserve at the single exit point: every strategy path above returns
        # through here, so a new strategy cannot forget the reservation (the
        # first cut reserved inside two of the four branches, leaving
        # simple-shuffle and the cross-provider pool uncapped).
        chosen.reserve_slot(getattr(ctx, "request_id", ""), est)
        return chosen

    def _choose(self, avail: list[Deployment], deps: list[Deployment],
                strategy: str) -> Deployment:
        """Apply the routing strategy to an already-filtered candidate list."""
        if strategy == "least-busy":
            return min(avail, key=lambda d: d.inflight)
        if strategy == "latency-based":
            # p95 == 0 means "no samples yet": among cold deployments,
            # break ties randomly so they get explored instead of all
            # traffic pinning to the first one in list order.
            cold = [d for d in avail if d.p95_latency() == 0.0]
            if cold and len(cold) == len(avail):
                return random.choice(cold)
            return min(avail, key=lambda d: d.p95_latency())
        # Cross-provider weighted round-robin: when this group has
        # deployments on 2+ providers, rotate across providers (provider-then-key)
        # instead of weighted-shuffling every pick.  This means each provider
        # gets a contiguous burst of key rotations before we move on, which
        # matches the user's "round robin over key plus provider" requirement.
        rr = self._group_provider_rr.get(deps[0].group)
        if rr is not None and len({d.provider.name for d in avail}) >= 2:
            return rr.pick(avail)
        # simple-shuffle: weight-weighted random
        total = sum(d.weight for d in avail)
        r = random.uniform(0, total)
        upto = 0.0
        for d in avail:
            upto += d.weight
            if r <= upto:
                return d
        return avail[-1]

    def fallback_targets(self, failed_group: str, ctx_kind: str = "fallbacks") -> list[str]:
        table = getattr(self.settings, ctx_kind)
        return table.get(failed_group, [])

    def set_provider_alias(self, name: str, alias_id: str | None) -> None:
        """Update the alias id on a provider account and keep the
        alias-to-provider map consistent.

        Setting an empty/None alias removes the entry.  Raises ValueError
        if ``alias_id`` is already claimed by a different provider.
        """
        acct = self.providers.get(name)
        if acct is None:
            raise ValueError(f"unknown provider {name!r}")
        # Remove any prior mapping pointing at this provider so a rename
        # of just the alias is a clean swap.
        for k, v in list(self.alias_to_provider.items()):
            if v == name:
                del self.alias_to_provider[k]
        if alias_id:
            prior = self.alias_to_provider.get(alias_id)
            if prior is not None and prior != name:
                raise ValueError(
                    f"alias_id {alias_id!r} already used by provider {prior!r}")
            self.alias_to_provider[alias_id] = name
        acct.alias_id = alias_id

    def rebuild_cross_provider_pools(self) -> None:
        """Recompute which groups need a cross-provider WRR cursor.

        Called by the admin API after a deployment is added/removed so the
        pool layer tracks the live set of multi-provider groups exactly.
        """
        self._group_provider_rr.clear()
        for gname, deps in self.groups.items():
            providers = {d.provider.name for d in deps}
            if len(providers) >= 2:
                self._group_provider_rr[gname] = _CrossProviderWRR()


@dataclass
class _CrossProviderWRR:
    """Smooth weighted round-robin cursor over provider accounts within one
    model group.  Each provider appears once with weight = sum of its
    deployments' weights in the group.  When a provider is picked, the
    per-provider key WRR (in ProviderAccount.pick_key) picks the actual key.

    The nginx smooth-WRR algorithm keeps deficits so a temporarily-unhealthy
    provider doesn't get starved after it recovers.  Weights are recomputed
    from the *available* deployments on every pick — a provider whose keys are
    all cooling must not keep claiming its share of the cursor.
    """
    _state: dict[str, float] = field(default_factory=dict)

    def pick(self, avail: list[Deployment]) -> Deployment | None:
        # Only consider providers with at least one available deployment.
        avail_providers: dict[str, int] = {}
        for d in avail:
            avail_providers[d.provider.name] = (
                avail_providers.get(d.provider.name, 0) + d.weight)
        if not avail_providers:
            return None
        # nginx smooth WRR over the available providers.
        total = sum(avail_providers.values())
        for pname, weight in avail_providers.items():
            self._state[pname] = self._state.get(pname, 0.0) + weight
        best = max(avail_providers, key=lambda p: self._state.get(p, 0.0))
        self._state[best] = self._state.get(best, 0.0) - total
        # Within the chosen provider, pick the first available deployment
        # for the requested model.  Multi-deployment-per-provider in the
        # same group is rare; if it happens, fall back to the first match.
        for d in avail:
            if d.provider.name == best:
                return d
        return None


def _default_base_url(provider_type: str) -> str:
    """Look up the default base URL from the built-in provider catalog.

    Derived from BUILTIN_PROVIDER_TYPES so there is a single source of truth —
    adding a provider type to the catalog automatically makes its default URL
    available here. Returns "" for types with no fixed default (e.g.
    openai-compatible) or unknown types.
    """
    for p in BUILTIN_PROVIDER_TYPES:
        if p["provider_type"] == provider_type:
            return p["default_base_url"]
    return ""


# Built-in provider types that ship with wiwi and can be selected by name
# in the admin UI. Mirrors registry.get_adapter's recognized types.
# Metadata sourced from each provider's official API docs as of Aug 2026.
BUILTIN_PROVIDER_TYPES: list[dict[str, str | list[str]]] = [
    {
        "provider_type": "openai",
        "label": "OpenAI",
        "default_base_url": "https://api.openai.com/v1",
        "description": (
            "GPT-5.6 family models via the OpenAI Chat Completions and "
            "Responses APIs. Supports reasoning effort, tool use, "
            "structured outputs, and vision."
        ),
        "latest_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
        "context_window": "1.05M tokens",
        "docs_url": "https://developers.openai.com/api/docs/models",
    },
    {
        "provider_type": "anthropic",
        "label": "Anthropic",
        "default_base_url": "https://api.anthropic.com/v1",
        "description": (
            "Claude models via the Anthropic Messages API. Features adaptive "
            "thinking, tool use, vision, and 1M-token context windows on "
            "Opus and Sonnet."
        ),
        "latest_models": [
            "claude-fable-5",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5",
        ],
        "context_window": "1M tokens",
        "docs_url": "https://platform.claude.com/docs/en/about-claude/models/overview",
    },
    {
        "provider_type": "gemini",
        "label": "Google Gemini",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "description": (
            "Gemini models via the Google Generative Language API. "
            "Multimodal input (text, images, video, audio), structured "
            "output, function calling, and the Interactions API."
        ),
        "latest_models": [
            "gemini-3.1-pro-preview",
            "gemini-3.7-flash",
            "gemini-3.5-flash-lite",
        ],
        "context_window": "1M+ tokens",
        "docs_url": "https://ai.google.dev/gemini-api/docs",
    },
    {
        "provider_type": "openrouter",
        "label": "OpenRouter",
        "default_base_url": "https://openrouter.ai/api/v1",
        "description": (
            "Unified gateway to 400+ models from 60+ providers via a single "
            "OpenAI-compatible endpoint. Automatic fallbacks, reasoning "
            "parameter translation, and a latest-alias system."
        ),
        "latest_models": [
            "~openai/gpt-latest",
            "~anthropic/claude-sonnet-latest",
            "google/gemini-3.1-pro-preview",
            "minimax/minimax-m3",
        ],
        "context_window": "varies per model",
        "docs_url": "https://openrouter.ai/docs/quickstart",
    },
    {
        "provider_type": "openai-compatible",
        "label": "OpenAI-compatible",
        "default_base_url": "",
        "description": (
            "Any endpoint that speaks the OpenAI Chat Completions wire "
            "format (e.g. vLLM, Ollama, Together, Groq, DeepSeek). "
            "Provide a custom base URL."
        ),
        "latest_models": [],
        "context_window": "varies by endpoint",
        "docs_url": "",
    },
    {
        "provider_type": "cline",
        "label": "Cline",
        "default_base_url": "https://api.cline.bot/api/v1",
        "description": (
            "Cline's provider gateway (api.cline.bot) via an OpenAI-compatible "
            "Chat Completions API with streaming-only responses. Authenticates "
            "with a Cline account OAuth token (WorkOS) and requires Cline "
            "client-identification headers, which wiwi sends automatically."
        ),
        "latest_models": ["z-ai/glm-5.2", "claude-sonnet-5", "gpt-5.5"],
        "context_window": "varies per model",
        "docs_url": "https://cline.bot",
    },
    {
        "provider_type": "gmicloud",
        "label": "GMI Cloud",
        "default_base_url": "https://api.gmi-serving.com/v1",
        "description": (
            "Serverless and dedicated GPU inference for 70+ open-source "
            "and frontier models (DeepSeek, GLM, Llama, Qwen, Claude, "
            "GPT, Gemini, Kimi) via an OpenAI-compatible Chat Completions "
            "API. Supports streaming, tool use, vision, and reasoning "
            "models with reasoning_content."
        ),
        "latest_models": [
            "deepseek-ai/DeepSeek-V3.2",
            "zai-org/GLM-5-FP8",
            "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
            "Qwen/Qwen3.6-Plus",
            "openai/gpt-5.5",
            "google/gemini-3.1-pro-preview",
            "anthropic/claude-opus-4.7",
            "moonshotai/Kimi-K2.6",
        ],
        "context_window": "varies per model",
        "docs_url": "https://docs.gmicloud.ai/quickstart",
    },
    {
        "provider_type": "bai",
        "label": "B.AI",
        "default_base_url": "https://api.b.ai/v1",
        "description": (
            "B.AI's unified LLM service (api.b.ai): one API key across the "
            "OpenAI Chat Completions, OpenAI Responses, and Anthropic "
            "Messages protocols. wiwi speaks Chat Completions and re-encodes "
            "any inbound dialect. Hosts DeepSeek thinking-mode models — the "
            "adapter replays reasoning_content on tool-call turns so their "
            "documented 400 round-trip requirement never fires."
        ),
        "latest_models": ["deepseek-v4-flash-vision-exp", "deepseek-v4-flash"],
        "context_window": "varies per model",
        "docs_url": "https://docs.b.ai/llmservice/api/",
    },
    {
        "provider_type": "workbuddy",
        "label": "WorkBuddy",
        "default_base_url": "https://copilot.tencent.com",
        "description": (
            "WorkBuddy / CodeBuddy (Tencent copilot.tencent.com, workbuddy.ai) "
            "via an OpenAI-compatible Chat Completions API. Authenticates with "
            "an OAuth access token + account uid headers, is streaming-only "
            "upstream, requires string tool_choice, and wraps errors in a "
            "{code,msg,data} envelope. wiwi sends the account headers and "
            "handles token refresh automatically."
        ),
        "latest_models": [
            "deepseek-v4-flash",
            "glm-5.3",
            "kimi-k3",
            "hy3",
        ],
        "context_window": "varies per model",
        "docs_url": "https://www.codebuddy.cn",
    },
    {
        "provider_type": "nvidia-nim",
        "label": "NVIDIA NIM",
        "default_base_url": "https://integrate.api.nvidia.com/v1",
        "description": (
            "NVIDIA NIM hosts 50+ open and frontier models (Nemotron, "
            "DeepSeek, GLM, Llama, Qwen, Kimi, MiniMax, StepFun) on "
            "optimized GPU infrastructure via an OpenAI-compatible Chat "
            "Completions API. Supports streaming, tool use, reasoning "
            "via chat_template_kwargs, and reasoning_content."
        ),
        "latest_models": [
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "deepseek-ai/deepseek-v4-pro",
            "zai-org/glm-5.2",
            "moonshotai/kimi-k2.6",
            "minimaxai/minimax-m3",
            "stepfun-ai/step-3.7-flash",
        ],
        "context_window": "varies per model",
        "docs_url": "https://docs.nvidia.com/nim/large-language-models/latest/",
    },
    {
        "provider_type": "opencode",
        "label": "OpenCode Zen",
        "default_base_url": "https://opencode.ai/zen/v1",
        "description": (
            "OpenCode Zen gateway (opencode.ai/zen) — curated coding models "
            "over four upstream protocols routed per model: Responses API "
            "for GPT/Grok/Muse-Spark, Messages API for Claude/Qwen, Gemini "
            "generateContent for Gemini, Chat Completions for the rest. "
            "Authenticates with a Zen API key and sends a live "
            "User-Agent: opencode/<version> (refreshed every 5 min) so "
            "Cloudflare/version gates never restrict traffic."
        ),
        "latest_models": [
            "gpt-5.5",
            "claude-sonnet-5",
            "gemini-3.1-pro",
            "deepseek-v4-pro",
            "glm-5.2",
            "kimi-k2.6",
        ],
        "context_window": "varies per model",
        "docs_url": "https://opencode.ai/docs/zen",
    },
]


# Sanity check: every catalog entry must be a recognized provider type, and
# every provider type in PROVIDER_TYPES should have a catalog card. This fails
# at import time so a new provider type added to config.py without a matching
# catalog entry (or vice-versa) is caught immediately, not silently at runtime.
_catalog_types = {p["provider_type"] for p in BUILTIN_PROVIDER_TYPES}
assert _catalog_types == set(PROVIDER_TYPES), (
    f"BUILTIN_PROVIDER_TYPES catalog ({_catalog_types}) is out of sync with "
    f"wiwi.config.PROVIDER_TYPES ({set(PROVIDER_TYPES)}) — update both"
)


# Historical retry sleep between failover attempts, extracted verbatim:
# min(5, max(retry_after, 0.5*2**attempt)) + uniform(0, 0.25).  Pinned by
# tests/test_recovery.py::TestBackoff::test_matches_router_inline_math.
_RETRY_BACKOFF = Backoff(base_s=0.5, cap_s=5.0, jitter_s=0.25)


async def execute_with_retries(router: Router, ctx: RequestContext,
                               call_one) -> Any:
    """call_one(dep, key) -> result; raises WiwiError on failure.
    Walks: primary group deployments (retries per settings), then fallback groups.

    Cycle-3 + any-error failover: when ``router.settings.cycle_every_n > 0``,
    after a key has served N consecutive successful requests the next pick
    excludes it (forces the WRR cursor to advance).  Same for the
    cross-provider cursor: after a provider has served N consecutive
    requests the next pick prefers a different provider.  When
    ``failover_mode == "any_error"`` (default), any non-200 applies a short
    cooldown so the next pick rotates to a different key — keys are only
    permanently retired after ``key_max_consecutive_fails`` consecutive
    failures (auth errors count double).
    """
    cycle_n = max(0, router.settings.cycle_every_n)
    failover_mode = router.settings.failover_mode
    key_max_fails = router.settings.key_max_consecutive_fails
    # Consecutive-success counters for the cycle cadence. They live on the
    # router so they survive across requests; a per-request dict made the
    # cadence unreachable (AUDIT #78). ``getattr`` guards legacy test fakes
    # (plain classes with only a ``group`` attribute). Note: don't use
    # ``getattr(...) or {}`` here — an empty dict is falsy, so the first
    # increment would land on a throwaway local.
    provider_consec: dict[str, int] = getattr(router, "_provider_consec", None)
    key_consec: dict[tuple[str, str], int] = getattr(router, "_key_consec", None)
    if provider_consec is None or key_consec is None:
        provider_consec, key_consec = {}, {}
    first_error: WiwiError | None = None
    queue: list[str] = []
    if ctx.group:
        queue.append(ctx.group)
    seen: set[str] = set(queue)

    # Proxy-log emitter for gateway-op events. `log_proxy` defaults to a no-op
    # on Router; the app wires it to the LoggingSubsystem at startup.
    proxy_log = router.log_proxy
    req_id = getattr(ctx, "request_id", "")

    def _proxy(level: str, message: str) -> None:
        proxy_log(level, message, req_id)

    while queue:
        group_name = queue.pop(0)
        _, deps = router.resolve_group(group_name)
        if not deps:
            continue
        last_err: WiwiError | None = None
        group_first_err: WiwiError | None = None
        tried_dep_ids: set[int] = set()
        tried_key_labels: set[tuple[str, str]] = set()
        for attempt in range(router.settings.num_retries + 1):
            # cycle-3: if the chosen provider has served N consecutive
            # requests already, prefer a different one this round.
            prefer_exclude: set[int] = set(tried_dep_ids)
            if cycle_n > 0:
                for d in deps:
                    pname = d.provider.name
                    if provider_consec.get(pname, 0) >= cycle_n:
                        prefer_exclude.add(id(d))
            dep = router.pick_deployment(deps, ctx, exclude=prefer_exclude)
            if dep is None:
                # relax cycle exclusion and try again with just the tried dep set
                dep = router.pick_deployment(deps, ctx, exclude=tried_dep_ids)
                if dep is None:
                    # Distinguish "nothing healthy" (503 — an outage) from
                    # "everything healthy but at its per-deployment rpm/tpm
                    # cap" (429 — a rate limit, retryable, with a horizon).
                    # Reporting the cap as 503 told the client to give up on a
                    # deployment that is serving fine (AUDIT #101).
                    now = time.monotonic()
                    capped = [d for d in deps
                              if d.available
                              and d.rate_limited(now, getattr(ctx, "est_tokens", 0))]
                    if capped:
                        retry_after = min(d.retry_after_s(now) for d in capped)
                        last_err = WiwiError(
                            429, "rate_limit_error",
                            f"all deployments for '{group_name}' are at their"
                            f" rpm/tpm cap", retry_after=float(retry_after))
                        _proxy("warn",
                               f"deployment cap reached for '{group_name}':"
                               f" retry in {retry_after}s")
                    else:
                        last_err = WiwiError(503, "service_unavailable",
                                             f"no healthy deployment for '{group_name}'",
                                             retryable=True)
                    break
            key_exclude = {lbl for (pn, lbl) in tried_key_labels
                           if pn == dep.provider.name}
            if cycle_n > 0:
                # Key-level cadence: after a key has served cycle_n consecutive
                # successful requests, skip it for this pick so traffic rotates
                # even when weights are lopsided (AUDIT #78). ``pick_key``
                # falls back to the full set when this excludes every key.
                #
                # The counter is *consumed* here — cleared for every key the
                # exclusion covers — rather than left climbing. Left alone it
                # saturates: once every key in the pool has served cycle_n
                # times the exclusion set contains all of them, ``pick_key``
                # hits its "every key excluded" fallback and ignores the
                # exclusion, and the counters only ever reset on error, so the
                # cadence was a permanent no-op indistinguishable from
                # cycle_every_n=0 (AUDIT #226).
                for (pn, lbl), n in list(key_consec.items()):
                    if pn == dep.provider.name and n >= cycle_n:
                        key_exclude.add(lbl)
                        key_consec.pop((pn, lbl), None)
            key, retry_in = await dep.provider.pick_key(
                exclude_labels=key_exclude,
                probation_weight=getattr(router, "probation_weight", 1.0),
            )
            if key is None:
                # The slot was reserved at pick time but no upstream call will
                # happen: refund it, or a key outage would hold the
                # deployment's rpm/tpm window for the full 60s.
                _refund_deployment_slot(dep, ctx)
                tried_dep_ids.add(id(dep))
                tried_key_labels.add((dep.provider.name, "*"))
                last_err = WiwiError(429, "rate_limit_error",
                                     f"all keys cooling for provider"
                                     f" '{dep.provider.name}'", retry_after=max(1.0, retry_in))
                fresh = any(d.available and id(d) not in tried_dep_ids for d in deps)
                if not fresh:
                    # All keys cooling and no fresh deployments: wait for a
                    # key to recover, then clear exclusions so the deployment
                    # can be retried instead of breaking with a 503.
                    if attempt < router.settings.num_retries:
                        await asyncio.sleep(min(5.0, max(1.0, retry_in)))
                        tried_dep_ids.clear()
                        tried_key_labels.clear()
                    continue
                continue  # siblings may have live keys
            tried_key_labels.add((dep.provider.name, key.label))
            # inflight/latency accounting lives in the gateway: for streams the
            # request stays in flight until the pump finishes, not until
            # execute_with_retries returns (which happens at connect time).
            try:
                result = await call_one(
                    dep, ProviderKeyRef(label=key.label, secret=key.secret), ctx)
                # Success: account the key — except for streaming, where
                # `call_one` returns at *connect* time, long before we know
                # whether the stream will actually deliver anything. Crediting
                # here resets err_count to 0, so a key that connects and then
                # dies mid-stream never accumulates a retirement streak and
                # keeps getting picked first (AUDIT #6). The pump credits the
                # key itself once the stream completes cleanly.
                if not getattr(ctx, "_defer_key_credit", False):
                    await dep.provider.on_result_locked(key, 200, None,
                                                        failover_mode=failover_mode,
                                                        key_max_consecutive_fails=key_max_fails)
                    # A clean completion decays the deployment's failure streak
                    # so a healthy high-volume deployment is not cooled from
                    # stale timestamps (AUDIT #93).
                    dep.record_success()
                    if dep.probation:
                        dep.probation = False
                        _proxy("info",
                               f"deployment '{dep.group}/{dep.model_id}'"
                               f" graduated from probation")
                        log.info("healer_probation_graduated", kind="deployment",
                                 provider=dep.provider.name, group=dep.group)
                # bump cycle counters
                if cycle_n > 0:
                    provider_consec[dep.provider.name] = (
                        provider_consec.get(dep.provider.name, 0) + 1)
                    key_consec[(dep.provider.name, key.label)] = (
                        key_consec.get((dep.provider.name, key.label), 0) + 1)
                return result
            except WiwiError as e:
                # The attempt failed before pricing could reconcile the
                # deployment's admission estimate: refund the rpm/tpm slot so
                # one 5xx does not throttle this deployment for the window.
                _refund_deployment_slot(dep, ctx)
                tried_dep_ids.add(id(dep))
                if group_first_err is None:
                    group_first_err = e
                last_err = e
                status = _status_of(e)
                # any error: clear this key/provider's cycle credit so the
                # rotation cadence doesn't shield a flapping key from being
                # re-picked.
                key_consec.pop((dep.provider.name, key.label), None)
                provider_consec.pop(dep.provider.name, None)
                # `status` is the *key-pool* signal, not the HTTP status: an
                # entitlement refusal (FreeTierError/CreditsError → 402/403
                # permission_error) returns None so the key is left alone,
                # but the operator still needs the warn line — it is the only
                # trace of WHY the request failed over when the pool is never
                # touched. The pool charge itself is gated on the same signal:
                # a None means the error says nothing about key health, so
                # feeding it to the pool would be the AUDIT #174 regression.
                if status is not None:
                    await dep.provider.on_result_locked(key, status, e.retry_after,
                                                        failover_mode=failover_mode,
                                                        key_max_consecutive_fails=key_max_fails)
                if e.etype == "permission_error" and e.status in (401, 402, 403):
                    # Account-level refusal (billing/policy): the pool was not
                    # charged, so say so and give the operator the real cause.
                    _proxy("warn",
                           f"{dep.provider.name} refused {dep.group}/{dep.model_id} "
                           f"[{key.label}] — account entitlement ({e.status}): {e.message}")
                elif status is not None:
                    _proxy("warn",
                           f"upstream {status} on {dep.group}/{dep.model_id} "
                           f"[{dep.provider.name}/{key.label}]: {e.message}")
                if status in (408, 500, 502, 503, 504, 529):
                    dep.record_fail(router.settings.allowed_fails,
                                    router.settings.cooldown_time)
                if not e.retryable:
                    # Non-retryable, but context-window errors should still
                    # try context_window_fallbacks (e.g. a model with a larger
                    # context window) before giving up.
                    if e.etype == "context_window_exceeded":
                        for fb in router.fallback_targets(group_name,
                                                          "context_window_fallbacks"):
                            if fb not in seen:
                                seen.add(fb)
                                queue.append(fb)
                        break  # let the while-loop process the fallback queue
                    raise
                fresh = any(d.available and id(d) not in tried_dep_ids for d in deps)
                if not fresh and attempt < router.settings.num_retries:
                    await asyncio.sleep(_RETRY_BACKOFF.delay(attempt, e.retry_after))
            except BaseException:
                # Cancellation (client gone) or an unexpected non-WiwiError
                # failure: refund the admission reservation so an aborted
                # request does not hold the deployment's rpm/tpm slot for the
                # window. A no-op when pricing already settled it, because
                # `release_slot` refunds estimated events only.
                _refund_deployment_slot(dep, ctx)
                raise
        if first_error is None:
            first_error = group_first_err or last_err
        # enqueue fallbacks for this group
        for fb in router.fallback_targets(group_name):
            if fb not in seen:
                seen.add(fb)
                queue.append(fb)
                _proxy("info",
                       f"failing over '{group_name}' to fallback group '{fb}'")
        # Context-window fallbacks: when the failure was a context-window
        # exceeded error, also enqueue groups from context_window_fallbacks
        # (e.g. a model with a larger context window).
        if last_err is not None and last_err.etype == "context_window_exceeded":
            for fb in router.fallback_targets(group_name, "context_window_fallbacks"):
                if fb not in seen:
                    seen.add(fb)
                    queue.append(fb)
                    _proxy("info",
                           f"context-window fallback for '{group_name}' -> '{fb}'")

    raise first_error or WiwiError(503, "service_unavailable", "no deployment could serve request")


def _status_of(e: WiwiError) -> int | None:
    return status_for_key_pool(e)


def _refund_deployment_slot(dep: Deployment, ctx: RequestContext) -> None:
    """Return an admitted-but-unpriced request's slot to *dep*.

    ``pick_deployment`` reserves the deployment's rpm/tpm slot at admission,
    but the request may never reach pricing: ``pick_key`` can find no live key,
    or ``call_one`` can fail before any usage is known. Without the refund a
    single failed attempt would hold the cap for the whole 60s window and
    refuse unrelated requests (the #70/#121 phantom-reservation class, one
    layer up). A request that *was* priced keeps its slot — ``release_slot``
    only refunds estimated reservations.
    """
    with contextlib.suppress(Exception):
        dep.release_slot(getattr(ctx, "request_id", ""))
