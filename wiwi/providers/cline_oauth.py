"""Cline OAuth: login URL, paste-code exchange, and token refresh.

Cline (WorkOS-backed) auth works differently from standard OAuth2:

- **No client_id / no PKCE.** The login "flow" is a redirect to
  ``/api/v1/auth/authorize`` with the callback URL as a query param; Cline
  identifies the caller by its client headers (see cline_adapter), not by a
  registered app.
- **The code is the token payload.** Cline embeds a base64-encoded JSON blob
  (accessToken/refreshToken/email/expiresAt) in the auth code it shows the
  user, so exchanging a pasted code is a pure offline decode — no network.
- **Refresh tokens rotate.** Each refresh consumes the old refresh_token and
  returns a new one; callers must persist the replacement atomically. To
  avoid burning rotations, refresh only when the access token is inside the
  5-minute lead window before expiry (see REFRESH_LEAD_S / expires_within_lead).
"""

from __future__ import annotations

import base64
import re
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
import orjson

CLINE_APP_BASE = "https://app.cline.bot"
CLINE_API_BASE = "https://api.cline.bot/api/v1"

# Refresh lead window (seconds): rotating refresh_tokens mean every refresh
# is a消费 event — only refresh when the access token is within this window
# of expiring. Matches the OmniRoute/Cline-extension behavior of refreshing
# in the final 5 minutes of token life.
REFRESH_LEAD_S = 5 * 60

_UNRECOVERABLE_CODES = {"invalid_grant", "invalid_request"}
_TIMEOUT_S = 30.0


def build_auth_url(callback_url: str) -> str:
    """Login entry point: user opens this in a browser, logs in with their
    Cline account, and copies the resulting code back into the admin UI."""
    q = urlencode({"callback_url": callback_url, "redirect_uri": callback_url,
                   "client_type": "extension"})
    return f"{CLINE_API_BASE}/auth/authorize?{q}"


def _extract_code(raw: str) -> str:
    """Pull the base64 token blob out of whatever the user pasted.

    Accepts a bare base64 blob, a URL-encoded blob (``%3D`` etc. — what the
    browser address bar shows for the ``code`` query param), or a full
    callback URL containing ``?code=<blob>``.
    """
    stripped = raw.strip()
    if "?" in stripped or "#" in stripped:
        # Looks like a URL — extract the code query param.
        parsed = parse_qs(urlparse(stripped).query)
        values = parsed.get("code")
        if values:
            stripped = values[0]
    # Undo any percent-encoding from the address bar (e.g. %3D → =, %2B → +, %2F → /).
    if "%" in stripped:
        stripped = unquote(stripped)
    return stripped


def _b64_decode(raw: str) -> bytes:
    """Decode base64 (standard or base64url), tolerant of missing padding
    and a trailing signature that makes the length 1 mod 4.

    Cline appends a binary signature after the JSON, producing a code whose
    total length is ``1 mod 4`` — invalid for standard base64.  We strip
    trailing chars until the length is valid, then decode.  The signature
    bytes after the JSON closing ``}`` are ignored by the caller.

    Raises ``ValueError`` when the input is not decodable in either alphabet.
    """
    candidate = raw.strip()

    # Strip trailing chars until the length is a valid base64 length
    # (0, 2, or 3 mod 4).  1 mod 4 is impossible — Cline's signature produces
    # this, so we drop the last char and retry if needed.
    while len(candidate) % 4 == 1:
        candidate = candidate[:-1]

    # Restore missing padding.
    pad = len(candidate) % 4
    if pad:
        candidate += "=" * (4 - pad)

    encoded = candidate.encode("ascii")
    # Try standard alphabet first, then base64url (Cline may use either).
    for altchars in (b"+/", b"-_"):
        try:
            return base64.b64decode(encoded, altchars=altchars)
        except (ValueError, base64.binascii.Error):
            continue
    raise ValueError("not a base64 token blob")


# Regex to extract JSON fields from decoded bytes, robust against trailing
# signature bytes that corrupt the last field's closing quote.
_FIELD_RE = re.compile(
    rb'"(accessToken|refreshToken|email|expiresAt|firstName|lastName|name)"'
    rb'\s*:\s*"((?:[^"\\]|\\.)*)"'
)
# Looser pattern for expiresAt whose closing quote may be clobbered by the
# signature: matches up to a `}` or end-of-data.
_EXPIRES_RE = re.compile(rb'"expiresAt"\s*:\s*"([0-9T:\.\-]+Z)')
# Truncated-accessToken pattern: when the signature eats the closing quote
# and all subsequent fields, the accessToken value runs to end-of-data.
# Matches "accessToken":"<everything-to-end>.
_TRUNCATED_ACCESS_RE = re.compile(
    rb'"accessToken"\s*:\s*"(.+)$'
)


