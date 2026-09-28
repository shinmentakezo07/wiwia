"""Round 104 — router defects, all reproduced against the pre-fix source.

Covered here:

* **#303** ``Deployment.retry_after_s`` reported the horizon of the globally
  oldest event across *both* windows instead of the saturated one, so a full
  ``rpm`` window with a stale cheap ``tpm`` event told the caller "retry in
  11 s" while the window was actually blocked for the rest of the minute.
* **#304** ``Router.resolve_group``'s ``for _ in range(8)`` hop budget cut off
  legitimate alias chains: an alias 8 links deep resolved to ``(None, [])`` and
  the caller got a 404 for a model the gateway could serve. ``seen`` already
  fails closed on a real cycle.
* **#305** the provider half of the ``cycle_every_n`` cadence never consumed
  its counters, so under skewed weights every provider saturated at
  ``>= cycle_n``, the exclusion set covered them all, and
  ``pick_deployment``'s relaxed pass made the cadence a permanent no-op —
  the same defect AUDIT #226 fixed at the key level.
* **#306** ``weight`` accepted ``0``/negative values on both ``KeyDef`` and
  ``DeploymentParams``. Smooth WRR *adds* every candidate's weight each round,
  so a negative entry starves its peers while absorbing 100 % of traffic; the
  admin API already clamps to ``>= 1``.
* **#307** ``_CrossProviderWRR``'s second-level cursor was keyed by
  ``(provider, model_id)``, so a group that lists the same provider/model pair
  twice (reachable through YAML ``model_list``; only the admin attach endpoint
  dedupes) made both entries share one deficit and permanently starved the
  second one.
"""

from __future__ import annotations

import itertools
import random
import time

import pytest
from pydantic import ValidationError

from wiwi.config import (
    DeploymentParams,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.ir.types import Request
from wiwi.router.router import (
    Deployment,
    Router,
    _DepEvent,
    execute_with_retries,
)


def _two_provider_config(*, weights: tuple[int, int], cycle: int) -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="pA", provider="openai",
                        keys=[KeyDef(label="ka", key="sk-aaaaaaaaaaaaaaaa")]),
            ProviderDef(name="pB", provider="openai",
                        keys=[KeyDef(label="kb", key="sk-bbbbbbbbbbbbbbbb")]),
        ],
        model_list=[
            ModelEntry(model_name="g",
                       wiwi_params=DeploymentParams(provider="pA", model="mA",
                                                    weight=weights[0])),
            ModelEntry(model_name="g",
                       wiwi_params=DeploymentParams(provider="pB", model="mB",
                                                    weight=weights[1])),
        ],
        router_settings=RouterSettings(num_retries=0, cycle_every_n=cycle),
    )


def _ctx() -> RequestContext:
    c = RequestContext(surface="chat",
                       ir_req=Request(model="g", messages=[]),
                       request_id="r")
    c.group = "g"
    return c


async def _provider_sequence(weights: tuple[int, int], cycle: int,
                             n: int = 120) -> tuple[list[str], Router]:
    random.seed(4)
    router = Router(_two_provider_config(weights=weights, cycle=cycle))

    async def call_one(dep, key, ctx):
        return dep.provider.name

    seq = [await execute_with_retries(router, _ctx(), call_one) for _ in range(n)]
    return seq, router


def _max_run(seq: list[str]) -> dict[str, int]:
    runs: dict[str, int] = {}
    cur = 1
    for a, b in itertools.pairwise(seq):
        if a == b:
            cur += 1
        else:
            runs[a] = max(runs.get(a, 0), cur)
            cur = 1
    runs[seq[-1]] = max(runs.get(seq[-1], 0), cur)
    return runs


# ---------------------------------------------------------------------------
# #305 — provider-level cycle_every_n must actually fire, and not saturate
# ---------------------------------------------------------------------------

async def test_provider_cycle_cadence_fires_under_skewed_weights():
    """20:1 weights, cycle_every_n=2: the heavy provider must yield.

    Pre-fix the provider counters were never consumed, so the exclusion covered
    both providers after a few requests, ``pick_deployment`` fell back to its
    relaxed pass, and the heavy provider served its full smooth-WRR burst
    (measured in a row: 20+). Post-fix the cadence caps the run.
    """
    seq, _router = await _provider_sequence((20, 1), cycle=2)
    runs = _max_run(seq)
    assert runs["pA"] <= 2, f"provider cadence did not fire: {runs}, seq={seq[:24]}"


async def test_provider_cycle_counters_do_not_saturate():
    """The counters bound themselves at ``cycle_n`` instead of climbing forever.

    Pre-fix every successful request incremented the counter and nothing ever
    cleared it, so after a handful of requests both providers sat at
    ``max(count)`` — the saturated shape that makes the exclusion a no-op.
    """
    _, router = await _provider_sequence((50, 1), cycle=3)
    counters = router._provider_consec
    assert counters, "expected the provider cadence to have run"
    assert max(counters.values()) <= 3, (
        f"provider counters saturated instead of being consumed: {counters}"
    )


async def test_provider_cadence_is_not_a_no_op():
    """Same weights, cycle 0 vs cycle 3 must differ observably."""
    off, _ = await _provider_sequence((20, 1), cycle=0)
    on, _ = await _provider_sequence((20, 1), cycle=3)
    assert _max_run(on)["pA"] < _max_run(off)["pA"], (
        f"cycle_every_n had no effect: off={_max_run(off)} on={_max_run(on)}"
    )


