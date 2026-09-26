"""Round 99 — combo (model-group) name resolution and intra-provider rotation.

Findings covered (all reproduced against the live app before fixing):

* A combo whose name collides with a provider ``alias_id`` is unreachable:
  ``resolve_group`` consulted the provider-alias map first, so the group was
  shadowed for routing *and* for every admin write (PATCH/POST/DELETE
  ``/admin/model-groups/…``). ``model_group_alias`` keeps its alias-first
  walk — pinned by ``test_admin_api.py`` and ``test_fix_round43.py``.
* ``DELETE /admin/model-groups/{name}/deployments`` resolves the name
  leniently, so a name that is an alias deletes a deployment from — and
  writes the DB row against — a *different* group than the caller named.
* ``POST /admin/model-groups/{alias_id}/deployments`` reported 201 while
  attaching the deployment to the *aliased* provider's group.
* ``_CrossProviderWRR.pick`` always returns the *first* available deployment
  of the chosen provider, so a combo with 2+ models on one provider sends
  that provider's entire share to the first one: the others never serve, and
  their ``weight`` (editable in the Combos UI) is silently ignored.

AUDIT #293 (resolve precedence), #294 (admin writes), #295 (intra-provider
rotation), #296/#297 (edit durability).
"""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

import wiwi.server.app as app_mod
from wiwi.config import (
    ConfigError,
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
    _validate,
)
from wiwi.router.router import Router

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _config(**router_overrides) -> WiwiConfig:
    rs = RouterSettings(num_retries=0, allowed_fails=5, cooldown_time=0.05)
    for k, v in router_overrides.items():
        setattr(rs, k, v)
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai", alias_id="shared",
                        keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="p2", provider="openai",
                        keys=[KeyDef(label="b", key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="grpA",
                       wiwi_params=DeploymentParams(provider="p1", model="m-a")),
            ModelEntry(model_name="grpB",
                       wiwi_params=DeploymentParams(provider="p1", model="m-b")),
        ],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=rs,
    )


class _Ctx:
    group = ""
    request_id = "r"
    est_tokens = 0


@pytest.fixture
async def client():
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


# ---------------------------------------------------------------------------
# 1. resolve_group must not let a provider alias shadow a real group
# ---------------------------------------------------------------------------


def test_provider_alias_does_not_shadow_same_named_group():
    """A group literally named after a provider alias_id stays routable.

    Pre-fix the provider-alias arm ran first, so ``resolve_group("shared")``
    returned p1's deployments from *grpA* and the group ``shared`` — created
    by the Combos page when the operator typed that name — was unreachable.
    """
    cfg = _config()
    r = Router(cfg)
    # The Combos page creates this group by POSTing its first deployment.
    r.groups["shared"] = [
        type(r.groups["grpA"][0])(group="shared", provider=r.providers["p2"],
                                  model_id="m-combo", weight=1)
    ]
    group, deps = r.resolve_group("shared")
    assert group == "shared", f"provider alias shadowed the group: {group!r}"
    assert [d.model_id for d in deps] == ["m-combo"]


def test_provider_alias_still_resolves_when_no_group_of_that_name_exists():
    """The alias arm must keep working for the plain 'alias of a provider' case."""
    r = Router(_config())
    group, deps = r.resolve_group("shared")
    assert group == "shared"
    assert {d.provider.name for d in deps} == {"p1"}
    assert sorted(d.model_id for d in deps) == ["m-a", "m-b"]


def test_model_group_alias_keeps_its_alias_first_walk():
    """``model_group_alias`` remains a redirect namespace that wins by name.

    Pinned product behaviour (``tests/test_admin_api.py``,
    ``tests/test_fix_round43.py``): the admin API refuses to *create* an alias
    over an existing group, and a cycle between two real groups fails closed.
    Only the provider-``alias_id`` arm moved behind the group lookup, because
    that arm returns other groups' deployments.
    """
    cfg = _config(model_group_alias={"grpA": "grpB"})
    r = Router(cfg)
    group, deps = r.resolve_group("grpA")
    assert group == "grpB", group
    assert [d.model_id for d in deps] == ["m-b"]


# ---------------------------------------------------------------------------
# 2. admin model-group writes must address the group the caller named
# ---------------------------------------------------------------------------


async def test_delete_deployment_rejects_an_alias_name(client):
    """DELETE must not resolve a name that is an alias to a different group.

    Pre-fix the handler used the lenient resolver, so
    ``DELETE /admin/model-groups/shared/deployments?provider=p1&model_id=m-a``
    removed *grpA*'s deployment and wrote the DB delete against ``grpA`` while
    echoing ``"group": "shared"`` — corrupting a group the caller never named.
    """
    c, app = client
    st = app.state.wiwi
    before = {g: [(d.provider.name, d.model_id) for d in ds]
              for g, ds in st.router.groups.items()}

    r = await c.delete("/admin/model-groups/shared/deployments"
                       "?provider=p1&model_id=m-a", headers=AUTH)
    assert r.status_code == 404, r.text
    after = {g: [(d.provider.name, d.model_id) for d in ds]
             for g, ds in st.router.groups.items()}
    assert after == before, "an aliased name mutated an unrelated group"


