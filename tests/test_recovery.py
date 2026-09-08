"""Recovery primitives + HealthHealer: backoff, circuits, verdicts, probes, probation."""

import time

import httpx
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    HealerSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.core.recovery import (
    Backoff,
    CircuitBreaker,
    HealthHealer,
    ProbeVerdict,
    probe_verdict,
)


class TestBackoff:
    def test_monotone_in_attempt(self):
        b = Backoff(base_s=0.5, cap_s=30.0)
        delays = [b.delay(i) for i in range(6)]
        assert delays == sorted(delays)
        assert delays[0] == 0.5
        assert delays[3] == 4.0

    def test_cap_clamps(self):
        b = Backoff(base_s=0.5, cap_s=2.0)
        assert b.delay(10) == 2.0

    def test_jitter_bounds(self):
        b = Backoff(base_s=0.5, cap_s=30.0, jitter_s=0.25)
        for _ in range(20):
            assert 0.5 <= b.delay(0) <= 0.75

    def test_retry_after_floored_by_exponential_and_capped(self):
        b = Backoff(base_s=0.5, cap_s=30.0)
        assert b.delay(0, retry_after=10.0) == 10.0
        assert b.delay(0, retry_after=0.001) == 0.5
        assert b.delay(6, retry_after=99.0) == 30.0

    def test_negative_attempt_treated_as_zero(self):
        b = Backoff(base_s=0.5, cap_s=30.0)
        assert b.delay(-3) == 0.5

    def test_matches_router_inline_math(self):
        """Pins the exact expression this primitive replaces at the router's
        retry sleep: min(5.0, max(ra, 0.5 * 2**attempt)) + uniform(0, 0.25)."""
        b = Backoff(base_s=0.5, cap_s=5.0, jitter_s=0.25)
        expected = min(5.0, max(2.0, 0.5 * (2 ** 3)))  # 4.0
        d = b.delay(3, retry_after=2.0)
        assert expected <= d <= expected + 0.25


class TestCircuitBreaker:
    def test_trip_blocks_then_expires(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.trip("t")
        assert cb.blocked("t")
        now[0] += 61.0
        assert not cb.blocked("t")

    def test_streak_doubles_window(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.trip("t")
        now[0] += 61.0
        assert not cb.blocked("t")
        cb.trip("t")
        now[0] += 61.0   # second window is 120s: still blocked
        assert cb.blocked("t")
        now[0] += 61.0   # 122s past second trip: open again
        assert not cb.blocked("t")

    def test_cap(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=90.0, clock=lambda: now[0])
        cb.trip("t")            # 60s window
        now[0] += 61.0          # 1061
        cb.trip("t")            # 120 -> capped at 90 => until 1151
        now[0] += 89.0          # 1150: still blocked
        assert cb.blocked("t")
        now[0] += 2.0           # 1152: open
        assert not cb.blocked("t")

    def test_clear_resets(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.trip("t")
        cb.clear("t")
        assert not cb.blocked("t")
        assert cb.streak("t") == 0

    def test_mark_dead_is_permanent(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.mark_dead("t")
        now[0] += 10 ** 9
        assert cb.blocked("t")
        assert cb.dead("t")
        cb.clear("t")
        assert not cb.dead("t")

    def test_targets_are_independent(self):
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0)
        cb.mark_dead("a")
        assert cb.blocked("a")
        assert not cb.blocked("b")


def test_healer_settings_defaults():
    c = WiwiConfig()
    assert c.healer.enabled is False
    assert c.healer.tick_s == 30.0
    assert c.healer.probes_to_restore == 2
    assert c.healer.probation_weight == 0.5


def test_healer_settings_yaml_section():
    c = WiwiConfig.model_validate({"healer": {"enabled": True, "tick_s": 5}})
    assert c.healer.enabled is True
    assert c.healer.tick_s == 5.0


class TestProbeVerdict:
    def test_table(self):
        assert probe_verdict(200) is ProbeVerdict.HEALTHY
        assert probe_verdict(429) is ProbeVerdict.ALIVE_THROTTLED
        assert probe_verdict(401) is ProbeVerdict.CREDS_REJECTED
        assert probe_verdict(403) is ProbeVerdict.CREDS_REJECTED
        assert probe_verdict(400) is ProbeVerdict.CREDS_VALID_MODEL_BAD
        assert probe_verdict(404) is ProbeVerdict.CREDS_VALID_MODEL_BAD
        for s in (None, 408, 500, 502, 503, 504, 529, 418):
            assert probe_verdict(s) is ProbeVerdict.UNREACHABLE


def test_parse_retry_after():
    from wiwi.core.recovery import parse_retry_after
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("1.5") == 1.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after("") is None


def _router_config(n_keys: int = 2) -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label=f"k{i}", key=f"secret{i}")
                              for i in range(n_keys)]),
            ProviderDef(name="p2", provider="openai",
                        keys=[KeyDef(label="p2k", key="p2secret")]),
        ],
        model_list=[
            ModelEntry(model_name="g",
                       wiwi_params=DeploymentParams(provider="p1", model="m")),
            ModelEntry(model_name="g",
                       wiwi_params=DeploymentParams(provider="p2", model="m2")),
        ],
    )


