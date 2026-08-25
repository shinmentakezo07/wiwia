"""FastAPI app factory: middleware chain, the three surfaces, admin API, health."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import orjson
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class ORJSONResponse(JSONResponse):
    """orjson-serialized JSON response.

    FastAPI's built-in ``ORJSONResponse`` was deprecated in 0.141 (it now
    serializes via Pydantic when a response_model is set). We return raw dicts
    from most admin endpoints, so we keep orjson (faster, handles datetimes)
    via this trivial subclass instead of the deprecated shim.
    """

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS)


class RequestIdMiddleware:
    """Pure ASGI middleware: request ID, body-size guard, latency header.

    Replaces the previous ``@app.middleware("http")`` (BaseHTTPMiddleware)
    which wraps every response — including StreamingResponse — in a
    background task pumping chunks through an internal anyio memory stream.
    When uvicorn cancels in-flight tasks during graceful shutdown, that pump
    is cancelled mid-flight and raises WouldBlock/CancelledError.  A pure
    ASGI middleware passes streaming responses through untouched, so shutdown
    cancellation simply ends the response with no error traceback.
    """

    def __init__(self, app: ASGIApp, *, max_body_mb: float) -> None:
        self.app = app
        self.max_body_bytes = int(max_body_mb * 1024 * 1024)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        import uuid

        rid = uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = rid
        t0 = time.monotonic()

        # --- body-size guard (early 413) -------------------------------------
        cl = None
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                cl = v
                break
        if cl is not None and cl.isdigit() and int(cl) > self.max_body_bytes:
            body = orjson.dumps(
                {"error": {"message": f"request body exceeds {self.max_body_bytes // (1024 * 1024)} MiB",
                           "type": "invalid_request_error",
                           "code": "invalid_request_error"}}
            )
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode()),
                                    (b"x-wiwi-request-id", rid.encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        # --- inject headers into the response.start message ------------------
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Avoid duplicate x-wiwi-request-id (handlers set it on
                # StreamingResponse/ORJSONResponse directly).
                has_rid = any(k == b"x-wiwi-request-id" for k, _ in headers)
                if not has_rid:
                    headers.append((b"x-wiwi-request-id", rid.encode()))
                headers.append((b"x-wiwi-latency-ms",
                                f"{(time.monotonic() - t0) * 1000:.1f}".encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


from wiwi.auth.service import AuthService
from wiwi.config import PROVIDER_TYPES, ConfigError, WiwiConfig, _interpolate, load_config, load_env
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway, build_log_event
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.logging_core.events import LogEvent
from wiwi.logging_core.subsystem import LoggingSubsystem, encode_sse, public_dict
from wiwi.providers.base import ProviderKeyRef, WiwiError
from wiwi.providers.registry import get_adapter
from wiwi.ratelimit.memory import RateLimiter
from wiwi.router.router import (
    BUILTIN_PROVIDER_TYPES,
    Deployment,
    ProviderAccount,
    ProviderKey,
    Router,
    _default_base_url,
)
from wiwi.server import stats as stats_mod
from wiwi.server.config_store import ConfigStore
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp


def _mask_secret(secret: str) -> str:
    if len(secret) >= 12:
        return secret[:5] + "…" + secret[-4:]
    return "***"


def _provider_models_url(provider_type: str, base_url: str) -> str:
    """Upstream model-list endpoint per provider type."""
    base = (base_url or _default_base_url(provider_type)).rstrip("/")
    return f"{base}/models"  # openai/compatible, anthropic, and gemini share the path


def _parse_models_response(provider_type: str, body: bytes) -> list[dict[str, str]]:
    """Normalize upstream model listings to [{id}] entries."""
    data = orjson.loads(body)
    if not isinstance(data, dict):
        return []
    if provider_type == "gemini":
        return [{"id": m["name"].split("/")[-1]}
                for m in data.get("models", []) if isinstance(m, dict) and "name" in m]
    return [{"id": str(m["id"])} for m in data.get("data", [])
            if isinstance(m, dict) and "id" in m]


class AppState:
    def __init__(self, config: WiwiConfig):
        self.config = config
        self.router = Router(config)
        self.cost = CostEngine()
        self.logs = LoggingSubsystem()
        rs = config.router_settings
        self.limiter = RateLimiter(global_rpm=rs.global_rpm, global_tpm=rs.global_tpm)
        self.auth: AuthService | None = None
        self.gateways: dict[str, Gateway] = {}
        self.alert_rules: list[dict[str, Any]] = []
        self.config_store: ConfigStore | None = None
        # Set during shutdown so long-lived SSE generators break out of their
        # event loop instead of blocking uvicorn's graceful-shutdown drain
        # (which otherwise hangs at "Waiting for connections to close").
        self.shutdown_event: asyncio.Event | None = None

    async def init_db(self) -> None:
        import sqlalchemy.ext.asyncio as saa
        # DATABASE_URL env var overrides config; fall back to SQLite if unset.
        # Normalize postgres:// and postgresql:// to postgresql+asyncpg:// so
        # the async driver (asyncpg) is always used.  Translate psycopg2-style
        # query params (sslmode) to asyncpg equivalents (ssl) since asyncpg
        # doesn't understand sslmode.  For serverless Postgres (Neon, Supabase),
        # pool parameters are tuned for short-lived connections.
        url = (os.environ.get("DATABASE_URL")
               or self.config.general_settings.database_url
               or "sqlite+aiosqlite:///wiwi.db")
        if url.startswith("sqlite:///"):
            url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql+asyncpg://"):
            import urllib.parse as _up
            parts = _up.urlparse(url)
            query = dict(_up.parse_qsl(parts.query))
            # asyncpg uses ssl=require instead of sslmode=require
            if "sslmode" in query:
                query["ssl"] = query.pop("sslmode")
            # channel_binding is not understood by asyncpg; drop it (Neon
            # enforces it server-side when sslmode=require anyway)
            query.pop("channel_binding", None)
            url = _up.urlunparse(parts._replace(query=_up.urlencode(query)))
        is_pg = url.startswith("postgresql+asyncpg://")
        engine_kwargs: dict[str, Any] = {"url": url}
        if is_pg:
            engine_kwargs.update(
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=300,
            )
        aengine = saa.create_async_engine(**engine_kwargs)
        if not is_pg:
            # SQLite: enable FK enforcement on every connection so that
            # ON DELETE CASCADE works (aiosqlite disables it by default).
            import sqlalchemy as _sa
            @_sa.event.listens_for(aengine.sync_engine, "connect")
            def _enable_sqlite_fk(dbapi_conn, _record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        self._db_engine = aengine
        self.auth = AuthService(aengine, self.config.general_settings.master_key)
        await self.auth.startup()
        from wiwi.logging_core.db_sink import DBSink
        self._db_sink = DBSink(aengine)
        await self._db_sink.startup()
        self.logs.set_db_sink(self._db_sink)
        # Persist admin-added providers/keys/deployments so they survive restart
        self.config_store = ConfigStore(aengine)
        await self.config_store.startup()
        await self._load_db_config()
        self.gateways = {
            "chat": Gateway(self.router, self.cost, "chat",
                            drop_params=self.config.wiwi_settings.drop_params),
        }
        self.shutdown_event = asyncio.Event()

    async def _load_db_config(self) -> None:
        """Merge DB-stored providers/keys/deployments into the YAML-built router.

        YAML entries are loaded first by ``Router._build``; DB entries are
        layered on top, skipping any name/label that already exists from YAML.
        Alert rules and routing strategy are loaded from the settings table.
        """
        if self.config_store is None:
            return
        data = await self.config_store.load_all()
        # providers
        for p in data["providers"]:
            if p["name"] in self.router.providers:
                continue  # YAML-sourced, skip
            self.router.providers[p["name"]] = ProviderAccount(
                name=p["name"], provider_type=p["provider_type"],
                base_url=p["base_url"], timeout_s=p["timeout_s"],
                extra_headers=p.get("extra_headers", {}),
                round_robin=p.get("round_robin", True))
        # keys
        for k in data["keys"]:
            acct = self.router.providers.get(k["provider_name"])
            if acct is None or acct.get_key(k["label"]) is not None:
                continue  # provider missing or label from YAML
            acct.keys.append(ProviderKey(
                label=k["label"], secret=k["secret"], weight=k["weight"],
                enabled=k["enabled"]))
        # deployments
        for d in data["deployments"]:
            acct = self.router.providers.get(d["provider_name"])
            if acct is None:
                continue
            already = any(
                dep.provider is acct and dep.model_id == d["model_id"]
                for dep in self.router.groups.get(d["group_name"], []))
            if already:
                continue
            dep = Deployment(group=d["group_name"], provider=acct,
                             model_id=d["model_id"], weight=d["weight"])
            self.router.groups.setdefault(d["group_name"], []).append(dep)
        # alert rules
        rules = await self.config_store.get_setting("alert_rules")
        if rules is not None:
            self.alert_rules = rules
        # routing strategy
        strategy = await self.config_store.get_setting("routing_strategy")
        if strategy is not None:
            self.router.settings.routing_strategy = strategy

    async def shutdown(self) -> None:
        if self.shutdown_event is not None:
            self.shutdown_event.set()
        for g in self.gateways.values():
            await g.aclose()
        await self.logs.stop()
        if getattr(self, "_db_sink", None) is not None:
            await self._db_sink.engine.dispose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: init DB, run logging workers."""
    state: AppState = app.state.wiwi
    await state.init_db()
    await state.logs.start()
    yield
    await state.shutdown()


