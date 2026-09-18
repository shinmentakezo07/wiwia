"""Round-75 regression tests for the stream pump (AUDIT #159, #211, #184).

Three defects, all in ``wiwi/core/gateway.py``:

* **#159** — a mid-stream resume emitted a *second* ``StreamStart``. ``started``
  is a local of ``Gateway.stream`` initialised once before the consumer loop,
  and the resume branch ``continue``s back into that loop without resetting it;
  the resumed pump runs a NEW adapter instance, whose own ``message_start``
  emits its own ``StreamStart``, and the unguarded ``StreamStart`` arm yielded
  it verbatim. ``streaming/deltas.py`` allows exactly one ``StreamStart``
  first, and an Anthropic client reads a second ``message_start`` as a new
  message — Claude Code re-initialises its context meter and accumulator and
  the turn splits across two message objects. The existing coverage
  (``tests/test_fix_round20.py``) missed it because its harness uses OpenAI
  providers, whose adapter emits no ``StreamStart`` at all (the gateway
  synthesizes one, and the synthetic branch was already guarded); only the
  Anthropic adapter emits one, which is the unguarded path.

* **#211** — the pump's error handler was unguarded. The mid-stream
  ``except Exception`` arm, the idle-timeout arm, and the clean-completion
  usage fallback awaited ``_note_stream_failure`` / ``_price_partial`` /
  ``queue.put(StreamError(...))`` with no ``try/except``. Any fault in that
  sequence killed the pump *before* the terminal frame was queued, and nothing
  noticed: the consumer is parked on ``await queue.get()`` and its own
  ``finally`` only runs once it exits, which it never does. No terminal frame,
  no timeout — the request never completes and every client retry re-runs the
  whole prompt.

* **#184** — ``flatten_request_text`` raised ``TypeError`` on a non-string IR
  field (``" ".join(out)`` with a non-``str`` ``TextPart.text`` or tool
  ``name``). The decode-site fix belongs to the codec owners; this file pins
  the defence in depth, because the estimator is called from the pump and is
  what made #184 reachable as a hang rather than a 500.

Every test here fails on the pre-fix code; the RED evidence is recorded in the
report accompanying this change.
"""

from __future__ import annotations

import asyncio

import httpx
import orjson
import pytest
import respx

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
from wiwi.core.gateway import Gateway, flatten_request_text
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl

# The consumer-side timeout every streaming test here uses. A hang must FAIL
# the test rather than wedge the suite — the whole point of #211 is that the
# client waits forever with nothing to notice.
CLIENT_TIMEOUT_S = 8.0


def _ant(*events: dict) -> bytes:
    """Anthropic SSE frames, one `event:`+`data:` block each."""
    return b"".join(
        b"event: " + e["type"].encode() + b"\ndata: " + orjson.dumps(e) + b"\n\n"
        for e in events)


