"""Provider layer contracts: requests, errors, credential seam, adapter protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from wiwi.ir.types import AssistantTurn
from wiwi.ir.types import Request as IRRequest
from wiwi.streaming.deltas import IRStreamDelta


class WiwiError(Exception):
    """Normalized gateway error; rendered per-surface by wire codecs."""

    def __init__(self, status: int, etype: str, message: str,
                 retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.etype = etype  # invalid_request_error | authentication_error | permission_error |
        # not_found_error | rate_limit_error | budget_exceeded | api_connection_error |
        # timeout | service_unavailable | context_window_exceeded | content_policy_violation | api_error
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}


def error_from_provider_status(status: int, body_text: str, provider: str) -> WiwiError:
    msg = body_text[:500] or f"{provider} returned HTTP {status}"
    if status == 401 or status == 403:
        # Preserve the auth-failure status so the router can invalidate the key
        # (ProviderKey.mark_invalid); retryable stays True so the request fails
        # over to the next key in the pool instead of hard-failing the client.
        return WiwiError(status, "authentication_error",
                         f"{provider} rejected credentials ({status}): {msg}",
                         retryable=True)
    if status == 429:
        return WiwiError(429, "rate_limit_error", f"{provider} rate limited: {msg}", retryable=True)
    if status == 504:
        return WiwiError(504, "timeout", f"{provider} timed out: {msg}", retryable=True)
    if status == 408 or status in (500, 502, 503, 529):
        return WiwiError(502, "api_connection_error",
                         f"{provider} error {status}: {msg}", retryable=True)
    if status == 400 and ("context" in body_text.lower() or "maximum" in body_text.lower()
                          or "too long" in body_text.lower() or "tokens" in body_text.lower()):
        return WiwiError(400, "context_window_exceeded", msg)
    if status == 400:
        return WiwiError(400, "invalid_request_error", f"{provider}: {msg}")
    return WiwiError(502, "api_error", f"{provider} error {status}: {msg}", retryable=status >= 500)


@dataclass
class ProviderKeyRef:
    label: str
    secret: str


@dataclass
class ProviderRequest:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    json_body: dict[str, Any] | None = None


class CredentialProvider(Protocol):
    """G5 seam: static keys now; Entra-ID/OAuth/SigV4 signers later."""

    def headers(self, key: ProviderKeyRef) -> dict[str, str]: ...


class ProviderAdapter(Protocol):
    provider_type: str

    def headers(self, key: ProviderKeyRef) -> dict[str, str]: ...
    def build_url(self, base_url: str, model_id: str, stream: bool, kind: str) -> str: ...
    def encode_request(self, req: IRRequest, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]: ...
    def decode_response(self, status: int, body: bytes) -> AssistantTurn: ...
    def decode_stream_event(self, event: str, data: str) -> list[IRStreamDelta]: ...
