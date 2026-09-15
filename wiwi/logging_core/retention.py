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
        """
        sink = self._state.logs.db_sink
        if sink is None:
            return
        ws = self._state.config.wiwi_settings

        t0 = time.monotonic()
        by_age = await sink.prune_old_requests(ws.log_retention_days)
        by_cap = await sink.enforce_log_cap(ws.log_max_rows)
        if by_age or by_cap:
            log.info("request_logs_pruned", by_age=by_age, by_cap=by_cap,
                     took_ms=int((time.monotonic() - t0) * 1000))
