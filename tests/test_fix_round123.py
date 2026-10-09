"""Round 123 — cross-provider WRR cursor self-heals against stale ``id()`` keys.

Finding covered (reproduced against the live router before fixing):

``_CrossProviderWRR._dep_cursors`` is keyed by ``id(deployment)`` (chosen over
``(provider, model_id)`` so a YAML combo listing the same pair twice does not
make the two entries share one deficit — AUDIT #307). That key is only valid
while the object is alive *and* still in the group. The shipped code keeps it
valid only through an implicit contract: every group-membership mutation calls
``Router.rebuild_cross_provider_pools``, which recreates the ``_CrossProviderWRR``
object (and so its cursor dict) from scratch. All eight current mutation sites
honour it, so there is no live bug — but the contract is unenforced and spread
across the admin API, the Cline default-model reconciler, the startup loader and
the provider importer. A future membership edit that forgot to rebuild would:

* leak one ``_dep_cursors`` entry per detached deployment for the process
  lifetime, and
* let CPython recycle the freed object's ``id()`` for the next ``Deployment``
  allocation, so a brand-new deployment silently inherits a dead one's deficit
  and is mis-served.

The fix makes the object self-healing: ``pick`` prunes ``_dep_cursors`` and
``_state`` to the group's live membership on every call. A detached deployment's
key is dropped; a merely-*cooling* deployment keeps its deficit (it is still a
member — a cooldown changes availability, not membership), preserving the
smooth-WRR "a temporarily-unhealthy peer is not starved after it recovers"
property. Steady-state routing is unchanged because every key is already live.

These tests pin all three properties: pruning, deficit retention for a cooling
member, and an unchanged steady-state distribution.
"""

from __future__ import annotations

from typing import ClassVar

from wiwi.config import (
    DeploymentParams,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.router.router import Deployment, Router


class _Ctx:
    """Minimal pick_deployment stand-in (mirrors test_fix_round99's _Ctx)."""

    group: ClassVar[str] = ""
    request_id: ClassVar[str] = "r"
    est_tokens: ClassVar[int] = 0
    metadata: ClassVar[dict] = {}
    auth = None
    session_id = None


def _two_provider_multi_dep() -> WiwiConfig:
    """One combo: two deployments on p1 (weights 1 and 1) plus one on p2."""
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
                                                    weight=1)),
            ModelEntry(model_name="combo",
                       wiwi_params=DeploymentParams(provider="p2", model="m-c",
                                                    weight=1)),
        ],
        router_settings=RouterSettings(num_retries=0, cooldown_time=0.05),
    )


def test_detached_deployment_cursor_key_is_pruned():
    """A deployment removed from the group must not keep a live WRR cursor key.

    Simulates the forbidden shape directly — mutate ``router.groups`` to drop a
    deployment WITHOUT calling ``rebuild_cross_provider_pools`` — then asserts
    the next pick prunes the orphaned ``id()`` key. Pre-fix the key lingered
    forever and a recycled ``id()`` could hand a new deployment a dead deficit.
    """
    r = Router(_two_provider_multi_dep())
    deps = r.groups["combo"]
    # Exercise the pool so the second-level cursor accumulates keys for both of
    # p1's deployments (m-a, m-b).
    for _ in range(50):
        r.pick_deployment(deps, _Ctx())
    rr = r._group_provider_rr["combo"]
    assert rr is not None
    keys_before = set(rr._dep_cursors)
    assert len(keys_before) >= 2, (
        f"expected cursors for both p1 deployments, got {keys_before}")

    # Detach m-b from the group without rebuilding (the forbidden shape).
    m_b = next(d for d in deps if d.model_id == "m-b")
    r.groups["combo"] = [d for d in deps if d.model_id != "m-b"]

    # The next pick must prune m-b's now-orphaned key.
    r.pick_deployment(r.groups["combo"], _Ctx())
    assert id(m_b) not in rr._dep_cursors, (
        "detached deployment's cursor key was not pruned — it would leak and "
        "its recycled id() could be inherited by a future Deployment")

    # And every surviving key maps to a deployment still in the group.
    live_ids = {id(d) for d in r.groups["combo"]}
    assert set(rr._dep_cursors) <= live_ids, (
        f"cursor keys outlived their deployment: {set(rr._dep_cursors) - live_ids}")


