"""Provider layer contracts: requests, errors, credential seam, adapter protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
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


def status_for_key_pool(e: WiwiError) -> int | None:
    """HTTP status a WiwiError should report to the key pool, if any.

    Returns the status that key-pool accounting cares about (429, 401/403
    *credential* failures, and other retryable upstream failures), or ``None``
    for statuses that say nothing about the key's health.

    A 401/403 is only a key-health signal when it is a credential rejection.
    Some upstreams use 401/403 for an entitlement/policy decision that applies
    equally to every key on the account — e.g. OpenCode Zen's ``FreeTierError``
    ("free tier can only be used from within OpenCode"), which arrives as 403
    even with a valid key. ``error_from_provider_status`` classifies those as
    ``permission_error``; reporting them to the pool retired healthy keys two
    at a time (err_count += 2) until the whole account was cooled off, turning
    one policy rejection into a self-inflicted outage. They return ``None`` so
    the key is left alone; the request still fails over to another
    deployment/provider because the error stays retryable.

    Shared by the router's retry loop and the gateway's stream pump/resume
    path.
    """
    if e.status == 429:
        return 429
    if e.status in (401, 403):
        return None if e.etype == "permission_error" else e.status
    if e.status in RETRYABLE_STATUS:
        return e.status
    return None


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
    # Not every upstream returns a JSON *object*: `null`, bare strings,
    # numbers, booleans and arrays are all valid JSON and do occur in the
    # wild (proxies, health-check pages, some gateways).  Probing those for
    # an "error" key raised AttributeError and turned a clean provider 4xx
    # into an opaque gateway 500 with the real status lost.
    if not isinstance(data, dict):
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


def _extract_error_type(body_text: str) -> str:
    """Extract an upstream error's machine ``type``/``code`` marker.

    Zen/Anthropic use ``{"type":"error","error":{"type":"FreeTierError"}}``;
    OpenAI-style bodies put it at ``error.type`` or ``error.code``. Returns
    ``""`` when the body is not an object or carries no marker.
    """
    try:
        data = orjson.loads(body_text)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        for key in ("type", "code"):
            val = err.get(key)
            if isinstance(val, str) and val:
                return val
    for key in ("type", "code"):
        val = data.get(key)
        if isinstance(val, str) and val and val != "error":
            return val
    return ""


# Account/entitlement error markers: the upstream accepted the credential but
# refused the *request* on a policy or billing ground. These apply to every
# key on the account, so they must never be charged to key health. Billing
# refusals are reported as 402 so a client does not render them as "re-enter
# your API key" (Cline, for one, classes every 401/403 as an auth error);
# policy refusals stay 403 but keep the non-auth ``permission_error`` etype.
_BILLING_ERROR_TYPES = frozenset({
    "FreeTierError",        # OpenCode Zen: "free tier can only be used from within OpenCode"
    "CreditsError",         # no payment method / insufficient balance
    "MonthlyLimitError",    # workspace monthly spend cap
    "UserLimitError",       # member monthly spend cap
    "FreeUsageLimitError",
    "GoUsageLimitError",
    "BlackUsageLimitError",
})

_POLICY_ERROR_TYPES = frozenset({
    "RegionError",          # model not available in the caller's country
    "DataPolicyError",      # model requires explicit training opt-in
    "ModelError",           # model unsupported / disabled for this workspace
})

# Phrase fallbacks for upstreams that send a refusal without a type marker
# (the deployed Console variant emits these in the human message).
_BILLING_MESSAGE_MARKERS = (
    "free tier can only be used",
    "no payment method",
    "insufficient balance",
    "spending limit",
    "usage limit reached",
    "subscription quota exceeded",
)

_POLICY_MESSAGE_MARKERS = (
    "not available in your country",
    "requires explicit opt in",
    "model is disabled",
    "model is not supported",
)


def _entitlement_kind(body_text: str, msg: str) -> str | None:
    """Classify a 401/403 as an account-level refusal, else ``None``.

    Returns ``"billing"`` (payment/credit/quota) or ``"policy"`` (region,
    data, model access) when the body names an account-wide condition rather
    than a bad credential; ``None`` means it is a genuine auth failure.
    """
    etype = _extract_error_type(body_text)
    if etype in _BILLING_ERROR_TYPES:
        return "billing"
    if etype in _POLICY_ERROR_TYPES:
        return "policy"
    low = msg.lower()
    if any(marker in low for marker in _BILLING_MESSAGE_MARKERS):
        return "billing"
    if any(marker in low for marker in _POLICY_MESSAGE_MARKERS):
        return "policy"
    return None


def error_from_provider_status(status: int, body_text: str, provider: str) -> WiwiError:
    msg = _extract_error_message(body_text) or f"{provider} returned HTTP {status}"
    if status == 401 or status == 403:
        # Distinguish a rejected credential from an account-level refusal.
        # Both arrive as 401/403, but only the former says anything about the
        # key: a ``FreeTierError``/``CreditsError`` applies to the whole
        # account, so classifying it as ``authentication_error`` retired every
        # healthy key in the pool (err_count += 2 each) and turned one policy
        # rejection into an outage. Entitlement refusals become
        # ``permission_error`` (so ``status_for_key_pool`` leaves the key
        # alone) and billing ones carry 402 so clients do not tell the user to
        # re-enter a working key; the request still fails over because the
        # error stays retryable.
        kind = _entitlement_kind(body_text, msg)
        if kind == "billing":
            return WiwiError(402, "permission_error",
                             f"{provider} requires billing ({status}): {msg}",
                             retryable=True)
        if kind == "policy":
            return WiwiError(403, "permission_error",
                             f"{provider} denied access ({status}): {msg}",
                             retryable=True)
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
                          or "too long" in msg.lower()):
        return WiwiError(400, "context_window_exceeded", msg)
    if status == 400:
        return WiwiError(400, "invalid_request_error", f"{provider}: {msg}")
    return WiwiError(502, "api_error", f"{provider} error {status}: {msg}", retryable=status >= 500)


@dataclass
class ProviderKeyRef:
    label: str
    secret: str


def coerce_args_fragment(value: Any) -> str:
    """Normalize a provider's tool-args fragment to the ``str`` the contract requires.

    ``ToolCallArgsDelta.args_fragment`` is typed ``str`` and client encoders
    serialize it straight into a JSON frame, so a non-string fragment (a bool
    or number from a sloppy gateway) crashed the stream *after* the client had
    already received a 200 (AUDIT #124). Args-as-object gateways send a real
    dict, which is re-serialized; every other non-string shape is unparseable
    and becomes "".
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value)
    return ""


