"""Round 116 — router load shedding, priority lanes and session affinity.

Three admission controls, all opt-in. Each test pins behaviour an operator would
notice: a request refused with a specific status and a usable ``Retry-After``; a
bulk request that cannot starve an interactive one; a session pin that survives a
healthy deployment and *only* a healthy one.

Accounting note. ``Deployment.inflight`` is owned by the gateway — incremented in
``_call`` for a unary round-trip and in the stream-pump wrapper until the last
delta — and read here by the router. ``pick_deployment`` deliberately does *not*
increment it, so a test drives it directly to model requests already upstream.
"""

import asyncio
import time
from typing import ClassVar

import pytest

from wiwi.auth.service import AuthInfo
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
    Deployment,
    ProviderAccount,
    ProviderKey,
    Router,
)


def _router(**router_overrides) -> Router:
    """A single-deployment group; the deployment factory stays the router's."""
    settings = RouterSettings(num_retries=0, allowed_fails=2, cooldown_time=0.05)
    for key, value in router_overrides.items():
        setattr(settings, key, value)
    return Router(WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k1")])],
        model_list=[ModelEntry(model_name="m",
                               wiwi_params=DeploymentParams(provider="p1", model="mm"))],
        router_settings=settings,
    ))


def _account() -> ProviderAccount:
    return ProviderAccount(name="p1", provider_type="openai",
                           base_url="https://api.openai.com",
                           keys=[ProviderKey(label="a", secret="k1")])


def _dep(**kwargs) -> Deployment:
    """A standalone deployment, not wired into any router."""
    return Deployment(group="m", provider=_account(), model_id="mm", **kwargs)


class Ctx:
    """Minimal duck-typed request context, mirroring tests/test_router.py."""

    group = "m"
    started = 0.0
    request_id = "req-1"
    est_tokens = 0
    attempts: ClassVar[list] = []
    session_id: str | None = None

    def __init__(self, auth: AuthInfo | None = None, session_id: str | None = None):
        self.metadata: dict = {}
        self.auth = auth
        self.session_id = session_id


def _key(lane: str | None = None, key_type: str = "virtual") -> AuthInfo:
    return AuthInfo(key_id="k", key_type=key_type, alias="k", priority=lane)


# --- no configuration means no change ---------------------------------------


def test_an_uncapped_deployment_is_never_shed():
    r = _router()
    d = _dep()
    d.inflight = 500
    assert r.pick_deployment([d], Ctx(_key())) is d


def test_the_router_default_applies_when_no_model_cap_is_declared():
    d = _dep()  # no max_inflight on the deployment itself
    r = _router(max_inflight=2)
    d.inflight = 1
    assert r.pick_deployment([d], Ctx(_key())) is d
    d.inflight = 2
    assert r.pick_deployment([d], Ctx(_key())) is None


def test_lanes_are_inert_when_none_are_configured():
    """A cap with no ``priority_lanes`` means the deployment's own cap governs."""
    r = _router(max_inflight=1)
    d = _dep()
    d.inflight = 1
    assert r.pick_deployment([d], Ctx(_key("any-lane"))) is None
    d.inflight = 0
    assert r.pick_deployment([d], Ctx(_key("any-lane"))) is d


# --- plain shedding ----------------------------------------------------------


def test_a_deployment_at_its_cap_is_refused_and_says_why():
    r = _router(max_inflight=2)
    d = _dep()
    assert r.pick_deployment([d], Ctx(_key())) is d  # 0 inflight: admitted
    assert r.pick_deployment([d], Ctx(_key())) is d  # 1 inflight: admitted

    # The gateway has now incremented twice, so the deployment is full.
    d.inflight = 2
    refused = Ctx(_key())
    assert r.pick_deployment([d], refused) is None
    # Recorded so the caller reports "at its concurrency limit" instead of the
    # indistinguishable "no healthy deployment".
    assert refused.metadata["shed_reason"] == "inflight"


def test_a_sibling_below_its_cap_is_preferred_over_refusing():
    r = _router(max_inflight=1, routing_strategy="least-busy")
    busy, idle = _dep(), _dep()
    busy.inflight = 1
    idle.inflight = 0
    assert r.pick_deployment([busy, idle], Ctx(_key())) is idle


