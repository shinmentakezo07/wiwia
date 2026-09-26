"""Regression tests for combo recovery from an upstream's first SSE error frame."""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx
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
from wiwi.core.gateway import Gateway
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl


def _combo_config(stream_resume: str = "enabled") -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(
                name="openrouter",
                provider="openrouter",
                base_url="https://openrouter.test/api/v1",
                keys=[KeyDef(label="or", key="test-or")],
            ),
            ProviderDef(
                name="healthy",
                provider="openai",
                base_url="https://healthy.test/v1",
                keys=[KeyDef(label="main", key="test-healthy")],
            ),
        ],
        model_list=[
            ModelEntry(
                model_name="combo",
                wiwi_params=DeploymentParams(
                    provider="openrouter", model="vendor/model"
                ),
            ),
            ModelEntry(
                model_name="combo",
                wiwi_params=DeploymentParams(provider="healthy", model="vendor/model"),
            ),
        ],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:",
        ),
        router_settings=RouterSettings(
            num_retries=1,
            stream_resume=stream_resume,
            stream_resume_max_retries=1,
        ),
    )


def _single_openai_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(
                name="openai",
                provider="openai",
                keys=[KeyDef(label="main", key="test-openai")],
            )
        ],
        model_list=[
            ModelEntry(
                model_name="gpt-4o",
                wiwi_params=DeploymentParams(provider="openai", model="gpt-4o"),
            )
        ],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:",
        ),
    )


def _context() -> RequestContext:
    return RequestContext(
        surface="chat",
        ir_req=ir.Request(
            model="combo",
            messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
            stream=True,
        ),
        group="combo",
    )


def _openrouter_error_frame() -> bytes:
    error = {
        "error": {
            "code": 502,
            "message": "JSON error injected into SSE stream",
            "metadata": {"error_type": "provider_unavailable"},
        },
        "choices": [{"delta": {"content": ""}, "finish_reason": "error"}],
    }
    return b"data: " + json.dumps(error).encode() + b"\n\n"


def _healthy_frame() -> bytes:
    return (
        b'data: {"choices":[{"delta":{"content":"resumed"},'
        b'"finish_reason":"stop"}],"usage":'
        b'{"prompt_tokens":1,"completion_tokens":1}}\n\n'
        b"data: [DONE]\n\n"
    )


async def _drain(gateway: Gateway, ctx: RequestContext) -> list[dl.IRStreamDelta]:
    return [delta async for delta in gateway.stream(ctx)]


def _assert_one_clean_terminal(deltas: list[dl.IRStreamDelta]) -> None:
    assert len([delta for delta in deltas if isinstance(delta, dl.StreamStart)]) == 1
    assert [delta for delta in deltas if isinstance(delta, dl.StreamError)] == []
    terminals = [
        delta for delta in deltas if isinstance(delta, (dl.StreamEnd, dl.StreamError))
    ]
    assert len(terminals) == 1
    assert isinstance(terminals[0], dl.StreamEnd)
    assert deltas[-1] is terminals[0]


@respx.mock
async def test_openrouter_first_event_error_resumes_healthy_combo_deployment():
    """The error pump stops before a delayed sibling can deliver the continuation."""

    async def primary_body():
        yield _openrouter_error_frame()
        await asyncio.sleep(0.05)

    async def healthy_body():
        await asyncio.sleep(0.1)
        yield _healthy_frame()

    openrouter = respx.post(
        "https://openrouter.test/api/v1/chat/completions"
    ).mock(return_value=httpx.Response(200, content=primary_body()))
    healthy = respx.post("https://healthy.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=healthy_body())
    )
    gateway = Gateway(Router(_combo_config()), CostEngine())

    try:
        deltas = await asyncio.wait_for(
            _drain(gateway, _context()), timeout=2.0
        )
    finally:
        await gateway.aclose()

    assert openrouter.call_count == 1
    assert healthy.call_count == 1
    _assert_one_clean_terminal(deltas)
    assert "".join(
        delta.text for delta in deltas if isinstance(delta, dl.TextDelta)
    ) == "resumed"
    outbound = json.loads(healthy.calls[0].request.content)
    assert outbound["messages"] == [{"role": "user", "content": "hi"}]
    assert gateway.router.groups["combo"][0].inflight == 0
    assert gateway.router.groups["combo"][1].inflight == 0


