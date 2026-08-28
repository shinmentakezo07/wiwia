"""Tests for the Cline OAuth background auto-refresh worker.

Cline uses single-use rotating refresh tokens, so the worker must refresh
ONLY when the access token is inside the 5-minute lead window before expiry.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
import respx
from asgi_lifespan import LifespanManager

import wiwi.server.app as app_mod
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.providers.cline_auto_refresh import ClineAutoRefresh
from wiwi.providers.cline_oauth import CLINE_API_BASE

MASTER = "sk-wiwi-master-test"

EXPIRY_FUTURE = "2099-01-01T00:00:00Z"
EXPIRY_SOON = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()


def _config(expires_at=EXPIRY_SOON) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="cline-prov", provider="cline",
                              base_url="https://api.cline.bot/api/v1",
                              keys=[KeyDef(label="default",
                                           key="workos:stale-token")])],
        model_list=[ModelEntry(
            model_name="cline-model",
            wiwi_params=DeploymentParams(provider="cline-prov",
                                         model="z-ai/glm-5.2"))],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
    )


def _b64_payload(access="acc-1234567890", refresh="ref-active",
                 expires=EXPIRY_SOON) -> str:
    import base64
    import json
    return base64.b64encode(json.dumps({
        "accessToken": access, "refreshToken": refresh,
        "email": "u@x.io", "expiresAt": expires,
    }).encode()).decode()


async def _connect(client, expires=EXPIRY_SOON):
    await client.post(
        "/admin/cline/oauth/connect",
        headers={"Authorization": f"Bearer {MASTER}"},
        json={"provider": "cline-prov", "code": _b64_payload(expires=expires)},
    )


@pytest.fixture
async def app_state():
    config = _config()
    app = app_mod.create_app(config)
    async with LifespanManager(app):
        yield app.state.wiwi


# -- sweep: not due -----------------------------------------------------------

async def test_sweep_skips_when_not_near_expiry(app_state):
    # Override expires_at to far future
    await app_state.config_store.set_setting(
        "cline_oauth:cline-prov",
        {"refresh_token": "ref-x", "expires_at": EXPIRY_FUTURE, "email": "u@x.io"})
    worker = ClineAutoRefresh(app_state)
    with respx.mock:
        route = respx.post(f"{CLINE_API_BASE}/auth/refresh")
        n = await worker._sweep()
        assert n == 1
        assert not route.called


# -- sweep: refreshes when near expiry ----------------------------------------

@respx.mock
async def test_sweep_refreshes_when_near_expiry(app_state):
    await app_state.config_store.set_setting(
        "cline_oauth:cline-prov",
        {"refresh_token": "ref-old", "expires_at": EXPIRY_SOON, "email": "u@x.io"})
    respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        json={"data": {"accessToken": "acc-new-1234567890",
                       "refreshToken": "ref-new",
                       "expiresAt": EXPIRY_FUTURE}})
    worker = ClineAutoRefresh(app_state)
    n = await worker._sweep()
    assert n == 1
    record = await app_state.config_store.get_setting("cline_oauth:cline-prov")
    assert record["refresh_token"] == "ref-new"
    key0 = app_state.router.providers["cline-prov"].keys[0]
    assert key0.secret == "acc-new-1234567890"


# -- sweep: unrecoverable stops further refresh ------------------------------

@respx.mock
async def test_unrecoverable_sets_circuit_breaker_forever(app_state):
    await app_state.config_store.set_setting(
        "cline_oauth:cline-prov",
        {"refresh_token": "ref-dead", "expires_at": EXPIRY_SOON, "email": "u@x.io"})
    respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        status_code=400, json={"error": "invalid_grant"})
    worker = ClineAutoRefresh(app_state)
    await worker._sweep()
    assert "cline-prov" in worker._circuit
    assert worker._circuit["cline-prov"]["until"] == float("inf")


# -- sweep: transient failure trips circuit breaker --------------------------

@respx.mock
async def test_transient_failure_trips_backoff(app_state):
    await app_state.config_store.set_setting(
        "cline_oauth:cline-prov",
        {"refresh_token": "ref-x", "expires_at": EXPIRY_SOON, "email": "u@x.io"})
    respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        status_code=502, text="bad gateway")
    worker = ClineAutoRefresh(app_state)
    await worker._sweep()
    cb = worker._circuit["cline-prov"]
    assert cb["streak"] == 1
    assert cb["until"] > time.time()


# -- circuit breaker skips refresh --------------------------------------------

@respx.mock
async def test_circuit_breaker_skips_when_in_backoff(app_state):
    await app_state.config_store.set_setting(
        "cline_oauth:cline-prov",
        {"refresh_token": "ref-x", "expires_at": EXPIRY_SOON, "email": "u@x.io"})
    worker = ClineAutoRefresh(app_state)
    # Pre-trip the circuit breaker
    worker._circuit["cline-prov"] = {"streak": 2, "until": time.time() + 9999}
    route = respx.post(f"{CLINE_API_BASE}/auth/refresh")
    await worker._sweep()
    assert not route.called


# -- no providers = no work ----------------------------------------------------

async def test_sweep_no_cline_providers():
    config = WiwiConfig(
        providers=[ProviderDef(name="oai", provider="openai",
                               keys=[KeyDef(label="k", key="sk-x")])],
        model_list=[],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(config)
    async with LifespanManager(app):
        worker = ClineAutoRefresh(app.state.wiwi)
        n = await worker._sweep()
        assert n == 0


# -- start/stop lifecycle -----------------------------------------------------

async def test_start_stop_graceful(app_state):
    worker = ClineAutoRefresh(app_state)
    worker.start()
    await asyncio.sleep(0.05)
    assert worker._task is not None
    await worker.stop()
    assert worker._task is None
