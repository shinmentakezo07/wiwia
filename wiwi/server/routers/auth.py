"""Session-cookie auth surface: signup / login / logout / me / playground-key.

Extracted verbatim from ``server/app.py`` (round 206): same paths, same
status codes, same cookie attributes, same throttle scopes. The handlers
close over per-app objects handed in by ``create_app`` (``state``,
``json_body`` and ``_err``), so the router is built once per app rather
than at import time; the guards are the module-level ``Depends``
dependencies from ``wiwi.server.dependencies`` (``CurrentUser`` where an
anonymous caller gets 401, ``resolve_current_user`` where anonymous is a
valid case).

No session is created or read here without going through ``sign_session``
/ ``resolve_current_user``; the secrets never leave ``state.users``.
"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Request

from wiwi.auth.users import SESSION_TTL, sign_session
from wiwi.server.dependencies import (
    SYNTHETIC_MASTER_ID,
    ORJSONResponse,
    CurrentUser,
    app_state,
    client_ip,
    request_is_https,
    resolve_current_user,
)
from wiwi.server.services.playground import playground_bearer


def make_router(state, config, *, json_body, _err):
    """Build the /auth router bound to the per-app helpers."""
    api = APIRouter(prefix="/auth", tags=["auth"])

    def _set_session_cookie(resp: ORJSONResponse, uid: str, role: str,
                            *, secure: bool) -> None:
        tok = sign_session(state.users._secret, uid, role,  # type: ignore[union-attr]
                           expires=time.time() + SESSION_TTL)
        resp.set_cookie("wiwi_session", tok, max_age=SESSION_TTL,
                        httponly=True, samesite="lax", secure=secure,
                        path="/")

    def _clear_session_cookie(resp: ORJSONResponse) -> None:
        resp.delete_cookie("wiwi_session", path="/")

    @api.post("/signup")
    async def auth_signup(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        if state.users is None:
            return _err(500, "api_error", "user service not initialized", request)
        # The decoders assume strings; a non-string username/password used to
        # raise TypeError/AttributeError and surface as a 500 on an endpoint
        # that promises 400/401 (AUDIT #80). Reject at the boundary.
        if not isinstance(body.get("username"), str) or not isinstance(
                body.get("password"), str):
            return _err(400, "invalid_request_error",
                        "username and password must be strings", request)
        # Registration is unauthenticated, so it needs its own throttle:
        # otherwise one host can create unlimited accounts (each of which mints
        # a playground key) and exhaust the user table or disk.
        scope = client_ip(request, config.general_settings.trusted_proxies)
        allowed, retry_after = await state.signup_throttle.try_consume(scope)
        if not allowed:
            resp = _err(429, "rate_limit_error",
                        f"too many signups from this address, retry in "
                        f"{retry_after}s", request)
            resp.headers["Retry-After"] = str(retry_after)
            return resp
        try:
            u = await state.users.create_user(
                body["username"], body["password"])
        except ValueError as e:
            # distinguish duplicate (409) from validation (400)
            if "already taken" in str(e):
                return _err(409, "conflict", str(e), request)
            return _err(400, "invalid_request_error", str(e), request)
        # The slot was reserved atomically before creation (AUDIT #221/#89), so
        # a successful signup consumes it with no further bookkeeping.
        #
        # No playground key is minted here any more: the wrapper mints one on
        # the first /v1/playground/completions call and reuses it from then
        # on, so signup cannot leave an unused credential behind.
        anon = await resolve_current_user(request, app_state(request)) is None
        resp = ORJSONResponse(
            {"user": {"id": u.id, "username": u.username, "role": u.role}},
            status_code=201)
        # Only log in the new user when the caller is anonymous: an admin
        # creating a user via signup should keep their own session.
        if anon:
            _set_session_cookie(resp, u.id, u.role,
                                secure=request_is_https(
                                    request,
                                    config.general_settings.trusted_proxies))
        return resp

    @api.post("/login")
    async def auth_login(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        if state.users is None:
            return _err(500, "api_error", "user service not initialized", request)
        # Normalize the account identifier before it becomes a throttle bucket:
        # `username` is lower-cased before lookup, so case variants of one
        # account must share a bucket or each spelling gets a fresh failure
        # budget (AUDIT #72). A non-string is rejected as invalid credentials.
        raw_user = body.get("username", "")
        if not isinstance(raw_user, str):
            return _err(401, "authentication_error", "invalid credentials", request)
        norm_user = raw_user.strip().lower()
        # Throttle by client IP *and* normalized account, so neither a single
        # account nor one host can be brute-forced without limit. The
        # master-key branch is keyed on the IP alone: the username is not
        # consulted there, so including it would hand an attacker a fresh
        # budget per arbitrary string against a full-admin credential.
        ip = client_ip(request, config.general_settings.trusted_proxies)
        mk = body.get("master_key")
        scope = f"{ip}:master" if mk else f"{ip}:{norm_user}"
        # Reserve the attempt *before* verifying. Reserving and counting under
        # one lock is what makes the cap hold under concurrency: the old
        # check-then-record pair let a burst of 20 all pass `check` before any
        # recorded a failure, so `limit=5` admitted 20 (AUDIT #221). A
        # successful login refunds the slot below, so only failures consume the
        # window.
        allowed, retry_after = await state.login_throttle.try_consume(scope)
        if not allowed:
            resp = _err(429, "rate_limit_error",
                        f"too many failed login attempts, retry in {retry_after}s",
                        request)
            resp.headers["Retry-After"] = str(retry_after)
            return resp
        # master-key login → synthetic master admin
        if mk:
            if hmac.compare_digest(str(mk).encode(),
                                   (config.general_settings.master_key or "").encode()):
                # No playground key is minted on login: the wrapper mints one
                # on the first Playground request and reuses it thereafter.
                resp = ORJSONResponse(
                    {"user": {"id": SYNTHETIC_MASTER_ID,
                              "username": SYNTHETIC_MASTER_ID,
                              "role": "admin"}})
                _set_session_cookie(resp, SYNTHETIC_MASTER_ID, "admin",
                                    secure=request_is_https(
                                        request,
                                        config.general_settings.trusted_proxies))
                # A successful login is not a failure: hand the reserved slot
                # back so valid traffic never exhausts the window.
                await state.login_throttle.reset(scope)
                return resp
            return _err(401, "authentication_error", "invalid master key", request)
        # username/password login
        password = body.get("password", "")
        if not isinstance(password, str):
            return _err(401, "authentication_error", "invalid credentials", request)
        try:
            u = await state.users.verify(norm_user, password)
        except ValueError:
            # malformed username charset/length — treat as invalid credentials
            return _err(401, "authentication_error", "invalid credentials", request)
        if u is None or u.disabled:
            return _err(401, "authentication_error", "invalid credentials", request)
        # Successful login clears any accumulated failures for this scope
        # (including the slot just reserved above).
        await state.login_throttle.reset(scope)
        # No playground key is minted on login: the wrapper mints one on the
        # first Playground request and reuses it thereafter.
        resp = ORJSONResponse(
            {"user": {"id": u.id, "username": u.username, "role": u.role}})
        _set_session_cookie(resp, u.id, u.role,
                            secure=request_is_https(
                                request,
                                config.general_settings.trusted_proxies))
        return resp

    @api.post("/logout")
    async def auth_logout(request: Request):
        # Drop the cached playground key before ending the session. The
        # plaintext would otherwise sit in memory for the process lifetime
        # with nothing left to revalidate or evict it (round 105).
        who = await resolve_current_user(request, app_state(request))
        if who is not None:
            state.pg_bearers.drop(who.id)
        resp = ORJSONResponse({"ok": True})
        _clear_session_cookie(resp)
        return resp

    @api.get("/me")
    async def auth_me(request: Request):
        u = await resolve_current_user(request, app_state(request))
        if u is None:
            return ORJSONResponse({"user": None})
        return ORJSONResponse(
            {"user": {"id": u.id, "username": u.username, "role": u.role}})

    @api.post("/playground-key")
    async def auth_playground_key(request: Request, actor: CurrentUser):
        """Return the current session's playground key, minting only if needed.

        Back-compat fallback for tooling that still wants a bearer: the SPA's
        Playground does not call this — /v1/playground/completions resolves
        and reuses the key server-side. Repeated calls return the same live
        credential rather than minting a rival one each time. Requires an
        authenticated session — anonymous callers get 401.
        """
        if state.auth is None:
            return _err(500, "api_error", "gateway not initialized", request)
        try:
            plaintext = await playground_bearer(state, actor)
        except Exception as e:  # noqa: BLE001 — mint/DB failure is a 500, not a crash
            import structlog as _sl

            _sl.get_logger("wiwi.playground").error(
                "playground_bearer_unavailable", user_id=actor.id,
                error=type(e).__name__, detail=str(e))
            return _err(500, "api_error",
                        "could not prepare the playground key", request)
        return ORJSONResponse({"key": plaintext})

    return api