def test_cooling_member_retains_its_deficit_after_pruning():
    """Pruning must not evict a deployment that is only *cooling*, not detached.

    A cooldown changes availability, not membership. The nginx smooth-WRR keeps
    deficits precisely so a peer that was briefly unhealthy is not starved once
    it recovers; an over-eager prune to the *available* subset would reset that
    deployment's deficit to 0 and defeat the mechanism.
    """
    r = Router(_two_provider_multi_dep())
    deps = r.groups["combo"]
    rr = r._group_provider_rr["combo"]

    # Cool one of p1's deployments — it stays in the group but leaves ``avail``.
    m_a = next(d for d in deps if d.model_id == "m-a")
    m_a.cooldown_until = 9_999_999_999.0
    for _ in range(60):
        r.pick_deployment(deps, _Ctx())

    # m-a is a member, so its key survives pruning even though it is not served.
    # Seed a cursor entry for it explicitly to make the invariant unambiguous:
    # it must not be swept just because it is unavailable.
    rr._dep_cursors[id(m_a)] = 3.5
    r.pick_deployment(deps, _Ctx())
    assert id(m_a) in rr._dep_cursors, (
        "a cooling (still-member) deployment had its deficit pruned — the "
        "smooth-WRR recovery guarantee is broken")
    assert rr._dep_cursors[id(m_a)] == 3.5


def test_steady_state_distribution_unchanged_by_pruning():
    """The pruning pass is a no-op when every key is already live.

    Guards the fix against regressing the exact two-level ratio the combo tests
    already pin: p1's share (1+1)/3 split internally 1:1, p2 gets 1/3.
    """
    r = Router(_two_provider_multi_dep())
    counts: dict[str, int] = {}
    for _ in range(3000):
        d = r.pick_deployment(r.groups["combo"], _Ctx())
        counts[d.model_id] = counts.get(d.model_id, 0) + 1

    total = sum(counts.values())
    for mid in ("m-a", "m-b", "m-c"):
        share = counts.get(mid, 0) / total
        assert 0.28 <= share <= 0.37, (
            f"distribution drifted after pruning fix: {counts} (share {share:.3f})")

    # Every cursor key still maps to a live deployment — no leak accumulated.
    rr = r._group_provider_rr["combo"]
    live_ids = {id(d) for d in r.groups["combo"]}
    assert set(rr._dep_cursors) <= live_ids


def test_reattached_deployment_gets_a_fresh_cursor():
    """A brand-new Deployment at a (possibly recycled) id() starts from zero.

    The exact failure the fix closes: after a detach frees an object, CPython
    may reuse its ``id()`` for the next allocation. Pre-fix a new deployment at
    that address inherited the old one's deficit and was mis-served; post-fix
    the orphaned key is gone, so the new object builds its own deficit from 0.
    """
    r = Router(_two_provider_multi_dep())
    deps = r.groups["combo"]
    rr = r._group_provider_rr["combo"]

    m_a = next(d for d in deps if d.model_id == "m-a")
    # Give m-a a large deficit, then detach it.
    rr._dep_cursors[id(m_a)] = 100.0
    for _ in range(20):
        r.pick_deployment(deps, _Ctx())
    r.groups["combo"] = [d for d in deps if d.model_id != "m-a"]
    r.pick_deployment(r.groups["combo"], _Ctx())  # triggers the prune
    assert id(m_a) not in rr._dep_cursors

    # Re-attach a fresh Deployment (same provider/model) — it must not inherit
    # m_a's old deficit even if the allocator hands back the same id().
    fresh = Deployment(group="combo", provider=m_a.provider, model_id="m-a", weight=1)
    r.groups["combo"].append(fresh)
    r.rebuild_cross_provider_pools()  # production path rebuilds the pool object
    rr = r._group_provider_rr["combo"]
    # A freshly rebuilt pool starts empty; drive a few picks and confirm the new
    # deployment participates normally rather than being frozen out.
    seen: set[str] = set()
    for _ in range(400):
        d = r.pick_deployment(r.groups["combo"], _Ctx())
        seen.add(d.model_id)
    assert "m-a" in seen, "reattached deployment never served after rebuild"

