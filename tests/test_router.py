"""Router tests: WRR key pools, cooldowns, retries, fallbacks."""

import asyncio
from typing import ClassVar

from wiwi.config import (
    DeploymentParams,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.providers.base import WiwiError
from wiwi.router.router import Router


def _config(**router_overrides) -> WiwiConfig:
    rs = RouterSettings(num_retries=1, allowed_fails=2, cooldown_time=0.05)
    for k, v in router_overrides.items():
        setattr(rs, k, v)
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="k1", weight=3),
                              KeyDef(label="b", key="k2", weight=1)]),
            ProviderDef(name="p2", provider="anthropic",
                        keys=[KeyDef(label="c", key="k3")]),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p2", model="gpt-4o")),
            ModelEntry(model_name="claude",
                       wiwi_params=DeploymentParams(provider="p2", model="claude-x")),
        ],
        router_settings=rs,
    )


def test_wrr_distribution():
    r = Router(_config())
    pool = r.providers["p1"]

    async def run():
        picks = {"a": 0, "b": 0}
        for _ in range(40):
            key, _ = await pool.pick_key()
            picks[key.label] += 1
        return picks

    picks = asyncio.run(run())
    # smooth WRR: exact 3:1 over a full cycle of total_weight picks
    assert picks["a"] == 30 and picks["b"] == 10


def test_key_cooling_on_429():
    r = Router(_config())
    pool = r.providers["p1"]
    key = pool.keys[0]
    key.mark_cooling(60)
    assert not key.available
    assert pool.keys[1].available
    key.recover()  # still cooling
    assert not key.available


def test_alias_resolution():
    cfg = _config(model_group_alias={"gpt-4": "gpt-4o"})
    r = Router(cfg)
    name, deps = r.resolve_group("gpt-4")
    assert name == "gpt-4o" and len(deps) == 2


def test_fallback_targets():
    cfg = _config(fallbacks={"claude": ["gpt-4o"]})
    r = Router(cfg)
    assert r.fallback_targets("claude") == ["gpt-4o"]


def test_execute_retries_then_fallbacks():
    """p1 fails -> retry must land on the OTHER deployment (p2) in the same group."""
    r = Router(_config())
    calls = []

    async def call_one(dep, key, ctx):
        calls.append(dep.provider.name)
        if dep.provider.name == "p2":
            return "ok"
        raise WiwiError(500, "api_connection_error", "boom", retryable=True)

    class Ctx:
        group = "gpt-4o"
        attempts: ClassVar[list] = []
        started = 0.0

    # force first pick to p1 deterministically: run until p1 is picked first
    for _ in range(50):
        calls.clear()
        result = asyncio.run(_run(r, Ctx(), call_one))
        assert result == "ok"
        if calls[0] == "p1":
            break
    assert calls[0] == "p1" and "p2" in calls  # retry escaped failed deployment


def test_single_key_429_cools_and_stops():
    """One key, upstream 429 -> key cools; no healthy deployment -> clean error."""
    cfg = WiwiConfig(
        providers=[ProviderDef(name="solo", provider="openai",
                               keys=[KeyDef(label="only", key="k")])],
        model_list=[ModelEntry(model_name="m",
                               wiwi_params=DeploymentParams(provider="solo",
                                                            model="m"))],
        router_settings=RouterSettings(num_retries=3),
    )
    r = Router(cfg)
    calls = []

    async def call_one(dep, key, ctx):
        calls.append("hit")
        raise WiwiError(429, "rate_limit_error", "rl", retryable=True,
                        retry_after=30.0)

    class Ctx:
        group = "m"
        attempts: ClassVar[list] = []
        started = 0.0

    try:
        asyncio.run(_run(r, Ctx(), call_one))
        raised = False
    except WiwiError as e:
        raised = e.etype in ("rate_limit_error", "service_unavailable")
    assert raised
    assert len(calls) <= 2  # cooled key prevents hammering


def test_exhausted_provider_keys_fall_through_to_sibling_deployment():
    """Regression: a deployment whose provider has no available keys must not
    abort the attempt loop — retries should land on sibling deployments backed
    by other providers in the same group."""
    from unittest.mock import patch

    r = Router(_config(routing_strategy="least-busy"))  # deterministic: p1 dep first

    async def exhausted_pick(exclude_labels=None):
        return None, 5.0  # simulates last key cooling between pick and use

    calls = []

    async def call_one(dep, key, ctx):
        calls.append(dep.provider.name)
        return "ok"

    class Ctx:
        group = "gpt-4o"
        attempts: ClassVar[list] = []
        started = 0.0

    with patch.object(r.providers["p1"], "pick_key", side_effect=exhausted_pick):
        result = asyncio.run(_run(r, Ctx(), call_one))
    assert result == "ok"
    assert calls and all(name == "p2" for name in calls)


async def _run(r, ctx, call_one):
    from wiwi.router.router import execute_with_retries
    return await execute_with_retries(r, ctx, call_one)
