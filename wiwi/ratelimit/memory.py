"""Rate limiter: sliding-window rpm/tpm counters, global + per-key scopes.

Memory backend is exact for a single instance. Redis parity interface exists
(redis_url config) but memory is the MVP backend.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

# Minimum seconds between full sweep passes of the window map. The sweep only
# reclaims empty windows, so running it less often costs a little memory for
# idle keys and nothing in correctness: admission prunes the windows it
# actually consults. Bounding the *frequency* is what keeps the cap from
# turning every request into an O(n) scan under the lock (round 66).
_SWEEP_INTERVAL_S = 5.0


@dataclass
class _Event:
    ts: float
    tokens: int          # 1 for rpm events
    estimated: bool = False  # True until record_tokens() confirms actual usage
    request_id: str = ""  # tags the reservation so record_tokens can match it

@dataclass
class _Window:
    # rpm windows store request events; tpm windows store token events.
    events: deque = field(default_factory=deque)
    is_token: bool = False
    # Running token total, kept in sync by append/prune so count() is O(1).
    # The naive `sum(e.tokens for e in events)` rescanned the whole 60s window
    # on every one of up to 4 admission checks per request.
    total: int = 0

    def count(self) -> int:
        return self.total


class RateLimiter:
    def __init__(self, global_rpm: int | None = None, global_tpm: int | None = None):
        self.global_rpm = global_rpm
        self.global_tpm = global_tpm
        self._windows: dict[str, _Window] = {}
        # Serializes check() and record_tokens() so concurrent callers cannot
        # both pass the limit at the same instant, and so a reservation can
        # be replaced (not appended alongside) when the actual usage arrives.
        self._lock = asyncio.Lock()
        # Per-key windows are created on first use and nothing removes them,
        # so a key deleted from the DB would leave its windows behind forever.
        # Sweep periodically instead of paying to detect deletion.
        self._max_windows = 10_000
        # Minimum spacing between sweep passes once the map is over the cap.
        # Sweeping on every admission turned the cap into a per-request O(n)
        # scan under the lock (round 66).
        self._last_sweep = 0.0

    def _sweep_windows(self, now: float) -> None:
        """Reclaim windows that have gone empty, at most once per window period.

        Only *empty* windows are dropped. A window still holding events belongs
        to a key with traffic inside the last 60 s, and evicting a live window
        would reset that key's count and admit it over its cap — a worse bug
        than the one this sweep exists to prevent.

        Note the bound is therefore "concurrent active keys *while traffic
        continues*", not a hard cap: the sweep only runs from ``check``, so a
        deployment that goes quiet with a large historical key set keeps the
        map at its high-water mark until the next admission. That is strictly
        better than the pre-fix behaviour (same entry condition, plus an O(n)
        scan per request) and costs one idle window per key seen, which the
        per-window ``_prune`` keeps correct without needing the sweep at all.

        What *was* wrong: the sweep scanned every window on **every** admission
        once the map passed ``_max_windows``, and it ran inside the limiter's
        lock. With enough distinct keys that serializes all concurrent
        admissions behind an O(n) scan — measured at 6.3 ms per check with
        22 000 windows, against 0.005 ms idle (round 66). Throttling the sweep
        to one pass per period makes the cost amortized instead of per-request;
        the events it would have pruned are still excluded from the count by
        the per-window ``_prune`` on the admission path, so correctness does not
        depend on the sweep running often.
        """
        if len(self._windows) < self._max_windows:
            return
        if now - self._last_sweep < _SWEEP_INTERVAL_S:
            return
        self._last_sweep = now
        for scope, w in list(self._windows.items()):
            self._prune(w, now)
            if not w.events:
                del self._windows[scope]

    def _window(self, scope: str, is_token: bool = False) -> _Window:
        w = self._windows.get(scope)
        if w is None:
            w = self._windows[scope] = _Window(is_token=is_token)
        return w

    @staticmethod
    def _prune(w: _Window, now: float) -> None:
        cutoff = now - 60.0
        while w.events and w.events[0].ts < cutoff:
            w.total -= w.events.popleft().tokens

    async def check(self, key_id: str, key_rpm: int | None = None,
                    key_tpm: int | None = None, est_tokens: int = 0,
                    request_id: str = "") -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds).

        Atomic against itself and against :meth:`record_tokens` so two
        concurrent callers cannot both pass when only one slot is free.
        """
        async with self._lock:
            now = time.monotonic()
            self._sweep_windows(now)
            checks: list[tuple[_Window, int]] = []
            if self.global_rpm:
                checks.append((self._window("global:rpm"), self.global_rpm))
            if self.global_tpm:
                checks.append((self._window("global:tpm", is_token=True), self.global_tpm))
            # Key-scoped limits are tested with ``is not None``, not for
            # truthiness. A stored ``rpm: 0`` used to skip its scope entirely,
            # so an operator parking a key by zeroing it silently got an
            # *unlimited* key — the opposite of the intent (AUDIT #163).
            # ``_coerce_limit`` now rejects 0 at the admin boundary, so the
            # only way to see one here is a row written before that; reading it
            # as "no requests allowed" fails closed, and the ``limit <= 0``
            # guard below already implements exactly that refusal.
            #
            # The global scopes keep the truthy test: ``global_rpm``/
            # ``global_tpm`` are config-level knobs where 0 has always meant
            # "cap off", and they are not settable through the key API.
            if key_rpm is not None:
                checks.append((self._window(f"{key_id}:rpm"), key_rpm))
            if key_tpm is not None:
                checks.append((self._window(f"{key_id}:tpm", is_token=True), key_tpm))

            for w, limit in checks:
                # Guard against a nonsensical limit reaching us from stored key
                # config: a negative or zero limit must reject cleanly rather
                # than read w.events[0] on an empty window (IndexError -> HTTP
                # 500 on every request using that key).
                if limit is None or limit <= 0:
                    return False, 60
                self._prune(w, now)
                # prospective admission: the incoming request's cost must fit
                cost = est_tokens if w.is_token else 1
                if w.count() + cost > limit:
                    if w.events:
                        retry_after = int(max(1.0, 60.0 - (now - w.events[0].ts))) + 1
                    else:
                        # Empty window after pruning: the request itself exceeds
                        # the limit (e.g. est_tokens > key_tpm), so nothing ages
                        # out sooner — the retry horizon is the full window.
                        retry_after = 60
                    return False, min(retry_after, 60)
            # reserve: one event per rpm scope; an estimated-cost event per tpm scope
            for w, limit in checks:
                if w.is_token:
                    w.events.append(_Event(ts=now, tokens=max(0, est_tokens),
                                           estimated=True, request_id=request_id))
                else:
                    # RPM events carry the request id too. Without it a refund
                    # could not tell its own slot from another request's, so
                    # ``release`` popped whatever was newest — freeing a slot
                    # still held by an in-flight request (round 66).
                    #
                    # They are also flagged ``estimated``: an rpm slot is a
                    # reservation for a request still in flight, never
                    # reconciled by ``record_tokens``, and ``release`` refunds
                    # only estimated events so that confirmed usage is never
                    # deleted.
                    w.events.append(_Event(ts=now, tokens=1, estimated=True,
                                           request_id=request_id))
                w.total += w.events[-1].tokens
            return True, 0

    async def record_tokens(self, key_id: str, tokens: int,
                            request_id: str = "") -> None:
        """Post-request confirmation: replace the newest estimated reservation
        with the actual usage (prevents double-counting estimate + actual).

        When *request_id* is provided, the reservation tagged with that id at
        check time is replaced — so concurrent same-key requests do not
        misattribute actual usage to the wrong reservation. When no id is
        given or the tagged reservation is not found, fall back to replacing
        the newest estimated reservation (backward-compatible behaviour).

        Atomic against :meth:`check` so the replacement is exclusive with
        the next admission decision.
        """
        async with self._lock:
            now = time.monotonic()
            actual = max(0, tokens)
            for scope in ("global:tpm", f"{key_id}:tpm"):
                w = self._windows.get(scope)
                if w is None or not w.is_token:
                    continue
                self._prune(w, now)
                # With an id: match *only* that id (AUDIT #32). The lenient
                # newest-estimated fallback was written for id-less callers,
                # but on the id-carrying path it fires for a different reason —
                # the caller's own reservation aged out of the 60 s window
                # while the request was still streaming. Adopting another
                # request's estimate then overwrites a *live* reservation and
                # flips it to confirmed, which both under-counts the window
                # (admitting past the cap) and makes the rightful owner
                # unrefundable, since release() never removes confirmed usage.
                # When the id's own reservation is gone, append the actual
                # usage instead: the window must still account for tokens the
                # provider really billed. Mirrors Deployment.settle_tokens,
                # which resolves id -> any event for that id -> append and
                # never adopts a different request's estimate.
                target = self._find_event(w, request_id) if request_id else None
                if target is None and not request_id:
                    target = self._newest_estimated(w)
                if target is not None:
                    w.total += actual - target.tokens
                    target.tokens = actual
                    target.estimated = False
                else:
                    # Tagged with the id when we have one, so a repeated
                    # reconcile of the same request adjusts this event rather
                    # than appending a second copy.
                    w.events.append(_Event(ts=now, tokens=actual,
                                           request_id=request_id))
                    w.total += actual

    async def release(self, key_id: str, request_id: str = "") -> None:
        """Refund a reservation whose upstream call never consumed tokens.

        A failed request (5xx, 429, all-keys-cooling, any WiwiError) reserves
        an *estimated* TPM/RPM slot at admission but never calls
        :meth:`record_tokens`. Without a refund that phantom reservation
        throttles unrelated requests for the rest of the window (AUDIT #70).

        The reservation tagged with *request_id* is removed from both the
        key's token window and the global token window, and its RPM event is
        removed from both the key's window and the global one.

        Refunds are identity-matched and never fall back to "newest" when an id
        is supplied: every other event in the window belongs to a request that
        is still in flight, so popping one would free a slot that was never
        given up and admit a request past the cap (round 66). The id-less
        fallback is kept only for callers that predate request ids, where at
        most one request per key can be in flight.
        """
        async with self._lock:
            now = time.monotonic()
            # Both scopes are refunded with the same strict, identity-matched
            # finder. Admission takes a slot in the key window AND the global
            # one, so both must be refunded — leaking the global slot burned
            # one of every `global_rpm` slots for the rest of the window
            # (AUDIT #121, residual of the #70 fix).
            #
            # The refund must identify the request's OWN slot. Popping whatever
            # was newest freed a slot belonging to a *different* request that
            # was still in flight, whenever the releasing request's own event
            # had already aged out of the 60 s window — a long stream, or any
            # request admitted more than a minute ago. The window total then
            # under-counted and admission let requests past the configured cap
            # (round 66).
            #
            # An empty key_id must not build the literal scopes ``":rpm"``/
            # ``":tpm"`` — they are real dict keys a later release would touch
            # (round 66). But the GLOBAL windows must still be refunded:
            # ``check("")`` reserves in them, and an early return leaked one of
            # every ``global_rpm``/``global_tpm`` slot for the window (round
            # 70). The key-scoped lookups are skipped when there is no id;
            # ``.get`` never creates windows, so the global loops are safe.
            key_tpm_scope = f"{key_id}:tpm" if key_id else None
            key_rpm_scope = f"{key_id}:rpm" if key_id else None
            for scope in ("global:tpm", key_tpm_scope):
                if scope is None:
                    continue
                w = self._windows.get(scope)
                if w is None or not w.is_token:
                    continue
                self._prune(w, now)
                target = self._find_refund(w, request_id)
                if target is not None:
                    self._drop(w, target)
            for scope in (key_rpm_scope, "global:rpm"):
                if scope is None:
                    continue
                w = self._windows.get(scope)
                if w is None:
                    continue
                self._prune(w, now)
                target = self._find_refund(w, request_id)
                if target is not None:
                    self._drop(w, target)

    @staticmethod
    def _find_event(w: _Window, request_id: str) -> _Event | None:
        """The event belonging to *request_id*, estimated or confirmed.

        Mirrors ``Deployment.settle_tokens``: id -> any event for that id.
        The ``estimated`` flag is deliberately ignored here — ``record_tokens``
        may run twice for one request (e.g. a resume followed by the pump's
        final settle), and the second call must *adjust* the first call's
        already-confirmed event rather than append a second copy of the usage.
        Callers that intend to *remove* capacity must use ``_find_refund``
        instead, which is strict about the estimate flag.
        """
        for e in reversed(w.events):
            if e.request_id == request_id:
                return e
        return None

    @staticmethod
    def _newest_estimated(w: _Window) -> _Event | None:
        """Newest *estimated* event — the fallback for callers that predate
        request ids, where at most one request per key can be in flight.

        Never reached when an id was supplied: on the id-carrying path the
        newest estimate may belong to a different, still-in-flight request,
        and adopting it both under-counts the window and makes the rightful
        owner unrefundable (AUDIT #32).
        """
        for e in reversed(w.events):
            if e.estimated:
                return e
        return None

    @staticmethod
    def _find_refund(w: _Window, request_id: str) -> _Event | None:
        """The *estimated* reservation belonging to *request_id*, or None.

        Strict by design — deliberately **no** "newest event" fallback when an
        id is supplied. A refund *removes* capacity, so attributing it to
        another request's event frees a slot that request is still using and
        admits a new request past the cap (round 66). An unmatched release must
        therefore refund nothing: every other event in the window belongs to a
        request that is still in flight.

        Only ``estimated`` events are refundable. ``release`` is documented as
        safe to call *after* ``_record_tpm_usage``, which flips the matching
        reservation to ``estimated=False``; refunding that would delete usage
        the provider actually billed (AUDIT #48). RPM events are always
        estimated — they are placeholders for a request in flight, never
        reconciled by ``record_tokens``.

        The id-less fallback is retained only for callers that predate request
        ids, where at most one request per key can be in flight.
        """
        if request_id:
            for e in reversed(w.events):
                if e.request_id == request_id and e.estimated:
                    return e
            return None
        for e in reversed(w.events):
            if e.estimated:
                return e
        return None

    @staticmethod
    def _drop(w: _Window, target: _Event) -> None:
        """Remove *target* from *w*, keeping the running total in sync."""
        try:
            w.events.remove(target)
        except ValueError:  # already pruned away
            return
        w.total = max(0, w.total - target.tokens)
