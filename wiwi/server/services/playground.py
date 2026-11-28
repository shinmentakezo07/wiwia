"""The Playground's hidden virtual key: minting, capping and reuse.

The Playground surfaces — ``/v1/playground/completions`` (the router lives in
``wiwi/server/routers/v1_api.py``) and ``/auth/playground-key`` — share one
hidden virtual key per user, minted on first use and reused for the
account's whole life. These functions are that policy, extracted verbatim
from ``server/app.py``; they hold no FastAPI imports, so the rules stay
testable without an HTTP client.

The bearer cache lives on ``AppState`` (``state.pg_bearers``): it is bounded
by ``_PgBearerCache`` and explicitly dropped on logout and on account
disable, so no plaintext key outlives the session that created it. Holding
the plaintext server-side is what removes the browser round-trip entirely:
the SPA calls the Playground with only its session cookie, and the key
material never travels to the client.
"""

from __future__ import annotations

from wiwi.auth.users import UserInfo
from wiwi.server.dependencies import key_owner_id

# A TTL keeps an abandoned key from living forever, and the cap is one so
# repeated use can never accumulate live credentials in the owner's key list.
PLAYGROUND_KEY_TTL_S = 24 * 3600.0
MAX_PLAYGROUND_KEYS_PER_USER = 1


def playground_key_usable(info) -> bool:
    """Whether an authenticated playground key is fit to be handed back.

    This must stay in step with ``authenticate()``'s admission checks in
    ``run_chat_like``, because the plaintext returned here is injected as
    that call's bearer and re-tested there. A predicate weaker than the one
    downstream wedges the Playground instead of healing it.

    ``AuthService.authenticate`` refuses an *expired* credential itself (both
    the cached and the miss path), so expiry is already covered by the
    ``is not None`` test. It does NOT refuse a *disabled* one:
    ``_lookup_db`` selects ``v.disabled`` into ``AuthInfo`` without
    filtering on it, by design, because every caller is expected to check
    ``info.disabled`` itself. The wrapper was that caller and was not
    checking it, so a revoked key read as healthy here, the plaintext was
    served, ``run_chat_like`` answered 401 "key disabled or expired", and
    because this predicate kept passing, no later request could re-mint.

    Only the two credential-liveness conditions belong here. Model
    allowlists and budget caps are request-scoped and are evaluated per call
    by the admission path; a playground key declares no budget, and copying
    the allowlist test in would make the cache depend on which model this
    particular turn happens to ask for.
    """
    return info is not None and not info.disabled


async def mint_playground_key(state, actor: UserInfo) -> str:
    """Mint the playground virtual key for *actor*.

    Called only when no cached key is usable, so the account normally keeps
    the one credential it was first given rather than collecting a new key
    per login. Every real account owns its key (admins included — see
    ``key_owner_id``), so it shows up in the owner's key list, respects
    role-based filtering, and gets its own budget and per-owner cap. Only
    the synthetic master is un-owned.
    """
    owner_id = key_owner_id(actor)
    # Bound the key: a TTL so an abandoned one expires, and a cap of one so
    # a re-mint (after a process restart, a TTL expiry or a revoke) retires
    # the previous live key instead of adding a rival to the owner's list.
    # The cap applies to admins too — they own their key (see
    # `key_owner_id`), so the count is per admin rather than across all of
    # them. Before round 105 admins minted unowned keys and were exempt
    # twice over: `create_key`'s per-owner limit skips owner_id=None, and
    # this branch skipped them as well, so admin playground keys grew
    # without limit.
    service = state.auth
    if service is not None:
        active = await service.count_keys(owner_id=owner_id, alias="playground")
        if active >= MAX_PLAYGROUND_KEYS_PER_USER:
            await service.expire_keys(owner_id=owner_id, alias="playground",
                                      keep_newest=MAX_PLAYGROUND_KEYS_PER_USER - 1)
    plaintext, _kid = await service.create_key(  # type: ignore[union-attr]
        alias="playground", owner_id=owner_id,
        ttl_seconds=PLAYGROUND_KEY_TTL_S)
    # Register with the wrapper's bearer cache so /v1/playground/completions
    # reuses exactly the key just handed out (to the signup/login response
    # or to the back-compat /auth/playground-key) instead of minting a
    # rival one on its first call. Keyed by actor id, not owner id: two
    # actors must never share a cache slot even where they share an owner.
    state.pg_bearers.set(actor.id, plaintext)
    return plaintext


async def playground_bearer(state, actor: UserInfo) -> str:
    """Return a live playground virtual key plaintext for *actor*.

    Resolution order: cached plaintext that still authenticates AND is still
    usable → one capped mint (same per-owner cap and TTL as
    /auth/playground-key). This is the only path that mints during normal
    use, so the same key is reused for the account's whole life instead of
    a fresh credential per login. Raises on a mint failure; the caller
    converts that into a 500.
    """
    service = state.auth
    if service is None:  # guarded by the caller, kept for type-checking
        raise RuntimeError("gateway not initialized")
    plaintext = state.pg_bearers.get(actor.id)
    if plaintext and playground_key_usable(
            await service.authenticate(plaintext)):
        return plaintext
    state.pg_bearers.drop(actor.id)
    return await mint_playground_key(state, actor)
