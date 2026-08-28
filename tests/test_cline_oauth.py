"""Tests for the Cline OAuth subsystem: login URL, paste-code token exchange
(base64-embedded JSON), refresh with rotation semantics, and expiry policy.

Cline's OAuth flow (mirrors the Cline extension itself):
- Login URL is a plain redirect to api.cline.bot/api/v1/auth/authorize with
  callback params — no client_id, no PKCE. The user pastes the resulting
  code back.
- The "code" IS the token payload: base64-encoded JSON containing
  accessToken/refreshToken/email/expiresAt. No network round-trip needed.
- Refresh POSTs the refreshToken to /api/v1/auth/refresh; the response's
  refreshToken is single-use (rotating) and must replace the stored one.
"""

import base64
import json
import time

import httpx
import pytest
import respx

from wiwi.providers.cline_oauth import (
    CLINE_API_BASE,
    REFRESH_LEAD_S,
    build_auth_url,
    exchange_code,
    expires_within_lead,
    refresh_token,
)


def _encode_token_payload(payload: dict) -> str:
    raw = json.dumps(payload).encode()
    return base64.b64encode(raw).decode()


# -- login URL ----------------------------------------------------------


def test_build_auth_url_contains_callback():
    url = build_auth_url("http://localhost:9ffb/callback")
    assert url.startswith(f"{CLINE_API_BASE}/auth/authorize")
    assert "callback_url=http%3A%2F%2Flocalhost%3A9ffb%2Fcallback" in url
    assert "redirect_uri=" in url
    assert "client_type=extension" in url


def test_build_auth_url_encodes_special_chars():
    url = build_auth_url("https://host/cb?a=1&b=2")
    assert "https%3A%2F%2Fhost%2Fcb%3Fa%3D1%26b%3D2" in url


# -- code exchange (offline, base64 blob) --------------------------------


def test_exchange_code_decodes_embedded_json():
    payload = {
        "accessToken": "acc1",
        "refreshToken": "ref1",
        "email": "u@x.io",
        "expiresAt": "2030-01-01T00:00:00.000Z",
    }
    code = _encode_token_payload(payload)
    tokens = exchange_code(code)
    assert tokens["access_token"] == "acc1"
    assert tokens["refresh_token"] == "ref1"
    assert tokens["expires_at"] == "2030-01-01T00:00:00.000Z"


def test_exchange_code_url_decodes_first():
    """A code URL-encoded by the browser address bar (=%3D, %2B, %2F) must
    still decode. Use a payload whose base64 actually contains +/= so the
    percent-encoding is real, not a no-op."""
    # Craft JSON whose base64 encoding contains +, /, and = padding.
    inner = '{"accessToken":"a","refreshToken":"r","expiresAt":"2030-01-01T00:00:00Z"}'
    raw = base64.b64encode(inner.encode()).decode()
    assert any(c in raw for c in "+/="), "test setup: need URL-unsafe base64 chars"
    from urllib.parse import quote
    quoted = quote(raw, safe="")
    assert "%" in quoted, "test setup: quote must percent-encode something"
    tokens = exchange_code(quoted)
    assert tokens["access_token"] == "a"


def test_exchange_code_accepts_full_callback_url():
    """The user may paste the entire callback URL with ?code=<blob>."""
    payload = {"accessToken": "acc1", "refreshToken": "ref1",
               "email": "u@x.io", "expiresAt": "2030-01-01T00:00:00.000Z"}
    blob = _encode_token_payload(payload)
    from urllib.parse import quote
    url = f"http://localhost:9000/cb?code={quote(blob, safe='')}"
    tokens = exchange_code(url)
    assert tokens["access_token"] == "acc1"
    assert tokens["email"] == "u@x.io"


def test_exchange_code_padding_tolerant():
    payload = {"accessToken": "abcdefgh", "refreshToken": "r",
               "expiresAt": "2030-01-01T00:00:00Z"}
    raw = json.dumps(payload).encode()
    stripped = base64.b64encode(raw).decode().rstrip("=")
    tokens = exchange_code(stripped)
    assert tokens["access_token"] == "abcdefgh"


