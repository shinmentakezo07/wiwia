"""Tool-call argument validation on close.

Validates accumulated tool-call arguments against the tool's JSON schema when
a ``ToolCallClose`` delta arrives. Violations are logged via structlog and
attached to request metadata rather than failing the stream — the client
still receives the tool call, but with a warning flag.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)


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
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError:
        msg = f"tool '{tool_name}': arguments are not valid JSON"
        log.warning("tool_args_invalid_json", tool=tool_name, raw_args=raw_args[:200])
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
