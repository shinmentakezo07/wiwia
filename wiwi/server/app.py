"""FastAPI app factory: middleware chain, the three surfaces, admin API, health."""

from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

import orjson

from wiwi.auth.service import AuthService
from wiwi.config import WiwiConfig
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway, build_log_event
from wiwi.cost.pricing import CostEngine
from wiwi.logging_core.subsystem import LoggingSubsystem, encode_sse, public_dict
from wiwi.ratelimit.memory import RateLimiter
from wiwi.router.router import Router
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp


class AppState:
    def __init__(self, config: WiwiConfig):
        self.config = config
        self.router = Router(config)
        self.cost = CostEngine()
        self.logs = LoggingSubsystem()
        self.limiter = RateLimiter()
        self.engine = sa.create_engine("sqlite:///:memory:")  # replaced in init_db
        self.auth: AuthService | None = None
        self.gateways: dict[str, Gateway] = {}

    async def init_db(self) -> None:
        import sqlalchemy.ext.asyncio as saa
        url = self.config.general_settings.database_url or "sqlite:///wiwi.db"
        aengine = saa.create_async_engine(url)
        self.auth = AuthService(aengine, self.config.general_settings.master_key)
        await self.auth.startup()
        self.gateways = {
            "chat": Gateway(self.router, self.cost, "chat"),
            "embeddings": Gateway(self.router, self.cost, "embeddings"),
        }

    async def shutdown(self) -> None:
        for g in self.gateways.values():
            await g.aclose()
        await self.logs.stop()