async def test_resume_settles_failed_pump_before_starting_sibling():
    """The superseded pump cannot remain alive after ``pump_task`` is replaced."""
    gateway = Gateway(Router(_combo_config()), CostEngine())
    primary_finished = asyncio.Event()
    observed: dict[str, bool] = {}
    calls = 0

    async def controlled_pump(dep, key, ctx, queue, ready, err_box):
        nonlocal calls
        calls += 1
        ready.set()
        if calls == 1:
            await queue.put(dl.StreamError("first event failed", "connection"))
            try:
                await asyncio.sleep(0.05)
            finally:
                primary_finished.set()
            return
        observed["primary_finished_before_resume"] = primary_finished.is_set()
        await queue.put(dl.UsageFinal(prompt=1, output=1))
        await queue.put(dl.Finish("stop"))
        await queue.put(dl.StreamEnd())

    gateway._pump = controlled_pump  # type: ignore[assignment]
    try:
        deltas = await asyncio.wait_for(
            _drain(gateway, _context()), timeout=2.0
        )
    finally:
        await gateway.aclose()

    assert calls == 2
    assert observed["primary_finished_before_resume"] is True
    _assert_one_clean_terminal(deltas)


async def test_cancel_resistant_failed_pump_cannot_block_resume(monkeypatch):
    """Cancellation cleanup gets its own bound instead of hanging the consumer."""
    monkeypatch.setattr("wiwi.core.gateway._PUMP_CANCEL_GRACE_S", 0.01)
    gateway = Gateway(Router(_combo_config()), CostEngine())
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    release_cleanup = asyncio.Event()
    calls = 0

    async def controlled_pump(dep, key, ctx, queue, ready, err_box):
        nonlocal calls
        calls += 1
        ready.set()
        if calls == 1:
            await queue.put(dl.StreamError("first event failed", "connection"))
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cleanup_started.set()
                try:
                    await release_cleanup.wait()
                finally:
                    cleanup_finished.set()
                raise
        await queue.put(dl.UsageFinal(prompt=1, output=1))
        await queue.put(dl.Finish("stop"))
        await queue.put(dl.StreamEnd())

    gateway._pump = controlled_pump  # type: ignore[assignment]
    try:
        deltas = await asyncio.wait_for(
            _drain(gateway, _context()), timeout=0.2
        )
        assert cleanup_started.is_set()
    finally:
        release_cleanup.set()
        await asyncio.wait_for(cleanup_finished.wait(), timeout=1.0)
        await gateway.aclose()

    assert calls == 2
    _assert_one_clean_terminal(deltas)


async def test_consumer_cancellation_keeps_failed_pump_owned(monkeypatch):
    """Cancelling during initial settlement still installs a completion owner."""
    monkeypatch.setattr("wiwi.core.gateway.pump_cancel_grace", lambda grace: 0.01)
    gateway = Gateway(Router(_combo_config()), CostEngine())
    primary_waiting = asyncio.Event()
    cancel_received = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    loop_errors: list[dict] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
    old_task: asyncio.Task | None = None
    calls = 0

    async def controlled_pump(dep, key, ctx, queue, ready, err_box):
        nonlocal calls, old_task
        calls += 1
        old_task = asyncio.current_task()
        dep.inflight += 1
        try:
            ready.set()
            await queue.put(dl.StreamError("first event failed", "connection"))
            primary_waiting.set()
            while True:
                try:
                    await release_cleanup.wait()
                    break
                except asyncio.CancelledError:
                    cancel_received.set()
            raise RuntimeError("late cleanup failure")
        finally:
            dep.inflight -= 1
            cleanup_finished.set()

    gateway._pump = controlled_pump  # type: ignore[assignment]
    drain_task = asyncio.create_task(_drain(gateway, _context()))
    try:
        await asyncio.wait_for(primary_waiting.wait(), timeout=1.0)
        await asyncio.sleep(0)
        drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain_task
        await asyncio.wait_for(cancel_received.wait(), timeout=1.0)
        release_cleanup.set()
        await asyncio.wait_for(cleanup_finished.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
    finally:
        release_cleanup.set()
        if not drain_task.done():
            drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drain_task
        await gateway.aclose()
        loop.set_exception_handler(previous_handler)

    assert calls == 1
    assert old_task is not None
    assert gateway.router.groups["combo"][0].inflight == 0
    assert getattr(old_task, "_log_traceback", False) is False
    assert loop_errors == []


@respx.mock
async def test_pump_terminal_preserves_provider_error_metadata(monkeypatch):
    """The guarded terminal path must not flatten status or provider etype."""
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=b"data: {}\n\n")
    )

    def error_event(self, event, data):
        return [dl.StreamError(
            "rate limited",
            kind="status",
            status=429,
            etype="rate_limit_error",
        )]

    monkeypatch.setattr(OpenAIAdapter, "decode_stream_event", error_event)
    gateway = Gateway(Router(_single_openai_config()), CostEngine())
    ctx = RequestContext(
        surface="messages",
        ir_req=ir.Request(
            model="gpt-4o",
            messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
            stream=True,
        ),
        group="gpt-4o",
    )

    try:
        deltas = await asyncio.wait_for(_drain(gateway, ctx), timeout=2.0)
    finally:
        await gateway.aclose()

    errors = [delta for delta in deltas if isinstance(delta, dl.StreamError)]
    assert len(errors) == 1
    assert (errors[0].status, errors[0].etype) == (429, "rate_limit_error")


