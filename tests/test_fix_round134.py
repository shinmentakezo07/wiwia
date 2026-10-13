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
from wiwi.providers import hermes_auto_refresh, hermes_oauth
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
            # One Nous account per pool key, stored as a hermes:<json> blob
            # (AUDIT #427). The stale access token is what the first 401 hits.
            keys=[KeyDef(label="default",
                         key=hermes_oauth.encode_credential(
                             "stale-access", "ref-active",
                             time.time() + 3600, "acct@example.com"))])],
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
    find the account's refresh grant and write back the rotated secret.
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


_UNSET = object()


def _set_account(state, *, access="stale-access", refresh="ref-active",
                 expires_at=_UNSET, label="acct@example.com"):
    """Set the single pool key's credential blob (the per-account model).

    ``expires_at`` defaults to a fresh future expiry, but an explicit ``None``
    is honoured as "unknown expiry" (which reads as due for Hermes).
    """
    if expires_at is _UNSET:
        expires_at = time.time() + 3600
    state.router.providers["hermes-prov"].keys[0].secret = (
        hermes_oauth.encode_credential(access, refresh, expires_at, label))


async def _seed(state, *, expires_at):
    _set_account(state, expires_at=expires_at)


# -- 1. 401 from Nous triggers on-demand refresh + one-shot retry -------------


@respx.mock
async def test_401_triggers_on_demand_refresh_and_retries():
    """Stale token gets 401 → gateway refreshes → retry succeeds."""
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        # Far-future expiry: the 401 hook must refresh UNCONDITIONALLY,
        # ignoring the stored expiry (AUDIT #169 rationale).
        await _seed(state, expires_at=time.time() + 3600)

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
        # First call carries the stale bearer; the retry carries the fresh one
        # (the access token is the bearer, prefix/JSON stripped by the adapter).
        assert upstream.calls[0].request.headers["Authorization"] == "Bearer stale-access"
        assert upstream.calls[1].request.headers["Authorization"] == "Bearer acc-fresh-abcdef"
        # The pool key's credential blob was rotated in memory.
        cred = hermes_oauth.decode_credential(
            state.router.providers["hermes-prov"].keys[0].secret)
        assert cred["access_token"] == "acc-fresh-abcdef"
        assert cred["refresh_token"] == "ref-rotated"
        assert cred["expires_at"] > time.time()
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
        await _seed(state, expires_at=time.time() - 1)
        await worker._sweep()
        assert refresh_route.call_count == 1
        cred = hermes_oauth.decode_credential(
            state.router.providers["hermes-prov"].keys[0].secret)
        assert cred["access_token"] == "acc-swept"

        # (b) unknown expiry (None) → refresh (unknown = due for Hermes).
        # A fresh worker so (a)'s rotation does not mark (b)'s reseed as
        # "already rotated" via the per-account generation counter.
        await _seed(state, expires_at=None)
        worker = hermes_auto_refresh.HermesAutoRefresh(state)
        await worker._sweep()
        assert refresh_route.call_count == 2

        # (c) far future, outside the lead window → NOT refreshed.
        await _seed(state, expires_at=time.time() + 3600)
        worker = hermes_auto_refresh.HermesAutoRefresh(state)
        await worker._sweep()
        assert refresh_route.call_count == 2  # unchanged
    finally:
        await cs.engine.dispose()
        await gw.aclose()


@respx.mock
async def test_sweeper_skips_providers_without_refresh_token():
    """A pool key that is not a managed OAuth account is never refreshed."""
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        worker = hermes_auto_refresh.HermesAutoRefresh(state)
        refresh_route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "x"}))

        # A bare hermes:<token> key (no JSON blob / no refresh grant).
        state.router.providers["hermes-prov"].keys[0].secret = "hermes:raw-key"
        assert await worker._sweep() == 0
        assert refresh_route.call_count == 0

        # A blob with no refresh_token is also skipped.
        state.router.providers["hermes-prov"].keys[0].secret = (
            hermes_oauth.encode_credential("acc", None, time.time() - 1))
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
        await _seed(state, expires_at=time.time() + 3600)

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
        assert worker._circuit.dead(("hermes-prov", "default"))
    finally:
        await cs.engine.dispose()
        await gw.aclose()


# -- 4. A refresh with no expires_in must not park expiry in the past ---------
#    (AUDIT #425: time.time() + 0 lands in the past, so expires_within_lead is
#    True forever and the sweeper re-refreshes the single-use grant every tick
#    until invalid_grant marks the provider dead.)


def test_compute_expires_at_falls_forward_when_expires_in_missing():
    """The helper behind every expiry write: absent/zero/garbage → a short
    forward window, never the past."""
    from wiwi.providers import hermes_oauth
    from wiwi.providers.hermes_oauth import compute_expires_at

    now = 1_000_000.0
    # Usable value honoured verbatim.
    assert compute_expires_at(3600, now) == now + 3600
    # Missing / None / zero / negative / garbage / bool → forward, not past,
    # and strictly BEYOND the lead window so the next expires_within_lead read
    # is False (a fallback of exactly REFRESH_LEAD_S lands on the boundary and
    # still reads as due — the #425 spin by one second).
    for bad in (None, 0, -5, "x", "", [], True):
        expires_at = compute_expires_at(bad, now)
        assert expires_at > now, f"{bad!r} produced a non-forward expiry"
        assert expires_at == pytest.approx(now + 240)
        assert not hermes_oauth.expires_within_lead(expires_at, now)


@respx.mock
async def test_refresh_without_expires_in_does_not_spin_the_sweeper():
    """End-to-end guard for the #425 storm: a refresh response that omits
    ``expires_in`` must leave the token NOT immediately due."""
    gw, state, cs = _build_gateway_with_store()
    await cs.startup()
    try:
        # Due now, so the first sweep refreshes.
        await _seed(state, expires_at=time.time() - 1)
        refresh_route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={
                "access_token": "acc-no-exp",
                "refresh_token": "ref-no-exp",
                # ``expires_in`` deliberately ABSENT.
            }))
        worker = hermes_auto_refresh.HermesAutoRefresh(state)

        await worker._sweep()
        assert refresh_route.call_count == 1

        # The account's expiry is now in the future (fallback window), so a
        # second immediate sweep must NOT refresh again — the pre-#425 shape
        # wrote ``time.time() + 0`` and the token read as permanently due.
        cred = hermes_oauth.decode_credential(
            state.router.providers["hermes-prov"].keys[0].secret)
        assert cred["expires_at"] > time.time()
        await worker._sweep()
        assert refresh_route.call_count == 1  # no second rotation
    finally:
        await cs.engine.dispose()
        await gw.aclose()

