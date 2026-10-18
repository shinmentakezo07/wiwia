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
import re
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


def _scan_json(text: str) -> tuple[list[str], bool, int, bool]:
    """Track JSON nesting and string state in one pass.

    Returns ``(stack, in_string, open_idx, open_is_key)``: the closers still
    owed (innermost last), whether the text ends inside a string, the index of
    that string's opening quote (``-1`` when the text ends outside a string),
    and whether that string sits where an object *key* belongs — a string at the
    head of an object or right after a comma inside one, with no ``:`` yet.

    Key detection needs the last significant character seen *outside* strings
    (``prev_sig``), which is why it is tracked here rather than recovered later:
    the difference between ``{"a":`` (a dangling key) and ``{"a": "b`` (a value
    string worth closing) is exactly that character.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    prev_sig = ""
    open_idx = -1
    open_is_key = False
    for idx, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == _QUOTE:
                in_string = False
                prev_sig = ch
            continue
        if ch == _QUOTE:
            in_string = True
            open_idx = idx
            open_is_key = (prev_sig in ("{", ",") and bool(stack)
                           and stack[-1] == "}")
        elif ch in _OPEN_CLOSE:
            if len(stack) < MAX_REPAIR_DEPTH:
                stack.append(_OPEN_CLOSE[ch])
            # else: drop the opener on the floor; the repair will close
            # what we have but not balloon the suffix.
            prev_sig = ch
        elif ch in _CLOSE_CHARS and stack and stack[-1] == ch:
            stack.pop()
            prev_sig = ch
        elif not ch.isspace():
            prev_sig = ch
    return stack, in_string, open_idx, open_is_key


# JSON's only bare literals. A truncated one is unambiguous — no other literal
# starts with ``t``/``f``/``n`` — so a half-written ``tru`` can be *completed*
# instead of dropped, keeping the value the model was in the middle of writing.
_PARTIAL_LITERALS = {"t": "true", "tr": "true", "tru": "true",
                     "f": "false", "fa": "false", "fal": "false",
                     "fals": "false", "n": "null", "nu": "null", "nul": "null"}

# A number token that cannot terminate: a trailing '.', a bare exponent, or an
# exponent whose sign arrived without its digits. The WHOLE token matches, not
# just the offending '.e+', because the digits seen so far are a prefix of the
# number the model was writing — ``12.`` is on its way to ``12.5``, so keeping
# ``12`` would assert a value that was never written. Strings are excluded by
# construction — the trimmer only ever runs on text that ends outside one.
_DANGLING_NUMBER = re.compile(r"[0-9][0-9.eE+-]*[.eE+-]$")

# The final string token of a text that ends with a closing quote. ``\\.``
# keeps an escaped quote inside the token, so ``"a\"`` does not match (it is an
# unterminated string, and the in-string path owns it).
_TRAILING_STRING = re.compile(r'"(?:[^"\\]|\\.)*"$')


def _trim_incomplete_value(text: str, stack: list[str]) -> str:
    """Cut a truncated JSON text back to its last complete element.

    Appending closers is enough when the cut lands *between* tokens, which is
    not where a real token stream cuts: mid-separator (``{"a": 1,``), mid-key
    (``{"b":``), mid-literal (``{"a": tru``) and mid-number (``{"n": 1.``) are
    the common cases. Closing those produced ``{"a": 1,}`` / ``{"b": }`` /
    ``{"a": tru}`` / ``{"n": 1.}`` — none of which is JSON, so every consumer of
    a repaired tool call fell back to ``{}`` and lost EVERY argument in the
    object at exactly the boundaries that occur most in practice, while
    streamed partial views (``parse_partial``) showed the values fine
    (AUDIT #482).

    A half-written literal is *completed* — its prefix is unambiguous and the
    value is real. A trailing comma, a lone sign, a dangling colon, a dangling
    key, and a number tail that cannot terminate are *dropped*: there is no
    value to invent, and the digits seen for ``1.`` are a prefix of a number
    that had not finished arriving — this repaired text is what the call is
    executed with, so keeping them would run a tool on a value the model never
    wrote. ``stack`` is the nesting from the full text and stays valid, because
    every cut here removes a token, never a bracket.
    """
    while True:
        text = text.rstrip()
        if not text:
            return text
        # A trailing separator carries no value of its own.
        if text[-1] in ",:":
            text = text[:-1]
            continue
        # A lone sign can only be a number that never started.
        if text[-1] in "+-":
            text = text[:-1]
            continue
        # A number tail that cannot terminate must be tested BEFORE the literal
        # branch: an exponent's ``e`` is a letter, so a text ending ``1e`` would
        # otherwise look like a half-written literal and be returned as-is.
        #
        # The whole token goes, not just the offending '.e+': the digits are a
        # prefix of a number that had not finished arriving, and this repaired
        # text is what the call is executed with — ``core/gateway.py``'s
        # ``_apply_event`` parses it and assigns ``tc.args``, as do the
        # decode-side repairs in ``wire/openai_chat.py``. Keeping the prefix
        # would run a tool on a value the model never wrote; dropping the token
        # lets the passes above drop the key that owned it, which is the honest
        # answer.
        m = _DANGLING_NUMBER.search(text)
        if m is not None:
            text = text[:m.start()]
            continue
        m = re.search(r"[A-Za-z]+$", text)
        if m is not None:
            completion = _PARTIAL_LITERALS.get(m.group(0))
            if completion is None:
                return text          # true/false/null are already complete
            return text[:m.start()] + completion
        if text.endswith(_QUOTE):
            m = _TRAILING_STRING.search(text)
            if m is not None:
                before = text[:m.start()].rstrip()
                # A complete string with nothing after it and no colon in front
                # is a KEY whose value never arrived. Only inside an object: the
                # same string inside an array is a finished value.
                if (before and before[-1] in "{," and stack
                        and stack[-1] == "}"):
                    text = before
                    continue
            return text
        return text


def _repair_truncated_json(text: str) -> str:
    """Append missing closing characters for truncated JSON.

    Tracks nesting depth and string state, then appends whatever closers
    are needed to produce valid JSON.  The container stack is bounded by
    :data:`MAX_REPAIR_DEPTH` so deeply-nested pathological input does not
    blow memory or spend unbounded time producing an arbitrarily long suffix.

    Closers alone are only sufficient when the cut landed between tokens, so
    the tail is also trimmed of a half-written token first (AUDIT #482), and a
    cut inside a *key* drops the pair rather than closing a key that can never
    be given a value.
    """
    stack, in_string, open_idx, open_is_key = _scan_json(text)
    # A stream cut mid-key (``{"qu``) has no value to keep: closing the key's
    # quote yields ``{"qu"}``, which is not JSON either, so drop the pair and
    # re-scan. Each pass strictly shortens the text, so this terminates.
    while in_string and open_is_key:
        text = text[:open_idx].rstrip()
        stack, in_string, open_idx, open_is_key = _scan_json(text)
    suffix = ""
    if in_string:
        # Unterminated string. Count the trailing backslash run: an ODD run
        # means the last backslash is a dangling escape (1, 3, 5, ...), so
        # the string cannot be closed cleanly with just a quote — strip that
        # final backslash first. An even run is escaped backslashes and is
        # safe to leave alone. (The old endswith heuristic only handled a
        # single trailing backslash and its doubled exclusion; runs >= 3
        # fell through and produced invalid JSON.)
        n_bs = len(text) - len(text.rstrip("\\"))
        if n_bs % 2 == 1:
            text = text[:-1]
        # An incomplete ``\uXXXX`` unicode escape (a backslash followed by
        # 0-3 hex digits) likewise cannot be closed cleanly: appending `"`
        # turns the partial escape into invalid JSON. Escape-aware: only a
        # backslash run of ODD length before the ``u`` means the final
        # backslash opens a real ``\u`` escape; an even run is complete
        # escaped-backslash pairs and the trailing ``u…`` is literal text
        # (``"C:\\u0f`` must NOT be stripped). Strip only the one fresh
        # backslash + hex tail, preserving the pairs before it.
        m = re.search(r'(\\+)u[0-9a-fA-F]{0,3}$', text)
        if m and len(m.group(1)) % 2 == 1:
            text = re.sub(r'\\u[0-9a-fA-F]{0,3}$', '', text)
        # A *complete* \uXXXX escape at the tail may still be half of a
        # surrogate pair whose low half never arrived (the model was cut
        # mid-emoji by a token limit). Closing the string there yields a lone
        # surrogate: `json.loads` accepts it, but `orjson.dumps` and httpx
        # reject it with `TypeError: str is not valid UTF-8`, so the repaired
        # args killed the user's turn with a 500. Strip the dangling escape
        # instead — a partial pair is unusable either way, and dropping it
        # leaves valid, encodable JSON (AUDIT #139).
        #
        # Escape-aware, exactly like the partial-escape strip above: only an
        # ODD backslash run before the ``u`` means that backslash opens a real
        # escape. An even run is complete escaped-backslash pairs and the
        # trailing ``uXXXX`` is literal text (``"C:\\uD83D`` is a literal
        # ``\uD83D``), so stripping it left a dangling backslash and lost the
        # whole argument object (AUDIT #193).
        m = re.search(r'(\\+)u([0-9a-fA-F]{4})$', text)
        if m and len(m.group(1)) % 2 == 1:
            # Drop only the one fresh backslash that opens the escape; the
            # complete escaped pairs before it stay (``"a\\\uD83D`` decodes
            # to one literal backslash, not to nothing).
            keep = m.group(1)[:-1]
            code = int(m.group(2), 16)
            if 0xD800 <= code <= 0xDBFF:
                # High surrogate: its low half would follow it, so a high
                # surrogate at the tail is always dangling.
                text = text[:m.start()] + keep
            elif 0xDC00 <= code <= 0xDFFF:
                # Low surrogate: only valid when a high surrogate escape
                # immediately precedes it. A bare low surrogate is as
                # unencodable as a bare high one. The predecessor must be a
                # *real* escape too — an even run before its ``u`` makes it
                # literal text, and treating it as the pair's high half kept
                # the lone low surrogate alive (AUDIT #193).
                prev = re.search(r'(\\+)u([0-9a-fA-F]{4})$', text[:m.start()])
                if not (prev and len(prev.group(1)) % 2 == 1
                        and 0xD800 <= int(prev.group(2), 16) <= 0xDBFF):
                    text = text[:m.start()] + keep
        suffix += _QUOTE
    else:
        text = _trim_incomplete_value(text, stack)
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

        The cap is enforced in **characters** (``MAX_BUFFER_BYTES`` is a
        count bound, not a strict byte limit): multi-byte UTF-8 text must
        not cause the head-trim to over-drop characters, and a single huge
        fragment must not exceed the bound after account for its tail.
        """
        if not fragment:
            return parse_partial(self._buf)[0]
        # Cheap check: if the fragment alone reaches the cap, only keep
        # its tail. Otherwise append and trim the head if needed.
        if len(fragment) >= MAX_BUFFER_BYTES:
            self._buf = fragment[-MAX_BUFFER_BYTES:]
        else:
            self._buf += fragment
            overflow = len(self._buf) - MAX_BUFFER_BYTES
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
