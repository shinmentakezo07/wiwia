"""Registry of provider-hosted builtin tools (docs/CORE.md §4).

A builtin tool is executed by the provider itself (Anthropic ``web_search_*``,
OpenAI Responses ``web_search``, Gemini ``google_search``); the model never
sees a function schema. The canonical name is what rides the IR — per-surface
wire types are rendered at the codec/adapter boundaries via this registry.

Layering: this module holds canonical facts only (name ↔ wire-type maps,
config-subset keys). Per-dialect parsing/rendering stays in ``wire/`` and
``providers/``.
"""

from __future__ import annotations

# canonical name -> per-surface wire tool "type" string. None means the
# surface has no representation for that builtin (decoders never produce it,
# encoders drop it with a warning).
BUILTIN_TOOL_TYPES: dict[str, dict[str, str | None]] = {
    "web_search": {
        "anthropic": "web_search_20250305",
        "openai_responses": "web_search",
        "openai_chat": None,
        "openrouter": "openrouter:web_search",
        "gemini": "google_search",
    },
}

# Provider-common config subset carried in Tool.builtin_config. Surfaces
# render the keys they understand and drop the rest (with a warning when a
# meaningful setting is lost, e.g. max_uses on surfaces without a count knob).
BUILTIN_CONFIG_KEYS = (
    "max_uses",
    "allowed_domains",
    "blocked_domains",
    "user_location",
    "search_context_size",
)

# Raw wire type kept on decode for builtins this registry cannot map (e.g.
# Anthropic code_execution_20250522): they stay builtin-shaped in the IR so
# no surface mangles them into function tools, and providers that cannot
# host them drop them with a warning.
WIRE_TYPE_KEY = "_wire_type"

# Surfaces that can carry a builtin tool definition. openai_chat has no
# hosted-tool representation today (kept in the set so helpers stay uniform).
SURFACES = ("anthropic", "openai_responses", "openai_chat", "openrouter", "gemini")


def _build_reverse(surface: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for canonical, row in BUILTIN_TOOL_TYPES.items():
        wt = row.get(surface)
        if isinstance(wt, str):
            out[wt] = canonical
    return out


# Reverse maps per surface, built once at import.
_REVERSE: dict[str, dict[str, str]] = {
    surface: _build_reverse(surface) for surface in SURFACES
}
# Decode-only aliases: legacy/versioned wire types that map onto a canonical
# builtin. Encode always emits the current wire_type_for() spelling.
_ALIASES: dict[str, dict[str, str]] = {
    "openai_responses": {
        "web_search_preview": "web_search",       # legacy Responses spelling
        "web_search_2025_08_26": "web_search",    # versioned spelling
    },
}


def canonical_for(surface: str, wire_type: str) -> str | None:
    """Map a surface's wire tool type to the canonical builtin name.

    Accepts the Anthropic versioned family (any ``web_search_*`` string) so
    future tool versions decode without a registry change. Returns None for
    wire types that are not a known builtin on that surface.
    """
    if not wire_type:
        return None
    hit = _REVERSE.get(surface, {}).get(wire_type)
    if hit is None:
        hit = _ALIASES.get(surface, {}).get(wire_type)
    if hit is not None:
        return hit
    if surface == "anthropic":
        for canonical, row in BUILTIN_TOOL_TYPES.items():
            family = row.get("anthropic")
            if isinstance(family, str):
                prefix = family.rsplit("_", 1)[0] + "_"
                if wire_type.startswith(prefix):
                    return canonical
    return None


def wire_type_for(surface: str, canonical: str) -> str | None:
    """Map a canonical builtin name to the surface's wire tool type (or None)."""
    row = BUILTIN_TOOL_TYPES.get(canonical)
    if row is None:
        return None
    wt = row.get(surface)
    return wt if isinstance(wt, str) else None


def is_builtin_name(name: str) -> bool:
    """True when a tool-call name (ToolUsePart.name) names a canonical builtin."""
    return name in BUILTIN_TOOL_TYPES
