"""Admin surface for provider accounts: catalog, CRUD, key pools, import/export.

Extracted verbatim from ``server/app.py``: same paths, same methods, same
status codes, same error bodies, same audit trail — every ``AUDIT #NNN``
comment moved with its handler. The handlers close over per-app objects
handed in by ``create_app`` (``state``, ``json_body`` and ``_err``) plus
the app.py module-level helpers this cluster shares with the rest of the
file (``_apply_cline_default_models``, ``_provider_models_url``,
``_parse_models_response``, ``_redact_url_secret``), so the router is
built once per app rather than at import time — the same convention as
``wiwi/server/routers/cline_oauth.py``. Authorization is a module-level
dependency: each handler declares ``actor: AdminUser`` from
``wiwi/server/dependencies``.

``_key_view`` captures nothing per-app, so it lives at module level here.
The registration order below is the original one, keeping
``/admin/providers/export`` and ``/admin/providers/import`` in their
original slots relative to the ``{name}``-capturing routes (FastAPI
matches in registration order).
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request

from wiwi.auth.keys import mask_key
from wiwi.config import PROVIDER_TYPES, _interpolate
from wiwi.core.recovery import (
    PROBE_MAX_TOKENS,
    ProbeVerdict,
    _probe_request,
    build_url,
    probe_verdict,
)
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.registry import fresh_adapter
from wiwi.router.router import (
    BUILTIN_PROVIDER_TYPES,
    Deployment,
    ProviderAccount,
    ProviderKey,
    _default_base_url,
)
from wiwi.server.config_store import ConfigStoreNotFound
from wiwi.server.dependencies import AdminUser, ORJSONResponse
from wiwi.server.routers import cline_oauth as cline_oauth_router


def _key_view(k, now_mono: float, now_wall: float) -> dict:
    cooling = k.status == "cooling" and now_mono < k.cooldown_until
    status = "disabled" if not k.enabled else ("cooling" if cooling else k.status)
    return {
        "label": k.label,
        "masked": mask_key(k.secret),
        # Whether the pool member can actually authenticate. An empty
        # secret renders as the same ``***`` as a masked one, so without
        # this flag a restored (credential-less) key is indistinguishable
        # from a live one in the console.
        "has_secret": bool((k.secret or "").strip()),
        "weight": k.weight,
        "enabled": k.enabled,
        "status": status,
        "cooldown_remaining_s": round(max(0.0, k.cooldown_until - now_mono), 1),
        "req_count": k.req_count,
        "err_count": k.err_count,
        "last_used_ts": round(now_wall - (now_mono - k.last_used), 3)
                        if k.last_used else None,
    }


def make_router(state, *, json_body, _err,
                _apply_cline_default_models, _provider_models_url,
                _parse_models_response, _redact_url_secret):
    """Build the /admin/providers router bound to the per-app helpers."""
    api = APIRouter(tags=["admin-providers"])

    @api.get("/admin/provider-catalog")
    async def admin_provider_catalog(actor: AdminUser):
        configured = {a.provider_type for a in state.router.providers.values()}
        out = []
        for p in BUILTIN_PROVIDER_TYPES:
            entry = {**p, "builtin": True}
            entry["configured"] = p["provider_type"] in configured
            out.append(entry)
        return ORJSONResponse(out)

    @api.get("/admin/providers")
    async def admin_providers(actor: AdminUser):
        mono, wall = time.monotonic(), time.time()
        out = []
        for name in sorted(state.router.providers):
            acct = state.router.providers[name]
            out.append({
                "name": acct.name,
                "provider_type": acct.provider_type,
                "base_url": acct.base_url,
                "round_robin": acct.round_robin,
                "enabled": acct.enabled,
                "alias_id": acct.alias_id,
                "healthy": acct.healthy,
                "keys": [_key_view(k, mono, wall) for k in acct.keys],
            })
        return ORJSONResponse({"providers": out,
                               "alias_to_provider": dict(state.router.alias_to_provider)})

    @api.get("/admin/providers/{name}/keys/{label}/secret")
    async def admin_reveal_provider_key(name: str, label: str, request: Request,
                                        actor: AdminUser):
        """Return the plaintext provider key secret (admin-only).

        The pool listing only carries a masked form; the UI fetches the
        plaintext on demand when an admin explicitly reveals a key. Reveals
        are audit-logged like mutations since they expose a credential.
        """
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        key = acct.get_key(label)
        if key is None:
            return _err(404, "not_found_error",
                        f"unknown key '{label}' on provider '{name}'", request)
        await state.logs.log_audit(actor=actor.username, action="provider_key.reveal",
                                   target=f"{name}/{label}", diff={})
        return ORJSONResponse({"label": label, "secret": key.secret})

    @api.post("/admin/providers/{name}/keys/{label}/test")
    async def admin_test_provider_key(name: str, label: str, request: Request,
                                      actor: AdminUser):
        """Send a 1-token completion through one pool key against a chosen model.

        Bypasses the router (no cooldown/circuit side effects): it reports what
        the upstream says for exactly this key + model. Body: ``{"model": id}``.
        """
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        key = acct.get_key(label)
        if key is None:
            return _err(404, "not_found_error",
                        f"unknown key '{label}' on provider '{name}'", request)
        if not (key.secret or "").strip():
            return _err(409, "invalid_request_error",
                        f"key '{label}' has no secret", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        model = body.get("model") if isinstance(body, dict) else None
        if not isinstance(model, str) or not model.strip():
            return _err(400, "invalid_request_error", "'model' is required", request)
        model = model.strip()
        adapter = fresh_adapter(acct.provider_type)
        stream = bool(getattr(adapter, "force_stream", False))
        key_ref = ProviderKeyRef(label=key.label, secret=key.secret)
        params: dict[str, Any] = {"max_tokens": PROBE_MAX_TOKENS, "extra_body": {},
                                  "drop_params": True,
                                  "provider_type": acct.provider_type}
        try:
            req_body = adapter.encode_request(_probe_request(stream, model), model, params)
            url = build_url(adapter, acct.base_url, model, acct.provider_type,
                            stream, key_ref)
            headers = {**adapter.headers(key_ref), **acct.extra_headers}
        except Exception as exc:  # noqa: BLE001 — report, never 500
            return _err(400, "invalid_request_error",
                        f"could not build test request: {type(exc).__name__}", request)
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as hc:
                r = await hc.post(url, json=req_body, headers=headers)
        except httpx.HTTPError as exc:
            # Class name only: the message can quote a querystring credential.
            verdict, status, detail = "unreachable", None, type(exc).__name__
        else:
            status = r.status_code
            v = probe_verdict(status, r.content, allow_unframed=acct.provider_type in (
                "cline", "opencode"))
            verdict = v.value
            detail = ""
            if v is not ProbeVerdict.HEALTHY:
                detail = (r.text[:300] if status != 200
                          else "error envelope in 200 body")
        latency_ms = round((time.monotonic() - t0) * 1000)
        await state.logs.log_audit(actor=actor.username, action="provider_key.test",
                                   target=f"{name}/{label}",
                                   diff={"model": model, "verdict": verdict})
        return ORJSONResponse({"ok": verdict == "healthy", "verdict": verdict,
                               "status": status, "latency_ms": latency_ms,
                               "model": model, "detail": detail})

    @api.patch("/admin/providers/{name}/keys/{label}")
    async def admin_patch_provider_key(name: str, label: str, request: Request,
                                       actor: AdminUser):
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
            # Require a real bool: bool("false") is True, so a string here
            # silently persisted the opposite of the request (AUDIT #82). This
            # matches the backup-import validator for the same column.
            if not isinstance(body["enabled"], bool):
                return _err(400, "invalid_request_error",
                            "enabled must be a boolean", request)
            key.enabled = body["enabled"]
            diff["enabled"] = key.enabled
        if "weight" in body:
            try:
                weight = max(1, int(body["weight"]))
            except (TypeError, ValueError):
                return _err(400, "invalid_request_error", "weight must be an integer",
                            request)
            key.weight = weight
            diff["weight"] = weight
        if "secret" in body:
            # The repair path for a key that has no credential: a key-pool row
            # persisted by a secret-less backup import, or one whose secret was
            # never filled in. An empty value is refused — it is the state this
            # field exists to end, and an empty credential can only produce
            # ``Authorization: Bearer `` (which httpx rejects before connect).
            raw_secret = _interpolate(body["secret"])
            if not isinstance(raw_secret, str) or not raw_secret.strip():
                return _err(400, "invalid_request_error",
                            "secret must be a non-empty string", request)
            key.secret = raw_secret
            diff["secret_updated"] = True
            # A fresh credential is not the one that failed, so it must not
            # inherit the retirement the old secret earned. Same reasoning as
            # ``reset_status``; the operator can still disable it explicitly.
            key.recover(force=True)
            diff["reset_status"] = True
        if body.get("reset_status"):
            # The operator's explicit "this key is fine now". Clearing only
            # ``status``/``cooldown_until`` left ``err_count`` at its
            # retirement value, so ``on_result``'s
            # ``err_count >= key_max_consecutive_fails`` re-retired the key on
            # the very next non-200 — the reset silently reverted (AUDIT #199).
            # ``recover(force=True)`` is the same revival the healer uses: it
            # revives a terminal ``invalid`` too and clears the fail streak and
            # the WRR deficit, which is what "reset" has to mean here.
            key.recover(force=True)
            diff["reset_status"] = True
        if state.config_store:
            try:
                await state.config_store.update_key(
                    name, label,
                    weight=diff.get("weight"),
                    enabled=diff.get("enabled"),
                    # AUDIT #319: a YAML-defined key has no row, and the
                    # fallback insert needs the live credential so the DB
                    # override does not blank it at startup.
                    secret=key.secret)
            except ConfigStoreNotFound:
                import structlog
                structlog.get_logger().warning(
                    "provider_key_update_not_persisted", provider=name, label=label,
                    reason="no DB row and no credential to seed one with")
        await state.logs.log_audit(actor=actor.username, action="provider_key.update",
                                   target=f"{name}/{label}", diff=diff)
        return ORJSONResponse({"key": _key_view(key, time.monotonic(), time.time())})

    @api.post("/admin/providers/{name}/keys")
    async def admin_add_provider_key(name: str, request: Request, actor: AdminUser):
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        label = str(body.get("label") or "").strip()
        raw_secret = _interpolate(body.get("key"))
        # A non-string (dict/list/number) previously passed through `str()`
        # and was persisted as a literal like "{'nested': True}", so every
        # request upstream failed auth with a misleading 401 and the corrupt
        # value survived restarts (AUDIT #85). `base_url` already rejects this
        # shape; apply the same guard to the credential.
        if raw_secret is not None and not isinstance(raw_secret, str):
            return _err(400, "invalid_request_error",
                        "key must be a string", request)
        secret = str(raw_secret or "")
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
        await state.logs.log_audit(actor=actor.username, action="provider_key.create",
                                   target=f"{name}/{label}",
                                   diff={"weight": weight})
        return ORJSONResponse({"key": _key_view(acct.get_key(label),  # type: ignore[arg-type]
                                              time.monotonic(), time.time())})

    @api.delete("/admin/providers/{name}/keys/{label}")
    async def admin_delete_provider_key(name: str, label: str, request: Request,
                                        actor: AdminUser):
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        key = acct.get_key(label)
        if key is None:
            return _err(404, "not_found_error",
                        f"unknown key '{label}' on provider '{name}'", request)
        acct.keys = [k for k in acct.keys if k.label != label]
        if state.config_store:
            # A key that exists only in ``wiwi.yaml`` has no row to delete, so
            # the store writes a ``detached`` tombstone that startup applies
            # (AUDIT #319) instead of the delete coming back on the next start.
            await state.config_store.delete_key(name, label)
        # Same reclaim for Cline: the pool its OAuth record wrote into just
        # changed shape, so a dead Cline circuit must not survive the key it
        # belonged to. ``forget`` skips while a rotation holds the lock, so an
        # in-flight single-use refresh keeps its generation bookkeeping.
        if acct.provider_type == "cline":
            from wiwi.providers.cline_auto_refresh import (
                forget_provider as cline_forget_provider,
            )
            cline_forget_provider(state, name)
        await state.logs.log_audit(actor=actor.username, action="provider_key.delete",
                                   target=f"{name}/{label}")
        return ORJSONResponse({"deleted": True, "label": label})

    @api.post("/admin/providers")
    async def admin_add_provider(request: Request, actor: AdminUser):
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
        raw_secret = _interpolate(body.get("key"))
        # Same non-string guard as the key-add path (AUDIT #85).
        if raw_secret is not None and not isinstance(raw_secret, str):
            return _err(400, "invalid_request_error",
                        "key must be a string", request)
        secret = str(raw_secret or "")
        alias_raw = body.get("alias_id")
        alias_id = (str(alias_raw).strip() if alias_raw is not None else None) or None
        if alias_id is not None and any(c.isspace() for c in alias_id):
            return _err(400, "invalid_request_error",
                        "alias_id must not contain whitespace", request)
        if not name or not secret:
            return _err(400, "invalid_request_error", "name and key are required",
                        request)
        # AUDIT #373: the duplicate-name guard and the insert are one critical
        # section. They used to straddle an await with nothing reserving the
        # name, so two concurrent creates for one name both passed the guard —
        # the DB kept the first key (first-wins on the PK) while the router
        # kept the last, leaving memory and storage disagreeing.
        async with state.provider_create_lock:
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
            # An alias equal to another account's *name* would capture it in
            # resolve_group (the alias arm is reached for names the groups table
            # lacks, and a provider is routable under its own name), making that
            # account unreachable.  Same rule the config validator enforces.
            if (alias_id is not None and alias_id != name
                    and alias_id in state.router.providers):
                return _err(409, "invalid_request_error",
                            f"alias_id '{alias_id}' is the name of an existing"
                            " provider — pick a different alias", request)
            # Persist before exposing a routable account. A failed DB write must
            # not leave a ghost provider (and plaintext key) in memory (AUDIT
            # #364).
            if state.config_store:
                await state.config_store.add_provider(name, ptype, base_url,
                                                      alias_id=alias_id)
                try:
                    await state.config_store.add_key(name, label, secret)
                except Exception:
                    # Do not leave a keyless provider row behind either.
                    with contextlib.suppress(Exception):
                        await state.config_store.delete_provider(name)
                    raise
            state.router.providers[name] = ProviderAccount(
                name=name, provider_type=ptype, base_url=base_url,
                keys=[ProviderKey(label=label, secret=secret)],
                alias_id=alias_id)
            if alias_id is not None:
                state.router.alias_to_provider[alias_id] = name
        # If a Cline provider was just added and global default models are
        # persisted, auto-deploy them for the new account so it joins the
        # cross-account WRR pool immediately (no manual setup required).
        if ptype == "cline":
            defaults = await state.config_store.get_setting(
                "cline_settings:default_models") if state.config_store else None
            if defaults:
                await _apply_cline_default_models(state, defaults)
        await state.logs.log_audit(actor=actor.username, action="provider.create",
                                   target=name,
                                   diff={"provider_type": ptype, "base_url": base_url,
                                         "alias_id": alias_id})
        return ORJSONResponse({"name": name, "provider_type": ptype,
                               "base_url": base_url, "alias_id": alias_id})

    @api.delete("/admin/providers/{name}")
    async def admin_delete_provider(name: str, request: Request, actor: AdminUser):
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        # Cascade: every model group this account feeds loses it. The delete
        # used to refuse with 409 and make the operator remove the
        # deployments one at a time from the provider-detail UI first, which
        # was both tedious and easy to get stuck in — a YAML ``model_list``
        # deployment has no DB row, so the only way to detach it was the
        # Combos page, and nothing in the 409 message said so.
        #
        # ``ConfigStore.delete_provider`` already DELETEs this account's
        # ``deployments`` rows, so the only thing that could resurrect a
        # YAML-sourced deployment is the *absence* of a tombstone for it.
        # Re-writing each removed triple through ``delete_deployment``
        # (``detached=1``) is what keeps them gone across a restart
        # (AUDIT #297) — do it after the cascade delete so the tombstones
        # are not deleted along with the rest.
        detached: list[tuple[str, str]] = [
            (gname, d.model_id)
            for gname, deps in state.router.groups.items()
            for d in deps
            if d.provider.name == name
        ]
        # Persist FIRST, then mutate in-memory routing — the same order
        # ``POST /admin/providers`` and ``DELETE /admin/keys/...`` already use.
        # This path used to delete from ``router.providers`` (and the alias and
        # price-scope maps) *before* the DB write, so a failed write answered
        # 500 while having already taken effect in memory: the operator
        # retried, the DB row and its plaintext key survived, and the provider
        # came back on the next restart. ``log_audit`` was never reached
        # either, so the mutation left no trace (AUDIT #181).
        if state.config_store:
            await state.config_store.delete_provider(name)
            await state.config_store.delete_setting(
                cline_oauth_router.cline_oauth_setting_key(name))
            # ``delete_provider`` matches zero rows for a YAML-defined
            # account (it has no DB row), so nothing above records the
            # decision — and on the next restart the YAML loader rebuilds
            # the account, plaintext key included, and (for Cline) the
            # lifespan re-applies the persisted default models, making the
            # "deleted" account routable again. Write the same ``detached``
            # tombstone the rename path writes for the old name (see the
            # PATCH handler's ``ConfigStoreNotFound`` fallback): startup's
            # ``_load_db_config`` consumes it and drops the YAML copy
            # (AUDIT #319's third arm — rename and key-delete got the
            # tombstone, account-delete did not).
            await state.config_store.add_provider(
                name, acct.provider_type, acct.base_url,
                timeout_s=acct.timeout_s,
                extra_headers=acct.extra_headers,
                round_robin=acct.round_robin,
                alias_id=acct.alias_id, detached=True)
        # Deleting the account deletes the credential, so the Cline refresh
        # state must go with it: the OAuth record is already being dropped, and
        # ``CircuitBreaker._dead`` survives forever (nothing else popped it), so
        # re-creating a Cline provider under the same name inherited the corpse
        # and the sweeper reported "blocked by the refresh circuit breaker"
        # permanently. Guarded on the provider type so a non-Cline delete does
        # not manufacture a worker for it.
        if acct.provider_type == "cline":
            from wiwi.providers.cline_auto_refresh import (
                forget_provider as cline_forget_provider,
            )
            cline_forget_provider(state, name)
        # drop any alias_id entry that points at this provider
        for k, v in list(state.router.alias_to_provider.items()):
            if v == name:
                del state.router.alias_to_provider[k]
        # Drop the provider's price scopes from the in-memory cost map as well
        # as the DB (ConfigStore.delete_provider handles the latter). The cost
        # engine resolves a scope by name, so a stale entry would keep billing
        # at a deleted account's rate — and would be silently adopted by any
        # provider later created under the freed name (AUDIT #149).
        for _entry in state.cost.prices.values():
            (_entry.get("providers") or {}).pop(name, None)
        del state.router.providers[name]
        # Detach the cascaded deployments in memory. A group left with no
        # deployments is popped outright, matching ``admin_delete_deployment``
        # — an empty group would linger as a routable-but-failing target.
        for gname, model_id in detached:
            deps = state.router.groups.get(gname)
            if deps is None:
                continue
            remaining = [d for d in deps
                         if not (d.provider.name == name and d.model_id == model_id)]
            if remaining:
                state.router.groups[gname] = remaining
            else:
                state.router.groups.pop(gname, None)
        state.router.rebuild_cross_provider_pools()
        # Tombstone the cascaded YAML-sourced deployments so they do not
        # reappear on the next restart (see the note above). Order matters:
        # ``delete_provider`` has already removed this account's rows, and
        # each call here re-inserts one detached row.
        if state.config_store:
            for gname, model_id in detached:
                await state.config_store.delete_deployment(gname, name, model_id)
        await state.logs.log_audit(
            actor=actor.username, action="provider.delete", target=name,
            diff={"detached_deployments": [
                {"group": g, "model_id": m} for g, m in detached]})
        return ORJSONResponse({"deleted": True, "name": name,
                               "detached_deployments": [
                                   {"group": g, "model_id": m} for g, m in detached]})

    @api.patch("/admin/providers/{name}")
    async def admin_patch_provider(name: str, request: Request, actor: AdminUser):
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        diff: dict[str, Any] = {}
        new_name: str | None = None
        # Validation only — nothing below mutates ``acct`` or the router maps
        # until the DB write has succeeded (AUDIT #181). The PATCH path used to
        # apply the rename (and the provider_type/base_url/round_robin edits)
        # in memory first and persist afterwards, so a failed write answered
        # 500 while the running process already routed and billed under a name
        # the DB had never heard of, and the audit row was never written. A
        # restart silently reverted it.
        new_ptype: str | None = None
        new_base_url: str | None = None
        new_round_robin: bool | None = None
        new_enabled: bool | None = None
        if "name" in body:
            new_name = str(body["name"]).strip()
            if not new_name:
                return _err(400, "invalid_request_error", "name must be non-empty",
                            request)
            if new_name != name and new_name in state.router.providers:
                return _err(409, "invalid_request_error",
                            f"provider '{new_name}' already exists", request)
            if new_name != name and new_name in state.router.alias_to_provider.values():
                # alias_to_provider is keyed by provider name, so renaming onto
                # a name another provider's alias already resolves to would
                # leave the two disagreeing about who owns it — resolve_group()
                # consults the alias map first.
                return _err(409, "invalid_request_error",
                            f"name '{new_name}' is already an alias of another"
                            f" provider", request)
            diff["name"] = new_name
        if "provider_type" in body:
            new_ptype = str(body["provider_type"])
            if new_ptype not in PROVIDER_TYPES:
                return _err(400, "invalid_request_error",
                            f"unsupported provider type '{new_ptype}'", request)
            diff["provider_type"] = new_ptype
        if "base_url" in body:
            raw_url = _interpolate(body["base_url"])
            if not isinstance(raw_url, str):
                return _err(400, "invalid_request_error",
                            "base_url must be a string", request)
            new_base_url = raw_url.strip()
            if not new_base_url:
                return _err(400, "invalid_request_error",
                            "base_url must be non-empty", request)
            diff["base_url"] = new_base_url
        if "round_robin" in body:
            # Same real-bool requirement as the key path (AUDIT #82):
            # bool("false") is True and would store the opposite.
            if not isinstance(body["round_robin"], bool):
                return _err(400, "invalid_request_error",
                            "round_robin must be a boolean", request)
            new_round_robin = body["round_robin"]
            diff["round_robin"] = new_round_robin
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                return _err(400, "invalid_request_error",
                            "enabled must be a boolean", request)
            new_enabled = body["enabled"]
            diff["enabled"] = new_enabled
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
        # Persist first, mutate second (AUDIT #181): a failed DB write must
        # leave the running gateway's routing state exactly as it was, so the
        # operator's retry is idempotent instead of racing a half-applied
        # rename. This matches ``POST /admin/providers`` and
        # ``DELETE /admin/keys/...``.
        if state.config_store:
            update_kwargs: dict[str, Any] = {
                "provider_type": new_ptype,
                "base_url": new_base_url,
                "round_robin": new_round_robin,
                "enabled": new_enabled,
                "new_name": new_name,
            }
            if alias_change is not None:
                update_kwargs["alias_id"] = alias_change[0]
                update_kwargs["alias_id_set"] = True
            try:
                await state.config_store.update_provider(name, **update_kwargs)
            except ConfigStoreNotFound:
                # AUDIT #319: the account is YAML-defined, so there was no row
                # to update. Write the operator's full desired state as an
                # override row — startup layers DB rows over YAML, so the edit
                # survives the restart — and tombstone the old name when the
                # edit is a rename, or the YAML copy would return alongside it.
                final_name = new_name or name
                await state.config_store.add_provider(
                    final_name,
                    new_ptype or acct.provider_type,
                    new_base_url or acct.base_url,
                    timeout_s=acct.timeout_s,
                    extra_headers=acct.extra_headers,
                    round_robin=(new_round_robin if new_round_robin is not None
                                 else acct.round_robin),
                    enabled=(new_enabled if new_enabled is not None
                             else acct.enabled),
                    alias_id=(alias_change[0] if alias_change is not None
                              else acct.alias_id))
                if final_name != name:
                    await state.config_store.add_provider(
                        name, acct.provider_type, acct.base_url,
                        timeout_s=acct.timeout_s,
                        extra_headers=acct.extra_headers,
                        round_robin=acct.round_robin,
                        enabled=acct.enabled,
                        alias_id=acct.alias_id, detached=True)
        if new_ptype is not None:
            acct.provider_type = new_ptype
        if new_base_url is not None:
            acct.base_url = new_base_url
        if new_round_robin is not None:
            acct.round_robin = new_round_robin
        if new_enabled is not None:
            acct.enabled = new_enabled
        # apply rename last so identity-based deployment refs stay valid
        if new_name is not None and new_name != name:
            acct.name = new_name
            state.router.providers[new_name] = acct
            del state.router.providers[name]
            target = f"{name}→{new_name}"
            # The alias map is keyed by provider *name*, so a rename must
            # rewrite every entry that still points at the old one. Gating
            # this on alias_change left the alias resolving to nothing, and a
            # later provider reusing the freed name silently inherited it.
            for k, v in list(state.router.alias_to_provider.items()):
                if v == name:
                    state.router.alias_to_provider[k] = new_name
            # Same reason, one layer down: a price scope is keyed by provider
            # name too. ConfigStore rewrites the persisted rows, but the cost
            # engine reads its own in-memory map, so without this the running
            # server keeps billing at the renamed account's scoped rate (and
            # /admin/pricing keeps echoing the stale scope) until a restart
            # (AUDIT #149). Assigning over an existing key is the same
            # collision rule ConfigStore applies: the renamed (live) account's
            # rates win.
            for _entry in state.cost.prices.values():
                _scopes = _entry.get("providers") or {}
                if name in _scopes:
                    _scopes[new_name] = _scopes.pop(name)
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
        await state.logs.log_audit(actor=actor.username, action="provider.update",
                                   target=target, diff=diff)
        mono, wall = time.monotonic(), time.time()
        return ORJSONResponse({
            "name": acct.name,
            "provider_type": acct.provider_type,
            "base_url": acct.base_url,
            "round_robin": acct.round_robin,
            "enabled": acct.enabled,
            "alias_id": acct.alias_id,
            "healthy": acct.healthy,
            "keys": [_key_view(k, mono, wall) for k in acct.keys],
        })

    @api.get("/admin/providers/export")
    async def admin_export_providers(request: Request, actor: AdminUser):
        """Export a full provider backup: accounts, keys, and deployments.

        Every account is emitted with its plaintext key secrets plus the
        deployments that reference it, so the file restores full routing
        on import. Guarded by the master key like key reveals, and
        audit-logged. Optionally scoped with ?provider=name.
        """
        provider_filter = request.query_params.get("provider", "").strip()
        dep_index: dict[str, list[dict]] = {}
        for gname, deps in state.router.groups.items():
            for d in deps:
                dep_index.setdefault(d.provider.name, []).append(
                    {"group_name": gname, "model_id": d.model_id,
                     "weight": d.weight})
        for v in dep_index.values():
            v.sort(key=lambda e: (e["group_name"], e["model_id"]))
        out = []
        for name in sorted(state.router.providers):
            if provider_filter and name != provider_filter:
                continue
            acct = state.router.providers[name]
            out.append({
                "name": acct.name,
                "provider_type": acct.provider_type,
                "base_url": acct.base_url,
                "timeout_s": acct.timeout_s,
                "extra_headers": dict(acct.extra_headers),
                "round_robin": acct.round_robin,
                "enabled": acct.enabled,
                "alias_id": acct.alias_id,
                "keys": [{"label": k.label, "secret": k.secret,
                          "weight": k.weight, "enabled": k.enabled}
                         for k in acct.keys],
                "deployments": dep_index.get(name, []),
            })
        await state.logs.log_audit(actor=actor.username, action="provider.export",
                                   target=provider_filter,
                                   diff={"providers": len(out)})
        if provider_filter and not out:
            return _err(404, "not_found_error",
                        f"unknown provider '{provider_filter}'", request)
        return ORJSONResponse({"version": 1, "exported_at": time.time(),
                               "providers": out})

    @api.post("/admin/providers/import")
    async def admin_import_providers(request: Request, actor: AdminUser):
        """Import a provider backup produced by GET /admin/providers/export.

        Upsert semantics: existing accounts/keys/deployments are updated in
        place, missing ones are created. The whole batch is validated first
        — any validation error rejects the import with nothing applied.
        """
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        entries = body.get("providers")
        if not isinstance(entries, list) or not entries:
            return _err(400, "invalid_request_error",
                        "'providers' must be a non-empty list", request)
        # -- validate everything before touching live state ------------------
        parsed: list[dict] = []
        seen_names: set[str] = set()
        for i, raw in enumerate(entries):
            if not isinstance(raw, dict):
                return _err(400, "invalid_request_error",
                            f"providers[{i}] is not a JSON object", request)
            name = str(raw.get("name") or "").strip()
            if not name:
                return _err(400, "invalid_request_error",
                            f"providers[{i}].name is required", request)
            if name in seen_names:
                return _err(400, "invalid_request_error",
                            f"duplicate provider '{name}' in import", request)
            seen_names.add(name)
            ptype = str(raw.get("provider_type") or "").strip()
            if ptype not in PROVIDER_TYPES:
                return _err(400, "invalid_request_error",
                            f"providers[{i}]: unsupported provider type "
                            f"'{ptype}'", request)
            if "base_url" in raw or name not in state.router.providers:
                raw_url = _interpolate(raw.get("base_url"))
                if raw_url is not None and not isinstance(raw_url, str):
                    return _err(400, "invalid_request_error",
                                "'base_url' must be a string", request)
                base_url = ((raw_url or "").strip()
                            or _default_base_url(ptype))
                if not base_url:
                    return _err(400, "invalid_request_error",
                                f"base_url is required for provider type "
                                f"'{ptype}'", request)
            else:
                base_url = None
            timeout_s = None
            if "timeout_s" in raw and raw["timeout_s"] is not None:
                try:
                    timeout_s = float(raw["timeout_s"])
                except (TypeError, ValueError):
                    return _err(400, "invalid_request_error",
                                "timeout_s must be a number", request)
                if timeout_s <= 0:
                    return _err(400, "invalid_request_error",
                                "timeout_s must be positive", request)
            extra_headers = None
            if "extra_headers" in raw and raw["extra_headers"] is not None:
                eh = raw["extra_headers"]
                if not isinstance(eh, dict) or not all(
                        isinstance(k, str) and isinstance(v, str)
                        for k, v in eh.items()):
                    return _err(400, "invalid_request_error",
                                "extra_headers must map strings to strings",
                                request)
                extra_headers = dict(eh)
            round_robin = None
            if "round_robin" in raw and raw["round_robin"] is not None:
                if not isinstance(raw["round_robin"], bool):
                    return _err(400, "invalid_request_error",
                                "round_robin must be a boolean", request)
                round_robin = raw["round_robin"]
            enabled = None
            if "enabled" in raw and raw["enabled"] is not None:
                if not isinstance(raw["enabled"], bool):
                    return _err(400, "invalid_request_error",
                                "enabled must be a boolean", request)
                enabled = raw["enabled"]
            alias_id_set = "alias_id" in raw
            alias_id = None
            if alias_id_set:
                alias_raw = raw.get("alias_id")
                if alias_raw is not None and str(alias_raw).strip():
                    alias_id = str(alias_raw).strip()
                    if any(c.isspace() for c in alias_id):
                        return _err(400, "invalid_request_error",
                                    "alias_id must not contain whitespace",
                                    request)
            raw_keys = raw.get("keys")
            if not isinstance(raw_keys, list) or not raw_keys:
                return _err(400, "invalid_request_error",
                            f"providers[{i}].keys must be a non-empty list",
                            request)
            keys = []
            seen_labels: set[str] = set()
            for j, rk in enumerate(raw_keys):
                if not isinstance(rk, dict):
                    return _err(400, "invalid_request_error",
                                f"providers[{i}].keys[{j}] is not an object",
                                request)
                label = str(rk.get("label") or "").strip()
                if not label:
                    return _err(400, "invalid_request_error",
                                f"providers[{i}].keys[{j}].label is required",
                                request)
                if label in seen_labels:
                    return _err(400, "invalid_request_error",
                                f"duplicate key label '{label}' on provider "
                                f"'{name}'", request)
                seen_labels.add(label)
                raw_secret = _interpolate(rk.get("secret", rk.get("key")))
                if raw_secret is not None and not isinstance(raw_secret, str):
                    return _err(400, "invalid_request_error",
                                f"providers[{i}].keys[{j}]: secret must be a"
                                " string", request)
                secret = str(raw_secret or "")
                if not secret:
                    return _err(400, "invalid_request_error",
                                f"providers[{i}].keys[{j}]: secret is "
                                "required", request)
                try:
                    weight = max(1, int(rk.get("weight") or 1))
                except (TypeError, ValueError):
                    return _err(400, "invalid_request_error",
                                "weight must be an integer", request)
                if "enabled" in rk and not isinstance(rk["enabled"], bool):
                    return _err(400, "invalid_request_error",
                                "enabled must be a boolean", request)
                keys.append({"label": label, "secret": secret,
                             "weight": weight,
                             "enabled": bool(rk.get("enabled", True))})
            raw_deps = raw.get("deployments", [])
            if raw_deps is None:
                raw_deps = []
            if not isinstance(raw_deps, list):
                return _err(400, "invalid_request_error",
                            f"providers[{i}].deployments must be a list",
                            request)
            deps = []
            seen_deps: set[tuple[str, str]] = set()
            for j, rd in enumerate(raw_deps):
                if not isinstance(rd, dict):
                    return _err(400, "invalid_request_error",
                                f"providers[{i}].deployments[{j}] is not an "
                                "object", request)
                gname = str(rd.get("group_name") or rd.get("group")
                            or "").strip()
                model_id = str(rd.get("model_id") or "").strip()
                if not gname or not model_id:
                    return _err(400, "invalid_request_error",
                                f"providers[{i}].deployments[{j}]: group_name "
                                "and model_id are required", request)
                if any(ch.isspace() for ch in model_id):
                    return _err(400, "invalid_request_error",
                                "model_id must not contain whitespace",
                                request)
                try:
                    dweight = max(1, int(rd.get("weight") or 1))
                except (TypeError, ValueError):
                    return _err(400, "invalid_request_error",
                                "weight must be an integer", request)
                if (gname, model_id) in seen_deps:
                    return _err(400, "invalid_request_error",
                                f"duplicate deployment {gname}/{model_id} "
                                f"on provider '{name}'", request)
                seen_deps.add((gname, model_id))
                deps.append({"group_name": gname, "model_id": model_id,
                             "weight": dweight})
            if alias_id_set:
                eff_alias = alias_id
            else:
                cur = state.router.providers.get(name)
                eff_alias = cur.alias_id if cur is not None else None
            parsed.append({"name": name, "provider_type": ptype,
                           "base_url": base_url, "timeout_s": timeout_s,
                           "extra_headers": extra_headers,
                           "round_robin": round_robin,
                           "enabled": enabled,
                           "alias_id": alias_id, "alias_id_set": alias_id_set,
                           "eff_alias": eff_alias, "keys": keys, "deps": deps})
        # Alias claims are checked against the map as it will look after the
        # import: mappings owned by imported providers are dropped first so
        # aliases can move between imported accounts.
        future_aliases = {a: n for a, n in
                          state.router.alias_to_provider.items()
                          if n not in seen_names}
        seen_alias: dict[str, str] = {}
        for p in parsed:
            a = p["eff_alias"]
            if a is None:
                continue
            if a in seen_alias:
                return _err(400, "invalid_request_error",
                            f"alias_id '{a}' is used by both provider "
                            f"'{seen_alias[a]}' and '{p['name']}'", request)
            seen_alias[a] = p["name"]
            owner = future_aliases.get(a)
            if owner is not None:
                return _err(409, "invalid_request_error",
                            f"alias_id '{a}' already used by provider "
                            f"'{owner}'", request)
        # -- apply (validation already passed) --------------------------------
        n_keys = 0
        n_deps = 0
        for p in parsed:
            name = p["name"]
            acct = state.router.providers.get(name)
            if acct is None:
                acct = ProviderAccount(
                    name=name, provider_type=p["provider_type"],
                    base_url=p["base_url"]
                    or _default_base_url(p["provider_type"]),
                    timeout_s=p["timeout_s"] or 120.0,
                    extra_headers=dict(p["extra_headers"] or {}),
                    round_robin=(p["round_robin"]
                                 if p["round_robin"] is not None else True),
                    enabled=(p["enabled"] if p["enabled"] is not None else True),
                    alias_id=p["alias_id"] if p["alias_id_set"] else None)
                state.router.providers[name] = acct
                if acct.alias_id is not None:
                    state.router.alias_to_provider[acct.alias_id] = name
                if state.config_store:
                    await state.config_store.add_provider(
                        name, p["provider_type"], acct.base_url,
                        timeout_s=acct.timeout_s,
                        extra_headers=dict(acct.extra_headers),
                        round_robin=acct.round_robin,
                        enabled=acct.enabled,
                        alias_id=acct.alias_id)
            else:
                acct.provider_type = p["provider_type"]
                if p["base_url"] is not None:
                    acct.base_url = p["base_url"]
                if p["timeout_s"] is not None:
                    acct.timeout_s = p["timeout_s"]
                if p["extra_headers"] is not None:
                    acct.extra_headers = dict(p["extra_headers"])
                if p["round_robin"] is not None:
                    acct.round_robin = p["round_robin"]
                if p["enabled"] is not None:
                    acct.enabled = p["enabled"]
                if p["alias_id_set"]:
                    for k, v in list(state.router.alias_to_provider.items()):
                        if v == acct.name:
                            del state.router.alias_to_provider[k]
                    if p["alias_id"] is not None:
                        state.router.alias_to_provider[p["alias_id"]] = (
                            acct.name)
                    acct.alias_id = p["alias_id"]
                if state.config_store:
                    try:
                        await state.config_store.update_provider(
                            name, provider_type=p["provider_type"],
                            base_url=p["base_url"], timeout_s=p["timeout_s"],
                            extra_headers=p["extra_headers"],
                            round_robin=p["round_robin"],
                            enabled=p["enabled"],
                            alias_id=(p["alias_id"] if p["alias_id_set"]
                                      else None),
                            alias_id_set=p["alias_id_set"])
                    except ConfigStoreNotFound:
                        # The import upserted a provider the DB has no row for
                        # (a YAML-defined account). In-memory state is already
                        # correct; the import simply cannot be persisted for
                        # it (AUDIT #319).
                        import structlog
                        structlog.get_logger().warning(
                            "provider_import_not_persisted", provider=name,
                            reason="provider is YAML-defined; import is "
                                   "in-memory only for this account")
            for k in p["keys"]:
                n_keys += 1
                ek = acct.get_key(k["label"])
                if ek is None:
                    acct.keys.append(ProviderKey(
                        label=k["label"], secret=k["secret"],
                        weight=k["weight"], enabled=k["enabled"]))
                else:
                    ek.secret = k["secret"]
                    ek.weight = k["weight"]
                    ek.enabled = k["enabled"]
                if state.config_store:
                    await state.config_store.add_key(
                        name, k["label"], k["secret"], k["weight"],
                        k["enabled"])
            for d in p["deps"]:
                n_deps += 1
                bucket = state.router.groups.setdefault(d["group_name"], [])
                match = next((x for x in bucket
                              if x.provider.name == name
                              and x.model_id == d["model_id"]), None)
                if match is None:
                    bucket.append(Deployment(
                        group=d["group_name"], provider=acct,
                        model_id=d["model_id"], weight=d["weight"]))
                else:
                    match.weight = d["weight"]
                if state.config_store:
                    await state.config_store.add_deployment(
                        d["group_name"], name, d["model_id"], d["weight"])
        state.router.rebuild_cross_provider_pools()
        await state.logs.log_audit(actor=actor.username, action="provider.import",
                                   target="",
                                   diff={"providers": len(parsed),
                                         "keys": n_keys,
                                         "deployments": n_deps})
        return ORJSONResponse({"imported_providers": len(parsed),
                               "imported_keys": n_keys,
                               "imported_deployments": n_deps,
                               "providers": [p["name"] for p in parsed]})

    @api.get("/admin/providers/{name}/models")
    async def admin_provider_models(name: str, request: Request, actor: AdminUser):
        """Fetch model ids live from the upstream provider (first available key)."""
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        key = next((k for k in acct.keys if k.available), None)
        if key is None:
            blank = sum(1 for k in acct.keys if not (k.secret or "").strip())
            off = sum(1 for k in acct.keys
                      if not k.enabled and (k.secret or "").strip())
            parts = []
            if blank:
                parts.append(f"{blank} without a secret (PATCH .../keys/<label>"
                             f" {{\"secret\": ...}} to fill one in)")
            if off:
                parts.append(f"{off} disabled")
            detail = f" ({'; '.join(parts)})" if parts else ""
            return _err(409, "invalid_request_error",
                        f"no available key on provider '{name}' to fetch models"
                        f"{detail}", request)
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
        except httpx.HTTPError as exc:
            # Class name only: a DNS/TLS/connect failure and a request httpx
            # refuses to send (e.g. an illegal header) are only distinguishable
            # by the exception type, and dropping it made every one of them
            # read "could not reach". The message is omitted because it can
            # quote the credential.
            return _err(502, "api_connection_error",
                        f"could not reach '{name}' ({safe_url}): "
                        f"{type(exc).__name__}", request)
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

    return api
