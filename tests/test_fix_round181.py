"""Round-181 regressions — the `wiwi/core/` sweep.

Five findings from reading `core/` against its callers. The theme is that
`core/gateway.py` carries three hand-written upstream paths (`_call_once`,
`_complete_via_stream`, `_pump_once`) and they had drifted apart:

1. A failed 401-refresh retry was reported as the *stale* 401 on both
   non-streaming arms, so a freshly-rotated key took a credential-failure
   penalty and the retry's own status/`Retry-After` were discarded (AUDIT #533).
2. `_complete_via_stream` dropped three of the thirteen delta kinds and skipped
   the loop detector and tool-arg validation the pump runs (AUDIT #534).
3. `merge_resume_context` folded usage but not the advisory `metadata` a
   resumed attempt produces (AUDIT #535).
4. Three `ctx.metadata` keys were written by nothing and read by nothing
   (AUDIT #536).
5. A deployment the healer marked "dead" was never probed again (AUDIT #537).
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
import respx

from wiwi.config import (
    DeploymentParams,
    HealerSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway, merge_resume_context
from wiwi.core.recovery import HealthHealer, ProbeVerdict
from wiwi.cost.pricing import CostEngine
from wiwi.ir.types import Request, Tool
from wiwi.providers.base import WiwiError
from wiwi.router.router import Router
from wiwi.server.app import _with_warnings
from wiwi.streaming import deltas as dl

URL = "https://api.openai.com/v1/chat/completions"


def _config(**router_overrides) -> WiwiConfig:
    rs = RouterSettings(num_retries=0, allowed_fails=2, cooldown_time=0.05)
    for k, v in router_overrides.items():
        setattr(rs, k, v)
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="k1")]),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
        ],
        router_settings=rs,
    )


def _ctx(**kwargs) -> RequestContext:
    base = {"surface": "chat", "ir_req": Request(model="gpt-4o", messages=[])}
    base.update(kwargs)
    ctx = RequestContext(**base)
    ctx.group = "gpt-4o"
    ctx.request_id = "req-181"
    return ctx


def _gateway(config: WiwiConfig | None = None):
    r = Router(config or _config())
    gw = Gateway(r, CostEngine())
    dep = r.groups["gpt-4o"][0]
    return gw, r, dep, dep.provider.keys[0]


# -- 1. the refresh retry's own error, not the stale 401 ----------------------


@respx.mock
async def test_refresh_retry_failure_surfaces_the_retry_error():
    """AUDIT #533: the retry's status and Retry-After win over the stale 401.

    The 401 is what *triggered* the rotation, so it describes a credential that
    no longer exists. Reporting it told the caller to re-authenticate, charged
    the freshly-rotated key with a credential failure (401 → the key pool), and
    dropped the retry's own ``Retry-After``.
    """
    gw, _r, dep, key = _gateway()
    ctx = _ctx()
    rotated: list[tuple[str, str]] = []

    async def hook(provider: str, label: str) -> bool:
        rotated.append((provider, label))
        return True

    gw._on_demand_refresh_hooks["openai"] = hook
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(401, json={"error": {"message": "token expired",
                                            "type": "invalid_request_error"}}),
        httpx.Response(500, json={"error": {"message": "upstream exploded"}},
                       headers={"retry-after": "7"}),
    ]

    try:
        with pytest.raises(WiwiError) as ei:
            await gw._call_once(dep, key, ctx)
    finally:
        await gw.aclose()

    err = ei.value
    assert rotated == [("p1", "a")], "the hook must have run exactly once"
    # `error_from_provider_status` normalizes a bare 5xx to 502, so the status
    # to assert on is the *retry's body* — the 401's message would be "token
    # expired" and would carry no Retry-After.
    assert "upstream exploded" in err.message, (
        f"the stale 401 was surfaced: {err.message!r}")
    assert err.status == 502, f"expected the retry's 5xx, got {err.status}"
    assert err.retry_after == 7.0, "the retry's Retry-After was dropped"
    assert [a.status for a in ctx.attempts] == ["http_401", "http_500"], (
        f"the retry must appear as its own attempt: {ctx.attempts}")


@respx.mock
async def test_refresh_retry_failure_on_the_force_stream_arm_surfaces_the_retry_error():
    """AUDIT #533, second arm: `_complete_via_stream` did the same thing."""
    gw, _r, dep, key = _gateway()
    ctx = _ctx()
    adapter = _FakeAdapter({})

    async def hook(provider: str, label: str) -> bool:
        return True

    gw._on_demand_refresh_hooks["openai"] = hook
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(401, json={"error": {"message": "token expired"}}),
        httpx.Response(429, json={"error": {"message": "quota"}},
                       headers={"retry-after": "3"}),
    ]

    try:
        with pytest.raises(WiwiError) as ei:
            await gw._complete_via_stream(dep, key, ctx, adapter)
    finally:
        await gw.aclose()

    assert ei.value.status == 429
    assert ei.value.retry_after == 3.0
    assert [a.status for a in ctx.attempts] == ["http_401", "http_429"]


