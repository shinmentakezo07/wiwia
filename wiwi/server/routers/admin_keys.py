"""Admin routes for virtual keys.

Thin by construction: each handler declares its dependencies, delegates to
:class:`~wiwi.server.services.key_admin.KeyAdminService`, and returns the
result. The authorization rules live in the service; the request shapes live
in ``wiwi/server/schemas/admin_keys.py``.

These five routes were previously inline closures in ``server/app.py``. The
behaviour is unchanged — same paths, same methods, same status codes, same
error bodies, same audit trail. What changed is that the logic is now
importable and testable, and that the handlers are four lines long instead of
forty.
"""

from __future__ import annotations

from fastapi import APIRouter

from wiwi.server.dependencies import CurrentUser
from wiwi.server.schemas.admin_keys import (
    KeyDeleteResponse,
    KeyDisableRequest,
    KeyDisableResponse,
    KeyGenerateRequest,
    KeyGenerateResponse,
    KeyListResponse,
    KeyPatchRequest,
    KeyPatchResponse,
)
from wiwi.server.services.key_admin import KeyAdminService

router = APIRouter(prefix="/admin/keys", tags=["admin-keys"])


def make_router(service: KeyAdminService) -> APIRouter:
    """Build the router bound to *service*.

    The service is per-app (it holds the ``AppState``), so the router is
    built once per app in ``create_app`` rather than at import time. The
    auth guard is a module-level dependency — see
    ``wiwi/server/dependencies.py`` for why it cannot be a closure.
    """
    api = APIRouter(prefix="/admin/keys", tags=["admin-keys"])

    @api.post("/generate", response_model=KeyGenerateResponse)
    async def admin_generate_key(body: KeyGenerateRequest, actor: CurrentUser):
        """Mint a virtual key. The plaintext is returned here and nowhere else."""
        plaintext, kid = await service.generate(actor, body)
        await service.state.logs.log_audit(
            actor=actor.username, action="key.generate", target=kid,
            diff={"source": "custom"} if body.custom_key else None)
        return KeyGenerateResponse(key=plaintext, id=kid)

    @api.get("", response_model=KeyListResponse,
             response_model_exclude_unset=True)
    async def admin_list_keys(actor: CurrentUser):
        """List the keys the caller may see."""
        return KeyListResponse(keys=await service.list(actor))

    @api.delete("/{key_id}", response_model=KeyDeleteResponse)
    async def admin_delete_key(key_id: str, actor: CurrentUser):
        """Revoke a key."""
        ok = await service.delete(actor, key_id)
        await service.state.logs.log_audit(actor=actor.username,
                                           action="key.delete", target=key_id)
        return KeyDeleteResponse(deleted=ok)

    @api.post("/{key_id}/disable", response_model=KeyDisableResponse)
    async def admin_disable_key(
        key_id: str,
        body: KeyDisableRequest,
        actor: CurrentUser,
    ):
        """Disable or re-enable a key without revoking it."""
        await service.set_disabled(actor, key_id, body)
        await service.state.logs.log_audit(
            actor=actor.username,
            action="key.disable" if body.disabled else "key.enable",
            target=key_id)
        return KeyDisableResponse(key_id=key_id, disabled=body.disabled)

    @api.patch("/{key_id}", response_model=KeyPatchResponse,
               response_model_exclude_unset=True)
    async def admin_patch_key(
        key_id: str,
        body: KeyPatchRequest,
        actor: CurrentUser,
    ):
        """Update a key's editable fields."""
        updated = await service.patch(actor, key_id, body)
        await service.state.logs.log_audit(actor=actor.username,
                                           action="key.update", target=key_id)
        return KeyPatchResponse(key=updated)

    return api