# ---------------------------------------------------------------------------
# #303 — retry_after_s must report the saturated window's horizon
# ---------------------------------------------------------------------------

def test_retry_after_uses_rpm_window_not_a_stale_tpm_event():
    """A full rpm window constrains admission even if a cheap old tpm event
    is the globally oldest event."""
    dep = Deployment(group="g", provider=None, model_id="m", rpm=3, tpm=10_000)
    now = time.monotonic()
    # one cheap request 50 s ago: only in the tpm window, long expired from rpm
    dep._window(True).add(_DepEvent(ts=now - 50.0, tokens=5))
    # rpm window is full with requests admitted just now
    for i in range(3):
        dep.reserve_slot(f"r{i}", 5)

    assert dep.rate_limited(now, est_tokens=5)
    # The rpm window clears in ~60 s, not in the ~10 s the stale tpm event
    # would suggest. 11 was the pre-fix answer.
    assert dep.retry_after_s(now) > 30, dep.retry_after_s(now)


def test_retry_after_uses_tpm_window_when_tpm_is_the_saturated_one():
    """rpm has room: the tpm window owns the horizon (~60 s for a fresh event)."""
    dep = Deployment(group="g", provider=None, model_id="m", rpm=10, tpm=100)
    dep.reserve_slot("a", 100)  # saturates tpm only
    assert dep.rate_limited(est_tokens=1)
    assert dep.retry_after_s() > 30


def test_retry_after_is_one_when_no_window_is_saturated():
    dep = Deployment(group="g", provider=None, model_id="m", rpm=10, tpm=1000)
    dep.reserve_slot("a", 5)
    assert dep.retry_after_s() == 1


# ---------------------------------------------------------------------------
# #304 — alias chains longer than the old 8-hop budget must resolve
# ---------------------------------------------------------------------------

def _alias_router(alias: dict[str, str]) -> Router:
    return Router(WiwiConfig(
        providers=[ProviderDef(name="p", provider="openai",
                               keys=[KeyDef(label="k", key="sk-aaaaaaaaaaaaaaaa")])],
        model_list=[ModelEntry(model_name="real",
                               wiwi_params=DeploymentParams(provider="p", model="m"))],
        router_settings=RouterSettings(model_group_alias=alias),
    ))


@pytest.mark.parametrize("hops", [7, 8, 12])
def test_long_alias_chain_resolves(hops: int):
    alias = {f"a{i}": f"a{i + 1}" for i in range(hops)}
    alias[f"a{hops}"] = "real"
    name, deps = _alias_router(alias).resolve_group("a0")
    assert name == "real", f"{hops}-hop chain resolved to {name!r}"
    assert len(deps) == 1


def test_long_alias_cycle_still_fails_closed():
    """Removing the hop budget must not let a long cycle resolve arbitrarily."""
    alias = {f"c{i}": f"c{i + 1}" for i in range(4)}
    alias["c4"] = "c0"
    assert _alias_router(alias).resolve_group("c0") == (None, [])


# ---------------------------------------------------------------------------
# #306 — non-positive weights are rejected at config load
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("weight", [0, -1])
def test_nonpositive_key_weight_rejected(weight: int):
    with pytest.raises(ValidationError):
        KeyDef(label="a", key="sk-aaaaaaaaaaaaaaaa", weight=weight)


@pytest.mark.parametrize("weight", [0, -3])
def test_nonpositive_deployment_weight_rejected(weight: int):
    with pytest.raises(ValidationError):
        DeploymentParams(provider="p", model="m", weight=weight)


def test_default_weights_still_accepted():
    assert KeyDef(label="a", key="sk-aaaaaaaaaaaaaaaa").weight == 1
    assert DeploymentParams(provider="p", model="m").weight == 1


# ---------------------------------------------------------------------------
# #307 — duplicate (provider, model) pairs must rotate, not starve
# ---------------------------------------------------------------------------

def _duplicate_pair_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="pA", provider="openai",
                        keys=[KeyDef(label="ka", key="sk-aaaaaaaaaaaaaaaa")]),
            ProviderDef(name="pB", provider="openai",
                        keys=[KeyDef(label="kb", key="sk-bbbbbbbbbbbbbbbb")]),
        ],
        model_list=[
            ModelEntry(model_name="g",
                       wiwi_params=DeploymentParams(provider="pA", model="m", weight=3)),
            ModelEntry(model_name="g",
                       wiwi_params=DeploymentParams(provider="pA", model="m", weight=1)),
            ModelEntry(model_name="g",
                       wiwi_params=DeploymentParams(provider="pB", model="mB", weight=1)),
        ],
        router_settings=RouterSettings(),
    )


def test_duplicate_provider_model_pair_rotates():
    """The second identical (provider, model) entry must still get traffic.

    Pre-fix both entries shared one ``(provider, model_id)`` cursor, so the
    second could never win the ``max`` — it was permanently starved while the
    first served double its share.
    """
    router = Router(_duplicate_pair_config())
    group = router.groups["g"]
    first, second, _other = group
    assert first.model_id == second.model_id and first.provider is second.provider
    assert first is not second, "expected two distinct Deployment objects"

    random.seed(5)
    picked = [router.pick_deployment(group, _ctx()) for _ in range(200)]
    assert picked.count(first) > 0 and picked.count(second) > 0, (
        "duplicate deployment starved: "
        f"first={picked.count(first)} second={picked.count(second)}"
    )
    assert 0 not in (picked.count(first), picked.count(second))
