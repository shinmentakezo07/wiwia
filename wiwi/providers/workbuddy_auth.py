"""WorkBuddy auth: credential parsing, region routing, and token refresh.

Ported from workbuddy2api's ``internal/auth`` + the refresh path of
``internal/upstream``. A WorkBuddy credential is an OAuth access/refresh
token pair tied to a Tencent CodeBuddy/WorkBuddy account. wiwi stores the
serialized auth JSON as the provider key's *secret* (same seam Cline uses
for its OAuth tokens), so a refresh rotates the secret in place.

Two on-disk shapes are accepted (mirroring the Go ``Parse``):

- nested   ``{"auth": {...}, "account": {...}}``  (plugin OAuth output)
- flat     ``{"accessToken": ..., "uid": ...}``   (hand-built panel entry)

Region routing: a domain of ``workbuddy.ai`` (or any ``*.workbuddy.ai``
subdomain) selects the global upstream; anything else — including an empty
domain — is CN. Refresh tokens rotate: every refresh consumes the old
refresh token and returns a new one, so callers must persist the whole
record atomically after a successful refresh.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import orjson
import structlog

log = structlog.get_logger("wiwi.workbuddy_auth")

CHAT_BASE_CN = "https://copilot.tencent.com"
CHAT_BASE_GLOBAL = "https://www.workbuddy.ai"
ORIGIN_CN = "https://www.codebuddy.cn"
ORIGIN_GLOBAL = "https://www.workbuddy.ai"
CLIENT_UA = "CLI/2.63.2 CodeBuddy/2.63.2"

# Refresh lead window (seconds): rotating refresh tokens make every refresh a
# consumption event — only refresh inside this window before expiry.
REFRESH_LEAD_S = 5 * 60

_GLOBAL_SUFFIX = ".workbuddy.ai"
_TIMEOUT_S = 30.0


class WorkBuddyAuthError(Exception):
    """Credential is unusable (unparseable, missing access token, or the
    refresh flow failed unrecoverably and the user must re-login)."""


@dataclass
class WorkBuddyAuth:
    """Normalized account credential (nested or flat shape, post-parse)."""

    access_token: str = ""
    refresh_token: str = ""
    expires_at: int = 0  # Unix seconds; 0 = unknown
    domain: str = ""
    uid: str = ""
    enterprise_id: str = ""
    nickname: str = ""

    def region(self) -> str:
        """'global' for workbuddy.ai domains, else 'cn' (empty domain = cn).

        The stored domain may be a bare host (``workbuddy.ai``) or a full
        URL (``https://www.workbuddy.ai`` — the shape the upstream plugin
        writes), so accept both.
        """
        d = self.domain.strip().lower()
        if "://" in d:
            d = d.split("://", 1)[1]
        d = d.split("/", 1)[0].split(":", 1)[0]
        if d == _GLOBAL_SUFFIX.lstrip(".") or d.endswith(_GLOBAL_SUFFIX):
            return "global"
        return "cn"

    def chat_base(self) -> str:
        return CHAT_BASE_GLOBAL if self.region() == "global" else CHAT_BASE_CN

    def origin(self) -> str:
        return ORIGIN_GLOBAL if self.region() == "global" else ORIGIN_CN

    def needs_refresh(self, within_s: float = 0.0) -> bool:
        """True when the token expires within ``within_s`` seconds (or has no
        known expiry — treat unknown as due, like the Go NeedsRefresh)."""
        if self.expires_at <= 0:
            return True
        return time.time() + within_s >= self.expires_at

    def to_secret(self) -> str:
        """Serialize back to the nested shape the upstream plugin reads, so a
        rotated token can be written back into the provider key's secret."""
        return orjson.dumps({
            "auth": {
                "accessToken": self.access_token,
                "refreshToken": self.refresh_token,
                "expiresAt": self.expires_at,
                "domain": self.domain,
            },
            "account": {
                "uid": self.uid,
                "enterpriseId": self.enterprise_id,
                "nickname": self.nickname,
            },
        }, option=orjson.OPT_INDENT_2).decode()


def parse_auth(raw: str | bytes) -> WorkBuddyAuth:
    """Parse a key secret (nested or flat JSON) into a WorkBuddyAuth.

    Raises WorkBuddyAuthError on empty/unparseable input or a missing
    accessToken — same refusals as the Go Parse.
    """
    if isinstance(raw, str):
        raw = raw.encode()
    if not raw or not raw.strip():
        raise WorkBuddyAuthError("empty auth storage")
    try:
        probe = orjson.loads(raw)
    except (orjson.JSONDecodeError, ValueError) as e:
        raise WorkBuddyAuthError(f"storage_parse_error: {e}") from e
    if not isinstance(probe, dict):
        raise WorkBuddyAuthError("storage_parse_error: not a JSON object")
    nested = probe.get("auth")
    if isinstance(nested, dict):
        account = probe.get("account")
        acct_blk: dict[str, Any] = account if isinstance(account, dict) else {}
        auth_blk: dict[str, Any] = nested
    else:
        auth_blk = acct_blk = probe

    def _s(d: dict[str, Any], k: str) -> str:
        v = d.get(k)
        return v if isinstance(v, str) else ""

    def _i(d: dict[str, Any], k: str) -> int:
        v = d.get(k)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    a = WorkBuddyAuth(
        access_token=_s(auth_blk, "accessToken"),
        refresh_token=_s(auth_blk, "refreshToken"),
        expires_at=_i(auth_blk, "expiresAt"),
        domain=_s(auth_blk, "domain"),
        uid=_s(acct_blk, "uid"),
        enterprise_id=_s(acct_blk, "enterpriseId"),
        nickname=_s(acct_blk, "nickname"),
    )
    if not a.access_token.strip():
        raise WorkBuddyAuthError("parse_error: missing accessToken")
    return a