def _ctx(group: str = "g"):
    from wiwi.core.context import RequestContext
    from wiwi.ir.types import Message, Request, TextPart
    ir_req = Request(model=group,
                     messages=[Message(role="user", parts=[TextPart(text="hi")])])
    return RequestContext(surface="chat", ir_req=ir_req, group=group)


class TestProbation:
    async def test_probation_key_available_and_half_weight(self):
        from wiwi.router.router import Router
        r = Router(_router_config())
        acct = r.providers["p1"]
        acct.keys[0].mark_recovered()
        assert acct.keys[0].status == "probation"
        assert acct.keys[0].available
        picks = {"k0": 0, "k1": 0}
        for _ in range(100):
            k, _ = await acct.pick_key(probation_weight=0.5)
            picks[k.label] += 1
        # smooth WRR over effective weights (1.0 vs 0.5) is exactly 2:1 —
        # the healthy key gets twice the traffic of the probation one
        assert abs(picks["k1"] / picks["k0"] - 2.0) < 0.2

    async def test_probation_key_graduates_on_success(self):
        from wiwi.router.router import Router
        r = Router(_router_config())
        acct = r.providers["p1"]
        k = acct.keys[0]
        k.mark_recovered()
        acct.on_result(k, 200, None)
        assert k.status == "active"

    async def test_probation_key_demotes_on_failure(self):
        from wiwi.router.router import Router
        r = Router(_router_config())
        acct = r.providers["p1"]
        k = acct.keys[0]
        k.mark_recovered()
        acct.on_result(k, 500, None, failover_mode="any_error")
        assert k.status == "cooling"

    async def test_active_key_stays_active_on_success(self):
        from wiwi.router.router import Router
        r = Router(_router_config())
        acct = r.providers["p1"]
        acct.on_result(acct.keys[0], 200, None)
        assert acct.keys[0].status == "active"

    async def test_pick_deployment_prefers_non_probation(self):
        from wiwi.router.router import Router
        r = Router(_router_config())
        sick = r.groups["g"][0]
        sick.probation = True
        for _ in range(10):
            assert r.pick_deployment(r.groups["g"], _ctx()) is not sick

    async def test_pick_deployment_falls_back_to_probation_alone(self):
        from wiwi.router.router import Router
        r = Router(_router_config())
        dep = r.groups["g"][0]
        dep.probation = True
        healthy = r.groups["g"][1]
        assert r.pick_deployment(r.groups["g"], _ctx(),
                                 exclude={id(healthy)}) is dep

    async def test_deployment_graduates_via_execute_with_retries(self):
        from wiwi.router.router import Router, execute_with_retries
        r = Router(_router_config())
        dep = r.groups["g"][0]
        dep.probation = True
        healthy = r.groups["g"][1]
        healthy.cooldown_until = time.monotonic() + 999.0  # only the sick one can serve

        async def call_one(d, key, c):
            return "ok"

        await execute_with_retries(r, _ctx(), call_one)
        assert dep.probation is False

    def test_record_fail_clears_probation(self):
        from wiwi.router.router import Router
        r = Router(_router_config())
        dep = r.groups["g"][0]
        dep.probation = True
        dep.record_fail(allowed_fails=3, cooldown_time=30.0)
        assert dep.probation is False


