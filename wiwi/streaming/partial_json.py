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

# Buffer cap (1 MiB) and repair-stack cap (4k) to bound memory and CPU on
# pathological inputs. A misbehaving model can otherwise send an unbounded
# stream that grows the buffer until the gateway OOMs.
MAX_BUFFER_BYTES = 1024 * 1024
MAX_REPAIR_DEPTH = 4096


def _repair_truncated_json(text: str) -> str:
    """Append missing closing characters for truncated JSON.

    Tracks nesting depth and string state, then appends whatever closers
    are needed to produce valid JSON.  The container stack is bounded by
    :data:`MAX_REPAIR_DEPTH` so deeply-nested pathological input does not
    blow memory or spend unbounded time producing an arbitrarily long suffix.
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
            if len(stack) < MAX_REPAIR_DEPTH:
                stack.append(_OPEN_CLOSE[ch])
            # else: drop the opener on the floor; the repair will close
            # what we have but not balloon the suffix.
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

    The internal buffer is bounded by :data:`MAX_BUFFER_BYTES` so a
    misbehaving model cannot grow it without limit.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, fragment: str) -> Any:
        """Accumulate *fragment* and return the best-effort parsed value.

        Once the buffer reaches the cap, further fragments replace the
        tail of the buffer rather than appending — this preserves the
        most recent arg text (which is what a partial-JSON parser can
        still make sense of) while bounding memory.
        """
        if not fragment:
            return parse_partial(self._buf)[0]
        # Cheap byte check: if the fragment alone exceeds the cap, only
        # keep its tail. Otherwise append and trim the head if needed.
        if len(fragment.encode("utf-8", "replace")) >= MAX_BUFFER_BYTES:
            self._buf = fragment[-MAX_BUFFER_BYTES:]
        else:
            self._buf += fragment
            overflow = len(self._buf.encode("utf-8", "replace")) - MAX_BUFFER_BYTES
            if overflow > 0:
                # Drop the overflow from the head, keeping the most recent
                # MAX_BUFFER_BYTES characters intact.
                self._buf = self._buf[overflow:]
        return parse_partial(self._buf)[0]

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
