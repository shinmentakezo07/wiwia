"""WorkBuddy / CodeBuddy (Tencent) provider adapter.

Upstream is an OpenAI Chat Completions-compatible API with four quirks,
ported from workbuddy2api's ``internal/upstream``:

1. **Auth**: the key secret is a WorkBuddy auth JSON (nested or flat — see
   :mod:`wiwi.providers.workbuddy_auth`). Requests carry the access token as
   ``Authorization: Bearer`` plus account identity headers (``X-User-Id``,
   ``X-Enterprise-Id``, ``X-Domain``, ``X-Product: SaaS``) and the CLI's
   ``X-No-*`` placeholders for empty fields. A secret that is not valid auth
   JSON is treated as a bare access token (paste-a-token UX).
2. **Streaming-only**: ``/v2/chat/completions`` only implements SSE (a
   non-stream request 400s with code 11101). ``stream`` is forced True and
   ``stream_options`` never forwarded; the gateway reassembles deltas for
   non-streaming callers via ``force_stream``.
3. **tool_choice is a string upstream**: object forms 400 with code 11101.
   auto/required collapse to their type string, a named choice collapses to
   the bare function name, and "none" additionally drops ``tools``/
   ``functions`` entirely.
4. **{code,msg,data} envelope**: business errors may ride HTTP 200 as
   ``{"code": N, "msg": "..."}`` chunks — mid-stream error chunks surface as
   StreamError so the gateway can fail the stream.

Requests are additionally sanitized against the upstream content-moderation
fingerprint blocklist (ported from sanitize.go): known template sentences
triggering verbatim-match moderation get a one-word rewrite, and
``x-anthropic-billing-header`` / ``cc_*`` key-value segments are stripped.
"""

from __future__ import annotations

import json
import re
from typing import Any

import orjson
import structlog

from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef, WiwiError
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.workbuddy_auth import CHAT_BASE_CN, chat_headers, parse_auth
from wiwi.streaming import deltas as dl

log = structlog.get_logger("wiwi.workbuddy_adapter")

# -- outbound body sanitization (port of sanitize.go) ------------------------

_SANITIZE_FEATURES = (
    "x-anthropic-billing-header",  # header key-name segment
    "cc_entrypoint=",               # trailing bare kv (prefix match)
    "You are Claude Code",          # identity sentence
    "Main branch (",                # injected-instruction sentence
)
_HDR_RE = re.compile(r"(?i)x-anthropic-billing-header:[^;\n]*;?\s*")
_KV_RE = re.compile(r"(?i)\bcc_[a-z0-9_]+=[^;\n]*;?\s*")
_REWRITES = (
    ("You are Claude Code, Anthropic's official CLI for Claude.",
     "You are Claude Code, Anthropic's official CLI tool for Claude."),
    ("Main branch (you will usually use this for PRs)",
     "Default branch (you will usually use this for PRs)"),
)


def _has_fingerprint(text: str) -> bool:
    for f in _SANITIZE_FEATURES:
        if f in text:
            return True
    return bool(_HDR_RE.search(text))


def _sanitize_text(text: str) -> str:
    """Strip moderation fingerprints; no-op (zero work) when none present."""
    if not _has_fingerprint(text):
        return text
    for old, new in _REWRITES:
        text = text.replace(old, new)
    text = _HDR_RE.sub("", text)
    if "cc_" in text:
        prev = ""
        while prev != text:  # scrub trailing bare kvs (cc_version=…; cc_x=…;)
            prev = text
            text = _KV_RE.sub("", text)
    return text.strip()


def _sanitize_content(value: Any) -> Any:
    """Sanitize a message content field (string or multimodal parts array)."""
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        for part in value:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part["text"] = _sanitize_text(part["text"])
    return value


def _sanitize_messages(messages: list[Any]) -> None:
    for msg in messages:
        if isinstance(msg, dict) and "content" in msg:
            msg["content"] = _sanitize_content(msg["content"])


# -- tool_choice normalization (port of normalizeToolChoice) -----------------

def _normalize_tool_choice(body: dict[str, Any]) -> None:
    """Rewrite OpenAI-shaped tool_choice into the upstream's string form.

    Mutates *body*. "none" additionally suppresses tools/functions entirely
    (the upstream rejects tool_choice=none alongside a tools array).
    """
    if "tool_choice" not in body:
        return
    tc = body["tool_choice"]

    def suppress() -> None:
        body.pop("tools", None)
        body.pop("functions", None)

    if isinstance(tc, str):
        if tc.strip().lower() == "none":
            body.pop("tool_choice")
            suppress()
        return
    if isinstance(tc, dict):
        typ = str(tc.get("type") or "").strip().lower()
        if typ == "none":
            body.pop("tool_choice")
            suppress()
        elif typ in ("auto", "required"):
            body["tool_choice"] = typ
        elif typ == "function":
            fn = tc.get("function")
            name = ""
            if isinstance(fn, dict):
                name = str(fn.get("name") or "")
            if not name.strip():
                name = str(tc.get("name") or "")
            name = name.strip()
            body["tool_choice"] = name if name else "auto"
        else:
            body.pop("tool_choice")
        return
    # non-scalar / unknown shape: the upstream would 400 on it
    body.pop("tool_choice")