async def test_add_deployment_rejects_an_alias_name(client):
    """POST must not silently attach into the aliased provider's group."""
    c, app = client
    st = app.state.wiwi

    r = await c.post("/admin/model-groups/shared/deployments", headers=AUTH,
                     json={"group": "shared", "provider": "p2", "model_id": "m-z"})
    assert r.status_code == 400, r.text
    assert "shared" not in st.router.groups


async def test_patch_model_group_rejects_an_alias_name(client):
    """PATCH must not retarget a weight onto the aliased provider's group.

    Pre-fix ``PATCH /admin/model-groups/shared {"weights": {"p1/m-b": 9}}``
    answered 200 and set *grpB*'s weight, while the response claimed the
    group was ``shared``.
    """
    c, app = client
    st = app.state.wiwi

    r = await c.patch("/admin/model-groups/shared", headers=AUTH,
                      json={"weights": {"p1/m-b": 9}})
    assert r.status_code == 404, r.text
    assert "alias" in r.json()["error"]["message"]
    assert st.router.groups["grpB"][0].weight == 1, "unrelated group was mutated"


async def test_admin_writes_still_work_for_a_real_group_name(client):
    """The guard must not break the ordinary, non-aliased path."""
    c, app = client
    st = app.state.wiwi
    r = await c.patch("/admin/model-groups/grpB", headers=AUTH,
                      json={"weights": {"p1/m-b": 9}})
    assert r.status_code == 200, r.text
    assert st.router.groups["grpB"][0].weight == 9


# ---------------------------------------------------------------------------
# 3. intra-provider rotation inside a cross-provider pool
# ---------------------------------------------------------------------------


def _three_dep_config() -> WiwiConfig:
    """One combo: two deployments on p1 (weights 1 and 5) plus one on p2."""
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="p2", provider="openai",
                        keys=[KeyDef(label="b", key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="combo",
                       wiwi_params=DeploymentParams(provider="p1", model="m-a",
                                                    weight=1)),
            ModelEntry(model_name="combo",
                       wiwi_params=DeploymentParams(provider="p1", model="m-b",
                                                    weight=5)),
            ModelEntry(model_name="combo",
                       wiwi_params=DeploymentParams(provider="p2", model="m-c",
                                                    weight=1)),
        ],
        router_settings=RouterSettings(num_retries=0, cooldown_time=0.05),
    )


def test_cross_provider_pool_rotates_within_a_provider():
    """Every deployment in the pool must serve, not just each provider's first.

    Pre-fix ``_CrossProviderWRR.pick`` returned the first available deployment
    of the chosen provider, so p1's whole share went to ``m-a`` and ``m-b``
    (weight 5, editable in the Combos UI) was never selected.
    """
    r = Router(_three_dep_config())
    counts: dict[str, int] = {}
    for _ in range(600):
        d = r.pick_deployment(r.groups["combo"], _Ctx())
        counts[d.model_id] = counts.get(d.model_id, 0) + 1

    assert counts.get("m-b", 0) > 0, (
        f"second model of the same provider never served: {counts}")
    # p1's pool share is (1+5)/7 of traffic; within it, weights 1:5.
    p1_share = (counts["m-a"] + counts["m-b"]) / 600
    assert 0.80 <= p1_share <= 0.90, f"provider share wrong: {counts}"
    ratio = counts["m-b"] / max(1, counts["m-a"])
    assert 3.0 <= ratio <= 8.0, f"intra-provider weights not honoured: {counts}"


def test_cross_provider_pool_single_dep_per_provider_unchanged():
    """The classic 1-deployment-per-provider combo still splits evenly."""
    cfg = _config()
    r = Router(cfg)
    counts = {"p1": 0, "p2": 0}
    # p2 must serve the group too for a cross-provider pool to exist.
    r.groups["grpA"] = list(r.groups["grpA"]) + [
        type(r.groups["grpA"][0])(group="grpA", provider=r.providers["p2"],
                                  model_id="m-a2", weight=1)
    ]
    r.rebuild_cross_provider_pools()
    for _ in range(40):
        d = r.pick_deployment(r.groups["grpA"], _Ctx())
        counts[d.provider.name] += 1
    assert counts == {"p1": 20, "p2": 20}, counts


def test_within_provider_rotation_skips_cooldown_deployment():
    """A cooling deployment on the chosen provider hands over to its sibling."""
    r = Router(_three_dep_config())
    deps = r.groups["combo"]
    m_a = next(d for d in deps if d.model_id == "m-a")
    m_a.cooldown_until = 9_999_999_999.0
    seen = {r.pick_deployment(deps, _Ctx()).model_id for _ in range(200)}
    assert "m-a" not in seen, f"cooling deployment still selected: {seen}"
    assert "m-b" in seen, f"sibling never selected: {seen}"


# ---------------------------------------------------------------------------
# 4. config-level guard: an alias_id must not equal another provider's name
# ---------------------------------------------------------------------------


