"""Round-46 regression tests — AUDIT #119.

#119: a force_stream probe receives business errors inside SSE ``data:``
frames. Treating the wrapped envelope as an unparseable healthy 200 restores a
still-dead key into probation and exposes it to live traffic.

The pair of tests below is discriminating: the first fails against the pre-fix
source (the SSE error envelope restored the dead key); the second is a control
that must keep passing — a healthy SSE probe body must still restore the key, so
the fix cannot overcorrect into never restoring force_stream keys.
"""

from __future__ import annotations

import orjson
import respx

from wiwi.config import (
    DeploymentParams,
    HealerSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.core.recovery import HealthHealer
from wiwi.router.router import ProviderKey, Router

WORKBUDDY_URL = "https://copilot.tencent.com/v2/chat/completions"


def _workbuddy_router() -> tuple[Router, ProviderKey]:
    """Single-key WorkBuddy router with a terminally retired (dead-session) key."""
    cfg = WiwiConfig(
        providers=[
            ProviderDef(
                name="workbuddy-main",
                provider="workbuddy",
                keys=[KeyDef(label="default", key="test-token")],
            )
        ],
        model_list=[
            ModelEntry(
                model_name="wb-model",
                wiwi_params=DeploymentParams(
                    provider="workbuddy-main",
                    model="claude-sonnet-4.5",
                ),
            )
        ],
    )
    router = Router(cfg)
    key = router.providers["workbuddy-main"].keys[0]
    key.mark_invalid(None)
    return router, key


@respx.mock
async def test_healer_does_not_restore_key_from_sse_error_envelope():
    """A force-stream error envelope must not count as a healthy probe."""
    router, key = _workbuddy_router()

    envelope = b'data: {"code":12153,"msg":"Offline user session"}\n\n'
    route = respx.post(WORKBUDDY_URL).respond(status_code=200, content=envelope)
    healer = HealthHealer(router, HealerSettings(probes_to_restore=1))
    try:
        await healer._sweep()
    finally:
        await healer.stop()

    assert route.called
    assert orjson.loads(route.calls.last.request.content)["stream"] is True
    assert key.status == "invalid", (
        f"SSE-wrapped error restored a dead key to {key.status!r}"
    )


@respx.mock
async def test_healer_restores_key_from_healthy_sse_probe():
    """Control: a healthy SSE probe body must still restore the key.

    Guards against overcorrection — the fix must classify the *error envelope*
    as unhealthy, not SSE bodies in general.
    """
    router, key = _workbuddy_router()

    healthy = (
        b'data: {"choices":[{"delta":{"role":"assistant","content":"ok"}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    route = respx.post(WORKBUDDY_URL).respond(status_code=200, content=healthy)
    healer = HealthHealer(router, HealerSettings(probes_to_restore=1))
    try:
        await healer._sweep()
    finally:
        await healer.stop()

    assert route.called
    assert key.status == "probation", (
        f"healthy SSE probe no longer restores the key: {key.status!r}"
    )