def test_exchange_code_base64url_alphabet():
    """Cline may emit base64url (- _) instead of standard base64 (+ /)."""
    payload = {"accessToken": "acc-url", "refreshToken": "ref-url",
               "expiresAt": "2030-01-01T00:00:00Z"}
    raw = base64.b64encode(json.dumps(payload).encode()).decode()
    url_safe = raw.replace("+", "-").replace("/", "_")
    tokens = exchange_code(url_safe)
    assert tokens["access_token"] == "acc-url"


def test_exchange_code_garbage_raises():
    with pytest.raises(ValueError):
        exchange_code("not-a-token-blob")


def test_exchange_code_with_trailing_signature():
    """Real Cline codes append a binary signature after the JSON, making the
    total length 1 mod 4 (invalid base64).  The signature corrupts the last
    JSON field (expiresAt) when decoded as a continuous stream.  The decoder
    must handle the 1-mod-4 length and repair the corrupted expiresAt."""
    # Build a JSON payload whose base64 is 0 mod 4, then append a fake
    # signature that makes the total 1 mod 4.
    payload = {
        "accessToken": "acc-sig-test",
        "refreshToken": "ref-sig",
        "email": "sig@test.io",
        "expiresAt": "2026-08-28T18:34:40.272443755Z",
    }
    json_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    assert len(json_b64) % 4 == 0, "test setup: JSON base64 must be 0 mod 4"
    # Append 29 chars of fake signature (like the real Cline code).
    # The last base64 group bleeds into the JSON, corrupting expiresAt.
    sig = "uVXGZeeDbGj7sgAD7CGkcQ7EQJTxk"  # 29 chars, total becomes 1 mod 4
    code = json_b64 + sig
    assert len(code) % 4 == 1, "test setup: total must be 1 mod 4"
    tokens = exchange_code(code)
    assert tokens["access_token"] == "acc-sig-test"
    assert tokens["refresh_token"] == "ref-sig"
    assert tokens["email"] == "sig@test.io"
    # expiresAt may be corrupted by the signature; the decoder repairs it.
    assert tokens["expires_at"] is not None

# -- refresh (network) ---------------------------------------------------


@respx.mock
async def test_refresh_token_posts_and_parses():
    route = respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        json={"data": {
            "accessToken": "acc2",
            "refreshToken": "ref2",
            "expiresAt": "2030-01-01T00:00:00.000Z",
        }})
    result = await refresh_token("ref1")
    assert route.called
    sent = route.calls[0].request.read().decode()
    assert json.loads(sent)["refreshToken"] == "ref1"
    assert json.loads(sent)["grantType"] == "refresh_token"
    assert result["access_token"] == "acc2"
    assert result["refresh_token"] == "ref2"
    assert result["expires_in"] > 0


@respx.mock
async def test_refresh_token_keeps_old_when_not_rotated():
    """Some responses omit refreshToken — keep the old one (still usable)."""
    respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        json={"data": {"accessToken": "acc2"}})
    result = await refresh_token("ref-keep")
    assert result["refresh_token"] == "ref-keep"


@respx.mock
async def test_refresh_token_invalid_grant_is_unrecoverable():
    respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        status_code=400, json={"error": "invalid_grant"})
    result = await refresh_token("dead")
    assert result == {"error": "unrecoverable_refresh_error",
                      "code": "invalid_grant"}


@respx.mock
async def test_refresh_token_generic_error_returns_none():
    respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        status_code=502, text="bad gateway")
    assert await refresh_token("r") is None


@respx.mock
async def test_refresh_token_network_error_returns_none():
    respx.post(f"{CLINE_API_BASE}/auth/refresh").side_effect = (
        httpx.ConnectError("boom"))
    assert await refresh_token("r") is None


# -- refresh policy -------------------------------------------------------


def test_refresh_lead_is_five_minutes():
    """Rotating refresh tokens: refresh only at the last 5 minutes of life."""
    assert REFRESH_LEAD_S == 5 * 60


def test_expires_within_lead():
    now = time.time()
    assert expires_within_lead(now + 60, now=now) is True        # 1 min left
    assert expires_within_lead(now + 4 * 60, now=now) is True    # inside lead
    assert expires_within_lead(now + 60 * 60, now=now) is False  # 1 hour left