def _jwt_claims(token: str) -> dict[str, Any]:
    """Decode the payload (middle segment) of a JWT without verifying.
    Returns {} when the token isn't a well-formed JWT."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1]
    # JWT uses base64url without padding; restore it.
    pad = len(payload_b64) % 4
    if pad:
        payload_b64 += "=" * (4 - pad)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64)
        data = orjson.loads(decoded)
        return data if isinstance(data, dict) else {}
    except (ValueError, orjson.JSONDecodeError):
        return {}


def _extract_fields(decoded: bytes) -> dict[str, Any]:
    """Extract token fields from decoded Cline code bytes.

    Tries ``json.loads`` first (clean codes).  Falls back to regex extraction
    when the trailing signature corrupts the JSON boundary, repairing
    ``expiresAt`` (the field nearest the corruption) by stripping a leading
    digit if the year is 5 digits (e.g. ``22026`` → ``2026``).

    Handles the truncated case where the signature eats the closing quote
    of ``accessToken`` and all subsequent fields (refreshToken/email/
    expiresAt). In that case the accessToken value runs to end-of-data;
    email and expiry are then derived from the JWT claims.
    """
    last_brace = decoded.rfind(b"}")
    if last_brace != -1:
        try:
            data = orjson.loads(decoded[: last_brace + 1])
            if isinstance(data, dict) and data.get("accessToken"):
                return data
        except (orjson.JSONDecodeError, ValueError):
            pass

    # Regex fallback: extract each field independently.
    fields: dict[str, Any] = {}
    for m in _FIELD_RE.finditer(decoded):
        fields[m.group(1).decode()] = m.group(2).decode()

    # Truncated code: accessToken has no closing quote (signature ate it
    # and everything after). Capture the value from the opening quote to
    # end-of-data, then derive email/expiry from the JWT claims.
    if "accessToken" not in fields:
        m = _TRUNCATED_ACCESS_RE.search(decoded)
        if m:
            access = m.group(1).decode("utf-8", errors="replace").rstrip('"')
            if access:
                fields["accessToken"] = access
                claims = _jwt_claims(access)
                if "email" not in fields and claims.get("email"):
                    fields["email"] = claims["email"]
                if "expiresAt" not in fields and claims.get("exp"):
                    from datetime import datetime, timezone
                    dt = datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
                    fields["expiresAt"] = dt.strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z")

    # expiresAt is the last field before the signature; its closing quote is
    # frequently clobbered.  Try the looser pattern and repair the year.
    if "expiresAt" not in fields:
        m = _EXPIRES_RE.search(decoded)
        if m:
            val = m.group(1).decode()
            # Repair: 5-digit year (22026) → 4-digit (2026) by dropping the
            # extra leading digit the signature byte introduced.
            if len(val) > 24 and val.startswith("2") and val[1] == "2":
                val = val[1:]
            fields["expiresAt"] = val

    return fields


def exchange_code(code: str) -> dict[str, Any]:
    """Exchange a pasted Cline auth code for tokens (offline).

    The code embeds a JSON payload as base64 (possibly URL-encoded and/or
    stripped of padding).  Cline appends a binary signature after the JSON,
    which can corrupt the last field when decoded as a continuous stream.
    Accepts a bare blob, a URL-encoded blob (as shown in the browser address
    bar), or a full callback URL with ``?code=``.

    Google OAuth codes may be truncated: the signature eats the closing
    quote of ``accessToken`` and all subsequent fields (refreshToken, email,
    expiresAt).  In that case the accessToken (a JWT) is extracted to
    end-of-data, and email/expiry are derived from the JWT claims.  The
    refresh_token will be ``None`` — auto-refresh won't work until a
    subsequent ``/auth/refresh`` call produces one, but the access token
    is usable for its lifetime.

    Raises ValueError when the code isn't decodable or is missing the
    accessToken.
    """
    raw = _extract_code(code)
    try:
        decoded = _b64_decode(raw)
    except ValueError:
        raise ValueError("code is not a base64 token blob") from None

    data = _extract_fields(decoded)
    access = data.get("accessToken")
    if not access:
        raise ValueError("code payload missing accessToken")
    refresh = data.get("refreshToken")
    return {
        "access_token": access,
        "refresh_token": refresh,  # None when truncated (Google OAuth)
        "expires_at": data.get("expiresAt"),
        "email": data.get("email"),
    }


async def refresh_token(
    refresh_token_value: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """POST the refresh token to Cline's refresh endpoint.

    Returns ``{access_token, refresh_token, expires_in?, expires_at?}`` on
    success, ``{"error": "unrecoverable_refresh_error", "code": ...}`` when
    the refresh token is permanently dead (re-login required), or None on
    transient errors (retry later).

    When a client is provided it is used as-is (tests / caller-managed
    lifetimes); otherwise a short-lived client is created per call.
    """
    owned = client is None
    c = client or httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        resp = await c.post(
            f"{CLINE_API_BASE}/auth/refresh",
            json={
                "refreshToken": refresh_token_value,
                "grantType": "refresh_token",
                "clientType": "extension",
            },
        )
        if resp.status_code != 200:
            code = _extract_error_code(resp.text)
            if code in _UNRECOVERABLE_CODES:
                return {"error": "unrecoverable_refresh_error", "code": code}
            return None
        payload = resp.json()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        access = data.get("accessToken")
        if not access:
            return None
        result: dict[str, Any] = {
            "access_token": access,
            "refresh_token": data.get("refreshToken") or refresh_token_value,
        }
        expires_at = data.get("expiresAt")
        if expires_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(expires_at))
                result["expires_in"] = max(1, int(dt.timestamp() - time.time()))
                result["expires_at"] = expires_at
            except ValueError:
                pass
        return result
    except (httpx.HTTPError, orjson.JSONDecodeError, ValueError):
        return None
    finally:
        if owned:
            await c.aclose()


def _extract_error_code(body_text: str) -> str | None:
    try:
        data = orjson.loads(body_text)
    except (orjson.JSONDecodeError, ValueError):
        return None
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        err = err.get("code") or err.get("type")
    return str(err) if isinstance(err, str) else None


def expires_within_lead(expires_at_epoch: float, now: float | None = None) -> bool:
    """True when the access token is inside the refresh lead window."""
    current = time.time() if now is None else now
    return expires_at_epoch - current <= REFRESH_LEAD_S


def parse_expires_at(expires_at: str | None) -> float | None:
    """ISO-8601 Cline expiresAt -> epoch seconds; None when absent/unparseable."""
    if not expires_at:
        return None
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(expires_at))
        return dt.timestamp()
    except ValueError:
        return None
