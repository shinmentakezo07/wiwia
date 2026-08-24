"""Adapter registry: provider account name -> adapter instance."""

from __future__ import annotations

from wiwi.config import PROVIDER_TYPES
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.base import ProviderAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.openrouter_adapter import OpenRouterAdapter

# Provider types that fall through to the OpenAI adapter (they share the
# Chat Completions wire format). Every other type in PROVIDER_TYPES must have
# an explicit branch in get_adapter.
_OPENAI_WIRE_TYPES = frozenset({"openai", "openai-compatible", "gmicloud"})


def get_adapter(provider_type: str) -> ProviderAdapter:
    if provider_type == "anthropic":
        return AnthropicAdapter()
    if provider_type == "gemini":
        return GeminiAdapter()
    if provider_type == "openrouter":
        return OpenRouterAdapter()
    if provider_type in _OPENAI_WIRE_TYPES:
        return OpenAIAdapter()
    # Should never happen: config validation and the admin API both reject
    # unknown provider types before we get here, but fail loudly if they do.
    raise ValueError(f"unsupported provider type {provider_type!r}")


# Sanity check: every provider type in PROVIDER_TYPES must be handled by
# get_adapter — either via an explicit branch or the OpenAI wire-format
# fallback. Catches drift at import time.
_unhandled = set(PROVIDER_TYPES) - _OPENAI_WIRE_TYPES - {"anthropic", "gemini", "openrouter"}
assert not _unhandled, (
    f"provider types {_unhandled} are in PROVIDER_TYPES but have no adapter "
    f"branch in get_adapter — add one"
)
