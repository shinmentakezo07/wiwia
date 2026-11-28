"""FastAPI app factory: middleware chain, the three surfaces, admin API, health."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
import uuid as _uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import orjson
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _parse_session_cookie(headers: list[tuple[bytes, bytes]]) -> str:
    """The raw ``wiwi_session`` cookie value from raw ASGI headers, or ''.

    Kept at module level (not a method on the middleware) so the renewal
    helper below can use it without dragging the middleware's state along.

    The value is taken in *order*: when a client carries a jar with the
    freshly-issued cookie alongside an explicit per-request one, the jar's
    (newer) value may come *after* the stale one. ``verify_session`` and
    the login path both work off the last value presented, so the renewal
    must too — otherwise the helper verifies the stale twin, finds it
    past half-life, and re-signs a cookie the client already considers
    superseded. The starlette ``Request`` cookie parse used by
    ``resolve_current_user`` reads the same way (later pairs win), which
    teams' round-193 tests confirmed with ``httpx``'s cookie jar.
    """
    value = ""
    for k, v in headers:
        if k != b"cookie":
            continue
        for part in v.decode("latin-1").split(";"):
            part = part.strip()
            if part.startswith("wiwi_session="):
                value = part.split("=", 1)[1].strip()
    return value


def _maybe_renew_session_cookie(headers: list[tuple[bytes, bytes]]) -> str:
    """A renewal ``Set-Cookie`` value for the session cookie, or ''.

    Pure function over raw header bytes so the middleware stays testable
    without an app: it decides *whether* to renew; the caller renders the
    header. The secret is read from ``WIWI_SESSION_SECRET`` at call time —
    the same value ``AppState.init_db`` derives the session secret from
    (``os.environ.get("WIWI_SESSION_SECRET") or master_key``) — because the
    middleware is built before the lifespan runs, so no app state is
    reachable from here.

    Renewal happens only when:

    * a ``wiwi_session`` cookie is present (bearer / logged-out callers get
      nothing — there is no session row to resurrect);
    * it verifies (an expired cookie verifies to ``None`` and is therefore
      never resurrectable — the 401 path is unchanged);
    * it is past ``SESSION_RENEW_AFTER_S`` (a fresh cookie is left alone —
      re-signing every response would be a cookie storm).
    """

    raw = _parse_session_cookie(headers)
    if not raw:
        return ""
    secret = os.environ.get("WIWI_SESSION_SECRET") or _RENEW_MASTER_KEY[0]
    if not secret:
        return ""
    parsed = verify_session(secret, raw)
    if parsed is None:
        return ""
    uid, role, expires = parsed
    now = time.time()
    if expires - now > SESSION_RENEW_AFTER_S:
        return ""  # still fresh — leave the jar alone
    renewed = sign_session(secret, uid, role, expires=now + SESSION_TTL)
    # Match the cookie attributes _set_session_cookie mints with, including
    # the Secure flag. The middleware cannot see the trusted-proxy gate the
    # mint path uses (no request scope object carries it), so derive the
    # flag the same way the app does for the in-process test and the
    # X-Forwarded-Proto header it receives.
    return renewed


#: Populated by ``create_app`` (module-level mutable so the pure renewal
#: helper above can reach it without an app instance). Empty until then —
#: and ``_maybe_renew_session_cookie`` refuses to mint anything while it is.
_RENEW_MASTER_KEY: list[str] = []


class RequestIdMiddleware:
    """Pure ASGI middleware: request ID, body-size guard, latency header,

    Replaces the previous ``@app.middleware("http")`` (BaseHTTPMiddleware)
    which wraps every response — including StreamingResponse — in a
    background task pumping chunks through an internal anyio memory stream.
    When uvicorn cancels in-flight tasks during graceful shutdown, that pump
    is cancelled mid-flight and raises WouldBlock/CancelledError.  A pure
    ASGI middleware passes streaming responses through untouched, so shutdown
    cancellation simply ends the response with no error traceback.
    """

    #: Paths whose bodies legitimately exceed ``max_request_body_mb``.
    #: Restoring a backup posts the whole document in one request, and a
    #: configuration-plus-logs file is routinely larger than the 50 MiB default
    #: would allow. The exemption is bounded, not open-ended — see
    #: ``_backup_import_limit_mb`` — so an admin cannot OOM the gateway.
    UNLIMITED_BODY_PATHS: frozenset[str] = frozenset({"/admin/backup/import"})

    def __init__(self, app: ASGIApp, *, max_body_mb: float) -> None:
        self.app = app
        self.max_body_bytes = int(max_body_mb * 1024 * 1024)
        self.unlimited_body_paths = self.UNLIMITED_BODY_PATHS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Normalize /V1/ → /v1/ for API routes — many clients and proxies send
        # uppercase path prefixes; FastAPI route matching is case-sensitive.
        path = scope.get("path", "")
        exempt = path in self.unlimited_body_paths
        if path[:4] == "/V1/" or path == "/V1":
            scope["path"] = path[:1] + "v1" + path[3:]
            # raw_path follows the same pattern
            rp = scope.get("raw_path")
            if isinstance(rp, bytes) and rp[:4] == b"/V1/":
                scope["raw_path"] = b"/v1/" + rp[4:]
            elif isinstance(rp, bytes) and rp == b"/V1":
                scope["raw_path"] = b"/v1"

        rid = _uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = rid
        t0 = time.monotonic()

        # --- body-size guard (early 413) -------------------------------------
        cl = None
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                cl = v
                break
        if (cl is not None and cl.isdigit()
                and int(cl) > self.max_body_bytes and not exempt):
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
        # The session cookie is renewed here (sliding renewal, round 193):
        # any response — JSON, SSE, or an error envelope — carries a
        # freshly-signed wiwi_session once the cookie is past half its TTL,
        # so an active Playground user never hits the 7-day hard expiry
        # while they are active. See ``_maybe_renew_session_cookie``.
        req_headers = list(scope.get("headers", []))
        secure_cookie = (
            scope.get("scheme") == "https"
            or any(k == b"x-forwarded-proto" and v.strip().lower() == b"https"
                   for k, v in req_headers)
        )

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
                # --- sliding session renewal (round 193) ---------------
                # Don't stack a second cookie on a response the handler
                # already set one on (logout clears, login mints).
                has_session_cookie = any(
                    k == b"set-cookie" and v.startswith(b"wiwi_session=")
                    for k, v in headers)
                if not has_session_cookie:
                    renewed = _maybe_renew_session_cookie(req_headers)
                    if renewed:
                        secure_flag = b"; Secure" if secure_cookie else b""
                        headers.append((
                            b"set-cookie",
                            b"wiwi_session=" + renewed.encode("latin-1")
                            + b"; Max-Age=" + str(int(SESSION_TTL)).encode()
                            + b"; Path=/; HttpOnly; SameSite=lax"
                            + secure_flag))
                message["headers"] = headers
            await send(message)

        # --- streamed / chunked body guard -----------------------------------
        # Content-Length only covers bodies that declare a length. Chunked and
        # HTTP/2 bodies don't, so counting received bytes is the only way to
        # enforce max_request_body_mb for them.
        async def receive_limited() -> Message:
            nonlocal seen_bytes, body_too_large
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"") or b""
                seen_bytes += len(chunk)
                if seen_bytes > self.max_body_bytes and not exempt:
                    body_too_large = True
                    # Flag the overage and stop growing the buffer, but keep
                    # the bytes we already have: json_body measures them to
                    # answer 413. Truncating to b"" hid the overage from it.
                    scope.setdefault("state", {})["body_too_large"] = True
                    return {"type": "http.request", "body": b"",
                            "more_body": False}
            return message

        seen_bytes = 0
        body_too_large = False
        await self.app(scope, receive_limited, send_wrapper)



from wiwi.auth.service import AuthService
from wiwi.auth.spend_buffer import SpendBuffer
from wiwi.auth.users import (
    SESSION_RENEW_AFTER_S,
    SESSION_TTL,
    UserInfo,
    UserService,
    sign_session,
    verify_session,
)
from wiwi.cache import build_response_cache
from wiwi.cache.hit_rebase import rebase_cached_body
from wiwi.cache.interface import CacheBackend, CacheEntry
from wiwi.cache.keygen import is_cacheable_request, response_cache_key
from wiwi.cache.singleflight import FlightResult, SingleFlight
from wiwi.config import (
    PROVIDER_TYPES,
    ConfigError,
    ModelAliasEntry,
    WiwiConfig,
    coerce_alias_value,
    load_config,
    load_env,
)
from wiwi.core.context import RequestContext, context_of
from wiwi.core.gateway import (
    Gateway,
    build_log_event,
)
from wiwi.core.recovery import (
    HealthHealer,
)
from wiwi.core.telemetry import tracer
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.logging_core.subsystem import LoggingSubsystem
from wiwi.providers.base import WiwiError
from wiwi.providers.nim_tool_schema import configure_schema_cache
from wiwi.ratelimit.memory import RateLimiter
from wiwi.server.dependencies import (
    SYNTHETIC_MASTER_ID,
    ORJSONResponse,
    bearer_token,
    resolve_current_user,
)
from wiwi.server.errors import APIError
from wiwi.server.errors import render as render_error
from wiwi.server.errors import validation_message as _validation_message
from wiwi.server.routers import admin_backup as admin_backup_router
from wiwi.server.routers import admin_cline_settings as admin_cline_settings_router
from wiwi.server.routers import admin_keys as admin_keys_router
from wiwi.server.routers import admin_logs as admin_logs_router
from wiwi.server.routers import admin_models as admin_models_router
from wiwi.server.routers import admin_pricing as admin_pricing_router
from wiwi.server.routers import admin_providers as admin_providers_router
from wiwi.server.routers import admin_stats as admin_stats_router
from wiwi.server.routers import admin_users as admin_users_router
from wiwi.server.routers import auth as auth_router
from wiwi.server.routers import cline_oauth as cline_oauth_router
from wiwi.server.routers import hermes_oauth as hermes_oauth_router
from wiwi.server.routers import public as public_router
from wiwi.server.routers import realtime as realtime_router
from wiwi.server.routers import system as system_router
from wiwi.server.routers import v1_api as v1_api_router
from wiwi.server.services.key_admin import KeyAdminService
from wiwi.streaming.machine import DeltaStreamState
from wiwi.streaming.sse import split_frames


class _SerializedAsyncEngine:
    """AsyncEngine stand-in that serializes every checkout through one lock.

    Only used for the plain in-memory SQLite URL, where SQLAlchemy collapses
    the pool to StaticPool. StaticPool hands out the same raw aiosqlite
    connection for every checkout, which is not safe for concurrent use: a
    second checkout shares the connection of a checkout already inside a
    transaction, and the implicit rollback issued on the overlapping close
    silently discards the first transaction's uncommitted work. Serializing
    here keeps the same single-connection behavior but makes interleaved use
    impossible. Everything except ``connect()``/``begin()`` (notably
    ``dialect``, ``dispose()``, ``execution_options()``) delegates through.
    Production file-backed SQLite and Postgres never see this wrapper.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    async def __aenter__(self) -> _SerializedAsyncEngine:  # tests do not, but keep it honest  # noqa: PYI034
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def connect(self) -> Any:
        return _SerializedCheckout(self._lock, self._engine.connect())

    def begin(self) -> Any:
        return _SerializedCheckout(self._lock, self._engine.begin())


class _SerializedCheckout:
    """Async context manager that holds the engine lock for the checkout."""

    def __init__(self, lock: asyncio.Lock, ctx: Any) -> None:
        self._lock = lock
        self._ctx = ctx

    async def __aenter__(self) -> Any:
        await self._lock.acquire()
        try:
            return await self._ctx.__aenter__()
        except BaseException:
            self._lock.release()
            raise

    async def __aexit__(self, *exc: object) -> Any:
        try:
            return await self._ctx.__aexit__(*exc)
        finally:
            self._lock.release()
from wiwi.router.router import (
    Deployment,
    ProviderAccount,
    ProviderKey,
    Router,
    _default_base_url,
)
from wiwi.server.config_store import (
    DEPLOYMENT_LIMIT_FIELDS,
    ConfigStore,
)
from wiwi.server.response_store import ResponseStore
from wiwi.streaming.tape_store import JournalStore
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_completions as ocmpl
from wiwi.wire import openai_responses as orp

# The synthetic master actor (``current_user`` mints it for a master bearer or
# a master session cookie). Real user ids are "u" + 16 hex, so this value can
# only ever name the synthetic actor, never a row in ``users``.
#
# The definition lives in ``wiwi/server/dependencies.py`` so the routers can
# share it; it is re-exported here because several call sites (and tests)
# import it from ``wiwi.server.app``.
_SYNTHETIC_MASTER_ID = SYNTHETIC_MASTER_ID

# The wrapper holds a plaintext key per owner, so the map needs a ceiling. It
# is deliberately far below the number of accounts a deployment can hold: an
# eviction only costs one extra mint, because the wrapper re-mints and
# re-caches on the next request.
_PG_BEARER_CACHE_MAX = 256


class _PgBearerCache:
    """Bounded, explicitly-invalidated owner -> playground key plaintext map.

    Round 103 shipped this as a bare dict that was only ever written, read,
    and popped by its own revalidation failure. Nothing dropped an entry on
    logout or on the account being disabled, and nothing bounded it, so a
    plaintext credential outlived the session that created it and the dict
    grew monotonically with distinct owner ids (round 105).

    Eviction is LRU on *use*, not on write: an owner actively streaming
    through the wrapper would otherwise be the one evicted.
    """

    def __init__(self, max_entries: int = _PG_BEARER_CACHE_MAX):
        self._max = max_entries
        self._items: dict[str, str] = {}

    def get(self, key: str) -> str:
        val = self._items.get(key, "")
        if val:
            # Re-insert so dict order tracks recency of use.
            self._items.pop(key, None)
            self._items[key] = val
        return val

    def set(self, key: str, plaintext: str) -> None:
        self._items.pop(key, None)  # replace, never duplicate
        self._items[key] = plaintext
        while len(self._items) > self._max:
            self._items.pop(next(iter(self._items)))

    def drop(self, key: str) -> None:
        self._items.pop(key, None)

    def __len__(self) -> int:
        return len(self._items)


_rt_log = structlog.get_logger("wiwi.realtime")

# Process-lifetime realtime loss counters, surfaced on /health next to
# ``spend_charge_failures``. They cannot be derived from the request-log ring:
# a session wiwi could not price is precisely one that is *absent* from it, so
# without a counter the gap between wiwi's books and the upstream's is
# invisible (AUDIT #347). Module-level rather than on ``AppState`` so the
# realtime relay — which runs on both the HTTP and the WebSocket path — has one
# counter regardless of which surface is looking.
_RT_COUNTERS: dict[str, int] = {"usage_missing": 0}

# How long the upstream pump is given to deliver a trailing frame after
# ``max_session_s`` cuts the session. ``response.done`` — the frame that carries
# the turn's usage, and always the LAST frame of that turn — is exactly the one
# a cap firing mid-turn is likely to interrupt, so cancelling both pumps the
# instant the cap fires bills real tokens at $0.
_RT_USAGE_GRACE_S = 0.75


async def _close_quietly(ws) -> None:
    """Close *ws*, tolerating an already-closed peer.

    Both relay directions close the other socket as they finish, so a peer that
    ended first will already be closed here — and Starlette's ``websockets``
    client raises rather than no-op in that case.
    """
    with contextlib.suppress(Exception):
        await ws.close()


def _realtime_usage(text: str) -> tuple[int, int] | None:
    """Extract ``(prompt, completion)`` from a usage-bearing session event.

    Reads defensively: a frame that is not JSON, or that carries no ``usage``,
    yields None instead of raising. A malformed frame must never tear down a
    live audio session — the relay's job is to carry bytes, and failing to bill
    one event under-counts one turn, while raising drops the call.
    """
    try:
        event = orjson.loads(text)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    response = event.get("response")
    usage = event.get("usage")
    if not isinstance(usage, dict) and isinstance(response, dict):
        usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        return (int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0))
    except (TypeError, ValueError):
        return None


async def _realtime_relay(client_ws, upstream_ws, rt_settings) -> tuple[int, int] | None:
    """Pump text frames both ways until either side closes or the cap fires.

    Frames pass through byte-for-byte in both directions. The realtime session
    protocol is stateful and ordered — ``session.update`` mutates server-side
    state that later turns depend on, and conversation items are referenced
    rather than resent — so wiwi reads it and never rewrites it.

    Returns the session's peak ``(prompt, completion)`` pair, or None.
    """
    peak: list[tuple[int, int]] = []

    async def absorb_upstream_frame(frame) -> None:
        """Forward one upstream frame verbatim, remembering any usage it bears.

        ``websockets.connect`` is opened without ``decode=True``, so ``recv()``
        yields ``str | bytes`` — and the realtime protocol is not all-JSON:
        audio responses arrive as binary frames. The two pump directions used
        to disagree about that: the client side iterated ``iter_text()`` while
        the upstream side assumed ``str``. One binary frame therefore raised
        ``TypeError: a bytes-like object is required`` on ``'"usage"' in frame``,
        the blanket ``except`` swallowed it, and the ``finally`` closed the
        *client* socket — a single audio chunk killed a live session, logged at
        ``debug`` where nothing in a default configuration could see it
        (AUDIT #347). Only a ``str`` can carry a JSON usage event.
        """
        if isinstance(frame, (bytes, bytearray, memoryview)):
            await client_ws.send_bytes(bytes(frame))
            return
        # Bill on the peak, never the sum. response.done and the
        # preceding item-done events restate overlapping windows of the
        # same conversation, and a resumed response restates its input,
        # so summing inflates every multi-turn session.
        if '"usage"' in frame:
            got = _realtime_usage(frame)
            if got is not None and (
                    not peak or got[0] + got[1] > peak[0][0] + peak[0][1]):
                peak[:] = [got]
        await client_ws.send_text(frame)

    async def from_client() -> None:
        try:
            async for frame in client_ws.iter_text():
                await upstream_ws.send(frame)
        except Exception as exc:  # noqa: BLE001 — ends this direction only
            # A client disconnecting mid-session is the normal end of a
            # realtime call, not an error worth a stack trace. Recorded as
            # debug so an operator can still see *why* a session ended.
            _rt_log.debug("realtime client stream ended", error=str(exc)[:200])
        finally:
            await _close_quietly(upstream_ws)

    async def from_upstream() -> None:
        try:
            async for frame in upstream_ws:
                await absorb_upstream_frame(frame)
        except Exception as exc:  # noqa: BLE001
            # Raised to info from debug (AUDIT #347): an unexpected end of the
            # upstream direction closes the client socket from under a live
            # session, and at debug it was indistinguishable from silence.
            _rt_log.info("realtime upstream stream ended",
                         error=str(exc)[:200], error_type=type(exc).__name__)
        finally:
            await _close_quietly(client_ws)

    tasks = [asyncio.ensure_future(from_client()),
             asyncio.ensure_future(from_upstream())]
    try:
        await asyncio.wait(tasks, timeout=rt_settings.max_session_s,
                           return_when=asyncio.FIRST_COMPLETED)
        # ``max_session_s`` fired with both directions still live: the turn in
        # flight is cut mid-stream, and the usage event pricing it may be
        # trailing right behind. Give the upstream pump a short grace window
        # before cancelling, or a session cut at the cap bills real tokens at
        # $0 and leaves no row to reconcile (AUDIT #347). Only on the timeout
        # path — when a direction genuinely finished, there is nothing to wait
        # for and holding the session open would be a behaviour change.
        if all(not task.done() for task in tasks):
            await asyncio.wait([tasks[1]], timeout=_RT_USAGE_GRACE_S)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return peak[0] if peak else None


