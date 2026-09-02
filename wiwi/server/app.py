"""FastAPI app factory: middleware chain, the three surfaces, admin API, health."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import orjson
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
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

        # Normalize /V1/ → /v1/ for API routes — many clients and proxies send
        # uppercase path prefixes; FastAPI route matching is case-sensitive.
        path = scope.get("path", "")
        if path[:4] == "/V1/" or path == "/V1":
            scope["path"] = path[:1] + "v1" + path[3:]
            # raw_path follows the same pattern
            rp = scope.get("raw_path")
            if isinstance(rp, bytes) and rp[:4] == b"/V1/":
                scope["raw_path"] = b"/v1/" + rp[4:]
            elif isinstance(rp, bytes) and rp == b"/V1":
                scope["raw_path"] = b"/v1"

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
                if seen_bytes > self.max_body_bytes:
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



from wiwi.auth.keys import mask_key
from wiwi.auth.service import AuthService
from wiwi.auth.users import SESSION_TTL, UserInfo, UserService, sign_session, verify_session
from wiwi.config import (
    PROVIDER_TYPES,
    ConfigError,
    ModelAliasEntry,
    WiwiConfig,
    _interpolate,
    load_config,
    load_env,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway, build_log_event
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.logging_core.events import LogEvent
from wiwi.logging_core.subsystem import LoggingSubsystem, encode_sse, public_dict
from wiwi.providers import cline_oauth, workbuddy_auth
from wiwi.providers.base import ProviderKeyRef, WiwiError
from wiwi.providers.registry import fresh_adapter
from wiwi.providers.workbuddy_auto_refresh import refresh_key_now
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

# Playground keys are minted automatically on login/signup, so they must be
# bounded: a TTL keeps abandoned ones from living forever, and a per-user cap
# stops repeated logins from accumulating unlimited live credentials.
_PLAYGROUND_KEY_TTL_S = 24 * 3600.0
_MAX_PLAYGROUND_KEYS_PER_USER = 5


def _client_ip(request: Request) -> str:
    """Best-effort client IP for throttling.

    X-Forwarded-For is only consulted for *rate limiting*, never for authn or
    for building URLs — a spoofed value can at worst make an attacker share a
    bucket with someone else (self-limiting), and never grant access. The
    left-most entry is used because that is the original client under a
    well-behaved proxy chain.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


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

    async def check(self, scope: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds) for the given scope."""
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


def _coerce_alias_value(v: object) -> str | ModelAliasEntry | None:
    """Normalize a ``model_group_alias`` value to ``str | ModelAliasEntry``.

    - ``str`` → returned as-is (back-compat for plain YAML aliases).
    - ``ModelAliasEntry`` → returned as-is.
    - ``dict`` (from JSON / DB overlay) → validated via ``ModelAliasEntry``;
      returns ``None`` if the dict is malformed (caller treats as error).
    Anything else returns ``None`` (caller treats as error)."""
    if isinstance(v, ModelAliasEntry):
        return v
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        try:
            return ModelAliasEntry.model_validate(v)
        except (ValueError, TypeError):
            return None
    return None


def _alias_value_to_json(v: object) -> str | dict:
    """Serialize a ``model_group_alias`` value for JSON output.

    Plain strings pass through; ``ModelAliasEntry`` values are dumped via
    ``model_dump`` so ``/admin/models`` and ``/public/models`` always emit a
    consistent shape for clients to parse."""
    if isinstance(v, ModelAliasEntry):
        return v.model_dump()
    return v  # str, or whatever was loaded (string back-compat)


def _alias_value_eq(a: object, b: object) -> bool:
    """Compare two alias values regardless of form (str vs ModelAliasEntry
    vs dict). Used to compute the overlay diff at write time without
    spuriously re-persisting values that didn't change form."""
    a_d = _alias_value_to_json(a) if not isinstance(a, dict) else a
    b_d = _alias_value_to_json(b) if not isinstance(b, dict) else b
    return a_d == b_d


