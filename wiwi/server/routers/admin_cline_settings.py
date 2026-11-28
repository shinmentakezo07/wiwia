"""Cline global model catalog & default-model settings (admin).

Extracted verbatim from ``server/app.py``: same paths, same status codes,
same error bodies, same audit trail. The handlers close over per-app
objects handed in by ``create_app`` (``state``, ``json_body`` and
``_err``), so the router is built once per app — auth is the module-level
``Depends`` guard (``AdminUser`` from ``wiwi/server/dependencies``).
The three module-level helpers of ``server/app.py`` this cluster uses
(``_apply_cline_default_models``, ``_provider_models_url`` and
``_parse_models_response``) are passed in as keyword arguments — importing
them from ``server/app`` would cycle.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request

from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.registry import fresh_adapter
from wiwi.server.dependencies import AdminUser, ORJSONResponse


def make_router(state, *, json_body, _err, _apply_cline_default_models,
                _provider_models_url, _parse_models_response):
    """Build the /admin/cline catalog & settings router bound to the helpers."""
    api = APIRouter(prefix="/admin/cline", tags=["admin-cline-settings"])

    @api.get("/models")
    async def admin_cline_models(request: Request, actor: AdminUser):
        """Fetch model ids from any available Cline provider (global fetch).

        All Cline OAuth accounts share the same upstream model catalog
        (api.cline.bot/api/v1/models).  Instead of fetching per-account,
        this endpoint queries the first Cline provider with an available
        key, caches the result for 5 minutes, and returns it for reuse
        across every Cline account.
        """
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
        except httpx.HTTPError as exc:
            return _err(502, "api_connection_error",
                        f"could not reach Cline ({url}): {type(exc).__name__}",
                        request)
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

    @api.get("/settings")
    async def admin_get_cline_settings(actor: AdminUser):
        """Read the persisted Cline default-model list."""
        cs = state.config_store
        if cs is None:
            return ORJSONResponse({"default_models": []})
        ids = await cs.get_setting("cline_settings:default_models") or []
        if not isinstance(ids, list):
            ids = []
        return ORJSONResponse({"default_models": [str(x) for x in ids]})

    @api.put("/settings")
    async def admin_put_cline_settings(request: Request, actor: AdminUser):
        """Replace the persisted Cline default-model list and reconcile.

        Body: ``{"default_models": ["z-ai/glm-5.2", "claude-sonnet-5"]}``.
        Each id is validated (non-empty, max 32 entries) and a Deployment
        is created for every Cline account in router.providers under the
        group ``cline:<model_id>``.  Idempotent — re-PUTting the same
        list is a no-op for existing deployments.
        """
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
        await state.logs.log_audit(actor=actor.username, action="cline_settings.apply",
                                   target="default_models",
                                   diff={"applied": result["applied"],
                                         "skipped": result["skipped"],
                                         "models": cleaned})
        return ORJSONResponse({"default_models": cleaned,
                               "applied": result["applied"],
                               "skipped": result["skipped"]})

    @api.delete("/settings/default-models/{model_id:path}")
    async def admin_delete_cline_default_model(model_id: str, request: Request, actor: AdminUser):
        """Remove one model id from the persisted list and drop the Cline
        deployments under the ``cline:<model_id>`` group.

        Other provider types in the same group are left alone: the group is a
        plain model group and may carry an operator's own deployments.
        """
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
            # ``cline:<model>`` is an ordinary model group, so a deployment an
            # operator attached to it by hand (any provider type) is not ours
            # to delete. The old code popped the whole group regardless of
            # provider type; filter to the Cline deployments this endpoint
            # auto-deployed and leave the rest routable.
            deps = state.router.groups[group_name]
            cline_deps = [d for d in deps if d.provider.provider_type == "cline"]
            keep = [d for d in deps if d.provider.provider_type != "cline"]
            if cline_deps:
                if keep:
                    state.router.groups[group_name] = keep
                else:
                    state.router.groups.pop(group_name, None)
            removed = len(cline_deps)
            if state.config_store is not None:
                for d in cline_deps:
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
        await state.logs.log_audit(actor=actor.username,
                                   action="cline_settings.default_models.delete",
                                   target=mid,
                                   diff={"removed_deployments": removed})
        return ORJSONResponse({"deleted": mid, "removed_deployments": removed})

    return api