# -- 2. the force-stream reassembly keeps what the pump keeps -----------------


class _FakeAdapter:
    """Adapter stub that maps one SSE frame's payload to scripted deltas."""

    def __init__(self, script: dict[str, list[dl.IRStreamDelta]]) -> None:
        self._script = script

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        return base_url.rstrip("/") + "/chat/completions"

    def headers(self, key) -> dict[str, str]:
        return {}

    def encode_request(self, req, model_id: str, params: dict) -> dict:
        return {"model": model_id}

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        return self._script.get(data, [])


def _sse(*frames: str) -> bytes:
    return "".join(f"data: {f}\n\n" for f in frames).encode()


@respx.mock
async def test_force_stream_reassembly_keeps_server_blocks_logprobs_and_stop_sequence():
    """AUDIT #534: three carriers the arm dropped while the pump kept them."""
    gw, _r, dep, key = _gateway()
    ctx = _ctx(ir_req=Request(model="gpt-4o", messages=[],
                              tools=[Tool(name="t", parameters_json_schema={
                                  "type": "object",
                                  "properties": {"a": {"type": "string"}}})]))
    block = {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
             "content": [{"type": "web_search_result", "url": "https://x"}]}
    adapter = _FakeAdapter({
        "1": [dl.StreamStart(model="gpt-4o", prompt=11, cached=2)],
        "2": [dl.TextDelta("hello ")],
        "3": [dl.LogprobsDelta(logprobs={"content": [{"token": "hello",
                                                     "logprob": -0.1}]})],
        "4": [dl.LogprobsDelta(logprobs={"content": [{"token": " world",
                                                     "logprob": -0.2}]})],
        "5": [dl.ServerToolResultDelta(index=2, block=block)],
        # A tool call whose args do not match the declared schema.
        "6": [dl.ToolCallOpen(index=0, id="c1", name="t"),
              dl.ToolCallArgsDelta(index=0, args_fragment='{"a": 1}'),
              dl.ToolCallClose(index=0)],
        "7": [dl.UsageFinal(prompt=11, output=2),
              dl.Finish("stop_sequence", stop_sequence="END")],
    })
    respx.post(URL).mock(return_value=httpx.Response(
        200, content=_sse("1", "2", "3", "4", "5", "6", "7"),
        headers={"content-type": "text/event-stream"}))

    try:
        turn = await gw._complete_via_stream(dep, key, ctx, adapter)
    finally:
        await gw.aclose()

    assert turn.server_blocks == [block], "the provider-hosted result block was dropped"
    assert [t["token"] for t in turn.logprobs["content"]] == ["hello", " world"], (
        "per-token logprobs must accumulate, not keep the last frame")
    assert turn.stop_sequence == "END"
    assert turn.stop_reason == "stop_sequence"
    assert turn.text == "hello "
    assert ctx.metadata.get("tool_args_violations"), (
        "the advisory tool-arg check never ran on this arm")


@respx.mock
async def test_force_stream_reassembly_aborts_a_repetition_loop():
    """AUDIT #534: the pump aborts a runaway loop; this arm used to bill it all.

    The abort must not charge the key or the deployment: a repetition loop is a
    model-quality failure (AUDIT #108), which is why the error carries the one
    status `status_for_key_pool` ignores.
    """
    gw, _r, dep, key = _gateway(_config(stream_loop_detection=True,
                                        stream_loop_limit=5))
    ctx = _ctx()
    adapter = _FakeAdapter({
        **{str(i): [dl.TextDelta("ha")] for i in range(1, 10)},
        "9": [dl.TextDelta("ha"), dl.Finish("stop")],
    })
    respx.post(URL).mock(return_value=httpx.Response(
        200, content=_sse(*[str(i) for i in range(1, 10)]),
        headers={"content-type": "text/event-stream"}))

    try:
        with pytest.raises(WiwiError) as ei:
            await gw._complete_via_stream(dep, key, ctx, adapter)
    finally:
        await gw.aclose()

    assert ei.value.status == 400
    assert "model loop detected" in ei.value.message
    assert ei.value.retryable is False, "a loop must not be retried elsewhere"
    # The delivered tokens were still billed (the upstream charged for them).
    assert ctx.usage is not None and ctx.usage.completion_tokens > 0, (
        f"the partial delivery was not priced: {ctx.usage}")


# -- 3. advisory metadata survives a resumed attempt -------------------------


def test_merge_resume_context_carries_the_advisory_channels():
    """AUDIT #535: the readers all look at the originating context."""
    origin = _ctx()
    origin.metadata["translation_warnings"] = ["dropped strict"]
    origin.metadata["cache_savings"] = 0.5
    resumed = _ctx()
    resumed.metadata["translation_warnings"] = ["dropped strict", "dropped top_k"]
    resumed.metadata["tool_args_violations"] = ["tool 't': bad args"]
    resumed.metadata["cache_savings"] = 0.25

    merge_resume_context(origin, resumed)

    assert origin.metadata["translation_warnings"] == [
        "dropped strict", "dropped top_k"], "deduped, order preserved"
    assert origin.metadata["tool_args_violations"] == ["tool 't': bad args"]
    assert origin.metadata["cache_savings"] == 0.75, "savings are dollars, so they add"


