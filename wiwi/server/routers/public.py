"""Public, secret-free surfaces.

Extracted from ``server/app.py`` (round 206). ``/public/models`` powers the
public Models catalog page. It strips all health/inflight/cooldown/weight
fields — only the model group's name and each deployment's provider +
model_id are exposed. Never errors on auth.
"""

from __future__ import annotations

from fastapi import APIRouter

from wiwi.config import alias_value_to_json
from wiwi.server.dependencies import ORJSONResponse


def make_router(state):
    """Build the public router bound to the per-app state."""
    api = APIRouter(prefix="/public", tags=["public"])

    @api.get("/models")
    async def public_models() -> ORJSONResponse:
        groups = []
        for gname in sorted(state.router.groups):
            deps = state.router.groups[gname]
            groups.append({
                "name": gname,
                "deployments": [{
                    "provider": d.provider.name,
                    # The adapter type is not a secret (it is already published
                    # on the docs page), and the public catalog needs it to show
                    # the provider's own badge rather than a bare name.
                    "type": d.provider.provider_type,
                    "model_id": d.model_id,
                } for d in deps],
            })
        return ORJSONResponse({
            "groups": groups,
            "aliases": {k: alias_value_to_json(v)
                        for k, v in state.router.settings.model_group_alias.items()},
        })

    return api
