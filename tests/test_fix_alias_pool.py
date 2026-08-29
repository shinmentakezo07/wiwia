"""Tests for the provider alias_id + cross-provider weighted round-robin pool
introduced alongside the per-provider model-id alias system.

Covers:
- alias_id field on ProviderDef (validation, default, whitespace rejection).
- Router.alias_to_provider is built at init from config.providers[*].alias_id.
- Router.resolve_group honors an alias_id by returning every deployment whose
  provider matches (so alias -> all models that provider serves).
- Cross-provider weighted round-robin rotation across providers when a model
  group spans 2+ provider accounts.
- Per-(provider, key) cooldown: when a provider's keys are all cooling, the
  cross-provider layer skips it and lands on the other provider.
- Admin API surface: PATCH /admin/providers accepts alias_id, validates
  uniqueness, returns it; GET /admin/providers and /admin/models include
  alias_id / provider_aliases.
"""

import textwrap
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from asgi_lifespan import LifespanManager

from wiwi.config import (
    ConfigError,
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
    load_config,
)
from wiwi.router.router import Deployment, Router

# ---------- helpers ----------------------------------------------------------


def _two_providers(**overrides) -> WiwiConfig:
    """Build a config with two providers each serving ``gpt-4o`` (so the
    router builds a cross-provider WRR pool for the model group)."""
    rs = RouterSettings(num_retries=1, allowed_fails=5, cooldown_time=0.05)
    for k, v in overrides.items():
        setattr(rs, k, v)
    return WiwiConfig(
        providers=[
            ProviderDef(
                name="p1", provider="openai", alias_id="shared-openai",
                keys=[KeyDef(label="a", key="k1", weight=2),
                      KeyDef(label="b", key="k2", weight=1)],
            ),
            ProviderDef(
                name="p2", provider="anthropic", alias_id="shared-anthropic",
                keys=[KeyDef(label="c", key="k3")],
            ),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p2", model="gpt-4o")),
        ],
        router_settings=rs,
    )


# ---------- config -----------------------------------------------------------


def test_alias_id_default_is_none():
    """Providers without alias_id get a None value."""
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[],
    )
    assert cfg.providers[0].alias_id is None


def test_alias_id_is_normalized():
    """Empty / whitespace strings are treated as 'not set'."""
    cfg = WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai", alias_id="   ",
                        keys=[KeyDef(label="a", key="k")]),
        ],
        model_list=[],
    )
    assert cfg.providers[0].alias_id is None


