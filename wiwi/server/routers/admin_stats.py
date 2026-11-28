"""Admin stats routes: usage overview and timeseries buckets.

Extracted verbatim from ``server/app.py``: same paths, same status codes,
same error bodies, same minutes/window contract (the ring and the DB must
answer one request identically — AUDIT_REPORT M12). The handlers close
over per-app objects handed in by ``create_app`` (``state`` and
``_err``), so the router is built once per app rather than at import
time. Authentication is a module-level dependency: each handler
declares ``actor: CurrentUser`` from ``wiwi/server/dependencies``,
the same convention as ``admin_keys.py``.

Both routes share ``/admin/stats``, so the router carries the prefix and
the handler paths are spelled short; the registered routes are identical
to the inline ones.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from wiwi.logging_core.events import LogEvent
from wiwi.server import stats as stats_mod
from wiwi.server.dependencies import CurrentUser, ORJSONResponse


def make_router(state, *, _err):
    """Build the admin stats router bound to the per-app helpers."""
    api = APIRouter(prefix="/admin/stats", tags=["admin-stats"])

    async def _request_events() -> list[LogEvent]:
        return [e for _, e in await state.logs.sse.replay("request", 0)]

    @api.get("/overview")
    async def admin_stats_overview(actor: CurrentUser, minutes: int = 60):
        minutes = max(0, min(minutes, 43200))
        kids: list[str] | None = None
        if actor.role != "admin":
            kids = [k["id"] for k in await state.auth.list_keys_for_owner(actor.id)]
        sink = state.logs.db_sink
        # Every window reads from the DB when a sink exists: the in-memory ring
        # starts empty after a restart, so routing small windows (1h/6h/24h)
        # through the ring made usage stats silently reset on every redeploy.
        if sink is not None:
            return ORJSONResponse(await sink.read_overview(minutes, key_ids=kids))
        evs = await _request_events()
        if kids is not None:
            evs = [e for e in evs if e.key_id in kids]
        # minutes=0 is all-time in both the ring and the DB: stats.overview treats it as
        # "no cutoff" exactly like DBSink.read_overview. Rewriting it to 1440
        # here made the ring answer a different question than the DB for the
        # same request (AUDIT_REPORT M12).
        return ORJSONResponse(stats_mod.overview(evs, minutes))

    @api.get("/timeseries")
    async def admin_stats_timeseries(request: Request, actor: CurrentUser,
                                     bucket: str = "minute",
                                     metric: str = "tokens", minutes: int = 60):
        try:
            minutes = max(0, min(minutes, 43200))
            kids: list[str] | None = None
            if actor.role != "admin":
                kids = [k["id"] for k in await state.auth.list_keys_for_owner(actor.id)]
            sink = state.logs.db_sink
            # Same as overview: DB for every window so 1h/6h/24h buckets
            # survive a restart (the ring does not).
            if sink is not None:
                bs = stats_mod.bucket_size_for(minutes)
                return ORJSONResponse(
                    await sink.read_timeseries(bs, metric, minutes, key_ids=kids))
            evs = await _request_events()
            if kids is not None:
                evs = [e for e in evs if e.key_id in kids]
            # Same minutes contract as the DB branch above: stats.timeseries
            # derives its width from bucket_size_for and treats 0 as all-time,
            # so the ring and the DB answer one request identically (AUDIT_REPORT M12).
            return ORJSONResponse(
                stats_mod.timeseries(evs, bucket, metric, minutes))
        except ValueError as e:
            return _err(400, "invalid_request_error", str(e), request)

    return api
