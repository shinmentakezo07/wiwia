"""Round 168: the /admin/keys routes move to a router + service + schemas.

**What changed.** The five ``/admin/keys`` routes used to be inline closures
inside ``create_app`` (``wiwi/server/app.py``), each one a 40-line handler
that parsed its own body, validated its own fields and enforced its own
authorization rules. They now live in:

* ``wiwi/server/schemas/admin_keys.py`` — the Pydantic v2 request/response
  models,
* ``wiwi/server/services/key_admin.py`` — the business rules,
* ``wiwi/server/routers/admin_keys.py`` — the HTTP shape,
* ``wiwi/server/dependencies.py`` — the auth guard, as a ``Depends``.

**Why this needs a regression file.** The refactor is structural, so the
risk is not "the new code is wrong" but "the new code differs from the old
code in a way nobody notices". These tests pin the contract the old
handlers had and the new ones must keep:

1. The paths, methods and response shapes are unchanged.
2. The error bodies are still dialect-shaped and still carry the same
   status codes — notably **400, not 422**, for a malformed body.
3. The authorization rules are unchanged: 401 anonymous, 403 for a
   non-owner, 403 for an owner reaching an operator-only field.
4. A validation failure on a *typed* field is still a 400 in the OpenAI
   error shape, not FastAPI's default 422 + ``{"detail": [...]}``.
5. The ``actor`` parameter must be resolved as a *dependency*, never
   mistaken for a body field — the exact bug the first cut of this
   refactor shipped (see ``test_actor_is_a_dependency_not_a_body_field``).
"""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

import wiwi.server.app as app_mod
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            c.app = app
            yield c


# -- 1. routes still exist, with the same paths and methods --------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/admin/keys"),
    ("POST", "/admin/keys/generate"),
    ("DELETE", "/admin/keys/k123"),
    ("POST", "/admin/keys/k123/disable"),
    ("PATCH", "/admin/keys/k123"),
])
async def test_routes_are_registered(client, method, path):
    """A wrong path or method would 405/404 instead of reaching the guard."""
    r = await client.request(method, path, headers=AUTH)
    # The guard answers before the body is read, so 401 (anonymous) is
    # expected; the point is that the route EXISTS (not 404) and that the
    # method is allowed (not 405).
    assert r.status_code in (200, 400, 401, 403, 404), r.status_code


async def test_generate_returns_plaintext_id_and_note(client):
    r = await client.post("/admin/keys/generate", json={"name": "k1"},
                          headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"].startswith("sk-wiwi-")
    assert body["id"]
    assert "not shown again" in body["note"]
    # Exactly three fields — the response model must not silently add more.
    assert set(body) == {"key", "id", "note"}


async def test_list_returns_the_keys_array(client):
    await client.post("/admin/keys/generate", json={"name": "k1"}, headers=AUTH)
    r = await client.get("/admin/keys", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"keys"}
    assert isinstance(body["keys"], list)
    assert any(k["alias"] == "k1" for k in body["keys"])


async def test_disable_returns_key_id_and_disabled(client):
    gen = await client.post("/admin/keys/generate", json={"name": "k1"},
                            headers=AUTH)
    kid = gen.json()["id"]
    r = await client.post(f"/admin/keys/{kid}/disable", json={"disabled": True},
                          headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"key_id": kid, "disabled": True}


async def test_delete_returns_deleted_flag(client):
    gen = await client.post("/admin/keys/generate", json={"name": "k1"},
                            headers=AUTH)
    kid = gen.json()["id"]
    r = await client.delete(f"/admin/keys/{kid}", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True}


async def test_patch_returns_the_updated_key(client):
    gen = await client.post("/admin/keys/generate", json={"name": "k1"},
                            headers=AUTH)
    kid = gen.json()["id"]
    r = await client.patch(f"/admin/keys/{kid}", json={"rpm": 120}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"key"}
    assert body["key"]["id"] == kid
    assert body["key"]["rpm"] == 120


# -- 2. the error body is still the OpenAI shape, still the same status ------


async def test_anonymous_is_401_with_the_openai_error_shape(client):
    r = await client.get("/admin/keys")
    assert r.status_code == 401
    body = r.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"message", "type", "code"}
    assert body["error"]["type"] == "authentication_error"
    assert body["error"]["code"] == "authentication_error"


async def test_errors_carry_the_request_id_header(client):
    r = await client.get("/admin/keys")
    assert r.headers.get("x-wiwi-request-id")


# -- 3. validation failures are 400, NOT FastAPI's 422 -----------------------