PROBE_OK_BODY = {
    "id": "chatcmpl-probe", "object": "chat.completion", "model": "m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _sick_router():
    """Router with key0 cooling and the p1 deployment cooled down."""
    from wiwi.router.router import Router
    r = Router(_router_config())
    key = r.providers["p1"].keys[0]
    key.mark_cooling(999.0)
    dep = r.groups["g"][0]
    dep.cooldown_until = time.monotonic() + 999.0
    return r, key, dep


def _sick_key_router():
    """Router with ONLY key0 cooling; deployment healthy."""
    from wiwi.router.router import Router
    r = Router(_router_config())
    key = r.providers["p1"].keys[0]
    key.mark_cooling(999.0)
    return r, key, r.groups["g"][0]


def _sick_dep_router():
    """Router with ONLY the p1 deployment cooled down; keys healthy."""
    from wiwi.router.router import Router
    r = Router(_router_config())
    dep = r.groups["g"][0]
    dep.cooldown_until = time.monotonic() + 999.0
    return r, dep, r.providers["p1"].keys[1]


def _healer(router, **overrides) -> HealthHealer:
    s = HealerSettings(enabled=True, tick_s=0.05, min_probe_interval_s=0.0,
                       **overrides)
    return HealthHealer(router, s)


class TestHealthHealer:
    @respx.mock
    async def test_restores_after_consecutive_probes(self):
        from wiwi.router.router import Router
        r, key, dep = _sick_key_router()
        respx.post(OPENAI_URL).respond(json=PROBE_OK_BODY)
        h = _healer(r, probes_to_restore=2)
        assert await h._sweep() == 1
        assert key.status == "cooling"          # 1 of 2 successes: no restore yet
        await h._sweep()
        assert key.status == "probation"
        assert key.available
        assert dep.cooldown_until <= time.monotonic()  # healthy dep untouched
        await h.stop()

    @respx.mock
    async def test_restores_cooled_deployment_after_consecutive_probes(self):
        from wiwi.router.router import Router
        r, dep, key = _sick_dep_router()
        respx.post(OPENAI_URL).respond(json=PROBE_OK_BODY)
        h = _healer(r, probes_to_restore=2)
        assert await h._sweep() == 1
        assert dep.cooldown_until > time.monotonic()  # 1 of 2: no restore yet
        await h._sweep()
        assert dep.cooldown_until <= time.monotonic()
        assert dep.probation is True
        await h.stop()

    @respx.mock
    async def test_both_sick_restored_by_one_pair_each(self):
        from wiwi.router.router import Router
        r, key, dep = _sick_router()
        respx.post(OPENAI_URL).respond(json=PROBE_OK_BODY)
        h = _healer(r, probes_to_restore=2)
        assert await h._sweep() == 2  # sick key (k0) + cooled dep (k1)
        assert key.status == "cooling"
        assert dep.cooldown_until > time.monotonic()
        await h._sweep()
        assert key.status == "probation"
        assert dep.probation is True
        await h.stop()

    @respx.mock
    async def test_429_extends_cooling_without_trip(self):
        from wiwi.router.router import Router
        r, key, dep = _sick_router()
        key.mark_cooling(5.0)
        respx.post(OPENAI_URL).respond(status_code=429,
                                       headers={"retry-after": "120"})
        h = _healer(r)
        await h._sweep()
        assert key.status == "cooling"
        assert key.cooldown_until > time.monotonic() + 100.0
        assert not h._circuits["key"].blocked(("p1", "k0"))
        assert not h._circuits["dep"].blocked(("g", "p1", "m"))
        await h.stop()

    @respx.mock
    async def test_401_trips_key_circuit_and_next_sweep_skips(self):
        from wiwi.router.router import Router
        r, key, _ = _sick_key_router()
        key.status = "invalid"
        route = respx.post(OPENAI_URL).respond(status_code=401, text="nope")
        h = _healer(r)
        await h._sweep()
        assert key.status == "invalid"
        assert h._circuits["key"].blocked(("p1", "k0"))
        await h._sweep()
        assert route.call_count == 1            # skipped while blocked
        await h.stop()

    @respx.mock
    async def test_400_restores_key_and_escalates_dep_circuit(self):
        from wiwi.router.router import Router
        r, key, _ = _sick_key_router()
        key.status = "invalid"
        respx.post(OPENAI_URL).respond(
            status_code=400, json={"error": {"message": "model not found"}})
        # zero-base circuits keep the escalation deterministic in-test
        h = _healer(r, probes_to_restore=1, probe_backoff_base_s=0.0)
        await h._sweep()
        assert key.status == "probation"        # creds proven -> restore-eligible
        assert not h._circuits["key"].blocked(("p1", "k0"))
        assert h._circuits["dep"].streak(("g", "p1", "m")) == 1
        # keep probing the same (dep, key) pair: key now probation + dep cooled?
        # No — dep is healthy here, so the dep circuit streak records but no
        # deployment state changes; the escalation-to-dead path is covered by
        # test_400_on_cooled_dep_marks_it_dead.
        await h._sweep()
        await h._sweep()
        assert key.status == "probation"
        await h.stop()

    @respx.mock
    async def test_400_on_cooled_dep_marks_it_dead(self):
        from wiwi.router.router import Router
        r, dep, key = _sick_dep_router()
        respx.post(OPENAI_URL).respond(
            status_code=400, json={"error": {"message": "model not found"}})
        h = _healer(r, probe_backoff_base_s=0.0)
        await h._sweep()
        await h._sweep()
        await h._sweep()
        assert h._circuits["dep"].dead(("g", "p1", "m"))
        assert dep.cooldown_until > time.monotonic()  # never restored
        await h.stop()

    @respx.mock
    async def test_never_probes_disabled_keys(self):
        from wiwi.router.router import Router
        r = Router(_router_config())
        key = r.providers["p1"].keys[0]
        key.enabled = False
        key.status = "cooling"
        key.mark_cooling(999.0)
        route = respx.post(OPENAI_URL).respond(json=PROBE_OK_BODY)
        h = _healer(r)
        assert await h._sweep() == 0
        assert not route.called
        await h.stop()

    @respx.mock
    async def test_respects_per_sweep_cap(self):
        from wiwi.router.router import Router
        r = Router(_router_config(n_keys=3))
        for k in r.providers["p1"].keys:
            k.mark_cooling(999.0)
        respx.post(OPENAI_URL).respond(json=PROBE_OK_BODY)
        h = _healer(r, max_probes_per_sweep=2)
        assert await h._sweep() == 2
        await h.stop()

    @respx.mock
    async def test_force_stream_probes_with_stream_true(self):
        from wiwi.router.router import Router
        cfg = WiwiConfig(
            providers=[ProviderDef(name="cl", provider="cline",
                                   keys=[KeyDef(label="a", key="tok")])],
            model_list=[ModelEntry(model_name="g",
                                   wiwi_params=DeploymentParams(provider="cl",
                                                                model="m"))],
        )
        r = Router(cfg)
        key = r.providers["cl"].keys[0]
        key.mark_cooling(999.0)
        route = respx.post("https://api.cline.bot/api/v1/chat/completions").respond(
            status_code=200, text="data: [DONE]\n\n")
        h = _healer(r, probes_to_restore=1)
        await h._sweep()
        assert key.status == "probation"
        import orjson
        body = orjson.loads(route.calls.last.request.content)
        assert body["stream"] is True
        await h.stop()

    async def test_disabled_start_is_a_noop(self):
        h = HealthHealer(_sick_router()[0], HealerSettings(enabled=False))
        h.start()
        assert h._task is None
        await h.stop()


class TestLifespanWiring:
    async def test_healer_starts_and_stops_with_app(self):
        from wiwi.server.app import create_app
        cfg = WiwiConfig(
            providers=[ProviderDef(name="p1", provider="openai",
                                   keys=[KeyDef(label="a", key="k")])],
            model_list=[ModelEntry(model_name="g",
                                   wiwi_params=DeploymentParams(provider="p1",
                                                                model="m"))],
            general_settings=GeneralSettings(
                master_key="sk-wiwi-master-test",
                database_url="sqlite+aiosqlite:///:memory:"),
            healer=HealerSettings(enabled=True, tick_s=0.05),
        )
        app = create_app(cfg)
        async with LifespanManager(app):
            state = app.state.wiwi
            assert state.healer is not None
            assert state.healer._task is not None and not state.healer._task.done()
        assert state.healer._task is None
