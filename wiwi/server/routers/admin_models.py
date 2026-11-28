"""Admin surface for model groups, routing strategy and model_group_alias.

Extracted verbatim from ``server/app.py``: same paths, same status codes,
same error bodies, same audit trail. The handlers close over per-app
objects handed in by ``create_app`` (``state``, ``json_body`` and
``_err``), so the router is built once per app — auth is the module-level
``Depends`` guards (``CurrentUser`` / ``AdminUser`` from
``wiwi/server/dependencies``). ``_validate_alias_batch`` is a
module-level helper of ``server/app.py`` passed in as a keyword argument
— importing it from ``server/app`` would cycle. ``_admin_group`` and
``_alias_name_conflict`` close over ``state`` and therefore live inside
the factory.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from wiwi.config import alias_value_eq, alias_value_to_json
from wiwi.router.router import Deployment
from wiwi.server.dependencies import AdminUser, CurrentUser, ORJSONResponse


def _dep_view(dep: Deployment, mono: float) -> dict[str, Any]:
    """Deployment fields shared by the add-deployment response and the models
    listing. ``probation`` is reported even though the router skips probation
    deployments while a fresh sibling is available — the UI needs it to show
    the real traffic split (AUDIT #372)."""
    return {
        "provider": dep.provider.name,
        "model_id": dep.model_id,
        "weight": dep.weight,
        "available": dep.available,
        "probation": dep.probation,
        "inflight": dep.inflight,
        "p95_latency_ms": round(dep.p95_latency(), 1),
        "cooldown_remaining_s": round(max(0.0, dep.cooldown_until - mono), 1),
    }


def make_router(state, *, json_body, _err, _validate_alias_batch):
    """Build the model-group / alias admin router bound to the helpers."""
    api = APIRouter(prefix="/admin", tags=["admin-models"])

    # -- admin: model groups & routing -------------------------------------------
    def _admin_group(name: str) -> tuple[str | None, list[Deployment] | str]:
        """Resolve an admin-supplied group name to ``(real_group, deps)``.

        Follows ``model_group_alias`` (the documented, tested behaviour: a
        PATCH/DELETE by alias name targets the alias *target* group), but
        never the provider-``alias_id`` arm.  ``Router.resolve_group`` returns
        that arm's answer as ``(requested, deps)`` — the alias name paired
        with deployments drawn from unrelated groups — so using it here meant
        ``DELETE /admin/model-groups/shared/…`` (with ``alias_id: shared`` on
        p1) re-keyed p1's deployment under a phantom group ``shared`` while
        leaving the real owner untouched (AUDIT #294).

        Returns ``(group, deps)`` or ``(None, <error message>)``.
        """
        deps = state.router.groups.get(name)
        if deps:
            return name, deps
        conflict = _alias_name_conflict(name)
        if conflict is not None:
            return None, conflict
        gname, adeps = state.router.resolve_group(name)
        if gname and adeps:
            return gname, adeps
        return None, f"unknown model group '{name}'"

    def _alias_name_conflict(name: str) -> str | None:
        """Explain why ``name`` is an alias rather than a model group, or None.

        Used by the admin model-group routes so an operator who typed an
        alias into a group-name field gets told which kind of name they used
        instead of a bare "unknown model group".
        """
        prov = state.router.alias_to_provider.get(name)
        if prov is not None:
            return (f"'{name}' is a provider alias_id (of provider '{prov}'),"
                    " not a model group — pick a different group name")
        return None

    @api.post("/model-groups/{name:path}/deployments")
    async def admin_add_deployment(name: str, request: Request, actor: AdminUser):
        """Attach a provider deployment to a model group (creating the group)."""
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        gname = str(body.get("group") or name).strip()
        pname = str(body.get("provider") or "").strip()
        model_id = str(body.get("model_id") or "").strip()
        if not gname or not pname or not model_id:
            return _err(400, "invalid_request_error",
                        "group, provider and model_id are required", request)
        # A name that only resolves through a provider alias_id is not a
        # group: the deployments it returns belong to unrelated groups, so
        # appending here would attach to the wrong group while the response
        # claimed ``gname`` (AUDIT #294).
        if state.router.groups.get(gname) is None:
            conflict = _alias_name_conflict(gname)
            if conflict is not None:
                return _err(400, "invalid_request_error", conflict, request)
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
        await state.logs.log_audit(actor=actor.username, action="deployment.create",
                                   target=f"{gname}/{pname}/{model_id}",
                                   diff={"weight": weight})
        mono = time.monotonic()
        return ORJSONResponse({"deployment": _dep_view(dep, mono)},
                              status_code=201)

    @api.delete("/model-groups/{name:path}/deployments")
    async def admin_delete_deployment(name: str, request: Request, actor: AdminUser):
        """Detach one deployment from a model group.

        The target is identified by query params (``?provider=&model_id=``)
        rather than path segments: model ids routinely contain slashes
        (e.g. ``z-ai/glm-5.2``), which a greedy ``{name:path}`` prefix would
        otherwise swallow into the group name.
        """
        gname, deps = _admin_group(name)
        if gname is None:
            return _err(404, "not_found_error", str(deps), request)
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
        await state.logs.log_audit(actor=actor.username, action="deployment.delete",
                                   target=f"{gname}/{pname}/{model_id}",
                                   diff={"group_emptied": not remaining})
        return ORJSONResponse({"deleted": True, "group": gname,
                               "provider": pname, "model_id": model_id,
                               "group_emptied": not remaining})

    # -- admin: models & routing -------------------------------------------------
    @api.get("/models")
    async def admin_models(actor: CurrentUser):
        # Read for any authenticated actor (admin or user); guard via the
        # actor-based resolver so logged-in users can see the model list
        # without holding the master key.
        mono = time.monotonic()
        groups = []
        for gname in sorted(state.router.groups):
            deps = state.router.groups[gname]
            groups.append({
                "name": gname,
                "deployments": [_dep_view(d, mono) for d in deps],
            })
        settings = state.router.settings
        return ORJSONResponse({"groups": groups,
                             "aliases": {k: alias_value_to_json(v)
                                          for k, v in settings.model_group_alias.items()},
                             "provider_aliases": dict(state.router.alias_to_provider),
                             "strategy": settings.routing_strategy})

    @api.patch("/model-groups/{name:path}")
    async def admin_patch_model_group(name: str, request: Request, actor: CurrentUser):
        if actor.role != "admin":
            return _err(403, "permission_error", "admin role required", request)
        gname, deps = _admin_group(name)
        if gname is None:
            return _err(404, "not_found_error", str(deps), request)
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
        await state.logs.log_audit(actor=actor.username, action="model_group.update",
                                   target=gname, diff=diff)
        return ORJSONResponse({"group": gname, **diff})

    # -- admin: model_group_alias CRUD -------------------------------------------
    # Batch endpoint (POST /admin/aliases) — body {"set": {...}, "unset": [...]}.
    # Batch avoids path-param URL encoding for alias keys (which may contain
    # slashes) and gives atomic validate-then-mutate semantics with one audit
    # event, mirroring the `weights` block in admin_patch_model_group above.
    @api.post("/aliases")
    async def admin_update_aliases(request: Request, actor: CurrentUser):
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
            elif not alias_value_eq(new_live[k], v):
                overlay[k] = alias_value_to_json(new_live[k])
        for k, v in new_live.items():
            if k not in base:
                overlay[k] = alias_value_to_json(v)
        if state.config_store is not None:
            await state.config_store.set_setting("model_group_alias", overlay)
        await state.logs.log_audit(actor=actor.username, action="aliases.update",
                                   target="model_group_alias",
                                   diff={"set": dict(set_map),
                                         "unset": list(unset)})
        dumped = {k: alias_value_to_json(v) for k, v in new_live.items()}
        return ORJSONResponse({"aliases": dumped,
                               "set": dict(set_map),
                               "unset": list(unset)})

    return api
