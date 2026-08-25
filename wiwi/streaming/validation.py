"""Tool-call argument validation on close.

Validates accumulated tool-call arguments against the tool's JSON schema when
a ``ToolCallClose`` delta arrives. Violations are logged via structlog and
attached to request metadata rather than failing the stream — the client
still receives the tool call, but with a warning flag.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Cap raw tool-arg payloads at 1 MiB to bound memory and JSON-parse time.
# A model can otherwise emit an unbounded stream that grows the buffer
# without limit, eventually OOM-ing the gateway process.
MAX_TOOL_ARGS_BYTES = 1024 * 1024


def _fingerprint(raw_args: str) -> str:
    """Stable short hash of *raw_args* for log correlation without leaking PII."""
    return hashlib.sha256(raw_args.encode("utf-8", "replace")).hexdigest()[:16]


def validate_tool_args(
    tool_name: str,
    raw_args: str,
    schema: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Validate *raw_args* against *schema*.

    Returns ``(valid, message)``. When *schema* is None or missing, validation
    is skipped and ``(True, "")`` is returned. Violations are logged but never
    raise — the caller decides whether to attach a warning.
    """
    if not schema:
        return True, ""
    # Reject oversize payloads up front so a hostile/malformed model cannot
    # push megabytes of args into a JSON parse. We never log the payload
    # itself — just length and a short fingerprint.
    raw_bytes = len(raw_args.encode("utf-8", "replace"))
    if raw_bytes > MAX_TOOL_ARGS_BYTES:
        msg = f"tool '{tool_name}': arguments exceed {MAX_TOOL_ARGS_BYTES} byte cap"
        log.warning("tool_args_oversize", tool=tool_name,
                    bytes=raw_bytes, fingerprint=_fingerprint(raw_args))
        return False, msg
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError:
        msg = f"tool '{tool_name}': arguments are not valid JSON"
        # Never log raw_args: tool arguments often contain user secrets.
        log.warning("tool_args_invalid_json", tool=tool_name,
                    bytes=raw_bytes, fingerprint=_fingerprint(raw_args))
        return False, msg

    # Basic type checking against the schema.
    expected_type = schema.get("type")
    if expected_type and not _check_type(args, expected_type):
        msg = f"tool '{tool_name}': expected {expected_type}, got {type(args).__name__}"
        log.warning("tool_args_type_mismatch", tool=tool_name,
                     expected=expected_type, actual=type(args).__name__)
        return False, msg

    # Check required properties for object type.
    if expected_type == "object" and isinstance(args, dict):
        required = schema.get("required", [])
        missing = [r for r in required if r not in args]
        if missing:
            msg = f"tool '{tool_name}': missing required properties: {missing}"
            log.warning("tool_args_missing_required", tool=tool_name, missing=missing)
            return False, msg

    return True, ""


def _check_type(value: Any, expected: str) -> bool:
    """Check that *value* matches the JSON schema *expected* type string."""
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }
    python_type = type_map.get(expected)
    if python_type is None:
        return True  # unknown type: skip
    # bool is a subclass of int, so check it separately.
    if expected == "integer" and isinstance(value, bool):
        return False
    return isinstance(value, python_type)
