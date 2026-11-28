"""Admin surface for per-model pricing and alert rules.

Extracted verbatim from ``server/app.py``: same paths, same status codes,
same audit trail, same error bodies. The pricing handlers own the
per-1M/per-token normalization and the retroactive true-up that re-bills
the history a model's first price covers; alert rules are storage only
(the evaluation engine is post-MVP). The handlers close over per-app
objects handed in by ``create_app`` (``state``, ``json_body`` and
``_err``), so the router is built once per app — auth is the module-level
``Depends`` guard (``AdminUser`` from ``wiwi/server/dependencies``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from wiwi.config import PROVIDER_TYPES
from wiwi.server.dependencies import AdminUser, ORJSONResponse


def make_router(state, *, json_body, _err):
    """Build the /admin/pricing and /admin/alert-rules router."""
    api = APIRouter(tags=["admin-pricing"])

    # -- admin: model pricing -----------------------------------------------------

    def _valid_pricing_scopes() -> set[str]:
        """Provider scopes a price may bind to: account names and provider types.

        Both live in one namespace (the cost engine resolves account-first), so
        an account named exactly like its type is legal and unambiguous.
        """
        return set(state.router.providers) | set(PROVIDER_TYPES)

    def _entry_to_per_1m(model_id: str, p: dict[str, Any],
                         scope: str | None = None) -> dict[str, Any]:
        """Render a per-token rate dict as the per-1M shape the API speaks."""
        entry: dict[str, Any] = {
            "model_id": model_id,
            "input_per_1m": round(p.get("input_cost_per_token", 0) * 1_000_000, 6),
            "output_per_1m": round(p.get("output_cost_per_token", 0) * 1_000_000, 6),
        }
        if scope is not None:
            entry["provider"] = scope
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
        return entry

    @api.get("/admin/pricing")
    async def admin_pricing(actor: AdminUser):
        prices = state.cost.prices
        out: list[dict[str, Any]] = []
        for model_id, p in sorted(prices.items()):
            entry = _entry_to_per_1m(model_id, p)
            scopes = p.get("providers") or {}
            if scopes:
                entry["scopes"] = [
                    _entry_to_per_1m(model_id, rates, scope)
                    for scope, rates in sorted(scopes.items())
                ]
            out.append(entry)
        return ORJSONResponse({"models": out})

    @api.put("/admin/pricing/{model_id:path}")
    async def admin_put_pricing(model_id: str, request: Request, actor: AdminUser):
        """Create or update a model's pricing (USD per 1M tokens in the body).

        ``?provider=<scope>`` prices the model for one provider only — an
        account name or a provider type. Without it, the base rate that covers
        every provider.
        """
        scope = request.query_params.get("provider")
        if scope is not None and scope not in _valid_pricing_scopes():
            return _err(400, "invalid_request_error",
                        f"unknown provider scope {scope!r}; expected a configured"
                        " provider account or one of: "
                        + ", ".join(sorted(PROVIDER_TYPES)), request)
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
        existing = state.cost.prices.get(model_id) or {}
        if scope is None:
            # A model counts as priced once ANY price exists for it — including
            # a scoped-only one — so a later edit does not re-run the true-up
            # over history that has already been billed.
            was_unpriced = model_id not in state.cost.prices
        else:
            was_unpriced = scope not in (existing.get("providers") or {})
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
        if scope is None:
            # Merge rather than replace: a base-rate edit must not delete the
            # per-provider prices configured for this model.
            merged = dict(existing)
            merged.update(entry)
            if existing.get("providers"):
                merged["providers"] = existing["providers"]
            state.cost.prices[model_id] = merged
        else:
            target = state.cost.prices.setdefault(model_id, {})
            target.setdefault("providers", {})[scope] = entry
        if state.config_store:
            if scope is None:
                await state.config_store.upsert_price(model_id, entry)
            else:
                await state.config_store.upsert_price_scope(model_id, scope, entry)
        await state.logs.log_audit(actor=actor.username, action="pricing.update",
                                   target=model_id,
                                   diff={"input_per_1m": ipt,
                                         "output_per_1m": opt,
                                         **({"provider": scope} if scope else {})})

        # Retroactive true-up: when a model (or one of its provider scopes) is
        # priced for the FIRST time, its historical requests were logged at
        # cost 0 and the virtual keys that served them were never charged.
        # Recompute those rows' cost and add the delta to each key's
        # spend_to_date so budgets reflect the retroactive spend. Rate EDITS
        # (already priced) never rewrite history — rows keep their originally
        # logged cost.
        key_deltas: dict[str, float] = {}
        if was_unpriced and state.logs.db_sink is not None:
            try:
                # Each row is repriced at ITS OWN serving provider's rate: the
                # attempts record names the account, and the router maps that
                # to a provider type for scoped lookup.
                def rate_for(provider_name: str,
                             _mid=model_id) -> dict | None:
                    acct = state.router.providers.get(provider_name)
                    return state.cost.resolve(
                        _mid, acct.provider_type if acct else None,
                        provider_name)

                # The FULL pricing key, not its last path segment: model ids
                # routinely contain "/" (OpenRouter's ``stealth/ox-alpha``), so
                # truncating here made the reprice match any model sharing a
                # final segment and bill its history at this model's rate
                # (round 65). The matcher applies the same slash-tail rule the
                # cost engine's lookup does, so a bare-tail key still works.
                key_deltas = await state.logs.db_sink.reprice_unpriced_history(
                    model_id, rate_for)
            except Exception:  # noqa: BLE001 — pricing update must not 500
                state.logs.log_proxy(
                    "error", "retroactive pricing true-up failed",
                    model_id=model_id, exc_info=True)
            if key_deltas:
                for key_id, delta in key_deltas.items():
                    # add_cost path enforces max_budget; a true-up that would
                    # push a key over budget is still applied (the spend
                    # already happened upstream) via the unconditional branch.
                    await state.auth.apply_spend_trueup(key_id, delta)
                state.logs.log_proxy(
                    "info", "retroactive pricing applied",
                    model_id=model_id, keys=len(key_deltas),
                    total_delta=round(sum(key_deltas.values()), 6))
        # Echo back the normalized per-1M entry the GET endpoint returns.
        result = _entry_to_per_1m(model_id, entry, scope)
        # Retroactive true-up summary so the admin UI can show what happened.
        result["retroactive"] = {
            "applied": bool(key_deltas),
            "keys": len(key_deltas),
            "total_delta": round(sum(key_deltas.values()), 6),
        }
        return ORJSONResponse(result)

    @api.delete("/admin/pricing/{model_id:path}")
    async def admin_delete_pricing(model_id: str, request: Request, actor: AdminUser):
        """Remove a model's custom pricing entry.

        ``?provider=<scope>`` removes only that provider's override, leaving
        the base entry and every other scope intact.
        """
        scope = request.query_params.get("provider")
        if scope is not None:
            scopes = (state.cost.prices.get(model_id) or {}).get("providers") or {}
            existed = scope in scopes
            if existed:
                del scopes[scope]
            if state.config_store:
                await state.config_store.delete_price_scope(model_id, scope)
            await state.logs.log_audit(actor=actor.username, action="pricing.delete",
                                       target=model_id, diff={"provider": scope})
            return ORJSONResponse({"deleted": existed, "model_id": model_id,
                                   "provider": scope})
        existed = model_id in state.cost.prices
        if existed:
            del state.cost.prices[model_id]
        if state.config_store:
            # Removes the model's scopes alongside its base entry.
            await state.config_store.delete_price(model_id)
        await state.logs.log_audit(actor=actor.username, action="pricing.delete",
                                   target=model_id)
        return ORJSONResponse({"deleted": existed, "model_id": model_id})

    # -- admin: alert rules (storage only; evaluation engine is post-MVP) ----------
    @api.get("/admin/alert-rules")
    async def admin_get_alert_rules(actor: AdminUser):
        return ORJSONResponse({"rules": state.alert_rules})

    @api.put("/admin/alert-rules")
    async def admin_put_alert_rules(request: Request, actor: AdminUser):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        rules = body.get("rules")
        if not isinstance(rules, list):
            return _err(400, "invalid_request_error", "'rules' must be a list", request)
        state.alert_rules = rules
        if state.config_store:
            await state.config_store.set_setting("alert_rules", rules)
        await state.logs.log_audit(actor=actor.username, action="alert_rules.update",
                                   target="*", diff={"count": len(rules)})
        return ORJSONResponse({"rules": state.alert_rules})

    return api
