"""Round 105 — playground bearer-cache lifetime, per-actor key ownership, and
the Cline OAuth callback URL's scheme.

The Playground wrapper (round 103) shipped two defects:

1. Its owner -> plaintext key cache was only ever written, read, and popped
   when its own revalidation failed. Nothing cleared it on logout, on user
   disable, or at shutdown, and nothing bounded it, so a plaintext key outlived
   the session that created it and the dict grew monotonically with distinct
   owner ids.

2. ``owner_id`` was ``None if role == "admin" else actor.id``, so every admin
   shared one virtual key, one budget, and one per-owner cap. A sixth admin
   using the Playground retired a key another admin was still using, and every
   mint overwrote the single ``None`` cache slot.

The synthetic master actor keeps an un-owned key deliberately: it has no
``users`` row, and the auth owner check fails closed on a missing owner, so an
owned master key would never authenticate.

Separately, ``_request_base`` composed the Cline auto-connect ``callback_url``
from ``request.url.scheme``. That is the same untrusted input as the session
cookie's ``Secure`` flag (round 106, ``tests/test_fix_round106.py``): behind an
external TLS terminator the scheme is http, so the authorization code came back
over plaintext.
"""

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.auth.users import UserInfo
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import _key_owner_id, _PgBearerCache, create_app

MASTER = "sk-wiwi-master-test"


def _config(tmp_path, trusted: list[str] | None = None,
            provider: str = "openai", name: str = "p1",
            model: str = "gpt-4o") -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name=name, provider=provider,
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider=name,
                                                            model=model))],
        general_settings=GeneralSettings(
            master_key=MASTER,
            trusted_proxies=trusted or [],
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"),
    )


async def _client(tmp_path, trusted: list[str] | None = None, **cfg):
    app = create_app(_config(tmp_path, trusted, **cfg))
    lm = LifespanManager(app)
    await lm.__aenter__()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    _orig_close = client.aclose

    async def _close_then_lifespan():
        try:
            await _orig_close()
        finally:
            await lm.__aexit__(None, None, None)

    client.aclose = _close_then_lifespan  # type: ignore[method-assign]
    return app, client


async def _make_admin(app, username: str) -> UserInfo:
    """Create a user and promote it to admin (create_user takes no role)."""
    st = app.state.wiwi
    u = await st.users.create_user(username, "password123")
    await st.users.patch(u.id, role="admin")
    return u


# -- the cache itself -------------------------------------------------------


def test_pg_bearer_cache_is_bounded():
    c = _PgBearerCache(max_entries=3)
    for i in range(10):
        c.set(f"u{i}", f"key-{i}")
    assert len(c) == 3
    # The oldest entries are evicted, the newest survive.
    assert c.get("u0") == ""
    assert c.get("u9") == "key-9"


def test_pg_bearer_cache_refreshes_on_hit():
    """An actively-used key must not be the one evicted."""
    c = _PgBearerCache(max_entries=2)
    c.set("a", "ka")
    c.set("b", "kb")
    assert c.get("a") == "ka"  # 'a' is now the most recently used
    c.set("c", "kc")
    assert c.get("b") == ""    # 'b' was the LRU entry
    assert c.get("a") == "ka"
    assert c.get("c") == "kc"


def test_pg_bearer_cache_drop_removes_one_entry():
    c = _PgBearerCache(max_entries=8)
    c.set("a", "ka")
    c.set("b", "kb")
    c.drop("a")
    assert c.get("a") == ""
    assert c.get("b") == "kb"


def test_pg_bearer_cache_replaces_on_duplicate_key():
    """A re-login for the same owner replaces, never duplicates."""
    c = _PgBearerCache(max_entries=8)
    c.set("a", "old")
    c.set("a", "new")
    assert len(c) == 1
    assert c.get("a") == "new"


# -- owner scoping ----------------------------------------------------------


def test_key_owner_id_gives_every_real_actor_its_own_key():
    real_admin = UserInfo(id="u0123456789abcdef", username="boss", role="admin")
    plain_user = UserInfo(id="ufedcba9876543210", username="joe", role="user")
    assert _key_owner_id(real_admin) == real_admin.id
    assert _key_owner_id(plain_user) == plain_user.id


def test_key_owner_id_leaves_the_synthetic_master_unowned():
    master = UserInfo(id="master", username="master", role="admin")
    assert _key_owner_id(master) is None


# -- lifetime: the cache must not outlive the session -----------------------


