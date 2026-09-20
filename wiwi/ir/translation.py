"""Shared canonical translation helpers.

These live in ``wiwi/ir/`` — not in ``wiwi/wire/`` — because **both** the
inbound codecs (``wiwi/wire/``) and the outbound adapters (``wiwi/providers/``)
need the same mappings, and the layering rule forbids ``wiwi/providers/`` from
importing anything in ``wiwi/wire/``. ``wiwi/ir/`` is the one package both
layers may import from, so the shared vocabulary lives here and the two sides
cannot drift apart.

Two families of helper:

* **finish / stop reason** — OpenAI spells the terminal reason one way, the IR
  another, and Anthropic a third. Providers in the wild also emit non-standard
  OpenAI spellings (``tool_use``, ``max_tokens``, ``end_turn``), so the
  normalizer is total: an unknown value degrades to ``"stop"`` rather than
  raising inside a stream decoder.
* **extras** — the request-body keys nobody modelled. The two codecs disagree
  on how to collect them (the Anthropic codec uses an allowlist, the OpenAI
  codec a denylist), and both used bare ``{**a, **b}`` merges that raise when a
  layer is ``None``. These helpers make both operations total.
"""
from typing import Any

import structlog

from wiwi.ir import types as ir

log = structlog.get_logger("wiwi.ir.translation")

# The single OpenAI ``finish_reason`` → IR map. Covers the documented OpenAI
# values plus the non-standard spellings seen from OpenAI-compatible servers
# (vLLM, llama.cpp, some Gemini/Anthropic shims) which otherwise silently
# landed on "stop" and cut a tool turn short.
OPENAI_FINISH_TO_IR: dict[str, ir.StopReason] = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_call",
    "function_call": "tool_call",
    "content_filter": "content_filter",
    # Non-standard, seen in the wild:
    "tool_use": "tool_call",
    "max_tokens": "length",
    "end_turn": "stop",
    "stop_sequence": "stop_sequence",
    "error": "stop",
}

# IR → OpenAI ``finish_reason``. Deliberately narrower than the IR vocabulary:
# Anthropic-only reasons (pause_turn, compaction) have no OpenAI spelling and
# are handled by ``ir_to_openai_finish``'s fallback, not listed here.
_IR_TO_OPENAI_FINISH: dict[str, str] = {
    "stop": "stop",
    "length": "length",
    "tool_call": "tool_calls",
    "content_filter": "content_filter",
}

# Terminal reason an OpenAI client is guaranteed to understand.
_OPENAI_FALLBACK_FINISH = "stop"

# Distinct unmapped values already logged, so a hot stream loop cannot flood
# the log with one line per chunk. Bounded: these strings come from upstream
# responses, so an unbounded set is a slow memory leak on a long-lived process
# fed junk. Past the cap the dedup stops (a few extra log lines) rather than
# the set growing without limit.
_UNMAPPED_LOGGED: set[str] = set()
_UNMAPPED_LOGGED_CAP = 1000


def normalize_finish_reason(raw: Any) -> ir.StopReason:
    """Map an upstream OpenAI ``finish_reason`` onto an IR ``StopReason``.

    Total by construction: non-``str`` input and unknown spellings both yield
    ``"stop"`` and emit ``finish_reason_unmapped`` once per distinct value per
    process. Never raises — this runs inside stream decoders, where an
    exception would abort a response mid-flight.
    """
    if not isinstance(raw, str):
        _log_unmapped(repr(raw))
        return "stop"
    mapped = OPENAI_FINISH_TO_IR.get(raw)
    if mapped is None:
        _log_unmapped(raw)
        return "stop"
    return mapped


def ir_to_openai_finish(stop_reason: str) -> str:
    """Map an IR ``StopReason`` onto the OpenAI ``finish_reason`` vocabulary.

    Total: an Anthropic-only or otherwise unknown reason falls back to
    ``"stop"`` so an OpenAI client always receives a value it accepts.
    """
    if not isinstance(stop_reason, str):
        return _OPENAI_FALLBACK_FINISH
    return _IR_TO_OPENAI_FINISH.get(stop_reason, _OPENAI_FALLBACK_FINISH)


def carry_extras(source: Any, known: frozenset[str]) -> dict[str, Any]:
    """Return the sub-dict of ``source`` whose keys are *not* in ``known``.

    The denylist shape ``wiwi/wire/openai_chat.py`` already uses: every key the
    codec did not model is carried through to the provider untouched. Total —
    a non-mapping ``source`` yields ``{}`` instead of raising.
    """
    if not isinstance(source, dict):
        return {}
    extras = {k: v for k, v in source.items() if k not in known}
    if extras:
        log.debug(
            "extras_carried",
            carried=sorted(extras),
            known_count=len(known),
        )
    return extras


def merge_extras(*layers: Any) -> dict[str, Any]:
    """Merge extras layers left-to-right; later layers win.

    Skips non-dict layers so a ``None`` (the common case: an optional upstream
    field that was absent) cannot poison the outgoing body the way a bare
    ``{**layer}`` would.
    """
    merged: dict[str, Any] = {}
    for layer in layers:
        if isinstance(layer, dict):
            merged.update(layer)
    return merged


def _log_unmapped(raw: str) -> None:
    """Log an unmapped finish reason once per distinct value, up to the cap."""
    if raw in _UNMAPPED_LOGGED:
        return
    if len(_UNMAPPED_LOGGED) < _UNMAPPED_LOGGED_CAP:
        _UNMAPPED_LOGGED.add(raw)
    log.debug("finish_reason_unmapped", raw=raw)
