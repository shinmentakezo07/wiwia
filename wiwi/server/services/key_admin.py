"""Business logic for virtual-key administration.

The route handlers in ``server/routers/admin_keys.py`` used to hold this
logic inline. Moving it here means the rules are in one place and testable
without an HTTP client:

* which fields a non-admin owner may change (AUDIT #220),
* that a key cannot sit in a lane the operator never configured,
* that the per-owner key cap is enforced by the service, not the route.

The service raises :class:`APIError`; the route layer turns that into a
response. Nothing here touches FastAPI, so the rules can be exercised
directly.
"""

from __future__ import annotations

from typing import Any

from wiwi.auth.service import AuthService
from wiwi.auth.users import UserInfo
from wiwi.server.dependencies import key_owner_id
from wiwi.server.errors import APIError
from wiwi.server.schemas.admin_keys import (
    UPDATABLE_FIELDS,
    KeyDisableRequest,
    KeyGenerateRequest,
    KeyPatchRequest,
)


class KeyAdminService:
    """Virtual-key administration rules.

    One instance per app; it holds the ``AppState`` and the configured lane
    names, and exposes one method per admin operation.
    """

    def __init__(self, state, priority_lanes: list[str]) -> None:
        self.state = state
        self.priority_lanes = list(priority_lanes)

    # -- helpers -------------------------------------------------------------

    @property
    def _auth(self) -> AuthService:
        auth = self.state.auth
        if auth is None:
            raise APIError(500, "api_error", "gateway not initialized")
        return auth

    async def _require_owner(self, actor: UserInfo, key_id: str) -> None:
        """Raise 403 unless *actor* may touch *key_id*.

        An admin may touch any key. A non-admin may touch only a key they
        own. Ownership alone was the whole check on the PATCH path, so a
        tenant could strip the budget/RPM/TPM/model-allowlist caps an
        operator had placed on their own key (AUDIT #220).
        """
        if actor.role == "admin":
            return
        owner = await self._auth.key_owner(key_id)
        if owner != actor.id:
            raise APIError(403, "permission_error", "not your key")

    def _validate_lane(self, priority: str | None) -> None:
        """Raise 400 when *priority* names a lane the operator never configured.

        An unknown lane would silently fall back to ``default_lane`` at
        admission, so what the caller asked for would be quietly ignored.
        Rejecting it here is what lets the caller see the 400 and fix it.
        """
        if priority is None:
            return
        if priority not in self.priority_lanes:
            raise APIError(
                400, "invalid_request_error",
                f"unknown priority lane {priority!r}; configured"
                f" lanes: {sorted(self.priority_lanes) or 'none'}")

    # -- operations ----------------------------------------------------------

    async def generate(self, actor: UserInfo, body: KeyGenerateRequest) -> tuple[str, str]:
        """Mint a virtual key. Returns ``(plaintext, key_id)``.

        The plaintext is returned here and nowhere else — it is stored
        hashed, so this is the only chance the caller has to keep it.
        """
        # Owner-scope real admins too (the synthetic master stays un-owned):
        # an un-owned admin key is exempt from ``max_keys_per_user`` and shares
        # one owner id with every other admin, which is the round-105 defect on
        # the Playground path (round 107).
        owner_id = key_owner_id(actor)
        priority = body.priority
        self._validate_lane(priority)
        try:
            return await self._auth.create_key(
                alias=str(body.name or body.alias or ""),
                models=body.models, max_budget=body.max_budget,
                rpm=body.rpm, tpm=body.tpm,
                ttl_seconds=body.ttl_seconds,
                custom_key=body.custom_key, owner_id=owner_id,
                priority=priority)
        except ValueError as e:
            # The per-owner cap ("key limit reached"), the custom-key length
            # rule and the numeric-limit rules all arrive as ValueError.
            raise APIError(400, "invalid_request_error", str(e)) from e

    async def list(self, actor: UserInfo) -> list[dict]:
        """List the keys *actor* may see.

        An admin sees every key; a non-admin sees only their own.
        """
        if actor.role == "admin":
            return await self._auth.list_keys()
        return await self._auth.list_keys_for_owner(actor.id)

    async def delete(self, actor: UserInfo, key_id: str) -> bool:
        """Revoke a key. Returns whether a row was actually removed."""
        await self._require_owner(actor, key_id)
        return await self._auth.delete_key(key_id)

    async def set_disabled(self, actor: UserInfo, key_id: str,
                           body: KeyDisableRequest) -> bool:
        """Disable or re-enable a key without revoking it.

        Returns whether the key exists.
        """
        await self._require_owner(actor, key_id)
        if not await self._auth.set_disabled(key_id, body.disabled):
            raise APIError(404, "not_found_error", f"unknown key '{key_id}'")
        return True

    async def patch(self, actor: UserInfo, key_id: str,
                    body: KeyPatchRequest) -> dict:
        """Update a key's editable fields. Returns the updated key."""
        # A non-admin may only touch a key they own, and only the owner-facing
        # fields on it. The allowlist is enforced again inside ``update_key``
        # so no future call site can reintroduce the gap.
        allow: tuple[str, ...] | None = None
        if actor.role != "admin":
            await self._require_owner(actor, key_id)
            allow = AuthService.OWNER_FACING_FIELDS
        # Project onto the known-writable set: the model ignores unknown
        # fields, and the service is the second gate (the route used to do
        # the projection itself).
        raw = body.model_dump(exclude_unset=True)
        fields: dict[str, Any] = {k: raw[k] for k in UPDATABLE_FIELDS if k in raw}
        if allow is not None:
            refused = [k for k in fields if k not in allow]
            if refused:
                raise APIError(
                    403, "permission_error",
                    f"field '{refused[0]}' may only be set by an admin")
        try:
            updated = await self._auth.update_key(key_id, fields, allow)
        except ValueError as e:
            # Malformed limit/model values used to escape as an uncaught
            # ValueError/TypeError (HTTP 500); report them as a client error.
            raise APIError(400, "invalid_request_error", str(e)) from e
        if updated is None:
            raise APIError(404, "not_found_error", f"unknown key '{key_id}'")
        return updated