def test_the_per_model_cap_overrides_the_router_default_in_both_directions():
    r = _router(max_inflight=8)
    strict = _dep(max_inflight=1)
    strict.inflight = 1
    assert r.pick_deployment([strict], Ctx(_key())) is None  # stricter sheds earlier

    loose = _dep(max_inflight=8)
    loose.inflight = 5
    assert r.pick_deployment([loose], Ctx(_key())) is loose  # looser still serves


def test_a_saturated_group_surfaces_503_with_retry_after():
    from wiwi.router.router import execute_with_retries

    r = _router(max_inflight=1, inflight_retry_after_s=0.25)
    r.groups["m"][0].inflight = 1  # as if one request were already upstream

    async def call_one(dep, key, ctx):
        raise AssertionError("must not reach the upstream when shed")

    with pytest.raises(WiwiError) as exc:
        asyncio.run(execute_with_retries(r, Ctx(_key()), call_one))
    # 503, not 429: a concurrency bound has no horizon, so telling a client
    # "rate limited" would send it elsewhere for something that is a full server
    # right now. 429 stays reserved for rpm/tmp horizons.
    assert exc.value.status == 503
    assert exc.value.retry_after == pytest.approx(0.25)


def test_an_rpm_cap_still_answers_429_not_503():
    """The two overload signals must not collapse into one."""
    from wiwi.router.router import execute_with_retries

    r = _router()
    d = _dep(rpm=1)
    r.groups["m"] = [d]

    async def call_one(dep, key, ctx):
        raise AssertionError("must not reach the upstream when capped")

    # One admitted request fills the rpm window; the gateway settles it, so the
    # second attempt is over the cap and must report 429 with a horizon.
    ctx = Ctx(_key())
    assert r.pick_deployment([d], ctx) is d
    with pytest.raises(WiwiError) as exc:
        asyncio.run(execute_with_retries(r, Ctx(_key()), call_one))
    assert exc.value.status == 429


# --- priority lanes ----------------------------------------------------------


def test_bulk_is_refused_before_interactive_on_the_same_deployment():
    """The exact sequence that makes lanes worth having.

    cap 4 split {"interactive": 0.5, "bulk": 0.25} -> interactive may hold 2,
    bulk 1. Bulk is refused while interactive is still admitted, and the
    deployment is *below its own cap* when it refuses bulk.
    """
    r = _router(max_inflight=4,
                priority_lanes={"interactive": 0.5, "bulk": 0.25},
                default_lane="bulk")
    d = _dep(max_inflight=4)
    bulk = Ctx(_key("bulk"))
    interactive = Ctx(_key("interactive"))

    # bulk: ceiling = max(1, ceil(4 * 0.25)) == 1
    d.inflight = 0
    assert r.pick_deployment([d], bulk) is d
    d.inflight = 1
    assert r.pick_deployment([d], bulk) is None
    assert d.inflight < 4  # refused well under the deployment's own cap

    # interactive: ceiling = ceil(4 * 0.5) == 2, so it may still take the
    # capacity bulk was refused.
    assert r.pick_deployment([d], interactive) is d
    d.inflight = 2
    assert r.pick_deployment([d], interactive) is None


def test_a_lane_is_always_admitted_at_least_one_slot():
    # ceil(2 * 0.01) == 0. Without max(1, ...) this lane would deadlock itself
    # out of a small pool and serve nothing at all.
    r = _router(max_inflight=2, priority_lanes={"whisper": 0.01},
                default_lane="whisper")
    d = _dep(max_inflight=2)
    d.inflight = 0
    assert r.pick_deployment([d], Ctx(_key("whisper"))) is d
    d.inflight = 1
    assert r.pick_deployment([d], Ctx(_key("whisper"))) is None


def test_an_unknown_lane_falls_back_to_the_default_lane():
    r = _router(max_inflight=4, priority_lanes={"interactive": 0.25},
                default_lane="interactive")
    d = _dep(max_inflight=4)
    d.inflight = 1  # at the default lane's ceiling of 1
    # A key carrying a lane the operator never configured must not silently
    # receive full capacity; it lands on the default lane's share.
    assert r.pick_deployment([d], Ctx(_key("typo-lane"))) is None


def test_an_unset_lane_lands_on_the_default_lane():
    r = _router(max_inflight=4, priority_lanes={"interactive": 0.25},
                default_lane="interactive")
    d = _dep(max_inflight=4)
    d.inflight = 1
    assert r.pick_deployment([d], Ctx(_key(None))) is None