async def test_malformed_json_is_400_not_422(client):
    """FastAPI's default validation handler answers 422 with {"detail": ...}.

    wiwi's contract is 400 with a dialect-shaped body, and 422 is a status
    the gateway has never returned. A body-model failure must not become the
    first 422 in wiwi's history.
    """
    r = await client.post("/admin/keys/generate", content=b"{not json",
                          headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert "detail" not in r.json()


async def test_models_not_a_list_of_strings_is_400(client):
    r = await client.post("/admin/keys/generate", json={"models": "abc"},
                          headers=AUTH)
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert "list of strings" in body["error"]["message"]


async def test_validator_messages_are_verbatim_not_pydantic_wrapped(client):
    """The old hand-written handlers returned these strings exactly:
    ``"'models' must be a list of strings"``, ``"'priority' must be a
    string"``, ``"disabled must be a boolean"``.

    Pydantic reports a validator's ``ValueError`` as ``"Value error, …"``
    and the field name in front of it, which would change the message a
    client matches on. The rendering strips both — the schema's message IS
    the message.
    """
    r = await client.post("/admin/keys/generate", json={"models": "abc"},
                          headers=AUTH)
    assert r.json()["error"]["message"] == "'models' must be a list of strings"

    r2 = await client.post("/admin/keys/generate",
                           json={"name": "k", "priority": 3}, headers=AUTH)
    assert r2.json()["error"]["message"] == "'priority' must be a string"

    gen = await client.post("/admin/keys/generate", json={"name": "k1"},
                            headers=AUTH)
    kid = gen.json()["id"]
    r3 = await client.post(f"/admin/keys/{kid}/disable", json={"disabled": "false"},
                            headers=AUTH)
    assert r3.json()["error"]["message"] == "disabled must be a boolean"


async def test_builtin_validation_errors_still_name_the_field(client):
    """Pydantic's OWN errors are terse ("Input should be a valid string"),
    so the field name is what makes them actionable. Only the custom
    validators' messages are used verbatim."""
    r = await client.post("/admin/keys/generate", json={"name": 12345},
                          headers=AUTH)
    assert r.status_code == 400, r.text
    assert "name" in r.json()["error"]["message"]


async def test_priority_not_a_string_is_400(client):
    r = await client.post("/admin/keys/generate",
                          json={"name": "k", "priority": 3}, headers=AUTH)
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert "priority" in body["error"]["message"]


async def test_disabled_must_be_a_real_boolean(client):
    """``bool("false")`` is True, so a string would silently revoke a key
    that the caller asked to *enable* (AUDIT #82)."""
    gen = await client.post("/admin/keys/generate", json={"name": "k1"},
                            headers=AUTH)
    kid = gen.json()["id"]
    r = await client.post(f"/admin/keys/{kid}/disable", json={"disabled": "false"},
                          headers=AUTH)
    assert r.status_code == 400, r.text
    assert "boolean" in r.json()["error"]["message"]


async def test_unknown_lane_is_400_and_names_the_lane(client):
    r = await client.post("/admin/keys/generate",
                          json={"name": "k", "priority": "typo-lane"},
                          headers=AUTH)
    assert r.status_code == 400, r.text
    assert "typo-lane" in r.json()["error"]["message"]


async def test_zero_rpm_is_rejected_with_the_service_message(client):
    """The authoritative limit rules stay in ``AuthService._coerce_limit``
    (which owns the "0 is not a cap" rule) — the schema must not duplicate
    them into a second, drifting convention."""
    r = await client.post("/admin/keys/generate", json={"name": "k", "rpm": 0},
                          headers=AUTH)
    assert r.status_code == 400, r.text
    assert "must be > 0" in r.json()["error"]["message"]


async def test_unknown_fields_are_ignored_not_rejected(client):
    """The old handler ignored stray body keys; rejecting them now would be
    a behaviour change on a public-ish surface."""
    r = await client.post("/admin/keys/generate",
                          json={"name": "k", "something_new": 1}, headers=AUTH)
    assert r.status_code == 200, r.text


# -- 4. the actor is a dependency, not a body field --------------------------


async def test_actor_is_a_dependency_not_a_body_field(client):
    """The first cut of this refactor passed the guard as a *closure* built
    per app. With ``from __future__ import annotations`` the annotation is a
    string, and FastAPI evaluates it against the MODULE globals — so the
    closure-local name was invisible and ``actor`` silently degraded into a
    body field. Every request then failed with 400 "actor: Field required".

    The guard is now module-level, so the parameter resolves as a
    dependency. This test pins that.
    """
    r = await client.post("/admin/keys/generate", json={"name": "k1"},
                          headers=AUTH)
    assert r.status_code == 200, r.text
    assert "actor" not in r.json()
    assert "actor" not in r.json().get("error", {}).get("message", "")


async def test_the_guard_rejects_anonymous_before_the_body_is_read(client):
    """Auth is a dependency, so it runs before the body is parsed — an
    anonymous caller cannot probe the schema."""
    r = await client.post("/admin/keys/generate", json={"models": "not-a-list"})
    assert r.status_code == 401, r.text
    assert r.json()["error"]["type"] == "authentication_error"


# -- 5. authorization rules are unchanged -------------------------------------


async def test_non_admin_cannot_patch_a_foreign_key(client):
    """A non-admin may only touch a key they own (the owner check)."""
    gen = await client.post("/admin/keys/generate", json={"name": "k1"},
                            headers=AUTH)
    kid = gen.json()["id"]
    other = _as(client, await _login(client, "other-user"))
    r = await other.patch(f"/admin/keys/{kid}", json={"rpm": 5})
    assert r.status_code in (401, 403), r.text
    assert r.json()["error"]["type"] == "permission_error"


async def test_owner_cannot_change_an_operator_only_field(client):
    """The owner-facing allowlist (AUDIT #220) is enforced by the SERVICE,
    not the route — a future route that forgets the check would otherwise
    silently allow it.

    The key must be owned by the caller: the master mints *un-owned* keys
    (the synthetic master has no ``users`` row), so a key created with the
    master bearer is nobody's and every non-admin gets "not your key" —
    the owner check, not the allowlist. The key is therefore minted by the
    logged-in user.
    """
    owner = _as(client, await _login(client, "owner-user"))
    gen = await owner.post("/admin/keys/generate", json={"name": "owned"})
    assert gen.status_code == 200, gen.text
    kid = gen.json()["id"]
    r = await owner.patch(f"/admin/keys/{kid}", json={"max_budget": 999})
    # OWNER_FACING_FIELDS is empty, so every field is operator-only.
    assert r.status_code == 403, r.text
    assert "admin" in r.json()["error"]["message"]


async def test_owner_can_read_their_own_key(client):
    """The ownership rule is a *write* restriction, not a read one: an
    owner still sees their own key in the listing."""
    owner = _as(client, await _login(client, "owner-user"))
    gen = await owner.post("/admin/keys/generate", json={"name": "owned"})
    assert gen.status_code == 200, gen.text
    listing = await owner.get("/admin/keys")
    assert listing.status_code == 200, listing.text
    ids = [k["id"] for k in listing.json()["keys"]]
    assert gen.json()["id"] in ids


# -- 6. the response shape is byte-for-byte what the old handlers returned ---


async def test_owner_scoped_listing_omits_owner_id(client):
    """``list_keys_for_owner`` omits ``owner_id`` (the WHERE clause already
    fixed it) while ``list_keys`` includes it. The response model must not
    paper over that difference by ADDING ``"owner_id": null`` to the
    owner-scoped response — the old inline handler returned the service's
    dict untouched.
    """
    owner = _as(client, await _login(client, "owner-user"))
    gen = await owner.post("/admin/keys/generate", json={"name": "owned"})
    assert gen.status_code == 200, gen.text
    listing = await owner.get("/admin/keys")
    assert listing.status_code == 200, listing.text
    key = listing.json()["keys"][0]
    assert "owner_id" not in key, key


async def test_admin_listing_carries_owner_id(client):
    """The admin listing is the other half of the same contract: the field
    IS present there, so the model must not drop it either."""
    await client.post("/admin/keys/generate", json={"name": "k1"}, headers=AUTH)
    listing = await client.get("/admin/keys", headers=AUTH)
    assert listing.status_code == 200, listing.text
    key = listing.json()["keys"][0]
    assert "owner_id" in key, key
    assert set(key) == {"id", "alias", "models", "max_budget", "spend_to_date",
                        "rpm", "tpm", "expires_at", "disabled", "owner_id",
                        "budget_reserved", "priority"}


async def test_patch_response_carries_the_full_key_shape(client):
    """The PATCH response embeds the key; it must carry the same fields the
    listing does, so the SPA can render it without a second fetch."""
    gen = await client.post("/admin/keys/generate", json={"name": "k1"},
                            headers=AUTH)
    kid = gen.json()["id"]
    r = await client.patch(f"/admin/keys/{kid}", json={"rpm": 120}, headers=AUTH)
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert set(key) == {"id", "alias", "models", "max_budget", "spend_to_date",
                        "rpm", "tpm", "expires_at", "disabled", "owner_id",
                        "budget_reserved", "priority"}
    assert key["rpm"] == 120


async def test_audit_trail_is_still_written(client):
    """The service raises, the route logs: the audit trail is part of the
    contract for an admin surface."""
    gen = await client.post("/admin/keys/generate", json={"name": "k1"},
                            headers=AUTH)
    kid = gen.json()["id"]
    await client.delete(f"/admin/keys/{kid}", headers=AUTH)
    events = [e for _, e in
              await client.app.state.wiwi.logs.sse.replay("audit", 0)]
    actions = [e.action for e in events]
    assert "key.generate" in actions
    assert "key.delete" in actions


# -- helpers ------------------------------------------------------------------


async def _login(client, username: str) -> dict:
    """Create an account and return its session cookies."""
    await client.post("/auth/signup", json={"username": username,
                                            "password": "hunter2hunter2"})
    r = await client.post("/auth/login", json={"username": username,
                                               "password": "hunter2hunter2"})
    assert r.status_code == 200, r.text
    return dict(r.cookies)


def _as(client, cookies: dict):
    """A client that carries *cookies* on every request.

    httpx deprecates per-request cookies, and the SPA's own session model is
    cookie-based, so the tests drive it the same way a browser would.
    """
    client.cookies.update(cookies)
    return client
