"""Provider layer contracts: requests, errors, credential seam, adapter protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import orjson

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


def _extract_error_message(body_text: str) -> str:
    """Extract the most useful human-readable message from a provider error body.

    Many OpenAI-compatible providers (OpenRouter, Together, etc.) nest the
    real error text inside ``error.message`` or ``error.metadata.raw``.
    The raw body is often hundreds of bytes of JSON scaffolding with no
    clue about what actually failed, so we drill into known shapes.
    """
    try:
        data = orjson.loads(body_text)
    except (json.JSONDecodeError, ValueError):
        return body_text[:500]
    # OpenAI shape: {"error": {"message": "..."}}
    err = data.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg:
            # OpenRouter sometimes wraps a useless top-level message like
            # "Provider returned error" around a more specific metadata.raw.
            meta = err.get("metadata") or {}
            raw = meta.get("raw")
            if isinstance(raw, str) and raw and raw != msg:
                return f"{msg} ({meta.get('provider_name', 'upstream')}: {raw})"
            return msg
        # Some providers put the message at error level as a string
    elif isinstance(err, str) and err:
        return err
    # Anthropic shape: {"type": "error", "error": {"message": "..."}}
    if isinstance(data.get("type"), str) and data["type"] == "error":
        inner = data.get("error")
        if isinstance(inner, dict) and isinstance(inner.get("message"), str):
            return inner["message"]
    # Generic: fall back to the whole body if it's small enough
    return body_text[:500]


def error_from_provider_status(status: int, body_text: str, provider: str) -> WiwiError:
    msg = _extract_error_message(body_text) or f"{provider} returned HTTP {status}"
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
    if status == 400 and ("context" in msg.lower() or "maximum" in msg.lower()
                          or "too long" in msg.lower() or "tokens" in msg.lower()):
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