def _alias_first_hop(entry_map: dict, name: str) -> str | ModelAliasEntry | None:
    """Look up the entry the client typed (first hop only). Returns
    ``None`` if the name is not a direct alias. Used to decide whether the
    response should echo the alias or show the resolved group."""
    return entry_map.get(name)


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
        entry = _coerce_alias_value(raw)
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
        rs = config.router_settings
        self.limiter = RateLimiter(global_rpm=rs.global_rpm, global_tpm=rs.global_tpm)
        # Abuse throttles for unauthenticated endpoints (login/signup). These
        # count failures only, so real users are never locked out by success.
        self.login_throttle = _AttemptThrottle(limit=10, window_s=300.0)
        self.signup_throttle = _AttemptThrottle(limit=5, window_s=3600.0)
        self.auth: AuthService | None = None
        self.gateways: dict[str, Gateway] = {}
        self.alert_rules: list[dict[str, Any]] = []
        self.config_store: ConfigStore | None = None
        self.users: UserService | None = None
        # Pending Cline OAuth sessions for the automatic (redirect-based)
        # connect flow: state_token → {provider, created_at}. Consumed once
        # by the /cline/oauth/callback redirect, evicted after 10 minutes.
        self.cline_pending: dict[str, dict[str, Any]] = {}
        self.cline_refresh: Any = None
        self.workbuddy_refresh: Any = None
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
        self.auth = AuthService(aengine, self.config.general_settings.master_key,
                                self.config.general_settings.max_keys_per_user)
        await self.auth.startup()
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
        # Prune stale request logs so the DB doesn't grow without bound.
        retention = self.config.wiwi_settings.log_retention_days
        if retention > 0 and self._db_sink is not None:
            try:
                pruned = await self._db_sink.prune_old_requests(retention)
                if pruned:
                    import structlog as _sl
                    _sl.get_logger("wiwi.startup").info(
                        "pruned_old_request_logs", rows=pruned, retention_days=retention)
            except Exception as e:  # noqa: BLE001 — pruning is best-effort
                import structlog as _sl
                _sl.get_logger("wiwi.startup").warning(
                    "prune_failed", error=str(e))
        # Persist admin-added providers/keys/deployments so they survive restart
        self.config_store = ConfigStore(aengine)
        await self.config_store.startup()
        await self._load_db_config()
        self.gateways = {
            "chat": Gateway(self.router, self.cost,
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
                round_robin=p.get("round_robin", True),
                alias_id=p.get("alias_id"))
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
                    coerced = _coerce_alias_value(_v)
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
    # WorkBuddy per-key token refresh: same on-demand contract as Cline
    # (hook(provider, label) -> bool) but keyed per pool key, since each
    # WorkBuddy key holds its own account auth JSON.
    from wiwi.providers.workbuddy_auto_refresh import WorkBuddyAutoRefresh
    from wiwi.providers.workbuddy_auto_refresh import (
        refresh_for_provider as workbuddy_refresh_for_provider,
    )
    state.workbuddy_refresh = WorkBuddyAutoRefresh(state)
    state.workbuddy_refresh.start()
    workbuddy_refresh_hook = workbuddy_refresh_for_provider(state)
    for gw in state.gateways.values():
        gw._on_demand_refresh_hooks["workbuddy"] = workbuddy_refresh_hook  # type: ignore[attr-defined]
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
    if state.workbuddy_refresh is not None:
        await state.workbuddy_refresh.stop()
    await state.cline_refresh.stop()
    await state.shutdown()


def _inject_id(chunk: bytes, event_id: int) -> bytes:
    """Prepend an SSE ``id:`` line to each SSE frame in the chunk.

    A chunk may contain multiple frames (joined by blank-line boundaries).
    Per the SSE spec, an ``id`` line sets the last-event-id for the NEXT
    event dispatched.  If we only tag the first frame, the client's
    Last-Event-ID points at the first sub-event, not the last — causing
    replay-from-wrong-offset on reconnect.  Tag every frame instead.
    """
    id_line = f"id: {event_id}\n".encode()
    frames = chunk.split(b"\n\n")
    tagged = [id_line + f for f in frames if f]
    return b"\n\n".join(tagged)


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
            allowed, retry_after = await state.limiter.check(info.key_id, info.rpm,
                                                             info.tpm,
                                                             est_tokens=est_tokens)
            if not allowed:
                resp = _err(429, "rate_limit_error",
                            f"rate limit exceeded, retry in {retry_after}s",
                            request, surface)
                resp.headers["Retry-After"] = str(retry_after)
                return None, resp
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

    async def json_body(request: Request) -> tuple[Any, ORJSONResponse | None]:
        """Parse the request body; malformed JSON is a client error (400)."""
        # Errors raised here happen before the surface is known to the handler,
        # so infer it from the path: the Anthropic dialect expects
        # {"type":"error",...} while OpenAI expects {"error":{...}}.
        surface = "messages" if request.url.path.endswith("/messages") else "chat"
        limit = config.wiwi_settings.max_request_body_mb * 1024 * 1024
        try:
            raw = await request.body()
        except Exception:  # noqa: BLE001
            raw = b""
        # Enforce the cap on what actually arrived. Content-Length is checked
        # in middleware, but chunked bodies declare no length — and the
        # middleware can only flag an overage, not guarantee the handler sees
        # the flag. Measuring here closes that gap either way.
        if len(raw) > limit or getattr(request.state, "body_too_large", False):
            return None, _err(413, "invalid_request_error",
                              f"request body exceeds "
                              f"{config.wiwi_settings.max_request_body_mb} MiB",
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
        import uuid as _uuid
        request_id = _uuid.uuid4().hex[:16]
        rl_err = await enforce_rate_limit(info, est, request, surface, request_id)
        if rl_err:
            return rl_err
        ctx = RequestContext(surface=surface, ir_req=ir_req, auth=info, group=group,
                             request_id=request_id)
        gateway = state_.gateways["chat"]
        if config.wiwi_settings.store_prompts_in_spend_logs:
            ctx.metadata["request_body"] = body
        try:
            if ir_req.stream:
                encoder_pair = _encoder_for(surface, resp_model, ctx.request_id)
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
                except BaseException:
                    # Non-WiwiError failure: release the pump's upstream
                    # connection before letting the outer handler deal with it.
                    await stream.aclose()
                    raise
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
            payload = codec_encode_response(ctx, turn, resp_model, ctx.request_id)
            if config.wiwi_settings.store_prompts_in_spend_logs:
                ctx.metadata["response_body"] = _serialize_turn(turn, payload)
            state_.logs.log_request(build_log_event(ctx))
            await _record_tpm_usage(info, ctx)
            if info and info.key_type != "master":
                # A False return means the conditional UPDATE was rejected —
                # the request would breach max_budget (or the key vanished).
                # Recording it anyway would let a caller exceed a hard cap by
                # sending one large request. Raising a 402 here is the point of
                # a hard budget; only genuine accounting *errors* are suppressed
                # so they don't mask an otherwise-successful response.
                try:
                    recorded = await state_.auth.update_spend(info.key_id, ctx.cost)
                except Exception:  # noqa: BLE001
                    recorded = True
                if not recorded:
                    ctx.status = 402
                    state_.logs.log_request(build_log_event(ctx))
                    return _err(402, "budget_exceeded",
                                "virtual key budget exhausted", request, surface)
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
            await _record_tpm_usage(ctx.auth, ctx)
            if ctx.usage and ctx.auth and ctx.auth.key_type != "master":
                # The response has already been streamed, so a budget breach
                # can't turn into a 402 here. Record it on the context (and
                # the log) so the *next* request is refused, rather than
                # silently allowing spend to run past the cap forever.
                try:
                    recorded = await state_.auth.update_spend(
                        ctx.auth.key_id, ctx.cost)
                except Exception:  # noqa: BLE001
                    recorded = True
                if not recorded:
                    ctx.status = 402
                    ctx.metadata["budget_exceeded"] = True

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
        _, err_resp = await authenticate(request, ir_req.model, "messages",
                                          reserve=False)
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
        _, err_resp = await authenticate(request, model="*", reserve=False)
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
        async def prometheus_metrics(request: Request):
            if not is_admin(request):
                return _err(401, "authentication_error", "master key required",
                            request, "chat")
            events = [e for _, e in await state.logs.sse.replay("request", 0)]
            text = render_metrics(events)
            return PlainTextResponse(text, media_type="text/plain; version=0.0.4")

    # -- admin -------------------------------------------------------------------
    @app.post("/admin/keys/generate")
    async def admin_generate_key(request: Request):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        models = body.get("models")
        if models is not None and (not isinstance(models, list)
                                   or not all(isinstance(m, str) for m in models)):
            return _err(400, "invalid_request_error",
                        "'models' must be a list of strings", request)
        owner_id = None if actor.role == "admin" else actor.id
        try:
            plaintext, kid = await state.auth.create_key(
                alias=str(body.get("name") or body.get("alias") or ""),
                models=body.get("models"), max_budget=body.get("max_budget"),
                rpm=body.get("rpm"), tpm=body.get("tpm"),
                ttl_seconds=body.get("ttl_seconds"),
                custom_key=body.get("custom_key"), owner_id=owner_id)
        except ValueError as e:
            return _err(400, "invalid_request_error", str(e), request)
        await state.logs.log_audit(
            actor=actor.username, action="key.generate", target=kid,
            diff={"source": "custom"} if body.get("custom_key") else None)
        return ORJSONResponse({"key": plaintext, "id": kid,
                             "note": "store this key now; it is not shown again"})

    @app.get("/admin/keys")
    async def admin_list_keys(request: Request):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
        if actor.role == "admin":
            keys = await state.auth.list_keys()
        else:
            keys = await state.auth.list_keys_for_owner(actor.id)
        return ORJSONResponse({"keys": keys})

    @app.delete("/admin/keys/{key_id}")
    async def admin_delete_key(key_id: str, request: Request):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
        if actor.role != "admin":
            owner = await state.auth.key_owner(key_id)
            if owner != actor.id:
                return _err(403, "permission_error", "not your key", request)
        ok = await state.auth.delete_key(key_id)
        await state.logs.log_audit(actor=actor.username, action="key.delete",
                                   target=key_id)
        return ORJSONResponse({"deleted": ok})

    @app.post("/admin/keys/{key_id}/disable")
    async def admin_disable_key(key_id: str, request: Request):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
        if actor.role != "admin":
            owner = await state.auth.key_owner(key_id)
            if owner != actor.id:
                return _err(403, "permission_error", "not your key", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        disabled = bool(body.get("disabled", True))
        await state.auth.set_disabled(key_id, disabled)
        await state.logs.log_audit(actor=actor.username,
                                   action="key.disable" if disabled else "key.enable",
                                   target=key_id)
        return ORJSONResponse({"key_id": key_id, "disabled": disabled})

    @app.get("/admin/logs/requests")
    async def admin_request_logs(request: Request, limit: int = 10000):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
        # Hard ceiling (50k) is a safety net against runaway callers, not a
        # product limit — the Usage page trusts the DB-backed overview for
        # the headline number and only uses this endpoint for the row table.
        limit = max(1, min(limit, 50000))
        kids: list[str] | None = None
        if actor.role != "admin":
            kids = [k["id"] for k in await state.auth.list_keys_for_owner(actor.id)]
        sink = state.logs.db_sink
        if sink is not None:
            return ORJSONResponse(
                {"logs": await sink.read_requests(limit, key_ids=kids)},
                headers={"Cache-Control": "no-store"},
            )
        # Ring fallback: deque is oldest→newest, so slice the newest N then
        # reverse to newest-first — matching the DB path contract.
        ring = list(await state.logs.sse.replay("request", 0))
        evs = [e for _, e in ring]
        if kids is not None:
            evs = [e for e in evs if e.key_id in kids]
        return ORJSONResponse(
            {"logs": [public_dict(e) for e in reversed(evs[-limit:])]},
            headers={"Cache-Control": "no-store"},
        )

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

    # -- session / user resolution ----------------------------------------------
    async def current_user(request: Request) -> UserInfo | None:
        """Resolve the caller from a signed session cookie OR a bearer master
        key (back-compat). Returns ``None`` when anonymous or when the session
        user has been disabled/deleted."""
        # master key via bearer → synthetic admin (back-compat for /auth/me)
        mk = config.general_settings.master_key
        tok = bearer(request)
        if mk and tok and hmac.compare_digest(tok.encode(), mk.encode()):
            return UserInfo(id="master", username="master", role="admin")
        # signed session cookie
        cookie = request.cookies.get("wiwi_session")
        if not cookie:
            return None
        parsed = verify_session(state.users._secret, cookie)  # type: ignore[union-attr]
        if parsed is None:
            return None
        uid, _role, _exp = parsed
        if uid == "master":
            # Only honour a synthetic master session when a master key is
            # actually configured. Otherwise any cookie signed with whatever
            # secret happened to be in use would mint an admin with no DB row.
            if not config.general_settings.master_key:
                return None
            return UserInfo(id="master", username="master", role="admin")
        if state.users is None:
            return None
        info = await state.users.get(uid)
        if info is None or info.disabled:
            return None
        return info

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

    def _key_view(k, now_mono: float, now_wall: float) -> dict:
        cooling = k.status == "cooling" and now_mono < k.cooldown_until
        status = "disabled" if not k.enabled else ("cooling" if cooling else k.status)
        return {
            "label": k.label,
            "masked": mask_key(k.secret),
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
        return ORJSONResponse(out)

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
                "alias_id": acct.alias_id,
                "healthy": acct.healthy,
                "keys": [_key_view(k, mono, wall) for k in acct.keys],
            })
        return ORJSONResponse({"providers": out,
                               "alias_to_provider": dict(state.router.alias_to_provider)})

    @app.get("/admin/providers/{name}/keys/{label}/secret")
    async def admin_reveal_provider_key(name: str, label: str, request: Request):
        """Return the plaintext provider key secret (admin-only).

        The pool listing only carries a masked form; the UI fetches the
        plaintext on demand when an admin explicitly reveals a key. Reveals
        are audit-logged like mutations since they expose a credential.
        """
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
        await state.logs.log_audit(actor="master", action="provider_key.reveal",
                                   target=f"{name}/{label}", diff={})
        return ORJSONResponse({"label": label, "secret": key.secret})

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
        raw_url = _interpolate(body.get("base_url"))
        if raw_url is not None and not isinstance(raw_url, str):
            # str() would stringify a dict/list into a truthy URL like
            # "{'nested': True}" and silently persist it as the upstream base.
            return _err(400, "invalid_request_error",
                        "'base_url' must be a string", request)
        base_url = (raw_url or "").strip() or _default_base_url(ptype)
        label = str(body.get("label") or "default")
        secret = str(_interpolate(body.get("key")) or "")
        alias_raw = body.get("alias_id")
        alias_id = (str(alias_raw).strip() if alias_raw is not None else None) or None
        if alias_id is not None and any(c.isspace() for c in alias_id):
            return _err(400, "invalid_request_error",
                        "alias_id must not contain whitespace", request)
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
        if alias_id is not None and alias_id in state.router.alias_to_provider:
            return _err(409, "invalid_request_error",
                        f"alias_id '{alias_id}' already used by provider"
                        f" '{state.router.alias_to_provider[alias_id]}'",
                        request)
        state.router.providers[name] = ProviderAccount(
            name=name, provider_type=ptype, base_url=base_url,
            keys=[ProviderKey(label=label, secret=secret)],
            alias_id=alias_id)
        if alias_id is not None:
            state.router.alias_to_provider[alias_id] = name
        if state.config_store:
            await state.config_store.add_provider(name, ptype, base_url,
                                                  alias_id=alias_id)
            await state.config_store.add_key(name, label, secret)
        # If a Cline provider was just added and global default models are
        # persisted, auto-deploy them for the new account so it joins the
        # cross-account WRR pool immediately (no manual setup required).
        if ptype == "cline":
            defaults = await state.config_store.get_setting(
                "cline_settings:default_models") if state.config_store else None
            if defaults:
                await _apply_cline_default_models(state, defaults)
        await state.logs.log_audit(actor="master", action="provider.create",
                                   target=name,
                                   diff={"provider_type": ptype, "base_url": base_url,
                                         "alias_id": alias_id})
        return ORJSONResponse({"name": name, "provider_type": ptype,
                               "base_url": base_url, "alias_id": alias_id})

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
        # drop any alias_id entry that points at this provider
        for k, v in list(state.router.alias_to_provider.items()):
            if v == name:
                del state.router.alias_to_provider[k]
        del state.router.providers[name]
        if state.config_store:
            await state.config_store.delete_provider(name)
            await state.config_store.delete_setting(_cline_oauth_setting_key(name))
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
            raw_url = _interpolate(body["base_url"])
            if not isinstance(raw_url, str):
                return _err(400, "invalid_request_error",
                            "base_url must be a string", request)
            base_url = raw_url.strip()
            if not base_url:
                return _err(400, "invalid_request_error",
                            "base_url must be non-empty", request)
            acct.base_url = base_url
            diff["base_url"] = base_url
        if "round_robin" in body:
            acct.round_robin = bool(body["round_robin"])
            diff["round_robin"] = acct.round_robin
        alias_change: tuple[str | None, bool] | None = None
        if "alias_id" in body:
            alias_raw = body["alias_id"]
            if alias_raw is None or (isinstance(alias_raw, str) and not alias_raw.strip()):
                new_alias: str | None = None
            else:
                new_alias = str(alias_raw).strip()
                if any(c.isspace() for c in new_alias):
                    return _err(400, "invalid_request_error",
                                "alias_id must not contain whitespace", request)
            prior = state.router.alias_to_provider.get(new_alias) if new_alias else None
            if new_alias is not None and prior is not None and prior != name:
                return _err(409, "invalid_request_error",
                            f"alias_id '{new_alias}' already used by provider"
                            f" '{prior}'", request)
            alias_change = (new_alias, True)
        # apply rename last so identity-based deployment refs stay valid
        if new_name is not None and new_name != name:
            acct.name = new_name
            state.router.providers[new_name] = acct
            del state.router.providers[name]
            target = f"{name}→{new_name}"
        else:
            target = name
        if alias_change is not None:
            new_alias, _ = alias_change
            # remove old mapping pointing at this provider (any alias_id)
            for k, v in list(state.router.alias_to_provider.items()):
                if v == acct.name:
                    del state.router.alias_to_provider[k]
            if new_alias is not None:
                state.router.alias_to_provider[new_alias] = acct.name
            acct.alias_id = new_alias
            diff["alias_id"] = new_alias
        if state.config_store:
            update_kwargs: dict[str, Any] = {
                "provider_type": diff.get("provider_type"),
                "base_url": diff.get("base_url"),
                "round_robin": diff.get("round_robin"),
                "new_name": new_name,
            }
            if alias_change is not None:
                update_kwargs["alias_id"] = alias_change[0]
                update_kwargs["alias_id_set"] = True
            await state.config_store.update_provider(name, **update_kwargs)
        await state.logs.log_audit(actor="master", action="provider.update",
                                   target=target, diff=diff)
        mono, wall = time.monotonic(), time.time()
        return ORJSONResponse({
            "name": acct.name,
            "provider_type": acct.provider_type,
            "base_url": acct.base_url,
            "round_robin": acct.round_robin,
            "alias_id": acct.alias_id,
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
        adapter = fresh_adapter(acct.provider_type)
        url = _provider_models_url(acct.provider_type, acct.base_url)
        headers = adapter.headers(ProviderKeyRef(label=key.label, secret=key.secret))
        # Gemini puts the API key in the querystring, not headers — so `url`
        # now holds a credential and must never be echoed back in an error.
        if acct.provider_type == "gemini":
            url += f"?key={key.secret}"
        safe_url = _redact_url_secret(url)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as hc:
                r = await hc.get(url, headers=headers)
        except httpx.HTTPError:
            return _err(502, "api_connection_error",
                        f"could not reach '{name}' ({safe_url})", request)
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

    @app.get("/admin/cline/models")
    async def admin_cline_models(request: Request):
        """Fetch model ids from any available Cline provider (global fetch).

        All Cline OAuth accounts share the same upstream model catalog
        (api.cline.bot/api/v1/models).  Instead of fetching per-account,
        this endpoint queries the first Cline provider with an available
        key, caches the result for 5 minutes, and returns it for reuse
        across every Cline account.
        """
        resp = _require_admin(request)
        if resp:
            return resp
        # ?refresh=true forces a re-fetch (bypasses the 5-minute in-memory cache)
        want_refresh = (request.query_params.get("refresh", "").lower()
                        in ("1", "true", "yes"))
        # Check in-memory cache (5-minute TTL) — skipped when refresh requested
        cache = getattr(state, "_cline_models_cache", None)
        import time as _time
        if not want_refresh and cache and _time.time() - cache["ts"] < 300:
            return ORJSONResponse({"models": cache["models"]})
        # Find the first Cline provider with an available key
        cline_acct = None
        cline_key = None
        for acct in state.router.providers.values():
            if acct.provider_type != "cline":
                continue
            key = next((k for k in acct.keys if k.available), None)
            if key is not None:
                cline_acct = acct
                cline_key = key
                break
        if cline_acct is None or cline_key is None:
            return _err(409, "invalid_request_error",
                        "no available Cline key — connect a Cline account first",
                        request)
        adapter = fresh_adapter("cline")
        url = _provider_models_url("cline", cline_acct.base_url)
        headers = adapter.headers(ProviderKeyRef(label=cline_key.label,
                                                   secret=cline_key.secret))
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as hc:
                r = await hc.get(url, headers=headers)
        except httpx.HTTPError:
            return _err(502, "api_connection_error",
                        f"could not reach Cline ({url})", request)
        if r.status_code != 200:
            etype = ("rate_limit_error" if r.status_code == 429
                     else "authentication_error" if r.status_code in (401, 403)
                     else "api_error")
            return _err(r.status_code if r.status_code < 500 else 502, etype,
                        f"Cline returned HTTP {r.status_code}: {r.text[:300]}",
                        request)
        models = [{"id": mid} for mid in
                  sorted(m["id"] for m in
                         _parse_models_response("cline", r.content))]
        # Cache for 5 minutes
        state._cline_models_cache = {"models": models, "ts": _time.time()}
        return ORJSONResponse({"models": models})

    # -- Cline global default-model settings ------------------------------
    # Persist a list of model ids that should be auto-deployed to every
    # Cline account (existing or future).  Each model becomes a router
    # group named ``cline:<model_id>`` with one Deployment per Cline
    # account, so requests smooth-WRR across accounts via the existing
    # ``_CrossProviderWRR`` cursor.  See tests/test_cline_global_model.py.

    @app.get("/admin/cline/settings")
    async def admin_get_cline_settings(request: Request):
        """Read the persisted Cline default-model list."""
        resp = _require_admin(request)
        if resp:
            return resp
        cs = state.config_store
        if cs is None:
            return ORJSONResponse({"default_models": []})
        ids = await cs.get_setting("cline_settings:default_models") or []
        if not isinstance(ids, list):
            ids = []
        return ORJSONResponse({"default_models": [str(x) for x in ids]})

    @app.put("/admin/cline/settings")
    async def admin_put_cline_settings(request: Request):
        """Replace the persisted Cline default-model list and reconcile.

        Body: ``{"default_models": ["z-ai/glm-5.2", "claude-sonnet-5"]}``.
        Each id is validated (non-empty, max 32 entries) and a Deployment
        is created for every Cline account in router.providers under the
        group ``cline:<model_id>``.  Idempotent — re-PUTting the same
        list is a no-op for existing deployments.
        """
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        raw = body.get("default_models")
        if not isinstance(raw, list):
            return _err(400, "invalid_request_error",
                        "default_models must be a list", request)
        cleaned: list[str] = []
        seen: set[str] = set()
        for x in raw:
            if not isinstance(x, str):
                return _err(400, "invalid_request_error",
                            "default_models entries must be strings", request)
            mid = x.strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            cleaned.append(mid)
        if len(cleaned) > 32:
            return _err(400, "invalid_request_error",
                        "default_models is capped at 32 entries", request)
        if state.config_store is not None:
            await state.config_store.set_setting("cline_settings:default_models",
                                                 cleaned)
        result = await _apply_cline_default_models(state, cleaned)
        await state.logs.log_audit(actor="master", action="cline_settings.apply",
                                   target="default_models",
                                   diff={"applied": result["applied"],
                                         "skipped": result["skipped"],
                                         "models": cleaned})
        return ORJSONResponse({"default_models": cleaned,
                               "applied": result["applied"],
                               "skipped": result["skipped"]})

    @app.delete("/admin/cline/settings/default-models/{model_id:path}")
    async def admin_delete_cline_default_model(model_id: str, request: Request):
        """Remove one model id from the persisted list and drop every
        deployment under the ``cline:<model_id>`` group."""
        resp = _require_admin(request)
        if resp:
            return resp
        mid = (model_id or "").strip()
        if not mid:
            return _err(400, "invalid_request_error",
                        "model_id is required", request)
        if state.config_store is not None:
            current = await state.config_store.get_setting(
                "cline_settings:default_models") or []
            if not isinstance(current, list):
                current = []
            remaining = [x for x in current if str(x).strip() != mid]
            if remaining:
                await state.config_store.set_setting(
                    "cline_settings:default_models", remaining)
            else:
                await state.config_store.set_setting(
                    "cline_settings:default_models", [])
        group_name = f"cline:{mid}"
        removed = 0
        if group_name in state.router.groups:
            deps = state.router.groups.pop(group_name)
            removed = len(deps)
            if state.config_store is not None:
                for d in deps:
                    try:
                        await state.config_store.delete_deployment(
                            group_name, d.provider.name, d.model_id)
                    except Exception as e:  # noqa: BLE001 — best-effort delete
                        # Persisted row may not exist (e.g. running from
                        # config without DB); in-memory state is the
                        # source of truth for this request.
                        import structlog as _sl
                        _sl.get_logger("wiwi.cline_defaults").debug(
                            "cline_default_delete_missing",
                            group=group_name, provider=d.provider.name,
                            model=d.model_id, err=str(e),
                        )
            state.router.rebuild_cross_provider_pools()
        await state.logs.log_audit(actor="master",
                                   action="cline_settings.default_models.delete",
                                   target=mid,
                                   diff={"removed_deployments": removed})
        return ORJSONResponse({"deleted": mid, "removed_deployments": removed})

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
        # Model ids are typed by hand in the provider-detail UI now, so guard
        # the one mistake upstreams never forgive: embedded whitespace.
        if any(ch.isspace() for ch in model_id):
            return _err(400, "invalid_request_error",
                        "model_id must not contain whitespace", request)
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
        # adding to an existing group may promote it to a cross-provider pool
        state.router.rebuild_cross_provider_pools()
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

    @app.delete("/admin/model-groups/{name:path}/deployments")
    async def admin_delete_deployment(name: str, request: Request):
        """Detach one deployment from a model group.

        The target is identified by query params (``?provider=&model_id=``)
        rather than path segments: model ids routinely contain slashes
        (e.g. ``z-ai/glm-5.2``), which a greedy ``{name:path}`` prefix would
        otherwise swallow into the group name.
        """
        resp = _require_admin(request)
        if resp:
            return resp
        gname, deps = state.router.resolve_group(name)
        if gname is None or not deps:
            return _err(404, "not_found_error", f"unknown model group '{name}'",
                        request)
        pname = (request.query_params.get("provider") or "").strip()
        model_id = (request.query_params.get("model_id") or "").strip()
        if not pname or not model_id:
            return _err(400, "invalid_request_error",
                        "provider and model_id query params are required", request)
        match = next((d for d in deps
                      if d.provider.name == pname and d.model_id == model_id), None)
        if match is None:
            return _err(404, "not_found_error",
                        f"no deployment {pname}/{model_id} on group '{gname}'",
                        request)
        remaining = [d for d in deps if d is not match]
        if remaining:
            state.router.groups[gname] = remaining
        else:
            # Dropping the last deployment leaves an empty group that would
            # otherwise linger as a routable-but-failing target.
            state.router.groups.pop(gname, None)
        state.router.rebuild_cross_provider_pools()
        if state.config_store:
            await state.config_store.delete_deployment(gname, pname, model_id)
        await state.logs.log_audit(actor="master", action="deployment.delete",
                                   target=f"{gname}/{pname}/{model_id}",
                                   diff={"group_emptied": not remaining})
        return ORJSONResponse({"deleted": True, "group": gname,
                               "provider": pname, "model_id": model_id,
                               "group_emptied": not remaining})

    # -- admin: models & routing -------------------------------------------------
    @app.get("/admin/models")
    async def admin_models(request: Request):
        # Read for any authenticated actor (admin or user); guard via the
        # actor-based resolver so logged-in users can see the model list
        # without holding the master key.
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
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
                             "aliases": {k: _alias_value_to_json(v)
                                          for k, v in settings.model_group_alias.items()},
                             "provider_aliases": dict(state.router.alias_to_provider),
                             "strategy": settings.routing_strategy})

    @app.patch("/admin/model-groups/{name:path}")
    async def admin_patch_model_group(name: str, request: Request):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
        if actor.role != "admin":
            return _err(403, "permission_error", "admin only", request)
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

    # -- admin: model_group_alias CRUD -------------------------------------------
    # Batch endpoint (POST /admin/aliases) — body {"set": {...}, "unset": [...]}.
    # Batch avoids path-param URL encoding for alias keys (which may contain
    # slashes) and gives atomic validate-then-mutate semantics with one audit
    # event, mirroring the `weights` block in admin_patch_model_group above.
    @app.post("/admin/aliases")
    async def admin_update_aliases(request: Request):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
        if actor.role != "admin":
            return _err(403, "permission_error", "admin only", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        set_map = body.get("set") or {}
        unset = body.get("unset") or []
        new_live, err = _validate_alias_batch(
            dict(state.router.settings.model_group_alias),
            set_map, unset,
            state.router.groups,
            state.router.alias_to_provider,
        )
        if err:
            return _err(400, "invalid_request_error", err, request)
        # Mutate live state. resolve_group reads this dict per request, so no
        # router rebuild is needed for changes to take effect.
        state.router.settings.model_group_alias = new_live
        # Recompute overlay = diff(live vs alias_yaml_base); null = tombstone
        # removing a YAML-defined alias so the unset survives a restart.
        # Coerce each side to its JSON form so str vs ModelAliasEntry compare
        # cleanly (a plain YAML alias and a rich entry pointing at the same
        # target are not equal as Python objects).
        base = getattr(state, "alias_yaml_base", {})
        overlay: dict[str, Any] = {}
        for k, v in base.items():
            if k not in new_live:
                overlay[k] = None
            elif not _alias_value_eq(new_live[k], v):
                overlay[k] = _alias_value_to_json(new_live[k])
        for k, v in new_live.items():
            if k not in base:
                overlay[k] = _alias_value_to_json(v)
        if state.config_store is not None:
            await state.config_store.set_setting("model_group_alias", overlay)
        await state.logs.log_audit(actor="master", action="aliases.update",
                                   target="model_group_alias",
                                   diff={"set": dict(set_map),
                                         "unset": list(unset)})
        dumped = {k: _alias_value_to_json(v) for k, v in new_live.items()}
        return ORJSONResponse({"aliases": dumped,
                               "set": dict(set_map),
                               "unset": list(unset)})

    # -- admin: virtual keys PATCH -----------------------------------------------
    @app.patch("/admin/keys/{key_id}")
    async def admin_patch_key(key_id: str, request: Request):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
        if actor.role != "admin":
            owner = await state.auth.key_owner(key_id)
            if owner != actor.id:
                return _err(403, "permission_error", "not your key", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        fields = {k: body[k] for k in state.auth.UPDATABLE_FIELDS  # type: ignore[union-attr]
                  if k in body}
        try:
            updated = await state.auth.update_key(key_id, fields)  # type: ignore[union-attr]
        except ValueError as e:
            # Malformed limit/model values used to escape as an uncaught
            # ValueError/TypeError (HTTP 500); report them as a client error.
            return _err(400, "invalid_request_error", str(e), request)
        if updated is None:
            return _err(404, "not_found_error", f"unknown key '{key_id}'", request)
        await state.logs.log_audit(actor=actor.username, action="key.update",
                                   target=key_id, diff=fields)
        return ORJSONResponse({"key": updated})

    # -- admin: logs & stats -------------------------------------------------------
    @app.get("/admin/logs/proxy")
    async def admin_proxy_logs(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        # Ring is oldest→newest; slice newest 500 then reverse to newest-first
        # so the contract matches /admin/logs/requests.
        ring = list(await state.logs.sse.replay("proxy", 0))
        return ORJSONResponse(
            {"logs": [public_dict(e) for _, e in reversed(ring[-500:])]},
            headers={"Cache-Control": "no-store"},
        )

    async def _request_events() -> list[LogEvent]:
        return [e for _, e in await state.logs.sse.replay("request", 0)]

    @app.get("/admin/stats/overview")
    async def admin_stats_overview(request: Request, minutes: int = 60):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
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
        minutes_ring = minutes if minutes > 0 else 1440
        evs = await _request_events()
        if kids is not None:
            evs = [e for e in evs if e.key_id in kids]
        return ORJSONResponse(stats_mod.overview(evs, minutes_ring))

    @app.get("/admin/stats/timeseries")
    async def admin_stats_timeseries(request: Request, bucket: str = "minute",
                                     metric: str = "tokens", minutes: int = 60):
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
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
            minutes_ring = minutes if minutes > 0 else 1440
            evs = await _request_events()
            if kids is not None:
                evs = [e for e in evs if e.key_id in kids]
            return ORJSONResponse(
                stats_mod.timeseries(evs, bucket, metric, minutes_ring))
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
            if "cache_creation_input_cost_per_token" in p:
                entry["cache_creation_per_1m"] = round(
                    p["cache_creation_input_cost_per_token"] * 1_000_000, 6)
            if "max_input_tokens" in p:
                entry["max_input_tokens"] = p["max_input_tokens"]
            if "max_output_tokens" in p:
                entry["max_output_tokens"] = p["max_output_tokens"]
            if "mode" in p:
                entry["mode"] = p["mode"]
            out.append(entry)
        return ORJSONResponse({"models": out})

    @app.put("/admin/pricing/{model_id:path}")
    async def admin_put_pricing(model_id: str, request: Request):
        """Create or update a model's pricing (USD per 1M tokens in the body)."""
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        if not isinstance(body, dict):
            return _err(400, "invalid_request_error", "body must be a JSON object", request)
        ipt = body.get("input_per_1m")
        opt = body.get("output_per_1m")
        if not isinstance(ipt, (int, float)) or not isinstance(opt, (int, float)):
            return _err(400, "invalid_request_error",
                        "'input_per_1m' and 'output_per_1m' are required numbers", request)
        # Convert per-1M-token rates to per-token for the cost engine.
        entry: dict[str, Any] = {
            "input_cost_per_token": round(ipt / 1_000_000, 12),
            "output_cost_per_token": round(opt / 1_000_000, 12),
        }
        if isinstance(body.get("cache_read_per_1m"), (int, float)):
            entry["cache_read_input_cost_per_token"] = round(body["cache_read_per_1m"] / 1_000_000, 12)
        if isinstance(body.get("cache_creation_per_1m"), (int, float)):
            entry["cache_creation_input_cost_per_token"] = round(
                body["cache_creation_per_1m"] / 1_000_000, 12)
        if isinstance(body.get("max_input_tokens"), int):
            entry["max_input_tokens"] = body["max_input_tokens"]
        if isinstance(body.get("max_output_tokens"), int):
            entry["max_output_tokens"] = body["max_output_tokens"]
        if isinstance(body.get("mode"), str):
            entry["mode"] = body["mode"]
        state.cost.prices[model_id] = entry
        if state.config_store:
            await state.config_store.upsert_price(model_id, entry)
        await state.logs.log_audit(actor="master", action="pricing.update",
                                   target=model_id,
                                   diff={"input_per_1m": ipt, "output_per_1m": opt})
        # Echo back the normalized per-1M entry the GET endpoint returns.
        result: dict[str, Any] = {
            "model_id": model_id,
            "input_per_1m": round(entry["input_cost_per_token"] * 1_000_000, 6),
            "output_per_1m": round(entry["output_cost_per_token"] * 1_000_000, 6),
        }
        if "cache_read_input_cost_per_token" in entry:
            result["cache_read_per_1m"] = round(entry["cache_read_input_cost_per_token"] * 1_000_000, 6)
        if "cache_creation_input_cost_per_token" in entry:
            result["cache_creation_per_1m"] = round(
                entry["cache_creation_input_cost_per_token"] * 1_000_000, 6)
        if "max_input_tokens" in entry:
            result["max_input_tokens"] = entry["max_input_tokens"]
        if "max_output_tokens" in entry:
            result["max_output_tokens"] = entry["max_output_tokens"]
        if "mode" in entry:
            result["mode"] = entry["mode"]
        return ORJSONResponse(result)

    @app.delete("/admin/pricing/{model_id:path}")
    async def admin_delete_pricing(model_id: str, request: Request):
        """Remove a model's custom pricing entry."""
        resp = _require_admin(request)
        if resp:
            return resp
        existed = model_id in state.cost.prices
        if existed:
            del state.cost.prices[model_id]
        if state.config_store:
            await state.config_store.delete_price(model_id)
        await state.logs.log_audit(actor="master", action="pricing.delete",
                                   target=model_id)
        return ORJSONResponse({"deleted": existed, "model_id": model_id})

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

    # -- admin: users ----------------------------------------------------------
    @app.get("/admin/users")
    async def admin_list_users(request: Request):
        # Accept the master bearer key (existing admin auth) OR a master/admin
        # session cookie, so an admin who logged in via /auth/login can manage
        # users from the UI.
        resp = await require_admin_dep(request)
        if resp:
            return resp
        if state.users is None:
            return _err(500, "api_error", "user service not initialized", request)
        return ORJSONResponse({"users": await state.users.list_users()})

    @app.patch("/admin/users/{uid}")
    async def admin_patch_user(uid: str, request: Request):
        # Accept the master bearer key (existing admin auth) OR a master/admin
        # session cookie, so an admin who logged in via /auth/login can manage
        # users from the UI.
        resp = await require_admin_dep(request)
        if resp:
            return resp
        if state.users is None:
            return _err(500, "api_error", "user service not initialized", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        role = body.get("role")
        disabled = body.get("disabled")
        # Last-admin guard: if demoting or disabling an admin would leave zero
        # active admins, reject. The master-key admin (id="master") is synthetic
        # and not in the users table, so count_admins() only counts DB admins.
        if role == "user" or disabled is True:
            cur = await state.users.get(uid)
            if (cur is not None and cur.role == "admin" and not cur.disabled
                    and await state.users.count_admins() <= 1):
                return _err(400, "invalid_request_error",
                            "cannot demote or disable the last admin", request)
        try:
            updated = await state.users.patch(uid, role=role, disabled=disabled)
        except ValueError as e:
            return _err(400, "invalid_request_error", str(e), request)
        if updated is None:
            return _err(404, "not_found_error", "user not found", request)
        await state.logs.log_audit(actor="master", action="user.update", target=uid,
                                   diff={"role": role, "disabled": disabled})
        return ORJSONResponse(updated)

    # -- public auth surface (signup / login / logout / me) --------------------
    def _set_session_cookie(resp: ORJSONResponse, uid: str, role: str,
                            *, secure: bool) -> None:
        tok = sign_session(state.users._secret, uid, role,  # type: ignore[union-attr]
                           expires=time.time() + SESSION_TTL)
        resp.set_cookie("wiwi_session", tok, max_age=SESSION_TTL,
                        httponly=True, samesite="lax", secure=secure,
                        path="/")

    def _clear_session_cookie(resp: ORJSONResponse) -> None:
        resp.delete_cookie("wiwi_session", path="/")

    @app.post("/auth/signup")
    async def auth_signup(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        if state.users is None:
            return _err(500, "api_error", "user service not initialized", request)
        # Registration is unauthenticated, so it needs its own throttle:
        # otherwise one host can create unlimited accounts (each of which mints
        # a playground key) and exhaust the user table or disk.
        scope = _client_ip(request)
        allowed, retry_after = await state.signup_throttle.check(scope)
        if not allowed:
            resp = _err(429, "rate_limit_error",
                        f"too many signups from this address, retry in "
                        f"{retry_after}s", request)
            resp.headers["Retry-After"] = str(retry_after)
            return resp
        try:
            u = await state.users.create_user(
                body.get("username", ""), body.get("password", ""))
        except ValueError as e:
            # distinguish duplicate (409) from validation (400)
            if "already taken" in str(e):
                return _err(409, "conflict", str(e), request)
            return _err(400, "invalid_request_error", str(e), request)
        # Mint a fresh playground key when the new user is being logged in so
        # the Playground can use it immediately without a separate call.
        pg_key = ""
        anon = await current_user(request) is None
        if anon and state.auth is not None:
            with contextlib.suppress(Exception):
                pg_key = await _mint_playground_key(u)
        resp = ORJSONResponse(
            {"user": {"id": u.id, "username": u.username, "role": u.role},
             "playground_key": pg_key},
            status_code=201)
        # Only log in the new user when the caller is anonymous: an admin
        # creating a user via signup should keep their own session.
        if anon:
            _set_session_cookie(resp, u.id, u.role,
                                secure=request.url.scheme == "https")
        return resp

    async def _mint_playground_key(actor: UserInfo) -> str:
        """Mint a fresh virtual key for the playground, scoped to the actor.

        Admins get an un-owned key (owner_id=None) for back-compat; regular
        users get an owner-scoped key so it shows up in their key list and
        respects role-based filtering.
        """
        owner_id = None if actor.role == "admin" else actor.id
        # Bound the key: a TTL so abandoned keys expire, and a per-owner cap so
        # repeated logins (or repeated /auth/playground-key calls) cannot
        # accumulate unbounded live credentials. Without both, every login
        # minted another never-expiring, unlimited-budget key.
        #
        # The cap applies to admins too (owner_id=None → unowned keys). Those
        # were previously exempt twice over: `create_key`'s per-owner limit
        # skips owner_id=None, and this branch skipped them as well, so admin
        # playground keys grew without limit. React StrictMode double-invokes
        # the mint effect in dev, so the leak was 2 keys per mount.
        service = state.auth
        if service is not None:
            active = await service.count_keys(owner_id=owner_id, alias="playground")
            if active >= _MAX_PLAYGROUND_KEYS_PER_USER:
                await service.expire_keys(owner_id=owner_id, alias="playground",
                                          keep_newest=_MAX_PLAYGROUND_KEYS_PER_USER - 1)
        plaintext, _kid = await service.create_key(  # type: ignore[union-attr]
            alias="playground", owner_id=owner_id,
            ttl_seconds=_PLAYGROUND_KEY_TTL_S)
        return plaintext

    @app.post("/auth/login")
    async def auth_login(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        if state.users is None:
            return _err(500, "api_error", "user service not initialized", request)
        # Throttle by client IP *and* attempted username, so neither a single
        # account nor one host can be brute-forced without limit.
        scope = f"{_client_ip(request)}:{body.get('username', '')}"
        allowed, retry_after = await state.login_throttle.check(scope)
        if not allowed:
            resp = _err(429, "rate_limit_error",
                        f"too many failed login attempts, retry in {retry_after}s",
                        request)
            resp.headers["Retry-After"] = str(retry_after)
            return resp
        # master-key login → synthetic master admin
        mk = body.get("master_key")
        if mk:
            if hmac.compare_digest(str(mk).encode(),
                                   (config.general_settings.master_key or "").encode()):
                # Mint a fresh playground key alongside the session cookie so
                # the Playground can use it immediately without a second call.
                pg_key = ""
                if state.auth is not None:
                    with contextlib.suppress(Exception):
                        pg_key = await _mint_playground_key(
                            UserInfo(id="master", username="master", role="admin"))
                resp = ORJSONResponse(
                    {"user": {"id": "master", "username": "master", "role": "admin"},
                     "playground_key": pg_key})
                _set_session_cookie(resp, "master", "admin",
                                    secure=request.url.scheme == "https")
                return resp
            await state.login_throttle.record_failure(scope)
            return _err(401, "authentication_error", "invalid master key", request)
        # username/password login
        try:
            u = await state.users.verify(body.get("username", ""), body.get("password", ""))
        except ValueError:
            # malformed username charset/length — treat as invalid credentials
            await state.login_throttle.record_failure(scope)
            return _err(401, "authentication_error", "invalid credentials", request)
        if u is None or u.disabled:
            await state.login_throttle.record_failure(scope)
            return _err(401, "authentication_error", "invalid credentials", request)
        # Successful login clears any accumulated failures for this scope.
        await state.login_throttle.reset(scope)
        # Mint a fresh playground key alongside the session cookie so the
        # Playground can use it immediately without a second call.
        pg_key = ""
        if state.auth is not None:
            with contextlib.suppress(Exception):
                pg_key = await _mint_playground_key(u)
        resp = ORJSONResponse(
            {"user": {"id": u.id, "username": u.username, "role": u.role},
             "playground_key": pg_key})
        _set_session_cookie(resp, u.id, u.role,
                            secure=request.url.scheme == "https")
        return resp

    @app.post("/auth/logout")
    async def auth_logout(request: Request):
        resp = ORJSONResponse({"ok": True})
        _clear_session_cookie(resp)
        return resp

    @app.get("/auth/me")
    async def auth_me(request: Request):
        u = await current_user(request)
        if u is None:
            return ORJSONResponse({"user": None})
        return ORJSONResponse(
            {"user": {"id": u.id, "username": u.username, "role": u.role}})

    @app.post("/auth/playground-key")
    async def auth_playground_key(request: Request):
        """Mint a fresh playground key for the current session.

        Used by the Playground when no key is cached in sessionStorage (new
        tab, first visit, or the cached key was evicted). Requires an
        authenticated session — anonymous callers get 401.
        """
        actor = await current_user(request)
        if actor is None:
            return _err(401, "authentication_error", "authentication required", request)
        if state.auth is None:
            return _err(500, "api_error", "gateway not initialized", request)
        plaintext = await _mint_playground_key(actor)
        return ORJSONResponse({"key": plaintext})

    # -- public: secret-free model catalog (no auth) --------------------------
    # Powers the public Models catalog page. Strips all health/inflight/
    # cooldown/weight fields — only the model group's name and each
    # deployment's provider + model_id are exposed. Never errors on auth.
    # -- Cline OAuth (paste-code flow; tokens live in the provider key pool) -----

    def _cline_oauth_setting_key(provider: str) -> str:
        return f"cline_oauth:{provider}"

    async def _update_provider_secret(provider: str, secret: str) -> bool:
        """Replace the secret of the provider's first key, in memory and DB.

        Returns False when the provider has no keys (caller should reject the
        connect/refresh rather than store tokens with no key to land them on).
        """
        acct = state.router.providers.get(provider)
        if acct is None or not acct.keys:
            return False
        key0 = acct.keys[0]
        key0.secret = secret
        # Reset runtime cooldown state — the credential just changed.
        key0.status = "active"
        key0.cooldown_until = 0.0
        if state.config_store:
            await state.config_store.update_key_secret(provider, key0.label, secret)
        return True

    @app.post("/admin/cline/oauth/login-url")
    async def cline_oauth_login_url(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        callback = str(body.get("callback_url") or "").strip()
        if not callback:
            return _err(400, "invalid_request_error",
                        "callback_url is required", request)
        return ORJSONResponse({"auth_url": cline_oauth.build_auth_url(callback)})

    @app.post("/admin/cline/oauth/connect")
    async def cline_oauth_connect(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "").strip()
        code = str(body.get("code") or "").strip()
        if not provider or not code:
            return _err(400, "invalid_request_error",
                        "provider and code are required", request)
        if provider not in state.router.providers:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        try:
            tokens = cline_oauth.exchange_code(code)
        except ValueError as e:
            return _err(400, "invalid_request_error",
                        f"invalid Cline code: {e}", request)
        if not await _update_provider_secret(provider, tokens["access_token"]):
            return _err(400, "invalid_request_error",
                        f"provider '{provider}' has no keys — add a pool key "
                        f"first, then connect", request)
        record = {
            "refresh_token": tokens["refresh_token"],
            "expires_at": tokens.get("expires_at"),
            "email": tokens.get("email"),
        }
        if state.config_store:
            await state.config_store.set_setting(
                _cline_oauth_setting_key(provider), record)
        await state.logs.log_audit(
            actor="master", action="cline_oauth.connect", target=provider,
            diff={"email": tokens.get("email")})
        return ORJSONResponse({
            "provider": provider,
            "email": tokens.get("email"),
            "access_token_masked": mask_key(tokens["access_token"]),
        })

    @app.post("/admin/cline/oauth/auto-connect")
    async def cline_oauth_auto_connect(request: Request):
        """Initiate an automatic (redirect-based) Cline OAuth connect.

        Unlike the paste-code flow, this returns a Cline auth URL whose
        callback points back to wiwi's own ``/cline/oauth/callback``. After
        the admin logs in at Cline, Cline redirects the browser to that
        callback with ``?code=...``; wiwi decodes the embedded tokens,
        persists them, and redirects the browser to the SPA. No manual
        copy-paste required.
        """
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "").strip()
        return_path = str(body.get("return_path") or "/console/oauth").strip()
        if not provider:
            return _err(400, "invalid_request_error",
                        "provider is required", request)
        if provider not in state.router.providers:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        # Guard against open redirect: only allow relative /console paths.
        if not return_path.startswith("/console/") and return_path != "/console":
            return_path = "/console/oauth"
        # Evict expired pending sessions (TTL 10 min).
        now = time.time()
        for tok, sess in list(state.cline_pending.items()):
            if now - sess["created_at"] > 600:
                state.cline_pending.pop(tok, None)
        state_token = secrets.token_urlsafe(24)
        state.cline_pending[state_token] = {
            "provider": provider,
            "return_path": return_path,
            "created_at": now,
        }
        # The callback_url Cline redirects to after login. It carries the
        # state token so we can match the pending session on return.
        cb = f"{_request_base(request)}/cline/oauth/callback?state={state_token}"
        return ORJSONResponse({
            "auth_url": cline_oauth.build_auth_url(cb),
            "state": state_token,
            "provider": provider,
        })

    @app.get("/cline/oauth/callback")
    async def cline_oauth_callback(request: Request):
        """Cline redirects the browser here with ?code=...&state=... after a
        successful login. Decodes the embedded tokens (offline), persists them
        to the provider, then redirects the browser to the SPA with a
        success/error flag so the frontend can show a result banner.

        This endpoint is NOT admin-gated — the admin auth happened at
        auto-connect time when the pending session was created. The state
        token is a single-use secret that authorizes this one callback.
        """
        code = request.query_params.get("code", "").strip()
        state_token = request.query_params.get("state", "").strip()
        if not code or not state_token:
            return _cline_callback_error(request, "missing code or state")
        sess = state.cline_pending.pop(state_token, None)
        if sess is None:
            return _cline_callback_error(request, "invalid or expired session")
        provider = sess["provider"]
        return_path = sess["return_path"]
        if provider not in state.router.providers:
            return _cline_callback_redirect(return_path, provider,
                                             error="provider no longer exists")
        try:
            tokens = cline_oauth.exchange_code(code)
        except ValueError as e:
            return _cline_callback_redirect(return_path, provider,
                                             error=f"invalid code: {e}")
        if not await _update_provider_secret(provider, tokens["access_token"]):
            return _cline_callback_redirect(return_path, provider,
                                             error="provider has no pool keys")
        record = {
            "refresh_token": tokens["refresh_token"],
            "expires_at": tokens.get("expires_at"),
            "email": tokens.get("email"),
        }
        if state.config_store:
            await state.config_store.set_setting(
                _cline_oauth_setting_key(provider), record)
        await state.logs.log_audit(
            actor="master", action="cline_oauth.connect", target=provider,
            diff={"email": tokens.get("email"), "flow": "auto"})
        return _cline_callback_redirect(return_path, provider,
                                         email=tokens.get("email"))

    def _request_base(request: Request) -> str:
        """Absolute base URL (scheme + host) for building callback URLs.

        ``wiwi_settings.public_url`` wins when configured — it is trusted
        operator config. Otherwise the request's own ``Host`` header is used.
        ``X-Forwarded-Host`` is deliberately NOT honoured: it is client
        controlled, and trusting it let an attacker point an OAuth callback
        (and thus the authorization code) at their own origin.
        """
        cfg_url = (config.wiwi_settings.public_url or "").strip().rstrip("/")
        if cfg_url:
            return cfg_url
        host = request.headers.get("host") or "localhost"
        return f"{request.url.scheme or 'https'}://{host}"

    def _cline_callback_redirect(return_path: str, provider: str,
                                  email: str | None = None,
                                  error: str | None = None) -> RedirectResponse:
        from urllib.parse import urlencode
        params: dict[str, str] = {"cline_provider": provider}
        if email:
            params["cline_connected"] = "1"
            params["cline_email"] = email
        if error:
            params["cline_error"] = error
        sep = "&" if "?" in return_path else "?"
        return RedirectResponse(f"{return_path}{sep}{urlencode(params)}",
                                status_code=302)

    def _cline_callback_error(request: Request, message: str) -> ORJSONResponse:
        return _err(400, "invalid_request_error", message, request)

    @app.get("/admin/cline/oauth/status")
    async def cline_oauth_status(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        provider = request.query_params.get("provider", "").strip()
        if not provider or provider not in state.router.providers:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        record = None
        if state.config_store:
            record = await state.config_store.get_setting(
                _cline_oauth_setting_key(provider))
        if not record:
            return ORJSONResponse({"connected": False})
        expires_epoch = cline_oauth.parse_expires_at(record.get("expires_at"))
        return ORJSONResponse({
            "connected": True,
            "email": record.get("email"),
            "expires_at": record.get("expires_at"),
            "needs_refresh": bool(
                expires_epoch is not None
                and cline_oauth.expires_within_lead(expires_epoch)),
        })

    @app.post("/admin/cline/oauth/refresh")
    async def cline_oauth_refresh(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "").strip()
        if provider not in state.router.providers:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        record = None
        if state.config_store:
            record = await state.config_store.get_setting(
                _cline_oauth_setting_key(provider))
        if not record or not record.get("refresh_token"):
            return _err(400, "invalid_request_error",
                        f"provider '{provider}' has no stored Cline tokens; "
                        "connect first", request)
        result = await cline_oauth.refresh_token(record["refresh_token"])
        if result is None:
            return _err(502, "upstream_error",
                        "Cline token refresh failed (transient); retry later",
                        request)
        if result.get("error") == "unrecoverable_refresh_error":
            return _err(401, "authentication_error",
                        f"Cline refresh token rejected — re-login required "
                        f"(code: {result.get('code')})", request)
        if not await _update_provider_secret(provider, result["access_token"]):
            return _err(400, "invalid_request_error",
                        f"provider '{provider}' has no keys — cannot write "
                        f"refreshed token", request)
        record["refresh_token"] = result["refresh_token"]
        if result.get("expires_at"):
            record["expires_at"] = result["expires_at"]
        if state.config_store:
            await state.config_store.set_setting(
                _cline_oauth_setting_key(provider), record)
        await state.logs.log_audit(
            actor="master", action="cline_oauth.refresh", target=provider)
        return ORJSONResponse({
            "provider": provider,
            "access_token_masked": mask_key(result["access_token"]),
            "expires_at": result.get("expires_at"),
        })

    @app.delete("/admin/cline/oauth/disconnect")
    async def cline_oauth_disconnect(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "").strip()
        if provider not in state.router.providers:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        if state.config_store:
            await state.config_store.delete_setting(
                _cline_oauth_setting_key(provider))
        await state.logs.log_audit(
            actor="master", action="cline_oauth.disconnect", target=provider)
        return ORJSONResponse({"provider": provider, "disconnected": True})

    # -- WorkBuddy accounts ---------------------------------------------------
    # WorkBuddy pool keys each hold a full account auth JSON (see
    # wiwi.providers.workbuddy_auth), so the admin surface here is about
    # account *files*: list connected accounts with expiry state, bulk-import
    # the auth JSONs produced by the upstream plugin / CPA panel (the
    # workbuddy2api ``auths/`` format), and export them back out in the same
    # shape for backup or re-import elsewhere.

    def _workbuddy_accounts() -> list[dict[str, Any]]:
        accounts: list[dict[str, Any]] = []
        for name, acct in sorted(state.router.providers.items()):
            if acct.provider_type != "workbuddy":
                continue
            for k in acct.keys:
                entry: dict[str, Any] = {
                    "provider": name, "label": k.label, "status": k.status,
                    "enabled": k.enabled, "valid_auth": False,
                }
                try:
                    a = workbuddy_auth.parse_auth(k.secret)
                except workbuddy_auth.WorkBuddyAuthError:
                    accounts.append(entry)
                    continue
                entry.update({
                    "valid_auth": True,
                    "uid": a.uid,
                    "nickname": a.nickname,
                    "domain": a.domain,
                    "region": a.region(),
                    "expires_at": a.expires_at or None,
                    "needs_refresh": a.needs_refresh(
                        workbuddy_auth.REFRESH_LEAD_S),
                    "has_refresh_token": bool(a.refresh_token),
                    "access_token_masked": mask_key(a.access_token),
                })
                accounts.append(entry)
        return accounts

    @app.get("/admin/workbuddy/accounts")
    async def workbuddy_accounts(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        return ORJSONResponse({"accounts": _workbuddy_accounts()})

    @app.post("/admin/workbuddy/import")
    async def workbuddy_import(request: Request):
        """Import WorkBuddy account auth JSONs as provider pool keys.

        Accepts the workbuddy2api ``auths/`` file format — either a single
        auth object (nested ``{auth, account}`` or flat) or a list of them.
        Each entry becomes one pool key on the target provider (created on
        first import with the default WorkBuddy base URL). Import succeeds
        only if every entry parses; partial failures are rejected so the
        admin can fix the file rather than end up with a half-imported set.
        """
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "workbuddy-main").strip()
        base_url = (str(body.get("base_url") or "").strip()
                    or _default_base_url("workbuddy"))
        accounts = body.get("accounts")
        if accounts is None and isinstance(body.get("auth"), dict):
            accounts = [{"auth": body["auth"],
                         "account": body.get("account") or {}}]
        if not isinstance(accounts, list) or not accounts:
            return _err(400, "invalid_request_error",
                        "'accounts' must be a non-empty list of WorkBuddy "
                        "auth JSON objects (or a single auth object)",
                        request)
        parsed: list[tuple[str, str]] = []
        for i, raw in enumerate(accounts):
            if not isinstance(raw, dict):
                return _err(400, "invalid_request_error",
                            f"accounts[{i}] is not a JSON object", request)
            try:
                a = workbuddy_auth.parse_auth(orjson.dumps(raw).decode())
            except workbuddy_auth.WorkBuddyAuthError as e:
                return _err(400, "invalid_request_error",
                            f"accounts[{i}]: {e}", request)
            label = (a.nickname or a.uid or f"wb-{i + 1}").strip()[:64] \
                or f"wb-{i + 1}"
            parsed.append((label, a.to_secret()))
        existing = state.router.providers.get(provider)
        if existing is not None and existing.provider_type != "workbuddy":
            return _err(409, "invalid_request_error",
                        f"provider '{provider}' exists with type "
                        f"'{existing.provider_type}'", request)
        taken = {k.label for k in existing.keys} if existing else set()
        for label, _ in parsed:
            if label in taken:
                return _err(409, "invalid_request_error",
                            f"account label '{label}' already exists on "
                            f"provider '{provider}' — nothing imported",
                            request)
        if existing is None:
            state.router.providers[provider] = ProviderAccount(
                name=provider, provider_type="workbuddy", base_url=base_url,
                keys=[ProviderKey(label=label, secret=secret) for label,
                      secret in parsed])
            if state.config_store:
                await state.config_store.add_provider(provider, "workbuddy",
                                                      base_url)
                for label, secret in parsed:
                    await state.config_store.add_key(provider, label, secret)
        else:
            for label, secret in parsed:
                existing.keys.append(ProviderKey(label=label, secret=secret))
                if state.config_store:
                    await state.config_store.add_key(provider, label, secret)
        await state.logs.log_audit(
            actor="master", action="workbuddy.import", target=provider,
            diff={"accounts": len(parsed)})
        return ORJSONResponse({
            "provider": provider, "imported": len(parsed),
            "labels": [label for label, _ in parsed],
        })

    @app.get("/admin/workbuddy/export")
    async def workbuddy_export(request: Request):
        """Export every WorkBuddy account in the workbuddy2api auths/ shape.

        Optionally scoped with ?provider=name. Secrets (access + refresh
        tokens) are returned in full — this is a credential backup, guarded
        by the master key like key reveals, and audit-logged.
        """
        resp = _require_admin(request)
        if resp:
            return resp
        provider_filter = request.query_params.get("provider", "").strip()
        out: list[dict[str, Any]] = []
        for name, acct in sorted(state.router.providers.items()):
            if acct.provider_type != "workbuddy":
                continue
            if provider_filter and name != provider_filter:
                continue
            for k in acct.keys:
                try:
                    a = workbuddy_auth.parse_auth(k.secret)
                except workbuddy_auth.WorkBuddyAuthError:
                    continue  # bare-token or corrupt secret: not exportable
                out.append({
                    "auth": {
                        "accessToken": a.access_token,
                        "refreshToken": a.refresh_token,
                        "expiresAt": a.expires_at,
                        "domain": a.domain,
                    },
                    "account": {
                        "uid": a.uid,
                        "enterpriseId": a.enterprise_id,
                        "nickname": a.nickname,
                    },
                })
        await state.logs.log_audit(
            actor="master", action="workbuddy.export", target=provider_filter,
            diff={"accounts": len(out)})
        return ORJSONResponse({"accounts": out})

    @app.post("/admin/workbuddy/refresh")
    async def workbuddy_refresh(request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "").strip()
        label = str(body.get("label") or "").strip()
        if not provider or not label:
            return _err(400, "invalid_request_error",
                        "provider and label are required", request)
        result = await refresh_key_now(state, provider, label)
        if not result["ok"]:
            return _err(502, "api_error",
                        f"workbuddy refresh failed: {result['error']}",
                        request)
        await state.logs.log_audit(
            actor="master", action="workbuddy.refresh",
            target=f"{provider}/{label}", diff={})
        return ORJSONResponse({"provider": provider, "label": label,
                               "refreshed": True})

    @app.get("/public/models")
    async def public_models() -> ORJSONResponse:
        groups = []
        for gname in sorted(state.router.groups):
            deps = state.router.groups[gname]
            groups.append({
                "name": gname,
                "deployments": [{
                    "provider": d.provider.name,
                    "model_id": d.model_id,
                } for d in deps],
            })
        return ORJSONResponse({
            "groups": groups,
            "aliases": {k: _alias_value_to_json(v)
                        for k, v in state.router.settings.model_group_alias.items()},
        })

    # -- admin UI (built SPA; wiwi/server/static produced by `cd web && bun run build`)
    static_dir = Path(os.environ.get("WIWI_STATIC_DIR")
                      or Path(__file__).parent / "static")

    class SPAStaticFiles(StaticFiles):
        """StaticFiles with SPA history fallback: unknown paths get index.html
        so client-side routes like /login or /app survive hard refresh."""

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
