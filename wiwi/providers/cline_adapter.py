"""Cline provider adapter (api.cline.bot).

Cline is an OpenAI Chat Completions-compatible gateway with three quirks:

1. Auth: WorkOS OAuth tokens sent as ``Authorization: Bearer workos:<token>``
   — the ``workos:`` prefix is mandatory and auto-prepended when missing.
2. Fingerprint: every request must carry a client-identification header set
   (``HTTP-Referer``, ``X-Title``, ``X-CLIENT-*``, ``X-PLATFORM*``); missing
   headers are rejected upstream. Versions default to the running wiwi
   version, read live at request-build time.
3. Streaming-only upstream: the chat/completions endpoint only implements
   SSE. ``stream`` is always forced True in the encoded body regardless of
   what the client asked for (the gateway re-assembles deltas for
   non-streaming callers), and ``stream_options`` is never forwarded.

Response bodies — both JSON and SSE chunks — may be wrapped in a
``{"success": ..., "data": {...}}`` envelope; the ``data`` payload is
unwrapped transparently before normal OpenAI decoding.
"""

from __future__ import annotations

import json
import re
from typing import Any

import orjson

from wiwi import __version__
from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.streaming import deltas as dl

_CLINE_BASE = "https://api.cline.bot/api/v1"

_HEADER_VALUE_RE = re.compile(r"[^\r\n\x00]")
_MAX_HEADER_LEN = 256


def _clean(value: Any) -> str | None:
    """Sanitize a header value: strip, cap length, drop CR/LF/NUL.

    Returns None when nothing usable remains — callers omit the header.
    """
    if value is None:
        return None
    text = "".join(_HEADER_VALUE_RE.findall(str(value))).strip()
    if not text:
        return None
    return text[:_MAX_HEADER_LEN]


def _ensure_workos_prefix(token: str) -> str:
    """WorkOS access tokens must be sent as ``Bearer workos:<token>``.
    Bare tokens (from admin key config) get the prefix; an already-prefixed
    token passes through unchanged."""
    trimmed = token.strip()
    if trimmed.startswith("workos:"):
        return trimmed
    return f"workos:{trimmed}"


def _unwrap_envelope(data: Any) -> Any:
    """Unwrap ``{"success": ..., "data": <openai-payload>}`` when present.

    Returns the payload unchanged when the body is not the envelope shape
    (plain OpenAI bodies have no top-level ``data`` with ``choices``).
    """
    if (isinstance(data, dict) and isinstance(data.get("data"), dict)
            and isinstance(data["data"].get("choices"), list)):
        return data["data"]
    return data


class ClineAdapter(OpenAIAdapter):
    """OpenAI wire format + Cline auth/fingerprint/streaming quirks."""

    provider_type = "cline"
    force_stream = True  # gateway reads this: upstream has no non-streaming mode

    def __init__(self) -> None:
        super().__init__()
        self._context: dict[str, str] = {}

    def set_header_context(self, context: dict[str, str]) -> None:
        """Set per-request identity context (e.g. a client-sent task id).

        Values come from the inbound request, never fabricated here; only
        known keys are consumed and everything is sanitized.
        """
        self._context = dict(context or {})

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        client_version = _clean(__version__) or "unknown"
        platform = _clean(_current_platform()) or "unknown"
        h: dict[str, str] = {
            "HTTP-Referer": "https://cline.bot",
            "X-Title": "Cline",
            "User-Agent": f"Cline/{client_version}",
            "X-CLIENT-TYPE": "wiwi",
            "X-CLIENT-VERSION": client_version,
            "X-CORE-VERSION": client_version,
            "X-PLATFORM": platform,
            "X-PLATFORM-VERSION": _clean(_current_platform_version()) or "unknown",
            "X-IS-MULTIROOT": "false",
        }
        task_id = _clean(self._context.get("task_id"))
        if task_id:
            h["X-Task-ID"] = task_id
        h["Authorization"] = f"Bearer {_ensure_workos_prefix(key.secret)}"
        return h

    def build_url(self, base_url: str, model_id: str, stream: bool, kind: str) -> str:
        base = (base_url or _CLINE_BASE).rstrip("/")
        return f"{base}/chat/completions"

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        body = super().encode_request(req, model_id, deployment_params)
        # Streaming-only upstream: force SSE and never send stream_options.
        body["stream"] = True
        body.pop("stream_options", None)
        return body

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        return super().decode_response(status, _envelope_bytes(body))

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        # Mid-stream error chunks: Cline surfaces {"error": {...}} with no
        # choices — emit StreamError so the gateway can fail the stream.
        if data != "[DONE]":
            try:
                chunk = orjson.loads(data)
            except (json.JSONDecodeError, ValueError):
                chunk = None
            if isinstance(chunk, dict) and isinstance(chunk.get("error"), dict):
                msg = str(chunk["error"].get("message") or "Cline stream error")
                return [dl.StreamError(message=msg, kind="status")]
        unwrapped = _unwrap_stream_data(event, data)
        if unwrapped is None:
            return super().decode_stream_event(event, data)
        event_name, payload = unwrapped
        return super().decode_stream_event(event_name, payload)


def _envelope_bytes(body: bytes) -> bytes:
    try:
        data = orjson.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body
    unwrapped = _unwrap_envelope(data)
    if unwrapped is data:
        return body
    return orjson.dumps(unwrapped)


def _unwrap_stream_data(event: str, data: str) -> tuple[str, str] | None:
    """Rewrite an SSE data payload through the envelope unwrap.

    Returns (event, new_data) when unwrapping changed the payload, else None
    (so the parent class sees the original string, including "[DONE]").
    """
    if data == "[DONE]":
        return None
    try:
        chunk = orjson.loads(data)
    except (json.JSONDecodeError, ValueError):
        return None
    unwrapped = _unwrap_envelope(chunk)
    if unwrapped is chunk:
        return None
    return event, orjson.dumps(unwrapped).decode()

def _current_platform() -> str:
    import sys
    return sys.platform


def _current_platform_version() -> str:
    import sys
    return sys.version.split()[0]
