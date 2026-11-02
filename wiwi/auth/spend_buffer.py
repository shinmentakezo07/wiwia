"""Batched spend settlement for virtual keys.

Every virtual-key request used to issue two DB writes on its success path:
``release_budget_reservation`` (give back the admission-time reservation) and
``update_spend`` (record the real charge). At any real request rate that is two
writes per request against a single SQLite file, or a constant write load on
Postgres.

The admission gate does not need either write to be synchronous: the cap is
enforced by the reservation taken in ``reserve_budget`` at admission, and the
cached ``AuthInfo`` carries the live total for this process. So the two writes
are aggregated per key and flushed on a bounded interval.

Crash safety is the reason the reservation is *not* released here. The flush
issues one combined statement::

    UPDATE vkeys
       SET spend_to_date   = spend_to_date + :cost,
           budget_reserved = max(budget_reserved - :reserved, 0),
           updated_at      = :now
     WHERE id = :id

If the process dies before a flush, the reservation is still in the database,
so headroom shrinks (fails closed) until the next startup reclaims it. The
buffered charge itself is lost in that window: the flush interval, the
per-key dollar cap and the pending-request cap bound the exposure, and a key
near its budget is flushed synchronously. A deployment that cannot accept
that bounded loss should leave batching off.

Multi-replica note: each process buffers independently, so unflushed spend is
invisible to a sibling for up to one flush interval. The reservation remains
synchronous and DB-visible, so the hard gate still holds; the residual window
is ``interval * aggregate spend rate``. Keys close to their cap are flushed
synchronously to keep that window small.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

import structlog

from wiwi.auth.service import AuthInfo, AuthService

log = structlog.get_logger("wiwi.spend")


@dataclass
class _Pending:
    cost: float = 0.0
    reserved: float = 0.0
    requests: int = 0
    first_at: float = field(default_factory=time.monotonic)


class SpendBuffer:
    """Aggregate virtual-key charges and flush them in one statement per key."""

    def __init__(self, auth: AuthService, *,
                 interval_s: float = 0.25,
                 max_pending: int = 1000,
                 max_dollars_per_key: float = 1.0,
                 sync_near_cap: bool = True) -> None:
        self.auth = auth
        self.enabled = True
        self.interval_s = max(0.01, interval_s)
        self.max_pending = max(1, max_pending)
        self.max_dollars_per_key = max(0.0, max_dollars_per_key)
        self.sync_near_cap = sync_near_cap
        self._pending: dict[str, _Pending] = {}
        self._pending_requests = 0
        self._flush_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.flushes = 0
        self.flush_failures = 0
        self.charges = 0

    # -- accounting ----------------------------------------------------------
    def charge(self, info: AuthInfo, cost: float, reserved: float) -> bool:
        """Record *cost* for *info*'s key, returning the cap decision.

        ``False`` means the charge crossed ``max_budget`` — the caller still
        answers 402 for this request, exactly as the synchronous
        ``update_spend`` conditional did. The money is recorded either way:
        the upstream already billed it.
        """
        if info.key_id == "master":
            return True
        # A zero-cost settle can still carry a reservation to release (an
        # unpriced model, a fully cached prompt). Do not drop it.
        if cost <= 0 and reserved <= 0:
            return True
        recorded = (info.max_budget is None
                    or info.spend_to_date + cost <= info.max_budget)
        # Keep this process's gate exact with zero lag: admission reads
        # ``spend_to_date + budget_reserved`` from the cached object.
        info.spend_to_date += cost
        if reserved > 0:
            info.budget_reserved = max(0.0, info.budget_reserved - reserved)
        p = self._pending.get(info.key_id)
        if p is None:
            p = self._pending[info.key_id] = _Pending()
        p.cost += cost
        p.reserved += max(0.0, reserved)
        p.requests += 1
        self._pending_requests += 1
        self.charges += 1
        # A key near its cap must not accumulate a cross-replica blind spot;
        # flush it synchronously. ``record_spend`` is async, so the caller
        # schedules this through :meth:`maybe_flush_near_cap` instead of
        # blocking here.
        return recorded

    def should_flush_now(self, info: AuthInfo, cost: float) -> bool:
        # Near the cap: bound the cross-replica blind spot for a key that is
        # about to be refused anyway.
        if self.sync_near_cap and info.max_budget is not None:
            remaining = info.max_budget - info.spend_to_date
            if remaining <= max(cost, 0.0) * 2:
                return True
        # Memory/staleness safety valves: a single key's batch or the total
        # number of buffered charges must not grow without bound.
        if self.max_dollars_per_key > 0:
            p = self._pending.get(info.key_id)
            if p is not None and p.cost >= self.max_dollars_per_key:
                return True
        return self._pending_requests >= self.max_pending

    async def flush(self, key_id: str | None = None) -> None:
        """Flush pending charges. A failure keeps them for the next attempt."""
        async with self._flush_lock:
            if key_id is not None:
                p = self._pending.pop(key_id, None)
                batch = {key_id: p} if p is not None else {}
            else:
                batch, self._pending = self._pending, {}
            if not batch:
                return
            failed: dict[str, _Pending] = {}
            for kid, p in batch.items():
                try:
                    await self.auth.settle_spend(kid, p.cost, p.reserved)
                except Exception as e:  # noqa: BLE001 — retry on the next tick
                    self.flush_failures += 1
                    failed[kid] = p
                    log.error("spend_flush_failed", key_id=kid,
                              cost=p.cost, error=type(e).__name__,
                              detail=str(e))
            if failed:
                # Merge back rather than overwrite: charges that arrived
                # during the await must survive.
                for kid, p in failed.items():
                    cur = self._pending.get(kid)
                    if cur is None:
                        self._pending[kid] = p
                    else:
                        cur.cost += p.cost
                        cur.reserved += p.reserved
                        cur.requests += p.requests
            else:
                self.flushes += 1
            # Charges that arrived during the awaits are already counted;
            # recomputing here is O(active keys) once per flush.
            self._pending_requests = sum(
                p.requests for p in self._pending.values())

    async def maybe_flush_near_cap(self, info: AuthInfo, cost: float) -> None:
        if self.should_flush_now(info, cost):
            await self.flush(info.key_id)

    @property
    def pending_dollars(self) -> float:
        return sum(p.cost for p in self._pending.values())

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="wiwi-spend-flush")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except TimeoutError:
                pass
            with contextlib.suppress(Exception):
                await self.flush()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=5.0)
            self._task = None
        # Final best-effort flush before the engine is disposed.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.flush(), timeout=5.0)