def as_dict(value: Any) -> dict:
    """Return *value* when it is a dict, else ``{}``.

    The sync decoders used ``data.get("usage") or {}`` / ``choice.get("message", {})``,
    which default only a *missing* key: an explicit JSON ``null`` (or a string,
    list or number) passes straight through and the next ``.get`` raises
    ``AttributeError``. ``_decode_response_guarded`` turns that into a
    retryable 502, so the router charges the failure to the key and deployment
    health — a self-inflicted cooldown from a frame carrying no semantics
    (AUDIT #247). Use this at every nested read on the sync path.
    """
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list:
    """Return *value* when it is a list, else ``[]`` (AUDIT #247/#224)."""
    return value if isinstance(value, list) else []

def as_str(value: Any, default: str = "") -> str:
    """Return *value* when it is a ``str``, else *default*.

    The mirror of :func:`as_dict`/:func:`as_list` for name/id fields:
    ``.get(k, "")`` defaults only a *missing* key, so an explicit JSON ``null``
    reached the IR as ``None`` and the client received ``"name": null`` — or,
    where the value is coerced to ``str``, the literal ``"None"`` (AUDIT #232).
    Use at every name/id read on the decode path.
    """
    return value if isinstance(value, str) else default



class ProviderAdapter(Protocol):
    provider_type: str

    def headers(self, key: ProviderKeyRef) -> dict[str, str]: ...
    def build_url(self, base_url: str, model_id: str, stream: bool) -> str: ...
    def encode_request(self, req: IRRequest, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]: ...
    def decode_response(self, status: int, body: bytes) -> AssistantTurn: ...
    def decode_stream_event(self, event: str, data: str) -> list[IRStreamDelta]: ...


def take_adapter_warnings(adapter: Any) -> list[str]:
    """Drain an adapter's advisory translation warnings, if it keeps any.

    Some IR constructs have no representation on a given provider (Gemini has
    no ``disable_parallel_tool_use`` knob; several providers drop ``strict``).
    The adapter logs those, which is right for operators but invisible to the
    CALLER who set the constraint — an agent that serializes tool calls for
    correctness (file edits, shell state) would never learn it is getting
    concurrency it forbade.

    Adapters that want to surface such warnings set ``self.translation_warnings``
    to a list during ``encode_request``; the gateway drains it into
    ``ctx.metadata["translation_warnings"]`` via this helper, which is total —
    an adapter without the attribute, or with a non-list, yields ``[]``.
    """
    warnings = getattr(adapter, "translation_warnings", None)
    if not isinstance(warnings, list):
        return []
    drained = [w for w in warnings if isinstance(w, str)]
    warnings.clear()
    return drained
