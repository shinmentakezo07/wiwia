"""Normalize native MiniMax-M3 tool markup leaked by NVIDIA NIM.

Some NIM-hosted models (MiniMax-M3, StepFun) emit tool calls as text content
using a custom XML-like namespace instead of proper OpenAI ``tool_calls``
deltas.  This module converts that markup into ordinary ``IRStreamDelta``
tool-call deltas so the rest of the gateway sees a clean stream.

The framer is a state machine fed incrementally by ``delta.content`` chunks:

  TEXT -> (sees tool-block-start marker) -> TOOL_BLOCK
       -> (sees tool-block-end marker)   -> AFTER_TOOL_BLOCK -> FINISHED

In TEXT mode, visible text before the marker is emitted immediately; a
partial marker at the tail is held back until the next chunk clarifies it.
In TOOL_BLOCK mode, everything is buffered until the end marker.

Ported from free-claude-code's ``providers/nvidia_nim/native_tool_stream.py``,
adapted to wiwi's IRStreamDelta taxonomy (per-event processing, not
iterator wrapping).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from wiwi.ir import types as ir  # noqa: F401  (re-exported for convenience)
from wiwi.streaming import deltas as dl

# -- markers (exact Unicode from the reference implementation) ----------------
# The namespace is a bracket-delimited tag: ]<]minimax[>[
_NAMESPACE = "]<]minimax[>["
_TOOL_BLOCK_START = _NAMESPACE + chr(0x3C) + "tool_call" + chr(0x3E)
_TOOL_BLOCK_END = _NAMESPACE + chr(0x3C) + "/tool_call" + chr(0x3E)
_INVOKE_START = _NAMESPACE + "<invoke"
_INVOKE_END = _NAMESPACE + "</invoke>"
_ELEMENT_START = _NAMESPACE + "<"
_ELEMENT_END_START = _NAMESPACE + "</"
_MIXED_TEXT_FIELD = "$text"
_MAX_TOOL_BLOCK_CHARS = 4 * 1024 * 1024
_MAX_ARG_DEPTH = 64


class NimToolProtocolError(Exception):
    """Malformed native MiniMax tool markup from NIM."""


# -- data structures ----------------------------------------------------------

@dataclass(frozen=True)
class _Element:
    name: str
    text: str
    children: tuple[_Element, ...]


@dataclass(frozen=True)
class NativeToolCall:
    index: int
    name: str
    arguments: dict[str, Any]


class _FramingMode(Enum):
    TEXT = "text"
    TOOL_BLOCK = "tool_block"
    AFTER_TOOL_BLOCK = "after_tool_block"
    FINISHED = "finished"


# -- framer: splits visible text from one native tool block --------------------

class MiniMaxFramer:
    """Stateful framer that separates visible text from a native tool block.

    ``feed(text_chunk)`` returns visible text (or ``""`` when buffering).
    When the tool block end marker is seen, ``tool_block`` holds the complete
    block and the framer switches to AFTER_TOOL_BLOCK mode.

    ``finish()`` flushes any held-back text tail and returns it.
    """

    def __init__(self) -> None:
        self._mode = _FramingMode.TEXT
        self._text_tail = ""
        self._tool_tail = ""
        self._tool_parts: list[str] = []
        self._tool_length = 0
        self.tool_block: str | None = None

    def feed(self, text: str) -> str:
        if self._mode is _FramingMode.FINISHED:
            raise NimToolProtocolError("framer already finished")
        if not text:
            return ""
        if self._mode is _FramingMode.TOOL_BLOCK:
            self._feed_tool_block(text)
            return ""
        if self._mode is _FramingMode.AFTER_TOOL_BLOCK:
            self._validate_trailing(text)
            return ""

        candidate = self._text_tail + text
        marker_idx = candidate.find(_TOOL_BLOCK_START)
        held = _partial_marker_suffix_length(candidate, _TOOL_BLOCK_START)
        protected_ns = (
            marker_idx if marker_idx >= 0
            else len(candidate) - held if held
            else -1
        )
        ns_idx = candidate.find(_NAMESPACE)
        if ns_idx >= 0 and ns_idx != protected_ns:
            raise NimToolProtocolError("unexpected NIM namespace in text")

        if marker_idx >= 0:
            visible = candidate[:marker_idx]
            self._text_tail = ""
            self._mode = _FramingMode.TOOL_BLOCK
            self._feed_tool_block(candidate[marker_idx + len(_TOOL_BLOCK_START):])
            return visible

        if held:
            visible = candidate[:-held]
            self._text_tail = candidate[-held:]
            return visible

        self._text_tail = ""
        return candidate

    def finish(self) -> str:
        if self._mode is _FramingMode.FINISHED:
            return ""
        if self._mode is _FramingMode.TEXT:
            tail = self._text_tail
            self._text_tail = ""
            self._mode = _FramingMode.FINISHED
            if _NAMESPACE in tail:
                # Incomplete marker -- drop it rather than emit garbage.
                return ""
            return tail
        # TOOL_BLOCK at finish = incomplete tool call; drop it.
        self._tool_parts.clear()
        self._tool_tail = ""
        self._mode = _FramingMode.FINISHED
        return ""

    def _feed_tool_block(self, text: str) -> None:
        candidate = self._tool_tail + text
        end_idx = candidate.find(_TOOL_BLOCK_END)
        if end_idx < 0:
            held = _partial_marker_suffix_length(candidate, _TOOL_BLOCK_END)
            if held:
                self._append_tool_fragment(candidate[:-held])
                self._tool_tail = candidate[-held:]
            else:
                self._append_tool_fragment(candidate)
                self._tool_tail = ""
            return
        self._append_tool_fragment(candidate[:end_idx])
        self.tool_block = "".join(self._tool_parts)
        self._tool_parts.clear()
        self._tool_tail = ""
        self._mode = _FramingMode.AFTER_TOOL_BLOCK
        self._validate_trailing(candidate[end_idx + len(_TOOL_BLOCK_END):])

    def _append_tool_fragment(self, fragment: str) -> None:
        if not fragment:
            return
        self._tool_length += len(fragment)
        if self._tool_length > _MAX_TOOL_BLOCK_CHARS:
            raise NimToolProtocolError("oversized native tool block")
        self._tool_parts.append(fragment)

    @staticmethod
    def _validate_trailing(text: str) -> None:
        if text.strip():
            raise NimToolProtocolError("content after native tool block")


# -- parser: XML-like tool block -> NativeToolCall[] --------------------------

def parse_tool_block(
    block: str,
    schemas: dict[str, dict[str, Any]],
    aliases: dict[str, dict[str, str]],
) -> tuple[NativeToolCall, ...]:
    """Parse a complete native tool block into tool calls.

    ``schemas`` maps tool name -> parameters JSON Schema.
    ``aliases`` maps tool name -> {alias: original} for un-aliasing args.
    """
    cursor = 0
    calls: list[NativeToolCall] = []
    while True:
        cursor = _skip_ws(block, cursor)
        if cursor == len(block):
            break
        name, body, cursor = _parse_invoke(block, cursor)
        schema = schemas.get(name, {})
        tool_aliases = aliases.get(name, {})
        arguments = _parse_arguments(body, schema)
        if tool_aliases:
            arguments = _unalias_args(arguments, tool_aliases)
        calls.append(NativeToolCall(index=len(calls), name=name, arguments=arguments))
    if not calls:
        raise NimToolProtocolError("empty native tool block")
    return tuple(calls)


def _unalias_args(args: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in args.items():
        out[aliases.get(k, k)] = v
    return out


def _parse_invoke(text: str, cursor: int) -> tuple[str, str, int]:
    if not text.startswith(_INVOKE_START, cursor):
        raise NimToolProtocolError("malformed invoke tag")
    tag_end = text.find(">", cursor + len(_INVOKE_START))
    if tag_end < 0:
        raise NimToolProtocolError("incomplete invoke tag")
    attrs = text[cursor + len(_INVOKE_START):tag_end]
    name = _parse_invoke_name(attrs)
    body_start = tag_end + 1
    body_end = text.find(_INVOKE_END, body_start)
    if body_end < 0:
        raise NimToolProtocolError("incomplete invoke block")
    return name, text[body_start:body_end], body_end + len(_INVOKE_END)


def _parse_invoke_name(attrs: str) -> str:
    value = attrs.strip()
    if not value.startswith("name"):
        raise NimToolProtocolError("invoke without name attribute")
    rest = value[len("name"):].lstrip()
    if not rest.startswith("="):
        raise NimToolProtocolError("malformed name attribute")
    rest = rest[1:].strip()
    if not rest:
        raise NimToolProtocolError("empty tool name")
    quote = rest[0]
    if quote in {'"', "'"}:
        if len(rest) < 2 or not rest.endswith(quote):
            raise NimToolProtocolError("unterminated tool name")
        name = rest[1:-1]
    else:
        if any(c.isspace() for c in rest):
            raise NimToolProtocolError("malformed invoke attributes")
        name = rest
    name = name.strip()
    if not name:
        raise NimToolProtocolError("empty tool name")
    return name


def _parse_arguments(body: str, schema: dict[str, Any]) -> dict[str, Any]:
    cursor = 0
    elements: list[_Element] = []
    while True:
        cursor = _skip_ws(body, cursor)
        if cursor == len(body):
            break
        element, cursor = _parse_element(body, cursor, depth=1)
        elements.append(element)
    return _elements_to_object(elements, schema)


def _parse_element(text: str, cursor: int, *, depth: int) -> tuple[_Element, int]:
    if depth > _MAX_ARG_DEPTH:
        raise NimToolProtocolError("excessively nested arguments")
    if (not text.startswith(_ELEMENT_START, cursor)
            or text.startswith(_ELEMENT_END_START, cursor)):
        raise NimToolProtocolError("malformed argument tag")
    name_start = cursor + len(_ELEMENT_START)
    tag_end = text.find(">", name_start)
    if tag_end < 0:
        raise NimToolProtocolError("incomplete argument tag")
    name = text[name_start:tag_end]
    if not name or name.strip() != name or any(c.isspace() for c in name):
        raise NimToolProtocolError("invalid argument name")
    close_tag = f"{_ELEMENT_END_START}{name}>"
    cursor = tag_end + 1
    text_parts: list[str] = []
    children: list[_Element] = []
    while True:
        marker = text.find(_NAMESPACE, cursor)
        if marker < 0:
            raise NimToolProtocolError("incomplete argument")
        text_parts.append(text[cursor:marker])
        if text.startswith(close_tag, marker):
            cursor = marker + len(close_tag)
            break
        if (text.startswith(_ELEMENT_START, marker)
                and not text.startswith(_ELEMENT_END_START, marker)):
            child, cursor = _parse_element(text, marker, depth=depth + 1)
            children.append(child)
            continue
        raise NimToolProtocolError("mismatched argument tags")
    return _Element(name=name, text="".join(text_parts), children=tuple(children)), cursor


def _elements_to_object(elements: list[_Element], schema: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties")
    prop_schemas = props if isinstance(props, dict) else {}
    additional = schema.get("additionalProperties")
    out: dict[str, Any] = {}
    for el in elements:
        ps = prop_schemas.get(el.name)
        if not isinstance(ps, dict):
            ps = additional if isinstance(additional, dict) else {}
        out[el.name] = _element_value(el, ps)
    return out


def _element_value(element: _Element, schema: dict[str, Any]) -> Any:
    declared = _schema_type(schema)
    if element.children:
        if declared == "array":
            item_schema = schema.get("items")
            if not isinstance(item_schema, dict):
                item_schema = {}
            return [_element_value(child, item_schema) for child in element.children]
        result = _elements_to_object(list(element.children), schema)
        if element.text.strip():
            mixed = _MIXED_TEXT_FIELD
            while mixed in result:
                mixed = f"${mixed}"
            result[mixed] = element.text
        return result
    return _coerce_text(element.text, schema)


# -- schema helpers (lightweight -- no jsonschema dependency) ------------------

def _schema_type(schema: dict[str, Any]) -> str | None:
    """Resolve one useful non-null JSON type from a simple or union schema."""
    declared = schema.get("type")
    if isinstance(declared, str):
        return declared
    if isinstance(declared, list):
        non_null = [t for t in declared if isinstance(t, str) and t != "null"]
        if len(non_null) == 1:
            return non_null[0]
    for keyword in ("oneOf", "anyOf"):
        alts = schema.get(keyword)
        if not isinstance(alts, list):
            continue
        types = {
            t for a in alts
            if isinstance(a, dict)
            if (t := _schema_type(a)) not in (None, "null")
        }
        if len(types) == 1:
            return types.pop()
    return None


def _coerce_text(value: str, schema: dict[str, Any]) -> Any:
    """Decode a textual tool argument per its declared JSON type.

    Conversion failures are signalled as ``NimToolProtocolError`` so callers
    that catch protocol errors (``parse_tool_block`` callers) also cover
    malformed argument values.
    """
    try:
        return _coerce_text_inner(value, schema)
    except (ValueError, json.JSONDecodeError) as exc:
        raise NimToolProtocolError(
            f"invalid tool argument value: {value!r}") from exc


def _coerce_text_inner(value: str, schema: dict[str, Any]) -> Any:
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list):
        for ev in enum_vals:
            if str(ev) == value:
                return ev
    if "const" in schema and str(schema["const"]) == value:
        return schema["const"]
    vt = _schema_type(schema)
    stripped = value.strip()
    if vt == "integer":
        return int(stripped)
    if vt == "number":
        num = json.loads(stripped)
        if isinstance(num, (int, float)) and not isinstance(num, bool):
            return num
        raise ValueError
    if vt == "boolean":
        if stripped.lower() == "true":
            return True
        if stripped.lower() == "false":
            return False
        raise ValueError
    if vt == "null":
        if stripped.lower() == "null":
            return None
        raise ValueError
    if vt == "array":
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return parsed
        raise ValueError
    if vt == "object":
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError
    return value


# -- emit: NativeToolCall -> IRStreamDelta[] -----------------------------------

def native_calls_to_deltas(
    calls: tuple[NativeToolCall, ...],
    open_indices: set[int],
    tool_names: dict[int, str],
) -> list[dl.IRStreamDelta]:
    """Convert parsed native tool calls into IRStreamDelta tool-call deltas.

    Emits ToolCallOpen + ToolCallArgsDelta + ToolCallClose for each call,
    mirroring how the OpenAI adapter emits structured tool_calls.
    """
    out: list[dl.IRStreamDelta] = []
    for call in calls:
        idx = call.index
        call_id = f"call_nim_{uuid.uuid4().hex}"
        if idx in open_indices:
            out.append(dl.ToolCallClose(index=idx))
        open_indices.add(idx)
        tool_names[idx] = call.name
        out.append(dl.ToolCallOpen(index=idx, id=call_id, name=call.name))
        args_json = json.dumps(call.arguments, ensure_ascii=False,
                               separators=(",", ":"))
        out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=args_json))
    return out


def tool_schemas_from_body(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract ``{tool_name: parameters_schema}`` from an OpenAI-format body."""
    tools = body.get("tools")
    if not isinstance(tools, list):
        return {}
    schemas: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        params = fn.get("parameters")
        schemas[name] = dict(params) if isinstance(params, dict) else {}
    return schemas


# -- small utils ---------------------------------------------------------------

def _skip_ws(text: str, cursor: int) -> int:
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return cursor


def _partial_marker_suffix_length(text: str, marker: str) -> int:
    """Length of a suffix of ``text`` that is a prefix of ``marker``.

    Used to hold back text that *might* be the start of a marker so it isn't
    emitted as visible text prematurely.
    """
    max_len = min(len(text), len(marker) - 1)
    for length in range(max_len, 0, -1):
        if marker.startswith(text[-length:]):
            return length
    return 0
