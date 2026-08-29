"""Round-7 regression tests: on-demand Cline token refresh on 401.

Bug: Cline rotates refresh tokens, and its access tokens can be invalidated
upstream at any time (Cline sometimes returns 401 mid-session even when the
JWT ``exp`` is in the future). The background auto-refresh worker only
refreshes inside the 5-minute lead window before the access token's
``expires_at``, so a 401 with 50+ minutes of nominal life left used to
fail the request with the upstream message ``Unauthorized: Please make
sure you're using the latest version of Cline and re-authenticate your
Cline account.``.

Fix: when a Cline request returns 401, the gateway refreshes the token
synchronously (using the stored ``refresh_token``), updates the in-memory
``ProviderKey.secret`` + DB, and retries the request once before bubbling
the error up.
"""

from __future__ import annotations

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
from wiwi.providers.cline_oauth import CLINE_API_BASE
from wiwi.router.router import Router
from wiwi.server.config_store import ConfigStore

CLINE_UPSTREAM = "https://api.cline.bot/api/v1/chat/completions"
EXPIRY_FAR_FUTURE = "2099-01-01T00:00:00Z"

# Cline's upstream is streaming-only; even when the client asked for
# non-streaming, the gateway reassembles SSE into a turn.  The "happy path"
# response is a sequence of SSE chunks that the streaming pump folds back
# into text + usage.
def _cline_sse_hello() -> bytes:
    chunks = (
        (b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk",'
         b'"model":"z-ai/glm-5.2",'
         b'"choices":[{"index":0,"delta":{"role":"assistant","content":"hello"},'
         b'"finish_reason":null}]}\n\n'),

        (b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk",'
         b'"model":"z-ai/glm-5.2",'
         b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
         b'"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n'),

        b'data: [DONE]\n\n',
    )
    return b"".join(chunks)

_OK_RESPONSE = _cline_sse_hello()

# The exact upstream error message the user reported.
_REAUTH_BODY = json.dumps({
    "success": False,
    "error": {"message": "Unauthorized: Please make sure you're using the"
                         " latest version of Cline and re-authenticate your"
                         " Cline account.",
              "code": "unauthorized"},
}).encode()


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="cline-prov", provider="cline",
                               base_url="https://api.cline.bot/api/v1",
                               keys=[KeyDef(label="default",
                                            key="workos:stale-access")])],
        model_list=[ModelEntry(
            model_name="cline-model",
            wiwi_params=DeploymentParams(provider="cline-prov",
                                         model="z-ai/glm-5.2"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=2,
                                       cooldown_time=60.0),
    )


def _build_gateway_with_store():
    """Return (gateway, app_state_like, config_store) — the gateway with
    an attached config_store and router so on-demand refresh can find the
    stored refresh_token and write back the rotated secret."""
    from dataclasses import dataclass

    from sqlalchemy.ext.asyncio import create_async_engine

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


# -- 1. 401 from Cline triggers on-demand refresh + retry ---------------------


@pytest.mark.asyncio
@respx.mock
async def test_401_triggers_on_demand_refresh_and_retries():
    """Stale token gets 401 → gateway refreshes → retry succeeds."""
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        await cs.set_setting(
            "cline_oauth:cline-prov",
            {"refresh_token": "ref-active",
             "expires_at": EXPIRY_FAR_FUTURE,
             "email": "u@x.io"})

        refresh_route = respx.post(f"{CLINE_API_BASE}/auth/refresh").mock(
            return_value=httpx.Response(200, json={"data": {
                "accessToken": "acc-fresh-1234567890",
                "refreshToken": "ref-rotated",
                "expiresAt": EXPIRY_FAR_FUTURE,
            }})
        )
        upstream = respx.post(CLINE_UPSTREAM).mock(side_effect=[
            httpx.Response(401, content=_REAUTH_BODY),
            httpx.Response(200, content=_OK_RESPONSE),
        ])

        # Attach the on-demand refresh hook to the gateway now that the
        # store + state are wired. The fix installs a callable; tests
        # inject one that talks to our store.
        from wiwi.providers import cline_auto_refresh
        gw._on_demand_cline_refresh = cline_auto_refresh.refresh_for_provider(
            state)  # type: ignore[attr-defined]

        req = ir.Request(
            model="cline-model", stream=False,
            messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        )
        ctx = RequestContext(surface="chat", ir_req=req, group="cline-model")
        turn = await gw.complete(ctx)

        assert turn.text == "hello"
        assert refresh_route.call_count == 1
        assert upstream.call_count == 2
        # First call: stale token → 401. Second call: fresh token → 200.
        first_auth = upstream.calls[0].request.headers["Authorization"]
        second_auth = upstream.calls[1].request.headers["Authorization"]
        assert first_auth == "Bearer workos:stale-access"
        assert second_auth == "Bearer workos:acc-fresh-1234567890"
        key0 = state.router.providers["cline-prov"].keys[0]
        assert key0.secret == "acc-fresh-1234567890"
        record = await cs.get_setting("cline_oauth:cline-prov")
        assert record["refresh_token"] == "ref-rotated"
    finally:
        await cs.engine.dispose()
        await gw.aclose()


# -- 2. Refresh failure: 401 is surfaced, no infinite loop --------------------


@pytest.mark.asyncio
@respx.mock
async def test_401_then_refresh_failure_surfaces_error():
    """If the on-demand refresh itself fails, surface the original 401
    rather than retrying forever."""
    from wiwi.providers import cline_auto_refresh
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        await cs.set_setting(
            "cline_oauth:cline-prov",
            {"refresh_token": "ref-active",
             "expires_at": EXPIRY_FAR_FUTURE,
             "email": "u@x.io"})

        respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
            502, content=b"bad gw")
        upstream = respx.post(CLINE_UPSTREAM).respond(
            401, content=_REAUTH_BODY)

        gw._on_demand_cline_refresh = cline_auto_refresh.refresh_for_provider(  # type: ignore[attr-defined]
            state)

        req = ir.Request(
            model="cline-model", stream=False,
            messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        )
        ctx = RequestContext(surface="chat", ir_req=req, group="cline-model")
        from wiwi.providers.base import WiwiError
        with pytest.raises(WiwiError) as ei:
            await gw.complete(ctx)
        assert ei.value.status in (401, 502)
        # Upstream was called exactly once (no retry after refresh fails).
        assert upstream.call_count == 1
    finally:
        await cs.engine.dispose()
        await gw.aclose()


# -- 3. No refresh_token stored: 401 surfaced, no refresh attempt ------------


@pytest.mark.asyncio
@respx.mock
async def test_401_without_stored_refresh_token_surfaces_error():
    """No stored refresh_token (e.g. truncated login) → on-demand refresh
    is impossible → surface the 401."""
    from wiwi.providers import cline_auto_refresh
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        # Deliberately NO cline_oauth:cline-prov setting.

        upstream = respx.post(CLINE_UPSTREAM).respond(
            401, content=_REAUTH_BODY)
        refresh_route = respx.post(f"{CLINE_API_BASE}/auth/refresh")

        gw._on_demand_cline_refresh = cline_auto_refresh.refresh_for_provider(  # type: ignore[attr-defined]
            state)

        req = ir.Request(
            model="cline-model", stream=False,
            messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        )
        ctx = RequestContext(surface="chat", ir_req=req, group="cline-model")
        from wiwi.providers.base import WiwiError
        with pytest.raises(WiwiError) as ei:
            await gw.complete(ctx)
        assert ei.value.status in (401, 502)
        assert not refresh_route.called
        assert upstream.call_count == 1
    finally:
        await cs.engine.dispose()
        await gw.aclose()
