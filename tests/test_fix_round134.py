"""Round 134: Hermes (Nous Portal) OAuth auto-refresh.

Bug: Hermes shipped with a working token-refresh protocol
(``hermes_oauth.refresh_access_token``) and an admin endpoint that calls it
(``/admin/hermes/oauth/refresh``), but no automation. Cline and WorkBuddy both
refresh proactively (a background sweeper) *and* on demand (a gateway 401
hook); Hermes had neither. An access token that expired — or was invalidated
upstream — mid-session produced a hard 401 to the caller with no rotation, and
under the default ``failover_mode == "any_error"`` that 401 charged the key's
error streak, so a routine expiry could retire an otherwise healthy key
(AUDIT #423).

Fix: ``wiwi/providers/hermes_auto_refresh.py`` mirrors the Cline refresher — a
60 s sweeper that rotates tokens inside the lead window, and a 401 hook that
rotates unconditionally. Both funnel through one worker (one per-provider lock,
one circuit breaker) via ``_worker_for`` so a rotating refresh token is never
presented twice.

Covers the three behaviours that make the fix correct:
1. a 401 from Nous triggers an on-demand refresh and a one-shot retry;
2. the sweeper refreshes a token that is inside the lead window (and skips one
   that is not), so a still-valid token is never needlessly rotated;
3. a dead refresh grant (invalid_grant) is terminal — no retry storm, and the
   401 surfaces to the caller.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import create_async_engine

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
from wiwi.providers import hermes_auto_refresh
from wiwi.providers.hermes_oauth import TOKEN_URL
from wiwi.router.router import Router
from wiwi.server.config_store import ConfigStore

MASTER = "sk-wiwi-master-test"
# Nous inference is OpenAI Chat Completions-shaped; HermesAdapter opts out of
# Realtime but otherwise behaves like OpenAIAdapter, so a JSON response body is
# the natural non-streaming reply. Hermes is NOT force_stream, so the gateway
# takes the plain complete() path.
NOUS_UPSTREAM = "https://inference-api.nousresearch.com/v1/chat/completions"


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(
            name="hermes-prov", provider="hermes",
            base_url="https://inference-api.nousresearch.com/v1",
            keys=[KeyDef(label="default", key="hermes:stale-access")])],
        model_list=[ModelEntry(
            model_name="hermes-model",
            wiwi_params=DeploymentParams(provider="hermes-prov",
                                         model="Hermes-4-405B"))],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=2,
                                       cooldown_time=60.0),
    )


def _build_gateway_with_store():
    """Return (gateway, mini_app_state, config_store).

    The gateway gets a config_store-bearing state so the on-demand hook can
    find the stored refresh_token and write back the rotated secret.
    """
    config = _config()
    router = Router(config)
    cs = ConfigStore(create_async_engine("sqlite+aiosqlite:///:memory:"))

    @dataclass
    class _Mini:
        router: Router
        config_store: ConfigStore

    state = _Mini(router=router, config_store=cs)
    gw = Gateway(router, CostEngine())
    return gw, state, cs


def _ok_response() -> httpx.Response:
    return httpx.Response(200, json={
        "id": "chatcmpl-1", "object": "chat.completion",
        "model": "Hermes-4-405B",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": "hello"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    })


def _req() -> ir.Request:
    return ir.Request(
        model="hermes-model", stream=False,
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])])


async def _seed(cs, *, expires_at):
    await cs.set_setting(
        "hermes_oauth:hermes-prov",
        {"refresh_token": "ref-active", "expires_at": expires_at})


# -- 1. 401 from Nous triggers on-demand refresh + one-shot retry -------------


@respx.mock
async def test_401_triggers_on_demand_refresh_and_retries():
    """Stale token gets 401 → gateway refreshes → retry succeeds."""
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        # Far-future expiry: the 401 hook must refresh UNCONDITIONALLY,
        # ignoring the stored expiry (AUDIT #169 rationale).
        await _seed(cs, expires_at=time.time() + 3600)

        refresh_route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={
                "access_token": "acc-fresh-abcdef",
                "refresh_token": "ref-rotated",
                "expires_in": 3600,
            }))
        upstream = respx.post(NOUS_UPSTREAM).mock(side_effect=[
            httpx.Response(401, json={"error": {"message": "unauthorized",
                                                "type": "authentication_error"}}),
            _ok_response(),
        ])

        gw._on_demand_refresh_hooks["hermes"] = hermes_auto_refresh.refresh_for_provider(
            state)

        turn = await gw.complete(RequestContext(
            surface="chat", ir_req=_req(), group="hermes-model"))

        assert turn.text == "hello"
        assert refresh_route.call_count == 1
        assert upstream.call_count == 2
        # First call carries the stale bearer; the retry carries the fresh one,
        # with the ``hermes:`` prefix stripped by the adapter.
        assert upstream.calls[0].request.headers["Authorization"] == "Bearer stale-access"
        assert upstream.calls[1].request.headers["Authorization"] == "Bearer acc-fresh-abcdef"
        # The pool key secret was rotated in memory.
        assert state.router.providers["hermes-prov"].keys[0].secret == "hermes:acc-fresh-abcdef"
        # The rotated refresh token and new expiry were persisted.
        record = await cs.get_setting("hermes_oauth:hermes-prov")
        assert record["refresh_token"] == "ref-rotated"
        assert record["expires_at"] > time.time()
    finally:
        await cs.engine.dispose()
        await gw.aclose()


# -- 2. Sweeper refreshes only inside the lead window --------------------------


@respx.mock
async def test_sweeper_refreshes_only_when_due():
    """A token past its lead window is rotated; a fresh one is left alone."""
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        worker = hermes_auto_refresh.HermesAutoRefresh(state)
        refresh_route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={
                "access_token": "acc-swept",
                "refresh_token": "ref-swept",
                "expires_in": 3600,
            }))

        # (a) expired / inside lead → refresh.
        await _seed(cs, expires_at=time.time() - 1)
        await worker._sweep()
        assert refresh_route.call_count == 1
        assert state.router.providers["hermes-prov"].keys[0].secret == "hermes:acc-swept"

        # (b) unknown expiry (None) → refresh (unknown = due for Hermes).
        await _seed(cs, expires_at=None)
        await worker._sweep()
        assert refresh_route.call_count == 2

        # (c) far future, outside the lead window → NOT refreshed.
        await _seed(cs, expires_at=time.time() + 3600)
        await worker._sweep()
        assert refresh_route.call_count == 2  # unchanged
    finally:
        await cs.engine.dispose()
        await gw.aclose()


@respx.mock
async def test_sweeper_skips_providers_without_refresh_token():
    """A provider with no stored OAuth state is skipped, never refreshed."""
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        worker = hermes_auto_refresh.HermesAutoRefresh(state)
        refresh_route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "x"}))

        # No hermes_oauth:<name> record at all.
        assert await worker._sweep() == 0
        assert refresh_route.call_count == 0

        # A record with no refresh_token is also skipped.
        await cs.set_setting("hermes_oauth:hermes-prov", {"expires_at": 0})
        assert await worker._sweep() == 0
        assert refresh_route.call_count == 0
    finally:
        await cs.engine.dispose()
        await gw.aclose()


# -- 3. Dead refresh grant is terminal; 401 surfaces ---------------------------


@respx.mock
async def test_dead_refresh_grant_surfaces_401():
    """invalid_grant is terminal: no retry storm, the 401 is surfaced."""
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        await _seed(cs, expires_at=time.time() + 3600)

        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(400, json={
                "error": "invalid_grant",
                "error_description": "refresh token revoked"}))
        upstream = respx.post(NOUS_UPSTREAM).respond(
            401, json={"error": {"message": "unauthorized",
                                 "type": "authentication_error"}})

        gw._on_demand_refresh_hooks["hermes"] = hermes_auto_refresh.refresh_for_provider(
            state)

        ctx = RequestContext(surface="chat", ir_req=_req(), group="hermes-model")
        with pytest.raises(Exception) as exc_info:
            await gw.complete(ctx)
        # The original 401 is surfaced, not a retry.
        assert "401" in str(exc_info.value) or "authentication" in str(
            exc_info.value).lower()
        # Upstream hit once; the refresh was attempted once; no second POST.
        assert upstream.call_count == 1
        # The circuit is marked dead so the sweeper stops retrying the grant.
        worker = hermes_auto_refresh._worker_for(state)
        assert worker._circuit.dead("hermes-prov")
    finally:
        await cs.engine.dispose()
        await gw.aclose()