def test_the_master_key_outranks_every_lane():
    r = _router(max_inflight=4,
                priority_lanes={"interactive": 0.25, "bulk": 0.25},
                default_lane="bulk")
    d = _dep(max_inflight=4)
    d.inflight = 3  # above bulk's ceiling of 1, below the deployment's own 4
    # An operator's request is a human at a console; a lane boundary must never
    # be what refuses it, even below the deployment's own cap.
    assert r.pick_deployment([d], Ctx(_key("bulk", key_type="master"))) is d
    assert r.pick_deployment([d], Ctx(_key("bulk"))) is None


# --- session affinity --------------------------------------------------------


def _affinity_router(cap: int = 8, **overrides) -> Router:
    settings = {"session_affinity": True, "session_affinity_ttl_s": 60.0,
                "max_inflight": cap, "routing_strategy": "simple-shuffle"}
    settings.update(overrides)
    r = _router(**settings)
    r.groups["m"] = [_dep(), _dep()]
    return r


def _kill(dep: Deployment) -> None:
    """Make *dep* unavailable the way a cooldown does."""
    dep.cooldown_until = time.monotonic() + 3600.0


def test_affinity_holds_a_healthy_session_on_its_deployment():
    r = _affinity_router()
    a, b = r.groups["m"]
    # Pin to `b`; with `b` pinned, repeated picks must keep returning `b`.
    r._affinity["s1"] = (id(b), time.monotonic() + 60.0)
    ctx = Ctx(session_id="s1")
    for _ in range(5):
        assert r.pick_deployment([a, b], ctx) is b


def test_affinity_drops_a_pin_into_a_cooling_deployment():
    r = _affinity_router()
    a, b = r.groups["m"]
    r._affinity["s1"] = (id(b), time.monotonic() + 60.0)
    _kill(b)
    # Never sticky into a cooling deployment: that is the exact failure affinity
    # exists to prevent, so the pin is ignored and re-pointed at the survivor.
    assert r.pick_deployment([a, b], Ctx(session_id="s1")) is a
    assert r._affinity["s1"][0] == id(a)


def test_affinity_drops_a_pin_above_this_lane_ceiling():
    r = _affinity_router(cap=2, priority_lanes={"bulk": 0.5}, default_lane="bulk")
    a, b = r.groups["m"]
    r._affinity["s1"] = (id(b), time.monotonic() + 60.0)
    b.inflight = 1  # at this lane's ceiling of 1, under the deployment's own 2
    chosen = r.pick_deployment([a, b], Ctx(_key("bulk"), session_id="s1"))
    assert chosen is a


def test_affinity_drops_an_expired_pin():
    r = _affinity_router()
    a, b = r.groups["m"]
    r._affinity["s1"] = (id(b), time.monotonic() - 1.0)
    # The pin is dropped rather than honoured: the entry is replaced by the
    # deployment this pick actually made.
    chosen = r.pick_deployment([a, b], Ctx(session_id="s1"))
    assert r._affinity["s1"][0] == id(chosen)
    assert r._affinity["s1"][1] > time.monotonic()


def test_affinity_off_leaves_pick_deployment_untouched():
    r = _router(max_inflight=1)
    d = _dep()
    r._affinity["s1"] = (id(d), time.monotonic() + 60.0)  # stale entry
    _kill(d)
    other = _dep()
    # With affinity off the stale pin must not resurrect a dead deployment.
    assert r.pick_deployment([d, other], Ctx(session_id="s1")) is other


def test_affinity_without_a_session_id_never_engages():
    r = _affinity_router()
    a, b = r.groups["m"]
    r._affinity["s1"] = (id(b), time.monotonic() + 60.0)
    _kill(a)
    ctx = Ctx()  # no session id
    ctx.auth = _key()
    assert r.pick_deployment([a, b], ctx) is b
    # Nothing new was pinned, and the pre-existing entry is left untouched
    # rather than being read: a request without a session must not consume or
    # extend anyone's pin.
    assert r._affinity == {"s1": r._affinity["s1"]}
    assert r._affinity["s1"][0] == id(b)


def test_a_pinned_deployment_is_still_excluded_after_failing():
    """Affinity is a preference among healthy candidates, not a retry policy."""
    r = _affinity_router()
    a, b = r.groups["m"]
    r._affinity["s1"] = (id(b), time.monotonic() + 60.0)
    # `b` already failed this request, so it is excluded; stickiness must not
    # send the retry straight back to the deployment that failed.
    assert r.pick_deployment([a, b], Ctx(session_id="s1"), exclude={id(b)}) is a