def _inject_id(chunk: bytes, event_id: int) -> bytes:
    """Prepend an SSE ``id:`` line to a complete SSE frame.

    The id line is placed before the first ``event:`` or ``data:`` line,
    which is valid SSE (id is part of the event block).
    """
    id_line = f"id: {event_id}\n".encode()
    return id_line + chunk


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
                           est_tokens: int = 0, reserve: bool = True):
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
        # "" / "*" = endpoint is not model-scoped (e.g. GET /v1/models):
        # listing never violates an allowlist, only real completions do
        if info.models and model and model != "*" and model not in info.models:
            return None, _err(403, "permission_error",
                              f"key not allowed for model '{model}'", request, surface)
        if reserve:
            allowed, retry_after = state.limiter.check(info.key_id, info.rpm,
                                                       info.tpm,
                                                       est_tokens=est_tokens)
            if not allowed:
                resp = _err(429, "rate_limit_error",
                            f"rate limit exceeded, retry in {retry_after}s",
                            request, surface)
                resp.headers["Retry-After"] = str(retry_after)
                return None, resp
        return info, None

    def enforce_rate_limit(info, est_tokens: int, request: Request,
                           surface: str) -> ORJSONResponse | None:
        """Reserve RPM/TPM window slots only once the model is known-good."""
        allowed, retry_after = state.limiter.check(info.key_id, info.rpm,
                                                   info.tpm,
                                                   est_tokens=est_tokens)
        if not allowed:
            resp = _err(429, "rate_limit_error",
                        f"rate limit exceeded, retry in {retry_after}s",
                        request, surface)
            resp.headers["Retry-After"] = str(retry_after)
            return resp
        return None

    def _record_tpm_usage(info, ctx) -> None:
        """Add actual token usage to the tpm sliding windows after a response."""
        u = ctx.usage
        if u is not None and info is not None:
            state.limiter.record_tokens(info.key_id,
                                        u.prompt_tokens + u.completion_tokens)

    async def json_body(request: Request) -> tuple[Any, ORJSONResponse | None]:
        """Parse the request body; malformed JSON is a client error (400)."""
        try:
            return await request.json(), None
        except ValueError:
            return None, _err(400, "invalid_request_error",
                              "request body is not valid JSON", request)

    def _err(status: int, etype: str, message: str,
             request: Request, surface: str = "chat") -> ORJSONResponse:
        rid = getattr(request.state, "request_id", "")
        if surface == "messages":
            body = am.error_body(status, etype, message)
        else:
            body = oc.error_body(status, etype, message)
        return ORJSONResponse(body, status_code=status,
                              headers={"x-wiwi-request-id": rid})

    # Pure ASGI middleware (replaces @app.middleware("http") / BaseHTTPMiddleware,
    # which breaks streaming responses during graceful shutdown — see RequestIdMiddleware).
    app.user_middleware.insert(0, Middleware(RequestIdMiddleware,
                                            max_body_mb=config.wiwi_settings.max_request_body_mb))

    # -- shared execution ------------------------------------------------------

    def _serialize_turn(turn: ir.AssistantTurn, payload: Any) -> dict[str, Any]:
        """Build a JSON-serializable snapshot of the model's response."""
        return {
            "text": turn.text,
            "thinking": [{"text": t.text} for t in turn.thinking],
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.raw_args or tc.args}
                for tc in turn.tool_calls
            ],
            "stop_reason": turn.stop_reason,
            "usage": ({"prompt_tokens": turn.usage.prompt_tokens,
                       "completion_tokens": turn.usage.completion_tokens,
                       "cached_tokens": turn.usage.cached_tokens,
                       "reasoning_tokens": turn.usage.reasoning_tokens}
                      if turn.usage else None),
            "response": payload,
        }

    async def run_chat_like(request: Request, surface: str, body: dict[str, Any],
                            codec_decode, codec_encode_response, error_body_fn):
        state_ = app.state.wiwi
        try:
            ir_req = codec_decode(body)
        except (oc.DialectError, ValueError) as e:
            return _err(400, "invalid_request_error", str(e), request, surface)
        est = len(orjson.dumps(body)) // 4 if isinstance(body, dict) else 0
        info, err_resp = await authenticate(request, ir_req.model, surface,
                                            est_tokens=est, reserve=False)
        if err_resp:
            return err_resp
        group, _ = state_.router.resolve_group(ir_req.model)
        if group is None:
            return _err(404, "not_found_error",
                        f"model '{ir_req.model}' not found", request, surface)
        rl_err = enforce_rate_limit(info, est, request, surface)
        if rl_err:
            return rl_err
        ctx = RequestContext(surface=surface, ir_req=ir_req, auth=info, group=group)
        gateway = state_.gateways["chat"]
        if config.wiwi_settings.store_prompts_in_spend_logs:
            ctx.metadata["request_body"] = body
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
                    await stream.aclose()  # release pump resources, if any
                    return _err(e.status, e.etype, e.message, request, surface)
                it = _stream_response(state_, ctx, encoder_pair, surface,
                                      stream, first,
                                      event_ids=config.router_settings.stream_event_ids)
                return StreamingResponse(
                    it,
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "x-wiwi-request-id": ctx.request_id})
            turn = await gateway.complete(ctx)
            ctx.status = 200
            payload = codec_encode_response(ctx, turn, ir_req.model, ctx.request_id)
            if config.wiwi_settings.store_prompts_in_spend_logs:
                ctx.metadata["response_body"] = _serialize_turn(turn, payload)
            state_.logs.log_request(build_log_event(ctx))
            _record_tpm_usage(info, ctx)
            if info and info.key_type != "master":
                await state_.auth.update_spend(info.key_id, ctx.cost)
            return ORJSONResponse(payload, headers={"x-wiwi-request-id": ctx.request_id})
        except Exception as e:  # noqa: BLE001
            if isinstance(e, WiwiError):
                ctx.status = e.status
                ctx.error = e
                state_.logs.log_request(build_log_event(ctx))
                resp = _err(e.status, e.etype, e.message, request, surface)
                if e.retry_after:
                    resp.headers["Retry-After"] = str(int(max(1.0, e.retry_after)))
                return resp
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

    def _capture_delta(
        d: Any,
        ctx: RequestContext,
        text_buf: list[str],
        thinking_buf: list[str],
        tools_map: dict[int, dict[str, Any]],
    ) -> None:
        """Accumulate streaming deltas into serializable buffers for log capture."""
        from wiwi.streaming import deltas as dl
        if isinstance(d, dl.TextDelta):
            text_buf.append(d.text)
        elif isinstance(d, dl.ThinkingDelta):
            thinking_buf.append(d.text)
        elif isinstance(d, dl.ToolCallOpen):
            tools_map[d.index] = {"id": d.id, "name": d.name, "arguments": ""}
        elif isinstance(d, dl.ToolCallArgsDelta):
            entry = tools_map.get(d.index)
            if entry is not None:
                entry["arguments"] += d.args_fragment
        elif isinstance(d, dl.UsageFinal):
            ctx._stream_usage = d  # type: ignore[attr-defined]
        elif isinstance(d, dl.Finish):
            ctx.stop_reason = d.stop_reason

    async def _stream_response(state_, ctx, encoder_pair, surface,
                               stream, first=None, event_ids=False):
        from wiwi.streaming import deltas as dl
        encoder, style = encoder_pair
        errored = False
        _seq = 0
        store_prompts = config.wiwi_settings.store_prompts_in_spend_logs
        stream_text: list[str] = []
        stream_thinking: list[str] = []
        stream_tools: dict[int, dict[str, Any]] = {}
        try:
            if first is not None:
                if isinstance(first, dl.StreamError):
                    errored = True
                    ctx.status = 502
                    ctx.error = WiwiError(502, "api_error", first.message)
                if store_prompts:
                    _capture_delta(first, ctx, stream_text, stream_thinking,
                                   stream_tools)
                _seq += 1
                chunk = encoder.feed(first)
                if chunk:
                    yield _inject_id(chunk, _seq) if event_ids else chunk
            async for d in stream:
                if isinstance(d, dl.StreamError) and not errored:
                    errored = True
                    ctx.status = 502
                    ctx.error = WiwiError(502, "api_error", d.message)
                if store_prompts:
                    _capture_delta(d, ctx, stream_text, stream_thinking,
                                   stream_tools)
                _seq += 1
                chunk = encoder.feed(d)
                if chunk:
                    yield _inject_id(chunk, _seq) if event_ids else chunk
            # terminal frames, correct order per dialect:
            if errored:
                pass  # error frame already emitted by the encoder's feed()
            elif style == "chat":
                chunk = encoder.final_frame()
                yield _inject_id(chunk, _seq) if event_ids else chunk
                yield b"data: [DONE]\n\n"
            elif style == "anthropic":
                chunk = encoder.final_frame()
                yield _inject_id(chunk, _seq) if event_ids else chunk
                yield b"event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
            else:
                chunk = encoder._completed()
                yield _inject_id(chunk, _seq) if event_ids else chunk
        finally:
            if store_prompts:
                stream_usage = getattr(ctx, "_stream_usage", None)
                ctx.metadata["response_body"] = {
                    "text": "".join(stream_text),
                    "thinking": [{"text": t} for t in stream_thinking],
                    "tool_calls": [stream_tools[i] for i in sorted(stream_tools)],
                    "stop_reason": ctx.stop_reason or "stop",
                    "usage": ({"prompt_tokens": stream_usage.prompt,
                               "completion_tokens": stream_usage.output,
                               "cached_tokens": stream_usage.cached,
                               "reasoning_tokens": stream_usage.reasoning}
                              if stream_usage else None),
                    "streamed": True,
                }
            state_.logs.log_request(build_log_event(ctx))
            _record_tpm_usage(ctx.auth, ctx)
            if ctx.usage and ctx.auth and ctx.auth.key_type != "master":
                # never let accounting failure mask the streamed response
                with contextlib.suppress(Exception):
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
        return ORJSONResponse({"input_tokens": max(1, total)})

    @app.get("/v1/models")
    async def list_models(request: Request):
        # authenticated like the rest of the API (OpenAI requires auth here too)
        _, err_resp = await authenticate(request, model="*")
        if err_resp:
            return err_resp
        data = []
        for name in sorted(app.state.wiwi.router.groups.keys()):
            data.append({"id": name, "object": "model", "owned_by": "wiwi"})
        return ORJSONResponse({"object": "list", "data": data})

    @app.get("/health")
    async def health():
        return {"status": "ok", "groups": len(app.state.wiwi.router.groups),
                "providers": len(app.state.wiwi.router.providers)}

    # -- metrics ---------------------------------------------------------------
    if config.router_settings.prometheus_enabled:
        from wiwi.server.metrics import render_metrics
        metrics_path = config.router_settings.prometheus_path

        @app.get(metrics_path)
        async def prometheus_metrics():
            events = [e for _, e in state.logs.sse.replay("request", 0)]
            text = render_metrics(events)
            return PlainTextResponse(text, media_type="text/plain; version=0.0.4")

    # -- admin -------------------------------------------------------------------
    @app.post("/admin/keys/generate")
    async def admin_generate_key(request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        models = body.get("models")
        if models is not None and (not isinstance(models, list)
                                   or not all(isinstance(m, str) for m in models)):
            return _err(400, "invalid_request_error",
                        "'models' must be a list of strings", request)
        try:
            plaintext, kid = await state.auth.create_key(
                alias=str(body.get("name") or body.get("alias") or ""),
                models=body.get("models"), max_budget=body.get("max_budget"),
                rpm=body.get("rpm"), tpm=body.get("tpm"),
                ttl_seconds=body.get("ttl_seconds"),
                custom_key=body.get("custom_key"))
        except ValueError as e:
            return _err(400, "invalid_request_error", str(e), request)
        await state.logs.log_audit(
            actor="master", action="key.generate", target=kid,
            diff={"source": "custom"} if body.get("custom_key") else None)
        return ORJSONResponse({"key": plaintext, "id": kid,
                             "note": "store this key now; it is not shown again"})

    @app.get("/admin/keys")
    async def admin_list_keys(request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        return ORJSONResponse({"keys": await state.auth.list_keys()})

    @app.delete("/admin/keys/{key_id}")
    async def admin_delete_key(key_id: str, request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        ok = await state.auth.delete_key(key_id)
        await state.logs.log_audit(actor="master", action="key.delete", target=key_id)
        return ORJSONResponse({"deleted": ok})

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
        return ORJSONResponse({"key_id": key_id, "disabled": disabled})

    @app.get("/admin/logs/requests")
    async def admin_request_logs(request: Request, limit: int = 10000):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        # Hard ceiling (50k) is a safety net against runaway callers, not a
        # product limit — the Usage page trusts the DB-backed overview for
        # the headline number and only uses this endpoint for the row table.
        limit = max(1, min(limit, 50000))
        sink = state.logs.db_sink
        if sink is not None:
            return ORJSONResponse({"logs": await sink.read_requests(limit)})
        ring = list(state.logs.sse.replay("request", 0))
        return ORJSONResponse({"logs": [public_dict(e) for _, e in ring[-limit:]]})

    @app.get("/admin/stream")
    async def admin_stream(request: Request):
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        last_id = int(request.headers.get("last-event-id", "0") or 0)

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
                for seq, evt in state.logs.sse.replay("request", last_id):
                    yield encode_sse(seq, evt)
                    last_sent = max(last_sent, seq)
                while True:
                    get_task = asyncio.create_task(combined.get())
                    wait_set: set[asyncio.Future] = {get_task}
                    if shutdown is not None:
                        wait_set.add(asyncio.create_task(shutdown.wait()))
                    done, _pending = await asyncio.wait(
                        wait_set, timeout=15.0, return_when=asyncio.FIRST_COMPLETED)
                    if not done:
                        # timed out — keepalive; also bail if the client is gone
                        if await request.is_disconnected():
                            break
                        yield b": ping\n\n"  # EventSource ignores comments
                        continue
                    if shutdown is not None and shutdown.is_set():
                        break
                    seq, evt = get_task.result()
                    if evt.stream == "request" and seq <= last_sent:
                        continue  # already replayed above
                    if evt.stream == "request":
                        last_sent = seq
                    yield encode_sse(seq, evt)
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

    # -- admin: providers & pools ------------------------------------------------
    def _require_admin(request: Request) -> ORJSONResponse | None:
        if not is_admin(request):
            return _err(401, "authentication_error", "master key required", request)
        return None

    def _key_view(k, now_mono: float, now_wall: float) -> dict:
        cooling = k.status == "cooling" and now_mono < k.cooldown_until
        status = "disabled" if not k.enabled else ("cooling" if cooling else k.status)
        return {
            "label": k.label,
            "masked": _mask_secret(k.secret),
            "secret": k.secret,
            "weight": k.weight,
            "enabled": k.enabled,
            "status": status,
            "cooldown_remaining_s": round(max(0.0, k.cooldown_until - now_mono), 1),
            "req_count": k.req_count,
            "err_count": k.err_count,
            "last_used_ts": round(now_wall - (now_mono - k.last_used), 3)
                            if k.last_used else None,
        }

    @app.get("/admin/provider-catalog")
    async def admin_provider_catalog(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        configured = {a.provider_type for a in state.router.providers.values()}
        out = []
        for p in BUILTIN_PROVIDER_TYPES:
            entry = {**p, "builtin": True}
            entry["configured"] = p["provider_type"] in configured
            out.append(entry)
        return ORJSONResponse({"providers": out})

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
                "round_robin": acct.round_robin,
                "healthy": acct.healthy,
                "keys": [_key_view(k, mono, wall) for k in acct.keys],
            })
        return ORJSONResponse({"providers": out})

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
        if body.get("reset_status"):
            key.status = "active"
            key.cooldown_until = 0.0
            diff["reset_status"] = True
        if state.config_store:
            await state.config_store.update_key(
                name, label,
                weight=diff.get("weight"),
                enabled=diff.get("enabled"))
        await state.logs.log_audit(actor="master", action="provider_key.update",
                                   target=f"{name}/{label}", diff=diff)
        return ORJSONResponse({"key": _key_view(key, time.monotonic(), time.time())})

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
        secret = str(_interpolate(body.get("key")) or "")
        if not label or not secret:
            return _err(400, "invalid_request_error", "label and key are required",
                        request)
        if acct.get_key(label) is not None:
            return _err(409, "invalid_request_error",
                        f"key label '{label}' already exists", request)
        try:
            weight = max(1, int(body.get("weight") or 1))
        except (TypeError, ValueError):
            return _err(400, "invalid_request_error", "weight must be an integer",
                        request)
        acct.keys.append(ProviderKey(label=label, secret=secret, weight=weight))
        if state.config_store:
            await state.config_store.add_key(name, label, secret, weight)
        await state.logs.log_audit(actor="master", action="provider_key.create",
                                   target=f"{name}/{label}",
                                   diff={"weight": weight})
        return ORJSONResponse({"key": _key_view(acct.get_key(label),  # type: ignore[arg-type]
                                              time.monotonic(), time.time())})

    @app.delete("/admin/providers/{name}/keys/{label}")
    async def admin_delete_provider_key(name: str, label: str, request: Request):
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
        acct.keys = [k for k in acct.keys if k.label != label]
        if state.config_store:
            await state.config_store.delete_key(name, label)
        await state.logs.log_audit(actor="master", action="provider_key.delete",
                                   target=f"{name}/{label}")
        return ORJSONResponse({"deleted": True, "label": label})

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
        base_url = str(_interpolate(body.get("base_url")) or "") or _default_base_url(ptype)
        label = str(body.get("label") or "default")
        secret = str(_interpolate(body.get("key")) or "")
        if not name or not secret:
            return _err(400, "invalid_request_error", "name and key are required",
                        request)
        if name in state.router.providers:
            return _err(409, "invalid_request_error",
                        f"provider '{name}' already exists", request)
        if ptype not in PROVIDER_TYPES:
            return _err(400, "invalid_request_error",
                        f"unsupported provider type '{ptype}'", request)
        if not base_url:
            return _err(400, "invalid_request_error",
                        f"base_url is required for provider type '{ptype}'",
                        request)
        state.router.providers[name] = ProviderAccount(
            name=name, provider_type=ptype, base_url=base_url,
            keys=[ProviderKey(label=label, secret=secret)])
        if state.config_store:
            await state.config_store.add_provider(name, ptype, base_url)
            await state.config_store.add_key(name, label, secret)
        await state.logs.log_audit(actor="master", action="provider.create",
                                   target=name,
                                   diff={"provider_type": ptype, "base_url": base_url})
        return ORJSONResponse({"name": name, "provider_type": ptype, "base_url": base_url})

    @app.delete("/admin/providers/{name}")
    async def admin_delete_provider(name: str, request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        # Block if any model group still references this provider.
        referencing = sorted(
            gname for gname, deps in state.router.groups.items()
            if any(d.provider is acct for d in deps)
        )
        if referencing:
            return _err(409, "invalid_request_error",
                        f"provider still referenced by groups: "
                        f"{', '.join(referencing)} — remove those deployments first",
                        request)
        del state.router.providers[name]
        if state.config_store:
            await state.config_store.delete_provider(name)
        await state.logs.log_audit(actor="master", action="provider.delete", target=name)
        return ORJSONResponse({"deleted": True, "name": name})

    @app.patch("/admin/providers/{name}")
    async def admin_patch_provider(name: str, request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        diff: dict[str, Any] = {}
        new_name: str | None = None
        if "name" in body:
            new_name = str(body["name"]).strip()
            if not new_name:
                return _err(400, "invalid_request_error", "name must be non-empty",
                            request)
            if new_name != name and new_name in state.router.providers:
                return _err(409, "invalid_request_error",
                            f"provider '{new_name}' already exists", request)
            diff["name"] = new_name
        if "provider_type" in body:
            ptype = str(body["provider_type"])
            if ptype not in PROVIDER_TYPES:
                return _err(400, "invalid_request_error",
                            f"unsupported provider type '{ptype}'", request)
            acct.provider_type = ptype
            diff["provider_type"] = ptype
        if "base_url" in body:
            base_url = str(_interpolate(body["base_url"])) or ""
            if not base_url:
                return _err(400, "invalid_request_error",
                            "base_url must be non-empty", request)
            acct.base_url = base_url
            diff["base_url"] = base_url
        if "round_robin" in body:
            acct.round_robin = bool(body["round_robin"])
            diff["round_robin"] = acct.round_robin
        # apply rename last so identity-based deployment refs stay valid
        if new_name is not None and new_name != name:
            acct.name = new_name
            state.router.providers[new_name] = acct
            del state.router.providers[name]
            target = f"{name}→{new_name}"
        else:
            target = name
        if state.config_store:
            await state.config_store.update_provider(
                name, provider_type=diff.get("provider_type"),
                base_url=diff.get("base_url"),
                round_robin=diff.get("round_robin"), new_name=new_name)
        await state.logs.log_audit(actor="master", action="provider.update",
                                   target=target, diff=diff)
        mono, wall = time.monotonic(), time.time()
        return ORJSONResponse({
            "name": acct.name,
            "provider_type": acct.provider_type,
            "base_url": acct.base_url,
            "round_robin": acct.round_robin,
            "healthy": acct.healthy,
            "keys": [_key_view(k, mono, wall) for k in acct.keys],
        })

    @app.get("/admin/providers/{name}/models")
    async def admin_provider_models(name: str, request: Request):
        """Fetch model ids live from the upstream provider (first available key)."""
        resp = _require_admin(request)
        if resp:
            return resp
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        key = next((k for k in acct.keys if k.available), None)
        if key is None:
            return _err(409, "invalid_request_error",
                        f"no available key on provider '{name}' to fetch models",
                        request)
        adapter = get_adapter(acct.provider_type)
        url = _provider_models_url(acct.provider_type, acct.base_url)
        headers = adapter.headers(ProviderKeyRef(label=key.label, secret=key.secret))
        # Gemini puts the API key in the querystring, not headers
        if acct.provider_type == "gemini":
            url += f"?key={key.secret}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as hc:
                r = await hc.get(url, headers=headers)
        except httpx.HTTPError:
            return _err(502, "api_connection_error",
                        f"could not reach '{name}' ({url})", request)
        if r.status_code != 200:
            etype = ("rate_limit_error" if r.status_code == 429
                     else "authentication_error" if r.status_code in (401, 403)
                     else "not_found_error" if r.status_code == 404
                     else "api_error")
            return _err(r.status_code if r.status_code < 500 else 502, etype,
                        f"{name} returned HTTP {r.status_code}: {r.text[:300]}", request)
        models = sorted(m["id"] for m in
                        _parse_models_response(acct.provider_type, r.content))
        return ORJSONResponse({"models": [{"id": mid} for mid in models]})

    @app.post("/admin/model-groups/{name:path}/deployments")
    async def admin_add_deployment(name: str, request: Request):
        """Attach a provider deployment to a model group (creating the group)."""
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        gname = str(body.get("group") or name).strip()
        pname = str(body.get("provider") or "").strip()
        model_id = str(body.get("model_id") or "").strip()
        if not gname or not pname or not model_id:
            return _err(400, "invalid_request_error",
                        "group, provider and model_id are required", request)
        acct = state.router.providers.get(pname)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{pname}'", request)
        try:
            weight = max(1, int(body.get("weight") or 1))
        except (TypeError, ValueError):
            return _err(400, "invalid_request_error", "weight must be an integer",
                        request)
        dep = Deployment(group=gname, provider=acct, model_id=model_id, weight=weight)
        if any(d.provider.name == acct.name and d.model_id == model_id
               for d in state.router.groups.get(gname, [])):
            return _err(409, "invalid_request_error",
                        f"deployment {acct.name}/{model_id} is already attached"
                        f" to group '{gname}'",
                        request)
        state.router.groups.setdefault(gname, []).append(dep)
        if state.config_store:
            await state.config_store.add_deployment(gname, pname, model_id, weight)
        await state.logs.log_audit(actor="master", action="deployment.create",
                                   target=f"{gname}/{pname}/{model_id}",
                                   diff={"weight": weight})
        mono = time.monotonic()
        return ORJSONResponse({"deployment": {
            "provider": dep.provider.name,
            "model_id": dep.model_id,
            "weight": dep.weight,
            "available": dep.available,
            "inflight": dep.inflight,
            "p95_latency_ms": round(dep.p95_latency(), 1),
            "cooldown_remaining_s": round(max(0.0, dep.cooldown_until - mono), 1),
        }}, status_code=201)

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
        return ORJSONResponse({"groups": groups,
                             "aliases": dict(settings.model_group_alias),
                             "strategy": settings.routing_strategy})

    @app.patch("/admin/model-groups/{name:path}")
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
            # atomic: validate every entry BEFORE mutating live routing state
            idents = {f"{d.provider.name}/{d.model_id}": d for d in deps}
            unknown = set(weights) - set(idents)
            if unknown:
                return _err(400, "invalid_request_error",
                            f"unknown deployments: {sorted(unknown)}", request)
            parsed: dict[str, int] = {}
            for ident, w in weights.items():
                try:
                    parsed[ident] = max(1, int(w))
                except (TypeError, ValueError):
                    return _err(400, "invalid_request_error",
                                f"weight for '{ident}' must be an integer", request)
            applied: dict[str, int] = {}
            for ident, w in parsed.items():
                idents[ident].weight = w
                applied[ident] = w
                if state.config_store:
                    pname, mid = ident.split("/", 1)
                    await state.config_store.update_deployment_weight(
                        gname, pname, mid, w)
            diff["weights"] = applied
        strategy = body.get("strategy")
        if strategy is not None:
            if strategy not in ("simple-shuffle", "least-busy", "latency-based"):
                return _err(400, "invalid_request_error",
                            f"invalid strategy '{strategy}'", request)
            state.router.settings.routing_strategy = strategy
            diff["strategy"] = strategy
            if state.config_store:
                await state.config_store.set_setting("routing_strategy", strategy)
        await state.logs.log_audit(actor="master", action="model_group.update",
                                   target=gname, diff=diff)
        return ORJSONResponse({"group": gname, **diff})

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
        return ORJSONResponse({"key": updated})

    # -- admin: logs & stats -------------------------------------------------------
    @app.get("/admin/logs/proxy")
    async def admin_proxy_logs(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        ring = list(state.logs.sse.replay("proxy", 0))
        return ORJSONResponse({"logs": [public_dict(e) for _, e in ring[-500:]]})

    def _request_events() -> list[LogEvent]:
        return [e for _, e in state.logs.sse.replay("request", 0)]

    @app.get("/admin/stats/overview")
    async def admin_stats_overview(request: Request, minutes: int = 60):
        resp = _require_admin(request)
        if resp:
            return resp
        minutes = max(0, min(minutes, 43200))
        sink = state.logs.db_sink
        if sink is not None and (minutes == 0 or minutes > 1440):
            return ORJSONResponse(await sink.read_overview(minutes))
        minutes_ring = minutes if minutes > 0 else 1440
        return ORJSONResponse(stats_mod.overview(_request_events(), minutes_ring))

    @app.get("/admin/stats/timeseries")
    async def admin_stats_timeseries(request: Request, bucket: str = "minute",
                                     metric: str = "tokens", minutes: int = 60):
        resp = _require_admin(request)
        if resp:
            return resp
        try:
            minutes = max(0, min(minutes, 43200))
            sink = state.logs.db_sink
            if sink is not None and (minutes == 0 or minutes > 1440):
                bs = stats_mod.bucket_size_for(minutes)
                return ORJSONResponse(
                    await sink.read_timeseries(bs, metric, minutes))
            minutes_ring = minutes if minutes > 0 else 1440
            return ORJSONResponse(
                stats_mod.timeseries(_request_events(), bucket, metric, minutes_ring))
        except ValueError as e:
            return _err(400, "invalid_request_error", str(e), request)

    # -- admin: model pricing -----------------------------------------------------
    @app.get("/admin/pricing")
    async def admin_pricing(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        prices = state.cost.prices
        out: list[dict[str, Any]] = []
        for model_id, p in sorted(prices.items()):
            entry: dict[str, Any] = {
                "model_id": model_id,
                "input_per_1m": round(p.get("input_cost_per_token", 0) * 1_000_000, 6),
                "output_per_1m": round(p.get("output_cost_per_token", 0) * 1_000_000, 6),
            }
            if "cache_read_input_cost_per_token" in p:
                entry["cache_read_per_1m"] = round(p["cache_read_input_cost_per_token"] * 1_000_000, 6)
            if "max_input_tokens" in p:
                entry["max_input_tokens"] = p["max_input_tokens"]
            if "max_output_tokens" in p:
                entry["max_output_tokens"] = p["max_output_tokens"]
            if "mode" in p:
                entry["mode"] = p["mode"]
            out.append(entry)
        return ORJSONResponse({"models": out})

    # -- admin: alert rules (storage only; evaluation engine is post-MVP) ----------
    @app.get("/admin/alert-rules")
    async def admin_get_alert_rules(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        return ORJSONResponse({"rules": state.alert_rules})

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
        if state.config_store:
            await state.config_store.set_setting("alert_rules", rules)
        await state.logs.log_audit(actor="master", action="alert_rules.update",
                                   target="*", diff={"count": len(rules)})
        return ORJSONResponse({"rules": state.alert_rules})

    # -- admin UI (built SPA; wiwi/server/static produced by `cd web && bun run build`)
    static_dir = Path(os.environ.get("WIWI_STATIC_DIR")
                      or Path(__file__).parent / "static")

    class SPAStaticFiles(StaticFiles):
        """StaticFiles with SPA history fallback: unknown paths get index.html
        so client-side routes like /admin/ui/login survive hard refresh."""

        async def get_response(self, path: str, scope):
            try:
                resp = await super().get_response(path, scope)
            except HTTPException as exc:
                if exc.status_code == 404:
                    return await super().get_response("index.html", scope)
                raise
            if resp.status_code == 404:
                return await super().get_response("index.html", scope)
            return resp

    if static_dir.is_dir():
        app.mount("/admin/ui", SPAStaticFiles(directory=str(static_dir), html=True),
                  name="admin-ui")

    return app


def create_app_from_config_path(config_path: str = "wiwi.yaml") -> FastAPI:
    """Factory used by uvicorn reload mode.

    Uvicorn's reloader re-imports the app in a fresh subprocess on each
    restart, so the app cannot be passed as an object — it must be built
    from the config path each time.
    """
    # Reload re-imports in a fresh subprocess that bypasses main.py, so load
    # .env here too — otherwise DATABASE_URL and provider keys would be missing.
    load_env()
    try:
        config = load_config(config_path)
    except ConfigError as e:
        print(f"wiwi: config error: {e}", file=sys.stderr)
        sys.exit(1)
    return create_app(config)