def test_alias_id_may_not_equal_another_provider_name():
    """``alias_id == <other provider name>`` makes that provider unreachable.

    ``resolve_group`` consults the alias map before a plain group lookup, and
    a provider is reachable by its own name in the deps view, so the alias
    silently captured the other account.
    """
    with pytest.raises((ConfigError, ValueError), match="alias_id"):
        load_config_from_dict({
            "providers": [
                {"name": "p1", "provider": "openai", "alias_id": "p2",
                 "keys": [{"label": "a", "key": "k1"}]},
                {"name": "p2", "provider": "openai",
                 "keys": [{"label": "b", "key": "k2"}]},
            ],
        })


def test_alias_id_may_equal_its_own_name():
    """A provider aliasing itself is harmless and stays legal."""
    cfg = load_config_from_dict({
        "providers": [
            {"name": "p1", "provider": "openai", "alias_id": "p1",
             "keys": [{"label": "a", "key": "k1"}]},
        ],
    })
    assert cfg.providers[0].alias_id == "p1"


def load_config_from_dict(raw: dict) -> WiwiConfig:
    """Validate an in-memory config dict through the same path as YAML."""
    return _validate(raw)


# ---------------------------------------------------------------------------
# 5. operator edits to a YAML-defined combo must survive a restart
# ---------------------------------------------------------------------------


def _yaml_combo_config(db_url: str) -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="p2", provider="openai",
                        keys=[KeyDef(label="b", key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="combo",
                       wiwi_params=DeploymentParams(provider="p1", model="m-a")),
            ModelEntry(model_name="combo",
                       wiwi_params=DeploymentParams(provider="p1", model="m-b")),
            ModelEntry(model_name="combo",
                       wiwi_params=DeploymentParams(provider="p2", model="m-c")),
        ],
        general_settings=GeneralSettings(master_key=MASTER, database_url=db_url),
        router_settings=RouterSettings(num_retries=0, cooldown_time=0.05),
    )


def _combo_state(app):
    st = app.state.wiwi
    return {d.model_id: d.weight for d in st.router.groups.get("combo", [])}


async def test_yaml_combo_weight_edit_survives_restart(tmp_path):
    """A weight edit on a YAML-defined group must persist.

    Such a deployment has no row in ``deployments`` (only admin-attached ones
    do), so ``update_deployment_weight``'s bare UPDATE matched nothing: the UI
    reported success, routing changed in memory, and the next restart silently
    reverted it (AUDIT #296).
    """
    db = tmp_path / "w.db"
    url = f"sqlite+aiosqlite:///{db}"

    app1 = app_mod.create_app(_yaml_combo_config(url))
    async with LifespanManager(app1):
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.patch("/admin/model-groups/combo", headers=AUTH,
                              json={"weights": {"p1/m-a": 9}})
            assert r.status_code == 200, r.text
            assert _combo_state(app1)["m-a"] == 9

    app2 = app_mod.create_app(_yaml_combo_config(url))
    async with LifespanManager(app2):
        assert _combo_state(app2)["m-a"] == 9, (
            "weight edit was dropped on restart")
        assert _combo_state(app2)["m-b"] == 1


async def test_yaml_combo_detach_survives_restart(tmp_path):
    """Detaching a YAML-defined deployment must persist.

    The delete was a hard DELETE against a table that never held the row, so
    the deployment reappeared on the next restart — the operator's detach
    silently undid itself (AUDIT #297).
    """
    db = tmp_path / "d.db"
    url = f"sqlite+aiosqlite:///{db}"

    app1 = app_mod.create_app(_yaml_combo_config(url))
    async with LifespanManager(app1):
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.delete("/admin/model-groups/combo/deployments"
                               "?provider=p1&model_id=m-b", headers=AUTH)
            assert r.status_code == 200, r.text
            assert "m-b" not in _combo_state(app1)

    app2 = app_mod.create_app(_yaml_combo_config(url))
    async with LifespanManager(app2):
        assert "m-b" not in _combo_state(app2), (
            "detached deployment came back after restart")
        assert sorted(_combo_state(app2)) == ["m-a", "m-c"]


async def test_reattaching_a_detached_yaml_deployment_clears_the_tombstone(tmp_path):
    """Re-attaching must undo the tombstone, not stay shadowed by it."""
    db = tmp_path / "r.db"
    url = f"sqlite+aiosqlite:///{db}"

    app1 = app_mod.create_app(_yaml_combo_config(url))
    async with LifespanManager(app1):
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            d = await c.delete("/admin/model-groups/combo/deployments"
                               "?provider=p1&model_id=m-b", headers=AUTH)
            assert d.status_code == 200, d.text
            a = await c.post("/admin/model-groups/combo/deployments", headers=AUTH,
                             json={"group": "combo", "provider": "p1",
                                   "model_id": "m-b", "weight": 3})
            assert a.status_code == 201, a.text
            assert _combo_state(app1)["m-b"] == 3

    app2 = app_mod.create_app(_yaml_combo_config(url))
    async with LifespanManager(app2):
        assert _combo_state(app2)["m-b"] == 3, (
            "re-attached deployment stayed tombstoned")
