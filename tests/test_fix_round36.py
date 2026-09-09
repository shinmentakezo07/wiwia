"""Round-36 regression tests: Cline live CLI and core version fingerprints."""
from __future__ import annotations

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
from wiwi.providers import cline_version as cv
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.cline_adapter import ClineAdapter
from wiwi.providers.registry import fresh_adapter
from wiwi.server.app import create_app


@pytest.fixture(autouse=True)
def _seed_versions():
    cv._set_cached_for_tests("9.9.9", "8.8.8", time.monotonic())
    yield
    cv._set_cached_for_tests(None, None, 0.0)


def _key(secret: str = "workos:test-token") -> ProviderKeyRef:
    return ProviderKeyRef(label="default", secret=secret)


def test_headers_use_live_cli_and_core_versions():
    h = fresh_adapter("cline").headers(_key())

    assert h["User-Agent"] == "Cline/9.9.9"
    assert h["X-CLIENT-VERSION"] == "9.9.9"
    assert h["X-PLATFORM-VERSION"] == "9.9.9"
    assert h["X-CORE-VERSION"] == "8.8.8"
    assert h["Authorization"] == "Bearer workos:test-token"


def test_headers_sanitize_cached_versions():
    cv._set_cached_for_tests(
        "3.0.61\r\nX-Evil: injected",
        "0.0.82\r\nX-Evil: injected",
        time.monotonic(),
    )

    h = ClineAdapter().headers(_key())

    assert h["User-Agent"] == "Cline/3.0.61X-Evil: injected"
    assert h["X-CLIENT-VERSION"] == "3.0.61X-Evil: injected"
    assert h["X-PLATFORM-VERSION"] == "3.0.61X-Evil: injected"
    assert h["X-CORE-VERSION"] == "0.0.82X-Evil: injected"
    assert "X-Evil" not in h


def test_headers_fallback_to_unknown_before_first_fetch():
    cv._set_cached_for_tests(None, None, 0.0)
    h = ClineAdapter().headers(_key())

    assert h["User-Agent"] == "Cline/unknown"
    assert h["X-CLIENT-VERSION"] == "unknown"
    assert h["X-PLATFORM-VERSION"] == "unknown"
    assert h["X-CORE-VERSION"] == "unknown"


def test_version_accessors_follow_cache_refresh():
    cv._set_cached_for_tests("3.0.61", "0.0.82", time.monotonic())

    assert cv.client_version() == "3.0.61"
    assert cv.core_version() == "0.0.82"


def test_stale_logic_five_minute_ttl():
    now = time.monotonic()
    cv._set_cached_for_tests("3.0.61", "0.0.82", now)

    assert cv.is_stale(now + cv.TTL_S - 1) is False
    assert cv.is_stale(now + cv.TTL_S + 1) is True


@respx.mock
async def test_refresh_version_caches_both_npm_versions():
    cv._set_cached_for_tests(None, None, 0.0)
    respx.get(cv.NPM_CLI_LATEST_URL).respond(json={"version": "3.0.61"})
    core_route = respx.get(cv.NPM_CORE_LATEST_URL).respond(
        json={"version": "0.0.82"},
    )

    assert await cv.refresh_version() == ("3.0.61", "0.0.82")
    assert core_route.calls[0].request.headers.get("accept") == "application/json"
    assert cv.get_cached_cli_version() == "3.0.61"
    assert cv.get_cached_core_version() == "0.0.82"


@respx.mock
async def test_refresh_version_rejects_invalid_payloads_without_clearing_cache():
    cv._set_cached_for_tests("3.0.61", "0.0.82", time.monotonic())
    respx.get(cv.NPM_CLI_LATEST_URL).respond(json={"version": 123})
    respx.get(cv.NPM_CORE_LATEST_URL).respond(json={"version": 456})

    assert await cv.refresh_version() == (None, None)
    assert cv.get_cached_cli_version() == "3.0.61"
    assert cv.get_cached_core_version() == "0.0.82"


@respx.mock
async def test_refresh_version_keeps_each_stale_value_on_failure():
    cv._set_cached_for_tests("9.9.9", "8.8.8", time.monotonic())
    respx.get(cv.NPM_CLI_LATEST_URL).respond(status_code=500)
    respx.get(cv.NPM_CORE_LATEST_URL).respond(json={"version": "0.0.82"})

    assert await cv.refresh_version() == (None, "0.0.82")
    assert cv.get_cached_cli_version() == "9.9.9"
    assert cv.get_cached_core_version() == "0.0.82"

    respx.get(cv.NPM_CLI_LATEST_URL).mock(
        return_value=httpx.Response(200, json={"version": "3.0.61"}),
    )
    respx.get(cv.NPM_CORE_LATEST_URL).mock(
        return_value=httpx.Response(500),
    )

    assert await cv.refresh_version() == ("3.0.61", None)
    assert cv.get_cached_cli_version() == "3.0.61"
    assert cv.get_cached_core_version() == "0.0.82"


async def test_version_refresh_task_start_stop():
    worker = cv.ClineVersionRefresh()
    worker.start()
    assert worker._task is not None
    worker.start()  # idempotent
    await worker.stop()
    assert worker._task is None


def _cline_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(
                name="cline",
                provider="cline",
                base_url="https://api.cline.bot/api/v1",
                keys=[KeyDef(label="default", key="workos:test-token")],
            )
        ],
        model_list=[
            ModelEntry(
                model_name="cline-model",
                wiwi_params=DeploymentParams(
                    provider="cline",
                    model="z-ai/glm-5.2",
                ),
            )
        ],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:",
        ),
    )


@pytest_asyncio.fixture
async def cline_client():
    app = create_app(_cline_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            yield client


@respx.mock
async def test_cline_chat_request_sends_live_versions(cline_client):
    route = respx.post(
        "https://api.cline.bot/api/v1/chat/completions",
    ).respond(
        json={
            "id": "chatcmpl-x",
            "object": "chat.completion",
            "model": "z-ai/glm-5.2",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "hello",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
    )

    response = await cline_client.post(
        "/v1/chat/completions",
        json={
            "model": "cline-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": "Bearer sk-wiwi-master-test"},
    )

    assert response.status_code == 200, response.text
    assert route.called
    sent = route.calls[0].request.headers
    assert sent.get("user-agent") == "Cline/9.9.9"
    assert sent.get("x-client-version") == "9.9.9"
    assert sent.get("x-platform-version") == "9.9.9"
    assert sent.get("x-core-version") == "8.8.8"
