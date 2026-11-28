"""Cline OAuth (paste-code and redirect flows; tokens live in the key pool).

Extracted verbatim from ``server/app.py`` (round 206): same paths, same
status codes, same audit trail. The module owns both the admin-gated
``/admin/cline/oauth/*`` endpoints and the public ``/cline/oauth/callback``
the browser returns to — one flow, one file, so the callback and the
connect path cannot drift apart.

The handlers close over per-app objects handed in by ``create_app``
(``state``, ``config``, ``json_body`` and ``_err``), so the router is
built once per app; the admin routes are gated by the module-level
``AdminUser`` dependency from ``wiwi.server.dependencies``.
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from wiwi.auth.keys import mask_key
from wiwi.providers import cline_oauth
from wiwi.server.dependencies import AdminUser, ORJSONResponse, request_is_https

# The paste-code exchange returns ``None`` for fields a truncated
# Google-OAuth code has swallowed; the connect record below falls back to
# what is already stored for exactly those fields.


def cline_oauth_setting_key(provider: str) -> str:
    """The config-store key holding *provider*'s Cline OAuth record.

    Public because ``server/app.py``'s provider-delete route clears the same
    record — one definition so the two paths cannot drift.
    """
    return f"cline_oauth:{provider}"


def make_router(state, config, *, json_body, _err):
    """Build the Cline OAuth router bound to the per-app helpers."""
    api = APIRouter(tags=["cline-oauth"])

    async def _cline_oauth_record(provider: str) -> dict | None:
        """The provider's stored Cline OAuth record, or None when unset."""
        if not state.config_store:
            return None
        return await state.config_store.get_setting(
            cline_oauth_setting_key(provider))

    async def _cline_store_access_token(provider: str, secret: str,
                                        owned: str | None) -> bool:
        """Write *secret* into every pool key this OAuth record owns.

        Replaces the old private ``_update_provider_secret``, which wrote
        ``acct.keys[0]`` and nothing else — the exact bug
        :meth:`wiwi.providers.cline_auto_refresh.ClineAutoRefresh.store_access_token`
        documents fixing. On a 2-key pool that single-key write left the
        sibling holding the stale access token; the round-robin cursor then
        handed the next request to that sibling, it 401'd, and the on-demand
        hook burned a *second* rotation of the single-use grant the operator
        had just consumed — ``invalid_grant``, then a dead provider.

        ``owned`` is the ``access_fingerprint`` the record carried *before*
        this token replaced it. Passing the post-rotation fingerprint instead
        would match nothing (so only ``keys[0]`` would be written — the bug
        again), and omitting it would clobber raw API keys the operator added
        to the pool, which are not this record's to overwrite.

        Returns False when the provider has no keys to land the token on.
        """
        from wiwi.providers.cline_auto_refresh import (
            _worker_for as cline_worker_for,
        )
        written = await cline_worker_for(state).store_access_token(
            provider, secret, owned=owned)
        return written > 0

    async def _cline_connect_record(provider: str,
                                    tokens: dict) -> dict:
        """The OAuth record to persist for a freshly exchanged auth code.

        ``cline_oauth.exchange_code`` deliberately returns ``None`` for fields
        a truncated Google-OAuth code has swallowed (see its docstring — the
        signature eats the closing quote of ``accessToken`` and every field
        after it, so a truncated code yields a usable access token and **no**
        ``refreshToken``). Writing that ``None`` over a working record
        disarmed auto-refresh silently: ``_sweep`` skips any provider whose
        record has no ``refresh_token``, so the account survived until token
        expiry and then died with no recovery but another manual paste.

        Every field the exchange legitimately omits therefore falls back to
        what is already stored, and the fingerprint of the token this record
        issues is stamped so a later rotation knows which pool keys it owns.
        """
        existing = await _cline_oauth_record(provider) or {}
        return {
            "refresh_token": (tokens.get("refresh_token")
                              or existing.get("refresh_token")),
            "email": tokens.get("email") or existing.get("email"),
            # A truncated code has no expiresAt either. Resolving it through
            # the shared helper gives a forward window; *preserving* the old
            # (already-stale) one would leave the record permanently due and
            # re-present the single-use grant on every 60 s tick.
            "expires_at": (tokens.get("expires_at")
                           or cline_oauth.resolve_expires_at(None)),
            "access_fingerprint": cline_oauth.access_fingerprint(
                tokens["access_token"]),
        }

    def _request_base(request: Request) -> str:
        """Absolute base URL (scheme + host) for building callback URLs.

        ``wiwi_settings.public_url`` wins when configured — it is trusted
        operator config. Otherwise the request's own ``Host`` header is used,
        with the scheme from ``request_is_https``: behind an external TLS
        terminator the ASGI scheme is http, so a callback URL built from it
        would send the authorization code back over plaintext (round 105).
        ``X-Forwarded-Host`` is deliberately NOT honoured: it is client
        controlled, and trusting it let an attacker point an OAuth callback
        (and thus the authorization code) at their own origin.
        """
        cfg_url = (config.wiwi_settings.public_url or "").strip().rstrip("/")
        if cfg_url:
            return cfg_url
        host = request.headers.get("host") or "localhost"
        scheme = "https" if request_is_https(
            request, config.general_settings.trusted_proxies) else "http"
        return f"{scheme}://{host}"

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

    @api.post("/admin/cline/oauth/login-url")
    async def cline_oauth_login_url(request: Request, actor: AdminUser):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        callback = str(body.get("callback_url") or "").strip()
        if not callback:
            return _err(400, "invalid_request_error",
                        "callback_url is required", request)
        return ORJSONResponse({"auth_url": cline_oauth.build_auth_url(callback)})

    @api.post("/admin/cline/oauth/connect")
    async def cline_oauth_connect(request: Request, actor: AdminUser):
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
        # Reject an empty pool BEFORE persisting anything: the OAuth state must
        # not survive a connect that had nowhere to land the token.
        acct = state.router.providers[provider]
        if not acct.keys:
            return _err(400, "invalid_request_error",
                        f"provider '{provider}' has no keys — add a pool key "
                        f"first, then connect", request)
        # The record (which carries the single-use grant) is persisted first,
        # the pool keys second — the same ordering the refresh worker uses,
        # because the record is the only copy of the replacement grant.
        record = await _cline_connect_record(provider, tokens)
        # Capture the fingerprint this record issued *previously*: it is what
        # tells ``store_access_token`` which keys the new token may overwrite.
        previous = await _cline_oauth_record(provider) or {}
        owned = previous.get("access_fingerprint")
        if state.config_store:
            await state.config_store.set_setting(
                cline_oauth_setting_key(provider), record)
        if not await _cline_store_access_token(
                provider, tokens["access_token"], owned):
            return _err(400, "invalid_request_error",
                        f"provider '{provider}' has no keys — add a pool key "
                        f"first, then connect", request)
        await state.logs.log_audit(
            actor=actor.username, action="cline_oauth.connect", target=provider,
            diff={"email": record.get("email")})
        return ORJSONResponse({
            "provider": provider,
            "email": record.get("email"),
            "access_token_masked": mask_key(tokens["access_token"]),
        })

    @api.post("/admin/cline/oauth/auto-connect")
    async def cline_oauth_auto_connect(request: Request, actor: AdminUser):
        """Initiate an automatic (redirect-based) Cline OAuth connect.

        Unlike the paste-code flow, this returns a Cline auth URL whose
        callback points back to wiwi's own ``/cline/oauth/callback``. After
        the admin logs in at Cline, Cline redirects the browser to that
        callback with ``?code=...``; wiwi decodes the embedded tokens,
        persists them, and redirects the browser to the SPA. No manual
        copy-paste required.
        """
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
        # Evict every expired pending session, whichever flow owns it.
        state.evict_expired_pending()
        now = time.time()
        state_token = secrets.token_urlsafe(24)
        state.oauth_pending[state_token] = {
            "kind": "cline_redirect",
            "provider": provider,
            "return_path": return_path,
            "created_at": now,
            "ttl_s": 600.0,
        }
        # The callback_url Cline redirects to after login. It carries the
        # state token so we can match the pending session on return.
        cb = f"{_request_base(request)}/cline/oauth/callback?state={state_token}"
        return ORJSONResponse({
            "auth_url": cline_oauth.build_auth_url(cb),
            "state": state_token,
            "provider": provider,
        })

    @api.get("/cline/oauth/callback")
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
        sess = state.oauth_pending.pop(state_token, None)
        if sess is None:
            return _cline_callback_error(request, "invalid or expired session")
        # Enforce the pending session's own TTL on the READ path, the way the
        # Hermes callback already does. Without this the state
        # token stayed usable for the whole process lifetime: expiry was only
        # applied by ``evict_expired_pending()``, which runs when a *new* flow
        # starts, so a leaked/unused token never aged out.
        if time.time() - sess.get("created_at", 0.0) > sess.get("ttl_s", 600.0):
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
        if not state.router.providers[provider].keys:
            return _cline_callback_redirect(return_path, provider,
                                             error="provider has no pool keys")
        record = await _cline_connect_record(provider, tokens)
        previous = await _cline_oauth_record(provider) or {}
        owned = previous.get("access_fingerprint")
        if state.config_store:
            await state.config_store.set_setting(
                cline_oauth_setting_key(provider), record)
        if not await _cline_store_access_token(
                provider, tokens["access_token"], owned):
            return _cline_callback_redirect(return_path, provider,
                                             error="provider has no pool keys")
        await state.logs.log_audit(
            actor="master", action="cline_oauth.connect", target=provider,
            diff={"email": record.get("email"), "flow": "auto"})
        return _cline_callback_redirect(return_path, provider,
                                         email=record.get("email"))

    @api.get("/admin/cline/oauth/status")
    async def cline_oauth_status(request: Request, actor: AdminUser):
        provider = request.query_params.get("provider", "").strip()
        if not provider or provider not in state.router.providers:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        record = None
        if state.config_store:
            record = await state.config_store.get_setting(
                cline_oauth_setting_key(provider))
        if not record:
            # AUDIT #411: an account provisioned by pasting a credential file
            # never creates the ``cline_oauth:<name>`` settings row, but its
            # JWT lives on the pool key. Fall back to decoding that so the
            # console can say "expired credential, still deployed" instead of
            # reporting a serving account as disconnected.
            acct = state.router.providers[provider]
            claims: dict = {}
            for k in acct.keys:
                claims = cline_oauth.jwt_credential_claims(k.secret)
                if claims:
                    break
            if not claims:
                return ORJSONResponse({"connected": False})
            exp = claims.get("exp")
            exp_epoch = float(exp) if isinstance(exp, (int, float)) and not \
                isinstance(exp, bool) else None
            exp_iso = (datetime.fromtimestamp(exp_epoch, tz=UTC)
                       .isoformat() if exp_epoch is not None else None)
            return ORJSONResponse({
                "connected": True,
                "email": claims.get("email"),
                "expires_at": exp_iso,
                "needs_refresh": bool(
                    exp_epoch is not None
                    and exp_epoch - time.time() <= cline_oauth.REFRESH_LEAD_S),
                "source": "credential",
            })
        expires_epoch = cline_oauth.parse_expires_at(record.get("expires_at"))
        return ORJSONResponse({
            "connected": True,
            "email": record.get("email"),
            "expires_at": record.get("expires_at"),
            "needs_refresh": bool(
                expires_epoch is not None
                and cline_oauth.expires_within_lead(expires_epoch)),
        })

    @api.post("/admin/cline/oauth/refresh")
    async def cline_oauth_refresh(request: Request, actor: AdminUser):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "").strip()
        if provider not in state.router.providers:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        record = await _cline_oauth_record(provider)
        if not record or not record.get("refresh_token"):
            return _err(400, "invalid_request_error",
                        f"provider '{provider}' has no stored Cline tokens; "
                        "connect first", request)
        # Delegate to the shared auto-refresh worker so this admin path and the
        # 60 s sweeper / gateway 401 hook share one per-provider lock and one
        # circuit breaker — the same convention as the Hermes admin
        # refresh endpoint.
        #
        # It used to call ``cline_oauth.refresh_token`` itself: a fourth
        # rotation path with no lock and no circuit. Cline's refreshToken is
        # single-use, so an operator clicking "Refresh" while the sweeper held
        # the wire made both POST the same grant; the loser's ``invalid_grant``
        # called ``mark_dead`` and the provider stayed dead until a human
        # re-authenticated. Worse, the admin path never touched the circuit,
        # so its own ``invalid_grant`` did not even record the death — it
        # failed silently while the sweeper kept burning grants.
        #
        # ``force=True`` inside the worker is deliberate and matches Hermes:
        # an explicit operator rotation is the documented way out of a tripped
        # (or dead) circuit. It still takes the lock and still honors the
        # single-use-grant bookkeeping.
        from wiwi.providers.cline_auto_refresh import (
            refresh_key_now as cline_refresh_key_now,
        )
        result = await cline_refresh_key_now(state, provider)
        if not result["ok"]:
            # A permanently rejected grant needs a human re-login — surface it
            # as 401, not a generic 502.
            if result["relogin_required"]:
                return _err(401, "authentication_error", result["error"], request)
            return _err(502, "upstream_error", result["error"], request)
        await state.logs.log_audit(
            actor=actor.username, action="cline_oauth.refresh", target=provider)
        acct = state.router.providers[provider]
        fresh = await _cline_oauth_record(provider) or {}
        return ORJSONResponse({
            "provider": provider,
            "access_token_masked": mask_key(
                next((k.secret for k in acct.keys), "")),
            "expires_at": fresh.get("expires_at"),
        })

    @api.delete("/admin/cline/oauth/disconnect")
    async def cline_oauth_disconnect(request: Request, actor: AdminUser):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "").strip()
        if provider not in state.router.providers:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        if state.config_store:
            await state.config_store.delete_setting(
                cline_oauth_setting_key(provider))
        # Reclaim the per-provider lock / generation counter / dead-code entry
        # and — the point of this call — clear the refresh circuit. Nothing
        # else ever popped them, so ``CircuitBreaker._dead`` survived forever:
        # the operator disconnected, re-pasted a valid code, and the sweeper
        # still reported "blocked by the refresh circuit breaker" because the
        # new credential inherited the *old* grant's death. A Cline account
        # must be reclaimable the moment its credential is replaced.
        from wiwi.providers.cline_auto_refresh import (
            forget_provider as cline_forget_provider,
        )
        cline_forget_provider(state, provider)
        await state.logs.log_audit(
            actor=actor.username, action="cline_oauth.disconnect", target=provider)
        return ORJSONResponse({"provider": provider, "disconnected": True})

    return api
