"""Tests for per-provider round-robin toggle.

When round_robin=True (default): smooth weighted round-robin (nginx algorithm).
When round_robin=False: sequential selection — first available key in list order,
advancing only when the current key is unavailable.
"""

import asyncio

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
    RouterSettings,
    WiwiConfig,
)
from wiwi.router.router import Router

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _config(round_robin: bool = True) -> WiwiConfig:
    rs = RouterSettings(num_retries=0, allowed_fails=3, cooldown_time=0.05)
    return WiwiConfig(
        providers=[
            ProviderDef(
                name="p1",
                provider="openai",
                round_robin=round_robin,
                keys=[
                    KeyDef(label="a", key="k1", weight=3),
                    KeyDef(label="b", key="k2", weight=1),
                    KeyDef(label="c", key="k3", weight=1),
                ],
            ),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
        ],
        router_settings=rs,
    )


def _api_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                                keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


# -- config: round_robin field loaded from YAML -------------------------------

def test_round_robin_defaults_true():
    """Providers default to round_robin=True."""
    cfg = _config()
    assert cfg.providers[0].round_robin is True


def test_round_robin_can_be_disabled():
    """Providers can set round_robin=False."""
    cfg = _config(round_robin=False)
    assert cfg.providers[0].round_robin is False


# -- router: ProviderAccount carries round_robin ------------------------------

def test_router_account_has_round_robin():
    """The router's ProviderAccount gets round_robin from config."""
    r = Router(_config(round_robin=False))
    assert r.providers["p1"].round_robin is False


# -- pick_key: WRR mode (round_robin=True) -------------------------------------

def test_wrr_distribution():
    """With round_robin=True, keys are distributed 3:1:1 over a full cycle."""
    r = Router(_config(round_robin=True))
    pool = r.providers["p1"]

    async def run():
        picks = {"a": 0, "b": 0, "c": 0}
        for _ in range(50):
            key, _ = await pool.pick_key()
            picks[key.label] += 1
        return picks

    picks = asyncio.run(run())
    # smooth WRR: exact 3:1:1 over a full cycle of total_weight(5) picks
    assert picks["a"] == 30 and picks["b"] == 10 and picks["c"] == 10


# -- pick_key: sequential mode (round_robin=False) ----------------------------

def test_sequential_always_picks_first_key():
    """With round_robin=False, the first available key is always picked."""
    r = Router(_config(round_robin=False))
    pool = r.providers["p1"]

    async def run():
        picks = {"a": 0, "b": 0, "c": 0}
        for _ in range(20):
            key, _ = await pool.pick_key()
            picks[key.label] += 1
        return picks

    picks = asyncio.run(run())
    # All requests go to key "a" — the first key, always available
    assert picks == {"a": 20, "b": 0, "c": 0}


def test_sequential_advances_on_cooldown():
    """When the first key is cooling, sequential mode picks the next one."""
    r = Router(_config(round_robin=False))
    pool = r.providers["p1"]

    async def run():
        # Put key "a" in cooldown
        key_a = pool.get_key("a")
        assert key_a is not None
        key_a.mark_cooling(60.0)

        # Next pick should be key "b" (first available after "a")
        key, _ = await pool.pick_key()
        assert key.label == "b"

        # Key "c" should never be reached while "b" is available
        key2, _ = await pool.pick_key()
        assert key2.label == "b"

        return True

    assert asyncio.run(run())

def test_sequential_wraps_around():
    """Sequential mode wraps around the key list when keys recover."""
    r = Router(_config(round_robin=False))
    pool = r.providers["p1"]

    async def run():
        # Put keys "a" and "b" in cooldown
        pool.get_key("a").mark_cooling(60.0)
        pool.get_key("b").mark_cooling(60.0)

        # Should pick key "c" (first available after cursor at 0)
        key, _ = await pool.pick_key()
        assert key.label == "c"

        # Put "c" in cooldown too — all keys now cooling
        pool.get_key("c").mark_cooling(60.0)

        # Recover "a" only — should pick "a" (cursor wraps to find it)
        pool.get_key("a").status = "active"
        key2, _ = await pool.pick_key()
        assert key2.label == "a"

        return True

    assert asyncio.run(run())


# -- admin API: round_robin in provider response ------------------------------

@pytest.fixture
async def client():
    app = app_mod.create_app(_api_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


async def test_admin_api_returns_round_robin(client):
    """GET /admin/providers includes round_robin in the response."""
    r = await client.get("/admin/providers", headers=AUTH)
    assert r.status_code == 200
    p = r.json()["providers"][0]
    assert "round_robin" in p
    assert p["round_robin"] is True


async def test_admin_api_patch_round_robin(client):
    """PATCH /admin/providers/{name} can toggle round_robin."""
    r = await client.patch("/admin/providers/p1",
                           json={"round_robin": False}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["round_robin"] is False

    # Verify it's reflected in the listing too
    lst = await client.get("/admin/providers", headers=AUTH)
    assert lst.json()["providers"][0]["round_robin"] is False
