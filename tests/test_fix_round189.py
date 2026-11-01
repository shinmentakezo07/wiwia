"""Round 189 — the IR's exported lookups are total, as their contracts say.

Three helpers in ``wiwi/ir/`` document (or are relied on for) "map or None,
never raise", but each raised on hostile input before this round:

* ``builtin_tools.canonical_for`` — a truthy non-``str`` wire type reached the
  reverse-map lookup (``TypeError`` for an unhashable ``list``/``dict``) and
  then the Anthropic family-prefix loop (``AttributeError`` for an
  ``int``/``float``/``bool`` on ``.startswith``). AUDIT #126 recorded the
  missing helper-level guard; both live callers pre-guard, so it stayed latent.
* ``builtin_tools.wire_type_for`` — the same unhashable lookup, reachable from
  a hand-built IR ``Tool.builtin``.
* ``translation.carry_extras`` — the debug log's ``sorted(extras)`` ran before
  the log call and raised ``TypeError`` on a mapping with mixed key types,
  contradicting the function's documented totality.

Every live caller passes a ``str`` today, so these are latent rather than live
crashes. They are fixed at the helper because that is where the contract lives:
one guard closes the class for every present and future caller, which is the
same reasoning AUDIT #351/#387/#388 used for their helpers.
"""

from __future__ import annotations

import pytest

from wiwi.ir import builtin_tools as bt
from wiwi.ir import translation as tr

NON_STRINGS = [5, True, False, 1.5, ["web_search"], {"a": 1}, b"web_search"]


# ---------------------------------------------------------------------------
# canonical_for
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wire_type", NON_STRINGS)
def test_canonical_for_is_total_for_a_non_str_wire_type(wire_type):
    assert bt.canonical_for("anthropic", wire_type) is None


@pytest.mark.parametrize("surface", [5, ["anthropic"], {"a": 1}])
def test_canonical_for_is_total_for_a_non_str_surface(surface):
    assert bt.canonical_for(surface, "web_search_20250305") is None


def test_canonical_for_still_maps_known_spellings():
    assert bt.canonical_for("anthropic", "web_search_20250305") == "web_search"
    assert bt.canonical_for("openai_responses", "web_search") == "web_search"


def test_canonical_for_still_accepts_the_versioned_anthropic_family():
    # Any ``web_search_*`` spelling decodes without a registry change.
    assert bt.canonical_for("anthropic", "web_search_20991231") == "web_search"


def test_canonical_for_still_accepts_decode_aliases():
    assert bt.canonical_for("openai_responses", "web_search_preview") == "web_search"


def test_canonical_for_unknown_string_is_none_not_an_error():
    assert bt.canonical_for("anthropic", "nope") is None
    assert bt.canonical_for("anthropic", "") is None


# ---------------------------------------------------------------------------
# wire_type_for
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("canonical", NON_STRINGS)
def test_wire_type_for_is_total_for_a_non_str_canonical(canonical):
    assert bt.wire_type_for("anthropic", canonical) is None


@pytest.mark.parametrize("surface", [5, ["anthropic"], {"a": 1}])
def test_wire_type_for_is_total_for_a_non_str_surface(surface):
    assert bt.wire_type_for(surface, "web_search") is None


def test_wire_type_for_still_maps_known_names():
    assert bt.wire_type_for("anthropic", "web_search") == "web_search_20250305"
    assert bt.wire_type_for("openai_responses", "web_search") == "web_search"


def test_wire_type_for_surface_without_a_spelling_is_none():
    # ``openai_chat`` has no hosted-tool representation.
    assert bt.wire_type_for("openai_chat", "web_search") is None
    assert bt.wire_type_for("anthropic", "not_a_builtin") is None


# ---------------------------------------------------------------------------
# carry_extras
# ---------------------------------------------------------------------------

def test_carry_extras_is_total_for_mixed_key_types():
    source = {"a": 1, 2: "two", (3, 4): "tuple", 1.5: "float"}
    assert tr.carry_extras(source, frozenset()) == source


def test_carry_extras_mixed_keys_still_honours_the_denylist():
    assert tr.carry_extras({"model": "m", 2: 3}, frozenset({"model"})) == {2: 3}


def test_carry_extras_still_drops_non_mapping_sources():
    for source in (None, [1], "x", 5):
        assert tr.carry_extras(source, frozenset()) == {}