class WorkBuddyAdapter(OpenAIAdapter):
    """OpenAI Chat wire format + WorkBuddy auth/streaming/tool_choice quirks."""

    provider_type = "workbuddy"
    force_stream = True  # upstream has no non-streaming mode (code 11101)

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        try:
            auth = parse_auth(key.secret)
        except Exception:  # noqa: BLE001 — bare-token fallback, never crash a request
            log.debug("workbuddy_secret_not_auth_json", label=key.label)
            auth = None
        if auth is None:
            # Paste-a-token UX: treat the secret as a raw access token.
            return {
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.codebuddy.cn",
                "Referer": "https://www.codebuddy.cn/",
                "Authorization": f"Bearer {key.secret.strip()}",
                "X-Product": "SaaS",
            }
        return chat_headers(auth)

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        base = (base_url or CHAT_BASE_CN).rstrip("/")
        return f"{base}/v2/chat/completions"

    def build_url_for_key(self, base_url: str, model_id: str, stream: bool,
                          key: ProviderKeyRef) -> str:
        """Per-credential URL: a WorkBuddy account's domain routes CN vs global.

        The account's own auth domain is the source of truth (mirrors the Go
        client's ``chatBase(auth)``): global tokens 401 on the CN host and
        vice versa, so a mixed-region pool under one provider must hit the
        right host per request. The provider's configured base_url is used
        only for secrets that are not auth JSON (bare tokens).
        """
        try:
            auth = parse_auth(key.secret)
        except Exception:  # noqa: BLE001 — unparseable secret falls back to config
            return self.build_url(base_url, model_id, stream)
        return f"{auth.chat_base()}/v2/chat/completions"

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        params = dict(deployment_params)
        # Strict compatible-gateway path: history reasoning_content stripped.
        params["provider_type"] = "openai-compatible"
        body = super().encode_request(req, model_id, params)
        # Streaming-only upstream: force SSE, never send stream_options.
        body["stream"] = True
        body.pop("stream_options", None)
        _normalize_tool_choice(body)
        if isinstance(body.get("messages"), list):
            _sanitize_messages(body["messages"])
        return body

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        # force_stream=True means the gateway reassembles via the SSE pump and
        # never calls this; kept for direct callers (tests, future non-stream
        # upstream support) with envelope unwrapping.
        try:
            data = orjson.loads(body)
        except (json.JSONDecodeError, ValueError):
            data = None
        if (isinstance(data, dict) and "choices" not in data
                and isinstance(data.get("code"), int) and data["code"] != 0):
            raise _envelope_error(data)
        if isinstance(data, dict) and isinstance(data.get("data"), dict) \
                and "choices" in data["data"]:
            body = orjson.dumps(data["data"])
        return super().decode_response(status, body)

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        # Mid-stream envelope errors: upstream surfaces {"code": N, "msg": …}
        # (e.g. 11101 non-stream rejected, 11102 service info not found,
        # 12153 session dead) as a data payload with no choices.
        if data != "[DONE]":
            try:
                chunk = orjson.loads(data)
            except (json.JSONDecodeError, ValueError):
                chunk = None
            if (isinstance(chunk, dict) and not chunk.get("choices")
                    and isinstance(chunk.get("code"), int) and chunk["code"] != 0):
                err = _envelope_error(chunk)
                return [dl.StreamError(message=err.message, kind="status")]
        return super().decode_stream_event(event, data)


def _envelope_error(env: dict[str, Any]) -> WiwiError:
    """Map a {code,msg} business envelope to a normalized WiwiError.

    12153 / "Offline user session" → authentication_error retryable (the
    pool retires 401-class keys and the on-demand refresh hook can rotate
    the token); credit markers → budget_exceeded; other codes stay generic.
    """
    code = env.get("code")
    msg = str(env.get("msg") or f"workbuddy business code {code}")
    lower = msg.lower()
    if code == 12153 or "offline user session" in lower:
        return WiwiError(401, "authentication_error",
                         f"workbuddy session dead (code {code}): {msg}",
                         retryable=True)
    if code == 402 or any(m in lower for m in (
            "insufficient credit", "no credit", "credit exhausted",
            "out of credit", "quota exceeded", "quota exhaust",
            "payment required", "credit not enough", "not enough credit",
            "积分不足", "额度不足", "余额不足", "积分用完", "额度用尽", "没有积分")):
        return WiwiError(402, "budget_exceeded",
                         f"workbuddy credit exhausted (code {code}): {msg}")
    return WiwiError(502, "api_error", f"workbuddy error code {code}: {msg}",
                     retryable=False)