def create_app(config: WiwiConfig) -> FastAPI:
    state = AppState(config)
    app = FastAPI(title="wiwi", version="0.1.0", docs_url="/docs")
    app.state.wiwi = state

    @app.on_event("startup")
    async def _startup() -> None:
        await state.init_db()
        await state.logs.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await state.shutdown()

    # -- helpers ---------------------------------------------------------------
    def bearer(request: Request) -> str:
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer "):
            return authz[7:].strip()
        xkey = request.headers.get("x-api-key")  # Claude Code on /v1/messages
        return xkey.strip() if xkey else ""

    def is_admin(request: Request) -> bool:
        return bearer(request) == config.general_settings.master_key and bool(
            config.general_settings.master_key)

    async def authenticate(request: Request, model: str):
        if state.auth is None:
            return None, _err(500, "api_error", "gateway not initialized", request)
        token = bearer(request)
        if not token:
            return None, _err(401, "authentication_error",
                              "missing API key", request)
        info = await state.auth.authenticate(token)
        if info is None:
            return None, _err(401, "authentication_error", "invalid API key", request)
        if info.disabled or (info.expires_at and time.time() > info.expires_at):
            return None, _err(401, "authentication_error", "key disabled or expired",
                              request)
        if info.over_budget:
            return None, _err(429, "budget_exceeded",
                              f"budget exhausted ({info.spend_to_date:.4f}"
                              f"/{info.max_budget})", request)
        if info.models and model not in info.models:
            return None, _err(403, "permission_error",
                              f"key not allowed for model '{model}'", request)
        allowed, retry_after = state.limiter.check(info.key_id, info.rpm, info.tpm)
        if not allowed:
            resp = _err(429, "rate_limit_error",
                        f"rate limit exceeded, retry in {retry_after}s", request)
            resp.headers["Retry-After"] = str(retry_after)
            return None, resp
        return info, None

    def _err(status: int, etype: str, message: str,
             request: Request, surface: str = "chat") -> JSONResponse:
        rid = getattr(request.state, "request_id", "")
        if surface == "messages":
            body = am.error_body(status, etype, message)
        else:
            body = oc.error_body(status, etype, message)
        return JSONResponse(body, status_code=status,
                            headers={"x-wiwi-request-id": rid})

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        import uuid
        rid = uuid.uuid4().hex[:16]
        request.state.request_id = rid
        t0 = time.monotonic()
        response = await call_next(request)
        response.headers["x-wiwi-request-id"] = rid
        response.headers["x-wiwi-latency-ms"] = f"{(time.monotonic()-t0)*1000:.1f}"
        return response

    # -- shared execution ------------------------------------------------------
    async def run_chat_like(request: Request, surface: str, body: dict[str, Any],
                            codec_decode, codec_encode_response, error_body_fn):
        state_ = app.state.wiwi
        try:
            ir_req = codec_decode(body)
        except (oc.DialectError, ValueError) as e:
            return _err(400, "invalid_request_error", str(e), request, surface)
        info, err_resp = await authenticate(request, ir_req.model)
        if err_resp:
            return err_resp
        group, deps = state_.router.resolve_group(ir_req.model)
        if group is None:
            return _err(404, "not_found_error",
                        f"model '{ir_req.model}' not found", request, surface)
        ctx = RequestContext(surface=surface, ir_req=ir_req, auth=info, group=group)
        gateway = state_.gateways["chat"]
        try:
            if ir_req.stream:
                encoder = _encoder_for(surface, ir_req.model, ctx.request_id)
                return StreamingResponse(
                    _stream_response(state_, ctx, gateway, encoder, surface),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "x-wiwi-request-id": ctx.request_id})
            turn = await gateway.complete(ctx)
            ctx.status = 200
            payload = codec_encode_response(ctx, turn, ir_req.model, ctx.request_id)
            state_.logs.log_request(build_log_event(ctx))
            if info and info.key_type != "master":
                await state_.auth.update_spend(info.key_id, ctx.cost)
            return JSONResponse(payload, headers={"x-wiwi-request-id": ctx.request_id})
        except Exception as e:  # noqa: BLE001
            from wiwi.providers.base import WiwiError
            if isinstance(e, WiwiError):
                ctx.status = e.status
                ctx.error = e
                state_.logs.log_request(build_log_event(ctx))
                return _err(e.status, e.etype, e.message, request, surface)
            ctx.status = 500
            state_.logs.log_proxy("error", f"internal error: {e}", ctx.request_id)
            state_.logs.log_request(build_log_event(ctx))
            return _err(500, "api_error", "internal gateway error", request, surface)

    def _encoder_for(surface: str, model: str, req_id: str):
        if surface == "chat":
            return oc.ChatStreamEncoder(model, req_id), "chat"
        if surface == "messages":
            return am.AnthropicStreamEncoder(model, req_id), "anthropic"
        return orp.ResponsesStreamEncoder(model, req_id), "responses"

    async def _stream_response(state_, ctx, gateway, encoder_pair, surface):
        from wiwi.streaming import deltas as dl
        encoder, style = encoder_pair
        usage_final = None
        stop = "stop"
        try:
            async for d in gateway.stream(ctx):
                chunk = encoder.feed(d)
                if isinstance(d, dl.UsageFinal):
                    usage_final = d
                if isinstance(d, dl.Finish):
                    stop = d.stop_reason
                if chunk:
                    yield chunk
            # final frame with usage (style-specific)
            if style == "chat":
                yield encoder.final_frame(usage_final, stop)
                yield b"data: [DONE]\n\n"
            elif style == "anthropic":
                yield encoder.final_frame()
                yield b"event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
            else:
                yield encoder._completed()
        finally:
            ctx.status = 200
            state_.logs.log_request(build_log_event(ctx))
            if ctx.usage and ctx.auth and ctx.auth.key_type != "master":
                await state_.auth.update_spend(ctx.auth.key_id, ctx.cost)

    # -- surfaces ---------------------------------------------------------------
    @app.post("/v1/chat/completions")
    @app.post("/v1/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        return await run_chat_like(request, "chat", body, oc.decode_request,
                                   oc.encode_response, oc.error_body)

    @app.post("/v1/responses")
    async def responses_api(request: Request):
        body = await request.json()
        return await run_chat_like(request, "responses", body, orp.decode_request,
                                   orp.encode_response, orp.error_body)

    @app.post("/v1/messages")
    async def messages_api(request: Request):
        body = await request.json()
        return await run_chat_like(request, "messages", body, am.decode_request,
                                   am.encode_response, am.error_body)

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request):
        body = await request.json()
        try:
            ir_req = am.decode_request(body)
        except (oc.DialectError, ValueError) as e:
            return _err(400, "invalid_request_error", str(e), request, "messages")
        from wiwi.ir import types as _ir
        total = 0
        for m in ir_req.messages:
            for p in m.parts:
                if isinstance(p, _ir.TextPart):
                    total += len(p.text) // 4 + 1
        return JSONResponse({"input_tokens": max(1, total)})

    @app.get("/v1/models")
    async def list_models(request: Request):
        data = []
        for name in sorted(app.state.wiwi.router.groups.keys()):
            data.append({"id": name, "object": "model", "owned_by": "wiwi"})
        return JSONResponse({"object": "list", "data": data})

    @app.get("/health")
    async def health():
        return {"status": "ok", "groups": len(app.state.wiwi.router.groups),
                "providers": len(app.state.wiwi.router.providers)}

    # -- admin -------------------------------------------------------------------
    @app.post("/admin/keys/generate")
    async def admin_generate_key(request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        body = await request.json()
        plaintext, kid = await state.auth.create_key(
            alias=body.get("name") or body.get("alias") or "",
            models=body.get("models"), max_budget=body.get("max_budget"),
            rpm=body.get("rpm"), tpm=body.get("tpm"),
            custom_key=body.get("custom_key"))
        await state.logs.log_audit(actor="master", action="key.generate", target=kid)
        return JSONResponse({"key": plaintext, "id": kid,
                             "note": "store this key now; it is not shown again"})

    @app.get("/admin/keys")
    async def admin_list_keys(request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        return JSONResponse({"keys": await state.auth.list_keys()})

    @app.delete("/admin/keys/{key_id}")
    async def admin_delete_key(key_id: str, request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        ok = await state.auth.delete_key(key_id)
        await state.logs.log_audit(actor="master", action="key.delete", target=key_id)
        return JSONResponse({"deleted": ok})

    @app.get("/admin/logs/requests")
    async def admin_request_logs(request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        # MVP: served from SSE ring; DB persistence lands with the batch writer sink
        ring = list(state.logs.sse.replay("request", 0))
        return JSONResponse({"logs": [public_dict(e) for _, e in ring[-500:]]})

    @app.get("/admin/stream")
    async def admin_stream(request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        last_id = int(request.headers.get("last-event-id", "0") or 0)

        async def gen():
            q = await state.logs.sse.subscribe("request")
            pq = await state.logs.sse.subscribe("proxy")
            try:
                for seq, evt in state.logs.sse.replay("request", last_id):
                    yield encode_sse(seq, evt)
                while True:
                    got = await q.get() if False else None
                    done = {"q": q, "pq": pq}
                    import asyncio as _aio
                    qq_task = _aio.create_task(q.get())
                    pq_task = _aio.create_task(pq.get())
                    done_t, pending = await _aio.wait(
                        [qq_task, pq_task], return_when=_aio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    for t in done_t:
                        seq, evt = t.result()
                        yield encode_sse(seq, evt)
            finally:
                await state.logs.sse.unsubscribe("request", q)
                await state.logs.sse.unsubscribe("proxy", pq)

        return EventSourceResponse(gen())

    return app
