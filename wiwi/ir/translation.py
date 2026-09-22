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

# ---------------------------------------------------------------------------
# Tool-turn validity
# ---------------------------------------------------------------------------
# Every surface encodes an IR ``tool_call`` stop reason into its own spelling
# (OpenAI ``tool_calls``, Anthropic ``tool_use``), and every one of them is
# invalid if the turn carries no tool call the CLIENT can act on. Clients --
# OpenAI SDK, Codex CLI, Claude Code -- branch on that reason to decide whether
# to dispatch tools, so emitting it with an empty tool set either stalls the
# agent (a ``tool_calls`` array that never arrives) or ends a tool turn early.
#
# The rule was re-derived, slightly differently and incompletely, in each
# encoder: ``openai_chat`` guarded only when a builtin had been suppressed
# (AUDIT #271), the Responses surface never guarded at all (AUDIT #272), and
# the Anthropic encoder keyed on a flag that the provider-hosted path never
# set. One predicate, consulted by all three, is the fix for the class rather
# than for each instance.


def tool_call_finish_is_valid(
    stop_reason: str,
    *,
    emitted_calls: int = 0,
    emitted_server_calls: int = 0,
) -> bool:
    """Is ``stop_reason`` a legal stop for a turn that emitted these calls?

    A ``tool_call`` reason is valid only when at least one tool call reached
    the client. Count both flavours:

    - ``emitted_calls`` -- client-dispatched ``tool_use`` / ``function_call``
      items the client will run and answer.
    - ``emitted_server_calls`` -- provider-hosted items (Anthropic
      ``server_tool_use`` / ``mcp_tool_use``, Responses ``web_search_call``).
      Anthropic's own upstream reports ``tool_use`` for a turn made of these,
      so a surface that emits them must keep the reason.

    Every non-``tool_call`` reason is vacuously valid: this predicate answers
    only the one question, and callers downgrade on ``False`` alone.
    """
    if stop_reason != "tool_call":
        return True
    return (emitted_calls + emitted_server_calls) > 0


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
