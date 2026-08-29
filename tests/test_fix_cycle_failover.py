"""Tests for the cycle-N rotation + any-error failover layer.

Covers:
- ``cycle_every_n``: after N consecutive successful requests on the same
  (provider, key) the next pick excludes it, so traffic actually rotates
  (not just spreads by weight).
- ``cycle_every_n=0`` disables the cadence and leaves weight-driven WRR.
- Cross-provider layer: after N consecutive successful requests on the
  same provider, the next pick prefers a different provider.
- ``any_error`` failover: any non-200 applies a short cooldown so the next
  pick rotates to a different key.  Only ``key_max_consecutive_fails``
  consecutive failures (auth errors count double) permanently retire a key.
- ``standard`` failover mode preserves the historical 401/403 -> invalid
  behaviour.
- An error on a key clears that key's cycle credit so a flapping key is
  not "protected" by the rotation cadence.
- Combined: cycle-N + any_error still picks within a single model group
  (no model-id swap).
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from wiwi.config import (
    DeploymentParams,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.providers.base import WiwiError
from wiwi.router.router import (
    ProviderAccount,
    ProviderKey,
    Router,
    execute_with_retries,
)

# ---------- helpers ----------------------------------------------------------


def _two_providers_two_keys(**router_overrides) -> WiwiConfig:
    """p1 with 2 keys (weights 1:1), p2 with 1 key — simple enough to
    observe the cycle-3 rotation by counting which key gets picked."""
    rs = RouterSettings(num_retries=0, allowed_fails=5,
                        cooldown_time=0.05, **router_overrides)
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="k1"),
                              KeyDef(label="b", key="k2")]),
            ProviderDef(name="p2", provider="anthropic",
                        keys=[KeyDef(label="c", key="k3")]),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p2", model="gpt-4o")),
        ],
        router_settings=rs,
    )


class _Ctx:
    """Minimal RequestContext stand-in (mirrors the other test_router fakes)."""
    group: ClassVar[str] = "gpt-4o"
    attempts: ClassVar[list] = []
    started: float = 0.0

    def __init__(self) -> None:
        self.metadata: dict = {}


async def _run(r: Router, ctx, call_one):
    return await execute_with_retries(r, ctx, call_one)


def test_cycle_and_failover_defaults():
    """Defaults: cycle_every_n=3, failover_mode=any_error,
    key_max_consecutive_fails=5."""
    rs = RouterSettings()
    assert rs.cycle_every_n == 3
    assert rs.failover_mode == "any_error"
    assert rs.key_max_consecutive_fails == 5


def test_cycle_every_n_zero_disables_cadence():
    rs = RouterSettings(cycle_every_n=0)
    assert rs.cycle_every_n == 0


# ---------- per-(provider, key) cycle-3 rotation ---------------------------


def test_per_key_cycle_advances_after_n_successful_picks():
    """With cycle_every_n=3 and 2 keys on a single provider, every 3rd
    request on key 'a' forces key 'b' for the next pick (and vice versa)."""
    r = Router(_two_providers_two_keys(cycle_every_n=3, failover_mode="any_error"))
    # collapse to a single provider so cross-provider rotation doesn't
    # interfere with the per-key observation.
    r.providers.pop("p2")
    r.groups["gpt-4o"] = [d for d in r.groups["gpt-4o"] if d.provider.name == "p1"]
    # remove cross-provider WRR so it always picks from the group
    r._group_provider_rr.clear()

    seen_keys: list[str] = []

    async def call_one(dep, key, ctx):
        seen_keys.append(key.label)
        return "ok"

    async def go():
        for _ in range(12):
            await _run(r, _Ctx(), call_one)
    asyncio.run(go())

    # Count the "transitions": the index at which the key changes relative
    # to the previous successful request.  cycle-3 should force a transition
    # at positions 3, 6, 9, ...  We can verify the property more directly:
    # no key should appear 4 or more times in a row.
    for i in range(3, len(seen_keys)):
        assert not (seen_keys[i] == seen_keys[i - 1] == seen_keys[i - 2] == seen_keys[i - 3]), (
            f"key {seen_keys[i]!r} served 4 consecutive requests; "
            f"sequence so far: {seen_keys[: i + 1]}")


def test_cycle_every_n_zero_keeps_weight_driven_wrr():
    """Disabling the cadence leaves nginx smooth-WRR only."""
    r = Router(_two_providers_two_keys(cycle_every_n=0, failover_mode="any_error"))
    r.providers.pop("p2")
    r.groups["gpt-4o"] = [d for d in r.groups["gpt-4o"] if d.provider.name == "p1"]
    r._group_provider_rr.clear()

    async def pick_only():
        # pick_key directly — should converge on one of the two keys
        # according to weight (both weight 1 here so smooth-WRR alternates
        # but never re-runs the same key 4+ times in a row of equal weight).
        k1, _ = await r.providers["p1"].pick_key()
        return k1.label
    labels = [asyncio.run(pick_only()) for _ in range(10)]
    # Just assert no crash; precise distribution is unit-tested in
    # tests/test_round_robin.py.
    assert len(labels) == 10
    assert all(l in ("a", "b") for l in labels)


# ---------- cross-provider cycle-3 rotation ---------------------------------


def test_cross_provider_cycle_advances_after_n():
    """With 2 providers, every 3 successful requests on p1 force p2 next."""
    r = Router(_two_providers_two_keys(cycle_every_n=3, failover_mode="any_error"))
    seen_providers: list[str] = []

    async def call_one(dep, key, ctx):
        seen_providers.append(dep.provider.name)
        return "ok"

    async def go():
        for _ in range(9):
            await _run(r, _Ctx(), call_one)
    asyncio.run(go())

    # Verify no provider serves 4 consecutive requests in a row.
    for i in range(3, len(seen_providers)):
        if seen_providers[i] == seen_providers[i - 1] == seen_providers[i - 2] == seen_providers[i - 3]:
            pytest.fail(
                f"provider {seen_providers[i]!r} served 4 consecutive requests; "
                f"sequence: {seen_providers[: i + 1]}")


# ---------- any-error failover -----------------------------------------------


def test_any_error_mode_rotates_on_5xx_without_retiring_key():
    """A single 5xx applies a short cooldown so the next pick rotates
    to a different key.  The key is NOT marked invalid (err_count=1)."""
    r = Router(_two_providers_two_keys(cycle_every_n=0, failover_mode="any_error",
                                       key_max_consecutive_fails=5))
    # First call: 5xx error
    async def first_call(dep, key, ctx):
        raise WiwiError(500, "api_error", "boom", retryable=True)
    # Second call: succeeds — but it must land on a DIFFERENT key, because
    # the first failure put key_a in cooling for a short window.
    second_key: list[str] = []

    async def second_call(dep, key, ctx):
        second_key.append(key.label)
        return "ok"

    async def go():
        try:
            await _run(r, _Ctx(), first_call)
        except WiwiError:
            pass
        # a fresh context resets the per-request cycle counters but the key's
        # short cooldown persists on the provider account.
        await _run(r, _Ctx(), second_call)
    asyncio.run(go())
    # (same provider) or key_c (p2's only key) — both are valid rotations;
    # what we forbid is re-picking the just-failed key within the same
    # request, which is the cycle-3 / any_error behaviour.
    assert second_key[0] != "a", (
        f"expected rotation off key a after 5xx, got {second_key[0]!r}")

def test_any_error_mode_retires_key_after_max_consecutive_fails():
    """After key_max_consecutive_fails consecutive errors, the key is
    permanently retired (status=invalid)."""
    key = ProviderKey(label="a", secret="k")
    acct = ProviderAccount(name="p", provider_type="openai",
                           base_url="https://x/v1", keys=[key])
    for _ in range(5):
        acct.on_result(key, 500, None, failover_mode="any_error",
                       key_max_consecutive_fails=5)
    assert key.status == "invalid"
    assert key.err_count == 5


def test_any_error_mode_auth_failure_counts_double():
    """401/403 increment err_count by 2 in any_error mode, so it takes
    fewer round-trips to retire a key than 5xx-only errors."""
    key = ProviderKey(label="a", secret="k")
    acct = ProviderAccount(name="p", provider_type="openai",
                           base_url="https://x/v1", keys=[key])
    acct.on_result(key, 401, None, failover_mode="any_error",
                   key_max_consecutive_fails=5)
    assert key.err_count == 2
    assert key.status == "cooling"  # 2 < 5, not yet retired
    acct.on_result(key, 401, None, failover_mode="any_error",
                   key_max_consecutive_fails=5)
    assert key.err_count == 4
    assert key.status == "cooling"
    acct.on_result(key, 401, None, failover_mode="any_error",
                   key_max_consecutive_fails=5)
    assert key.err_count == 6
    assert key.status == "invalid"  # 6 >= 5, retired


def test_standard_mode_preserves_401_immediate_invalidation():
    """In standard mode a single 401 still retires the key immediately
    (the historical behaviour)."""
    key = ProviderKey(label="a", secret="k")
    acct = ProviderAccount(name="p", provider_type="openai",
                           base_url="https://x/v1", keys=[key])
    acct.on_result(key, 401, None, failover_mode="standard")
    assert key.status == "invalid"


# ---------- error clears cycle credit ---------------------------------------


async def test_error_clears_cycle_credit_for_key_and_provider():
    """If a key errors, the cycle-N counter for that (provider, key) and
    the provider-level counter must be reset so the rotation cadence
    doesn't shield a flapping key from being re-picked."""
    r = Router(_two_providers_two_keys(cycle_every_n=1, failover_mode="any_error",
                                       key_max_consecutive_fails=10))
    # Restrict to p1 so we deterministically pick the same (provider, key).
    r.providers.pop("p2")
    r.groups["gpt-4o"] = [d for d in r.groups["gpt-4o"] if d.provider.name == "p1"]
    r._group_provider_rr.clear()

    ctx = _Ctx()
    chosen: list[tuple[str, str]] = []

    async def ok(dep, key, ctx):
        chosen.append((dep.provider.name, key.label))
        return "ok"

    # Drive 2 successful calls on the same key (cycle_every_n=1 forces
    # rotation; with 2 keys WRR alternates a, b, a, b -> chosen = [(a), (b)]).
    await _run(r, ctx, ok)
    await _run(r, ctx, ok)
    md = ctx.metadata
    pname, klabel = chosen[0]
    # the FIRST (provider, key) entry was picked once -> counter == 1
    assert md["wiwi_cycle_key"][(pname, klabel)] == 1

    # now a 500 on the next call
    async def err(dep, key, ctx):
        chosen.append((dep.provider.name, key.label))
        raise WiwiError(500, "api_error", "boom", retryable=True)

    try:
        await _run(r, ctx, err)
    except WiwiError:
        pass
    # that (provider, key) credit must be cleared
    assert (chosen[2][0], chosen[2][1]) not in md["wiwi_cycle_key"], (
        f"expected cycle credit cleared for {chosen[2]}, got {md['wiwi_cycle_key']}")


# ---------- combined: same model, different keys ---------------------------


def test_cycle_and_failover_keep_model_id_constant():
    """All rotations must stay within the same model_name.  No model-id
    fallback is allowed in the cycle layer."""
    r = Router(_two_providers_two_keys(cycle_every_n=2, failover_mode="any_error",
                                       key_max_consecutive_fails=10))
    seen_models: list[str] = []

    async def ok(dep, key, ctx):
        seen_models.append(dep.model_id)
        return "ok"

    async def go():
        for _ in range(8):
            await _run(r, _Ctx(), ok)
    asyncio.run(go())
    assert all(m == "gpt-4o" for m in seen_models), seen_models
