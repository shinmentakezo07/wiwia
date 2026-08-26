"""NVIDIA NIM tool schema sanitization.

NIM (vLLM-backed) rejects two classes of JSON Schema constructs that the OpenAI
Chat Completions wire format accepts:

1. **Boolean subschemas** — JSON Schema 2020-12 allows ``true``/``false`` as
   subschema values (e.g. ``"additionalProperties": true``).  NIM's schema
   validator rejects these.  We strip them.

2. **Unsafe parameter names** — a tool parameter named ``"type"`` collides with
   the JSON Schema ``"type"`` keyword inside the vLLM tool-call parser.  We
   alias such parameters to ``_nim_arg_<name>`` and record the mapping so the
   adapter can un-alias tool-call arguments on the way back.

Ported from the reference implementation in free-claude-code's
``providers/nvidia_nim/tool_schema.py``.
"""

from __future__ import annotations

from typing import Any

# JSON Schema keys whose *values* are schemas ( recurse into them ).
_SCHEMA_VALUE_KEYS = frozenset(
    {"additionalProperties", "not", "contains", "propertyNames", "if", "then", "else"}
)
# JSON Schema keys whose *values* are lists of schemas.
_SCHEMA_LIST_KEYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
# JSON Schema keys whose *values* are maps of schemas.
_SCHEMA_MAP_KEYS = frozenset(
    {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
)

# Parameter names that NIM/vLLM rejects ( collide with JSON Schema keywords ).
_UNSAFE_PARAM_NAMES = frozenset({"type"})

_ALIAS_PREFIX = "_nim_arg_"


def sanitize_nim_tool_schemas(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize tool definitions for NIM: strip boolean subschemas and alias unsafe params.

    Returns a new list of tool dicts with sanitized parameter schemas.
    """
    sanitized: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            sanitized.append(tool)
            continue
        out = dict(tool)
        fn = tool.get("function")
        if isinstance(fn, dict):
            out_fn = dict(fn)
            params = fn.get("parameters")
            if isinstance(params, dict):
                _, clean = _sanitize_schema_node(params)
                clean = _alias_unsafe_params(clean)
                out_fn["parameters"] = clean
            out["function"] = out_fn
        sanitized.append(out)
    return sanitized


def unalias_nim_tool_args(
    args: dict[str, Any],
    aliases: dict[str, str],
) -> dict[str, Any]:
    """Reverse parameter aliasing: map ``_nim_arg_type`` back to ``type``.

    ``aliases`` maps alias → original name.  Recurses into nested dicts and
    lists so aliased keys at any depth are restored.
    """
    if not aliases:
        return args
    out: dict[str, Any] = {}
    for k, v in args.items():
        out[aliases.get(k, k)] = _unalias_value(v, aliases)
    return out


def _unalias_value(value: Any, aliases: dict[str, str]) -> Any:
    """Recursively un-alias nested dict/list values."""
    if isinstance(value, dict):
        return unalias_nim_tool_args(value, aliases)
    if isinstance(value, list):
        return [_unalias_value(v, aliases) for v in value]
    return value


# -- boolean subschema removal -------------------------------------------------

def _sanitize_schema_node(value: Any) -> tuple[bool, Any]:
    """Remove boolean JSON Schema subschemas that NIM rejects.

    Returns ``(keep, sanitized)``: ``keep=False`` means the node was a boolean
    subschema and should be dropped from its parent.
    """
    if isinstance(value, bool):
        return False, None
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _SCHEMA_VALUE_KEYS:
                keep, clean = _sanitize_schema_node(item)
                if keep:
                    out[key] = clean
            elif key in _SCHEMA_LIST_KEYS and isinstance(item, list):
                items = []
                for sub in item:
                    keep, clean = _sanitize_schema_node(sub)
                    if keep:
                        items.append(clean)
                if items:
                    out[key] = items
            elif key in _SCHEMA_MAP_KEYS and isinstance(item, dict):
                m: dict[str, Any] = {}
                for mk, mv in item.items():
                    keep, clean = _sanitize_schema_node(mv)
                    if keep:
                        m[mk] = clean
                out[key] = m
            else:
                out[key] = item
        return True, out
    if isinstance(value, list):
        items = []
        for item in value:
            keep, clean = _sanitize_schema_node(item)
            if keep:
                items.append(clean)
        return True, items
    return True, value


# -- unsafe parameter aliasing -------------------------------------------------

def _alias_unsafe_params(parameters: dict[str, Any]) -> dict[str, Any]:
    """Alias tool parameters whose names collide with JSON Schema keywords.

    Walks the ``properties`` map and ``required`` list, replacing unsafe names
    with ``_nim_arg_<name>``.  Returns the modified parameters dict.
    """
    aliases: dict[str, str] = {}  # alias → original
    return _alias_in_node(parameters, set(), aliases, {})


def _alias_in_node(
    value: Any,
    reserved: set[str],
    alias_to_orig: dict[str, str],
    orig_to_alias: dict[str, str],
) -> Any:
    if isinstance(value, list):
        return [_alias_in_node(v, reserved, alias_to_orig, orig_to_alias) for v in value]
    if not isinstance(value, dict):
        return value

    local_aliases: dict[str, str] = {}
    out: dict[str, Any] = {}
    props = value.get("properties")
    if isinstance(props, dict):
        aliased_props: dict[str, Any] = {}
        for name, schema in props.items():
            aliased = _alias_in_node(schema, reserved, alias_to_orig, orig_to_alias)
            if isinstance(name, str) and name in _UNSAFE_PARAM_NAMES:
                alias = orig_to_alias.get(name)
                if alias is None:
                    alias = _make_alias(name, reserved)
                    alias_to_orig[alias] = name
                    orig_to_alias[name] = alias
                local_aliases[name] = alias
                aliased_props[alias] = aliased
            else:
                aliased_props[name] = aliased
        out["properties"] = aliased_props

    for key, item in value.items():
        if key == "properties":
            continue
        if key == "required" and isinstance(item, list):
            out[key] = [local_aliases.get(r, r) if isinstance(r, str) else r for r in item]
            continue
        out[key] = _alias_in_node(item, reserved, alias_to_orig, orig_to_alias)
    return out


def _make_alias(name: str, reserved: set[str]) -> str:
    candidate = f"{_ALIAS_PREFIX}{name}"
    alias = candidate
    suffix = 2
    while alias in reserved:
        alias = f"{candidate}_{suffix}"
        suffix += 1
    reserved.add(alias)
    return alias


def collect_nim_tool_aliases(tools: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Build a ``{tool_name: {alias: original}}`` map from sanitized tool defs.

    Scans the sanitized parameter schemas for aliased property names
    ( prefixed with ``_nim_arg_`` ) at all nesting levels and reverses them.
    """
    result: dict[str, dict[str, str]] = {}
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
        if not isinstance(params, dict):
            continue
        aliases: dict[str, str] = {}
        _collect_aliases_in_node(params, aliases)
        if aliases:
            result[name] = aliases
    return result


def _collect_aliases_in_node(value: Any, aliases: dict[str, str]) -> None:
    """Recursively collect ``_nim_arg_``-prefixed property names → originals."""
    if isinstance(value, list):
        for v in value:
            _collect_aliases_in_node(v, aliases)
        return
    if not isinstance(value, dict):
        return
    props = value.get("properties")
    if isinstance(props, dict):
        for pname in props:
            if isinstance(pname, str) and pname.startswith(_ALIAS_PREFIX):
                aliases[pname] = pname[len(_ALIAS_PREFIX):]
        for schema in props.values():
            _collect_aliases_in_node(schema, aliases)
    for key in _SCHEMA_VALUE_KEYS | _SCHEMA_LIST_KEYS | _SCHEMA_MAP_KEYS:
        if key in value:
            _collect_aliases_in_node(value[key], aliases)
