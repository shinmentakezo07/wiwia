"""Round-62 regression tests — per-deployment ``rpm``/``tpm`` were a silent no-op.

AUDIT #101: ``DeploymentParams.rpm``/``tpm`` were parsed into
``Deployment.rpm``/``tpm`` and documented as per-deployment overrides
(``README.md`` "Per-deployment: ``max_tokens``, ``rpm``, ``tpm``, ``timeout``…",
``detailed.md``), but no code path ever read them. An operator who wrote
``wiwi_params: {..., tpm: 100000}`` got an uncapped deployment and no warning:
the cap "silently does nothing".

The fix enforces both caps as sliding 60-second windows on the deployment
itself, mirroring the virtual-key limiter's window semantics
(``wiwi/ratelimit/memory.py``):

- admission (``pick_deployment``) skips a deployment whose window is full, so
  traffic lands on a sibling instead of failing;
- when *every* candidate is at its cap the router answers **429** (a rate
  limit, with the seconds until a slot frees) rather than 503 (an outage);
- the admission-time token *estimate* is reconciled to actual usage at pricing
  time, so one request is not charged twice against the cap;
- an attempt that fails before pricing refunds its slot, or a single 500 would
  throttle the deployment for the rest of the window (the #70/#121
  phantom-reservation class).

Each test below fails against the pre-fix source and passes after the fix.
Controls mark the places a naive fix would overcorrect: release must never
refund confirmed usage, a partially-filled window must still admit, and a
positive cap must still load.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
import respx
from pydantic import ValidationError

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.providers.base import WiwiError
from wiwi.router.router import Router, execute_with_retries
from wiwi.streaming import deltas as dl
from wiwi.streaming.resume import StreamTape


def _cfg(strategy: str = "simple-shuffle", **deployment_params) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(
                                   provider="p1", model="gpt-4o",
                                   **deployment_params))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        # num_retries=0 so a refused attempt surfaces as one clean error
        # instead of being retried against the same saturated deployment.
        router_settings=RouterSettings(num_retries=0,
                                       routing_strategy=strategy),
    )


def _ctx(group: str = "gpt-4o", rid: str = "r1", est: int = 0) -> RequestContext:
    c = RequestContext(surface="chat",
                       ir_req=ir.Request(model=group, messages=[]),
                       request_id=rid)
    c.group = group
    c.est_tokens = est
    return c


# ---------------------------------------------------------------------------
# rpm: admission and diversion
# ---------------------------------------------------------------------------

async def test_deployment_rpm_cap_is_enforced():
    """A deployment with rpm=2 serves exactly two requests per window.

    Pre-fix the third request reached the upstream: ``rpm`` was stored and
    documented but never consulted.
    """
    r = Router(_cfg(rpm=2))
    calls: list[str] = []

    async def call_one(dep, key, ctx):
        calls.append(ctx.request_id)
        return "ok"

    assert await execute_with_retries(r, _ctx(rid="a"), call_one) == "ok"
    assert await execute_with_retries(r, _ctx(rid="b"), call_one) == "ok"
    with pytest.raises(WiwiError) as ei:
        await execute_with_retries(r, _ctx(rid="c"), call_one)
    assert ei.value.status == 429
    assert len(calls) == 2, "the capped deployment must never see a third call"


async def test_rpm_saturated_deployment_yields_to_sibling():
    """The cap must divert traffic, not fail it: with an uncapped sibling in
    the group the request still succeeds."""
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[
            ModelEntry(model_name="g", wiwi_params=DeploymentParams(
                provider="p1", model="capped", rpm=1)),
            ModelEntry(model_name="g", wiwi_params=DeploymentParams(
                provider="p1", model="uncapped")),
        ],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        # least-busy is deterministic here: all inflight counts are 0, so the
        # first available deployment in list order wins.
        router_settings=RouterSettings(num_retries=0,
                                       routing_strategy="least-busy"),
    )
    r = Router(cfg)
    picked: list[str] = []

    async def call_one(dep, key, ctx):
        picked.append(dep.model_id)
        return "ok"

    for rid in ("a", "b", "c"):
        await execute_with_retries(r, _ctx(group="g", rid=rid), call_one)

    assert picked[0] == "capped", "control: the capped deployment is picked first"
    assert "capped" not in picked[1:], (
        "traffic must divert to the uncapped sibling once the cap is reached"
    )
    assert len(picked) == 3


async def test_all_deployments_at_cap_report_429_not_503():
    """Every candidate capped is a rate limit (retryable, with a horizon), not
    an outage — 503 would tell the client to give up on a healthy deployment.
    """
    r = Router(_cfg(rpm=1))

    async def call_one(dep, key, ctx):
        return "ok"

    await execute_with_retries(r, _ctx(rid="a"), call_one)
    with pytest.raises(WiwiError) as ei:
        await execute_with_retries(r, _ctx(rid="b"), call_one)
    e = ei.value
    assert e.status == 429, "a per-deployment cap is a rate limit, not a 503"
    assert e.retry_after and e.retry_after > 0, (
        "a rate limit must carry the seconds until a slot frees"
    )


# ---------------------------------------------------------------------------
# tpm: estimate -> actual reconciliation, and refunds
# ---------------------------------------------------------------------------

def test_deployment_tpm_settles_estimate_to_actual():
    """The tpm window must hold actual tokens, not estimate + actual."""
    r = Router(_cfg(tpm=1000))
    dep = r.groups["gpt-4o"][0]
    now = time.monotonic()
    dep.reserve_slot("r1", 900)
    assert dep.rate_limited(now, 200), "control: the reservation must consume the cap"
    dep.settle_tokens("r1", 50)
    assert not dep.rate_limited(now, 950), (
        "settling must replace the estimate (50 + 950 == 1000), not add to it"
    )
    assert dep.rate_limited(now, 951)


def test_deployment_release_refunds_estimate_but_never_confirmed_usage():
    """A failed attempt refunds its slot; confirmed usage survives a release.

    A stream that delivered tokens and then died must stay billed (the release
    path is only for attempts that never reached pricing).
    """
    r = Router(_cfg(tpm=1000, rpm=1))
    dep = r.groups["gpt-4o"][0]
    now = time.monotonic()
    dep.reserve_slot("r1", 900)
    dep.release_slot("r1")
    assert not dep.rate_limited(now, 900), "the tpm estimate must be refunded"
    assert not dep.rate_limited(now, 0), "the rpm slot must be refunded too"

    dep.reserve_slot("r2", 900)
    dep.settle_tokens("r2", 900)
    dep.release_slot("r2")
    assert dep.rate_limited(now, 200), (
        "control: confirmed usage must never be refunded"
    )


def test_double_settle_does_not_double_charge_the_cap():
    """A second settle for one request adjusts the charge, never adds it.

    The pump prices a completed stream and can then be cancelled while blocked
    on the output queue, so its cancellation handler prices the same request
    again. Appending a second event would charge the cap twice for one request.
    """
    r = Router(_cfg(tpm=1000))
    dep = r.groups["gpt-4o"][0]
    now = time.monotonic()
    dep.reserve_slot("r1", 500)
    dep.settle_tokens("r1", 200)
    dep.settle_tokens("r1", 200)
    assert not dep.rate_limited(now, 800), (
        "one request settled twice must still occupy 200 tokens, not 400"
    )
    assert dep.rate_limited(now, 801)


def test_cap_admits_until_reached():
    """Controls the off-by-one: a partially filled window still admits."""
    r = Router(_cfg(rpm=2, tpm=1000))
    dep = r.groups["gpt-4o"][0]
    now = time.monotonic()
    dep.reserve_slot("a", 400)
    assert not dep.rate_limited(now, 400), "1 of 2 rpm, 800 of 1000 tpm"
    dep.reserve_slot("b", 400)
    assert dep.rate_limited(now, 0), "2 of 2 rpm is the cap"


async def test_tpm_admission_uses_ctx_estimate():
    """A request whose estimated prompt alone exceeds the cap is refused before
    any upstream call, and the estimate comes from ``ctx.est_tokens``."""
    calls: list[int] = []

    async def call_one(dep, key, ctx):
        calls.append(1)
        return "ok"

    r = Router(_cfg(tpm=100))
    with pytest.raises(WiwiError) as ei:
        await execute_with_retries(r, _ctx(rid="a", est=200), call_one)
    assert ei.value.status == 429
    assert not calls, "the cap must refuse before the upstream call"

    # Control: the same request fits when the estimate is under the cap.
    r2 = Router(_cfg(tpm=100))
    assert await execute_with_retries(r2, _ctx(rid="b", est=50), call_one) == "ok"


async def test_resume_skips_a_deployment_at_its_cap():
    """A mid-stream resume must not push a saturated deployment over its cap.

    ``_attempt_resume`` picks a deployment directly rather than through
    ``pick_deployment``, so it needs its own admission check — otherwise a
    resume is the one path that can exceed a configured cap.
    """
    g = Gateway(Router(_cfg(rpm=1)), CostEngine())
    dep = g.router.groups["gpt-4o"][0]
    ctx = _ctx(rid="orig", est=10)

    tape = StreamTape()
    tape.append(dl.TextDelta("partial response"))
    queue: asyncio.Queue = asyncio.Queue()

    async def mock_pump(dep_, key, resume_ctx, q, ready, err_box):
        ready.set()
        err_box[0] = None

    g._pump = mock_pump

    # Saturated: the only candidate is at its rpm cap, so the resume is
    # refused and no pump starts.
    dep.reserve_slot("someone-else", 10)
    resumed, task = await g._attempt_resume(ctx, tape, queue)
    assert resumed is False and task is None, (
        "a saturated deployment must not host a resume"
    )

    # Control: with the window empty the same deployment hosts the resume.
    dep.release_slot("someone-else")
    resumed, task = await g._attempt_resume(ctx, tape, queue)
    assert resumed is True and task is not None
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


async def test_failed_attempt_refunds_the_deployment_slot():
    """An attempt that fails before pricing must not throttle the deployment.

    Without the refund the first failure leaves a phantom reservation and the
    next legitimate request is refused for the rest of the 60s window (the
    #70/#121 leak class, one layer up).

    This one is a **guard**, not a discriminating regression: it passes
    against the pre-fix tree too, because pre-fix there was no reservation to
    leak. It exists to pin the refund against a future change that adds
    admission without a matching release.

    The failure used here is a *non-retryable client* error: the router does
    not cool the key or deployment for it (``_status_of`` returns ``None`` for
    a 400), so the second request genuinely exercises the deployment's cap
    rather than the key pool's cooldown. A 5xx would take the key out of
    rotation on its own and mask the reservation leak.
    """
    r = Router(_cfg(rpm=1))

    async def bad_request(dep, key, ctx):
        raise WiwiError(400, "invalid_request_error", "bad input")

    with pytest.raises(WiwiError):
        await execute_with_retries(r, _ctx(rid="a"), bad_request)

    async def ok(dep, key, ctx):
        return "ok"

    assert await execute_with_retries(r, _ctx(rid="b"), ok) == "ok"


# ---------------------------------------------------------------------------
# pricing settles the reservation (non-streaming and streaming)
# ---------------------------------------------------------------------------

def test_non_streaming_pricing_settles_deployment_tokens():
    g = Gateway(Router(_cfg(tpm=1000)), CostEngine())
    dep = g.router.groups["gpt-4o"][0]
    ctx = _ctx(rid="r1", est=900)
    dep.reserve_slot(ctx.request_id, ctx.est_tokens)
    turn = ir.AssistantTurn(text="ok",
                            usage=ir.Usage(prompt_tokens=100,
                                           completion_tokens=20))
    g._price(ctx, dep, turn.usage)
    now = time.monotonic()
    assert not dep.rate_limited(now, 880), (
        "the 900-token estimate must be replaced by the 120 actual tokens"
    )
    assert dep.rate_limited(now, 881)


def test_streaming_pricing_settles_deployment_tokens():
    g = Gateway(Router(_cfg(tpm=1000)), CostEngine())
    dep = g.router.groups["gpt-4o"][0]
    ctx = _ctx(rid="r1", est=900)
    dep.reserve_slot(ctx.request_id, ctx.est_tokens)
    g._price_stream(ctx, dep, dl.UsageFinal(prompt=100, output=20))
    now = time.monotonic()
    assert not dep.rate_limited(now, 880)
    assert dep.rate_limited(now, 881)


@respx.mock
async def test_stream_pump_settles_deployment_tokens():
    """End-to-end: the pump reconciles the admission estimate with the usage
    the provider actually reported."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(text=(
        'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":100,"completion_tokens":5}}\n\n'
        'data: [DONE]\n\n'))
    g = Gateway(Router(_cfg(tpm=1000)), CostEngine())
    try:
        dep = g.router.groups["gpt-4o"][0]
        req = ir.Request(model="gpt-4o", stream=True,
                         messages=[ir.Message(role="user",
                                              parts=[ir.TextPart("hi")])])
        ctx = RequestContext(surface="chat", ir_req=req, group="gpt-4o",
                             request_id="r1")
        # `pick_deployment` reserves this at admission (AUDIT #101); the pump
        # must then settle it against the usage the provider reported.
        ctx.est_tokens = 900
        async for _ in g.stream(ctx):
            pass
    finally:
        await g.aclose()
    now = time.monotonic()
    assert not dep.rate_limited(now, 890), (
        "the pump must settle actual usage (105) over the 900 estimate"
    )
    assert dep.rate_limited(now, 896)


# ---------------------------------------------------------------------------
# config validation: a nonsensical cap is rejected, not silently ignored
# ---------------------------------------------------------------------------

def test_deployment_limits_must_be_positive():
    with pytest.raises(ValidationError):
        DeploymentParams(provider="p", model="m", rpm=0)
    with pytest.raises(ValidationError):
        DeploymentParams(provider="p", model="m", tpm=-1)
    # Control: a positive cap still loads and is carried onto the deployment.
    r = Router(_cfg(rpm=5, tpm=1000))
    dep = r.groups["gpt-4o"][0]
    assert (dep.rpm, dep.tpm) == (5, 1000)
