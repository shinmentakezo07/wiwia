"""Hermes (Nous Portal) adapter + registry + catalog integration.

Hermes is OpenAI Chat Completions-compatible, so its adapter subclasses
``OpenAIAdapter`` and only opts out of the Realtime WebSocket surface (Portal
does not expose it). These tests pin that contract: the provider type resolves
through both registry accessors, encodes on the Chat Completions wire, carries
a catalog card that matches ``PROVIDER_TYPES``, and — the whole point of the
subclass — reports no realtime URL while still building the chat endpoint.
"""

from __future__ import annotations

from wiwi.config import PROVIDER_TYPES
from wiwi.ir import types as ir
from wiwi.providers import registry
from wiwi.providers.hermes_adapter import HermesAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import BUILTIN_PROVIDER_TYPES

PORTAL = "https://inference-api.nousresearch.com/v1"

# -- registry wiring -----------------------------------------------------------

def test_hermes_is_a_registered_provider_type():
    assert "hermes" in PROVIDER_TYPES

def test_get_adapter_returns_the_shared_hermes_singleton():
    a = registry.get_adapter("hermes")
    assert isinstance(a, HermesAdapter)
    # The shared singleton is a single reused instance.
    assert registry.get_adapter("hermes") is a

def test_fresh_adapter_returns_a_private_hermes_instance():
    a = registry.fresh_adapter("hermes")
    assert isinstance(a, HermesAdapter)
    # Private per-acquisition: two hot-path callers never share state.
    assert registry.fresh_adapter("hermes") is not a

def test_hermes_provider_type_tag():
    assert HermesAdapter().provider_type == "hermes"

def test_hermes_adapter_is_an_openai_wire_adapter():
    assert issubclass(HermesAdapter, OpenAIAdapter)

# -- catalog -------------------------------------------------------------------

def test_catalog_carries_a_hermes_card():
    card = next(p for p in BUILTIN_PROVIDER_TYPES if p["provider_type"] == "hermes")
    assert card["default_base_url"] == PORTAL
    assert card["latest_models"] == ["Hermes-4-405B", "Hermes-4-70B", "Hermes-4.3-36B"]

# -- encode + endpoint contract ------------------------------------------------

def _req() -> ir.Request:
    return ir.Request(
        model="Hermes-4-405B",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
    )

def test_build_url_is_chat_completions():
    adapter = HermesAdapter()
    assert adapter.build_url(PORTAL, "Hermes-4-405B", stream=True) == (
        f"{PORTAL}/chat/completions"
    )

def test_realtime_url_is_none_portal_exposes_no_ws_surface():
    # The whole reason the subclass exists: OpenAIAdapter would return a
    # wss://…/realtime endpoint here, which Portal 404s.
    adapter = HermesAdapter()
    assert adapter.realtime_url(PORTAL) is None
    # The parent it overrides *would* have produced a realtime URL — proving
    # the override is load-bearing and not merely inheriting a no-op.
    assert OpenAIAdapter().realtime_url(PORTAL) == "wss://inference-api.nousresearch.com/v1/realtime"

def test_encode_request_is_openai_shaped():
    out = HermesAdapter().encode_request(_req(), "Hermes-4-405B", {})
    assert out["model"] == "Hermes-4-405B"
    assert out["messages"] == [{"role": "user", "content": "hi"}]