async def _bill_realtime_session(state, info, ctx, dep,
                                 usage: tuple[int, int] | None,
                                 *, status: int = 200,
                                 stop_reason: str = "realtime_session") -> None:
    """Price one realtime session and write its single request-log row.

    Billed on the *peak* usage the upstream reported, not the sum of its events,
    and charged exactly once — at close. Per-event rows would cost a DB write per
    audio-chunk batch and make the request log unreadable; per-turn charges
    would double-count, because the realtime protocol restates overlapping
    windows of the same conversation across several events.

    *usage* is None — or a zero pair — when the session produced no usage
    event at all: the handshake failed or timed out, upstream answered 401/5xx,
    or ``max_session_s`` cut the session before a ``response.done``. This used
    to ``return`` *before* the log write, and the route additionally gated the
    whole call on ``if usage is not None``, so those sessions left no row, no
    status, no stop reason and no counter (AUDIT #347). A key whose only
    traffic was realtime then showed a permanently flat ``spend_to_date`` and
    an empty Request Logs tab, with nothing to reconcile against the upstream
    bill. Every session that passed admission is now logged; the missing usage
    is itself the thing an operator has to be able to see. Every HTTP surface
    has always logged unconditionally.
    """
    ctx.deployment = dep
    ctx.status = status
    ctx.stop_reason = stop_reason
    prompt_tokens, completion_tokens = usage if usage is not None else (0, 0)
    if prompt_tokens <= 0 and completion_tokens <= 0:
        # Nothing to price, but the session happened. The zero counts are
        # marked *estimated*: they are zero because nothing was reported, not
        # because the provider said zero, and a spend report must never present
        # the latter as fact (AUDIT #131).
        ctx.usage = ir.Usage(prompt_tokens=0, completion_tokens=0, estimated=True)
        _RT_COUNTERS["usage_missing"] += 1
        _rt_log.warning("realtime session closed with no usage",
                        request_id=ctx.request_id, status=status,
                        key_id=getattr(info, "key_id", ""),
                        model_group=ctx.group)
        state.logs.log_request(build_log_event(ctx))
        return
    model_key = f"{dep.provider.provider_type}/{dep.model_id}"
    priced = state.cost.cost(
        model_key, prompt_tokens, completion_tokens,
        provider_type=dep.provider.provider_type,
        provider_name=dep.provider.name)
    ctx.usage = ir.Usage(prompt_tokens=prompt_tokens,
                         completion_tokens=completion_tokens)
    dep.settle_tokens(ctx.request_id, prompt_tokens + completion_tokens)
    ctx.cost = priced
    if state.auth is not None:
        # Charge the virtual key for real tokens consumed. This used to call
        # ``state.auth.record_spend(...)`` — a method AuthService never had
        # (its real surface is ``update_spend`` / ``apply_spend_trueup``), so
        # every call raised AttributeError and the surrounding
        # ``contextlib.suppress`` swallowed it: a session was priced, logged and
        # shown in the console, but spend_to_date never moved, so a
        # budget-capped key accrued realtime cost for free and its cap never
        # tripped (AUDIT #345). Use the unconditional true-up, matching what
        # ``record_spend`` applies for a request that already crossed its cap:
        # the upstream really served these tokens, so the cap must not reject
        # them; it only blocks FUTURE requests.
        try:
            await state.auth.apply_spend_trueup(info.key_id, priced)
        except Exception as e:  # noqa: BLE001 — accounting must never break a served session
            # Loud, counted, and never swallowed silently (AUDIT #179/#345): a
            # realtime session that cannot be charged is money the budget cap
            # is not enforcing, and it must be visible in /health and /metrics.
            state.spend_charge_failures += 1
            _rt_log.error("realtime_spend_charge_failed", key_id=info.key_id,
                          cost=priced, error=type(e).__name__, detail=str(e))
    state.logs.log_request(build_log_event(ctx))


class _AttemptThrottle:
    """Fixed-window attempt counter for abuse-prone endpoints.

    Deliberately not built on RateLimiter: that reserves a slot on every
    check (including successes), whereas a login throttle must only count
    *failures*, so genuine users are never locked out by their own traffic.

    Bounded: entries are pruned whenever they fall outside the window, and
    the whole map is capped so a flooding attacker cannot grow it without end.
    """

    def __init__(self, limit: int, window_s: float = 300.0, max_keys: int = 10_000):
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._hits: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def try_consume(self, scope: str) -> tuple[bool, int]:
        """Atomically admit one attempt, or refuse it.

        Reserving and counting in a *single* lock acquisition is the whole
        point. The old ``check()`` then ``record_failure()`` pair took the lock
        twice with the entire credential verification (a 200k-iteration PBKDF2)
        in between, so N concurrent requests all passed ``check`` before any of
        them recorded — a burst of 20 against ``limit=5`` admitted all 20
        (AUDIT #221). Callers reserve *before* verifying and ``reset(scope)`` on
        success, so only genuine failures consume the window.
        """
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_s
            evs = [t for t in self._hits.get(scope, ()) if t > cutoff]
            if len(evs) >= self.limit:
                retry = int(max(1.0, self.window_s - (now - evs[0]))) + 1
                self._hits[scope] = evs
                return False, retry
            evs.append(now)
            self._hits[scope] = evs
            if len(self._hits) > self.max_keys:
                self._drop_stale(now)
            return True, 0

    async def refund(self, scope: str) -> None:
        """Give back one reservation taken by :meth:`try_consume`.

        Used when the attempt turned out to be legitimate (a successful login)
        so a busy-but-valid user does not exhaust their own window.
        """
        async with self._lock:
            evs = self._hits.get(scope)
            if evs:
                evs.pop()
                if not evs:
                    self._hits.pop(scope, None)

    async def check(self, scope: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds) for the given scope.

        Not used on the request path (``try_consume`` is the AUDIT #221
        replacement) but pinned by ``tests/test_fix_round9.py`` — delete the
        tests with it, never the method alone.
        """
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_s
            evs = [t for t in self._hits.get(scope, ()) if t > cutoff]
            if len(evs) >= self.limit:
                retry = int(max(1.0, self.window_s - (now - evs[0]))) + 1
                self._hits[scope] = evs
                return False, retry
            self._hits[scope] = evs
            if len(self._hits) > self.max_keys:
                self._drop_stale(now)
            return True, 0

    async def record_failure(self, scope: str) -> None:
        async with self._lock:
            now = time.monotonic()
            evs = [t for t in self._hits.get(scope, ()) if t > now - self.window_s]
            evs.append(now)
            self._hits[scope] = evs

    async def reset(self, scope: str) -> None:
        async with self._lock:
            self._hits.pop(scope, None)

    def _drop_stale(self, now: float) -> None:
        cutoff = now - self.window_s
        for k in [k for k, v in self._hits.items()
                  if not v or v[-1] <= cutoff]:
            self._hits.pop(k, None)
        # Still over the cap: drop the oldest-touched entries outright rather
        # than letting the map grow unbounded.
        if len(self._hits) > self.max_keys:
            for k in sorted(self._hits, key=lambda k: self._hits[k][-1]
                            if self._hits[k] else 0.0)[:self.max_keys // 10 or 1]:
                self._hits.pop(k, None)


_SECRET_QUERY_KEYS = frozenset({
    "key", "api_key", "apikey", "access_token", "token", "auth",
})


def _redact_url_secret(url: str) -> str:
    """Strip credential-bearing query params so a URL is safe to log or return.

    Gemini (and some compatible gateways) carry the API key in the querystring,
    so the request URL itself is a secret — echoing it in an error body would
    hand the key to the caller.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = [(k, "***" if k.lower() in _SECRET_QUERY_KEYS else v)
             for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(pairs), parts.fragment))


def _provider_models_url(provider_type: str, base_url: str) -> str:
    """Upstream model-list endpoint per provider type."""
    base = (base_url or _default_base_url(provider_type)).rstrip("/")
    return f"{base}/models"  # openai/compatible, anthropic, and gemini share the path


# Alias-chain walk bound. Must match `Router.resolve_group` (router.py) so a
# legal-but-truncated chain can't degrade silently at runtime — if this number
# changes in the router, update the validation below and the bound there too.
_ALIAS_CHAIN_MAX_HOPS = 8


def _alias_first_hop(entry_map: dict, name: str) -> str | ModelAliasEntry | None:
    """Look up the entry the client typed (first hop only). Returns
    ``None`` if the name is not a direct alias. Used to decide whether the
    response should echo the alias or show the resolved group."""
    return entry_map.get(name)


# Bound on the caller-visible translation-warning header. A request can drop
# several things (a builtin tool, a sampling param, an attachment), and each
# message is a sentence; the header must stay small enough that the HTTP
# response headers remain legal. The full list is always in the request log
# (``LogEvent.translation_warnings``), so truncation loses nothing that is
# not recoverable.
_MAX_WARNING_HEADER = 900


def _warning_header_value(raw: object) -> str:
    """Join a metadata warning list into one legal header value, or ``""``.

    Entries are filtered to non-empty strings because the list is written by
    several producers (adapter warnings, the tool-arg validator) and a header
    built from a non-string would raise inside the response path.

    The result is additionally forced into latin-1: Starlette encodes response
    headers with that codec at construction time, so any other character raises
    ``UnicodeEncodeError`` BEFORE a byte is sent — after the upstream was billed
    and (non-streaming) after ``record_spend``, converting a successful turn
    into a 500 with a double-billed retry behind it. Two producers could hit
    that: the truncation marker used to be U+2026, and a model/tool name inside
    an adapter warning can carry any Unicode the operator typed. The ellipsis
    is ASCII ``...`` and everything else is coerced per character (U+FFFD-free:
    latin-1's 256 code points are a straight low-byte truncation).
    """
    if not isinstance(raw, list):
        return ""
    msgs = [w for w in raw if isinstance(w, str) and w]
    if not msgs:
        return ""
    value = "; ".join(msgs)
    if len(value) > _MAX_WARNING_HEADER:
        value = value[:_MAX_WARNING_HEADER - 3] + "..."
    # Headers are latin-1 on the wire (Starlette encodes at response build
    # time). Coerce rather than let the response constructor raise.
    return value.encode("latin-1", "replace").decode("latin-1")


def _with_warnings(headers: dict[str, str], ctx: RequestContext) -> dict[str, str]:
    """Add the caller-visible advisory warnings to a response's headers.

    Two channels, both built for exactly this — a construct the caller asked
    for that the request could not honour as stated:

    * ``translation_warnings`` — the provider cannot express it (a hosted tool
      the backend cannot run, a ``strict`` mode it cannot enforce, a sampling
      param it has no field for). Until AUDIT #498 these went to the *operator*
      only, so the caller, who set the constraint and pays for the request,
      learned nothing.
    * ``tool_args_violations`` — the model emitted a tool call whose arguments
      do not match the schema the caller declared. Advisory: the call is still
      delivered, because agents handle their own errors, but an agent that
      validates what it receives needs to know the arguments it was handed are
      malformed. Written by the gateway since round 20 and read by nothing until
      AUDIT #536.

    The header is the only way to tell the caller: the response body is
    dialect-owned, and an OpenAI-shaped body cannot carry an Anthropic-shaped
    note without breaking the client's schema expectations. Headers are
    additive and every HTTP client surfaces them. Both are absent when empty, so
    a clean request's response is byte-identical to what it was before.
    """
    out = dict(headers)
    for key, header in (("translation_warnings", "x-wiwi-translation-warnings"),
                        ("tool_args_violations", "x-wiwi-tool-args-warnings")):
        value = _warning_header_value(ctx.metadata.get(key))
        if value:
            out[header] = value
    return out


def _validate_alias_batch(current: dict, set_map: object, unset: object,
                          groups: dict, provider_aliases: dict[str, str]
                          ) -> tuple[dict | None, str | None]:
    """Validate an admin alias update batch.

    Each value in ``set_map`` may be a plain string (legacy) or a
    ``ModelAliasEntry``-shaped dict (rich form, modelled on shinway's
    ``OAuthModelAlias``). The live map type is therefore
    ``dict[str, str | ModelAliasEntry]``.

    Returns ``(new_live_map, None)`` on success or ``(None, error_message)`` on
    the first violation. Validation order: type/shape, then shadow / provider
    alias / self-loop rejections (so the error messages are most informative),
    then cycle/depth checks against the merged map.

    Atomicity: if any single entry in the batch is invalid, the whole batch
    is rejected and the live map is not touched.
    """
    live: dict = dict(current)
    if not isinstance(unset, list):
        return None, "'unset' must be an array of alias keys"
    for k in unset:
        if not isinstance(k, str) or not k:
            return None, "alias keys must be non-empty strings"
        if k not in live:
            return None, f"unknown alias '{k}'"
        live.pop(k)
    if not isinstance(set_map, dict):
        return None, "'set' must be an object mapping alias -> target"
    # Track keys whose values need to be re-coerced (dict input → ModelAliasEntry)
    # so the live map holds normalized types, not raw dicts.
    coerced: dict[str, str | ModelAliasEntry] = {}
    for k, raw in set_map.items():
        if not isinstance(k, str) or not k.strip():
            return None, "alias keys must be non-empty strings"
        if k in groups:
            return None, (f"alias '{k}' shadows model group '{k}' — "
                          "pick a different alias name")
        if k in provider_aliases:
            return None, (f"alias '{k}' collides with provider alias_id"
                          f" '{provider_aliases[k]}' — provider aliases win"
                          " in resolve_group, so this entry would be dead")
        if isinstance(raw, str):
            if not raw.strip():
                return None, "alias targets must be non-empty strings"
            if raw == k:
                return None, f"alias '{k}' points to itself"
            coerced[k] = raw
            continue
        # Rich form: dict or ModelAliasEntry.
        # Pydantic v2 treats bools as int-subtypes by default, so accept
        # only true booleans (and reject dicts that fail extra="forbid"
        # by re-checking the raw payload shape).
        if isinstance(raw, dict):
            if "target" not in raw:
                return None, f"alias '{k}' is missing required 'target'"
            if "force_mapping" in raw and not isinstance(
                    raw["force_mapping"], bool):
                return None, (f"alias '{k}'.force_mapping must be a"
                              " boolean")
            if "display_name" in raw and raw["display_name"] is not None \
                    and not isinstance(raw["display_name"], str):
                return None, (f"alias '{k}'.display_name must be a"
                              " string or null")
            if "fork" in raw and not isinstance(raw["fork"], bool):
                return None, f"alias '{k}'.fork must be a boolean"
        entry = coerce_alias_value(raw)
        if entry is None:
            return None, (f"alias '{k}' has an invalid value; expected a"
                          f" string or {{target, force_mapping?,"
                          f" display_name?, fork?}}")
        if isinstance(entry, ModelAliasEntry):
            if not entry.target or not entry.target.strip():
                return None, f"alias '{k}' has empty target"
            if entry.target == k:
                return None, f"alias '{k}' points to itself"
            if entry.fork:
                return None, (f"alias '{k}' uses fork=true, which is not"
                              " implemented yet")
            coerced[k] = entry
        else:
            if not entry.strip():
                return None, "alias targets must be non-empty strings"
            if entry == k:
                return None, f"alias '{k}' points to itself"
            coerced[k] = entry
    live.update(coerced)
    # Cycle + depth check on every touched key against the merged map.
    for k in set_map:
        seen: set[str] = set()
        name = k
        for _ in range(_ALIAS_CHAIN_MAX_HOPS + 1):
            if name in seen:
                return None, f"alias cycle detected starting at '{k}'"
            seen.add(name)
            entry = live.get(name)
            if entry is None:
                break
            nxt = entry.target if isinstance(entry, ModelAliasEntry) else entry
            if nxt == name:
                break
            name = nxt
        else:
            return None, (f"alias chain from '{k}' exceeds"
                          f" {_ALIAS_CHAIN_MAX_HOPS} hops")
    return live, None


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


async def _apply_cline_default_models(state, ids: list[str]) -> dict:
    """Reconcile the router for a list of Cline default model ids.

    For every Cline account in ``state.router.providers`` and every
    ``model_id`` in ``ids``, ensure a Deployment exists in the group
    ``cline:<model_id>``.  Idempotent: existing deployments are not
    duplicated.  Calls ``rebuild_cross_provider_pools()`` so the
    cross-account WRR cursor is built/updated.  Returns counts for
    audit logging.
    """
    applied = 0
    skipped = 0
    for mid in ids:
        gname = f"cline:{mid}"
        for acct in state.router.providers.values():
            if acct.provider_type != "cline":
                continue
            existing = state.router.groups.get(gname, [])
            if any(d.provider is acct and d.model_id == mid
                   for d in existing):
                skipped += 1
                continue
            dep = Deployment(group=gname, provider=acct,
                             model_id=mid, weight=1)
            state.router.groups.setdefault(gname, []).append(dep)
            if state.config_store is not None:
                try:
                    await state.config_store.add_deployment(
                        gname, acct.name, mid, 1)
                except Exception as e:  # noqa: BLE001 — DB row may exist
                    # The DB row may already exist (in-memory providers
                    # whose row was never persisted); in-memory state is
                    # the source of truth, so ignore the duplicate insert.
                    import structlog as _sl
                    _sl.get_logger("wiwi.cline_defaults").debug(
                        "cline_default_deployment_dup",
                        group=gname, provider=acct.name, model=mid, err=str(e),
                    )
            applied += 1
    if applied or skipped:
        state.router.rebuild_cross_provider_pools()
    return {"applied": applied, "skipped": skipped}


