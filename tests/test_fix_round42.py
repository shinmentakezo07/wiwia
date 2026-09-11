"""Round-42 regression: the live proxy log names the provider key that served
each attempt, successes included.

Regression target: round 23 wired proxy events for gateway *failures* only
(upstream 5xx, fallback switches, mid-stream deaths). A healthy round-robin
therefore produced no proxy line at all, so watching the proxy-log tail gave
no evidence of which pool key served a request — the request log carried
``provider_key_label`` but the live stream stayed silent. Round 23's own
fixtures masked this: every path it asserted ended in an error.

Fix: ``Gateway`` emits one proxy line per attempt through ``_log_attempt``
naming ``[provider/key]``, at every terminal outcome (ok, http_*, transport
error, encode error, ok_after_refresh), in all three call paths —
``_call_once``, ``_complete_via_stream`` (force_stream), and the ``_pump_once``
stream pump.
"""

from __future__ import annotations

import asyncio

import httpx
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
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway
from wiwi.cost.pricing import CostEngine
from wiwi.ir.types import Request
from wiwi.router.router import Router
from wiwi.server.app import create_app


def _config(**router_overrides) -> WiwiConfig:
    rs = RouterSettings(num_retries=0, allowed_fails=2, cooldown_time=0.05)
    for k, v in router_overrides.items():
        setattr(rs, k, v)
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="k1"),
                              KeyDef(label="b", key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
        ],
        router_settings=rs,
    )


def test_successful_attempt_emits_proxy_event_naming_key():
    """A 200 from the upstream must emit a proxy event naming provider and key.

    This is the round-42 core: success used to be silent, so the live proxy
    tail could not show which pool key served a request."""
    r = Router(_config())
    events: list[tuple[str, str, str]] = []
    r.log_proxy = lambda level, message, request_id="": events.append(
        (level, message, request_id))
    gw = Gateway(r, CostEngine())

    ctx = RequestContext(surface="chat", ir_req=Request(model="gpt-4o",
                                                        messages=[]))
    ctx.group = "gpt-4o"
    ctx.request_id = "req-ok"

    dep = r.groups["gpt-4o"][0]
    key = dep.provider.keys[0]

    async def scenario():
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={
                    "id": "x", "object": "chat.completion",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant",
                                             "content": "hi"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }))
            await gw._call_once(dep, key, ctx)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(gw.aclose())

    assert events, "a successful attempt must emit a proxy-log event"
    lvl, msg, rid = events[0]
    assert lvl == "info"
    assert "p1" in msg and key.label in msg, msg
    assert f"[p1/{key.label}]" in msg, msg
    assert rid == "req-ok"


def test_failed_attempt_emits_proxy_event_naming_key():
    """An upstream 500 must still name provider and key on the proxy line."""
    r = Router(_config())
    events: list[tuple[str, str, str]] = []
    r.log_proxy = lambda level, message, request_id="": events.append(
        (level, message, request_id))
    gw = Gateway(r, CostEngine())

    ctx = RequestContext(surface="chat", ir_req=Request(model="gpt-4o",
                                                        messages=[]))
    ctx.group = "gpt-4o"
    ctx.request_id = "req-fail"

    dep = r.groups["gpt-4o"][0]
    key = dep.provider.keys[0]

    async def scenario():
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    500, json={"error": {"message": "boom"}}))
            # The failure is expected; the proxy log it emits is the assertion.
            caught: list[Exception] = []
            try:
                await gw._call_once(dep, key, ctx)
            except Exception as e:  # noqa: BLE001
                caught.append(e)
            assert caught, "a 500 upstream must surface as an error"

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(gw.aclose())

    assert events, "a failed attempt must emit a proxy-log event"
    assert any(f"[p1/{key.label}]" in msg for _l, msg, _r in events), events
    assert any("http_500" in msg for _l, msg, _r in events), events


async def test_round_robin_success_populates_proxy_log_endpoint():
    """End-to-end through the real app: a successful request must make the
    serving provider key visible on ``/admin/logs/proxy`` — the feed the
    proxy-logs page tails."""
    app = create_app(WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="key-one", key="k1")])],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
    ))
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "x", "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }))
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as c:
                r = await c.post("/v1/chat/completions", json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "hi"}],
                }, headers={"Authorization": "Bearer sk-wiwi-master-test"})
                assert r.status_code == 200
                await asyncio.sleep(0.05)  # let the ring pump drain
                logs = await c.get("/admin/logs/proxy", headers={
                    "Authorization": "Bearer sk-wiwi-master-test"})
                assert logs.status_code == 200
                rows = logs.json()["logs"]

    assert rows, "a successful request must populate /admin/logs/proxy"
    assert any("p1" in row["message"] and "key-one" in row["message"]
               for row in rows), rows