def _config(*, max_resumes: int = 2, idle_s: float = 0.3) -> WiwiConfig:
    """Two Anthropic deployments in one group plus a fallback group, with
    mid-stream resume enabled so a failed pump can be superseded by a new one.

    Anthropic (not OpenAI, as in test_fix_round20) is the point: it is the only
    adapter that emits its own ``StreamStart``, so it is the only provider type
    that can exercise #159 at all.
    """
    return WiwiConfig(
        providers=[ProviderDef(name="a1", provider="anthropic",
                               base_url="https://r75a.example/v1",
                               keys=[KeyDef(label="k1", key="sk-ant-1")]),
                   ProviderDef(name="a2", provider="anthropic",
                               base_url="https://r75b.example/v1",
                               keys=[KeyDef(label="k2", key="sk-ant-2")])],
        model_list=[ModelEntry(model_name="claude-x",
                               wiwi_params=DeploymentParams(provider="a1",
                                                            model="claude-x")),
                    ModelEntry(model_name="claude-x-fb",
                               wiwi_params=DeploymentParams(provider="a2",
                                                            model="claude-x"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(
            num_retries=0, allowed_fails=1, cooldown_time=60.0,
            stream_resume="enabled", stream_resume_max_retries=max_resumes,
            stream_idle_timeout_s=idle_s,
            fallbacks={"claude-x": ["claude-x-fb", "claude-x"]}),
    )


def _req() -> ir.Request:
    return ir.Request(model="claude-x",
                      messages=[ir.Message(role="user",
                                           parts=[ir.TextPart("hi")])],
                      stream=True)


def _ctx() -> RequestContext:
    return RequestContext(surface="messages", ir_req=_req(), group="claude-x")


async def _drain(gw: Gateway, ctx: RequestContext, *,
                 timeout: float = CLIENT_TIMEOUT_S) -> list:
    """Consume ``gw.stream(ctx)`` under a real timeout.

    ``asyncio.wait_for`` around the whole consumption: without it a pump that
    dies before queueing its terminal frame parks this coroutine on
    ``queue.get()`` forever and the test never returns (AUDIT #211).
    """
    out: list = []

    async def _run() -> None:
        async for d in gw.stream(ctx):
            out.append(d)

    await asyncio.wait_for(_run(), timeout=timeout)
    return out


# ---------------------------------------------------------------------------
# #159 — a resumed stream yields exactly one StreamStart
# ---------------------------------------------------------------------------

async def _resume_deltas(*, resume_ok: bool) -> list:
    """Primary emits content then dies; the fallback either completes cleanly
    or dies too. Returns everything the consumer observed, in order."""
    gw = Gateway(Router(_config()), CostEngine())

    async def dying() -> object:
        # message_start carries the *primary's* prompt count (11); the idle
        # timeout below turns the stall into a mid-stream StreamError.
        yield _ant(
            {"type": "message_start", "message": {
                "id": "m1", "model": "claude-x", "content": [],
                "usage": {"input_tokens": 11, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "partial "}})
        await asyncio.sleep(30)

    async def healthy() -> object:
        # The resume's message_start carries a DIFFERENT count (99): the
        # reproducer distinguishes the two frames by it.
        yield _ant(
            {"type": "message_start", "message": {
                "id": "m2", "model": "claude-x", "content": [],
                "usage": {"input_tokens": 99, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "resumed"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
             "usage": {"output_tokens": 3}},
            {"type": "message_stop"})

    fallback = healthy if resume_ok else dying
    try:
        respx.post("https://r75a.example/v1/messages").mock(
            side_effect=[httpx.Response(200, content=dying()) for _ in range(4)])
        respx.post("https://r75b.example/v1/messages").mock(
            side_effect=[httpx.Response(200, content=fallback()) for _ in range(4)])
        return await _drain(gw, _ctx())
    finally:
        await gw.aclose()


@respx.mock
async def test_resume_emits_exactly_one_stream_start():
    """End-to-end through the real pump: a resume onto a healthy Anthropic
    fallback must produce ONE StreamStart, opening the stream with the
    primary's provider-reported prompt count."""
    out = await _resume_deltas(resume_ok=True)
    starts = [d for d in out if isinstance(d, dl.StreamStart)]
    assert len(starts) == 1, (
        "a mid-stream resume emitted a second StreamStart; the contract allows "
        f"exactly one first (AUDIT #159): {[type(d).__name__ for d in out]}")
    # The one opening frame keeps the primary's numbers, and it is FIRST.
    assert out[0] is starts[0]
    assert starts[0].prompt == 11, (
        "the opening StreamStart must carry the originating attempt's "
        f"provider-reported prompt count, got {starts[0].prompt}")
    # Both attempts' text reached the client, and the stream still terminated
    # exactly once.
    text = "".join(d.text for d in out if isinstance(d, dl.TextDelta))
    assert text == "partial resumed", text
    terminals = [d for d in out
                 if isinstance(d, (dl.StreamEnd, dl.StreamError))]
    assert len(terminals) == 1 and out[-1] is terminals[0]


@respx.mock
async def test_resume_usage_is_folded_into_one_terminal_usage_final():
    """The resume's prompt/cache numbers are not *lost* by folding its
    StreamStart: they arrive on its own UsageFinal, which the gateway sums into
    the single terminal frame the client is sent."""
    out = await _resume_deltas(resume_ok=True)
    usages = [d for d in out if isinstance(d, dl.UsageFinal)]
    assert len(usages) == 1, (
        f"expected one UsageFinal for one logical stream, got {len(usages)}")
    # Primary reported 11 prompt tokens in its message_start; the resume
    # reported 99. Summed, not replaced.
    assert usages[0].prompt == 110, (
        "the resumed attempt's prompt tokens must be summed into the single "
        f"terminal UsageFinal, got {usages[0].prompt}")


@respx.mock
async def test_resumed_stream_renders_one_message_start_through_the_encoder():
    """The client-visible symptom: rendered through the real
    ``AnthropicStreamEncoder``, the wire carries exactly one ``message_start``.
    A second one mid-stream is what makes Claude Code treat the resume as a new
    message."""
    from wiwi.wire.anthropic_messages import AnthropicStreamEncoder

    out = await _resume_deltas(resume_ok=True)
    enc = AnthropicStreamEncoder(model="claude-x", req_id="r75")
    frames = [b for b in (enc.feed(d) for d in out) if b]
    frames.append(enc.final_frame())
    wire = b"".join(frames).decode()
    assert wire.count("event: message_start") == 1, (
        "the Anthropic client received a second message_start mid-stream "
        f"(AUDIT #159):\n{wire}")


# ---------------------------------------------------------------------------
# #211 — a fault in the failure handler still terminates the stream
# ---------------------------------------------------------------------------

async def _faulty_drain(fault: str, *, arm: str = "idle",
                        max_resumes: int = 0) -> list:
    """Drive a stream whose upstream fails mid-stream, with *fault* injected
    into one of the failure path's awaits.

    ``arm="idle"`` reaches the idle-timeout handler; ``arm="crash"`` makes the
    upstream body itself raise, which is the ``except Exception`` arm reached
    when ``started`` is True — the arm the audit's own reproducer used.
    """
    gw = Gateway(Router(_config(max_resumes=max_resumes)), CostEngine())

    if fault == "note":
        async def boom(*_a, **_k):
            raise RuntimeError("db blip during failure accounting")
        gw._note_stream_failure = boom
    elif fault == "price":
        async def boom(*_a, **_k):
            raise RuntimeError("estimator blip")
        gw._price_partial = boom
    else:  # pragma: no cover - guards the test's own parameterisation
        raise AssertionError(f"unknown fault {fault!r}")

    async def stalling() -> object:
        # One real content delta so the failure is MID-stream (`started` is
        # True and content has flowed), then silence.
        yield _ant(
            {"type": "message_start", "message": {
                "id": "m1", "model": "claude-x", "content": [],
                "usage": {"input_tokens": 7, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "hello"}})
        await asyncio.sleep(30)

    async def crashing() -> object:
        yield _ant(
            {"type": "message_start", "message": {
                "id": "m1", "model": "claude-x", "content": [],
                "usage": {"input_tokens": 7, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "hello"}})
        raise RuntimeError("upstream socket died mid-body")

    body = stalling if arm == "idle" else crashing
    try:
        for host in ("https://r75a.example/v1", "https://r75b.example/v1"):
            respx.post(f"{host}/messages").mock(
                side_effect=[httpx.Response(200, content=body())
                             for _ in range(4)])
        return await _drain(gw, _ctx())
    finally:
        await gw.aclose()


@respx.mock
async def test_fault_in_note_stream_failure_still_sends_terminal_error():
    """A fault in the handler's FIRST await must not strand the consumer: the
    pump still queues a terminal StreamError, within the client's bounded
    wait."""
    out = await _faulty_drain("note")
    assert out, "the consumer received nothing at all"
    terminal = out[-1]
    assert isinstance(terminal, dl.StreamError), (
        "a fault in _note_stream_failure killed the pump before it queued a "
        f"terminal frame (AUDIT #211): {[type(d).__name__ for d in out]}")
    assert not any(isinstance(d, dl.StreamEnd) for d in out), \
        "an errored stream must not also report a clean end"


@respx.mock
async def test_fault_in_price_partial_still_sends_terminal_error():
    """Same for the second await: a fault while pricing the partial delivery
    must still terminate the stream."""
    out = await _faulty_drain("price")
    assert out and isinstance(out[-1], dl.StreamError), (
        "a fault in _price_partial killed the pump before it queued a terminal "
        f"frame (AUDIT #211): {[type(d).__name__ for d in out]}")


@respx.mock
async def test_pump_fault_before_connect_does_not_strand_the_caller():
    """Same never-completing shape as #211, one region earlier: a fault raised
    before ``_pump_once``'s own try (here, adapter construction) used to leave
    ``ready`` unset, and the caller parked on ``await ready.wait()`` forever.
    It must surface as an error instead."""
    import wiwi.core.gateway as gwmod

    gw = Gateway(Router(_config(max_resumes=0)), CostEngine())
    orig = gwmod.fresh_adapter

    def boom(_provider_type: str):
        raise RuntimeError("adapter registry exploded")

    gwmod.fresh_adapter = boom
    try:
        for host in ("https://r75a.example/v1", "https://r75b.example/v1"):
            respx.post(f"{host}/messages").mock(
                return_value=httpx.Response(200, content=b""))
        with pytest.raises(Exception) as ei:
            await _drain(gw, _ctx(), timeout=5.0)
        # Not a bare RuntimeError leaking out of the pump task: the caller gets
        # a routable gateway error (execute_with_retries can fail over on it).
        assert "stream pump error" in str(ei.value), str(ei.value)
    finally:
        gwmod.fresh_adapter = orig
        await gw.aclose()


@respx.mock
async def test_partial_pricing_keeps_the_attempts_provider_reported_prompt():
    """The resumed attempt's StreamStart numbers are not *dropped* by folding
    them out of the client-visible frame: an attempt that dies before its
    ``message_delta`` is priced from the prompt count its own ``message_start``
    reported, not from a local estimate (AUDIT #159)."""
    gw = Gateway(Router(_config(max_resumes=0)), CostEngine())

    async def stalling() -> object:
        yield _ant(
            {"type": "message_start", "message": {
                "id": "m1", "model": "claude-x", "content": [],
                "usage": {"input_tokens": 7, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "hello"}})
        await asyncio.sleep(30)

    try:
        for host in ("https://r75a.example/v1", "https://r75b.example/v1"):
            respx.post(f"{host}/messages").mock(
                side_effect=[httpx.Response(200, content=stalling())
                             for _ in range(4)])
        ctx = _ctx()
        out = await _drain(gw, ctx)
    finally:
        await gw.aclose()
    assert isinstance(out[-1], dl.StreamError)
    assert ctx.usage is not None
    assert ctx.usage.prompt_tokens == 7, (
        "the attempt's own message_start reported 7 prompt tokens; the partial "
        f"pricing must use that, not a local estimate (got "
        f"{ctx.usage.prompt_tokens})")


@respx.mock
async def test_midstream_exception_arm_is_total_with_a_faulty_handler():
    """The exact shape of the audit's reproducer: the upstream raises mid-body
    (so the ``except Exception`` arm runs with ``started`` True) AND the
    handler's first await faults. The consumer must still get its terminal
    frame — this is the arm where the pump used to die silently."""
    out = await _faulty_drain("note", arm="crash")
    assert out and isinstance(out[-1], dl.StreamError), (
        "a mid-stream upstream fault plus a faulty failure handler left the "
        f"client with no terminal frame (AUDIT #211): "
        f"{[type(d).__name__ for d in out]}")
    assert any(isinstance(d, dl.TextDelta) for d in out), \
        "the pre-failure content must still have reached the client"


@respx.mock
async def test_clean_completion_with_unflattenable_request_still_terminates():
    """The clean-completion usage fallback is the third arm named by #211: it
    runs whenever the provider omits usage (the NORMAL case for streaming-only
    upstreams) and it calls the estimator. A request the estimator cannot
    flatten (a non-string IR field, AUDIT #184) must still end the stream."""
    gw = Gateway(Router(_config(max_resumes=0)), CostEngine())

    async def no_usage() -> object:
        # message_start carries NO usage, and message_delta reports only
        # output_tokens, so `real_usage.prompt == 0` and the estimator fallback
        # runs — the normal case for streaming-only upstreams.
        yield _ant(
            {"type": "message_start", "message": {
                "id": "m1", "model": "claude-x", "content": []}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "hi"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
             "usage": {"output_tokens": 2}},
            {"type": "message_stop"})

    # A request whose IR carries a non-string text, exactly as a sloppy client
    # produces after a codec misses the guard (AUDIT #184).
    req = ir.Request(
        model="claude-x",
        messages=[ir.Message(role="user", parts=[ir.TextPart(text=5)])],
        stream=True)
    ctx = RequestContext(surface="messages", ir_req=req, group="claude-x")

    try:
        for host in ("https://r75a.example/v1", "https://r75b.example/v1"):
            respx.post(f"{host}/messages").mock(
                side_effect=[httpx.Response(200, content=no_usage())
                             for _ in range(4)])
        out = await _drain(gw, ctx)
    finally:
        await gw.aclose()
    assert isinstance(out[-1], (dl.StreamEnd, dl.StreamError)), (
        "the stream did not terminate: the estimator took the pump down "
        f"(AUDIT #184/#211): {[type(d).__name__ for d in out]}")
    assert any(isinstance(d, dl.UsageFinal) for d in out), \
        "the client must still be told the usage the gateway billed"
    assert any(isinstance(d, dl.StreamEnd) for d in out), (
        "a clean upstream completion must not be reported as an error: "
        f"{[type(d).__name__ for d in out]}")


# ---------------------------------------------------------------------------
# #184 — the estimator is total
# ---------------------------------------------------------------------------

def test_flatten_request_text_coerces_non_string_fields():
    """A non-string ``TextPart.text`` / tool ``name`` / ``description`` must not
    raise ``TypeError``: the estimator is the pump's usage fallback, so raising
    there is a hang, not a 500 (AUDIT #184, #211)."""
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[
            ir.TextPart(text=5),
            ir.ThinkingPart(text=None),
            ir.ToolUsePart(id="t1", name=7, raw_args=None, args={"a": 1}),
            ir.ToolResultPart(tool_use_id="t1", content=3),
        ])],
        tools=[ir.Tool(name=9, description=11)],
        stream=True)
    ctx = RequestContext(surface="chat", ir_req=req, group="g")
    text = flatten_request_text(ctx)
    assert isinstance(text, str)
    # The values are still represented, not silently dropped.
    for token in ("5", "7", "9", "11", "3"):
        assert token in text, f"{token!r} missing from {text!r}"


def test_flatten_request_text_is_unchanged_for_normal_requests():
    """The coercion must be a no-op on well-formed input — the estimator's
    numbers are billed, so the join cannot alter them."""
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hello world")])],
        tools=[ir.Tool(name="t", description="does things")],
        stream=True)
    ctx = RequestContext(surface="chat", ir_req=req, group="g")
    assert flatten_request_text(ctx) == 'hello world t does things {"type":"object"}'


# ---------------------------------------------------------------------------
# The bounded-time contract itself: a hang must fail, not wedge
# ---------------------------------------------------------------------------

@respx.mock
async def test_terminal_frame_arrives_within_the_client_budget():
    """Guard the guard: if the failure path ever regresses to hanging, this
    asserts the *timing*, so the failure mode is a clean assertion error rather
    than a suite that never returns."""
    gw = Gateway(Router(_config(max_resumes=0)), CostEngine())

    async def stalling() -> object:
        yield _ant(
            {"type": "message_start", "message": {
                "id": "m1", "model": "claude-x", "content": [],
                "usage": {"input_tokens": 3, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "hi"}})
        await asyncio.sleep(30)

    try:
        for host in ("https://r75a.example/v1", "https://r75b.example/v1"):
            respx.post(f"{host}/messages").mock(
                side_effect=[httpx.Response(200, content=stalling())
                             for _ in range(4)])
        started = asyncio.get_running_loop().time()
        out = await _drain(gw, _ctx())
        elapsed = asyncio.get_running_loop().time() - started
    finally:
        await gw.aclose()
    assert isinstance(out[-1], dl.StreamError), \
        f"no terminal frame: {[type(d).__name__ for d in out]}"
    assert elapsed < CLIENT_TIMEOUT_S, (
        f"the terminal frame took {elapsed:.1f}s — the idle timeout (0.3s) is "
        "not bounding the failure path")
