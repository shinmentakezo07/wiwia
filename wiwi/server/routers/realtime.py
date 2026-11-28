"""The realtime WebSocket surface (spec E).

Extracted verbatim from ``server/app.py`` (round 206). Every admission
decision happens BEFORE ``accept()``. Once the client has seen 101 it believes
it has a session, and a refusal after that point can only arrive as a close
frame the client cannot map to a status. Closing before accept makes Starlette
answer with a real HTTP error response, so a bad key is a readable 401 and an
unknown model a readable 404, exactly as on the request/response surfaces.

The relay and the billing tail are ``server/app.py`` module-level functions
(passed in — tests import them from there); the unpriced-model gate and the
TPM recorder are ``create_app`` closures.
"""

from __future__ import annotations

import contextlib
import time
import uuid as _uuid

import orjson
import websockets
from fastapi import APIRouter, WebSocket

from wiwi.core.context import RequestContext
from wiwi.ir import types as ir
from wiwi.providers.registry import get_adapter
from wiwi.router.router import _refund_deployment_slot
from wiwi.wire import openai_chat as oc


def make_router(state, config, *, _model_group_is_unpriced,
                _record_tpm_usage, _realtime_relay, _bill_realtime_session,
                _rt_log):
    """Build the realtime router bound to the per-app helpers."""
    api = APIRouter(prefix="/v1", tags=["realtime"])

    async def _ws_refuse(ws: WebSocket, status: int, etype: str, msg: str,
                         retry_after: float | None = None) -> None:
        """Refuse a websocket before accept, with a readable HTTP status.

        Sends a literal ``websocket.http.response.start``/``.body`` pair rather
        than calling ``ws.close()``. A close issued before accept is reported to
        the client as a *generic* handshake rejection — uvicorn answers 403 for
        any of them, discarding the code — so a bad key and an unknown model
        were indistinguishable to the client. Writing the response by hand is
        what makes a 401 read as a 401, which is the whole reason for refusing
        here rather than after ``accept``.
        """
        body = orjson.dumps(oc.error_body(status, etype, msg))
        headers = [(b"content-type", b"application/json"),
                   (b"content-length", str(len(body)).encode())]
        if retry_after is not None:
            headers.append((b"retry-after",
                            str(int(max(1.0, retry_after))).encode()))
        await ws.send({"type": "websocket.http.response.start",
                       "status": status, "headers": headers})
        await ws.send({"type": "websocket.http.response.body", "body": body})
        state.logs.log_proxy(
            "warn", f"realtime refused {status} {etype}: {msg}")

    @api.websocket("/realtime")
    async def realtime_endpoint(ws: WebSocket):
        rt = config.realtime
        if not rt.enabled:
            await _ws_refuse(ws, 404, "invalid_request_error",
                             "the realtime surface is disabled"
                             " (realtime.enabled=false)")
            return

        token = (ws.headers.get("authorization", "") or "")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        else:
            token = (ws.headers.get("x-api-key") or "").strip()
        if not token and rt.allow_key_in_query:
            token = (ws.query_params.get("key") or "").strip()
        if not token:
            await _ws_refuse(ws, 401, "authentication_error",
                             "missing API key; send it in Authorization or"
                             " x-api-key" + (" (realtime.allow_key_in_query is"
                             " off, so ?key= is not accepted)" if not
                             rt.allow_key_in_query else ""))
            return

        model = (ws.query_params.get("model") or "").strip()
        session_id = (ws.query_params.get("session_id")
                      or ws.headers.get("x-wiwi-session-id") or "").strip()[:128]

        if state.auth is None:
            await _ws_refuse(ws, 500, "api_error", "gateway not initialized")
            return
        info = await state.auth.authenticate(token)
        if info is None or info.disabled or (
                info.expires_at and time.time() > info.expires_at):
            await _ws_refuse(ws, 401, "authentication_error",
                             "invalid API key")
            return
        if info.over_budget:
            await _ws_refuse(ws, 402, "budget_exceeded",
                             f"budget exhausted ({info.spend_to_date:.4f}"
                             f"/{info.max_budget})")
            return
        # The unpriced-model gate of AUDIT #323. It used to live ONLY in the
        # HTTP admission closure ``authenticate()``; this route authenticates
        # its own socket credential and hand-rolled the other three checks
        # (disabled/expires, over_budget, model allowlist) but not this one, so
        # with ``unpriced_model_policy=reject`` a budget-capped key was refused
        # on /v1/chat/completions and admitted over WS. The session then priced
        # at $0, ``apply_spend_trueup`` no-ops on a non-positive amount, and the
        # cap never tripped — the one control that stops unpriced spend was a
        # no-op on the easiest surface to bypass (AUDIT #347).
        #
        # This is a deliberate mirror, not a shared helper: ``authenticate()``
        # renders its verdict as an HTTP error body and this one cannot reuse
        # it. It MUST stay in lockstep with ``authenticate()`` — the guard, the
        # ``model != "*"`` scope and the ``max_budget is not None`` narrowing
        # alike. ``test_the_unpriced_gate_reads_identically_on_both_surfaces``
        # in tests/test_fix_round139.py pins the two verdicts together, and
        # ``test_an_uncapped_key_is_not_refused_by_the_unpriced_gate`` pins the
        # narrowing, so a one-sided change fails instead of drifting.
        if (model and model != "*" and info.max_budget is not None
                and config.general_settings.unpriced_model_policy == "reject"
                and await _model_group_is_unpriced(model)):
            await _ws_refuse(ws, 503, "unpriced_model_error",
                             f"model '{model}' has no pricing entry;"
                             " budget-capped keys are refused until an admin"
                             " prices it"
                             " (general_settings.unpriced_model_policy=reject)")
            return
        if info.models and model and model != "*" and model not in info.models:
            await _ws_refuse(ws, 403, "permission_error",
                             f"key not allowed for model '{model}'")
            return

        group_name, deps = state.router.resolve_group(model)
        if group_name is None or not deps:
            await _ws_refuse(ws, 404, "invalid_request_error",
                             f"unknown model '{model}'; pass ?model=<group>")
            return

        ctx = RequestContext(surface="chat", ir_req=ir.Request(
            model=model, messages=[],
            gen_params=ir.GenParams()),
            auth=info, group=group_name,
            request_id=_uuid.uuid4().hex[:16],
            session_id=session_id or None)

        # The deployment pick reuses pick_deployment wholesale, so cooldowns,
        # probation, rpm/tpm, the concurrency cap, the priority lane and
        # session affinity all apply to realtime sessions exactly as they do to
        # HTTP requests. No separate admission path to keep in sync.
        dep = state.router.pick_deployment(deps, ctx)
        if dep is None:
            shed = ctx.metadata.get("shed_reason") == "inflight"
            await _ws_refuse(
                ws, 503, "server_overloaded" if shed else "service_unavailable",
                (f"all deployments for '{group_name}' are at their concurrency"
                 " limit") if shed else
                (f"no healthy deployment for '{group_name}'"))
            return

        key, _weight = await dep.provider.pick_key(
            probation_weight=state.router.probation_weight)
        if key is None:
            _refund_deployment_slot(dep, ctx)
            await _ws_refuse(ws, 503, "service_unavailable",
                             f"no available key for '{dep.provider.name}'")
            return

        adapter = get_adapter(dep.provider.provider_type)
        url = adapter.realtime_url(dep.provider.base_url)
        if url is None:
            _refund_deployment_slot(dep, ctx)
            await _ws_refuse(ws, 501, "not_implemented",
                             f"provider type '{dep.provider.provider_type}'"
                             " has no realtime surface")
            return

        # Key-level RPM/TPM. ``enforce_rate_limit`` owns the reservation and its
        # only call site was the HTTP path, so a realtime session — the one
        # surface that can hold a deployment slot for an hour — ran entirely
        # unthrottled at the key level; ``pick_deployment`` above enforces only
        # the *per-deployment* caps, and a single uncapped deployment therefore
        # let a key open unlimited concurrent sessions (AUDIT #347).
        #
        # Reserved with est_tokens=0 and reconciled exactly once, after the
        # session, below: the limiter replaces the reservation tagged with
        # ``ctx.request_id`` rather than appending beside it, so however long
        # the session ran its tokens are counted once, never twice. Placed after
        # the deployment/key/url lookups so the only thing between the
        # reservation and the session itself is this one refusal.
        allowed, retry_after = await state.limiter.check(
            info.key_id, info.rpm, info.tpm,
            est_tokens=0, request_id=ctx.request_id)
        if not allowed:
            _refund_deployment_slot(dep, ctx)
            await _ws_refuse(ws, 429, "rate_limit_error",
                             f"rate limit exceeded, retry in {retry_after}s",
                             retry_after=retry_after)
            return

        # From here the session exists upstream; every path must close it.
        dep.inflight += 1  # hold the slot for the whole session, not one turn
        usage: tuple[int, int] | None = None
        status = 200
        stop_reason = "realtime_session"
        try:
            # Accept can fail if the client disconnects during the handshake;
            # keep it inside the slot's finally (AUDIT #363).
            await ws.accept()
            async with websockets.connect(
                    url,
                    additional_headers={
                        "Authorization": f"Bearer {key.secret}",
                        "OpenAI-Beta": "realtime=v1",
                    },
                    open_timeout=10.0,
                    max_size=16 * 1024 * 1024) as upstream:
                usage = await _realtime_relay(ws, upstream, rt)
        except (OSError, websockets.WebSocketException) as exc:
            _rt_log.warning("realtime upstream connection failed",
                            error=str(exc)[:200], model=model)
            usage = None
            # The session was admitted and then never served. That is the case
            # the early return used to swallow whole: no charge, no row, no
            # status. Recorded as a failed session instead (AUDIT #347).
            status = 502
            stop_reason = "realtime_upstream_error"
        finally:
            dep.inflight -= 1
            _refund_deployment_slot(dep, ctx)
            with contextlib.suppress(Exception):
                await ws.close()
        # Unconditional: every session that passed admission gets a row,
        # including the ones with no usage to price.
        await _bill_realtime_session(state, info, ctx, dep, usage,
                                     status=status, stop_reason=stop_reason)
        if status >= 400:
            # Never served, so the reservation is phantom: refund it rather
            # than confirming a zero-token one (the HTTP path's release on a
            # failed request, same shape).
            await state.limiter.release(info.key_id, ctx.request_id)
        else:
            await _record_tpm_usage(info, ctx)

    return api
