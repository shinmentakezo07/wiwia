"""Provider layer contracts: requests, errors, credential seam, adapter protocol."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import orjson

from wiwi.ir.types import AssistantTurn
from wiwi.ir.types import Request as IRRequest
from wiwi.streaming.deltas import IRStreamDelta


class WiwiError(Exception):
    """Normalized gateway error; rendered per-surface by wire codecs."""

    def __init__(self, status: int, etype: str, message: str,
                 retryable: bool = False, retry_after: float | None = None,
                 upstream_type: str | None = None,
                 egress_scoped: bool = False):
        super().__init__(message)
        self.status = status
        self.etype = etype  # invalid_request_error | authentication_error | permission_error |
        # not_found_error | rate_limit_error | budget_exceeded | api_connection_error |
        # timeout | service_unavailable | context_window_exceeded | content_policy_violation | api_error
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after
        # The upstream's own error type, verbatim ("FreeTierError",
        # "CreditsError", "RegionError", ...), when the body named one.
        #
        # `etype` is wiwi's own classification and it is lossy in exactly the
        # place that hurts: an OpenCode Zen request-shape refusal arrives as
        # `permission_error` + 402 + "requires billing", which reads like an
        # account problem and sent one investigation chasing keys instead of the
        # request. Keeping the vendor's own label means the diagnostic starts
        # from what the vendor actually said. See docs/UPSTREAM_DIAGNOSIS.md.
        self.upstream_type = upstream_type
        # True when the refusal names the *egress address* rather than the
        # credential, the account or the request (AUDIT #557). The router
        # cannot act on it by itself — the same exit will answer the same way —
        # but an operator-supplied egress pool can: it is the only signal that
        # retiring this exit and drawing another is the right response.
        self.egress_scoped = egress_scoped


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

    The same holds for a ``429`` that names a quota marker
    (``FreeUsageLimitError`` and friends): ``error_from_provider_status``
    renders it as ``402 permission_error``, so it reports ``None`` here too and
    one exhausted free allowance cannot cool a credential that every sibling
    deployment shares. A plain 429 — a rate limit with no quota wording — still
    reports 429.

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


# Credential shapes that providers routinely echo back inside an error body.
# An upstream that rejects a key frequently quotes the offending value
# ("Invalid API key: sk-live-…"), and that text was being concatenated
# straight into the response the *caller* receives — handing one tenant's
# provider secret to another. Redacted at the source so every caller benefits.
_CREDENTIAL_PATTERNS: tuple[tuple[Any, str], ...] = (
    # OpenAI / Anthropic / most gateways: sk-…, sk-ant-…, rk-…
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    # Google: AIza…
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{8,}"), "AIza***"),
    # Google OAuth access tokens: ya29.…
    (re.compile(r"\bya29\.[A-Za-z0-9_\-]{8,}"), "ya29.***"),
    # GitHub / generic long opaque tokens.
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "gh*_***"),
    # Bearer/JWT-looking values in prose.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer ***"),
    # ``key=…`` / ``api_key=…`` / ``token=…`` query-param or JSON forms.
    # Bare ``key`` and ``token`` are included: an upstream error that quotes
    # ``"token": "…"`` is exactly as leaky as one quoting ``api_key``.
    (re.compile(r"(?i)\b((?:api[_-]?key|access[_-]?token|auth[_-]?token|secret"
                r"|password|key|token)\s*[=:]\s*)\"?([A-Za-z0-9_\-\.]{8,})\"?"),
     r"\1***"),
)


def _scrub_credentials(text: str) -> str:
    """Strip credential-shaped substrings from text bound for a client.

    Deliberately pattern-based rather than value-based: this layer has no way
    to know which secrets the caller's deployment holds, so it redacts things
    that *look* like credentials. Over-redacting an error message is a far
    better failure mode than leaking a key.
    """
    for pattern, repl in _CREDENTIAL_PATTERNS:
        text = pattern.sub(repl, text)
    return text


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
        return _scrub_credentials(body_text[:500])
    # Not every upstream returns a JSON *object*: `null`, bare strings,
    # numbers, booleans and arrays are all valid JSON and do occur in the
    # wild (proxies, health-check pages, some gateways).  Probing those for
    # an "error" key raised AttributeError and turned a clean provider 4xx
    # into an opaque gateway 500 with the real status lost.
    if not isinstance(data, dict):
        return _scrub_credentials(body_text[:500])
    # OpenAI shape: {"error": {"message": "..."}}
    err = data.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg:
            # OpenRouter sometimes wraps a useless top-level message like
            # "Provider returned error" around a more specific metadata.raw.
            meta = err.get("metadata")
            if not isinstance(meta, dict):
                return _scrub_credentials(msg)
            raw = meta.get("raw")
            if isinstance(raw, str) and raw and raw != msg:
                provider_name = (meta.get("provider_name")
                                 if isinstance(meta.get("provider_name"), str)
                                 else "upstream")
                return _scrub_credentials(f"{msg} ({provider_name}: {raw})")
            return _scrub_credentials(msg)
        # Some providers put the message at error level as a string
    elif isinstance(err, str) and err:
        return _scrub_credentials(err)
    # Anthropic shape: {"type": "error", "error": {"message": "..."}}
    if isinstance(data.get("type"), str) and data["type"] == "error":
        inner = data.get("error")
        if isinstance(inner, dict) and isinstance(inner.get("message"), str):
            return _scrub_credentials(inner["message"])
    # Generic: fall back to the whole body if it's small enough
    return _scrub_credentials(body_text[:500])


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

# The vendor refused the *network* the request left from, not the credential.
# OpenCode's free tier answers 403 with "free models aren't available from your
# current network because we've detected abusive traffic from this IP address —
# this isn't related to your account", and another gateway reports the variants
# "Forbidden, OpenCode Free Model Gateway Detect Abuse Traffic" and "Abuse of
# traffic" (opencode2api, internal/gateway/abuse.go). Left unclassified it took
# the credential branch: a 403 `authentication_error`, which retired a healthy
# key for 600 s and told the caller to re-authenticate a key that was never the
# problem. It is an account/exit-level refusal, so it belongs with the
# entitlement refusals — and it must never be answered by retrying the same
# egress, which is why the message names the network instead of the key.
_NETWORK_BLOCK_MARKERS = (
    "abusive traffic",
    "abuse traffic",
    "abuse of traffic",
    "from your current network",
)

# Phrase fallbacks for upstreams that refuse on the *shape* of the request
# rather than on the account — the caller is a client the vendor does not
# serve from that channel. Sent as a 400, so it never reaches the 401/403
# entitlement branch above, yet it behaves exactly like one: it is a property
# of this deployment, not of the key, and another deployment in the same model
# group can serve the very same request.
#
# WorkBuddy refuses Claude Code and Codex this way, naming the system prompt
# as the reason and telling the operator to "add another provider to this
# group" — advice wiwi could not act on, because a bare 400 classified as a
# non-retryable invalid_request_error is re-raised by the router *before* it
# drains its fallback queue. Keyed on the vendor's wording rather than on the
# provider name, so a second vendor refusing on channel is covered too
# (AUDIT #487).
_CHANNEL_REFUSAL_MARKERS = (
    "unapproved channel",
    "illegal api invocation",
)


def _entitlement_kind(body_text: str, msg: str) -> str | None:
    """Classify a 401/403 as an account-level refusal, else ``None``.

    Returns ``"billing"`` (payment/credit/quota), ``"policy"`` (region, data,
    model access) or ``"network"`` (the egress address was flagged) when the
    body names a condition that applies to the whole account/exit rather than
    to a bad credential; ``None`` means it is a genuine auth failure.
    """
    etype = _extract_error_type(body_text)
    if etype in _BILLING_ERROR_TYPES:
        return "billing"
    if etype in _POLICY_ERROR_TYPES:
        return "policy"
    low = msg.lower()
    if any(marker in low for marker in _NETWORK_BLOCK_MARKERS):
        return "network"
    if any(marker in low for marker in _BILLING_MESSAGE_MARKERS):
        return "billing"
    if any(marker in low for marker in _POLICY_MESSAGE_MARKERS):
        return "policy"
    return None


def is_quota_refusal(body_text: str = "", message: str = "") -> bool:
    """True when a refusal names an account/network quota condition.

    The same markers that turn a 401/403 into an entitlement refusal also
    arrive on ``429``: OpenCode's free tier answers ``429 FreeUsageLimitError``
    ("Free usage limit reached … subscribe to OpenCode Go") when the allowance
    for the account, workspace or network is spent, and its subscription tiers
    use ``GoUsageLimitError`` / ``BlackUsageLimitError``. That is a statement
    about an allowance, not about the credential's health, so the caller needs
    to charge it the way it charges an entitlement refusal.

    ``body_text`` is the raw error body; ``message`` is the human text when the
    caller already extracted it (and is what the marker fallback reads). Public
    because the OpenCode adapter makes the same call on a refusal that arrives
    *inside* a 200 event stream, where there is no HTTP status to classify
    (`opencode_adapter._stream_error`). One predicate keeps the pre-connect and
    mid-stream paths from drifting.

    Total: no body, no message, or no marker yields ``False``.
    """
    return _entitlement_kind(body_text, message or _extract_error_message(body_text)) == "billing"


def error_from_provider_status(status: int, body_text: str, provider: str) -> WiwiError:
    """Classify an upstream refusal, tagging it with the vendor's own label.

    ``WiwiError.upstream_type`` carries whatever the provider named in its body
    ("FreeTierError", "CreditsError", "RegionError", "ModelError", ...) so a log
    line says *why the vendor refused* rather than only how wiwi chose to render
    it. Set here, in one place, rather than at each ``return`` below: the
    classifier has eight exits and a forgotten one is a silent gap in the very
    field that exists to close gaps.
    """
    err = _classify_provider_status(status, body_text, provider)
    # `or None` so the contract is honestly ``str | None``: the extractor returns
    # "" for a body with no type (a proxy-level 403 page, say), and an empty
    # string in a log field reads as a type that was found but unnamed.
    err.upstream_type = _extract_error_type(body_text) or None
    return err


def _classify_provider_status(status: int, body_text: str, provider: str) -> WiwiError:
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
        if kind == "network":
            # The egress address was flagged, not the key: the body says so
            # ("this isn't related to your account"). Retrying the *same* exit
            # cannot succeed, but a fallback group on another provider can, so
            # it stays retryable; the message names the network so the operator
            # does not go looking for a credential or a request-shape bug.
            # `egress_scoped` marks it as the one class of refusal an egress
            # pool (when the operator configured one) can answer by retiring
            # the exit that carried the request.
            return WiwiError(403, "permission_error",
                             f"{provider} blocked this network ({status}): {msg}",
                             retryable=True, egress_scoped=True)
        # Preserve the auth-failure status so the router can invalidate the key
        # (ProviderKey.mark_invalid); retryable stays True so the request fails
        # over to the next key in the pool instead of hard-failing the client.
        return WiwiError(status, "authentication_error",
                         f"{provider} rejected credentials ({status}): {msg}",
                         retryable=True)
    if status == 429:
        # A 429 that names a quota/billing condition is a statement about an
        # allowance, not about this credential's health: OpenCode's free tier
        # answers ``429 FreeUsageLimitError`` once the free allowance for the
        # account/workspace/network is spent, and every free deployment there
        # shares one ``anonymous`` sentinel — charging that to key health
        # retired the whole tier for the cooldown (the sibling of AUDIT #469,
        # which only covered a 429 that states no window at all). Classified
        # as an entitlement refusal (402 ``permission_error``) so
        # ``status_for_key_pool`` leaves the key alone, the client is not told
        # to "retry in a second" for a daily allowance, and the request still
        # fails over because the error stays retryable.
        if is_quota_refusal(body_text):
            return WiwiError(402, "permission_error",
                             f"{provider} quota exhausted ({status}): {msg}",
                             retryable=True)
        return WiwiError(429, "rate_limit_error", f"{provider} rate limited: {msg}", retryable=True)
    if status == 504:
        return WiwiError(504, "timeout", f"{provider} timed out: {msg}", retryable=True)
    # 529 is Anthropic's own overload signal (several OpenAI-compatible vendors
    # reuse it), NOT a generic 5xx. It used to share an arm with 500/502/503
    # and came back as WiwiError(502, "api_connection_error"), which destroyed
    # BOTH the status and the vendor's own type — so an overloaded upstream
    # reached the client as an opaque api_error and Claude Code had nothing to
    # back off from. The vendor type did survive on ``upstream_type``, but
    # nothing on the error path reads it (only the log lines do).
    #
    # Stays retryable: an overload is a "fail over to the next key/deployment"
    # signal, exactly as it was when it arrived rewritten as a 502, and 529 is
    # already in RETRYABLE_STATUS so ``status_for_key_pool`` keeps reporting it
    # and deployment cooldowns keep firing (AUDIT #476).
    if status == 529:
        return WiwiError(529, "overloaded_error",
                         f"{provider} overloaded ({status}): {msg}", retryable=True)
    if status == 408 or status in (500, 502, 503):
        return WiwiError(502, "api_connection_error",
                         f"{provider} error {status}: {msg}", retryable=True)
    if status == 400 and ("context" in msg.lower() or "maximum" in msg.lower()
                          or "too long" in msg.lower()):
        return WiwiError(400, "context_window_exceeded", msg)
    if status == 400 and any(m in msg.lower() for m in _CHANNEL_REFUSAL_MARKERS):
        # A channel refusal is not a malformed request: the same body sails
        # through on a different deployment. Marked permission_error (not
        # authentication_error) so status_for_key_pool leaves the key alone —
        # nothing is wrong with the credential — and retryable so the router
        # drains its fallback queue instead of raising at the client. The
        # vendor's own wording is preserved verbatim because it is the only
        # evidence of what was actually objected to (AUDIT #487).
        return WiwiError(400, "permission_error",
                         f"{provider} refused this client/channel: {msg}",
                         retryable=True)
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

    def realtime_url(self, base_url: str) -> str | None:
        """WebSocket endpoint for a Realtime-style session, or None.

        A method, not an attribute: the answer depends on the deployment's
        configured ``base_url``, which is a call-time argument everywhere else
        in the adapter protocol too. ``None`` means the provider has no such
        surface, and the /v1/realtime route refuses it with a 501 rather than
        dialling a URL that does not exist.
        """

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


class WarningMixin:
    """Mixin giving an adapter the caller-visible translation-warning channel.

    The mechanism itself (``translation_warnings`` → ``take_adapter_warnings``
    → ``ctx.metadata`` → the spend log) is described in
    :func:`take_adapter_warnings`. It used to be used by exactly ONE adapter
    out of ten (Gemini, for ``strict`` and ``disable_parallel_tool_use``),
    while every other drop — the OpenAI adapter's builtin-tool drop, its
    ``top_k`` drop, the Anthropic and Gemini audio drops — was visible only
    to the *operator* in the log. The caller, who set the constraint and
    pays for the request, learned nothing (AUDIT #498).

    The mixin provides the attribute, its reset, and one ``warn`` method so
    every adapter phrases its warnings the same way and the channel is reset
    with the rest of the per-request state. Adapters that do not inherit it
    are unaffected: ``take_adapter_warnings`` is total, and the attribute
    lookup is a ``getattr`` with a type check.

    Inheritance note: this is a *generic* contract with no provider-specific
    logic, so it lives in ``providers/base.py`` alongside the other generic
    contracts rather than in a provider module — the layering rule lets
    ``core/`` import ``providers.base`` but no concrete adapter.
    """

    translation_warnings: list[str]

    def _init_warnings(self) -> None:
        """Start the warning list. Call from the adapter's ``__init__``."""
        self.translation_warnings = []

    def _reset_warnings(self) -> None:
        """Empty the warning list. Call from the adapter's ``reset``.

        A previous request's warning must never ride along on the next one:
        the shared singleton is handed out reset, and a stale warning would
        be attributed to a request that never hit the condition.
        """
        self.translation_warnings = []

    def warn(self, message: str) -> None:
        """Record one caller-visible translation warning.

        Appends to the list the gateway drains into
        ``ctx.metadata["translation_warnings"]``. The adapter's own
        ``log.warning`` remains the operator's view; this is the CALLER's.
        """
        if not isinstance(message, str) or not message:
            return
        self.translation_warnings.append(message)


# The OpenAI-2026 platform params that ride ``Request.extras`` (the IR does
# not model them) and that an OpenAI-shaped upstream honours and bills for.
#
# The gap they closed (AUDIT #501): a caller that set ``logit_bias`` against
# an Anthropic or Gemini deployment got a request the backend silently
# ignored, with nothing anywhere saying so. These are *request-side* knobs —
# unlike ``logprobs`` (#326) they have no response-side value, so there is
# nothing to carry back; the only honest signal is to say the parameter did
# not apply.
#
# Kept here rather than in one adapter because BOTH layers need it: the
# OpenAI-shaped adapters forward them (they CAN honour them), and the
# Anthropic/Gemini adapters must report that they cannot. A single table
# keeps the two sides from drifting — a param added to the forward list
# without a row here would be silently dropped on every non-OpenAI backend,
# which is the exact defect this table exists to prevent.
OPENAI_PLATFORM_PARAMS: frozenset[str] = frozenset({
    "user", "frequency_penalty", "presence_penalty", "logit_bias",
    "service_tier", "store", "prediction", "modalities", "verbosity",
    "safety_identifier", "prompt_cache_key", "web_search_options",
    "logprobs", "top_logprobs",
})


def unhonoured_platform_params(extras: Any) -> list[str]:
    """The OpenAI-2026 platform params in *extras* that a non-OpenAI backend drops.

    *extras* is the IR's ``Request.extras`` — the request-body keys the
    inbound codec did not model. A param in this set is one an
    OpenAI-shaped upstream honours and bills for; on a provider with no
    such field the value is simply lost.

    Returns the sorted names, so a caller-facing message is deterministic.
    Total: a non-mapping *extras* yields ``[]``.
    """
    if not isinstance(extras, dict):
        return []
    return sorted(k for k in extras if k in OPENAI_PLATFORM_PARAMS)
