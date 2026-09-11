"""Round-44 regression tests: WorkBuddy live CodeBuddy CLI version fingerprint.

The WorkBuddy provider pinned its ``User-Agent`` to ``CLI/2.63.2 CodeBuddy/2.63.2``
— a CodeBuddy CLI release from 2026-03-17, six months and ~87 releases behind
the current npm latest. Mirror the Cline/OpenCode fix: poll the npm registry in
the background every five minutes, keep the result in a module cache, and let
the synchronous ``headers()`` path read it without doing I/O.

The bare-token path also omitted ``User-Agent`` entirely, so httpx's own
``python-httpx/…`` UA leaked upstream.
"""
from __future__ import annotations

import json
import time

import httpx
import pytest
import pytest_asyncio
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
from wiwi.providers import workbuddy_version as wv
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.registry import fresh_adapter
from wiwi.providers.workbuddy_adapter import WorkBuddyAdapter
from wiwi.server.app import create_app

NESTED_SECRET = json.dumps({
    "auth": {
        "accessToken": "at-1",
        "refreshToken": "rt-1",
        "expiresAt": int(time.time()) + 3600,
        "domain": "workbuddy.ai",
    },
    "account": {"uid": "10001", "enterpriseId": "ent-9", "nickname": "wb-user"},
})


@pytest.fixture(autouse=True)
def _seed_version():
    wv._set_cached_for_tests("9.9.9", time.monotonic())
    yield
    wv._set_cached_for_tests(None, 0.0)


def _key(secret: str = NESTED_SECRET) -> ProviderKeyRef:
    return ProviderKeyRef(label="default", secret=secret)


# -- header fingerprint ------------------------------------------------------

def test_headers_use_live_codebuddy_version():
    h = fresh_adapter("workbuddy").headers(_key())
    assert h["User-Agent"] == "CLI/9.9.9 CodeBuddy/9.9.9"


def test_headers_fall_back_to_unknown_before_first_fetch():
    """No cached version yet (cold start, registry unreachable) must not crash."""
    wv._set_cached_for_tests(None, 0.0)
    h = WorkBuddyAdapter().headers(_key())
    assert h["User-Agent"] == "CLI/unknown CodeBuddy/unknown"


def test_bare_token_path_carries_live_user_agent():
    """A non-auth-JSON secret takes the paste-a-token path.

    Pre-fix that path set no ``User-Agent`` at all, so httpx's default
    ``python-httpx/…`` leaked upstream (AUDIT #91).
    """
    h = WorkBuddyAdapter().headers(_key(secret="at-raw-pasted-token"))
    assert h["User-Agent"] == "CLI/9.9.9 CodeBuddy/9.9.9"
    assert h["Authorization"] == "Bearer at-raw-pasted-token"
    assert h["X-Requested-With"] == "XMLHttpRequest"


def test_version_is_sanitized_against_header_injection():
    """A poisoned registry payload must not be able to inject a header."""
    wv._set_cached_for_tests("2.1.0\r\nX-Evil: injected", time.monotonic())
    h = WorkBuddyAdapter().headers(_key())
    assert "X-Evil" not in h
    assert h["User-Agent"] == (
        "CLI/2.1.0X-Evil: injected CodeBuddy/2.1.0X-Evil: injected"
    )


def test_blank_cached_version_falls_back_to_unknown():
    wv._set_cached_for_tests("   ", time.monotonic())
    assert wv.client_version() == "unknown"


# -- cache staleness ---------------------------------------------------------

def test_is_stale_at_ttl_boundary():
    now = 1_000_000.0
    wv._set_cached_for_tests("2.150.0", now)
    assert wv.is_stale(now + wv.TTL_S - 1) is False
    assert wv.is_stale(now + wv.TTL_S) is True


def test_is_stale_when_never_fetched():
    wv._set_cached_for_tests(None, 0.0)
    assert wv.is_stale(time.monotonic()) is True


# -- registry refresh --------------------------------------------------------

@respx.mock
async def test_refresh_version_caches_npm_latest():
    wv._set_cached_for_tests(None, 0.0)
    respx.get(wv.NPM_LATEST_URL).respond(json={"version": "2.150.0"})
    fetched = await wv.refresh_version()
    assert fetched == "2.150.0"
    assert wv.get_cached_version() == "2.150.0"
    assert wv.client_version() == "2.150.0"


@respx.mock
async def test_refresh_version_upgrades_on_change():
    """A published version bump must be adopted on the next sweep."""
    wv._set_cached_for_tests("2.63.2", time.monotonic())
    respx.get(wv.NPM_LATEST_URL).respond(json={"version": "2.151.0"})
    await wv.refresh_version()
    assert wv.get_cached_version() == "2.151.0"


@respx.mock
async def test_refresh_version_keeps_stale_cache_on_http_error():
    wv._set_cached_for_tests("2.150.0", 0.0)
    respx.get(wv.NPM_LATEST_URL).respond(status_code=503)
    assert await wv.refresh_version() is None
    assert wv.get_cached_version() == "2.150.0", "stale value must survive"


@respx.mock
async def test_refresh_version_keeps_stale_cache_on_network_error():
    wv._set_cached_for_tests("2.150.0", 0.0)
    respx.get(wv.NPM_LATEST_URL).mock(side_effect=httpx.ConnectError("boom"))
    assert await wv.refresh_version() is None
    assert wv.get_cached_version() == "2.150.0"


@respx.mock
async def test_refresh_version_rejects_malformed_payload():
    wv._set_cached_for_tests("2.150.0", 0.0)
    respx.get(wv.NPM_LATEST_URL).respond(json={"name": "codebuddy-code"})
    assert await wv.refresh_version() is None
    assert wv.get_cached_version() == "2.150.0"


# -- background worker -------------------------------------------------------

async def test_worker_start_and_stop_are_idempotent():
    worker = wv.WorkBuddyVersionRefresh()
    worker.start()
    worker.start()  # second start must not spawn a second task
    await worker.stop()
    await worker.stop()  # double stop must not raise


# -- end-to-end --------------------------------------------------------------

def _wb_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(
                name="workbuddy-main",
                provider="workbuddy",
                keys=[KeyDef(label="default", key=NESTED_SECRET)],
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
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:",
        ),
    )


@pytest_asyncio.fixture
async def wb_client():
    app = create_app(_wb_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            yield client


@respx.mock
async def test_gateway_sends_live_user_agent_upstream(wb_client):
    """The upstream request must identify as the live CodeBuddy CLI."""
    lines = [
        (b'data: {"id":"x","object":"chat.completion.chunk","model":"m","choices":['
         b'{"index":0,"delta":{"role":"assistant","content":"hi"},'
         b'"finish_reason":null}]}'),
        (b'data: {"id":"x","object":"chat.completion.chunk","model":"m","choices":['
         b'{"index":0,"delta":{},"finish_reason":"stop"}]}'),
        b"data: [DONE]",
    ]
    route = respx.post("https://www.workbuddy.ai/v2/chat/completions").respond(
        status_code=200,
        content=b"\n\n".join(lines) + b"\n\n",
        headers={"Content-Type": "text/event-stream"},
    )
    r = await wb_client.post(
        "/v1/chat/completions",
        json={"model": "wb-model",
              "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"},
    )
    assert r.status_code == 200, r.text
    assert route.called
    sent = route.calls[0].request.headers
    assert sent.get("user-agent") == "CLI/9.9.9 CodeBuddy/9.9.9"
    assert sent.get("x-product") == "SaaS"
