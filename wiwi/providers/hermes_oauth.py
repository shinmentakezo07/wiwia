"""Hermes (Nous Portal) OAuth — device-code login, polling, and token refresh.

Nous Portal speaks standard OAuth 2.0 device authorization (RFC 8628). The
contract below is verified against the Hermes Agent CLI's own client
(``hermes_cli/auth_nous.py`` + ``hermes_cli/auth_device_flow.py``):

- **Device code request:** ``POST {portal}/api/oauth/device/code`` with form
  ``client_id`` + ``scope``. Returns ``device_code``, ``user_code``,
  ``verification_uri``, ``verification_uri_complete``, ``expires_in``, ``interval``.
- **Token poll:** ``POST {portal}/api/oauth/token`` with
  ``grant_type=urn:ietf:params:oauth:grant-type:device_code`` + ``device_code``.
  Returns ``access_token`` / ``refresh_token`` on approval; ``authorization_pending``
  while the user has not finished in the browser; ``access_denied`` if refused.
- **Refresh:** ``POST {portal}/api/oauth/token`` with ``grant_type=refresh_token``
  and the refresh token in the ``x-nous-refresh-token`` header (a Nous-specific
  shape, not the RFC 6749 body form). An ``invalid_grant`` is terminal and means
  the session must be re-established with a fresh device-code login.

The access token is sent to the inference host as a plain
``Authorization: Bearer <token>`` (see ``hermes_adapter.HermesAdapter``), which
is why a stored token lands on the key pool as ``hermes:<token>`` — the adapter
strips the prefix so the bearer carries only the JWT.

``expires_within_lead`` mirrors the CLI's lead-window rule: refresh only when the
access token is within ``REFRESH_LEAD_S`` of expiry, so a still-valid token is
never needlessly rotated.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

# Verified Nous Portal OAuth endpoints and client identity (from the Hermes CLI).
PORTAL_BASE = "https://portal.nousresearch.com"
CLIENT_ID = "hermes-cli"
SCOPE = "inference:invoke"
DEVICE_CODE_URL = f"{PORTAL_BASE}/api/oauth/device/code"
TOKEN_URL = f"{PORTAL_BASE}/api/oauth/token"
GRANT_TYPE_DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"

# Refresh the access token this many seconds before it expires.
REFRESH_LEAD_S = 120
_TIMEOUT_S = 30.0
# Codes whose refresh can never succeed by retrying — force a fresh login.
_TERMINAL_CODES = frozenset({"invalid_grant", "invalid_token", "refresh_token_reused"})


class HermesAuthError(RuntimeError):
    """A Nous OAuth failure. ``relogin_required`` marks a terminal grant death."""

    def __init__(self, message: str, *, code: str | None = None,
                 relogin_required: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.relogin_required = relogin_required


def request_device_code(*, client_id: str | None = None,
                        scope: str | None = None) -> dict[str, Any]:
    """Start a device-code authorization. Returns the raw device-code payload."""
    client_id = client_id or CLIENT_ID
    scope = scope or SCOPE
    with httpx.Client(timeout=_TIMEOUT_S, headers={"Accept": "application/json"}) as client:
        resp = client.post(DEVICE_CODE_URL,
                           data={"client_id": client_id, "scope": scope})
    resp.raise_for_status()
    data = resp.json()
    for field in ("device_code", "user_code", "verification_uri",
                  "verification_uri_complete", "expires_in", "interval"):
        if field not in data:
            raise HermesAuthError(f"device code response missing {field!r}")
    return data


def poll_token(device_code: str, *, client_id: str | None = None,
               poll_interval: int = 5, expires_in: int = 600,
               single_poll: bool = False) -> dict[str, Any] | None:
    """Poll the token endpoint until the user approves or the code expires.

    ``authorization_pending`` keeps waiting; ``access_denied`` and any other
    error raise immediately. Transient 5xx / rate-limit responses back off
    rather than aborting a login the user may still be completing.

    With ``single_poll=True`` exactly one request is made and ``None`` is
    returned on ``authorization_pending`` (the caller drives the retry cadence
    instead of wiwi holding the request open); the ``expires_in`` deadline is
    ignored.
    """
    client_id = client_id or CLIENT_ID
    interval = max(1, min(int(poll_interval or 1), 5))
    deadline = time.monotonic() + max(1, int(expires_in))
    with httpx.Client(timeout=_TIMEOUT_S, headers={"Accept": "application/json"}) as client:
        while single_poll or time.monotonic() < deadline:
            resp = client.post(TOKEN_URL, data={
                "grant_type": GRANT_TYPE_DEVICE_CODE,
                "client_id": client_id,
                "device_code": device_code,
            })
            if resp.status_code == 200:
                payload = resp.json()
                if "access_token" not in payload:
                    raise HermesAuthError("token response missing access_token")
                return payload
            try:
                err = resp.json()
            except ValueError:
                err = {}
            code = str(err.get("error") or "") if isinstance(err, dict) else ""
            if code == "authorization_pending":
                if single_poll:
                    return None
                time.sleep(interval)
                continue
            if code == "slow_down":
                interval = min(interval + 1, 30)
                time.sleep(interval)
                continue
            if resp.status_code >= 500 or resp.status_code in (408, 429):
                time.sleep(min(interval * 2, 30))
                continue
            desc = str(err.get("error_description") or "") \
                if isinstance(err, dict) else ""
            detail = desc or code or "authentication failed"
            raise HermesAuthError(f"Nous Portal rejected the device code: {detail}",
                                  code=code or "access_denied")
    raise HermesAuthError("Timed out waiting for device-code approval.", code="expired")


def refresh_access_token(refresh_token: str, *, client_id: str | None = None) -> dict[str, Any]:
    """Exchange a refresh token for a new access token.

    Nous sends the refresh token in the ``x-nous-refresh-token`` header (not the
    RFC 6749 body form). An ``invalid_grant`` / revoked / reused refresh token is
    terminal: the caller must run a fresh device-code login.
    """
    client_id = client_id or CLIENT_ID
    with httpx.Client(timeout=_TIMEOUT_S, headers={"Accept": "application/json"}) as client:
        resp = client.post(
            TOKEN_URL,
            headers={"x-nous-refresh-token": refresh_token},
            data={"grant_type": "refresh_token", "client_id": client_id},
        )
    if resp.status_code == 200:
        payload = resp.json()
        if "access_token" not in payload:
            raise HermesAuthError("refresh response missing access_token",
                                  code="invalid_token", relogin_required=True)
        return payload
    if resp.status_code >= 500:
        raise HermesAuthError(f"Nous Portal is temporarily unavailable "
                              f"(HTTP {resp.status_code}).", code="temporarily_unavailable")
    try:
        err = resp.json()
    except ValueError:
        err = {}
    err = err if isinstance(err, dict) else {}
    code = err.get("error")
    # A 401/403 with no OAuth ``error`` code still means the token endpoint
    # refused the grant; treat it as invalid_grant (terminal).
    if code is None and resp.status_code in (401, 403):
        code = "invalid_grant"
    code = str(code) if code is not None else None
    desc = str(err.get("error_description") or "refresh token exchange failed")
    relogin = code in _TERMINAL_CODES
    raise HermesAuthError(f"Nous Portal token refresh failed: {desc}", code=code,
                          relogin_required=relogin)


def expires_within_lead(expires_at: float | None, now: float,
                        lead: int = REFRESH_LEAD_S) -> bool:
    """True when the token is expired, expiring within *lead* seconds, or has
    an unknown expiry — i.e. it should be refreshed before use."""
    if expires_at is None:
        return True
    try:
        return float(expires_at) - float(now) <= lead
    except (TypeError, ValueError):
        return True
