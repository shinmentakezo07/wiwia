"""Admin surface for user management.

Extracted verbatim from ``server/app.py``: same paths, same status codes,
same audit trail, same error bodies. Both routes accept the master bearer
key (existing admin auth) OR a master/admin session cookie, so an admin
who logged in via /auth/login can manage users from the UI. The handlers
close over per-app objects handed in by ``create_app`` (``state``,
``json_body`` and ``_err``), so the router is built once per app.
Authorization is a module-level dependency: each handler declares
``actor: AdminUser`` from ``wiwi/server/dependencies``, the same
convention as ``admin_keys.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from wiwi.server.dependencies import AdminUser, ORJSONResponse


def make_router(state, *, json_body, _err):
    """Build the /admin/users router bound to the per-app helpers."""
    api = APIRouter(tags=["admin-users"])

    # -- admin: users ----------------------------------------------------------
    @api.get("/admin/users")
    async def admin_list_users(request: Request, actor: AdminUser):
        # Accept the master bearer key (existing admin auth) OR a master/admin
        # session cookie, so an admin who logged in via /auth/login can manage
        # users from the UI.
        if state.users is None:
            return _err(500, "api_error", "user service not initialized", request)
        return ORJSONResponse({"users": await state.users.list_users()})

    @api.patch("/admin/users/{uid}")
    async def admin_patch_user(uid: str, request: Request, actor: AdminUser):
        # Accept the master bearer key (existing admin auth) OR a master/admin
        # session cookie, so an admin who logged in via /auth/login can manage
        # users from the UI.
        if state.users is None:
            return _err(500, "api_error", "user service not initialized", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        role = body.get("role")
        disabled = body.get("disabled")
        # `disabled` must be a real boolean. The guard used an identity check
        # (`disabled is True`) while UserService.patch coerced with `int()`, so
        # `1` / `"1"` / `1.0` skipped the guard and still disabled the account —
        # locking out the last admin (AUDIT #71). Non-bool shapes that int()
        # could not coerce raised TypeError and returned 500 (AUDIT #81). Both
        # are now a clean 400; only a genuine bool is accepted.
        if disabled is not None and not isinstance(disabled, bool):
            return _err(400, "invalid_request_error",
                        "disabled must be a boolean", request)
        # Last-admin guard: if demoting or disabling an admin would leave zero
        # active admins, reject. The master-key admin (id="master") is synthetic
        # and not in the users table, so count_admins() only counts DB admins.
        #
        # The guard and the write are ONE critical section: both straddle
        # awaits (get, count, patch), so two concurrent demotions of the two
        # remaining admins each read "2 admins" and both committed, leaving the
        # table with no admin at all.
        async with state.user_mutation_lock:
            if role == "user" or disabled is True:
                cur = await state.users.get(uid)
                if (cur is not None and cur.role == "admin" and not cur.disabled
                        and await state.users.count_admins() <= 1):
                    return _err(400, "invalid_request_error",
                                "cannot demote or disable the last admin", request)
            try:
                updated = await state.users.patch(uid, role=role, disabled=disabled)
            except ValueError as e:
                return _err(400, "invalid_request_error", str(e), request)
        if updated is None:
            return _err(404, "not_found_error", "user not found", request)
        # Disabling an account must revoke the keys it owns (AUDIT #148).
        # Without this a shut-off user keeps full API access through every
        # key they minted: the request path authenticates against `vkeys`
        # alone, and there is no delete-user endpoint, so disabling is the
        # only lever an operator has. Expire rather than delete — the audit
        # trail survives, and expire_keys evicts each credential from the
        # auth cache so it stops working immediately rather than at TTL.
        revoked = 0
        if disabled is True:
            revoked = await state.auth.expire_keys(owner_id=uid)  # type: ignore[union-attr]
            # The key rows are expired; drop the wrapper's in-memory plaintext
            # for the same account, or it would keep being re-minted for a
            # user who can no longer hold a session (round 105).
            state.pg_bearers.drop(uid)
        await state.logs.log_audit(actor=actor.username, action="user.update", target=uid,
                                   diff={"role": role, "disabled": disabled,
                                         "revoked_keys": revoked})
        return ORJSONResponse({**updated, "revoked_keys": revoked})

    return api
