"""Hermes (Nous Portal) OAuth — RFC 8628 device-code flow.

Nous Portal uses RFC 8628 device authorization (see
``wiwi.providers.hermes_oauth`` for the verified endpoint contract). The
admin starts a device-code session, opens the verification URL in a
browser, and wiwi polls the token endpoint until approval. The access
token lands on the provider's first pool key prefixed ``hermes:``
(``HermesAdapter`` strips the prefix for the bearer); the refresh token is
kept in the config store for on-demand refresh, mirroring the Cline flow.

Extracted verbatim from ``server/app.py`` (round 206): same paths, same
status codes, same audit trail. The handlers close over per-app objects
handed in by ``create_app`` (``state``, ``json_body`` and ``_err``), so
the router is built once per app; the admin routes are gated by the
module-level ``AdminUser`` dependency from ``wiwi.server.dependencies``.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time

import httpx
import orjson
import structlog
from fastapi import APIRouter, Request

from wiwi.auth.keys import mask_key
from wiwi.providers import hermes_oauth
from wiwi.router.router import ProviderAccount, ProviderKey, _default_base_url
from wiwi.server.dependencies import AdminUser, ORJSONResponse


def _hermes_oauth_setting_key(provider: str) -> str:
    return f"hermes_oauth:{provider}"


def _hermes_account_label(access_token: str) -> str:
    """Human label for a connected Nous account, from its JWT email.

    The Hermes CLI labels credential-pool entries by account email; wiwi
    mirrors that so a multi-account pool reads the same in the console.
    Falls back to a short token fingerprint when the JWT carries no email
    or is not decodable — never raises on a malformed token.
    """
    import base64

    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = orjson.loads(base64.urlsafe_b64decode(payload))
        email = claims.get("email") if isinstance(claims, dict) else None
        if isinstance(email, str) and email:
            return email
    except Exception as e:  # noqa: BLE001 — label is best-effort, never fatal
        structlog.get_logger("wiwi.hermes_oauth").debug(
            "hermes_account_label_decode_failed", error=type(e).__name__)
    return mask_key(access_token)


def _unique_hermes_key_label(state, provider: str, base: str) -> str:
    """First ``base``-suffixed pool-key label not already taken.

    *base* is the ACCOUNT label (an email, or a masked token fingerprint),
    never the credential blob: pool-key labels are not masked — unlike
    ``secret`` — and are echoed verbatim by ``/poll``, ``/status``,
    ``/admin/providers``, generic key listings and every request log row, so
    a blob here would publish the plaintext access + refresh tokens and keep
    publishing them after a rotation (which only rewrites ``k.secret``).

    A re-login for the SAME account does not come through here at all:
    ``/poll`` finds the existing key by its decoded credential label and
    rotates it in place. This only guarantees that a genuinely NEW account
    whose label collides with a taken one gets ``base-2``, ``base-3``… so
    accounts never silently collide.
    """
    acct = state.router.providers.get(provider)
    taken = {k.label for k in acct.keys} if acct is not None else set()
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def make_router(state, *, json_body, _err):
    """Build the Hermes OAuth router bound to the per-app helpers."""
    api = APIRouter(prefix="/admin/hermes/oauth", tags=["hermes-oauth"])

    @api.post("/device-code")
    async def hermes_oauth_device_code(request: Request, actor: AdminUser):
        """Start a device-code authorization; return the verification URL."""
        provider = request.query_params.get("provider", "").strip()
        if not provider:
            return _err(400, "invalid_request_error",
                        "provider is required", request)
        acct = state.router.providers.get(provider)
        if acct is None:
            # OAuth bootstraps the account: there is nothing to add a key *to*
            # yet, which is the entire point of device-code login. Create a
            # Hermes account with a single placeholder pool key; /poll DROPS
            # that placeholder (memory + DB) and appends the real credential on
            # approval — it appends, it does not replace. A surviving
            # placeholder is enabled+active in smooth-WRR rotation and decodes
            # to no credential, so it would send ``Authorization: Bearer
            # pending`` — one guaranteed upstream 401 per cycle, forever.
            name = provider
            base_url = None
            label = "default"
            secret = hermes_oauth.HERMES_PLACEHOLDER_SECRET
            state.router.providers[name] = ProviderAccount(
                name=name, provider_type="hermes",
                base_url=base_url or _default_base_url("hermes"),
                keys=[ProviderKey(label=label, secret=secret)])
            try:
                if state.config_store:
                    # This branch only runs when the account is absent from the
                    # router, which is built from the DB at startup — so the DB
                    # has no such row and this is a genuinely new account.
                    await state.config_store.add_provider(
                        name, "hermes", base_url or _default_base_url("hermes"))
                    await state.config_store.add_key(name, label, secret)
            except Exception as e:  # noqa: BLE001 — YAML/DB path varies; in-memory is enough
                structlog.get_logger("wiwi.hermes_oauth").warning(
                    "hermes_oauth_bootstrap_db_write_failed provider=%s err=%s",
                    name, e)
            acct = state.router.providers[name]
        if acct.provider_type != "hermes":
            return _err(400, "invalid_request_error",
                        f"provider '{provider}' is not a Hermes account", request)
        if not acct.keys:
            return _err(400, "invalid_request_error",
                        f"provider '{provider}' has no keys — add a pool key "
                        "first, then connect", request)
        try:
            # Sync httpx with a 30 s timeout, on a worker thread: inline this
            # POST would stall the uvicorn event loop for the whole portal
            # round-trip, blocking every in-flight completion and stream. AUDIT
            # #426 threaded the /poll and /refresh call sites but missed this
            # one — the FIRST endpoint of the connect flow, so the stall landed
            # on the very request the operator was waiting on.
            device = await asyncio.to_thread(hermes_oauth.request_device_code)
        except httpx.HTTPError as e:
            return _err(502, "upstream_error",
                        f"Nous Portal device-code request failed: {e}", request)
        # Stash the device code so /poll can exchange it; TTL 10 minutes,
        # matching the Cline pending-session window.
        state.evict_expired_pending()
        token = secrets.token_urlsafe(24)
        state.oauth_pending[token] = {
            "kind": "hermes_device_code",
            "provider": provider,
            "device_code": device["device_code"],
            "interval": int(device["interval"]),
            "created_at": time.time(),
            "ttl_s": 600.0,
        }
        return ORJSONResponse({
            "state": token,
            "provider": provider,
            "user_code": device["user_code"],
            "verification_uri": device["verification_uri"],
            "verification_uri_complete": device["verification_uri_complete"],
            "expires_in": int(device["expires_in"]),
            "interval": int(device["interval"]),
        })

    @api.post("/poll")
    async def hermes_oauth_poll(request: Request, actor: AdminUser):
        """Poll the token endpoint once for a pending device-code session.

        Returns ``{"pending": True}`` while the user has not approved yet, or
        the connected account on success. One poll per call so the SPA drives
        the cadence rather than wiwi holding a long-lived request open.
        """
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        token = str(body.get("state") or "").strip()
        sess = state.oauth_pending.get(token)
        if not sess or sess.get("kind") != "hermes_device_code":
            return _err(404, "not_found_error", "unknown or expired session", request)
        provider = sess["provider"]
        if provider not in state.router.providers:
            state.oauth_pending.pop(token, None)
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        if time.time() - sess["created_at"] > sess.get("ttl_s", 600.0):
            state.oauth_pending.pop(token, None)
            return _err(410, "invalid_request_error",
                        "device-code session expired; start a new one", request)
        try:
            # Nous refresh is synchronous httpx (shared with the CLI connect
            # flow), so run the single poll on a worker thread: an inline POST
            # would stall the uvicorn event loop for the portal round-trip,
            # blocking every in-flight completion and stream (AUDIT #426/#33).
            tokens = await asyncio.to_thread(
                hermes_oauth.poll_token,
                sess["device_code"],
                poll_interval=sess.get("interval", 5),
                single_poll=True)
        except hermes_oauth.HermesAuthError as e:
            state.oauth_pending.pop(token, None)
            if e.relogin_required:
                return _err(401, "authentication_error", str(e), request)
            return _err(400, "invalid_request_error", str(e), request)
        except httpx.HTTPError as e:
            return _err(502, "upstream_error",
                        f"Nous Portal token poll failed: {e}", request)
        if tokens is None:
            # authorization_pending: keep the session, tell the SPA to retry.
            return ORJSONResponse({"pending": True})
        state.oauth_pending.pop(token, None)
        access = tokens["access_token"]
        expires_at = hermes_oauth.compute_expires_at(
            tokens.get("expires_in"), time.time())
        # Each Nous account is a separate OAuth identity with its own rotating
        # refresh token and its own rate-limit bucket, so a connect APPENDS the
        # account as a new pool key rather than overwriting key #0 — the key
        # pool's WRR then spreads traffic across accounts (AUDIT #427).
        acct = state.router.providers.get(provider)
        if acct is None:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        # Drop the bootstrap placeholder /device-code seeded (memory AND DB).
        # "hermes:pending" decodes to no credential, so credential_bearer falls
        # through and sends ``Authorization: Bearer pending`` — and since
        # ProviderKey defaults to enabled=True/status="active", it would sit in
        # smooth-WRR rotation for the life of the process, burning one
        # guaranteed-401 upstream request (plus a refresh hook that can never
        # succeed) on every cycle. /poll only APPENDED, so nothing ever removed it.
        for gone in [k.label for k in acct.keys
                     if hermes_oauth.is_placeholder(k.secret)]:
            acct.keys[:] = [k for k in acct.keys if k.label != gone]
            try:
                if state.config_store:
                    await state.config_store.delete_key(provider, gone)
            except Exception as e:  # noqa: BLE001 — YAML/DB path varies
                structlog.get_logger("wiwi.hermes_oauth").warning(
                    "hermes_oauth_placeholder_db_delete_failed provider=%s "
                    "key=%s err=%s", provider, gone, e)
        account_label = _hermes_account_label(access)
        secret = hermes_oauth.encode_credential(
            access, tokens.get("refresh_token"), expires_at, account_label)
        # The pool-key label is the ACCOUNT label — never the credential blob.
        # Labels are not masked (unlike ``secret``) and are returned verbatim by
        # /poll, /status, the generic key listings, every LogEvent row and the
        # auto-refresh log line; a blob there published both plaintext tokens and
        # kept publishing the ORIGINAL (now-dead) ones, because a rotation only
        # rewrites k.secret.
        existing = next(
            (k for k in acct.keys
             if (prev := hermes_oauth.decode_credential(k.secret)) is not None
             and prev.get("label") == account_label),
            None)
        if existing is not None:
            # Re-login of an account already in the pool: rotate it IN PLACE.
            # Appending instead left the old key holding a single-use grant that
            # this very login had already consumed — the sweeper would later
            # present it, get ``invalid_grant``, and that account's circuit would
            # stay dead until a human re-authenticated it.
            label = existing.label
            existing.secret = secret
            # Route the reset through ProviderKey.recover(force=True) rather than
            # hand-setting the two fields: the hand-rolled form left err_count at
            # its retirement value, so the next non-200 re-retired the key through
            # on_result (AUDIT #199). A fresh OAuth grant is exactly an operator
            # reset, so it earns the same streak and WRR reset.
            existing.recover(force=True)
            try:
                if state.config_store:
                    await state.config_store.update_key_secret(
                        provider, label, secret)
            except Exception as e:  # noqa: BLE001 — YAML/DB path varies
                structlog.get_logger("wiwi.hermes_oauth").warning(
                    "hermes_oauth_rotate_db_write_failed provider=%s key=%s "
                    "err=%s", provider, label, e)
        else:
            label = _unique_hermes_key_label(state, provider, account_label)
            acct.keys.append(ProviderKey(label=label, secret=secret))
            try:
                if state.config_store:
                    await state.config_store.add_key(provider, label, secret)
            except Exception as e:  # noqa: BLE001 — YAML/DB path varies; in-memory is enough
                structlog.get_logger("wiwi.hermes_oauth").warning(
                    "hermes_oauth_account_db_write_failed provider=%s err=%s",
                    provider, e)
        await state.logs.log_audit(
            actor=actor.username, action="hermes_oauth.connect", target=provider)
        return ORJSONResponse({
            "provider": provider,
            "connected": True,
            "key_label": label,
            "access_token_masked": mask_key(access),
            "expires_at": expires_at,
        })

    @api.get("/status")
    async def hermes_oauth_status(request: Request, actor: AdminUser):
        provider = request.query_params.get("provider", "").strip()
        acct = state.router.providers.get(provider) if provider else None
        if acct is None:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        # One account per pool key (AUDIT #427): report each with its own
        # expiry/refresh state so the console shows the whole pool.
        accounts = []
        for k in acct.keys:
            cred = hermes_oauth.decode_credential(k.secret)
            if cred is None:
                # A raw key or bare hermes:<token>: usable for inference, but
                # not an auto-refreshable OAuth account.
                accounts.append({"key_label": k.label, "managed": False})
                continue
            expires_at = cred.get("expires_at")
            accounts.append({
                "key_label": k.label,
                "managed": True,
                "label": cred.get("label"),
                "expires_at": expires_at,
                "needs_refresh": bool(
                    hermes_oauth.expires_within_lead(expires_at, time.time())),
                "has_refresh_token": bool(cred.get("refresh_token")),
            })
        if not any(a["managed"] for a in accounts):
            return ORJSONResponse({"connected": False, "accounts": accounts})
        return ORJSONResponse({
            "connected": True,
            "accounts": accounts,
        })

    @api.post("/refresh")
    async def hermes_oauth_refresh(request: Request, actor: AdminUser):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "").strip()
        acct = state.router.providers.get(provider)
        if acct is None:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        # Per-account: refresh one pool key (default: the first managed one).
        label = str(body.get("key_label") or "").strip()
        cred = None
        for k in acct.keys:
            if label and k.label != label:
                continue
            cred = hermes_oauth.decode_credential(k.secret)
            if cred and cred.get("refresh_token"):
                label = k.label
                break
        if not cred or not cred.get("refresh_token"):
            return _err(400, "invalid_request_error",
                        f"provider '{provider}' has no stored Hermes tokens; "
                        "connect first", request)
        # Delegate to the shared auto-refresh worker so this admin path and the
        # gateway 401 hook share one per-account lock + circuit, and force past
        # a tripped circuit (an operator asking for a rotation is the way out).
        #
        # The worker is resolved through the module's own ``_worker_for``, never
        # through ``state.hermes_refresh``: that attribute is None before the
        # lifespan runs (and in minimal test states), and the previous code
        # papered over it with ``except AttributeError`` — then called
        # ``state.hermes_refresh._update_secret`` on the very object it had just
        # proven was None, and dereferenced ``state.hermes_refresh._circuit``
        # unconditionally. Both raised only AFTER the threaded exchange had
        # already rotated Nous's single-use refresh grant, so the fresh token was
        # discarded, the pool key kept the now-consumed grant, and the next
        # refresh came back invalid_grant -> a dead circuit and a forced
        # re-login from one admin click. It also masked genuine AttributeErrors
        # raised inside ``_refresh_locked`` after the grant was spent.
        from wiwi.providers.hermes_auto_refresh import (
            refresh_key_now as hermes_refresh_key_now,
        )
        result = await hermes_refresh_key_now(state, provider, label)
        if not result["ok"]:
            # A dead grant (invalid_grant / revoked / reused) needs a human
            # re-login — surface that as 401, not a generic 502.
            if result["relogin_required"]:
                return _err(401, "authentication_error",
                            "Nous refresh token rejected — re-login required",
                            request)
            return _err(502, "upstream_error", result["error"], request)
        await state.logs.log_audit(
            actor=actor.username, action="hermes_oauth.refresh", target=provider)
        new_cred = hermes_oauth.decode_credential(
            next(k.secret for k in acct.keys if k.label == label))
        return ORJSONResponse({
            "provider": provider,
            "key_label": label,
            "access_token_masked": mask_key(new_cred.get("access_token", "")),
            "expires_at": new_cred.get("expires_at"),
        })

    @api.delete("/disconnect")
    async def hermes_oauth_disconnect(request: Request, actor: AdminUser):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        provider = str(body.get("provider") or "").strip()
        acct = state.router.providers.get(provider)
        if acct is None:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider}'", request)
        # Per-account: with ``key_label`` remove just that account's pool key;
        # without it, remove every managed Hermes account (the whole-pool
        # disconnect). Also clears the legacy single-account settings record.
        # An un-bootstrapped ``hermes:pending`` placeholder counts as removable
        # too: it holds no credential, so requiring one left the operator unable
        # to clear the key that /device-code created and /poll may never have
        # replaced (e.g. the login was abandoned).
        label = str(body.get("key_label") or "").strip()
        removed = 0
        removed_labels: list[str] = []
        kept = []
        for k in acct.keys:
            removable = hermes_oauth.decode_credential(k.secret) is not None \
                or hermes_oauth.is_placeholder(k.secret)
            if (not label or k.label == label) and removable:
                removed += 1
                removed_labels.append(k.label)
                continue
            kept.append(k)
        acct.keys[:] = kept
        if state.config_store:
            # Every label actually removed must be deleted from the DB, not just
            # the requested one: the router is rebuilt from ``provider_keys`` at
            # startup (``ConfigStore.load_all``), so a whole-pool disconnect that
            # only filtered the in-memory list brought every account straight
            # back after a restart.
            for gone in removed_labels:
                try:
                    await state.config_store.delete_key(provider, gone)
                except Exception as e:  # noqa: BLE001 — YAML/DB path varies
                    structlog.get_logger("wiwi.hermes_oauth").warning(
                        "hermes_oauth_delete_key_failed provider=%s key=%s "
                        "err=%s", provider, gone, e)
            with contextlib.suppress(Exception):
                await state.config_store.delete_setting(
                    _hermes_oauth_setting_key(provider))
        # Reclaim the per-account lock / generation counter / circuit entry, or
        # they accumulate for every account ever connected (nothing else ever
        # popped them).
        if removed_labels:
            from wiwi.providers.hermes_auto_refresh import (
                _worker_for as hermes_worker_for,
            )
            worker = hermes_worker_for(state)
            for gone in removed_labels:
                worker.forget(provider, gone)
        await state.logs.log_audit(
            actor=actor.username, action="hermes_oauth.disconnect", target=provider)
        return ORJSONResponse({"provider": provider, "disconnected": removed})

    return api
