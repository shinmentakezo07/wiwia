"""Adapter registry: provider type -> adapter instance.

Adapters *do* accumulate state while decoding a stream (open tool indices,
framers, buffered args, deferred tool opens), so ownership matters:

- ``fresh_adapter`` returns a private instance and is what the request hot
  path uses — the non-streaming call and the stream pump both hold the
  adapter across awaits, so a shared instance would be reset underneath an
  in-flight stream by a concurrent request.
- ``get_adapter`` returns the shared singleton, reset before handing it out.
  It is for callers that use the adapter synchronously and do not hold it
  across an await.
"""

from __future__ import annotations

from wiwi.config import PROVIDER_TYPES
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.bai_adapter import BAIAdapter
from wiwi.providers.base import ProviderAdapter
from wiwi.providers.cline_adapter import ClineAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.nim_adapter import NimAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.opencode_adapter import OpencodeAdapter
from wiwi.providers.openrouter_adapter import OpenRouterAdapter
from wiwi.providers.workbuddy_adapter import WorkBuddyAdapter

# Provider types that fall through to the OpenAI adapter (they share the
# Chat Completions wire format). Every other type in PROVIDER_TYPES must have
# an explicit branch in get_adapter.
_OPENAI_WIRE_TYPES = frozenset({"openai", "openai-compatible", "gmicloud", "bai"})

_SINGLETONS: dict[str, ProviderAdapter] = {
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
    "openrouter": OpenRouterAdapter(),
    "nvidia-nim": NimAdapter(),
    "cline": ClineAdapter(),
    "openai": OpenAIAdapter(),
    "bai": BAIAdapter(),
    "workbuddy": WorkBuddyAdapter(),
    "opencode": OpencodeAdapter(),
}


def get_adapter(provider_type: str) -> ProviderAdapter:
    """Return the shared adapter for *provider_type*, reset to a clean state."""
    adapter = _SINGLETONS.get(provider_type)
    if adapter is None and provider_type in _OPENAI_WIRE_TYPES:
        adapter = _SINGLETONS["openai"]
    if adapter is None:
        # Should never happen: config validation and the admin API both reject
        # unknown provider types before we get here, but fail loudly if they do.
        raise ValueError(f"unsupported provider type {provider_type!r}")
    adapter.reset()
    return adapter


def fresh_adapter(provider_type: str) -> ProviderAdapter:
    """Return a *private* adapter instance for *provider_type*.

    This is the acquisition to use on the request hot path: both the
    non-streaming call and the stream pump hold the adapter across awaits
    while accumulating per-stream decode state (deferred tool opens, name
    fragments, open indices, NIM tool schemas/aliases), so they must own their
    instance exclusively. ``get_adapter``'s shared singleton is reset on every
    acquisition, which would wipe a concurrent in-flight stream's state.
    """
    if provider_type == "anthropic":
        return AnthropicAdapter()
    if provider_type == "gemini":
        return GeminiAdapter()
    if provider_type == "openrouter":
        return OpenRouterAdapter()
    if provider_type == "nvidia-nim":
        return NimAdapter()
    if provider_type == "cline":
        return ClineAdapter()
    if provider_type == "workbuddy":
        return WorkBuddyAdapter()
    if provider_type == "opencode":
        return OpencodeAdapter()
    if provider_type == "bai":
        return BAIAdapter()
    if provider_type in _OPENAI_WIRE_TYPES:
        return OpenAIAdapter()
    raise ValueError(f"unsupported provider type {provider_type!r}")


# Sanity check: every provider type in PROVIDER_TYPES must be handled by
# get_adapter — either via an explicit branch or the OpenAI wire-format
# fallback. Catches drift at import time.
_unhandled = set(PROVIDER_TYPES) - _OPENAI_WIRE_TYPES - {
    "anthropic", "gemini", "openrouter", "nvidia-nim", "cline", "workbuddy",
    "opencode"}
assert not _unhandled, (
    f"provider types {_unhandled} are in PROVIDER_TYPES but have no adapter "
    f"branch in get_adapter — add one"
)
