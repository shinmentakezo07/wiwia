"""Regression tests for bug-fix round 4 (2026-08-23).

Covers: build_log_event crash when ctx.usage is None (client disconnects
mid-stream before the pump sets usage), and pure-ASGI RequestIdMiddleware
that replaces BaseHTTPMiddleware (which broke streaming responses during
graceful shutdown).
"""

import contextlib

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import build_log_event
from wiwi.ir import types as ir
from wiwi.server.app import create_app


# -- R1: build_log_event must not crash when usage is None -------------------------
# Happens when a client disconnects mid-stream: the pump is cancelled before
# _price_stream sets ctx.usage, but the finally block still calls build_log_event.
def test_build_log_event_with_none_usage():
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    # simulate a stream that produced tokens then got cancelled before pricing
    ctx.first_token_at = ctx.started
    ctx.last_token_at = ctx.started + 1.0  # stream_secs > 0.05 triggers tps calc
    ctx.usage = None  # pump never set it
    evt = build_log_event(ctx)
    assert evt.tps == 0.0
    assert evt.tok_in == 0
    assert evt.tok_out == 0


def test_build_log_event_with_none_usage_no_tokens():
    """No first_token_at (stream cancelled before any data flowed)."""
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = None
    evt = build_log_event(ctx)
    assert evt.tps == 0.0
    assert evt.tok_out == 0


# -- R1b: non-streaming TPS computed from total latency --------------------------
# OpenRouter reports throughput (output tokens/sec) for all requests, not just
# streaming ones.  For non-streaming requests where first_token_at is never set,
# TPS must fall back to completion_tokens / total_latency.
def test_build_log_event_non_streaming_tps():
    """Non-streaming request: TPS from total latency (no first/last_token_at)."""
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = ir.Usage(prompt_tokens=10, completion_tokens=100)
    # Simulate 500ms total latency by shifting started back in time.
    ctx.started = ctx.started - 0.5
    evt = build_log_event(ctx)
    assert evt.tok_out == 100
    # 100 tokens / 0.5s = 200 tps
    assert abs(evt.tps - 200.0) < 1.0


def test_build_log_event_streaming_tps_preferred_over_latency():
    """Streaming request: stream-phase TPS is used when available."""
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = ir.Usage(prompt_tokens=10, completion_tokens=50)
    ctx.first_token_at = ctx.started + 0.1   # 100ms TTFT
    ctx.last_token_at = ctx.started + 0.6   # 500ms generation phase
    # stream_secs = 0.5 -> 50 / 0.5 = 100 tps
    # total latency = 0.6s -> 50 / 0.6 = 83.3 tps
    # stream-phase should be preferred (100 > 83.3)
    evt = build_log_event(ctx)
    assert abs(evt.tps - 100.0) < 0.1


def test_build_log_event_short_stream_falls_back_to_latency():
    """Stream too short to time (<0.05s): falls back to total latency TPS."""
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = ir.Usage(prompt_tokens=10, completion_tokens=30)
    ctx.first_token_at = ctx.started
    ctx.last_token_at = ctx.started + 0.01  # 10ms — below 0.05 threshold
    ctx.started = ctx.started - 0.3  # 300ms total latency
    # stream_secs = 0.01 (too short) -> fallback: 30 / 0.3 = 100 tps
    evt = build_log_event(ctx)
    assert abs(evt.tps - 100.0) < 1.0


def test_build_log_event_zero_output_tps_is_zero():
    """No output tokens -> TPS is 0 regardless of timing."""
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = ir.Usage(prompt_tokens=10, completion_tokens=0)
    ctx.first_token_at = ctx.started
    ctx.last_token_at = ctx.started + 1.0
    evt = build_log_event(ctx)
    assert evt.tps == 0.0


def test_build_log_event_very_fast_request_tps_is_zero():
    """Latency too short (<50ms) to be meaningful -> TPS is 0."""
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = ir.Usage(prompt_tokens=10, completion_tokens=5)
    # No first/last_token_at, and started is basically now -> latency < 50ms
    evt = build_log_event(ctx)
    assert evt.tps == 0.0


# -- R2: pure ASGI RequestIdMiddleware replaces BaseHTTPMiddleware -----------------
# The old @app.middleware("http") used Starlette BaseHTTPMiddleware, which wraps
# every response in a background task pumping chunks through an anyio memory
# stream.  When uvicorn cancels in-flight tasks during graceful shutdown, that
# pump is cancelled mid-flight and raises WouldBlock/CancelledError.  The pure
# ASGI middleware passes streaming responses through untouched.

def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


