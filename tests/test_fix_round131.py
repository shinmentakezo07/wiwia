"""Hermes (Nous Portal) OAuth device-code flow + HermesAdapter bearer.

Nous Portal speaks standard OAuth 2.0 device authorization (RFC 8628) — verified
against the Hermes Agent CLI's own client (``hermes_cli/auth_nous.py`` +
``auth_device_flow.py``):

- device code:  POST {portal}/api/oauth/device/code   (client_id, scope)
- poll token:   POST {portal}/api/oauth/token          (grant_type=device_code, device_code)
- refresh:      POST {portal}/api/oauth/token          (grant_type=refresh_token,
                header ``x-nous-refresh-token: <refresh>``)

The access token is sent upstream as a plain ``Authorization: Bearer <token>``.
A stored OAuth token lands on the key pool as ``hermes:<token>``; the adapter
must strip that prefix and send the bare token as the bearer.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from wiwi.providers import hermes_oauth
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.hermes_adapter import HermesAdapter

PORTAL = "https://portal.nousresearch.com"
CLIENT_ID = "hermes-cli"
SCOPE = "inference:invoke"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(hermes_oauth.time, "sleep", lambda *a, **k: None)


# ---------------------------------------------------------------------------
# hermes_oauth module — the verified Nous device-code contract
# ---------------------------------------------------------------------------

def test_constants_match_the_nous_contract():
    assert hermes_oauth.PORTAL_BASE == PORTAL
    assert hermes_oauth.CLIENT_ID == CLIENT_ID
    assert hermes_oauth.SCOPE == SCOPE
    assert hermes_oauth.DEVICE_CODE_URL == f"{PORTAL}/api/oauth/device/code"
    assert hermes_oauth.TOKEN_URL == f"{PORTAL}/api/oauth/token"


@respx.mock
def test_request_device_code_posts_the_expected_form():
    seen = {}

    def _cb(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={
            "device_code": "dev-123",
            "user_code": "ABCD-1234",
            "verification_uri": f"{PORTAL}/device",
            "verification_uri_complete": f"{PORTAL}/device?user_code=ABCD-1234",
            "expires_in": 600,
            "interval": 5,
        })

    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(side_effect=_cb)
    data = hermes_oauth.request_device_code()

    assert seen["url"] == hermes_oauth.DEVICE_CODE_URL
    assert f"client_id={CLIENT_ID}" in seen["body"]
    # httpx form-encodes the body, so the scope's ":" arrives as %3A.
    assert "scope=inference" in seen["body"]
    assert data["device_code"] == "dev-123"
    assert data["user_code"] == "ABCD-1234"


@respx.mock
def test_poll_token_returns_access_token_when_approved():
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "acc-1",
            "refresh_token": "ref-1",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": SCOPE,
        })
    )
    out = hermes_oauth.poll_token("dev-123")
    assert out["access_token"] == "acc-1"
    assert out["refresh_token"] == "ref-1"


@respx.mock
def test_poll_token_raises_auth_error_when_user_denies():
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "access_denied"})
    )
    with pytest.raises(hermes_oauth.HermesAuthError) as exc:
        hermes_oauth.poll_token("dev-123")
    assert "denied" in str(exc.value).lower()


@respx.mock
def test_refresh_access_token_sends_refresh_in_header():
    seen = {}

    def _cb(request: httpx.Request) -> httpx.Response:
        seen["header"] = request.headers.get("x-nous-refresh-token")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "acc-2", "expires_in": 3600})

    respx.post(hermes_oauth.TOKEN_URL).mock(side_effect=_cb)
    out = hermes_oauth.refresh_access_token("ref-old")

    assert seen["header"] == "ref-old"
    assert "grant_type=refresh_token" in seen["body"]
    assert out["access_token"] == "acc-2"


@respx.mock
def test_refresh_terminal_invalid_grant_is_relogin_required():
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    with pytest.raises(hermes_oauth.HermesAuthError) as exc:
        hermes_oauth.refresh_access_token("ref-dead")
    assert exc.value.relogin_required is True


def test_expiry_helpers():
    now = 1_000_000.0
    # Not expiring within the 120s lead window.
    assert hermes_oauth.expires_within_lead(now + 10_000, now) is False
    # Inside the lead window.
    assert hermes_oauth.expires_within_lead(now + 30, now) is True
    # Already past.
    assert hermes_oauth.expires_within_lead(now - 1, now) is True
    # Unknown expiry is treated as needing refresh.
    assert hermes_oauth.expires_within_lead(None, now) is True


# ---------------------------------------------------------------------------
# HermesAdapter — bearer carries the bare token, never the hermes: prefix
# ---------------------------------------------------------------------------

def test_hermes_adapter_bearer_for_plain_key():
    h = HermesAdapter().headers(ProviderKeyRef(label="l", secret="sk-nous-123"))
    assert h["Authorization"] == "Bearer sk-nous-123"


def test_hermes_adapter_bearer_strips_hermes_prefix():
    h = HermesAdapter().headers(ProviderKeyRef(label="l", secret="hermes:tok-abc"))
    assert h["Authorization"] == "Bearer tok-abc"
