"""Cross-surface provider registration consistency tests.

Ensures that adding a provider type to ``wiwi.config.PROVIDER_TYPES`` cannot
silently leave the adapter registry, the built-in catalog, the default-URL
helper, or the admin API validation out of sync.  Each surface that needs to
know about provider types must derive from — or be checked against — that
single constant.
"""

import pytest

from wiwi.config import PROVIDER_TYPES
from wiwi.providers.registry import get_adapter
from wiwi.router.router import BUILTIN_PROVIDER_TYPES, _default_base_url


def test_every_provider_type_has_adapter():
    """Every type in PROVIDER_TYPES must resolve to a concrete adapter
    with a matching provider_type attribute."""
    for ptype in PROVIDER_TYPES:
        adapter = get_adapter(ptype)
        assert hasattr(adapter, "provider_type"), (
            f"get_adapter({ptype!r}) returned {type(adapter)} with no provider_type"
        )


def test_every_provider_type_has_catalog_card():
    """Every type in PROVIDER_TYPES must have a BUILTIN_PROVIDER_TYPES entry."""
    catalog_types = {p["provider_type"] for p in BUILTIN_PROVIDER_TYPES}
    assert catalog_types == set(PROVIDER_TYPES), (
        f"catalog types {catalog_types} != PROVIDER_TYPES {set(PROVIDER_TYPES)}"
    )


def test_every_catalog_card_has_default_base_url():
    """Every catalog entry must have a default_base_url key (may be empty)."""
    for card in BUILTIN_PROVIDER_TYPES:
        assert "default_base_url" in card, (
            f"catalog card for {card['provider_type']!r} missing 'default_base_url'"
        )


def test_default_base_url_matches_catalog():
    """_default_base_url must return the same value as the catalog card."""
    for card in BUILTIN_PROVIDER_TYPES:
        expected = card["default_base_url"]
        actual = _default_base_url(card["provider_type"])
        assert actual == expected, (
            f"_default_base_url({card['provider_type']!r}) = {actual!r}, "
            f"catalog says {expected!r}"
        )


def test_unknown_provider_type_returns_empty_base_url():
    assert _default_base_url("nonexistent") == ""


def test_unknown_provider_type_raises_in_registry():
    """get_adapter must raise for truly unknown types, not silently fall through."""
    with pytest.raises(ValueError, match="unsupported provider type"):
        get_adapter("nonexistent")