@respx.mock
async def test_middleware_sets_request_id_and_latency_on_json(client):
    """Both headers present on a normal JSON response."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(json={
        "id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-4o",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                      "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200
    assert r.headers.get("x-wiwi-request-id")
    assert r.headers.get("x-wiwi-latency-ms")


@respx.mock
async def test_middleware_sets_headers_on_streaming_response(client):
    """Streaming responses get request-id and latency headers too.

    This is the case that broke with BaseHTTPMiddleware during shutdown:
    the streaming response's headers must be injected at http.response.start
    time, before any body chunks flow.
    """
    done = "data: [DONE" + "]\n\n"
    sse = (
        'data: {"choices":[{"delta":{"role":"assistant","content":"Hi"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
        + done
    )
    respx.post("https://api.openai.com/v1/chat/completions").respond(text=sse)
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "stream": True,
        "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200
    assert r.headers.get("x-wiwi-request-id"), "streaming response missing request-id"
    assert r.headers.get("x-wiwi-latency-ms"), "streaming response missing latency"


async def test_middleware_body_size_limit_413(client):
    """Oversized body is rejected with 413 before reaching the handler."""
    # default max_request_body_mb is 50; send 51 MiB
    big = "x" * (51 * 1024 * 1024)
    r = await client.post("/v1/chat/completions",
                          content=big,
                          headers={"Authorization": "Bearer sk-wiwi-master-test",
                                   "Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.headers.get("x-wiwi-request-id"), "413 response missing request-id"


async def test_middleware_request_id_on_error_response(client):
    """Error responses (404 unknown model) still carry the request-id header."""
    r = await client.post("/v1/chat/completions", json={
        "model": "nonexistent", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 404
    assert r.headers.get("x-wiwi-request-id")


async def test_middleware_does_not_use_base_http_middleware():
    """The app must not contain any BaseHTTPMiddleware instances.

    BaseHTTPMiddleware breaks streaming responses during graceful shutdown
    by wrapping them in an internal memory-stream pump that raises
    WouldBlock/CancelledError when cancelled.
    """
    from starlette.middleware.base import BaseHTTPMiddleware

    app = create_app(_config())
    # Force the middleware stack to be built
    _ = app.middleware_stack

    def _walk(mw):
        # Unwrap the middleware chain: each layer wraps an inner app.
        if isinstance(mw, BaseHTTPMiddleware):
            return True
        inner = getattr(mw, "app", None)
        if inner is not None and inner is not mw:
            return _walk(inner)
        return False

    assert not _walk(app.middleware_stack), (
        "BaseHTTPMiddleware found in the stack — streaming responses will "
        "break during graceful shutdown"
    )


# -- R3: pump_task reference must be updated after _attempt_resume ------------
# H1: When a mid-stream resume fires, _attempt_resume creates a new pump task
# locally but the outer stream()'s pump_task reference is never updated. The
# finally block then cancels the *original* pump, not the resume pump, so the
# resume pump leaks its upstream connection. The fix is to return the new
# pump task from _attempt_resume and have stream() update the outer reference.
import asyncio

from wiwi.streaming.resume import StreamTape


async def test_attempt_resume_returns_new_pump_task():
    """_attempt_resume must return the new pump task so the caller can update
    its outer reference (otherwise the resume pump leaks on cancel)."""
    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        RouterSettings,
        WiwiConfig,
    )
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.ir import types as ir
    from wiwi.router.router import Router

    # Two providers in the same model group; primary is the one that "failed"
    # in the outer request, fallback is what _attempt_resume will try.
    cfg = WiwiConfig(
        providers=[
            ProviderDef(name="primary", provider="openai",
                        keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="fallback", provider="openai",
                        keys=[KeyDef(label="b", key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="primary", model="gpt-4o")),
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="fallback", model="gpt-4o")),
        ],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(
            fallbacks={"gpt-4o": ["gpt-4o"]},
            stream_resume="enabled",
            stream_resume_max_retries=1,
        ),
    )
    router = Router(cfg)
    gw = Gateway(router, CostEngine())

    ctx = RequestContext(surface="chat",
                        ir_req=ir.Request(model="gpt-4o",
                                          messages=[ir.Message(role="user",
                                                              parts=[ir.TextPart("hi")])]))
    ctx.group = "gpt-4o"
    ctx.deployment = router.groups["gpt-4o"][0]  # primary
    ctx.provider_key = ctx.deployment.provider.keys[0]

    queue: asyncio.Queue = asyncio.Queue()
    tape = StreamTape()

    # Stub _pump to simulate a successful fallback connection (sets ready,
    # then sleeps until cancelled — this lets us observe whether the outer
    # reference is the one being cancelled, proving the leak is fixed).
    captured: dict = {}

    async def fake_pump(dep, key, c, q, ready, err_box):
        captured["new_dep"] = dep
        captured["new_key"] = key
        ready.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            captured["cancelled"] = True
            raise

    gw._pump = fake_pump  # type: ignore[assignment]

    success, new_task = await gw._attempt_resume(ctx, tape, queue)
    try:
        assert success is True
        assert isinstance(new_task, asyncio.Task)
        assert not new_task.done()
        # The deployment chosen is a function of the candidate list ordering;
        # we don't care *which* one, only that a new task was created and
        # returned (this is the structural fix).
        assert captured["new_dep"] in router.groups["gpt-4o"]
        # Simulate the outer stream() finally: cancel the *returned* task.
        # If the fix is in place, this is the same task _pump is awaiting,
        # and the fake _pump records that it was cancelled.
        new_task.cancel()
        with contextlib.suppress(BaseException):
            await new_task
        assert captured.get("cancelled") is True, (
            "the new pump task must be the one that was cancelled "
            "(regression: outer pump_task was not updated after resume)"
        )
    finally:
        if not new_task.done():
            new_task.cancel()
            with contextlib.suppress(BaseException):
                await new_task


async def test_stream_consumer_cancels_resume_pump_not_original():
    """End-to-end: drive Gateway.stream() with a primary pump that errors
    mid-stream and a fallback pump that records its own cancellation.

    This is the real proof of the H1 fix: a regression that removed the
    `pump_task = new_pump` assignment in stream()'s consumer loop would let
    the original (already-done) primary pump be cancelled by the finally,
    while the active fallback pump leaks. We assert the fallback's pump
    saw the cancel — which only happens if the outer `pump_task` reference
    was updated to point at the new task.
    """
    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        RouterSettings,
        WiwiConfig,
    )
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.ir import types as ir
    from wiwi.router.router import Router
    from wiwi.streaming import deltas as dl

    cfg = WiwiConfig(
        providers=[
            ProviderDef(name="primary", provider="openai",
                        keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="fallback", provider="openai",
                        keys=[KeyDef(label="b", key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="primary", model="gpt-4o")),
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="fallback", model="gpt-4o")),
        ],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(
            fallbacks={"gpt-4o": ["gpt-4o"]},
            stream_resume="enabled",
            stream_resume_max_retries=1,
        ),
    )
    router = Router(cfg)
    gw = Gateway(router, CostEngine())

    # Two distinct fake pumps keyed on the deployment's provider.
    #   - The primary pushes a text delta then a StreamError on the FIRST
    #     call. On the SECOND call (the resume picks it first because
    #     fb_targets resolves the same group), it sets err_box so the
    #     resume moves on to the fallback.
    #   - The fallback hangs and records whether it saw a cancel.
    observed: dict = {
        "fallback_cancelled": False,
        "fallback_started": False,
        "primary_call_count": 0,
    }
    original_pump = gw._pump

    async def fake_pump(dep, key, c, q, ready, err_box):
        if dep.provider.name == "primary":
            observed["primary_call_count"] += 1
            if observed["primary_call_count"] == 1:
                # First call: mid-stream error.
                ready.set()
                await q.put(dl.TextDelta("partial "))
                await q.put(dl.StreamError("upstream died", "connection"))
                return
            # Subsequent calls: fail-fast so the resume moves to the fallback.
            from wiwi.providers.base import WiwiError
            err_box[0] = WiwiError(503, "service_unavailable", "primary down")
            ready.set()
            return
        # Fallback: hang, record cancellation.
        observed["fallback_started"] = True
        ready.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            observed["fallback_cancelled"] = True
            raise

    gw._pump = fake_pump  # type: ignore[assignment]
    try:
        ctx = RequestContext(surface="chat",
                            ir_req=ir.Request(model="gpt-4o",
                                              messages=[ir.Message(role="user",
                                                                  parts=[ir.TextPart("hi")])],
                                              stream=True))
        ctx.group = "gpt-4o"

        gen = gw.stream(ctx)
        deltas_received = []
        try:
            for _ in range(2):
                d = await gen.__anext__()
                deltas_received.append(d)
            # The 3rd read may be the StreamError OR a queued-up delta from
            # the primary's re-pump (in the simpler single-group test the
            # resume reuses the primary, which queues another TextDelta+Error
            # before the resume path picks the fallback). Allow several reads.
            for _ in range(10):
                try:
                    d = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
                    deltas_received.append(d)
                except TimeoutError:
                    break
        finally:
            await gen.aclose()

        # Give the event loop a moment so the cancel propagates to the
        # fallback's fake _pump (it records `fallback_cancelled = True`).
        for _ in range(20):
            if observed["fallback_cancelled"]:
                break
            await asyncio.sleep(0.05)

        assert observed["fallback_started"], (
            "fallback (resume) pump should have started after the primary error"
        )
        assert observed["fallback_cancelled"], (
            "fallback (resume) pump must be the one cancelled by the consumer "
            "loop's finally — if the outer `pump_task` reference wasn't "
            "updated to the new task, the finally would cancel the "
            "already-done primary and the fallback would leak"
        )
    finally:
        gw._pump = original_pump  # type: ignore[assignment]
