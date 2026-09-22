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
# ``items`` belongs here and was missing: it is the single most common schema
# keyword in real agent tool catalogs (``Edit``'s ``edits: [{old_string,
# new_string, type}]``). ``_alias_in_node`` recurses over *every* key, so it
# aliased unsafe params inside ``items`` while ``_collect_aliases_in_node`` and
# ``_sanitize_schema_node`` — which walk only these sets — never descended, so
# the alias was never reversed and a boolean subschema survived: both defects
# the module exists to prevent, and the result was self-inconsistent (AUDIT
# #246). ``items`` may hold a schema or a list of schemas; both walkers handle
# either shape.
_SCHEMA_VALUE_KEYS = frozenset(
    {"additionalProperties", "not", "contains", "propertyNames", "if", "then",
     "else", "items"}
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


def _declared_prop_names(value: Any, out: set[str]) -> None:
    """Collect every ``properties`` key name in a schema, at any depth.

    Used to tell a name the caller *literally declared* (``_nim_arg_foo``)
    apart from one the sanitizer minted (AUDIT #251).
    """
    if isinstance(value, list):
        for v in value:
            _declared_prop_names(v, out)
        return
    if not isinstance(value, dict):
        return
    props = value.get("properties")
    if isinstance(props, dict):
        out.update(k for k in props if isinstance(k, str))
        for schema in props.values():
            _declared_prop_names(schema, out)
    for key in _SCHEMA_VALUE_KEYS | _SCHEMA_LIST_KEYS | _SCHEMA_MAP_KEYS:
        if key in value:
            _declared_prop_names(value[key], out)


def declared_prop_names(tools: list[dict[str, Any]]) -> dict[str, set[str]]:
    """``{tool_name: {property names the caller declared}}`` for *tools*.

    Computed from the ORIGINAL (pre-sanitize) definitions and handed to
    :func:`collect_nim_tool_aliases` so a literal ``_nim_arg_*`` parameter is
    not mistaken for a minted alias (AUDIT #251).
    """
    result: dict[str, set[str]] = {}
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
        names: set[str] = set()
        _declared_prop_names(params, names)
        result[name] = names
    return result


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
    return _alias_in_node(parameters)


def _renames_for_node(props: dict[str, Any]) -> dict[str, str]:
    """``{original: alias}`` for every property of one ``properties`` map.

    A property must move when its name is unsafe (it collides with a JSON
    Schema keyword) or when another property's alias already occupies its name
    — a tool declaring both a ``type`` parameter and a literal
    ``_nim_arg_type`` one. Previously the alias of the first was written
    straight over the second, so the model was told there is ONE parameter
    instead of two and ``required`` named it twice (AUDIT #201).

    Every alias is exactly ``_ALIAS_PREFIX`` + the original name, because that
    is the only shape ``collect_nim_tool_aliases`` can reverse (it strips one
    prefix). A suffixed fallback like ``_nim_arg_type_2`` would strip back to
    ``type_2`` — a name the tool never declared, silently mis-keying the
    model's arguments — so a collision is resolved by displacing the *literal*
    one prefix further instead. Displacing can collide again (``type`` +
    ``_nim_arg_type`` + ``_nim_arg__nim_arg_type``), so propagate to a fixed
    point; each step moves strictly outward and the map is finite, so it
    terminates.
    """
    moved = {name for name in props
             if isinstance(name, str) and name in _UNSAFE_PARAM_NAMES}
    while True:
        # Any property whose name is now taken by an alias must move too.
        occupied = {f"{_ALIAS_PREFIX}{name}" for name in moved} & set(props)
        fresh = occupied - moved
        if not fresh:
            break
        moved |= fresh
    return {name: f"{_ALIAS_PREFIX}{name}" for name in moved}


def _alias_in_node(value: Any) -> Any:
    if isinstance(value, list):
        return [_alias_in_node(v) for v in value]
    if not isinstance(value, dict):
        return value

    local_aliases: dict[str, str] = {}
    out: dict[str, Any] = {}
    props = value.get("properties")
    if isinstance(props, dict):
        renamed = _renames_for_node(props)
        aliased_props: dict[str, Any] = {}
        for name, schema in props.items():
            aliased = _alias_in_node(schema)
            final = renamed.get(name, name) if isinstance(name, str) else name
            if isinstance(name, str) and final != name:
                local_aliases[name] = final
            aliased_props[final] = aliased
        out["properties"] = aliased_props

    for key, item in value.items():
        if key == "properties":
            continue
        if key == "required" and isinstance(item, list):
            out[key] = [local_aliases.get(r, r) if isinstance(r, str) else r for r in item]
            continue
        out[key] = _alias_in_node(item)
    return out


def collect_nim_tool_aliases(
    tools: list[dict[str, Any]],
    declared: dict[str, set[str]] | None = None,
) -> dict[str, dict[str, str]]:
    """Build a ``{tool_name: {alias: original}}`` map from sanitized tool defs.

    Scans the sanitized parameter schemas for aliased property names
    (prefixed with ``_nim_arg_``) at all nesting levels and reverses them.

    *declared* maps a tool name to the property names the caller literally
    declared before sanitizing (see :func:`sanitize_nim_tool_schemas`); names
    in that set are never treated as minted aliases (AUDIT #251). When omitted,
    the sanitized schemas alone are used — correct for tools whose params do not
    already start with the alias prefix.
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
        declared_names = (declared or {}).get(name, set())
        _collect_aliases_in_node(params, aliases, declared_names)
        if aliases:
            result[name] = aliases
    return result


def _collect_aliases_in_node(
    value: Any,
    aliases: dict[str, str],
    declared: set[str],
) -> None:
    """Recursively collect ``alias -> original`` from sanitized ``properties``.

    Collection is by ``_nim_arg_`` prefix, but a name the caller *literally
    declared* as ``_nim_arg_foo`` is excluded via *declared* — otherwise it was
    renamed back to ``foo`` (destroying the declared name) or silently merged
    with a sibling ``foo`` when both were present (AUDIT #251). *declared*
    holds every property name seen in the ORIGINAL (pre-sanitize) schema, which
    only the sanitizer can supply: after aliasing, a minted ``_nim_arg_type``
    and a literal ``_nim_arg_type`` are indistinguishable by shape alone.
    """
    if isinstance(value, list):
        for v in value:
            _collect_aliases_in_node(v, aliases, declared)
        return
    if not isinstance(value, dict):
        return
    props = value.get("properties")
    if isinstance(props, dict):
        for pname in props:
            if not isinstance(pname, str) or pname in declared:
                continue
            if pname.startswith(_ALIAS_PREFIX):
                aliases[pname] = pname[len(_ALIAS_PREFIX):]
        for schema in props.values():
            _collect_aliases_in_node(schema, aliases, declared)
    for key in _SCHEMA_VALUE_KEYS | _SCHEMA_LIST_KEYS | _SCHEMA_MAP_KEYS:
        if key in value:
            _collect_aliases_in_node(value[key], aliases, declared)