class AppState:
    def __init__(self, config: WiwiConfig):
        self.config = config
        self.router = Router(config)
        self.cost = CostEngine()
        self.logs = LoggingSubsystem()
        # Playground wrapper key plaintexts, keyed by actor id. Cleared on
        # logout and when an account is disabled.
        self.pg_bearers = _PgBearerCache()
        # Router emits gateway-op proxy events (upstream 5xx, key cooldown,
        # retries, fallback switches) into the LoggingSubsystem proxy ring.
        self.router.log_proxy = self.logs.log_proxy
        rs = config.router_settings
        # Redis-backed limiting when a URL is configured: the in-memory
        # limiter is per-process, so N workers multiply every rpm/tpm cap by N.
        # RedisRateLimiter falls back to memory on an import/connection
        # failure, so a configured-but-unavailable Redis degrades rather than
        # refusing traffic.
        limiter_redis = (os.environ.get("REDIS_URL")
                         or config.general_settings.redis_url)
        if limiter_redis:
            from wiwi.ratelimit.redis import RedisRateLimiter
            self.limiter = RedisRateLimiter(
                limiter_redis, global_rpm=rs.global_rpm,
                global_tpm=rs.global_tpm)
        else:
            self.limiter = RateLimiter(global_rpm=rs.global_rpm,
                                       global_tpm=rs.global_tpm)
        # Abuse throttles for unauthenticated endpoints (login/signup). These
        # count failures only, so real users are never locked out by success.
        self.login_throttle = _AttemptThrottle(limit=10, window_s=300.0)
        self.signup_throttle = _AttemptThrottle(limit=5, window_s=3600.0)
        self.auth: AuthService | None = None
        # Charges ``AuthService.update_spend`` failed to persist (AUDIT #179).
        # A failed write used to be indistinguishable from a successful one —
        # the caller was told the charge landed, so a hard budget cap stayed
        # unenforced with no counter, no /health field and no metric. This is
        # the process-lifetime counter that makes the loss visible.
        self.spend_charge_failures = 0
        # Batched spend settlement (constructed in init_db, after the auth
        # service exists). ``None`` keeps the synchronous two-write path.
        self.spend: SpendBuffer | None = None
        self.gateways: dict[str, Gateway] = {}
        self.alert_rules: list[dict[str, Any]] = []
        self.config_store: ConfigStore | None = None
        self.response_store: ResponseStore | None = None
        self.users: UserService | None = None
        # Pending browser OAuth sessions for every connect flow — Cline's
        # redirect connect, Hermes's device code and WorkBuddy's login:
        # state_token → {kind, provider, created_at, ttl_s}. Each flow stamps
        # its own TTL at creation so one sweep cannot shorten another's window
        # (AUDIT #424: Hermes used to piggyback on the Cline map and its
        # Cline-specific 10-minute rule).
        self.oauth_pending: dict[str, dict[str, Any]] = {}
        # Serializes provider creation so the duplicate-name guard and the
        # insert cannot interleave across their await (AUDIT #373).
        self.provider_create_lock = asyncio.Lock()
        # Serializes user-role/disabled mutations so the last-admin guard's
        # read → count → write cannot interleave with a sibling PATCH. Two
        # concurrent demotions of the two remaining DB admins both read
        # "2 admins", both passed the guard, and both committed — leaving the
        # users table with zero admins (the synthetic master still worked, so
        # nothing noticed until an operator needed a session admin).
        self.user_mutation_lock = asyncio.Lock()
        self.cline_refresh: Any = None
        self.hermes_refresh: Any = None
        self.opencode_refresh: Any = None
        self.log_retention: Any = None
        self.cline_version_refresh: Any = None
        self.healer: Any = None
        # Durable stream journal (restart-safe SSE replay). Created eagerly so
        # handlers can reach it before init_db runs; sweep happens at startup.
        #
        # WIWI_STREAM_JOURNAL_DIR env var overrides config, mirroring how
        # REDIS_URL and DATABASE_URL work. The configured default
        # (``.wiwi/journals``) is RELATIVE, so it resolves against the process
        # CWD — which in the shipped image is ``/app``, owned by root while the
        # process runs as ``USER wiwi``. Nothing could create it there, and the
        # failure surfaced as #380: every virtual-key stream refused 503 while
        # master streams and non-streaming requests worked, so the deployment
        # looked healthy. An empty env value falls back to config, so platforms
        # that inject empty variables (Railway/Render) do not shadow it.
        rjs = config.router_settings
        journal_dir = (os.environ.get("WIWI_STREAM_JOURNAL_DIR")
                       or rjs.stream_journal_dir)
        self.journals = JournalStore(journal_dir, rjs.stream_journal_ttl_s,
                                     rjs.stream_journal_max_bytes)
        # Exact-match response cache (docs/CORE.md §6). Off by default.
        # Backend: memory LRU (default) or Redis when general_settings.redis_url
        # is set — Redis survives restarts and is shared across replicas, but a
        # dict lookup is faster, so memory stays the default for 1 process.
        cs = config.cache_settings
        # REDIS_URL env var overrides config, mirroring how DATABASE_URL works
        # (init_db below). Without this the shipped container — which boots on
        # wiwi.yaml.example — could only be given a Redis URL by mounting a
        # custom config file.
        redis_url = os.environ.get("REDIS_URL") or config.general_settings.redis_url
        self.response_cache: CacheBackend | None = (
            build_response_cache(cs, redis_url)
            if cs.enabled else None)
        # Concurrent identical non-streaming requests share one upstream call
        # when enabled. Off by default: it changes billing (one charge for N
        # served requests) and should be an explicit operator choice.
        ws = config.wiwi_settings
        self.singleflight: SingleFlight | None = (
            SingleFlight(max_flights=ws.singleflight_max_flights,
                         max_waiters=ws.singleflight_max_waiters)
            if ws.singleflight_enabled else None)
        # Set during shutdown so long-lived SSE generators break out of their
        # event loop instead of blocking uvicorn's graceful-shutdown drain
        # (which otherwise hangs at "Waiting for connections to close").
        self.shutdown_event: asyncio.Event | None = None

    def evict_expired_pending(self, now: float | None = None) -> None:
        """Drop every expired pending OAuth session, whatever its flow.

        The three browser flows share :attr:`oauth_pending` but not a TTL, so
        the rule lives on the session (``ttl_s``, stamped at creation) rather
        than in the sweep. A new flow therefore needs no new map and cannot
        inherit another provider's eviction policy (AUDIT #424).
        """
        now = time.time() if now is None else now
        for tok, sess in list(self.oauth_pending.items()):
            if now - sess.get("created_at", now) > sess.get("ttl_s", 600.0):
                self.oauth_pending.pop(tok, None)

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
        gs = self.config.general_settings
        engine_kwargs: dict[str, Any] = {"url": url}
        if is_pg:
            engine_kwargs.update(
                pool_size=max(1, gs.db_pool_size),
                max_overflow=max(0, gs.db_max_overflow),
                pool_pre_ping=True,
                pool_recycle=gs.db_pool_recycle_s,
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
                cursor.execute("PRAGMA busy_timeout=5000")
                # WAL lets the log pump, spend settlement and admin reads
                # proceed concurrently instead of serializing every writer
                # behind the default rollback journal. ``synchronous=NORMAL``
                # is the standard WAL pairing: durable across process crashes
                # (the journal is replayed), trading only an OS-crash window
                # that the default already cannot protect against without
                # fsync-per-commit. Skipped for in-memory databases, where
                # neither setting means anything.
                if not url.endswith(":///:memory:"):
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
        if url.endswith(":///:memory:"):
            # The plain ":memory:" URL collapses the pool to StaticPool, which
            # is not safe to checkout concurrently: a second checkout (e.g. a
            # request-log reader) would share the raw aiosqlite connection the
            # log pump holds mid-transaction, and its implicit rollback on an
            # overlapping close would silently discard the pump's uncommitted
            # batch — no error, no failure counter. Serialize every checkout
            # through one asyncio lock instead.
            aengine = _SerializedAsyncEngine(aengine)
        self._db_engine = aengine
        self.auth = AuthService(
            aengine, gs.master_key, gs.max_keys_per_user,
            shared_database=gs.shared_database,
            reservation_stale_s=gs.reservation_stale_s)
        await self.auth.startup()
        ws = self.config.wiwi_settings
        if ws.spend_batching_enabled:
            self.spend = SpendBuffer(
                self.auth,
                interval_s=max(0.01, ws.spend_flush_interval_ms / 1000.0),
                max_pending=ws.spend_flush_max_pending,
                max_dollars_per_key=ws.spend_flush_max_dollars_per_key)
            # Admin reads flush first so operators see current spend.
            self.auth.spend_buffer = self.spend
        # User accounts + signed session cookies. The session signing secret
        # is WIWI_SESSION_SECRET when provided; otherwise it is derived from
        # the master key (or a fixed default if even that is absent).
        mk = self.config.general_settings.master_key or ""
        # Fail closed when no secret is configured. Falling back to a hardcoded
        # constant meant anyone could forge an admin session cookie: the secret
        # is the only thing protecting `current_user()`'s uid == "master" branch.
        session_secret = os.environ.get("WIWI_SESSION_SECRET") or mk
        if not session_secret:
            raise RuntimeError(
                "no session secret configured: set WIWI_MASTER_KEY (or "
                "WIWI_SESSION_SECRET). Refusing to start with a default "
                "secret, which would allow forged admin sessions.")
        if not os.environ.get("WIWI_SESSION_SECRET") and mk:
            import structlog as _sl
            _sl.get_logger("wiwi.startup").info(
                "session_secret_derived_from_master_key")
        self.users = UserService(aengine, session_secret)
        await self.users.startup()
        from wiwi.logging_core.db_sink import DBSink
        self._db_sink = DBSink(aengine)
        await self._db_sink.startup()
        self.logs.set_db_sink(self._db_sink)
        # Bound request_logs: roll the doomed rows into request_rollups, then
        # delete them, so aggregates stay complete while per-request detail is
        # capped. This used to run once here, which meant a long-running server
        # never pruned at all — a periodic sweep now owns it (see
        # wiwi/logging_core/retention.py). The first pass is on a short delay,
        # so it still happens promptly after a restart.
        if self._db_sink is not None:
            from wiwi.logging_core.retention import LogRetention
            self.log_retention = LogRetention(self)
            self.log_retention.start()
        # Persist admin-added providers/keys/deployments so they survive restart
        self.config_store = ConfigStore(aengine)
        await self.config_store.startup()
        if self.config.wiwi_settings.store_responses:
            self.response_store = ResponseStore(
                aengine, self.config.wiwi_settings.response_store_ttl_s)
            await self.response_store.startup()
        await self._load_db_config()
        ws = self.config.wiwi_settings
        configure_schema_cache(ws.tool_schema_cache_entries)
        self.gateways = {
            "chat": Gateway(
                self.router, self.cost,
                drop_params=ws.drop_params,
                http_limits=httpx.Limits(
                    max_connections=max(1, ws.http_max_connections),
                    max_keepalive_connections=max(
                        0, ws.http_max_keepalive_connections),
                    keepalive_expiry=max(1.0, ws.http_keepalive_expiry_s))),
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
            if p.get("detached"):
                # Tombstone: an operator renamed or removed this account, and a
                # YAML entry of the same name must not bring it back (AUDIT
                # #319).
                self.router.providers.pop(p["name"], None)
                continue
            if p["provider_type"] not in PROVIDER_TYPES:
                # Re-validate what the DB hands back. The YAML path enforces
                # this (``ProviderDef.provider`` is ``Literal[PROVIDER_TYPES]``)
                # and the admin create route does too (``app.py:3584``), but a
                # row written by an older build, a hand edit, or a restore from
                # a foreign dump can carry anything. An unvalidated type reaches
                # ``registry.fresh_adapter`` inside the gateway, which raises a
                # bare ``ValueError`` — and ``router.py`` catches only
                # ``WiwiError`` and re-raises everything else, so it skips the
                # retry/fallback machinery and surfaces as an unhandled 500
                # instead of a clean "deployment unavailable" (AUDIT #394).
                # Skip the provider so its deployments simply have no account to
                # resolve against, which is the existing no-such-provider path.
                # Local import: this method already binds the name ``structlog``
                # in a nested scope, and the module-level one is not visible here.
                import structlog as _sl

                _sl.get_logger("wiwi.startup").warning(
                    "db_provider_type_unknown",
                    provider=p["name"], provider_type=p["provider_type"],
                    known=sorted(PROVIDER_TYPES))
                continue
            acct = self.router.providers.get(p["name"])
            if acct is None:
                self.router.providers[p["name"]] = ProviderAccount(
                    name=p["name"], provider_type=p["provider_type"],
                    base_url=p["base_url"], timeout_s=p["timeout_s"],
                    extra_headers=p.get("extra_headers", {}),
                    round_robin=p.get("round_robin", True),
                    enabled=p.get("enabled", True),
                    alias_id=p.get("alias_id"))
                continue
            # The account came from YAML and a DB row exists for it: that row is
            # an operator override (a PATCH, or a rotated OAuth credential) and
            # wins over the YAML fields, so the edit survives the restart
            # (AUDIT #319). ``alias_id`` is written with the full desired state
            # whenever the row is created, so NULL here means "cleared".
            acct.provider_type = p["provider_type"]
            acct.base_url = p["base_url"]
            acct.timeout_s = p["timeout_s"]
            acct.extra_headers = p.get("extra_headers", {})
            acct.round_robin = p.get("round_robin", True)
            acct.enabled = p.get("enabled", True)
            acct.alias_id = p.get("alias_id")
        # reapply admin-saved alias_id (and the alias-to-provider map)
        for p in data["providers"]:
            alias = p.get("alias_id")
            if alias and p["name"] in self.router.providers:
                try:
                    self.router.set_provider_alias(p["name"], alias)
                except ValueError:
                    pass  # duplicate alias; surface via the PATCH path
        # keys
        for k in data["keys"]:
            acct = self.router.providers.get(k["provider_name"])
            if acct is None:
                continue
            existing = acct.get_key(k["label"])
            if k.get("detached"):
                # Tombstone: the operator deleted a key that only existed in
                # YAML, and it must not come back on the next start (AUDIT
                # #319).
                if existing is not None:
                    acct.keys = [x for x in acct.keys if x.label != k["label"]]
                continue
            if existing is not None:
                # DB row overrides the YAML key. An empty stored secret is a
                # routing-only row (backup import without secrets), so it must
                # not blank a working credential.
                if k["secret"]:
                    existing.secret = k["secret"]
                existing.weight = k["weight"]
                existing.enabled = k["enabled"]
                continue
            acct.keys.append(ProviderKey(
                label=k["label"], secret=k["secret"], weight=k["weight"],
                enabled=k["enabled"]))
        # deployments
        for d in data["deployments"]:
            acct = self.router.providers.get(d["provider_name"])
            deps = self.router.groups.get(d["group_name"], [])
            if d.get("detached"):
                # Tombstone: the operator detached this deployment from the
                # admin UI. A YAML-sourced one has no other record, so drop it
                # here or it silently returns on every restart (AUDIT #297).
                #
                # Matched by provider NAME, not by account identity: when the
                # account itself is tombstoned (a deleted YAML provider) it is
                # already out of ``router.providers``, yet the YAML-built group
                # still holds its Deployment objects. The old ``acct is None:
                # continue`` skipped the tombstone in exactly that case, so the
                # group stayed routable against a deleted account.
                gone = [dep for dep in deps
                        if dep.provider.name == d["provider_name"]
                        and dep.model_id == d["model_id"]]
                for dep in gone:
                    deps.remove(dep)
                if not deps:
                    self.router.groups.pop(d["group_name"], None)
                continue
            if acct is None:
                continue
            existing = next(
                (dep for dep in deps
                 if dep.provider is acct and dep.model_id == d["model_id"]),
                None)
            if existing is not None:
                # The deployment came from YAML. A DB row for the same triple
                # is an operator override (e.g. a weight edited in the Combos
                # page), so apply it rather than discard it — otherwise every
                # weight edit reverted on the next restart (AUDIT #296).
                existing.weight = d["weight"]
                # Same rule for the per-deployment limits (AUDIT #461): a
                # stored value overrides the YAML one, and NULL means it was
                # never stored, so the YAML value stands.
                for field in DEPLOYMENT_LIMIT_FIELDS:
                    value = d.get(field)
                    if value is not None:
                        setattr(existing, field, value)
                continue
            limits = {f: d[f] for f in DEPLOYMENT_LIMIT_FIELDS
                      if d.get(f) is not None}
            dep = Deployment(group=d["group_name"], provider=acct,
                             model_id=d["model_id"], weight=d["weight"],
                             **limits)
            self.router.groups.setdefault(d["group_name"], []).append(dep)
        # cross-provider pools may have grown from admin-added deployments
        self.router.rebuild_cross_provider_pools()
        # alert rules
        rules = await self.config_store.get_setting("alert_rules")
        if rules is not None:
            self.alert_rules = rules
        # routing strategy
        strategy = await self.config_store.get_setting("routing_strategy")
        if strategy is not None:
            self.router.settings.routing_strategy = strategy
        # model_group_alias: DB overlay merges over the YAML map (per key;
        # null value = tombstone removing a YAML-defined alias).
        # `alias_yaml_base` is captured BEFORE the overlay so write-time
        # persistence can recompute the overlay as diff(live vs YAML).
        self.alias_yaml_base = dict(self.router.settings.model_group_alias)
        alias_overlay = await self.config_store.get_setting("model_group_alias")
        if alias_overlay:
            merged = dict(self.router.settings.model_group_alias)
            for _k, _v in alias_overlay.items():
                if _v is None:
                    merged.pop(_k, None)
                else:
                    coerced = coerce_alias_value(_v)
                    if coerced is None:
                        # Malformed overlay entry — skip with a log so a
                        # bad row doesn't poison the whole merge.
                        import structlog
                        structlog.get_logger().warning(
                            "alias_overlay_dropped", key=_k, value=_v)
                        continue
                    merged[_k] = coerced
            self.router.settings.model_group_alias = merged
        # custom model pricing overrides (admin-added; survive restarts)
        for p in await self.config_store.load_prices():
            mid = p["model_id"]
            entry = {k: v for k, v in p.items() if k != "model_id"}
            self.cost.prices[mid] = entry
        # per-provider price overrides, layered onto their model's entry
        for s in await self.config_store.load_price_scopes():
            mid, scope = s["model_id"], s["scope"]
            rates = {k: v for k, v in s.items() if k not in ("model_id", "scope")}
            entry = self.cost.prices.setdefault(mid, {})
            entry.setdefault("providers", {})[scope] = rates

    async def shutdown(self) -> None:
        if self.shutdown_event is not None:
            self.shutdown_event.set()
        # Flush buffered spend before the engine is disposed; a failure here
        # leaves the reservation in the DB (conservative), never overspend.
        if self.spend is not None:
            await self.spend.stop()
        close_limiter = getattr(self.limiter, "close", None)
        if close_limiter is not None:
            with contextlib.suppress(Exception):
                await close_limiter()
        for g in self.gateways.values():
            await g.aclose()
        if self.response_cache is not None:
            await self.response_cache.aclose()
        await self.logs.stop()
        if getattr(self, "_db_sink", None) is not None:
            await self._db_sink.engine.dispose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: init DB, run logging workers."""
    state: AppState = app.state.wiwi
    await state.init_db()
    # Batched spend settlement: flush on a bounded interval for the life of
    # the process. ``shutdown()`` performs the final flush.
    if state.spend is not None:
        await state.spend.start()
    # Journals left over from a previous process are either still within TTL
    # (replayable — keep) or stale (sweep now). While serving, the background
    # TTL sweeper keeps expiring them so a long-lived server does not
    # accumulate journals until restart (the "lifespan sweep loop" the
    # tape_store module docstring promises).
    if state.journals is not None:
        removed = state.journals.sweep()
        if removed:
            import structlog as _sl
            _sl.get_logger("wiwi.startup").info("swept_stale_stream_journals",
                                                removed=removed)
        if state.config.router_settings.stream_journal_enabled:
            # AUDIT #380: probe the journal dir ONCE at boot and say so loudly
            # if it is unusable. Every other symptom of an unwritable dir is
            # swallowed — ``open`` logs the mkdir/touch failure on the request
            # path, and the startup sweep below returns 0 on its own OSError —
            # so the container reports healthy while refusing virtual-key
            # streams 503. The absolute path is in the message because the
            # failure is a *relative-path-resolved-against-the-wrong-CWD* bug,
            # and the configured value alone never reveals where it landed.
            #
            # Deliberately NOT fatal: journaling is a replay convenience, and
            # refusing to boot would turn a resume-feature misconfiguration
            # into a worse outage than the one being fixed.
            import structlog as _sl
            _jlog = _sl.get_logger("wiwi.startup")
            try:
                await asyncio.to_thread(
                    state.journals.dir.mkdir, parents=True, exist_ok=True)
                _probe = state.journals.dir / ".writable"
                await asyncio.to_thread(_probe.touch, exist_ok=True)
                await asyncio.to_thread(_probe.unlink)
            except OSError as e:
                _jlog.error(
                    "stream_journal_dir_unwritable",
                    dir=str(state.journals.dir.resolve()),
                    error=f"{type(e).__name__}: {e}",
                    hint="point WIWI_STREAM_JOURNAL_DIR at a writable path "
                         "(the image ships /data for this); without it every "
                         "virtual-key streaming request is refused 503")
            # Sweep at a fraction of the TTL so expiry lags the configured
            # TTL by at most one interval (clamped: at least 1s, at most 60s).
            ttl = state.config.router_settings.stream_journal_ttl_s
            state.journals.start(interval_s=min(60.0, max(1.0, ttl / 4)))
    await state.logs.start()
    # Background auto-refresh for Cline OAuth tokens (proactively rotates
    # expiring access tokens so requests don't fail mid-flight).
    from wiwi.providers.cline_auto_refresh import (
        ClineAutoRefresh,
        refresh_for_provider,
    )
    state.cline_refresh = ClineAutoRefresh(state)
    state.cline_refresh.start()
    # On-demand refresh hook: when a Cline request returns 401 mid-session
    # (the background sweeper only refreshes inside the 5-minute lead
    # window), the gateway uses this hook to rotate the token and retry
    # once before surfacing the error.  See wiwi.core.gateway for the
    # call site and tests/test_fix_round7.py for the regression coverage.
    cline_refresh_hook = refresh_for_provider(state)
    for gw in state.gateways.values():
        gw._on_demand_cline_refresh = cline_refresh_hook  # type: ignore[attr-defined]
    # Hermes (Nous Portal) OAuth token refresh: same two-path contract as
    # Cline/WorkBuddy. Hermes shipped with a working refresh protocol but no
    # automation, so a token that expired (or was invalidated upstream)
    # mid-session produced a hard 401 with no rotation, and the 401 charged a
    # healthy key's error streak under the default any_error failover (AUDIT
    # #423). The sweeper rotates inside the lead window; the hook rotates on a
    # gateway 401. Both share one worker (one lock, one circuit) via
    # _worker_for so a rotating refresh token is never presented twice.
    from wiwi.providers.hermes_auto_refresh import HermesAutoRefresh
    from wiwi.providers.hermes_auto_refresh import (
        refresh_for_provider as hermes_refresh_for_provider,
    )
    state.hermes_refresh = HermesAutoRefresh(state)
    state.hermes_refresh.start()
    hermes_refresh_hook = hermes_refresh_for_provider(state)
    for gw in state.gateways.values():
        gw._on_demand_refresh_hooks["hermes"] = hermes_refresh_hook  # type: ignore[attr-defined]
    # OpenCode Zen live version: refresh the cached opencode/<version>
    # User-Agent every 5 min so Zen/Cloudflare never restricts traffic.
    # No restart or reload needed — the adapter reads the cache live.
    from wiwi.providers.opencode_version import OpencodeVersionRefresh
    state.opencode_refresh = OpencodeVersionRefresh()
    state.opencode_refresh.start()
    # Cline's API expects live CLI/core fingerprints. Fetch both from npm in
    # the background so the synchronous request path never performs I/O.
    from wiwi.providers.cline_version import ClineVersionRefresh
    state.cline_version_refresh = ClineVersionRefresh()
    state.cline_version_refresh.start()
    # Health healer: probes cooling/invalid keys and cooled deployments and
    # restores them early into probation. Opt-in via the ``healer:`` config
    # section; start() is a no-op when disabled.
    state.healer = HealthHealer(state.router, state.config.healer,
                                log_proxy=state.router.log_proxy)
    state.healer.start()
    # Persisted Responses state (spec B): TTL sweeper for stored_responses.
    # Interval 0 disables the background loop; expiry stays enforced on read.
    if state.response_store is not None:
        sweep_interval = state.config.wiwi_settings.response_store_sweep_interval_s
        if sweep_interval > 0:
            state.response_store.start(interval_s=sweep_interval)
    # OTLP tracing: no-op unless ``telemetry.enabled`` and the ``[otel]`` extra
    # is installed, so this is safe to call unconditionally.
    tracer.configure(state.config.telemetry)
    # Reconcile persisted Cline default-model settings (one global model
    # id → one Deployment per Cline account under ``cline:<model_id>``).
    if state.config_store is not None:
        try:
            saved = await state.config_store.get_setting(
                "cline_settings:default_models")
            if isinstance(saved, list) and saved:
                await _apply_cline_default_models(state, [str(x) for x in saved])
        except Exception as e:  # noqa: BLE001 — best-effort startup
            import structlog as _sl
            _sl.get_logger("wiwi.cline_defaults").warning(
                "cline_default_apply_failed_at_startup", err=str(e),
            )
    yield
    # Teardown: EVERY stop runs, even if an earlier one raised.
    #
    # The teardown was twelve bare sequential ``await X.stop()`` calls with
    # no try/finally, no suppress and no aggregation. The first to raise
    # aborted every later one AND ``state.shutdown()`` — so the DB engine
    # was never disposed, ``shutdown_event`` was never set (the
    # ``/admin/stream`` SSE keepalive loop's only break condition is that
    # event, so it never returned), and the tracer's OTLP exporter was left
    # unflushed. Seven orphaned workers kept polling providers and holding
    # sockets and DB connections.
    #
    # Starlette's ``Router.lifespan`` re-raises anything raised while
    # serving, so an exception in the request path skipped this half
    # entirely — the same defect, reached by a different route (AUDIT #348).
    #
    # Each stop is now independent: a failure is logged and the rest still
    # run. The order is preserved — the DB-touching workers stop BEFORE
    # ``state.shutdown()`` disposes the engine.
    async def _stop(name: str, stop: Any) -> None:
        """Run one worker's stop, absorbing and logging its failure."""
        try:
            await stop()
        except Exception as e:  # noqa: BLE001 — teardown must not raise
            import structlog as _sl
            _sl.get_logger("wiwi.shutdown").warning(
                "worker_stop_failed", worker=name, error=str(e)[:200])

    if state.journals is not None:
        await _stop("journals", state.journals.stop)
    if state.healer is not None:
        await _stop("healer", state.healer.stop)
    if state.response_store is not None:
        await _stop("response_store", state.response_store.stop)
    try:
        tracer.shutdown()
    except Exception as e:  # noqa: BLE001 — teardown must not raise
        import structlog as _sl
        _sl.get_logger("wiwi.shutdown").warning(
            "tracer_shutdown_failed", error=str(e)[:200])
    if state.opencode_refresh is not None:
        await _stop("opencode_refresh", state.opencode_refresh.stop)
    if state.cline_version_refresh is not None:
        await _stop("cline_version_refresh", state.cline_version_refresh.stop)
    if state.hermes_refresh is not None:
        await _stop("hermes_refresh", state.hermes_refresh.stop)
    if state.log_retention is not None:
        await _stop("log_retention", state.log_retention.stop)
    if state.cline_refresh is not None:
        await _stop("cline_refresh", state.cline_refresh.stop)
    # The engine disposal is last: the workers above still touch the DB.
    try:
        await state.shutdown()
    except Exception as e:  # noqa: BLE001 — teardown must not raise
        import structlog as _sl
        _sl.get_logger("wiwi.shutdown").warning(
            "state_shutdown_failed", error=str(e)[:200])


def _inject_id(chunk: bytes, first_id: int) -> tuple[bytes, int]:
    """Prepend an SSE ``id:`` line to each frame in *chunk*.

    A chunk may carry several frames (joined by blank-line boundaries), and an
    ``id`` line sets the last-event-id for the event that follows it. Tagging
    every sub-frame with the SAME id — the previous behaviour — made a
    reconnect ambiguous: a client whose connection dropped after sub-frame 1 but
    before sub-frame 2 reported ``Last-Event-ID`` of the shared id, and the
    server's ``seq > last_seq`` replay then skipped sub-frame 2 entirely, losing
    it (AUDIT #283). Giving each frame its own monotonically increasing id makes
    the cursor land on the right frame.

    Returns ``(tagged_bytes, last_id_assigned)`` so the caller can keep its
    sequence counter monotonic across chunks; when *chunk* holds no frame the
    input id is returned unchanged.

    Only complete frames are tagged: a trailing fragment left by a split on
    ``\\n\\n`` is re-terminated, matching how the frames are written.
    """
    frames = [f for f in chunk.split(b"\n\n") if f]
    if not frames:
        return chunk, first_id
    out: list[bytes] = []
    event_id = first_id
    for i, frame in enumerate(frames):
        if i:
            event_id += 1
        out.append(f"id: {event_id}\n".encode() + frame + b"\n\n")
    return b"".join(out), event_id


def create_app(config: WiwiConfig) -> FastAPI:
    # Fail fast (and closed) on a config that would allow forged admin
    # sessions. Checked here as well as in startup, so a misconfigured
    # deployment fails at import/first-call rather than only when the
    # lifespan runs.
    if not (os.environ.get("WIWI_SESSION_SECRET")
            or config.general_settings.master_key):
        raise RuntimeError(
            "no session secret configured: set WIWI_MASTER_KEY (or "
            "WIWI_SESSION_SECRET). Refusing to start with a default secret, "
            "which would allow forged admin sessions.")
    # Feed the sliding-renewal helper (module-level so the ASGI middleware
    # can reach it without an app instance). The middleware runs before the
    # lifespan, so the secret cannot come from app state.
    _RENEW_MASTER_KEY.clear()
    _RENEW_MASTER_KEY.append(
        os.environ.get("WIWI_SESSION_SECRET")
        or config.general_settings.master_key or "")
    state = AppState(config)
    app = FastAPI(title="wiwi", version="0.1.0", docs_url=None,
                  lifespan=lifespan)
    app.state.wiwi = state

    # -- helpers ---------------------------------------------------------------
    def bearer(request: Request) -> str:
        # The credential extraction lives in ``wiwi/server/dependencies.py``
        # so the routers read the same header the inline routes do.
        return bearer_token(request)

    # Headers an Anthropic client sets to negotiate capabilities that its
    # BODY then depends on. Anthropic's Messages format is header-coupled:
    # ``anthropic-beta`` gates features whose body fields are rejected with a
    # hard 400 when the header is absent, so a gateway that forwards the body
    # must forward the header. Claude Code sends eleven betas on every request
    # (context-1m, interleaved-thinking, context-management, effort,
    # mid-conversation-system, …) and wiwi previously read none of them: every
    # header-only capability vanished and every body/header pair 400'd.
    # An explicit allowlist is required because Anthropic's guidance is that
    # the beta VALUE must pass through verbatim (it changes per release) while
    # arbitrary client headers must not be relayed to an upstream.
    _FORWARDABLE_HEADERS = ("anthropic-beta",)

    def _forward_headers(request: Request) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in _FORWARDABLE_HEADERS:
            value = request.headers.get(name)
            if value:
                out[name] = value
        return out

    def _session_id(request: Request) -> str | None:
        """The client's session identity for router deployment affinity.

        A header is authoritative; the query parameter exists for clients that
        cannot set headers (browsers opening a WebSocket, ``curl`` without
        ``-H``). Truncated to 128 chars: it is an opaque routing hint, and an
        unbounded client-supplied string would otherwise become a key in a
        process-lifetime map.
        """
        raw = request.headers.get("x-wiwi-session-id") or request.query_params.get(
            "session_id")
        if not raw:
            return None
        raw = raw.strip()
        return raw[:128] or None

    async def _model_group_is_unpriced(group: str) -> bool:
        """True when nothing in *group*'s deployment set has a pricing row.

        Best-effort admission signal for ``unpriced_model_policy`` (AUDIT
        #323): ``resolve_group`` returns ``(name, deployments)``, and the
        gateway later prices through ``f"{provider_type}/{model_id}"``.
        An unresolvable deployment would cost nothing either, so it counts as
        unpriced. False when *any* deployment is priced — the gateway may
        legally route there, and that path is billed normally.
        """
        resolved, deps = state.router.resolve_group(group)
        if not resolved or not deps:
            return True
        cost = state.cost
        for dep in deps:
            if cost._lookup(f"{dep.provider.provider_type}/{dep.model_id}",
                            dep.provider.provider_type, dep.provider.name):
                return False
        return True

    async def authenticate(request: Request, model: str, surface: str = "chat",
                           bearer_token: str | None = None):
        """Resolve and vet the caller's credential. Does NOT reserve rate-limit
        slots.

        ``bearer_token`` overrides the request's Authorization header: the
        playground wrapper authenticates the browser session itself and injects
        the server-held virtual key here, so the rest of the admission path
        (model allowlist, budget, rate limit) is identical to a bearer call.

        Reservation is deliberately a separate step (``enforce_rate_limit``),
        taken only once the model is known to resolve — reserving before a 404
        burned a window slot on a request that never reached upstream. This
        function used to carry an optional ``reserve=True`` branch that did the
        same ``limiter.check``; every caller passed ``reserve=False``, so the
        branch was dead — and had anyone used the default, the two calls would
        each reserve a slot while only one refund path existed, leaking the
        other into the 60 s window permanently. Removed rather than left as a
        trap.
        """
        if state.auth is None:
            return None, _err(500, "api_error", "gateway not initialized", request)
        token = bearer_token if bearer_token is not None else bearer(request)
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
            # 402, not 429 (AUDIT #142): the post-hoc check below refuses the
            # same condition with 402, and docs/API_REFERENCE.md, docs/ADMIN.md
            # and docs/ARCHITECTURE.md all pin "budget cap exceeded" to 402
            # while reserving 429 for rate limits. 429 also tells an SDK to
            # back off and retry — wrong advice for a cap that never clears.
            return None, _err(402, "budget_exceeded",
                              f"budget exhausted ({info.spend_to_date:.4f}"
                              f"/{info.max_budget})", request, surface)
        # Fail closed on unpriced models when the operator asked for it
        # (AUDIT #323): CostEngine prices a missing row at $0 and
        # update_spend waves a non-positive charge through, so on the old
        # behaviour an unpriced model accrued zero spend forever and
        # max_budget never tripped. "warn" (default) keeps serving but tags
        # the request so the log/metrics expose it; "reject" refuses with
        # 503 — the model exists, the *pricing* is missing server-side, so
        # 4xx would tell the client a lie about whose fault this is.
        if (model and model != "*" and info.max_budget is not None
                and config.general_settings.unpriced_model_policy == "reject"
                and await _model_group_is_unpriced(model)):
            return None, _err(
                503, "unpriced_model_error",
                f"model '{model}' has no pricing entry; budget-capped keys are"
                " refused until an admin prices it"
                " (general_settings.unpriced_model_policy=reject)",
                request, surface)
        # "" / "*" = endpoint is not model-scoped (e.g. GET /v1/models):
        # listing never violates an allowlist, only real completions do
        if info.models and model and model != "*" and model not in info.models:
            return None, _err(403, "permission_error",
                              f"key not allowed for model '{model}'", request, surface)
        return info, None

    async def enforce_rate_limit(info, est_tokens: int, request: Request,
                                 surface: str, request_id: str = "") -> ORJSONResponse | None:
        """Reserve RPM/TPM window slots only once the model is known-good."""
        allowed, retry_after = await state.limiter.check(info.key_id, info.rpm,
                                                         info.tpm,
                                                         est_tokens=est_tokens,
                                                         request_id=request_id)
        if not allowed:
            resp = _err(429, "rate_limit_error",
                        f"rate limit exceeded, retry in {retry_after}s",
                        request, surface)
            resp.headers["Retry-After"] = str(retry_after)
            return resp
        return None

    async def _record_tpm_usage(info, ctx) -> None:
        """Add actual token usage to the tpm sliding windows after a response."""
        u = ctx.usage
        if u is not None and info is not None:
            await state.limiter.record_tokens(
                info.key_id, u.prompt_tokens + u.completion_tokens,
                request_id=ctx.request_id)

    async def record_spend(key_id: str, cost: float, ctx: RequestContext | None = None) -> bool:
        """Charge *cost* to *key_id*, returning False when the cap refused it.

        A False return means ``update_spend``'s conditional UPDATE was
        rejected: the charge would cross ``max_budget``, or the key vanished.
        The upstream already served and billed the request, so the true-up
        below records the charge unconditionally — otherwise ``spend_to_date``
        stayed frozen at its pre-crossing value and the key kept passing the
        admission check for the rest of its life (AUDIT_REPORT C1). The
        refusal is still reported so the caller refuses *this* response; the
        recorded spend is what makes the *next* request fail admission.

        A *raise* out of ``update_spend`` is a different event: the write path
        itself is down (SQLite ``database is locked``, Postgres failover, pool
        exhaustion), so the charge was neither rejected nor recorded. That used
        to return True from a bare ``except`` — indistinguishable from a
        successful charge — so a hard budget cap silently stopped being
        enforced, with no counter, no /health field and no metric (AUDIT #179).
        The exception is now loud (an ``error`` log with the key id and cost,
        plus the ``spend_charge_failures`` counter in /health and /metrics) and
        the charge is retried through ``apply_spend_trueup``, which records it
        regardless of the cap — so a transient failure loses no money and the
        cap stays enforced for the next request.

        Only if that second, unconditional attempt *also* fails is the charge
        genuinely unrecorded, and only then does this return False. That is the
        one case where reporting success would be the fail-open the audit
        describes: the money is gone and the cap will not stop the next
        request. Note what is deliberately *not* refused — a write failure the
        true-up repaired. The upstream has already served and billed the
        request, so turning a repaired accounting blip into an error would be
        the AUDIT #24 defect (a 500/402 after a successful completion) wearing
        a different status code.
        """
        # Batched path: the buffer aggregates the charge and the reservation
        # release into one flush-time UPDATE. It keeps this process's cached
        # AuthInfo exact immediately, so admission still sees the real total;
        # the DB write lags by at most one flush interval. A key close to its
        # cap is flushed synchronously to bound the cross-replica window.
        info = getattr(ctx, "auth", None) if ctx is not None else None
        if (state.spend is not None and info is not None
                and getattr(info, "key_id", None) == key_id):
            recorded = state.spend.charge(info, cost, ctx.budget_reserved)
            ctx.budget_reserved = 0.0
            await state.spend.maybe_flush_near_cap(info, cost)
            return recorded
        # An admission-time reservation (AUDIT #324) is admission-scoped: the
        # moment a real charge exists, the reservation is redundant (the cap
        # gate now reads spend directly) and would double-count the same
        # dollars if left in place — the conditional UPDATE below tests
        # ``spend_to_date + cost <= max_budget``, which a stale reservation
        # would make spuriously fail. Release first, then charge once.
        if ctx.budget_reserved > 0:
            reserved = ctx.budget_reserved
            try:
                await state.auth.release_budget_reservation(key_id, reserved)
            except Exception as e:  # noqa: BLE001 — see below for why this counts
                # A stranded reservation is an *additive* term in over_budget
                # (auth/service.py), so losing this release permanently shrinks
                # the key's headroom for the life of the process. Pre-fix this
                # was a bare suppress: no log, no counter, no operator surface
                # at all. Count it alongside the charge failures — it is the
                # same class of silent accounting loss.
                state.spend_charge_failures += 1
                import structlog as _sl
                _sl.get_logger("wiwi.spend").error(
                    "spend_reservation_release_failed", key_id=key_id,
                    reserved=reserved, error=type(e).__name__, detail=str(e))
                # Deliberately NOT cleared: the reservation really is still held
                # in the database, so leaving ctx.budget_reserved set is what
                # lets a later release path (_release_tpm_reservation, the
                # stream teardown) retry it. Zeroing it here would strand the
                # dollars permanently and invisibly.
            else:
                ctx.budget_reserved = 0.0
        try:
            recorded = await state.auth.update_spend(key_id, cost)
        except Exception as e:  # noqa: BLE001 — accounting must never 500 a served turn
            state.spend_charge_failures += 1
            import structlog as _sl
            _log = _sl.get_logger("wiwi.spend")
            _log.error("spend_charge_failed", key_id=key_id, cost=cost,
                       error=type(e).__name__, detail=str(e))
            # Unconditional retry. Unlike the False path below this cannot be
            # an over-budget crossing (a crossing returns False, it never
            # raises), so the cap test does not apply and recording the charge
            # is the honest accounting: the money was really spent.
            try:
                await state.auth.apply_spend_trueup(key_id, cost)
            except Exception as e2:  # noqa: BLE001
                _log.error("spend_charge_unrecorded", key_id=key_id, cost=cost,
                           error=type(e2).__name__, detail=str(e2))
                return False
            return True
        if not recorded:
            # Over-cap crossing (or a vanished key). The charge was *refused*,
            # not lost — the upstream already billed it — so the unconditional
            # true-up is what keeps spend_to_date honest and makes the *next*
            # request fail admission (AUDIT_REPORT C1). This used to sit under a
            # bare contextlib.suppress, the second half of AUDIT #179: a raising
            # true-up lost the charge silently, leaving spend_to_date below
            # max_budget so over_budget never tripped and the key was admitted
            # indefinitely, with no log and spend_charge_failures stuck at 0.
            try:
                await state.auth.apply_spend_trueup(key_id, cost)
            except Exception as e:  # noqa: BLE001 — the charge is genuinely lost
                state.spend_charge_failures += 1
                import structlog as _sl
                _sl.get_logger("wiwi.spend").error(
                    "spend_charge_unrecorded", key_id=key_id, cost=cost,
                    error=type(e).__name__, detail=str(e))
        return recorded

    async def _release_tpm_reservation(info, ctx) -> None:
        """Refund the estimated RPM/TPM slot a request reserved at admission.

        Called on every non-success exit so a request whose upstream never
        consumed tokens (5xx, 429, all-keys-cooling, any WiwiError before
        usage is known) does not throttle unrelated later requests for the
        rest of the rate-limit window (AUDIT #70). Safe to call after
        ``_record_tpm_usage`` too: :meth:`RateLimiter.release` only removes
        still-estimated reservations, never confirmed usage.
        """
        if info is not None and ctx.budget_reserved > 0:
            # The budget reservation is admission-scoped exactly like the
            # rate-limit slot: a request that died before producing usage
            # never consumed the dollars it reserved, so holding the
            # reservation would permanently shrink the key's cap for the
            # process lifetime (AUDIT #324). RELEASE, not settle — the
            # estimate is given back untouched, because billing it would
            # charge requests that never ran. Every early return between
            # reserve and settle funnels through here; the success path
            # zeroes ``budget_reserved`` after settling, so this is a no-op
            # there.
            with contextlib.suppress(Exception):
                await state.auth.release_budget_reservation(
                    info.key_id, ctx.budget_reserved)
            ctx.budget_reserved = 0.0
        if info is None:
            return
        u = ctx.usage
        if u is not None:
            # Usage is known: the success path reconciles it, so there is
            # nothing to refund.
            return
        with contextlib.suppress(Exception):
            await state.limiter.release(info.key_id, request_id=ctx.request_id)

    async def _persist_journal_owner(state_, journal, journal_id, info) -> bool:
        """Make a journal's ownership line durable before data flows (AUDIT #320).

        ``open()`` writes the owner line best-effort — correct for in-process
        scoping, where the RAM intent already fails the gate closed on a lost
        write (#317). But the RAM intent dies with the process, and "the
        process died before the line landed" is on disk indistinguishable
        from "the line was lost while the process lived": after a restart the
        gate read ``None`` and treated the journal as legacy-and-open, so a
        caller holding a *different* key could replay another tenant's
        response.

        The fix is to make the durable owner write PRIMARY: this fsyncs the
        ownership line and runs BEFORE the first data frame is journaled, so
        a journal that carries data always carries a durable owner too —
        after any restart, ``owner_of`` scopes it correctly. Returns False
        when the line could not be persisted (ENOSPC, read-only mount): the
        caller must then drop the journal and stream WITHOUT it
        (:func:`_drop_journal`) rather than write tenant data into a journal
        that would be ownerless on disk. Failing closed on the *journal* is the
        audit's own fix sketch — the alternative (best-effort persist) is
        exactly the hole.

        Failing closed on the *request* was the original sketch too, and was
        wrong in practice (AUDIT #380): the shipped image's journal dir is
        unwritable, so refusing turned a replay-feature misconfiguration into
        a 503 on every virtual-key stream while the rest of the gateway looked
        perfectly healthy. Dropping the journal is strictly safer than
        persisting an ownerless one — no file on disk means no replay data to
        leak — so the cross-tenant hole stays closed either way.

        Master streams are exempt: their journals are explicitly readable
        only by master (the gate's ``jowner == "master"`` arm), and an
        unscoped-line journal written by master would after a restart read as
        legacy-open — which for a master-only content set is the documented
        pre-#67 behaviour, not a cross-tenant leak.
        """
        kid = getattr(info, "key_id", None)
        if (journal is None or kid is None or kid == "master"
                or state_.journals.owner_intent_durable(journal_id)):
            return True
        try:
            await asyncio.to_thread(journal.persist_owner_sync, kid)
            state_.journals.note_owner_durable(journal_id)
            return True
        except OSError as e:
            state_.logs.log_proxy(
                "error",
                f"journal owner persist failed for {journal_id}; dropping the"
                f" journal so no ownerless replay data is written, and"
                f" streaming without reconnect-resume:"
                f" {type(e).__name__}: {e}",
                journal_id)
            return False

    async def _abandon_journal(state_, journal, journal_id) -> None:
        """Close and drop a journal opened for an attempt that failed before
        streaming its first chunk.

        The client never received a byte under this stream id, so nothing may
        tail it — and an adopted reconnect id must not linger in the active
        set, or later reconnects would tail a dead journal until TTL (AUDIT
        #117). No ``done`` record is written: the logical stream never
        produced client-visible content here.
        """
        if journal is not None:
            with contextlib.suppress(Exception):
                await journal.aclose()
            state_.journals.release(journal_id)
    async def _drop_journal(state_, journal, journal_id) -> None:
        """Remove a journal that can never be durably owned, leaving it
        UNUSABLE for the rest of this request.

        Distinct from :func:`_abandon_journal`, which leaves the file on disk:
        this is the AUDIT #320 case, where the owner line could not be made
        durable, so the file must not survive at all.

        The in-memory intent is dropped too (``forget_owner``, AUDIT #395).
        ``release`` deliberately keeps it for a *finished* journal, because that
        file stays replayable for its whole TTL and still needs its owner to gate
        it (AUDIT #175). Here there is no file and no replay to scope, and the
        sweep only reclaims intents whose file it unlinks itself — so keeping it
        leaked one entry per streamed request for the process lifetime in the
        degraded-journal state this path exists for. The gate is unaffected:
        ``owner_of`` on a nonexistent journal can only make a replay deny or miss,
        never serve one key another key's bytes.

        Callers must ALSO pass ``journal_disabled=True`` to
        ``_stream_response``: ``journal=None`` alone would re-enter the
        fallback branch and re-open a journal on the very directory that just
        failed, looping back to the same refusal.
        """
        if journal is None:
            return
        with contextlib.suppress(Exception):
            await journal.aclose()
        state_.journals.release(journal_id)
        with contextlib.suppress(OSError):
            await asyncio.to_thread(journal.path.unlink, True)
        # The intent is no longer needed once the FILE is gone: nothing can be
        # replayed, so there is no owner left to scope a replay to. ``release``
        # keeps it on purpose for a finished-but-still-replayable journal, and
        # the sweep only reclaims intents whose file it unlinked itself — so
        # without this the dropped journal's intent lived for the process
        # lifetime, one leaked entry per streamed request in the degraded-journal
        # state (AUDIT #395). The gate is unaffected: ``owner_of`` for a
        # nonexistent journal only ever makes the replay deny or miss, never leak.
        state_.journals.forget_owner(journal_id)

    async def json_body(request: Request, *, limit_mb: float | None = None
                        ) -> tuple[Any, ORJSONResponse | None]:
        """Parse the request body; malformed JSON is a client error (400).

        *limit_mb* overrides the configured cap for callers that legitimately
        post more than ``max_request_body_mb`` — the backup restore path, whose
        bound is set by ``_backup_import_limit_mb`` rather than opened up.
        """
        # Errors raised here happen before the surface is known to the handler,
        # so infer it from the path: each inbound dialect wants its own error
        # body shape (see _surface_for_path).
        surface = _surface_for_path(request.url.path)
        cap_mb = (limit_mb if limit_mb is not None
                  else config.wiwi_settings.max_request_body_mb)
        limit = cap_mb * 1024 * 1024
        try:
            raw = await request.body()
        except Exception:  # noqa: BLE001
            raw = b""
        # Enforce the cap on what actually arrived. Content-Length is checked
        # in middleware, but chunked bodies declare no length — and the
        # middleware can only flag an overage, not guarantee the handler sees
        # the flag. Measuring here closes that gap either way. The middleware
        # flag only applies when it was the one enforcing the default cap.
        flagged = (limit_mb is None
                   and getattr(request.state, "body_too_large", False))
        if len(raw) > limit or flagged:
            return None, _err(413, "invalid_request_error",
                              f"request body exceeds {cap_mb:g} MiB",
                              request, surface)
        try:
            body = orjson.loads(raw) if raw else None
        except ValueError:
            return None, _err(400, "invalid_request_error",
                              "request body is not valid JSON", request, surface)
        # Every inbound dialect expects a JSON *object*. Anything else (`null`,
        # a bare string, a number, an array) made the decoders call
        # `body.get(...)` and raise AttributeError, which surfaced as a 500
        # instead of a dialect-correct 400.
        if not isinstance(body, dict):
            return None, _err(400, "invalid_request_error",
                              "request body must be a JSON object",
                              request, surface)
        return body, None

    def _surface_for_path(path: str) -> str:
        """Dialect to report errors in, when the codec has not run yet.

        Mirrors the route table: `/v1/responses` speaks the Responses dialect,
        and everything under `/v1/messages` (including `count_tokens`, which
        does not *end* with `/messages`) speaks Anthropic's. Admin routes are
        not a dialect surface and keep the OpenAI-shaped error.
        """
        if path.startswith("/v1/messages"):
            return "messages"
        if path.startswith("/v1/responses"):
            return "responses"
        if path.startswith("/v1/completions"):
            return "completions"
        return "chat"

    def _error_body_for(surface: str):
        """Per-dialect error body — the error-path mirror of `_encoder_for`."""
        if surface == "messages":
            return am.error_body
        if surface == "responses":
            return orp.error_body
        return oc.error_body

    # -- session / user resolution ----------------------------------------------
    # The resolution rules live in ``wiwi/server/dependencies.py`` so the
    # routers (which are separate modules and cannot see this closure) share
    # one definition with the inline routes. These three remain as thin
    # wrappers because the extracted routers take them as factory parameters,
    # and the "return a response or None" shape is what those call sites are
    # written against.
    async def current_user(request: Request) -> UserInfo | None:
        """Resolve the caller from a signed session cookie OR a bearer master
        key (back-compat). Returns ``None`` when anonymous or when the session
        user has been disabled/deleted."""
        return await resolve_current_user(request, state)

    async def require_user_dep(request: Request) -> ORJSONResponse | None:
        """401 when the caller is not an authenticated user (nor master)."""
        if await current_user(request) is None:
            return _err(401, "authentication_error",
                        "authentication required", request)
        return None

    async def require_admin_dep(request: Request) -> ORJSONResponse | None:
        """401 when anonymous, 403 when authenticated but not an admin."""
        u = await current_user(request)
        if u is None:
            return _err(401, "authentication_error",
                        "authentication required", request)
        if u.role != "admin":
            return _err(403, "permission_error", "admin role required", request)
        return None

    # -- error rendering ------------------------------------------------------

    # A raised APIError is rendered here, in the caller's dialect. The inline
    # `_err` calls below return the same bodies; the raise path exists so a
    # service or a dependency can report a failure without threading a
    # response object back to the caller (see wiwi/server/errors.py).
    @app.exception_handler(APIError)
    async def _api_error_handler(request: Request, exc: APIError):
        rid = getattr(request.state, "request_id", "")
        body = render_error(exc, rid)
        return ORJSONResponse(body, status_code=exc.status_code,
                              headers={"x-wiwi-request-id": rid})

    # FastAPI's default validation-error handler answers 422 with
    # ``{"detail": [...]}``. wiwi's contract is 400 with a dialect-shaped
    # body (docs/API_REFERENCE.md), and 422 is a status wiwi has never
    # returned — a request-model validation failure must not become the only
    # 422 in the gateway's history. Re-render it as a 400 in the same shape
    # the inline `_err` calls produce.
    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request,
                                        exc: RequestValidationError):
        rid = getattr(request.state, "request_id", "")
        surface = _surface_for_path(request.url.path)
        message = _validation_message(exc)
        body = render_error(APIError(400, "invalid_request_error", message,
                                     surface), rid)
        return ORJSONResponse(body, status_code=400,
                              headers={"x-wiwi-request-id": rid})

    def _err(status: int, etype: str, message: str,
             request: Request, surface: str = "chat",
             span: Any = None) -> ORJSONResponse:
        rid = getattr(request.state, "request_id", "")
        headers = {"x-wiwi-request-id": rid}
        # Telemetry (spec C): record the outcome on the request span here rather
        # than at each return site that builds an error response. Sites that
        # pass nothing are covered by the execution body's ``finally``.
        if span is not None:
            tracer.close(span, status=status,
                         **{"wiwi.status": status, "wiwi.error_type": etype})
            # Spec §4 requires the trace id on success *and* error responses.
            # It was emitted only on the success path, so a caller reporting a
            # failed request — exactly the case where a trace is wanted — had
            # nothing to quote to the collector. Read it after ``close``: the
            # context is still valid, and closing first keeps the error the
            # span's recorded outcome.
            _trace_id = tracer.trace_id_of(span)
            if _trace_id:
                headers["x-wiwi-trace-id"] = _trace_id
        # Only the Anthropic dialect echoes the id inside the body (the real
        # Messages API always does, and Claude Code surfaces it in bug
        # reports). The OpenAI-shaped dialects carry it as a header only, so
        # their error_body() signatures stay untouched.
        if surface == "messages":
            body = am.error_body(status, etype, message, request_id=rid)
        else:
            body = _error_body_for(surface)(status, etype, message)
        return ORJSONResponse(body, status_code=status, headers=headers)

    # Pure ASGI middleware (replaces @app.middleware("http") / BaseHTTPMiddleware,
    # which breaks streaming responses during graceful shutdown — see RequestIdMiddleware).
    app.user_middleware.insert(0, Middleware(RequestIdMiddleware,
                                            max_body_mb=config.wiwi_settings.max_request_body_mb))

    # -- shared execution ------------------------------------------------------

    def _stored_input_items(body: dict[str, Any]) -> list[dict[str, Any]]:
        """The request's input items, normalized for replay on the next turn.

        A bare string prompt replays as a message item; everything else must
        already be a list of item dicts. Without this, a first-turn string
        prompt stored as-is would not decode on replay.
        """
        raw = body.get("input")
        if isinstance(raw, str):
            return [{"type": "message", "role": "user", "content": raw}]
        if isinstance(raw, list):
            return [i for i in raw if isinstance(i, dict)]
        return []

    def _streamed_response_payload(ctx, model: str,
                                   deltas: list[Any]) -> dict[str, Any]:
        """Assemble the dialect response object for a *streamed* turn.

        The stored copy is rebuilt from the IR deltas of the turn itself
        (round 195), not from flat text/thinking/tool accumulators: those
        collapsed tool-bracketed text into one message item, minted
        ``msg_<req>`` ids the stream never used, and dropped the encrypted
        reasoning blob (AUDIT #355's sibling copy). The codec owns the
        rebuild so the stored object is byte-identical to the live
        terminal object the client saw on the wire.
        """
        return orp.ResponsesStreamEncoder.rebuild_stored_payload(
            model, ctx.request_id, deltas)

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
                            codec_decode, codec_encode_response,
                            bearer_token: str | None = None,
                            codec_history=None, store_response: bool = False):
        """Request entrypoint. Owns the telemetry root span.

        Thin on purpose: the span has to be opened *outside* the execution body
        so that a ``finally`` can close it on every one of the body's many
        return paths, and it has to be opened *here* rather than inside so a
        streaming response can take ownership of ending it — for a stream the
        request is not over when this function returns, because the generator
        handed to Starlette keeps running.
        """
        # ``extract`` returns the caller's context; the root span is that
        # context's child, which is what continues an upstream trace.
        req_span = tracer.open(
            "wiwi.request", kind="server",
            context=tracer.extract(request.headers),
            **{"wiwi.surface": surface,
               "wiwi.request_id": getattr(request.state, "request_id", None),
               "wiwi.model": body.get("model") if isinstance(body, dict) else None})
        handed_off: list[bool] = [False]
        try:
            return await _chat_like(request, surface, body, codec_decode,
                                    codec_encode_response, bearer_token,
                                    codec_history, store_response,
                                    req_span, handed_off)
        finally:
            if not handed_off[0]:
                tracer.close(req_span)

    async def _chat_like(request: Request, surface: str, body: dict[str, Any],
                         codec_decode, codec_encode_response,
                         bearer_token: str | None, codec_history,
                         store_response: bool, req_span, handed_off: list[bool]):
        state_ = app.state.wiwi
        # Optional per-stage timings (``x-wiwi-timings``) for bench.py. The
        # marks are monotonic timestamps; the header renders deltas between
        # consecutive stages.
        _perf = config.wiwi_settings.perf_timings
        _marks: dict[str, float] = {"start": time.monotonic()} if _perf else {}

        def _timings() -> dict[str, str]:
            if not _perf or not _marks:
                return {}
            order = ("decode", "auth", "admit", "dispatch", "upstream",
                     "encode")
            prev = _marks["start"]
            parts: list[str] = []
            for name in order:
                at = _marks.get(name)
                if at is not None:
                    parts.append(f"{name}={int((at - prev) * 1000)}")
                    prev = at
            return {"x-wiwi-timings": ";".join(parts)} if parts else {}

        # Stored Responses state (spec B). A response id is only meaningful to
        # the key that owns it, so identity is established before the transcript
        # is read — an id must never act as a capability token. Auth here is the
        # same ``authenticate`` the request would run anyway; the object it
        # resolves is reused by the call below.
        if (codec_history is not None and isinstance(body, dict)
                and body.get("previous_response_id")):
            info0, err0 = await authenticate(request, str(body.get("model") or ""),
                                             surface, bearer_token=bearer_token)
            if err0:
                return err0
            store = state_.response_store
            with tracer.span("wiwi.retrieve", context=context_of(req_span),
                             **{"wiwi.previous_response_id":
                                body["previous_response_id"]}) as _rspan:
                prev = (await store.get(body["previous_response_id"], info0.key_id)
                        if store is not None else None)
                _rspan.set_attribute("wiwi.found", prev is not None)
            if prev is None:
                return _err(404, "not_found_error",
                            f"response '{body['previous_response_id']}' not found",
                            request, surface, span=req_span)
            # BOTH halves of the stored turn are replayed: the previous
            # turn's OUTPUT (the assistant's answer) and its INPUT (the
            # user's message). The store recorded both, but only the output
            # was read — so the user's original question never reached the
            # upstream, and every stateful multi-turn session silently lost
            # its context (AUDIT #350). The input half goes FIRST: the
            # conversation order is user-then-assistant.
            body = codec_history(body, prev.output.get("output", []),
                                 prev.input_items)
        try:
            ir_req = codec_decode(body)
        except (oc.DialectError, ValueError) as e:
            return _err(400, "invalid_request_error", str(e), request, surface,
                        span=req_span)
        if _perf:
            _marks["decode"] = time.monotonic()
        est = len(orjson.dumps(body)) // 4 if isinstance(body, dict) else 0
        info, err_resp = await authenticate(request, ir_req.model, surface,
                                            bearer_token=bearer_token)
        if err_resp:
            return err_resp
        if _perf:
            _marks["auth"] = time.monotonic()
        group, _ = state_.router.resolve_group(ir_req.model)
        if group is None:
            return _err(404, "not_found_error",
                        f"model '{ir_req.model}' not found", request, surface,
                        span=req_span)
        # ForceMapping rewrite: if the client typed a rich alias entry whose
        # ``force_mapping`` is False, the response should reveal the resolved
        # group rather than echoing the alias. The first-hop entry wins —
        # intermediate chain hops don't change the response name.
        # Default behavior (plain-string alias, or force_mapping=True) keeps
        # wiwi/LiteLLM's "echo the client's model" semantics.
        resp_model = ir_req.model
        first_hop = _alias_first_hop(
            state_.router.settings.model_group_alias, ir_req.model)
        if (isinstance(first_hop, ModelAliasEntry)
                and not first_hop.force_mapping
                and group != ir_req.model):
            resp_model = group
        # AUDIT #323, policy "warn" (default): a budget-capped key serving an
        # unpriced model keeps working — back-compat with the old $0 behaviour
        # — but the response is tagged so the bypass is observable instead of
        # silent. Mirrors the reject branch's guard in authenticate(): the
        # policy only ever concerns real completions on capped virtual keys.
        unpriced_model = ""
        if (info.key_type != "master" and info.max_budget is not None
                and ir_req.model and ir_req.model != "*"
                and config.general_settings.unpriced_model_policy == "warn"
                and await _model_group_is_unpriced(ir_req.model)):
            unpriced_model = ir_req.model
        request_id = _uuid.uuid4().hex[:16]
        rl_err = await enforce_rate_limit(info, est, request, surface, request_id)
        if rl_err:
            return rl_err
        # Budget reservation at admission (AUDIT #324). The old gate was a
        # non-atomic read at authenticate() plus a conditional UPDATE only
        # *after* the upstream had served the request, so the TOCTOU window
        # spanned the whole upstream call and overspend scaled with
        # concurrency (measured 250× at 500 concurrent). The reserve is a
        # conditional UPDATE of its own — N concurrent admissions against
        # single-request headroom now admit exactly one.
        #
        # Estimate: the request's serialized size / 4 (the same estimator the
        # TPM path uses, reconciled to actual usage at pricing time), plus a
        # small output allowance so a huge-prompt/small-cap request cannot
        # bill past the cap on output tokens alone.
        reserved = 0.0
        if info.key_type != "master" and info.max_budget is not None:
            est_spend = est / 4.0 * 1e-6 + 0.000_1
            if est_spend > 0:
                try:
                    ok = await state.auth.reserve_budget(info.key_id, est_spend)
                except Exception:  # noqa: BLE001 — a reserve outage must not 500
                    state.spend_charge_failures += 1
                    state.logs.log_proxy(
                        "error",
                        f"budget reservation failed for key {info.key_id};"
                        " admission gate degrades to post-hoc", request_id)
                    ok = False
                if not ok:
                    # Refused at the cap: same contract as the pre-flight
                    # branch in authenticate() — 402, never 429.
                    #
                    # The rate-limit slot reserved a few lines above must be
                    # REFUNDED before returning. The request never reached
                    # an upstream, so the reservation is pure phantom — and
                    # it sits in the GLOBAL scope as well as the key's, so
                    # leaving it in place throttles unrelated traffic (and
                    # unrelated TENANTS: three refusals from a budget-capped
                    # key locked an uncapped key out with a 429, AUDIT #347).
                    # Every sibling early return between the reserve and
                    # the settle funnels through ``_release_tpm_reservation``;
                    # this one predated that convention and was not
                    # converted.
                    with contextlib.suppress(Exception):
                        await state.limiter.release(info.key_id, request_id)
                    refused = RequestContext(
                        surface=surface, ir_req=ir_req, auth=info, group=group,
                        request_id=request_id, status=402)
                    refused.error = WiwiError(402, "budget_exceeded",
                                              "budget exhausted at admission")
                    state_.logs.log_request(build_log_event(refused))
                    return _err(402, "budget_exceeded",
                                f"budget exhausted ({info.spend_to_date:.4f}"
                                f"/{info.max_budget})", request, surface,
                                span=req_span)
                reserved = est_spend
        ctx = RequestContext(surface=surface, ir_req=ir_req, auth=info, group=group,
                             request_id=request_id,
                             forward_headers=_forward_headers(request),
                             session_id=_session_id(request))
        ctx.span = req_span
        ctx.budget_reserved = reserved
        success_headers = {"x-wiwi-request-id": request_id}
        # Telemetry (spec C): a caller can quote the trace id to an operator to
        # pull the exact trace. Present only when tracing is enabled.
        _trace_id = tracer.trace_id_of(req_span)
        if _trace_id:
            success_headers["x-wiwi-trace-id"] = _trace_id
        if unpriced_model:
            # A model-group name is operator-defined and may carry any
            # Unicode; a response header is latin-1 on the wire, so coerce
            # rather than let the response constructor raise (same class as
            # the warning-header truncation bug).
            success_headers["x-wiwi-unpriced-model"] = (
                unpriced_model.encode("latin-1", "replace").decode("latin-1"))
        # Per-deployment tpm admission needs the request's size up front. The
        # same body-size estimate that feeds the virtual-key limiter is good
        # enough for a sliding-window cap and costs nothing extra; it is
        # reconciled to actual usage at pricing time (AUDIT #101).
        ctx.est_tokens = est
        if _perf:
            _marks["admit"] = time.monotonic()
        # -- durable stream replay ------------------------------------------------
        # A client whose stream died (process restart, dropped connection)
        # re-POSTs the same request with `x-wiwi-stream-id: <original
        # request id>` and `Last-Event-ID: <last chunk seq>`. When the
        # journal has content past that offset we answer purely from it —
        # no upstream call, no double billing. The `done` record proves the
        # original stream terminated; without it we replay what exists and
        # then keep tailing the file in case the original request is still
        # streaming (same process) or the process died mid-flight.
        replay_id = request.headers.get("x-wiwi-stream-id", "")
        replay_after = 0
        journal_id = ctx.request_id
        lei = request.headers.get("last-event-id", "")
        if lei:
            with contextlib.suppress(ValueError):
                replay_after = int(lei)
        if (config.router_settings.stream_journal_enabled
                and replay_id and replay_after >= 0):
            # Key scoping (AUDIT #67): a journal is readable only by the
            # virtual key that created it. Master (key_id == "master")
            # owns admin-originated streams; unscoped journals predate this
            # fix and stay readable to preserve restart-replay compat.
            # One off-loop read answers the whole gate (AUDIT #237): the old
            # four calls did three whole-file ``read_bytes()`` on the event
            # loop per reconnect.
            rstate = await state_.journals.replay_state(replay_id, replay_after)
            jowner = rstate.owner
            caller_kid = getattr(info, "key_id", None)
            # AUDIT #320: ``jowner is None`` used to mean legacy-and-open.
            # The #317 in-memory intent cannot survive a restart, so a
            # journal whose owner line was lost with the process read as
            # ``None`` here and opened to *any* caller — cross-tenant
            # replay. ``has_owner_intent`` adds the disk-side signal: a
            # journal created after this process started was being opened by
            # the process that died, so its lineless state means "lost", not
            # "legacy", and the gate fails closed. Only a journal that
            # predates this process AND carries no owner line is still
            # legacy-and-open (documented restart-replay compat).
            owner_ok = (caller_kid == jowner
                        or (jowner is None
                            and not state_.journals.has_owner_intent(replay_id))
                        or (jowner == "master" and caller_kid == "master"))
            replay = rstate.frames
            complete = rstate.complete
            active = rstate.active
            # AUDIT #66: an ACTIVE journal with no replayable chunks yet
            # still means the original stream is running in THIS process —
            # falling through would dispatch and bill the request a second
            # time. `active` is the gate half the eager-touch comment
            # always intended but the gate's shape dropped.
            if (owner_ok and (replay or complete or active)):
                ctx.metadata["stream_replayed"] = True

                async def _replay_iter():
                    for _seq, chunk in replay:
                        yield chunk
                    if complete:
                        return
                    # Original stream may still be running (same process):
                    # tail the journal file until the done record lands or
                    # the stream expires.
                    last = replay[-1][0] if replay else replay_after
                    # Incremental cursor (AUDIT #282): the old loop called
                    # `read_after` (whole-file re-read + re-parse) and
                    # `is_complete` (the same, again) every 50 ms, so an idle
                    # tail burned O(journal size) of event-loop time per tick.
                    tail = state_.journals.tail_reader(replay_id, last)
                    deadline = time.time() + config.router_settings.stream_journal_ttl_s
                    # Keep-alive while the tail waits (AUDIT #504). The live
                    # path has one for exactly this window — the upstream
                    # going quiet for ``stream_ping_interval_s`` — and a
                    # reconnect is BY DEFINITION waiting on someone else's
                    # turn, so without one this is the single connection with
                    # no protection against an idle proxy reaping it (and the
                    # client reconnecting into the same silence).
                    #
                    # An SSE COMMENT, not an event: comments are ignored by
                    # every conforming parser, so this is safe on all four
                    # dialects (the live path's ``ping`` event is Anthropic's
                    # named event and does not exist elsewhere).
                    keepalive_s = config.router_settings.stream_ping_interval_s
                    next_keepalive = (time.time() + keepalive_s
                                      if keepalive_s > 0 else None)
                    while time.time() < deadline:
                        await asyncio.sleep(0.05)
                        for seq, chunk, done in await tail.read_next_async():
                            last = seq
                            if done:
                                return
                            yield chunk
                        if next_keepalive is not None and time.time() >= next_keepalive:
                            yield b": keep-alive\n\n"
                            next_keepalive = time.time() + keepalive_s
                        if (await tail.truncated_async()
                                or await state_.journals.is_expired_async(
                                    replay_id)):
                            return

                # A replay is a request like any other and is subject to the
                # same two rules as the cache-hit path above (AUDIT #120):
                #
                # 1. It must appear in the request log. This branch returns
                #    before the logging call further down, so reconnects were
                #    invisible to /admin/stats and to every rollup built on
                #    it — a client reconnecting in a loop could serve
                #    arbitrarily many requests that never showed up. The row
                #    is written with ctx.usage unset and ctx.cost 0: the
                #    journal replay consumed nothing upstream, and billing
                #    the caller for it would be the opposite error.
                # 2. It must refund the admission-time reservation.
                #    ``enforce_rate_limit`` reserved an RPM event and an
                #    estimated TPM event in both the key and global scopes
                #    before this branch was reached; the replay makes no
                #    upstream call, so those reservations are pure phantom.
                #    Left in place they throttle unrelated traffic for the
                #    full 60 s window — and because round 66 made refunds
                #    strictly identity-matched, nothing else can reclaim them
                #    afterwards, so each reconnect burns a slot for good.
                state_.logs.log_request(build_log_event(ctx))
                await _release_tpm_reservation(info, ctx)
                # Fully served from the journal: the trace ends here (spec C).
                tracer.close(ctx.span, **{"wiwi.status": 200, "wiwi.replayed": True})
                return StreamingResponse(
                    _replay_iter(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             # Same anti-buffering header the live inference
                             # path and the admin stream set (AUDIT #503).
                             # A replay is not a short burst: it TAILS a
                             # still-running turn, so a buffering proxy would
                             # hold every frame until the response closes —
                             # the client asked to resume and gets silence
                             # followed by a dump, or a proxy write timeout.
                             "x-accel-buffering": "no",
                             "x-wiwi-request-id": ctx.request_id,
                             "x-wiwi-stream-replay": replay_id})
            if owner_ok:
                # Gate missed (AUDIT #117): nothing replayable, no done
                # record, no active journal under this id. This attempt is
                # about to dispatch upstream for the logical stream the
                # client identifies by *replay_id*, so it must journal under
                # that id — not this attempt's own request id. Otherwise the
                # re-dispatch is invisible to later reconnects, which will
                # dispatch again while it is still running (double upstream
                # call, double billing): the residual window of the #66 fix,
                # one phase earlier in the timeline.
                journal_id = replay_id
        gateway = state_.gateways["chat"]
        if config.wiwi_settings.store_prompts_in_spend_logs:
            ctx.metadata["request_body"] = body
        cache = state_.response_cache
        cache_bypass = (cache is not None
                        and request.headers.get(config.cache_settings.bypass_header)
                        not in (None, "", "0", "false"))
        cache_key = ""
        if (cache is not None and not cache_bypass and not ir_req.stream
                and not any(t.builtin for t in ir_req.tools)
                # Sampling makes the output non-reproducible: an identical
                # prompt legitimately yields different text, and caching it
                # would pin the caller to the first completion for the whole
                # TTL. Only deterministic requests are admitted.
                and is_cacheable_request(ir_req)):
            cache_key = response_cache_key(
                ir_req, group, surface, getattr(info, "key_id", ""),
                # Header-coupled to the body on the Anthropic surface.
                request.headers.get("anthropic-beta", ""))
            entry = await cache.get(cache_key)
            served = None
            if entry is not None:
                # A cache is advisory: a backend that stored something that is
                # not a JSON object (a foreign writer sharing the Redis
                # namespace, an entry from an older format, a partial restore)
                # must degrade to a MISS, never raise into the request path.
                # This decode used to run unguarded, outside the try below, so
                # a corrupt entry surfaced as an undialected 500 with no
                # request-log row and an un-refunded admission reservation.
                try:
                    parsed = orjson.loads(entry.payload)
                    if not isinstance(parsed, dict):
                        raise TypeError(
                            f"cached payload is {type(parsed).__name__},"
                            " not a JSON object")
                    served = rebase_cached_body(parsed, ctx.request_id)
                except Exception as e:  # noqa: BLE001 — corrupt entry = miss
                    served = None
                    structlog.get_logger().warning(
                        "response_cache_corrupt_entry",
                        request_id=ctx.request_id,
                        detail=f"{type(e).__name__}: {e}",
                        note="dropping the entry and dispatching upstream")
                    with contextlib.suppress(Exception):
                        await cache.delete(cache_key)
            if served is not None:
                ctx.status = 200
                # cache_hit stays False: LogEvent.cache_hit means a provider
                # prompt-cache hit; a gateway response-cache hit is not one
                # and would inflate wiwi_prompt_cache_hits_total.
                ctx.metadata["response_cache_hit"] = True
                state_.logs.log_proxy("info", "response cache hit",
                                      ctx.request_id)
                # The body now carries THIS request's id, so that id must name
                # a stored object or the caller holds a dangling handle:
                # ``GET /v1/responses/{id}`` and ``previous_response_id`` both
                # dereference it. Persist the hit exactly as the upstream path
                # persists a fresh response (same key, same TTL policy) rather
                # than leaving the id resolvable only to the earlier request.
                if (store_response and state_.response_store is not None
                        and body.get("store") is not False):
                    try:
                        await state_.response_store.put(
                            f"resp_{ctx.request_id}",
                            info.key_id if info is not None else "master",
                            group or ir_req.model, _stored_input_items(body),
                            served)
                    except Exception as e:  # noqa: BLE001
                        # A cache hit must never fail the request because the
                        # store could not be written (same rule as the cache
                        # itself): log it and serve.
                        structlog.get_logger().warning(
                            "response_store_put_on_cache_hit_failed",
                            request_id=ctx.request_id, detail=str(e))
                state_.logs.log_request(build_log_event(ctx))
                # Served locally: the upstream consumed zero tokens, so the
                # estimated reservation taken at admission must be refunded.
                await _release_tpm_reservation(info, ctx)
                tracer.close(ctx.span, **{"wiwi.status": 200, "wiwi.cache_hit": True})
                return ORJSONResponse(
                    served,
                    headers={**success_headers, "x-wiwi-cache": "HIT"})
        # Singleflight identity. Reuse the cache key when the cache computed
        # one; otherwise derive the same digest, which is exactly the
        # request-equivalence relation (and includes key_id, so different
        # virtual keys never share a result).
        flight_key = ""
        if (state_.singleflight is not None and not ir_req.stream
                and not any(t.builtin for t in ir_req.tools)
                and is_cacheable_request(ir_req)):
            flight_key = cache_key or response_cache_key(
                ir_req, group, surface, getattr(info, "key_id", ""),
                request.headers.get("anthropic-beta", ""))
        try:
            if _perf:
                _marks["dispatch"] = time.monotonic()
            if ir_req.stream:
                encoder_pair = _encoder_for(surface, resp_model, ctx.request_id,
                                            include_usage=ir_req.stream_options_include_usage)
                # Open the journal BEFORE dispatch (AUDIT #117): a reconnect
                # arriving while this attempt waits for its first token must
                # find the journal active and tail it, never re-dispatch.
                journal = None
                if (config.router_settings.stream_journal_enabled
                        and state_.journals is not None):
                    journal = await state_.journals.open(
                        journal_id,
                        key_id=getattr(info, "key_id", None))
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
                    await _abandon_journal(state_, journal, journal_id)
                    await _release_tpm_reservation(info, ctx)
                    resp = _err(e.status, e.etype, e.message, request, surface,
                               span=ctx.span)
                    # The window the upstream stated rides along, exactly as on
                    # the non-streaming path: a refusal that arrives BEFORE the
                    # stream starts is still a normal error response, so the
                    # client can be told when to come back (a spent OpenCode
                    # free allowance states its reset window in the body and
                    # in no header at all — AUDIT #600).
                    if e.retry_after:
                        resp.headers["Retry-After"] = str(
                            int(max(1.0, e.retry_after)))
                    return resp
                except BaseException:
                    # Non-WiwiError failure: release the pump's upstream
                    # connection before letting the outer handler deal with it.
                    await stream.aclose()
                    await _abandon_journal(state_, journal, journal_id)
                    raise
                # The upstream connected and the first delta exists: this
                # journal WILL carry client-visible data, so its ownership
                # line must be durable before that data is readable (AUDIT
                # #320). Deliberately after the connect-failure branches — a
                # failed connect abandons the journal and no client ever
                # replays it, so persisting first would only add an fsync to
                # every error path.
                #
                # If the durable persist fails, the journal is DROPPED and the
                # stream proceeds without one (AUDIT #380). Returning 503 here
                # — which is what this site used to do — satisfied #320's letter
                # but blew up its blast radius: an unwritable journal directory
                # (the shipped image's relative default resolved against a
                # root-owned CWD, AUDIT #380) refused EVERY virtual-key stream
                # while master streams and all non-streaming traffic kept
                # working, so the deployment looked healthy. Dropping the
                # journal is strictly safer than persisting an ownerless one —
                # with no file on disk there is no replay data to leak, which
                # is exactly the hole #320 closed — and it costs only
                # reconnect-resume for this one request.
                if not await _persist_journal_owner(state_, journal,
                                                    journal_id, info):
                    await _drop_journal(state_, journal, journal_id)
                    journal = None
                it = _stream_response(state_, ctx, encoder_pair, surface,
                                      stream, first, journal=journal,
                                      journal_id=journal_id,
                                      journal_disabled=journal is None,
                                      event_ids=config.router_settings.stream_event_ids,
                                      response_store=(state_.response_store
                                                      if store_response else None),
                                      store_body=body,
                                      store_model=resp_model,
                                      store_group=group or ir_req.model)
                # The response is a generator: the request is not finished when
                # this function returns, so the entrypoint must not end the span.
                # ``_stream_response`` owns it now and ends it in its teardown
                # tail, after the body has actually been served.
                handed_off[0] = True
                return StreamingResponse(
                    it,
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             # nginx and friends buffer an SSE body unless told
                             # not to, which turns a stream into one burst at
                             # the end and can trip a proxy write timeout on a
                             # long turn. /admin/stream already set this; the
                             # inference surfaces did not (AUDIT #156).
                             "x-accel-buffering": "no",
                             # The caller-visible translation warnings. The
                             # first delta was pulled above, so the pump has
                             # already encoded the request and the warnings
                             # are known by the time the headers are built
                             # (AUDIT #498).
                             **_with_warnings({}, ctx),
                             **_timings(),
                             **success_headers})
            if flight_key:
                async def _leader_complete() -> FlightResult:
                    t = await gateway.complete(ctx)
                    p = codec_encode_response(ctx, t, resp_model, ctx.request_id)
                    return FlightResult(payload=p, usage=t.usage,
                                        cost=ctx.cost, turn=t)

                result, joined = await state_.singleflight.run(
                    flight_key, _leader_complete)
                if joined:
                    # Served from the leader's upstream call: charge nothing,
                    # refund the admission reservation, and log the request
                    # with the shared usage. This mirrors a response-cache hit
                    # (the upstream consumed no tokens for this caller).
                    await _release_tpm_reservation(info, ctx)
                    ctx.usage = result.usage
                    ctx.cost = 0.0
                    ctx.status = 200
                    ctx.metadata["singleflight_joined"] = True
                    served = rebase_cached_body(result.payload, ctx.request_id)
                    if (store_response and state_.response_store is not None
                            and body.get("store") is not False):
                        try:
                            await state_.response_store.put(
                                f"resp_{ctx.request_id}",
                                info.key_id if info is not None else "master",
                                group or ir_req.model,
                                _stored_input_items(body), served)
                        except Exception as e:  # noqa: BLE001
                            structlog.get_logger().warning(
                                "response_store_put_on_singleflight_failed",
                                request_id=ctx.request_id, detail=str(e))
                    state_.logs.log_request(build_log_event(ctx))
                    return ORJSONResponse(
                        served,
                        headers={**success_headers,
                                 "x-wiwi-singleflight": "JOINED"})
                turn = result.turn
                payload = result.payload
            else:
                turn = await gateway.complete(ctx)
                payload = codec_encode_response(ctx, turn, resp_model,
                                                ctx.request_id)
            if _perf:
                # The leader path folds complete+encode into one call, so the
                # two marks land together; the upstream still dominates and
                # the split is exact on the non-flight path below.
                _marks["upstream"] = time.monotonic()
                _marks["encode"] = time.monotonic()
            ctx.status = 200
            if config.wiwi_settings.store_prompts_in_spend_logs:
                ctx.metadata["response_body"] = _serialize_turn(turn, payload)
            await _record_tpm_usage(info, ctx)
            # The admission reservation (AUDIT #324) has done its job — the
            # cap bound at dispatch time. The charge itself goes through
            # record_spend like every other request, which releases the
            # reservation before the conditional UPDATE so the same dollars
            # are never tested twice, and keeps the crossing/true-up/402
            # semantics pinned by rounds 45, 55 and #179 intact (a charge
            # that crosses the cap is still real money: recorded, and the
            # response refused).
            if (info and info.key_type != "master"
                    and not await record_spend(info.key_id, ctx.cost, ctx)):
                    ctx.status = 402
                    # Log exactly once: the over-budget status replaces the
                    # success event, so the request is not double-counted in
                    # stats/rollups (AUDIT #90).
                    state_.logs.log_request(build_log_event(ctx))
                    return _err(402, "budget_exceeded",
                                "virtual key budget exhausted", request, surface,
                                span=ctx.span)
            # Cache only AFTER the budget decision (AUDIT #116): caching above
            # this point stored the payload of a request that is about to be
            # refused with 402, and the hit path serves it as a free 200 with
            # zero spend recorded for the whole TTL.
            if cache is not None and cache_key and ctx.status == 200 and not ctx.error:
                await cache.set(
                    cache_key,
                    CacheEntry(
                        payload=orjson.dumps(payload),
                        media_headers={"content-type": "application/json"},
                        stored_at=time.time(),
                        request_id=ctx.request_id,
                        model=resp_model,
                    ))
            if (store_response and state_.response_store is not None
                    and body.get("store") is not False):
                # Persist the dialect response object so GET replays it verbatim,
                # and the request's normalized input items so the next turn can be
                # rebuilt. ``store: false`` opts out per request.
                with tracer.span("wiwi.persist", context=context_of(req_span),
                                 **{"wiwi.store_id": f"resp_{ctx.request_id}"}):
                    await state_.response_store.put(
                        f"resp_{ctx.request_id}",
                        info.key_id if info is not None else "master",
                        group or ir_req.model, _stored_input_items(body), payload)
            state_.logs.log_request(build_log_event(ctx))
            tracer.close(ctx.span, **{"wiwi.status": ctx.status,
                                      "wiwi.cost": ctx.cost or None})
            success_headers.update(_timings())
            return ORJSONResponse(payload, headers=_with_warnings(success_headers,
                                                                  ctx))
        except Exception as e:  # noqa: BLE001
            if isinstance(e, WiwiError):
                ctx.status = e.status
                ctx.error = e
                state_.logs.log_request(build_log_event(ctx))
                # The upstream failed before delivering usage: refund the
                # admission-time reservation so it cannot throttle later
                # requests (AUDIT #70).
                await _release_tpm_reservation(info, ctx)
                resp = _err(e.status, e.etype, e.message, request, surface,
                            span=ctx.span)
                if e.retry_after:
                    resp.headers["Retry-After"] = str(int(max(1.0, e.retry_after)))
                return resp
            ctx.status = 500
            state_.logs.log_proxy("error", f"internal error: {e}", ctx.request_id)
            state_.logs.log_request(build_log_event(ctx))
            await _release_tpm_reservation(info, ctx)
            return _err(500, "api_error", "internal gateway error", request, surface,
                        span=ctx.span)
        finally:
            # Backstop for the non-streaming paths: every return above closes the
            # span already (via ``_err`` or an explicit ``tracer.close``) and
            # ``close`` is idempotent, so this only catches a branch that forgot.
            # It must NOT run once the span has been handed to a stream: this
            # function returns as soon as the generator is built, long before the
            # response is served, and closing here would freeze the span without
            # the stream-only attributes and end it mid-flight.
            if not handed_off[0]:
                tracer.close(ctx.span, **{"wiwi.status": ctx.status or 500})

    def _encoder_for(surface: str, model: str, req_id: str,
                     include_usage: bool = False):
        if surface == "chat":
            return oc.ChatStreamEncoder(model, req_id,
                                        include_usage=include_usage), "chat"
        if surface == "completions":
            return ocmpl.CompletionStreamEncoder(
                model, req_id, include_usage=include_usage), "chat"
        if surface == "messages":
            return am.AnthropicStreamEncoder(model, req_id), "anthropic"
        return orp.ResponsesStreamEncoder(model, req_id), "responses"

    def _capture_delta(
        d: Any,
        ctx: RequestContext,
        text_buf: list[str],
        thinking_buf: list[str],
        tools_map: dict[int, dict[str, Any]],
        deltas: list[Any] | None = None,
    ) -> None:
        """Accumulate streaming deltas into serializable buffers for log capture.

        Tool arguments accumulate as a LIST joined at teardown, not with
        ``+=``. Per-fragment concatenation is O(n^2) in total bytes, which the
        gateway pump already fixed for text and tool args (AUDIT #104) — a
        multi-MB argument payload streamed in many fragments stalled the event
        loop here, on every concurrent request, only when prompt capture was
        enabled (so the fix is easy to miss).

        ``deltas`` is the ordering-faithful copy the Responses store needs
        (round 195): re-derived from flat buffers it collapsed interleaved
        items and dropped encrypted blobs, so when the caller asks for it
        the raw deltas are appended verbatim.
        """
        if deltas is not None:
            deltas.append(d)
        from wiwi.streaming import deltas as dl
        if isinstance(d, dl.TextDelta):
            text_buf.append(d.text)
        elif isinstance(d, dl.ThinkingDelta):
            thinking_buf.append(d.text)
        elif isinstance(d, dl.ToolCallOpen):
            tools_map[d.index] = {"id": d.id, "name": d.name, "_args": [],
                                  "builtin": d.builtin}
        elif isinstance(d, dl.ToolCallArgsDelta):
            entry = tools_map.get(d.index)
            if entry is not None:
                entry["_args"].append(d.args_fragment)
        elif isinstance(d, dl.UsageFinal):
            ctx._stream_usage = d  # type: ignore[attr-defined]
        elif isinstance(d, dl.Finish):
            ctx.stop_reason = d.stop_reason

    async def _stream_response(state_, ctx, encoder_pair, surface,
                               stream, first=None, event_ids=False,
                               journal=None, journal_id=None,
                               journal_disabled=False,
                               response_store=None, store_body=None,
                               store_model="", store_group=""):
        from wiwi.streaming import deltas as dl
        encoder, style = encoder_pair
        errored = False
        completed = False
        # The client's ``Last-Event-ID`` names a single FRAME, so the sequence
        # counter counts frames; the chunk counter is separate and is what the
        # ``wiwi.chunks`` attribute reports (AUDIT #493).
        _seq = 0
        _chunks = 0
        # Capture is needed by the spend log and by the Responses store (spec B);
        # with neither asking for it the accumulators stay empty and no per-token
        # string work happens.
        capture = (config.wiwi_settings.store_prompts_in_spend_logs
                   or response_store is not None)
        store_prompts = config.wiwi_settings.store_prompts_in_spend_logs
        if journal_id is None:
            journal_id = ctx.request_id
        if (journal is None and not journal_disabled
                and config.router_settings.stream_journal_enabled
                and state_.journals is not None):
            # Fallback path: the caller did not pre-open a journal. Prefer the
            # caller-opened one — it is open before the first upstream call,
            # which is what makes the reconnect gate correct (AUDIT #117).
            journal = await state_.journals.open(
                journal_id, key_id=getattr(ctx.auth, "key_id", None))
            # Same durability contract as the pre-dispatch site (AUDIT #320):
            # this generator is about to emit its first chunk into the
            # journal, so the owner line must be on disk first. If it cannot
            # be made durable, drop the journal entirely — nothing was
            # written yet, so nothing ownerless reaches disk.
            if not await _persist_journal_owner(state_, journal, journal_id,
                                                ctx.auth):
                await _drop_journal(state_, journal, journal_id)
                journal = None
                # Nothing is left to re-open it on a later chunk, so this
                # request streams without a journal (AUDIT #380).
                journal_disabled = True
        stream_text: list[str] = []
        stream_thinking: list[str] = []
        stream_tools: dict[int, dict[str, Any]] = {}
        # The ordering-faithful copy for the Responses store (round 195):
        # allocated unconditionally but only appended when a store is in play.
        stream_deltas: list[Any] = []
        # The delta-stream contract (streaming/machine.py), observed on the
        # live path. Purely diagnostic: observe() never raises and the result
        # only ever becomes a log line, so a contract violation can no longer be
        # invisible without also being able to break a response.
        stream_state = DeltaStreamState()
        _contract_log = structlog.get_logger("wiwi.stream")
        watched_deltas = 0

        def _watch_delta(delta: Any) -> None:
            nonlocal watched_deltas
            problem = stream_state.observe(delta)
            if problem is not None:
                _contract_log.warning(
                    "stream_contract_violation",
                    request_id=ctx.request_id,
                    detail=problem, after=watched_deltas,
                    state=stream_state.explain())
            watched_deltas += 1

        async def _teardown_tail() -> None:
            """Bookkeeping that must complete even when the peer disconnects.

            Split out so the caller can ``asyncio.shield`` it (AUDIT #222).
            """
            if journal is not None:
                with contextlib.suppress(Exception):
                    await journal.finish(_seq)
                state_.journals.release(journal_id)
            # Release the gateway pump's upstream connection no matter how we
            # leave this generator. The body (`encoder.feed(...)`) can raise, and
            # Starlette's StreamingResponse does NOT aclose our async iterator on
            # error — an `async for` that raises leaves `stream` abandoned, so
            # the pump task keeps its upstream socket checked out until GC.
            # aclose() here drives the pump's own teardown (site-2 `finally`).
            with contextlib.suppress(Exception):
                await stream.aclose()
            if store_prompts:
                stream_usage = getattr(ctx, "_stream_usage", None)
                ctx.metadata["response_body"] = {
                    "text": "".join(stream_text),
                    "thinking": [{"text": t} for t in stream_thinking],
                    "tool_calls": [
                        {"id": tc["id"], "name": tc["name"],
                         "arguments": "".join(tc["_args"]),
                         "builtin": tc["builtin"]}
                        for _, tc in sorted(stream_tools.items())],
                    "stop_reason": ctx.stop_reason or "stop",
                    "usage": ({"prompt_tokens": stream_usage.prompt,
                               "completion_tokens": stream_usage.output,
                               "cached_tokens": stream_usage.cached,
                               "reasoning_tokens": stream_usage.reasoning}
                              if stream_usage else None),
                    "streamed": True,
                }
            state_.logs.log_request(build_log_event(ctx))
            await _record_tpm_usage(ctx.auth, ctx)
            if (response_store is not None and completed and not errored
                    and store_body is not None and store_body.get("store") is not False):
                # Only a clean StreamEnd makes a response continuable. A client
                # disconnect or an abandoned generator can still have deltas and
                # no error frame; rebuilding those would mint a completed object
                # for a turn that never finished.
                # are the same content the non-streaming path serializes, so a
                # streamed response can be continued by id like any other. A
                # store failure must not abort the teardown or surface as an
                # error to the caller — but it must not pass silently either:
                # the id the caller will reference later simply won't exist.
                with tracer.span(
                        "wiwi.persist", context=context_of(ctx.span),
                        **{"wiwi.store_id": f"resp_{ctx.request_id}"}):
                    try:
                        await response_store.put(
                            f"resp_{ctx.request_id}",
                            ctx.auth.key_id if ctx.auth else "master",
                            store_group, _stored_input_items(store_body),
                            _streamed_response_payload(
                                ctx, store_model, stream_deltas))
                    except Exception as e:  # noqa: BLE001 — the turn served; the store is best-effort
                        state_.logs.log_proxy(
                            "error",
                            f"response store persist failed: {type(e).__name__}: {e}",
                            ctx.request_id)
                        structlog.get_logger().warning(
                            "response_store_put_on_stream_failed",
                            request_id=ctx.request_id,
                            detail=f"{type(e).__name__}: {e}")
            if ctx.usage:
                if ctx.auth and ctx.auth.key_type != "master":
                    # The response was already streamed, so a budget breach
                    # can't turn into a 402 here. record_spend releases the
                    # admission reservation before the conditional UPDATE (so
                    # the cap test is not skewed by stale reserved dollars,
                    # AUDIT #324) and still trues up the charge — the upstream
                    # billed it — which is what makes the *next* request fail
                    # admission instead of letting spend run past the cap
                    # forever (AUDIT_REPORT C1).
                    await record_spend(ctx.auth.key_id, ctx.cost, ctx)
            else:
                # No usage: the upstream died, or the peer hung up before
                # UsageFinal. The tail runs on every exit, so give back
                # everything admission reserved — the budget estimate
                # (AUDIT #324) AND the RPM/estimated-TPM slot. The rate-limit
                # half used to be refunded only on the non-streaming error
                # exits, so a stream that opened and then aborted burned one
                # RPM slot and ``len(body)/4`` estimated TPM for the rest of
                # the 60s window, in the key scope *and* the global one — the
                # exact throttling failure mode of AUDIT #347. It runs for
                # master too: a global rpm/tpm cap reserves a slot for every
                # caller, master included, and only the key-scoped windows
                # skip it. ``_release_tpm_reservation`` is a no-op when usage
                # is known, so it cannot double-refund a served turn.
                await _release_tpm_reservation(ctx.auth, ctx)
            # Telemetry (spec C): the request span stays open for the whole life
            # of a stream — the body runs inside the generator handed to
            # Starlette, long after the entrypoint returned — so this teardown,
            # which is guaranteed to run last (shielded, AUDIT #222), is where
            # the request actually ends. Attributes come from fields the same
            # tail already computed. ``tracer.close`` is idempotent.
            tracer.close(ctx.span, **{"wiwi.status": ctx.status or 200,
                                      "wiwi.cost": ctx.cost or None,
                                      "wiwi.streamed": True,
                                      "wiwi.errored": errored,
                                      "wiwi.chunks": _chunks,
                                      "wiwi.ttft_ms": (
                                          int((ctx.first_token_at - ctx.started) * 1000)
                                          if ctx.first_token_at else None)})

        try:
            async def _emit(chunk: bytes) -> None:
                nonlocal _seq, _chunks
                frames = split_frames(chunk)
                if not frames:
                    return
                _chunks += 1
                # The sequence counter counts FRAMES, not chunks: the client's
                # ``Last-Event-ID`` names a single frame, and the journal
                # slices a chunk at frame granularity against that cursor
                # (AUDIT #493). A chunk carries several frames (the encoders'
                # terminal frames do), so the chunk's frame range is
                # ``first_seq .. _seq`` and both ends go to the journal.
                first_seq = _seq + 1
                if event_ids:
                    # Each sub-frame takes the next id so a reconnect cursor
                    # lands on a single frame rather than a whole chunk
                    # (AUDIT #283). The journal records the same numbering,
                    # so Last-Event-ID means one thing to both sides.
                    tagged, last_id = _inject_id(chunk, first_seq)
                    _seq = last_id
                else:
                    _seq += len(frames)
                    tagged = chunk
                if journal is not None:
                    with contextlib.suppress(Exception):
                        await journal.append(_seq, tagged, first_seq=first_seq)
                yield tagged
            if first is not None:
                if isinstance(first, dl.StreamError):
                    errored = True
                    ctx.status = 502
                    ctx.error = WiwiError(502, "api_error", first.message)
                if capture:
                    _capture_delta(first, ctx, stream_text, stream_thinking,
                                   stream_tools,
                                   stream_deltas if response_store is not None else None)
                if not isinstance(first, bytes):
                    _watch_delta(first)
                # A raw ``bytes`` item is a pre-framed SSE keep-alive from the
                # gateway pump, not an IR delta: the encoders' ``feed``
                # isinstance-checks on delta classes only, so passing it there
                # returned None and the frame was silently discarded — the
                # configured ``stream_ping_interval_s`` was a no-op and an idle
                # proxy/ALB still reaped a long thinking turn (AUDIT #177). It
                # still goes through ``_emit`` so it is journaled with a
                # sequence id like every other frame.
                chunk = first if isinstance(first, bytes) else encoder.feed(first)
                if chunk:
                    async for t in _emit(chunk):
                        yield t
            async for d in stream:
                if isinstance(d, dl.StreamEnd):
                    completed = True
                if isinstance(d, dl.StreamError) and not errored:
                    errored = True
                    ctx.status = 502
                    ctx.error = WiwiError(502, "api_error", d.message)
                if capture:
                    _capture_delta(d, ctx, stream_text, stream_thinking,
                                   stream_tools,
                                   stream_deltas if response_store is not None else None)
                if not isinstance(d, bytes):
                    _watch_delta(d)
                # Same keep-alive passthrough as the ``first`` block above.
                chunk = d if isinstance(d, bytes) else encoder.feed(d)
                if chunk:
                    async for t in _emit(chunk):
                        yield t
            # terminal frames, correct order per dialect:
            if errored:
                # The error frame was already emitted by the encoder's feed(),
                # which also closed any open content block. Still terminate the
                # client's stream so it is well-formed: a lone Anthropic
                # ``error`` with no following ``message_stop`` leaves Claude
                # Code's SSE reader waiting for the stream to end. The
                # ``message_delta`` carries the final usage and stop_reason —
                # without it a failed turn contributed nothing to the client's
                # token accounting and reported no stop_reason at all
                # (AUDIT #156). (OpenAI/Responses clients close on the error
                # frame and need neither.)
                if style == "anthropic":
                    chunk = encoder.final_frame()
                    if chunk:
                        async for t in _emit(chunk):
                            yield t
                    async for t in _emit(b"event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"):
                        yield t
            elif style == "chat":
                chunk = encoder.final_frame()
                async for t in _emit(chunk):
                    yield t
                async for t in _emit(b"data: [DONE]\n\n"):
                    yield t
            elif style == "anthropic":
                chunk = encoder.final_frame()
                async for t in _emit(chunk):
                    yield t
                async for t in _emit(b"event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"):
                    yield t
            else:
                chunk = encoder._completed()
                async for t in _emit(chunk):
                    yield t
        finally:
            # The whole teardown tail must survive client disconnect. It used
            # to be a bare sequence whose FIRST statement was an await, so the
            # pending cancellation was re-delivered there and nothing after it
            # ran: the request was never logged, never charged, its TPM
            # reservation never reconciled and the journal handle leaked — on
            # the ordinary abort path (browser tab closed, Ctrl-C, proxy
            # timeout). ``contextlib.suppress(Exception)`` does not catch
            # ``CancelledError`` (BaseException), and Starlette cancels this
            # generator when the peer goes away (AUDIT #222). Shielding the tail
            # makes it uncancellable, so bookkeeping completes and the
            # cancellation is honoured on the next await outside the shield.
            with contextlib.suppress(BaseException):
                await asyncio.shield(_teardown_tail())

    # -- surfaces ---------------------------------------------------------------
    # Extracted to ``wiwi/server/routers/v1_api.py`` — behaviour unchanged:
    # chat/completions, playground, responses (+ stored-response
    # replay/delete), legacy completions, messages, count_tokens and models.
    # The realtime websocket lives in ``wiwi/server/routers/realtime.py``.
    app.include_router(v1_api_router.make_router(
        state, authenticate=authenticate, run_chat_like=run_chat_like,
        json_body=json_body, _err=_err))
    app.include_router(realtime_router.make_router(
        state, config, _model_group_is_unpriced=_model_group_is_unpriced,
        _record_tpm_usage=_record_tpm_usage, _realtime_relay=_realtime_relay,
        _bill_realtime_session=_bill_realtime_session, _rt_log=_rt_log))

    # -- health & metrics --------------------------------------------------------
    # Extracted to ``wiwi/server/routers/system.py``; behaviour unchanged
    # (liveness/readiness body and the always-registered Prometheus endpoint,
    # which answers 404 when the exporter is off — AUDIT #349). The path is
    # resolved here because the route registers at the configured path.
    metrics_path = config.router_settings.prometheus_path
    app.include_router(system_router.make_router(
        state, config, metrics_path=metrics_path, _rt_counters=_RT_COUNTERS))

    # -- admin -------------------------------------------------------------------
    # -- admin: virtual keys ---------------------------------------------------
    # The five /admin/keys routes live in ``wiwi/server/routers/admin_keys.py``
    # (a router module + a service + Pydantic request models). It is mounted
    # here because the service is a per-app object.
    app.include_router(
        admin_keys_router.make_router(
            KeyAdminService(state, config.router_settings.priority_lanes)),
    )

    # -- admin: logs & stream ----------------------------------------------------
    # Extracted to ``wiwi/server/routers/admin_logs.py``; behaviour unchanged
    # (request logs, per-request metrics, proxy/audit logs, and the SSE
    # stream channel).
    app.include_router(admin_logs_router.make_router(
        state, json_body=json_body, _err=_err))

    # -- admin: providers & pools ------------------------------------------------
    # Every /admin/* route (and the SSE channel below) authorizes through the
    # module-level ``require_admin`` dependency
    # (``wiwi/server/dependencies.py``), which resolves a signed session cookie
    # OR the bearer master key. README.md and docs/ADMIN.md both promise that
    # either credential reaches the admin API, so there is deliberately no
    # bearer-only guard to drift away from it.
    # Extracted to ``wiwi/server/routers/admin_providers.py``; behaviour
    # unchanged (catalog, providers CRUD, key CRUD/reveal/test, export/import,
    # and GET {name}/models).
    app.include_router(admin_providers_router.make_router(
        state, json_body=json_body,
        _err=_err, _apply_cline_default_models=_apply_cline_default_models,
        _provider_models_url=_provider_models_url,
        _parse_models_response=_parse_models_response,
        _redact_url_secret=_redact_url_secret))

    # -- whole-gateway backup / restore ---------------------------------------
    # Extracted to ``wiwi/server/routers/admin_backup.py``; behaviour
    # unchanged. The logic lives in wiwi/server/backup.py; the routes are thin
    # wrappers so the admin-auth, audit, and error-shape conventions stay in
    # one place. The Cline reconciler is passed in (app.py owns it — the
    # lifespan calls it too).
    app.include_router(admin_backup_router.make_router(
        state, config, json_body=json_body, _err=_err,
        _apply_cline_default_models=_apply_cline_default_models))

    # GET /admin/providers/{name}/models lives in
    # ``wiwi/server/routers/admin_providers.py``, registered with the other
    # provider routes above.
    # -- admin: Cline global default-model settings ------------------------------
    # Extracted to ``wiwi/server/routers/admin_cline_settings.py``; behaviour
    # unchanged (the global models fetch with its 5-minute cache, the
    # default-models settings pair and the per-model delete). The reconciler
    # stays in app.py (``_apply_cline_default_models``) — the lifespan calls
    # it too, so it is passed in here.
    app.include_router(admin_cline_settings_router.make_router(
        state, json_body=json_body, _err=_err,
        _apply_cline_default_models=_apply_cline_default_models,
        _provider_models_url=_provider_models_url,
        _parse_models_response=_parse_models_response))
    # -- admin: model groups, models & routing -----------------------------------
    # Extracted to ``wiwi/server/routers/admin_models.py``; behaviour
    # unchanged (deployments POST/DELETE, the models listing, the model-group
    # PATCH and the alias batch endpoint, with the _admin_group /
    # _alias_name_conflict helpers).
    app.include_router(admin_models_router.make_router(
        state, json_body=json_body, _err=_err,
        _validate_alias_batch=_validate_alias_batch))
    # -- admin: logs & stats -------------------------------------------------------
    # /admin/logs/proxy and /admin/logs/audit live in
    # ``wiwi/server/routers/admin_logs.py`` (registered with the other log
    # routes above); the two stats routes live in
    # ``wiwi/server/routers/admin_stats.py``. Behaviour unchanged.
    app.include_router(admin_stats_router.make_router(
        state, _err=_err))
    # -- admin: model pricing + alert rules -------------------------------------
    # Extracted to ``wiwi/server/routers/admin_pricing.py``; behaviour
    # unchanged (pricing GET/PUT/DELETE and the alert-rules storage pair).
    app.include_router(admin_pricing_router.make_router(
        state, json_body=json_body, _err=_err))
    # -- admin: users ----------------------------------------------------------
    # Extracted to ``wiwi/server/routers/admin_users.py``; behaviour unchanged.
    app.include_router(admin_users_router.make_router(
        state, json_body=json_body, _err=_err))
    # -- public auth surface (signup / login / logout / me) --------------------
    # Extracted to ``wiwi/server/routers/auth.py``; behaviour unchanged —
    # same cookies, same throttle scopes, same status codes. The playground
    # key policy lives in ``wiwi/server/services/playground.py``.
    app.include_router(auth_router.make_router(
        state, config, json_body=json_body,
        _err=_err))

    # -- Cline OAuth (paste-code flow; tokens live in the provider key pool) -----
    # Extracted to ``wiwi/server/routers/cline_oauth.py``; behaviour
    # unchanged. The module also owns the public /cline/oauth/callback the
    # browser returns to — one flow, one file.
    app.include_router(cline_oauth_router.make_router(
        state, config, json_body=json_body, _err=_err))

    # -- Hermes (Nous Portal) OAuth — device-code flow ------------------------
    # Extracted to ``wiwi/server/routers/hermes_oauth.py``; behaviour
    # unchanged. The account/label policy (rotate-in-place, placeholder
    # drop, per-account circuits) lives with the routes there.
    app.include_router(hermes_oauth_router.make_router(
        state, json_body=json_body, _err=_err))
    # -- public: secret-free model catalog (no auth) ----------------------------
    # Extracted to ``wiwi/server/routers/public.py``; behaviour unchanged.
    app.include_router(public_router.make_router(state))

    # -- admin UI (built SPA; wiwi/server/static produced by `cd web && npm run build`)
    static_dir = Path(os.environ.get("WIWI_STATIC_DIR")
                      or Path(__file__).parent / "static")

    class SPAStaticFiles(StaticFiles):
        """StaticFiles with SPA history fallback: unknown paths get index.html
        so client-side routes like /login or /app survive hard refresh.

        The HTML entry document is served ``no-cache``: hashed assets under
        /assets/ may be cached forever, but a heuristically cached index
        pins the previous build's hashed filenames, so users keep getting
        the old SPA after a redeploy until the heuristic expires.

        Missing assets never use the history fallback. Returning index.html
        for a removed JavaScript chunk produces a misleading import error.
        """

        async def get_response(self, path: str, scope):
            try:
                resp = await super().get_response(path, scope)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                if path == "assets" or path.startswith("assets/"):
                    return PlainTextResponse(
                        "Static asset not found", status_code=404,
                        headers={"Cache-Control": "no-store"})
                return await self._spa_fallback(scope)
            if resp.status_code == 404:
                if path.startswith("assets/"):
                    return PlainTextResponse(
                        "Static asset not found", status_code=404,
                        headers={"Cache-Control": "no-store"})
                return await self._spa_fallback(scope)
            if path in ("", ".", "index.html") or path.endswith(".html"):
                resp.headers["Cache-Control"] = "no-cache"
            return resp

        async def _spa_fallback(self, scope):
            resp = await super().get_response("index.html", scope)
            resp.headers["Cache-Control"] = "no-cache"
            return resp

    if static_dir.is_dir():
        # Mount the SPA at the root.  API routes (/admin/*, /v1/*, /auth/*,
        # /public/*, /health) are registered above and matched first, so they
        # still return JSON.  Any path that doesn't match an API route falls
        # through to the SPA and serves index.html (client-side routing).
        app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True),
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
