"""Admin log-reading routes and the admin SSE event stream.

Extracted verbatim from ``server/app.py``: same paths, same status codes,
same error bodies, same wire shapes. The handlers close over per-app
objects handed in by ``create_app`` (``state`` and ``_err``), so the
router is built once per app rather than at import time — auth is the
module-level ``Depends`` guards (``CurrentUser`` / ``AdminUser`` from
``wiwi/server/dependencies``).

The router declares no ``prefix`` on purpose: four of the five routes share
``/admin/logs``, but the SSE channel is ``/admin/stream``, so the paths are
spelled in full to keep the registered routes identical to the inline ones
(one router, one prefix — a prefix here would move ``/admin/stream``).
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from wiwi.logging_core.events import LogEvent
from wiwi.logging_core.subsystem import encode_sse, public_dict
from wiwi.server.dependencies import AdminUser, CurrentUser, ORJSONResponse


def make_router(state, *, _err):
    """Build the admin logs router bound to the per-app helpers."""
    api = APIRouter(tags=["admin-logs"])

    @api.get("/admin/logs/requests")
    async def admin_request_logs(actor: CurrentUser, limit: int = 10000,
                                 minutes: int = 0, offset: int = 0):
        # Hard ceiling (50k) is a safety net against runaway callers, not a
        # product limit — the Usage page trusts the DB-backed overview for
        # the headline number and only uses this endpoint for the row table.
        limit = max(1, min(limit, 50000))
        # minutes == 0 means all-time (same convention as /admin/stats/*). A
        # negative window would push the cutoff into the future and hide every
        # row; a negative OFFSET is invalid SQL. Clamp both to their no-op.
        minutes = max(0, minutes)
        offset = max(0, offset)
        kids: list[str] | None = None
        if actor.role != "admin":
            kids = [k["id"] for k in await state.auth.list_keys_for_owner(actor.id)]
        sink = state.logs.db_sink
        if sink is not None:
            return ORJSONResponse(
                {"logs": await sink.read_requests(limit, key_ids=kids,
                                                  minutes=minutes, offset=offset)},
                headers={"Cache-Control": "no-store"},
            )
        # Ring fallback: deque is oldest→newest, so reverse to newest-first
        # first, then apply the SAME ts window and offset the DB path uses —
        # the two paths must agree for a given query.
        ring = list(await state.logs.sse.replay("request", 0))
        evs = [e for _, e in ring]
        if kids is not None:
            evs = [e for e in evs if e.key_id in kids]
        newest_first = list(reversed(evs))
        if minutes > 0:
            cutoff = time.time() - minutes * 60
            newest_first = [e for e in newest_first if e.ts >= cutoff]
        page = newest_first[offset:offset + limit]
        return ORJSONResponse(
            {"logs": [public_dict(e) for e in page]},
            headers={"Cache-Control": "no-store"},
        )

    def _playground_metrics(event: LogEvent | dict) -> dict:
        """Project the public request timing/usage contract for Playground.

        ``total_tokens`` follows the OpenAI usage shape used by Playground:
        prompt plus completion. Cached and reasoning values stored on
        ``LogEvent`` are subsets of those wire totals, not additional tokens.
        """
        if isinstance(event, LogEvent):
            values = {
                "request_id": event.request_id,
                "prompt_tokens": event.tok_in,
                "completion_tokens": event.tok_out,
                "tps": event.tps,
                "ttft_ms": event.ttft_ms,
                "latency_ms": event.latency_ms,
                "usage_estimated": bool(event.usage_estimated),
            }
        else:
            values = {
                "request_id": event["request_id"],
                "prompt_tokens": event["tok_in"],
                "completion_tokens": event["tok_out"],
                "tps": event["tps"],
                "ttft_ms": event["ttft_ms"],
                "latency_ms": event["latency_ms"],
                "usage_estimated": bool(event["usage_estimated"]),
            }
        values["total_tokens"] = values["prompt_tokens"] + values["completion_tokens"]
        return values

    @api.get("/admin/logs/requests/{request_id}/metrics")
    async def admin_request_metrics(request: Request, request_id: str, actor: CurrentUser):
        """Return exact, tenant-scoped timing and usage for one request.

        The request id is the correlation key returned on every gateway response.
        Do not substitute a timestamp or newest-row lookup: concurrent Playground
        requests can otherwise receive another turn's metrics. Non-admin callers
        are restricted to request events belonging to one of their virtual keys.
        """
        kids: list[str] | None = None
        if actor.role != "admin":
            kids = [k["id"] for k in await state.auth.list_keys_for_owner(actor.id)]
        sink = state.logs.db_sink
        if sink is not None:
            row = await sink.read_request_by_id(request_id, key_ids=kids)
            if row is not None:
                return ORJSONResponse(
                    _playground_metrics(row), headers={"Cache-Control": "no-store"}
                )
        ring = await state.logs.sse.replay("request", 0)
        for _seq, event in reversed(ring):
            if event.request_id != request_id:
                continue
            if kids is not None and event.key_id not in kids:
                continue
            return ORJSONResponse(
                _playground_metrics(event), headers={"Cache-Control": "no-store"}
            )
        return _err(404, "not_found_error", "request metrics not found", request)

    @api.get("/admin/stream")
    async def admin_stream(request: Request, actor: AdminUser):
        # Both credential forms: the SPA streams this with the master key as a
        # bearer, and an admin logged in by cookie must get the same channel.
        # SSE reconnect ids are client-supplied: a malformed one must fall
        # back to 0 (full replay), not 500 the endpoint before a byte is sent.
        try:
            last_id = int(request.headers.get("last-event-id", "0") or 0)
        except ValueError:
            last_id = 0

        async def gen():
            q = await state.logs.sse.subscribe("request")
            pq = await state.logs.sse.subscribe("proxy")
            combined: asyncio.Queue = asyncio.Queue()
            last_sent = last_id
            # Breaks the keepalive loop during app shutdown so this SSE
            # generator returns instead of blocking uvicorn's graceful
            # shutdown drain (which otherwise hangs at "Waiting for
            # connections to close", forcing a manual ^C on every reload).
            shutdown = state.shutdown_event

            async def forward(src):
                # merger tasks are only ever cancelled while awaiting get(),
                # so no dequeued event can be lost by cancellation
                while True:
                    combined.put_nowait(await src.get())

            fwd_tasks = [asyncio.create_task(forward(q)),
                         asyncio.create_task(forward(pq))]
            # First byte must flow immediately: some ASGI stacks gate header
            # forwarding on the first body chunk, so an idle-tailed SSE stream
            # would otherwise hang its own response start.
            yield b": connected\n\n"
            try:
                for seq, evt in await state.logs.sse.replay("request", last_id):
                    yield encode_sse(seq, evt)
                    last_sent = max(last_sent, seq)
                while True:
                    get_task = asyncio.create_task(combined.get())
                    wait_set: set[asyncio.Future] = {get_task}
                    if shutdown is not None:
                        wait_set.add(asyncio.create_task(shutdown.wait()))
                    done, _pending = await asyncio.wait(
                        wait_set, timeout=15.0, return_when=asyncio.FIRST_COMPLETED)
                    for t in _pending:
                        t.cancel()
                    if not done:
                        # timed out — keepalive; also bail if the client is gone
                        if await request.is_disconnected():
                            break
                        yield b": ping\n\n"  # EventSource ignores comments
                        continue
                    if shutdown is not None and shutdown.is_set():
                        break
                    seq, stream, frame = get_task.result()
                    if stream == "request" and seq <= last_sent:
                        continue  # already replayed above
                    if stream == "request":
                        last_sent = seq
                    # Frames arrive pre-encoded (publish() encodes once per
                    # event); only the ring-replay path encodes here.
                    yield frame
            finally:
                for t in fwd_tasks:
                    t.cancel()
                await state.logs.sse.unsubscribe("request", q)
                await state.logs.sse.unsubscribe("proxy", pq)

        # Plain StreamingResponse: sse-starlette's EventSourceResponse stalls
        # behind BaseHTTPMiddleware on this stack (headers never flush); chat
        # SSE over plain StreamingResponse is proven end-to-end.
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "x-accel-buffering": "no"})

    @api.get("/admin/logs/proxy")
    async def admin_proxy_logs(actor: AdminUser):
        # Ring is oldest→newest; slice newest 500 then reverse to newest-first
        # so the contract matches /admin/logs/requests.
        ring = list(await state.logs.sse.replay("proxy", 0))
        return ORJSONResponse(
            {"logs": [public_dict(e) for _, e in reversed(ring[-500:])]},
            headers={"Cache-Control": "no-store"},
        )

    @api.get("/admin/logs/audit")
    async def admin_audit_logs(actor: AdminUser, limit: int = 200):
        limit = max(1, min(limit, 5000))
        sink = state.logs.db_sink
        logs = (await sink.read_audit(limit) if sink is not None
                else await state.logs.read_audit(limit))
        return ORJSONResponse({"logs": logs}, headers={"Cache-Control": "no-store"})

    return api
