"""Incremental JSON parser for streaming tool-call arguments.

Ports the Vercel AI SDK ``partial-json`` approach: parse **incomplete** JSON
streams so clients can render tool arguments as they arrive, and **auto-repair
truncated JSON** at close time (append missing ``"``, ``]``, ``}``) instead of
dropping args to ``{}``.

Usage:
    parser = PartialJSONParser()
    for fragment in stream:
        value = parser.feed(fragment)   # best-effort parse of accumulated text
    final = parser.finalize()           # repair + parse; always returns a dict

The parser never raises on malformed or truncated input; it returns the
best-effort value (``{}`` as ultimate fallback).
"""

from __future__ import annotations

import json
from typing import Any

# Pairs that need closing when truncated.
_OPEN_CLOSE = {"{": "}", "[": "]", "(": ")"}
_CLOSE_CHARS = set(_OPEN_CLOSE.values())
# When we see these characters we *might* be in the middle of a string token.
_QUOTE = '"'


def _repair_truncated_json(text: str) -> str:
    """Append missing closing characters for truncated JSON.

    Tracks nesting depth and string state, then appends whatever closers
    are needed to produce valid JSON.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == _QUOTE:
                in_string = False
            continue
        if ch == _QUOTE:
            in_string = True
        elif ch in _OPEN_CLOSE:
            stack.append(_OPEN_CLOSE[ch])
        elif ch in _CLOSE_CHARS and stack and stack[-1] == ch:
            stack.pop()
    suffix = ""
    if in_string:
        # Unterminated string. If the last character was a single backslash
        # (escape = True) it is a dangling escape sequence and the string
        # cannot be closed cleanly with just a quote — appending `"` would
        # produce invalid JSON like {"k": "v\\". Drop the trailing backslash
        # first. Doubled backslashes (`\\\\`) are escaped backslashes and are
        # safe to leave alone.
        if escaped and text.endswith("\\") and not text.endswith("\\\\"):
            text = text[:-1]
        suffix += _QUOTE
    # Close open containers in reverse order.
    suffix += "".join(reversed(stack))
    return text + suffix


def parse_partial(text: str) -> tuple[Any, bool]:
    """Parse potentially-incomplete JSON.

    Returns ``(value, complete)``.  When *complete* is ``False`` the JSON
    is still being streamed and *value* is the best-effort parse of the
    accumulated text so far (may be truncated/empty).  When ``True`` the
    JSON parsed without needing repair.

    Never raises on truncated or malformed input; returns ``({}, False)``
    as the ultimate fallback.
    """
    if not text or not text.strip():
        return {}, False
    text = text.strip()
    try:
        return json.loads(text), True
    except json.JSONDecodeError:
        pass
    # Try repairing truncated JSON.
    repaired = _repair_truncated_json(text)
    try:
        return json.loads(repaired), False
    except json.JSONDecodeError:
        return {}, False


class PartialJSONParser:
    """Stateful accumulator for streaming JSON tool-call arguments.

    Call :meth:`feed` with each fragment as it arrives; call :meth:`finalize`
    when the tool call closes to get the repaired final value.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, fragment: str) -> Any:
        """Accumulate *fragment* and return the best-effort parsed value."""
        self._buf += fragment
        value, _complete = parse_partial(self._buf)
        return value

    def finalize(self) -> Any:
        """Repair and parse the accumulated buffer. Always returns a dict."""
        if not self._buf.strip():
            return {}
        repaired = _repair_truncated_json(self._buf.strip())
        try:
            result = json.loads(repaired)
            if isinstance(result, dict):
                return result
            return {"value": result}
        except json.JSONDecodeError:
            return {}

    @property
    def raw(self) -> str:
        """The accumulated raw string so far."""
        return self._buf

    def reset(self) -> None:
        self._buf = ""