@respx.mock
async def test_pump_terminal_uses_status_for_key_health(monkeypatch):
    """A preserved 429 follows normal key-pool rate-limit accounting."""
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=b"data: {}\n\n")
    )

    def error_event(self, event, data):
        return [dl.StreamError(
            "rate limited",
            kind="status",
            status=None,
            etype="rate_limit_error",
        )]

    monkeypatch.setattr(OpenAIAdapter, "decode_stream_event", error_event)
    config = _single_openai_config()
    config.router_settings.failover_mode = "standard"
    config.router_settings.cooldown_time = 60.0
    gateway = Gateway(Router(config), CostEngine())
    ctx = RequestContext(
        surface="chat",
        ir_req=ir.Request(
            model="gpt-4o",
            messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
            stream=True,
        ),
        group="gpt-4o",
    )

    try:
        await asyncio.wait_for(_drain(gateway, ctx), timeout=2.0)
    finally:
        await gateway.aclose()

    key = gateway.router.providers["openai"].keys[0]
    assert key.status == "cooling"
    assert not key.available


@pytest.mark.parametrize(
    ("status", "etype", "expected_key_status"),
    [
        (429, "rate_limit_error", "cooling"),
        (403, "permission_error", "active"),
        (400, "invalid_request_error", "active"),
    ],
)
async def test_note_stream_failure_respects_provider_health_policy(
    status, etype, expected_key_status
):
    """Explicit status and etype follow the same pool policy as pre-connect errors."""
    config = _single_openai_config()
    config.router_settings.failover_mode = "standard"
    gateway = Gateway(Router(config), CostEngine())
    deployment = gateway.router.groups["gpt-4o"][0]
    key = deployment.provider.keys[0]

    try:
        await gateway._note_stream_failure(
            deployment, key, status=status, etype=etype)
    finally:
        await gateway.aclose()

    assert key.status == expected_key_status
    assert bool(key.available) is (expected_key_status == "active")
    assert deployment.fails == []


@pytest.mark.parametrize("resume_mode", ["off", "content_only"])
@respx.mock
async def test_first_event_error_keeps_disabled_resume_modes_unchanged(resume_mode):
    """No-content errors do not resume unless the operator selected ``enabled``."""
    openrouter = respx.post(
        "https://openrouter.test/api/v1/chat/completions"
    ).mock(
        return_value=httpx.Response(200, content=_openrouter_error_frame())
    )
    healthy = respx.post("https://healthy.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=_healthy_frame())
    )
    gateway = Gateway(Router(_combo_config(resume_mode)), CostEngine())

    try:
        deltas = await asyncio.wait_for(
            _drain(gateway, _context()), timeout=2.0
        )
    finally:
        await gateway.aclose()

    assert openrouter.call_count == 1
    assert healthy.call_count == 0
    assert len([delta for delta in deltas if isinstance(delta, dl.StreamStart)]) == 1
    terminals = [
        delta for delta in deltas if isinstance(delta, (dl.StreamEnd, dl.StreamError))
    ]
    assert len(terminals) == 1
    assert isinstance(terminals[0], dl.StreamError)
    assert deltas[-1] is terminals[0]
