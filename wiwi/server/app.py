"""FastAPI app factory: middleware chain, the three surfaces, admin API, health."""

from __future__ import annotations

import hmac
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import orjson
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from wiwi.auth.service import AuthService
from wiwi.config import WiwiConfig
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway, build_log_event
from wiwi.cost.pricing import CostEngine
from wiwi.logging_core.events import LogEvent
from wiwi.logging_core.subsystem import LoggingSubsystem, encode_sse, public_dict
from wiwi.providers.base import WiwiError
from wiwi.ratelimit.memory import RateLimiter
from wiwi.router.router import ProviderAccount, ProviderKey, Router, _default_base_url
from wiwi.server import stats as stats_mod
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp


def _mask_secret(secret: str) -> str:
    if len(secret) >= 12:
        return secret[:5] + "…" + secret[-4:]
    return "***"


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
        self.alert_rules: list[dict[str, Any]] = []  # storage only; runtime-scoped

    async def init_db(self) -> None:
        import sqlalchemy.ext.asyncio as saa
        url = self.config.general_settings.database_url or "sqlite+aiosqlite:///wiwi.db"
        if url.startswith("sqlite:///"):
            url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: init DB, run logging workers."""
    state: AppState = app.state.wiwi
    await state.init_db()
    await state.logs.start()
    yield
    await state.shutdown()

def create_app(config: WiwiConfig) -> FastAPI:
    state = AppState(config)
    app = FastAPI(title="wiwi", version="0.1.0", docs_url="/docs",
                  lifespan=lifespan)
    app.state.wiwi = state

    # -- helpers ---------------------------------------------------------------
    def bearer(request: Request) -> str:
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer "):
            return authz[7:].strip()
        xkey = request.headers.get("x-api-key")  # Claude Code on /v1/messages
        return xkey.strip() if xkey else ""

    def is_admin(request: Request) -> bool:
        mk = config.general_settings.master_key
        if not mk:
            return False
        return hmac.compare_digest(bearer(request).encode(), mk.encode())

    async def authenticate(request: Request, model: str, surface: str = "chat",
                           est_tokens: int = 0):
        if state.auth is None:
            return None, _err(500, "api_error", "gateway not initialized", request)
        token = bearer(request)
        if not token:
            return None, _err(401, "authentication_error",
                              "missing API key", request, surface)
        info = await state.auth.authenticate(token)
        if info is None:
            return None, _err(401, "authentication_error", "invalid API key",
                              request, surface)
        if info.disabled or (info.expires_at and time.time() > info.expires_at):
            return None, _err(401, "authentication_error", "key disabled or expired",
                              request, surface)
        if info.over_budget:
            return None, _err(429, "budget_exceeded",
                              f"budget exhausted ({info.spend_to_date:.4f}"
                              f"/{info.max_budget})", request, surface)
        if info.models and model not in info.models:
            return None, _err(403, "permission_error",
                              f"key not allowed for model '{model}'", request, surface)
        allowed, retry_after = state.limiter.check(info.key_id, info.rpm, info.tpm,
                                                   est_tokens=est_tokens)
        if not allowed:
            resp = _err(429, "rate_limit_error",
                        f"rate limit exceeded, retry in {retry_after}s", request, surface)
            resp.headers["Retry-After"] = str(retry_after)
            return None, resp
        return info, None

    def _record_tpm_usage(info, ctx) -> None:
        """Add actual token usage to the tpm sliding windows after a response."""
        u = ctx.usage
        if u is not None and info is not None:
            state.limiter.record_tokens(info.key_id,
                                        u.prompt_tokens + u.completion_tokens)

    async def json_body(request: Request) -> tuple[Any, JSONResponse | None]:
        """Parse the request body; malformed JSON is a client error (400)."""
        try:
            return await request.json(), None
        except ValueError:
            return None, _err(400, "invalid_request_error",
                              "request body is not valid JSON", request)

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
        est = len(orjson.dumps(body)) // 4 if isinstance(body, dict) else 0
        info, err_resp = await authenticate(request, ir_req.model, surface,
                                            est_tokens=est)
        if err_resp:
            return err_resp
        group, _ = state_.router.resolve_group(ir_req.model)
        if group is None:
            return _err(404, "not_found_error",
                        f"model '{ir_req.model}' not found", request, surface)
        ctx = RequestContext(surface=surface, ir_req=ir_req, auth=info, group=group)
        gateway = state_.gateways["chat"]
        try:
            if ir_req.stream:
                encoder_pair = _encoder_for(surface, ir_req.model, ctx.request_id)
                # Pull the first delta before committing to a streaming
                # response: if the upstream fails during connect (bad request,
                # auth, rate limit...), execute_with_retries raises before any
                # byte is sent and we can still answer with a real JSON error.
                stream = gateway.stream(ctx)
                try:
                    first = await anext(stream)
                except StopAsyncIteration:
                    first = None
                except WiwiError as e:
                    ctx.status = e.status
                    ctx.error = e
                    state_.logs.log_request(build_log_event(ctx))
                    return _err(e.status, e.etype, e.message, request, surface)
                it = _stream_response(state_, ctx, encoder_pair, surface,
                                      stream, first)
                return StreamingResponse(
                    it,
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "x-wiwi-request-id": ctx.request_id})
            turn = await gateway.complete(ctx)
            ctx.status = 200
            payload = codec_encode_response(ctx, turn, ir_req.model, ctx.request_id)
            state_.logs.log_request(build_log_event(ctx))
            _record_tpm_usage(info, ctx)
            if info and info.key_type != "master":
                await state_.auth.update_spend(info.key_id, ctx.cost)
            return JSONResponse(payload, headers={"x-wiwi-request-id": ctx.request_id})
        except Exception as e:  # noqa: BLE001
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

    async def _stream_response(state_, ctx, encoder_pair, surface,
                               stream, first=None):
        from wiwi.streaming import deltas as dl
        encoder, style = encoder_pair
        errored = False
        try:
            if first is not None:
                if isinstance(first, dl.StreamError):
                    errored = True
                    ctx.status = 502
                    ctx.error = WiwiError(502, "api_error", first.message)
                chunk = encoder.feed(first)
                if chunk:
                    yield chunk
            async for d in stream:
                if isinstance(d, dl.StreamError) and not errored:
                    errored = True
                    ctx.status = 502
                    ctx.error = WiwiError(502, "api_error", d.message)
                chunk = encoder.feed(d)
                if chunk:
                    yield chunk
            # terminal frames, correct order per dialect:
            if errored:
                pass  # error frame already emitted by the encoder's feed()
            elif style == "chat":
                yield encoder.final_frame()          # finish_reason + usage
                yield b"data: [DONE]\n\n"
            elif style == "anthropic":
                yield encoder.final_frame()          # message_delta w/ usage+stop
                yield b"event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
            else:
                yield encoder._completed()           # response.completed
        finally:
            state_.logs.log_request(build_log_event(ctx))
            _record_tpm_usage(ctx.auth, ctx)
            if ctx.usage and ctx.auth and ctx.auth.key_type != "master":
                await state_.auth.update_spend(ctx.auth.key_id, ctx.cost)

    # -- surfaces ---------------------------------------------------------------
    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        return await run_chat_like(request, "chat", body, oc.decode_request,
                                   oc.encode_response, oc.error_body)

    @app.post("/v1/responses")
    async def responses_api(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        return await run_chat_like(request, "responses", body, orp.decode_request,
                                   orp.encode_response, orp.error_body)

    @app.post("/v1/messages")
    async def messages_api(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        return await run_chat_like(request, "messages", body, am.decode_request,
                                   am.encode_response, am.error_body)

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        try:
            ir_req = am.decode_request(body)
        except (oc.DialectError, ValueError) as e:
            return _err(400, "invalid_request_error", str(e), request, "messages")
        _, err_resp = await authenticate(request, ir_req.model, "messages")
        if err_resp:
            return err_resp
        from wiwi.ir import types as _ir
        total = 0
        for m in ir_req.messages:
            for p in m.parts:
                if isinstance(p, _ir.TextPart):
                    total += len(p.text) // 4 + 1
        return JSONResponse({"input_tokens": max(1, total)})

    @app.get("/v1/models")
    async def list_models(request: Request):
        # authenticated like the rest of the API (OpenAI requires auth here too)
        _, err_resp = await authenticate(request, model="*")
        if err_resp:
            return err_resp
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
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        plaintext, kid = await state.auth.create_key(
            alias=body.get("name") or body.get("alias") or "",
            models=body.get("models"), max_budget=body.get("max_budget"),
            rpm=body.get("rpm"), tpm=body.get("tpm"),
            ttl_seconds=body.get("ttl_seconds"),
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

    @app.post("/admin/keys/{key_id}/disable")
    async def admin_disable_key(key_id: str, request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        disabled = bool(body.get("disabled", True))
        await state.auth.set_disabled(key_id, disabled)
        await state.logs.log_audit(actor="master",
                                   action="key.disable" if disabled else "key.enable",
                                   target=key_id)
        return JSONResponse({"key_id": key_id, "disabled": disabled})

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
        import asyncio as _aio

        async def gen():
            q = await state.logs.sse.subscribe("request")
            pq = await state.logs.sse.subscribe("proxy")
            try:
                for seq, evt in state.logs.sse.replay("request", last_id):
                    yield encode_sse(seq, evt)
                while True:
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

    # -- admin: providers & pools ------------------------------------------------
    def _require_admin(request: Request) -> JSONResponse | None:
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        return None

    def _key_view(k, now_mono: float, now_wall: float) -> dict:
        cooling = k.status == "cooling" and now_mono < k.cooldown_until
        status = "disabled" if not k.enabled else ("cooling" if cooling else k.status)
        return {
            "label": k.label,
            "masked": _mask_secret(k.secret),
            "weight": k.weight,
            "enabled": k.enabled,
            "status": status,
            "cooldown_remaining_s": round(max(0.0, k.cooldown_until - now_mono), 1),
            "req_count": k.req_count,
            "err_count": k.err_count,
            "last_used_ts": round(now_wall - (now_mono - k.last_used), 3)
                            if k.last_used else None,
        }

    @app.get("/admin/providers")
    async def admin_providers(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        mono, wall = time.monotonic(), time.time()
        out = []
        for name in sorted(state.router.providers):
            acct = state.router.providers[name]
            out.append({
                "name": acct.name,
                "provider_type": acct.provider_type,
                "base_url": acct.base_url,
                "healthy": acct.healthy,
                "keys": [_key_view(k, mono, wall) for k in acct.keys],
            })
        return JSONResponse({"providers": out})

    @app.patch("/admin/providers/{name}/keys/{label}")
    async def admin_patch_provider_key(name: str, label: str, request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        key = acct.get_key(label)
        if key is None:
            return _err(404, "not_found_error",
                        f"unknown key '{label}' on provider '{name}'", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        diff: dict[str, Any] = {}
        if "enabled" in body:
            key.enabled = bool(body["enabled"])
            diff["enabled"] = key.enabled
        if "weight" in body:
            try:
                weight = max(1, int(body["weight"]))
            except (TypeError, ValueError):
                return _err(400, "invalid_request_error", "weight must be an integer",
                            request)
            key.weight = weight
            diff["weight"] = weight
        await state.logs.log_audit(actor="master", action="provider_key.update",
                                   target=f"{name}/{label}", diff=diff)
        return JSONResponse({"key": _key_view(key, time.monotonic(), time.time())})

    @app.post("/admin/providers/{name}/keys")
    async def admin_add_provider_key(name: str, request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        label = str(body.get("label") or "").strip()
        secret = str(body.get("key") or "")
        if not label or not secret:
            return _err(400, "invalid_request_error", "label and key are required",
                        request)
        if acct.get_key(label) is not None:
            return _err(409, "invalid_request_error",
                        f"key label '{label}' already exists", request)
        weight = max(1, int(body.get("weight") or 1))
        acct.keys.append(ProviderKey(label=label, secret=secret, weight=weight))
        await state.logs.log_audit(actor="master", action="provider_key.create",
                                   target=f"{name}/{label}",
                                   diff={"weight": weight})
        return JSONResponse({"key": _key_view(acct.get_key(label),  # type: ignore[arg-type]
                                              time.monotonic(), time.time())})

    @app.post("/admin/providers")
    async def admin_add_provider(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        name = str(body.get("name") or "").strip()
        ptype = str(body.get("provider_type") or "openai-compatible")
        base_url = str(body.get("base_url") or "") or _default_base_url(ptype)
        label = str(body.get("label") or "default")
        secret = str(body.get("key") or "")
        if not name or not secret:
            return _err(400, "invalid_request_error", "name and key are required",
                        request)
        if name in state.router.providers:
            return _err(409, "invalid_request_error",
                        f"provider '{name}' already exists", request)
        if ptype not in ("openai", "anthropic", "gemini", "openai-compatible"):
            return _err(400, "invalid_request_error",
                        f"unsupported provider type '{ptype}'", request)
        state.router.providers[name] = ProviderAccount(
            name=name, provider_type=ptype, base_url=base_url,
            keys=[ProviderKey(label=label, secret=secret)])
        await state.logs.log_audit(actor="master", action="provider.create",
                                   target=name,
                                   diff={"provider_type": ptype, "base_url": base_url})
        return JSONResponse({"name": name, "provider_type": ptype, "base_url": base_url})

    # -- admin: models & routing -------------------------------------------------
    @app.get("/admin/models")
    async def admin_models(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        mono = time.monotonic()
        groups = []
        for gname in sorted(state.router.groups):
            deps = state.router.groups[gname]
            groups.append({
                "name": gname,
                "deployments": [{
                    "provider": d.provider.name,
                    "model_id": d.model_id,
                    "weight": d.weight,
                    "available": d.available,
                    "inflight": d.inflight,
                    "p95_latency_ms": round(d.p95_latency(), 1),
                    "cooldown_remaining_s": round(max(0.0, d.cooldown_until - mono), 1),
                } for d in deps],
            })
        settings = state.router.settings
        return JSONResponse({"groups": groups,
                             "aliases": dict(settings.model_group_alias),
                             "strategy": settings.routing_strategy})

    @app.patch("/admin/model-groups/{name}")
    async def admin_patch_model_group(name: str, request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        gname, deps = state.router.resolve_group(name)
        if gname is None or not deps:
            return _err(404, "not_found_error", f"unknown model group '{name}'",
                        request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        diff: dict[str, Any] = {}
        weights = body.get("weights")
        if isinstance(weights, dict):
            applied: dict[str, int] = {}
            for dep in deps:
                ident = f"{dep.provider.name}/{dep.model_id}"
                if ident in weights:
                    try:
                        dep.weight = max(1, int(weights[ident]))
                    except (TypeError, ValueError):
                        return _err(400, "invalid_request_error",
                                    f"weight for '{ident}' must be an integer", request)
                    applied[ident] = dep.weight
            unknown = set(weights) - {f"{d.provider.name}/{d.model_id}" for d in deps}
            if unknown:
                return _err(400, "invalid_request_error",
                            f"unknown deployments: {sorted(unknown)}", request)
            diff["weights"] = applied
        strategy = body.get("strategy")
        if strategy is not None:
            if strategy not in ("simple-shuffle", "least-busy", "latency-based"):
                return _err(400, "invalid_request_error",
                            f"invalid strategy '{strategy}'", request)
            state.router.settings.routing_strategy = strategy
            diff["strategy"] = strategy
        await state.logs.log_audit(actor="master", action="model_group.update",
                                   target=gname, diff=diff)
        return JSONResponse({"group": gname, **diff})

    # -- admin: virtual keys PATCH -----------------------------------------------
    @app.patch("/admin/keys/{key_id}")
    async def admin_patch_key(key_id: str, request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        fields = {k: body[k] for k in state.auth.UPDATABLE_FIELDS  # type: ignore[union-attr]
                  if k in body}
        updated = await state.auth.update_key(key_id, fields)  # type: ignore[union-attr]
        if updated is None:
            return _err(404, "not_found_error", f"unknown key '{key_id}'", request)
        await state.logs.log_audit(actor="master", action="key.update", target=key_id,
                                   diff=fields)
        return JSONResponse({"key": updated})

    # -- admin: logs & stats -------------------------------------------------------
    @app.get("/admin/logs/proxy")
    async def admin_proxy_logs(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        ring = list(state.logs.sse.replay("proxy", 0))
        return JSONResponse({"logs": [public_dict(e) for _, e in ring[-500:]]})

    def _request_events() -> list[LogEvent]:
        return [e for _, e in state.logs.sse.replay("request", 0)]

    @app.get("/admin/stats/overview")
    async def admin_stats_overview(request: Request, minutes: int = 60):
        resp = _require_admin(request)
        if resp:
            return resp
        minutes = max(1, min(minutes, 1440))
        return JSONResponse(stats_mod.overview(_request_events(), minutes))

    @app.get("/admin/stats/timeseries")
    async def admin_stats_timeseries(request: Request, bucket: str = "minute",
                                     metric: str = "tokens", minutes: int = 60):
        resp = _require_admin(request)
        if resp:
            return resp
        try:
            return JSONResponse(stats_mod.timeseries(_request_events(), bucket, metric,
                                                     max(1, min(minutes, 1440))))
        except ValueError as e:
            return _err(400, "invalid_request_error", str(e), request)

    # -- admin: alert rules (storage only; evaluation engine is post-MVP) ----------
    @app.get("/admin/alert-rules")
    async def admin_get_alert_rules(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        return JSONResponse({"rules": state.alert_rules})

    @app.put("/admin/alert-rules")
    async def admin_put_alert_rules(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        rules = body.get("rules")
        if not isinstance(rules, list):
            return _err(400, "invalid_request_error", "'rules' must be a list", request)
        state.alert_rules = rules
        await state.logs.log_audit(actor="master", action="alert_rules.update",
                                   target="*", diff={"count": len(rules)})
        return JSONResponse({"rules": state.alert_rules})

    # -- admin UI (built SPA; wiwi/server/static produced by `cd web && bun run build`)
    static_dir = Path(os.environ.get("WIWI_STATIC_DIR")
                      or Path(__file__).parent / "static")

    class SPAStaticFiles(StaticFiles):
        """StaticFiles with SPA history fallback: unknown paths get index.html
        so client-side routes like /admin/ui/login survive hard refresh."""

        async def get_response(self, path: str, scope):
            resp = await super().get_response(path, scope)
            if resp.status_code == 404:
                return await super().get_response("index.html", scope)
            return resp

    if static_dir.is_dir():
        app.mount("/admin/ui", SPAStaticFiles(directory=str(static_dir), html=True),
                  name="admin-ui")

    return app