def test_alias_id_rejects_whitespace_inside():
    """Whitespace inside the alias is invalid (slashes etc. are allowed but
    spaces aren't, since aliases are used as-is in model names)."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="alias_id"):
        WiwiConfig(
            providers=[
                ProviderDef(name="p1", provider="openai", alias_id="has space",
                            keys=[KeyDef(label="a", key="k")]),
            ],
            model_list=[],
        )


def test_alias_id_must_be_unique_across_providers():
    """Two providers sharing the same alias_id is a config error.

    Goes through ``load_config`` so the pydantic ``ValidationError`` is
    rewrapped as our domain ``ConfigError``.
    """
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(textwrap.dedent("""\
            providers:
              - {name: p1, provider: openai, alias_id: dup, keys: [{label: a, key: k1}]}
              - {name: p2, provider: anthropic, alias_id: dup, keys: [{label: b, key: k2}]}
            model_list: []
        """))
        path = f.name
    with pytest.raises(ConfigError, match="alias_id"):
        load_config(path)


def test_alias_id_loaded_from_yaml(tmp_path: Path):
    """YAML -> WiwiConfig round-trips the alias_id field."""
    p = tmp_path / "wiwi.yaml"
    p.write_text(textwrap.dedent("""\
        providers:
          - name: p1
            provider: openai
            alias_id: shared-openai
            keys: [{label: a, key: k1}]
        model_list: []
    """))
    cfg = load_config(p)
    assert cfg.providers[0].alias_id == "shared-openai"


# ---------- router: alias map + resolve_group --------------------------------


def test_router_builds_alias_to_provider_map():
    r = Router(_two_providers())
    assert r.alias_to_provider == {
        "shared-openai": "p1",
        "shared-anthropic": "p2",
    }


def test_resolve_group_by_alias_returns_only_that_providers_deployments():
    """Requesting the alias for p1 returns only p1's deployments even though
    p2 also has a deployment in the same model group."""
    r = Router(_two_providers())
    name, deps = r.resolve_group("shared-openai")
    assert name == "shared-openai"
    assert {d.provider.name for d in deps} == {"p1"}
    name, deps = r.resolve_group("shared-anthropic")
    assert {d.provider.name for d in deps} == {"p2"}


def test_resolve_group_by_model_name_unchanged():
    """resolve_group still works for plain model_name lookups (the path
    used by every existing request)."""
    r = Router(_two_providers())
    name, deps = r.resolve_group("gpt-4o")
    assert name == "gpt-4o"
    assert {d.provider.name for d in deps} == {"p1", "p2"}


def test_resolve_group_unknown_alias_returns_none():
    r = Router(_two_providers())
    assert r.resolve_group("not-an-alias") == (None, [])


# ---------- cross-provider WRR ----------------------------------------------


def test_cross_provider_pool_rotates_across_providers():
    """pick_deployment rotates between providers for a multi-provider group,
    instead of weighted-shuffling on every call.

    Cross-provider rotation is weighted by each provider's *deployment* weights
    in the group (not per-key weights).  Each provider in _two_providers()
    has one deployment with weight=1, so we get exact 50/50 over an even
    number of picks.
    """
    r = Router(_two_providers())
    counts = {"p1": 0, "p2": 0}
    for _ in range(20):
        dep = r.pick_deployment(r.groups["gpt-4o"], _FakeCtx())
        assert dep is not None
        counts[dep.provider.name] += 1
    assert counts["p1"] == 10
    assert counts["p2"] == 10


def test_cross_provider_pool_weights_by_deployment_weight():
    """When a provider's deployment in the group has higher weight, the
    cross-provider WRR distributes traffic proportionally."""
    cfg = WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="p2", provider="anthropic",
                        keys=[KeyDef(label="b", key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p1", model="gpt-4o",
                                                    weight=3)),
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p2", model="gpt-4o",
                                                    weight=1)),
        ],
        router_settings=RouterSettings(num_retries=0, cooldown_time=0.05),
    )
    r = Router(cfg)
    counts = {"p1": 0, "p2": 0}
    for _ in range(40):
        dep = r.pick_deployment(r.groups["gpt-4o"], _FakeCtx())
        assert dep is not None
        counts[dep.provider.name] += 1
    # weights 3:1 -> 30/10 over 40 picks (smooth WRR converges immediately)
    assert counts["p1"] == 30
    assert counts["p2"] == 10


def test_single_provider_group_keeps_simple_shuffle():
    """Single-provider groups do not get a cross-provider pool, so picking
    returns the only available deployment every time."""
    cfg = WiwiConfig(
        providers=[ProviderDef(name="solo", provider="openai",
                               keys=[KeyDef(label="a", key="k"),
                                     KeyDef(label="b", key="k2")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="solo",
                                                            model="gpt-4o"))],
        router_settings=RouterSettings(num_retries=0, cooldown_time=0.05),
    )
    r = Router(cfg)
    for _ in range(10):
        dep = r.pick_deployment(r.groups["gpt-4o"], _FakeCtx())
        assert dep is not None and dep.provider.name == "solo"


def test_cross_provider_pool_skips_provider_whose_keys_are_all_cooling():
    """When every key on one provider is cooling, pick_deployment must skip
    that provider and land on the other (per-provider, per-key cooldown is
    preserved)."""
    r = Router(_two_providers())
    # cool p1's only healthy key -- the heavier-weighted one.  We need to
    # cool both p1 keys (a, b) so p1.healthy becomes False.
    p1 = r.providers["p1"]
    p1.keys[0].mark_cooling(60)
    p1.keys[1].mark_cooling(60)
    assert not p1.healthy
    for _ in range(10):
        dep = r.pick_deployment(r.groups["gpt-4o"], _FakeCtx())
        assert dep is not None
        assert dep.provider.name == "p2", "all p1 keys are cooling; p2 must serve"


# ---------- router: set_provider_alias / rebuild helpers ---------------------


def test_set_provider_alias_updates_map_and_account():
    r = Router(_two_providers())
    r.set_provider_alias("p1", "renamed-openai")
    assert r.alias_to_provider["renamed-openai"] == "p1"
    assert r.providers["p1"].alias_id == "renamed-openai"


def test_set_provider_alias_none_clears_entry():
    r = Router(_two_providers())
    r.set_provider_alias("p1", None)
    assert "shared-openai" not in r.alias_to_provider
    assert r.providers["p1"].alias_id is None


def test_set_provider_alias_rejects_duplicate():
    r = Router(_two_providers())
    with pytest.raises(ValueError, match="already used"):
        r.set_provider_alias("p1", "shared-anthropic")  # owned by p2


def test_rebuild_cross_provider_pools_picks_up_new_deployments():
    """The admin API mutates groups dict directly; rebuild must re-detect
    multi-provider groups so the WRR layer tracks live state."""
    r = Router(_two_providers())
    # add a third provider to the existing gpt-4o group -> pool should be
    # present in the rebuilt map.
    r._group_provider_rr.clear()
    if "solo" not in r.providers:
        solo_acct = type(r.providers["p1"])(name="solo", provider_type="openai",
                                            base_url="", keys=[])
        r.providers["solo"] = solo_acct
    r.groups["gpt-4o"].append(Deployment(group="gpt-4o",
                                        provider=r.providers["solo"],
                                        model_id="gpt-4o", weight=1))
    r.rebuild_cross_provider_pools()
    assert "gpt-4o" in r._group_provider_rr


# ---------- admin API surface ------------------------------------------------


class _FakeCtx:
    group: ClassVar[str] = "gpt-4o"
    attempts: ClassVar[list] = []
    started: float = 0.0


@pytest.fixture
async def client():
    from wiwi.server import app as app_mod

    cfg = _two_providers()
    cfg.general_settings = GeneralSettings(master_key="sk-wiwi-master-test",
                                            database_url="sqlite+aiosqlite:///:memory:")
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}


async def test_admin_get_providers_includes_alias_id_and_map(client):
    r = await client.get("/admin/providers", headers=AUTH)
    assert r.status_code == 200, r.text
    data = r.json()
    by_name = {p["name"]: p for p in data["providers"]}
    assert by_name["p1"]["alias_id"] == "shared-openai"
    assert by_name["p2"]["alias_id"] == "shared-anthropic"
    assert data["alias_to_provider"] == {
        "shared-openai": "p1", "shared-anthropic": "p2",
    }


async def test_admin_patch_provider_alias_roundtrip(client):
    """PATCH /admin/providers/{name} accepts and persists alias_id."""
    r = await client.patch("/admin/providers/p1", headers=AUTH,
                           json={"alias_id": "renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["alias_id"] == "renamed"
    # confirm in subsequent GET
    r2 = await client.get("/admin/providers", headers=AUTH)
    by_name = {p["name"]: p for p in r2.json()["providers"]}
    assert by_name["p1"]["alias_id"] == "renamed"
    assert r2.json()["alias_to_provider"]["renamed"] == "p1"
    assert "shared-openai" not in r2.json()["alias_to_provider"]


async def test_admin_patch_provider_alias_uniqueness(client):
    """Setting p1's alias to one already owned by p2 is a 409."""
    r = await client.patch("/admin/providers/p1", headers=AUTH,
                           json={"alias_id": "shared-anthropic"})
    assert r.status_code == 409
    assert "already used" in r.json()["error"]["message"]


async def test_admin_patch_provider_alias_clears(client):
    """PATCH with alias_id=null clears the alias entry."""
    await client.patch("/admin/providers/p1", headers=AUTH,
                      json={"alias_id": "renamed"})
    r = await client.patch("/admin/providers/p1", headers=AUTH,
                           json={"alias_id": None})
    assert r.status_code == 200
    assert r.json()["alias_id"] is None
    r2 = await client.get("/admin/providers", headers=AUTH)
    by_name = {p["name"]: p for p in r2.json()["providers"]}
    assert by_name["p1"]["alias_id"] is None
    assert "renamed" not in r2.json()["alias_to_provider"]


async def test_admin_models_includes_provider_aliases(client):
    r = await client.get("/admin/models", headers=AUTH)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["provider_aliases"] == {
        "shared-openai": "p1", "shared-anthropic": "p2",
    }
