"""Round 107 — the admin key-generate route was the last place still minting
un-owned keys for real admins.

``/admin/keys/generate`` resolved ``owner_id = None if actor.role == "admin"
else actor.id``. That is the round-105 defect (AUDIT #309) on a second path:
``AuthService.create_key`` enforces ``max_keys_per_user`` only for a non-NULL
owner, so a real admin could mint unbounded live credentials, and every admin
shared the single ``owner_id=None`` bucket.

The fix points the route at the same ``_key_owner_id`` helper the playground
mint uses (renamed from ``_pg_owner_id``): every real account owns its keys,
admins included; only the synthetic master (``_SYNTHETIC_MASTER_ID``, no
``users`` row) stays un-owned, because ``AuthService._lookup_db`` fails closed
on a missing owner row and an owned master key could never authenticate.
"""

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import _SYNTHETIC_MASTER_ID, create_app

MASTER = "sk-wiwi-master-test"


def _config(tmp_path, max_keys_per_user: int = 50) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER,
            max_keys_per_user=max_keys_per_user,
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"),
    )


async def _client(tmp_path, **cfg):
    app = create_app(_config(tmp_path, **cfg))
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


async def _admin_session(app, client, username: str) -> str:
    """Create an admin account, log in, return its user id."""
    st = app.state.wiwi
    u = await st.users.create_user(username, "password123")
    await st.users.patch(u.id, role="admin")
    r = await client.post("/auth/login",
                          json={"username": username, "password": "password123"})
    assert r.status_code == 200, r.text
    return u.id


async def test_admin_generated_key_is_owned_by_that_admin(tmp_path):
    app, client = await _client(tmp_path)
    try:
        uid = await _admin_session(app, client, "boss")
        r = await client.post("/admin/keys/generate", json={"name": "ops"})
        assert r.status_code == 200, r.text

        info = await app.state.wiwi.auth.authenticate(r.json()["key"])
        assert info is not None
        assert info.owner_id == uid, (
            "admin-minted key is un-owned; it is exempt from max_keys_per_user "
            "and shared with every other admin")
    finally:
        await client.aclose()


async def test_admin_key_generate_is_capped(tmp_path):
    """A real admin is capped at max_keys_per_user, like any other owner."""
    app, client = await _client(tmp_path, max_keys_per_user=2)
    try:
        st = app.state.wiwi
        uid = await _admin_session(app, client, "boss")
        # Login already mints one playground key under this owner, so the
        # ceiling is shared exactly as it is for any other account.
        live = await st.auth.count_keys(owner_id=uid)
        assert live == 1, live
        r = await client.post("/admin/keys/generate", json={"name": "k0"})
        assert r.status_code == 200, r.text
        assert await st.auth.count_keys(owner_id=uid) == 2

        r = await client.post("/admin/keys/generate", json={"name": "k-over"})
        assert r.status_code == 400, r.text
        assert "key limit reached" in r.json()["error"]["message"]
    finally:
        await client.aclose()


async def test_two_admins_keys_do_not_share_an_owner(tmp_path):
    app, client = await _client(tmp_path)
    try:
        st = app.state.wiwi
        ids = []
        for name in ("bossone", "bosstwo"):
            ids.append(await _admin_session(app, client, name))
            await client.post("/auth/logout")
        # Log both in again and mint from each session.
        keys = []
        for name, _uid in zip(("bossone", "bosstwo"), ids):
            await client.post("/auth/login",
                              json={"username": name, "password": "password123"})
            r = await client.post("/admin/keys/generate", json={"name": "ops"})
            assert r.status_code == 200, r.text
            keys.append(r.json()["key"])
            await client.post("/auth/logout")

        owners = set()
        for k in keys:
            info = await st.auth.authenticate(k)
            assert info is not None and info.owner_id is not None
            owners.add(info.owner_id)
        assert len(owners) == 2, "two admins share one owner bucket"
        assert await st.auth.count_keys(owner_id=None) == 0, (
            "admin keys still land in the un-owned pool")
    finally:
        await client.aclose()


async def test_master_key_holder_still_mints_un_owned_keys(tmp_path):
    """The synthetic master has no ``users`` row, so its key must stay
    un-owned — an owned one would fail ``_lookup_db``'s owner check closed."""
    app, client = await _client(tmp_path)
    try:
        r = await client.post("/admin/keys/generate", json={"name": "ops"},
                              headers={"Authorization": f"Bearer {MASTER}"})
        assert r.status_code == 200, r.text
        info = await app.state.wiwi.auth.authenticate(r.json()["key"])
        assert info is not None, "an owned master key would not authenticate"
        assert info.owner_id is None
        assert _SYNTHETIC_MASTER_ID == "master"
    finally:
        await client.aclose()
