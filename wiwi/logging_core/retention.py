"""Background retention/cap sweeper for the request log.

``request_logs`` grows without bound on a busy gateway. Two knobs bound it:
``log_retention_days`` (age) and ``log_max_rows`` (count). Both roll the
doomed rows into ``request_rollups`` before deleting them, so the dashboard's
totals, token counts, cost and percentiles stay complete — only per-request
detail is dropped.

The sweep used to run once, at startup, which meant a long-running server
never pruned at all. This runs it on an interval instead.

One instance per process. ``start()`` is idempotent and ``stop()`` is safe to
call when never started, matching the other ``*_auto_refresh`` helpers.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from wiwi.server.app import AppState

log = structlog.get_logger("wiwi.log_retention")

# The first sweep waits a little so startup is not competing with the DB for
# the initial burst of dashboard queries.
FIRST_SWEEP_DELAY_S = 30.0


class LogRetention:
    """Periodic rollup+prune of ``request_logs``."""

    def __init__(self, state: AppState) -> None:
        self._state = state
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="log-retention")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # expected: we just cancelled it
            except Exception as e:  # noqa: BLE001 — teardown must not raise
                log.warning("log_retention_stop_failed", error=str(e))
            self._task = None

    async def _run(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=FIRST_SWEEP_DELAY_S)
            return  # stopped during the initial delay
        except TimeoutError:
            pass
        while not self._stop.is_set():
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — a sweep must never kill the task
                log.warning("log_retention_sweep_failed", error=str(e))
            interval = self._state.config.wiwi_settings.log_prune_interval_s
            if interval <= 0:
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                continue

    async def sweep(self) -> None:
        """Run one retention pass: age prune, then the row cap.

        Order matters. Age first removes rows that are old regardless of
        count; the cap then trims to the newest ``log_max_rows`` of whatever
        remains. Running the cap first would roll up and delete rows the age
        pass was about to remove anyway — same result, but the cap's OFFSET
        scan would be over a larger table.

        The ``audit_logs`` table is swept too, and with a cap of its own: it
        was written on every admin mutation and never pruned at all, so it
        grew without bound (AUDIT #353).

        The whole pass runs under a **cross-process** lock. Two gateway
        processes pointed at the same database both swept it, and each
        rolled the same rows up — so the rollup counted every request
        twice and the dashboard's totals were inflated (AUDIT #354). The
        lock is taken on the DATABASE, not in this process, so it is
        effective across processes; a process that cannot take it skips the
        sweep rather than double-counting.
        """
        sink = self._state.logs.db_sink
        if sink is None:
            return
        ws = self._state.config.wiwi_settings

        t0 = time.monotonic()
        async with self._sweep_lock(sink):
            by_age = await sink.prune_old_requests(ws.log_retention_days)
            by_cap = await sink.enforce_log_cap(ws.log_max_rows)
            audit_age = await sink.prune_old_audit(ws.log_retention_days)
            audit_cap = await sink.enforce_audit_cap(ws.log_max_rows)
        if by_age or by_cap or audit_age or audit_cap:
            log.info("request_logs_pruned", by_age=by_age, by_cap=by_cap,
                     audit_by_age=audit_age, audit_by_cap=audit_cap,
                     took_ms=int((time.monotonic() - t0) * 1000))

    @contextlib.asynccontextmanager
    async def _sweep_lock(self, sink):
        """Hold the database-level sweep lock for the duration of the pass.

        Implemented as a row in a tiny ``sweep_locks`` table: a Postgres
        ``pg_advisory_lock`` has no SQLite equivalent, and a file lock does
        not work for a Postgres-backed deployment with several hosts. One
        mechanism that works on both backends is worth the extra table.

        The lock is released by DELETING the row, so a crashed process
        leaves a stale row rather than a held lock; the stale row is
        reclaimed by its age (see ``db_sink._STALE_LOCK_S``).
        """
        try:
            await sink.acquire_sweep_lock()
        except Exception as e:  # noqa: BLE001 — a lock failure must not kill the task
            log.warning("log_retention_lock_failed", error=str(e)[:200])
            yield False
            return
        try:
            yield True
        finally:
            with contextlib.suppress(Exception):
                await sink.release_sweep_lock()