async def test_logout_drops_the_cached_playground_bearer(tmp_path):
    app, client = await _client(tmp_path)
    try:
        st = app.state.wiwi
        u = await st.users.create_user("joe", "password123")
        r = await client.post("/auth/login",
                              json={"username": "joe", "password": "password123"})
        assert r.status_code == 200
        assert st.pg_bearers.get(u.id), "login must have cached a playground key"

        await client.post("/auth/logout")
        assert st.pg_bearers.get(u.id) == "", (
            "logout left a plaintext playground key in memory")
    finally:
        await client.aclose()


async def test_disabling_a_user_drops_their_cached_playground_bearer(tmp_path):
    app, client = await _client(tmp_path)
    try:
        st = app.state.wiwi
        u = await st.users.create_user("joe", "password123")
        await client.post("/auth/login",
                          json={"username": "joe", "password": "password123"})
        assert st.pg_bearers.get(u.id)

        r = await client.patch(f"/admin/users/{u.id}", json={"disabled": True},
                               headers={"Authorization": f"Bearer {MASTER}"})
        assert r.status_code == 200
        assert st.pg_bearers.get(u.id) == "", (
            "disabling a user left their plaintext playground key in memory")
    finally:
        await client.aclose()


# -- ownership: two admins must not share one key or budget -----------------


async def test_two_admins_do_not_share_one_playground_key(tmp_path):
    app, client = await _client(tmp_path)
    try:
        st = app.state.wiwi
        a = await _make_admin(app, "bossone")
        b = await _make_admin(app, "bosstwo")

        await client.post("/auth/login",
                          json={"username": "bossone", "password": "password123"})
        await client.post("/auth/login",
                          json={"username": "bosstwo", "password": "password123"})

        ka, kb = st.pg_bearers.get(a.id), st.pg_bearers.get(b.id)
        assert ka and kb, "both admins must have a cached playground key"
        assert ka != kb, "two admins share one playground key, budget and cap"

        # ...and each key's owner is that admin, so the per-owner cap is
        # counted per admin rather than across all of them.
        assert await st.auth.authenticate(ka) is not None
        info = await st.auth.authenticate(kb)
        assert info is not None and info.owner_id == b.id
    finally:
        await client.aclose()


@pytest.mark.parametrize("count", [1, 3])
async def test_admin_playground_calls_do_not_collapse_to_one_key(tmp_path, count):
    """Repeated logins across admins must not leave them sharing one key."""
    app, client = await _client(tmp_path)
    try:
        st = app.state.wiwi
        admins = [await _make_admin(app, f"boss{i}") for i in range(count)]
        for adm in admins:
            await client.post(
                "/auth/login",
                json={"username": adm.username, "password": "password123"})
        keys = {st.pg_bearers.get(adm.id) for adm in admins}
        assert None not in keys
        assert len(keys) == count, "admins collapsed onto a shared playground key"
    finally:
        await client.aclose()


# -- the OAuth callback URL must not be built from the ASGI scheme -----------


async def _auto_connect_callback(tmp_path, trusted: list[str] | None,
                                 headers: dict[str, str]) -> str:
    """Return the callback_url embedded in the auto-connect auth_url."""
    from urllib.parse import parse_qs, urlparse

    _app, client = await _client(tmp_path, trusted, provider="cline",
                                 name="cline-prov", model="z-ai/glm-5.2")
    try:
        r = await client.post("/admin/cline/oauth/auto-connect",
                              headers={**headers,
                                       "Authorization": f"Bearer {MASTER}"},
                              json={"provider": "cline-prov"})
        assert r.status_code == 200, r.text
        qs = parse_qs(urlparse(r.json()["auth_url"]).query)
        callback = qs.get("callback_url", [""])[0]
        assert callback, r.text
        return callback
    finally:
        await client.aclose()


async def test_callback_url_uses_https_behind_a_trusted_proxy(tmp_path):
    """A TLS terminator in trusted_proxies must yield an https callback URL —
    an http one would send the OAuth authorization code back in cleartext."""
    cb = await _auto_connect_callback(
        tmp_path, trusted=["127.0.0.1/32"],
        headers={"X-Forwarded-Proto": "https"})
    assert cb.startswith("https://"), cb
    assert "/cline/oauth/callback" in cb


async def test_callback_url_stays_http_without_trusted_proxies(tmp_path):
    """Untrusted peers cannot mint an https callback either."""
    cb = await _auto_connect_callback(
        tmp_path, trusted=[], headers={"X-Forwarded-Proto": "https"})
    assert cb.startswith("http://"), cb