def test_merge_resume_context_without_advisories_leaves_the_origin_alone():
    """Control: a clean resumed attempt must not invent metadata keys."""
    origin = _ctx()
    merge_resume_context(origin, _ctx())
    assert "translation_warnings" not in origin.metadata
    assert "tool_args_violations" not in origin.metadata


# -- 4. the write-only metadata keys now have a reader ----------------------


def test_tool_arg_violations_reach_the_caller_as_a_header():
    """AUDIT #536: written since round 20 and read by nothing."""
    ctx = _ctx()
    ctx.metadata["tool_args_violations"] = ["tool 't': property 'a' expected string"]

    headers = _with_warnings({}, ctx)

    assert "tool 't'" in headers["x-wiwi-tool-args-warnings"]


def test_both_advisory_channels_ride_the_same_response():
    """Both headers, and neither when the request was clean."""
    ctx = _ctx()
    ctx.metadata["translation_warnings"] = ["strict unsupported"]
    ctx.metadata["tool_args_violations"] = ["tool 't': bad args"]

    headers = _with_warnings({"x-other": "1"}, ctx)

    assert headers["x-other"] == "1"
    assert headers["x-wiwi-translation-warnings"] == "strict unsupported"
    assert headers["x-wiwi-tool-args-warnings"] == "tool 't': bad args"
    assert _with_warnings({}, _ctx()) == {}, "a clean request grows no headers"


def test_pricing_no_longer_writes_metadata_nothing_reads():
    """AUDIT #536: an unpriced model is a log line, not a dead metadata key."""
    gw, _r, dep, _key = _gateway()
    ctx = _ctx()
    gw._price(ctx, dep, SimpleNamespace(
        prompt_tokens=3, completion_tokens=2, cached_tokens=0,
        cache_creation_tokens=0, reasoning_tokens=0, estimated=False,
        cached_in_prompt=None))

    assert "unpriced_model" not in ctx.metadata
    assert "unpriced_model_id" not in ctx.metadata


# -- 5. a model-bad deployment is re-probed, not retired forever ------------


class _Key:
    label = "a"
    enabled = True
    status = "invalid"
    available = True
    cooldown_until = 0.0

    def mark_recovered(self) -> None:
        self.status = "active"

    def mark_cooling(self, window: float) -> None:
        self.status = "cooling"


def _healer_router():
    key = _Key()
    dep = SimpleNamespace(group="g", model_id="m", cooldown_until=time.monotonic() + 600,
                          provider=SimpleNamespace(name="p", keys=[key]),
                          mark_recovered=lambda: None)
    return SimpleNamespace(groups={"g": [dep]},
                           providers={"p": SimpleNamespace(keys=[key])}), dep, key


async def test_model_bad_deployment_is_probed_again_after_the_backoff():
    """AUDIT #537: `mark_dead` had no un-dead path, so the sweep skipped it forever.

    ``probe_verdict`` maps 400/404 to CREDS_VALID_MODEL_BAD, and a transient 404
    (or a 400 raised by the probe's own shape) is enough to reach the streak.
    The circuit now keeps escalating its capped backoff instead, so the
    deployment returns to the candidate set once that window elapses.
    """
    router, dep, key = _healer_router()
    healer = HealthHealer(router, HealerSettings(
        enabled=True, tick_s=1.0, probe_timeout_s=1.0, max_probes_per_sweep=8,
        min_probe_interval_s=0.0, probe_backoff_base_s=0.0,
        probe_backoff_cap_s=0.0, probes_to_restore=2))

    async def model_bad(d, k):
        return ProbeVerdict.CREDS_VALID_MODEL_BAD, "400 bad request", None

    healer._probe = model_bad  # type: ignore[method-assign]
    for _ in range(3):
        await healer._probe_pair(dep, key)

    did = ("g", "p", "m")
    assert not healer._circuits["dep"].dead(did), (
        "a transient model-bad streak must not retire the deployment forever")
    assert healer._collect_pairs(), (
        "the deployment is no longer a probe candidate — it can never heal")


async def test_a_healthy_probe_still_restores_a_cooled_deployment():
    """Control: the restore path is untouched by the #537 change."""
    router, dep, key = _healer_router()
    healer = HealthHealer(router, HealerSettings(
        enabled=True, tick_s=1.0, probe_timeout_s=1.0, max_probes_per_sweep=8,
        min_probe_interval_s=0.0, probe_backoff_base_s=0.0,
        probe_backoff_cap_s=0.0, probes_to_restore=1))

    async def healthy(d, k):
        return ProbeVerdict.HEALTHY, "", None

    healer._probe = healthy  # type: ignore[method-assign]
    await healer._probe_pair(dep, key)

    assert healer._circuits["dep"].streak(("g", "p", "m")) == 0
