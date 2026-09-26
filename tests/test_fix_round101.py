"""Regression: ``stream_resume: off`` must never fall back after content.

``_attempt_resume`` only refuses the ``content_only`` mode (when the tape is
empty), so the ``off`` check lives solely in ``Gateway.stream``'s mid-stream
branch. Dropping it there made the shipped default resume anyway — with a tape
that was never recorded, which rebuilds the *original* request, so the client
received the partial answer followed by a full regenerated one (and paid for
both). AUDIT #290.
"""

from __future__ import annotations

import asyncio
import json

import httpx
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
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl


def _config(stream_resume: str) -> WiwiConfig:
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


def _dying_frames() -> bytes:
    """One content delta, then OpenRouter's error event."""
    error = {
        "error": {
            "code": 502,
            "message": "JSON error injected into SSE stream",
            "metadata": {"error_type": "provider_unavailable"},
        },
        "choices": [{"delta": {"content": ""}, "finish_reason": "error"}],
    }
    return (
        b'data: {"choices":[{"delta":{"content":"partial "}}]}\n\n'
        + b"data: "
        + json.dumps(error).encode()
        + b"\n\n"
    )


def _healthy_frames() -> bytes:
    return (
        b'data: {"choices":[{"delta":{"content":"resumed"},'
        b'"finish_reason":"stop"}],"usage":'
        b'{"prompt_tokens":1,"completion_tokens":1}}\n\n'
        b"data: [DONE]\n\n"
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


async def _drain(gateway: Gateway, ctx: RequestContext) -> list[dl.IRStreamDelta]:
    return [delta async for delta in gateway.stream(ctx)]


@respx.mock
async def test_resume_off_never_calls_a_fallback_after_content():
    """``off`` is the default: a mid-stream error surfaces instead of restarting."""
    respx.post("https://openrouter.test/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=_dying_frames())
    )
    healthy = respx.post("https://healthy.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=_healthy_frames())
    )
    gateway = Gateway(Router(_config("off")), CostEngine())

    try:
        deltas = await asyncio.wait_for(_drain(gateway, _context()), timeout=3.0)
    finally:
        await gateway.aclose()

    assert healthy.call_count == 0
    text = "".join(d.text for d in deltas if isinstance(d, dl.TextDelta))
    assert text == "partial "
    assert [d for d in deltas if isinstance(d, dl.StreamEnd)] == []
    terminals = [
        d for d in deltas if isinstance(d, (dl.StreamEnd, dl.StreamError))
    ]
    assert len(terminals) == 1
    assert isinstance(terminals[0], dl.StreamError)


@respx.mock
async def test_content_only_still_resumes_after_content():
    """The non-default modes keep their documented behaviour."""
    respx.post("https://openrouter.test/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=_dying_frames())
    )
    healthy = respx.post("https://healthy.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=_healthy_frames())
    )
    gateway = Gateway(Router(_config("content_only")), CostEngine())

    try:
        deltas = await asyncio.wait_for(_drain(gateway, _context()), timeout=3.0)
    finally:
        await gateway.aclose()

    assert healthy.call_count == 1
    assert [d for d in deltas if isinstance(d, dl.StreamError)] == []
    terminals = [
        d for d in deltas if isinstance(d, (dl.StreamEnd, dl.StreamError))
    ]
    assert len(terminals) == 1
    assert isinstance(terminals[0], dl.StreamEnd)
    assert len([d for d in deltas if isinstance(d, dl.StreamStart)]) == 1
