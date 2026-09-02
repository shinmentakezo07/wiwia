"""Round-23 regression: proxy logs emit gateway-op events.

Regression target: the /console/proxy-logs page was always empty. The ring,
pump, SSE replay, ``/admin/logs/proxy`` endpoint, and frontend all worked
(pinned by test_admin_api), but nothing called ``log_proxy`` during normal
gateway operations — the only producer was the internal-error handler in
app.py. The documented gateway ops (upstream 5xx, key cooldown/invalid,
retries, fallback switches, mid-stream deaths) never emitted a proxy event.

Fix: emit proxy events from the routing loop (``execute_with_retries``) and
the stream pump failure path, via a ``log_proxy`` callback set on the Router
by the app at startup.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar
from unittest.mock import patch

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
    RouterSettings,
    WiwiConfig,
)
from wiwi.core.gateway import Gateway
from wiwi.cost.pricing import CostEngine
from wiwi.providers.base import WiwiError
from wiwi.router import router as router_mod
from wiwi.router.router import Router, execute_with_retries
from wiwi.server.app import create_app


async def _noop_sleep(*_args, **_kwargs) -> None:  # keep retry loop instant
    return None


def _config(**router_overrides) -> WiwiConfig:
    rs = RouterSettings(num_retries=1, allowed_fails=2, cooldown_time=0.05)
    for k, v in router_overrides.items():
        setattr(rs, k, v)
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="k1", weight=3),
                              KeyDef(label="b", key="k2", weight=1)]),
            ProviderDef(name="p2", provider="anthropic",
                        keys=[KeyDef(label="c", key="k3")]),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p2", model="gpt-4o")),
            ModelEntry(model_name="claude",
                       wiwi_params=DeploymentParams(provider="p2", model="claude-x")),
        ],
        router_settings=rs,
    )


def test_upstream_failure_emits_proxy_log_event():
    """A retryable upstream 5xx must emit a proxy-log event naming the status
    and deployment. Previously nothing called ``log_proxy`` here, so the
    proxy-log page stayed empty even while the gateway was failing over."""
    r = Router(_config())
    events: list[tuple[str, str, str]] = []
    r.log_proxy = lambda level, message, request_id="": events.append(
        (level, message, request_id))

    async def call_one(dep, key, ctx):
        raise WiwiError(500, "api_connection_error", "boom", retryable=True)

    class Ctx:
        group = "gpt-4o"
        request_id = "req-1"
        attempts: ClassVar[list] = []
        started = 0.0

    with patch.object(router_mod.asyncio, "sleep", new=_noop_sleep), \
            pytest.raises(WiwiError):
        asyncio.run(execute_with_retries(r, Ctx(), call_one))

    assert events, "a failed attempt must emit a proxy-log event"
    levels = {lvl for lvl, _msg, _rid in events}
    assert "warn" in levels or "error" in levels
    # The event should identify the upstream status and/or error.
    assert any("500" in msg or "boom" in msg for _lvl, msg, _rid in events)


def test_fallback_switch_emits_proxy_log_event():
    """A cross-group fallback must emit an informational proxy event naming
    the fallback group, so the proxy-log page shows the routing decision."""
    cfg = _config()
    cfg.router_settings.fallbacks = {"gpt-4o": ["claude"]}
    # Give the fallback group its own provider so its key is not cooled by the
    # failed primary-group attempt (they'd otherwise share provider p2/key c).
    cfg.providers.append(
        ProviderDef(name="p3", provider="anthropic",
                    keys=[KeyDef(label="d", key="k4")]))
    cfg.model_list.append(
        ModelEntry(
            model_name="claude",
            wiwi_params=DeploymentParams(provider="p3", model="claude-x"),
        ))
    r = Router(cfg)
    events: list[tuple[str, str, str]] = []
    r.log_proxy = lambda level, message, request_id="": events.append(
        (level, message, request_id))

    calls: list[str] = []

    async def call_one(dep, key, ctx):
        calls.append(dep.provider.name)
        if dep.group == "claude":
            return "ok"
        raise WiwiError(500, "api_connection_error", "boom", retryable=True)

    class Ctx:
        group = "gpt-4o"
        request_id = "req-2"
        attempts: ClassVar[list] = []
        started = 0.0

    with patch.object(router_mod.asyncio, "sleep", new=_noop_sleep):
        result = asyncio.run(execute_with_retries(r, Ctx(), call_one))

    assert result == "ok"  # landed on the fallback group
    assert any("claude" in msg for _lvl, msg, _rid in events), events

def test_midstream_failure_emits_proxy_log_event():
    """A mid-stream death (idle/timeout/truncation) must emit a proxy event
    naming the deployment, so the proxy-log page shows gateway stream health."""
    r = Router(_config())
    events: list[tuple[str, str, str]] = []
    r.log_proxy = lambda level, message, request_id="": events.append(
        (level, message, request_id))
    gw = Gateway(r, CostEngine())
    try:
        dep = r.groups["gpt-4o"][0]
        key = dep.provider.keys[0]

        class Ctx:
            request_id = "req-3"

        asyncio.run(gw._note_stream_failure(dep, key, Ctx()))  # type: ignore[arg-type]
    finally:
        asyncio.run(gw.aclose())

    assert events, "a mid-stream failure must emit a proxy-log event"
    lvl, msg, rid = events[0]
    assert lvl == "warn"
    assert "stream" in msg and "gpt-4o" in msg
    assert rid == "req-3"

@respx.mock
async def test_failing_request_populates_proxy_logs_endpoint():
    """End-to-end: a request that fails upstream must produce proxy events
    visible via ``/admin/logs/proxy`` — the exact feed the /console/proxy-logs
    page reads. This proves the AppState wiring (router.log_proxy =
    logs.log_proxy) reaches the ring."""
    app = create_app(WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k1")])],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
    ))
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            500, json={"error": {"message": "upstream boom"}}))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
            }, headers={"Authorization": "Bearer sk-wiwi-master-test"})
            assert r.status_code == 502  # upstream 500 normalized upstream
            # give the ring pump a beat to drain the proxy queue
            await asyncio.sleep(0.05)
            logs = await c.get("/admin/logs/proxy",
                               headers={"Authorization":
                                        "Bearer sk-wiwi-master-test"})
            assert logs.status_code == 200
            rows = logs.json()["logs"]
    assert rows, "a failing request must populate /admin/logs/proxy"
    assert any("upstream" in row["message"] and "boom" in row["message"]
               for row in rows), rows
