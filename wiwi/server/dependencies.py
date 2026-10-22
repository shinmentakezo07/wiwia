"""Shared FastAPI dependencies: the auth guards, as reusable callables.

Before this module the auth guards were closures inside ``create_app``:
``current_user``, ``require_user_dep`` and ``require_admin_dep``. A closure
can only be used by handlers defined in the same function, which is exactly
why every route had to live in ``server/app.py`` — the guards were not
importable.

These dependencies are importable, so a router module can declare
``actor: UserInfo = Depends(require_user)`` and get the same guard the
inline routes use. The resolution logic itself is unchanged: the master bearer
key wins, then the signed session cookie, then anonymous.

**The guards are module-level, not built per app.** The app state is read
from ``request.app.state.wiwi``, which is the standard FastAPI way to reach
per-app state from a dependency. Building the guard per app instead (closing
over the ``AppState``) looks tempting but breaks the annotation resolution:
``from __future__ import annotations`` makes annotations strings, and FastAPI
evaluates them against the *module* globals — so a closure-local dependency
name is invisible and the parameter silently degrades into a body field.

Two rules the callers must not break:

* **401 vs 403 are distinct.** ``require_user`` raises 401 (who are you);
  ``require_admin`` raises 403 (we know who you are, and the answer is no).
  The admin check does the 401 first, so an anonymous caller always sees 401.
* **The guard never returns a response.** It raises :class:`APIError`, which
  the app's exception handler renders in the caller's dialect. Handlers
  therefore cannot "forget" to check for an error the way the old
  ``resp = await require_admin_dep(request); if resp: return resp`` pattern
  invited.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Request

from wiwi.auth.users import UserInfo, verify_session
from wiwi.server.errors import APIError

#: The synthetic master id. ``server/app.py`` re-exports this so the callers
#: that already import it from there keep working. It is defined here (not
#: imported from ``server/app.py``) because ``app.py`` imports this module —
#: importing back would be a cycle.
SYNTHETIC_MASTER_ID = "master"


def bearer_token(request: Request) -> str:
    """The caller's bearer credential, or ``""``.

    Accepts ``Authorization: Bearer …`` and the ``x-api-key`` header that
    Claude Code sends on ``/v1/messages``.
    """
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return authz[7:].strip()
    xkey = request.headers.get("x-api-key")
    return xkey.strip() if xkey else ""


def master_actor() -> UserInfo:
    """The synthetic master admin (no ``users`` row).

    One definition so the master-bearer branch of the session resolution and
    the session mint in ``/auth/login`` cannot drift apart.
    """
    return UserInfo(id=SYNTHETIC_MASTER_ID, username=SYNTHETIC_MASTER_ID,
                    role="admin")


def key_owner_id(actor: UserInfo) -> str | None:
    """Owner id to mint a virtual key under, or None for an un-owned key.

    Every real account owns its own key, admin included. Sharing one un-owned
    key across all admins gave them a single budget and a single per-owner
    cap, so a sixth admin using the Playground retired a key another admin was
    still using (round 105); the admin key-generate route minted un-owned keys
    for the same reason and was exempt from ``max_keys_per_user`` (round 107).

    The synthetic master is the one exception. It has no ``users`` row, and
    ``AuthService._lookup_db``'s owner check fails closed on a missing owner,
    so an owned master key would never authenticate at all. It also needs no
    separation: every master-key holder is the same operator behind the same
    secret.
    """
    return None if actor.id == SYNTHETIC_MASTER_ID else actor.id


def app_state(request: Request):
    """The app's ``AppState``, reached the standard FastAPI way."""
    return request.app.state.wiwi


async def resolve_current_user(request: Request, state) -> UserInfo | None:
    """Resolve the caller from a master bearer key OR a signed session cookie.

    Returns ``None`` when anonymous, or when the session's user has been
    disabled or deleted.

    ``state`` is the ``AppState``; it is passed in rather than imported so
    this module stays free of an import cycle with ``server/app.py``.
    """
    master_key = state.config.general_settings.master_key
    token = bearer_token(request)
    if master_key and token and hmac.compare_digest(token.encode(),
                                                     master_key.encode()):
        return master_actor()
    cookie = request.cookies.get("wiwi_session")
    if not cookie:
        return None
    parsed = verify_session(state.users._secret, cookie)  # type: ignore[union-attr]
    if parsed is None:
        return None
    uid, _role, _exp = parsed
    if uid == SYNTHETIC_MASTER_ID:
        # Only honour a synthetic master session when a master key is actually
        # configured. Otherwise any cookie signed with whatever secret happened
        # to be in use would mint an admin with no DB row.
        if not master_key:
            return None
        return master_actor()
    if state.users is None:
        return None
    info = await state.users.get(uid)
    if info is None or info.disabled:
        return None
    return info


async def require_user(request: Request) -> UserInfo:
    """The authenticated caller.

    Raises 401 when the caller is anonymous.
    """
    actor = await resolve_current_user(request, app_state(request))
    if actor is None:
        raise APIError(401, "authentication_error", "authentication required")
    return actor


async def require_admin(request: Request) -> UserInfo:
    """The authenticated caller, who must also be an admin.

    Raises 401 when anonymous and 403 when authenticated but not an admin.
    """
    actor = await require_user(request)
    if actor.role != "admin":
        raise APIError(403, "permission_error", "admin role required")
    return actor


#: Type aliases for the two guards. Handlers declare
#: ``actor: Annotated[UserInfo, Depends(require_admin)]``.
#:
#: The alias lives here (not in each router) so the dependency is resolved
#: against THIS module's globals — which is where ``require_user`` is
#: defined. A router that re-declared the alias locally would still work
#: (the name would be in its own globals), but keeping one alias means one
#: definition of "what an authenticated caller looks like in a signature".
CurrentUser = Annotated[UserInfo, Depends(require_user)]
AdminUser = Annotated[UserInfo, Depends(require_admin)]
