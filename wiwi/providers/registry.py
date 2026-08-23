"""Adapter registry: provider account name -> adapter instance."""

from __future__ import annotations

from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.base import ProviderAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.openrouter_adapter import OpenRouterAdapter


def get_adapter(provider_type: str) -> ProviderAdapter:
    if provider_type == "anthropic":
        return AnthropicAdapter()
    if provider_type == "gemini":
        return GeminiAdapter()
    if provider_type == "openrouter":
        return OpenRouterAdapter()
    return OpenAIAdapter()  # openai + openai-compatible share the wire format