def parse_expires_at(value: Any) -> int | None:
    """Coerce a stored expires_at (epoch int or ISO-8601 str) to epoch seconds."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        from datetime import datetime

        try:
            return int(datetime.fromisoformat(value).timestamp())
        except ValueError:
            return None
    return None


def expires_within_lead(expires_epoch: int, lead_s: float = REFRESH_LEAD_S) -> bool:
    return expires_epoch <= 0 or time.time() + lead_s >= expires_epoch


def _common_headers(origin: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin + "/",
        "User-Agent": CLIENT_UA,
    }


def chat_headers(auth: WorkBuddyAuth) -> dict[str, str]:
    """Headers for /v2/chat/completions.

    Mirrors the Go ChatHeaders: common headers + account identity headers.
    Empty fields use the CLI's X-No-* placeholder convention. Security red
    line: X-Refresh-Token must NEVER appear on a chat request.
    """
    h = _common_headers(auth.origin())
    if auth.access_token:
        h["Authorization"] = f"Bearer {auth.access_token}"
    else:
        h["X-No-Authorization"] = "1"
    if auth.uid:
        h["X-User-Id"] = auth.uid
    else:
        h["X-No-User-Id"] = "1"
    if auth.enterprise_id:
        h["X-Enterprise-Id"] = auth.enterprise_id
    else:
        h["X-No-Enterprise-Id"] = "1"
    if auth.domain:
        h["X-Domain"] = auth.domain
    else:
        h["X-No-Department-Info"] = "1"
    h["X-Product"] = "SaaS"
    return h


def refresh_headers(auth: WorkBuddyAuth) -> dict[str, str]:
    """Headers for the token-refresh endpoint (X-Refresh-Token lives only here)."""
    h = _common_headers(auth.origin())
    h["X-Refresh-Token"] = auth.refresh_token
    h["X-Auth-Refresh-Source"] = "workbuddy"
    if auth.enterprise_id:
        h["X-Enterprise-Id"] = auth.enterprise_id
    return h


@dataclass
class RefreshOutcome:
    """Result of a refresh attempt. ``auth`` is the updated credential when
    ``ok``; ``unrecoverable`` means re-login is required (stop retrying)."""

    ok: bool
    auth: WorkBuddyAuth | None = None
    unrecoverable: bool = False
    error: str = ""


def _is_session_dead(text: str) -> bool:
    lower = text.lower()
    return "12153" in lower or "offline user session" in lower


async def refresh_token(auth: WorkBuddyAuth,
                        client: httpx.AsyncClient | None = None) -> RefreshOutcome:
    """Rotate the access token via POST {chatBase}/v2/plugin/auth/token/refresh.

    On success returns a *new* WorkBuddyAuth with rotated tokens (missing
    response fields keep their old values — the Go preserveExpiry rule that
    prevents refresh storms when expiresIn is absent). Never mutates the
    caller's object.
    """
    if not auth.refresh_token.strip():
        return RefreshOutcome(ok=False, unrecoverable=True, error="no refreshToken")
    url = auth.chat_base() + "/v2/plugin/auth/token/refresh"
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        resp = await client.post(url, headers=refresh_headers(auth))
    except httpx.HTTPError as e:
        return RefreshOutcome(ok=False, error=f"refresh transport error: {type(e).__name__}")
    finally:
        if owns_client:
            await client.aclose()
    body = resp.text
    if resp.status_code >= 400:
        return RefreshOutcome(ok=False,
                              unrecoverable=resp.status_code in (401, 403)
                              or _is_session_dead(body),
                              error=f"refresh http {resp.status_code}: {body[:200]}")
    try:
        env = orjson.loads(body)
    except (orjson.JSONDecodeError, ValueError):
        return RefreshOutcome(ok=False, error=f"refresh parse failed: {body[:120]}")
    if not isinstance(env, dict):
        return RefreshOutcome(ok=False, error=f"refresh unexpected shape: {body[:120]}")
    code = env.get("code")
    data = env.get("data")
    if code != 0 or not isinstance(data, dict):
        msg = str(env.get("msg", ""))
        return RefreshOutcome(ok=False, unrecoverable=_is_session_dead(msg),
                              error=f"refresh code={code} msg={msg[:160]}")
    new_access = data.get("accessToken")
    if not isinstance(new_access, str) or not new_access:
        return RefreshOutcome(ok=False, unrecoverable=True,
                              error="refresh_failed: no accessToken in response "
                                    "— re-login required")
    rotated = WorkBuddyAuth(
        access_token=new_access,
        refresh_token=data.get("refreshToken") or auth.refresh_token,
        expires_at=auth.expires_at,
        domain=data.get("domain") or auth.domain,
        uid=auth.uid,
        enterprise_id=auth.enterprise_id,
        nickname=auth.nickname,
    )
    expires_in = data.get("expiresIn")
    if (isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool)
            and expires_in > 0):
        rotated.expires_at = int(time.time() + expires_in)
    return RefreshOutcome(ok=True, auth=rotated)
