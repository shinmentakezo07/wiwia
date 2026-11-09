"""Round 202 — IR reasoning accessors and extras-helper totality (AUDIT #591-#593).

Three findings from an executed sweep of ``wiwi/ir/``:

* ``GenParams.effective_reasoning_effort`` returned ``reasoning_effort``/``effort``
  verbatim, so a typed-wrong value reached an adapter's encode path. The NIM
  adapter hashes the result (``_NIM_EFFORT_BUDGETS.get(effort, ...)``) and raised
  ``TypeError`` — a 500 blaming a healthy upstream (#591).
* ``effective_thinking_budget``'s docstring promised a float was preserved, but
  the #389 fix routes the field through ``coerce_int``, which drops a fractional
  value. The behaviour is correct (every consumer wants an int on the wire); the
  prose was false (#592).
* ``carry_extras`` guarded ``source`` but not ``known``, so a non-container
  ``known`` raised ``TypeError`` from ``k not in known`` (#593).

All three are latent: every live codec guards effort at the boundary and both
``carry_extras`` callers pass module-level literal sets. The guards belong in the
shared helpers, which closes the class for every present and future caller — the
rule #351/#387/#562-#564 already established for this package.
"""

from __future__ import annotations

import pytest

from wiwi.config import PROVIDER_TYPES
from wiwi.ir import translation as tr
from wiwi.ir import types as ir
from wiwi.providers.registry import fresh_adapter

# Values a plain dataclass accepts but the ``str | None`` annotation forbids.
# The inbound decoders read upstream JSON, so every one of these is a shape a
# future codec could forward by forgetting its guard.
_NON_STR_EFFORTS = (["high"], {"a": 1}, 5, 1.5, True, (), set())


def _req(**gen_kwargs) -> ir.Request:
    return ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart(text="hi")])],
        gen_params=ir.GenParams(max_tokens=16000, **gen_kwargs),
    )


# ---------------------------------------------------------------------------
# #591 — the effort accessors are total
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["reasoning_effort", "effort"])
@pytest.mark.parametrize("value", _NON_STR_EFFORTS)
def test_effective_reasoning_effort_drops_non_str(field, value):
    """A typed-wrong effort degrades to None, never escapes to an adapter."""
    g = ir.GenParams(**{field: value})
    assert g.effective_reasoning_effort() is None, (
        f"effective_reasoning_effort forwarded {field}={value!r}")


@pytest.mark.parametrize("field", ["reasoning_effort", "effort"])
@pytest.mark.parametrize("value", _NON_STR_EFFORTS)
def test_effective_thinking_budget_drops_non_str_effort(field, value):
    """The sibling accessor must not derive a budget from a junk effort either."""
    g = ir.GenParams(**{field: value})
    assert g.effective_thinking_budget() is None


@pytest.mark.parametrize("field", ["reasoning_effort", "effort"])
@pytest.mark.parametrize("value", _NON_STR_EFFORTS)
@pytest.mark.parametrize("provider", PROVIDER_TYPES)
def test_non_str_effort_never_raises_in_any_adapter(field, value, provider):
    """The real encode path of all 13 provider types survives a junk effort.

    This is the assertion that fails before the fix: ``nvidia-nim`` raised
    ``TypeError: unhashable type: 'list'`` from ``_NIM_EFFORT_BUDGETS.get``.
    """
    adapter = fresh_adapter(provider)
    adapter.encode_request(_req(**{field: value}), "m", {})


def test_well_typed_effort_still_wins():
    """The guard must not change any documented precedence."""
    assert ir.GenParams(reasoning_effort="high").effective_reasoning_effort() == "high"
    assert ir.GenParams(effort="low").effective_reasoning_effort() == "low"
    # An explicit off-switch still outranks a separate effort knob (round 178).
    g = ir.GenParams(thinking_type="disabled", effort="high")
    assert g.effective_reasoning_effort() == "none"
    # A junk effort must not mask a usable budget behind it.
    g = ir.GenParams(reasoning_effort=["high"], thinking_budget=4096)
    assert g.effective_reasoning_effort() == "medium"
    assert g.effective_thinking_budget() == 4096


def test_empty_effort_string_is_still_absent():
    """An empty string stays falsy, so the next knob resolves as before."""
    g = ir.GenParams(reasoning_effort="", effort="high")
    assert g.effective_reasoning_effort() == "high"


# ---------------------------------------------------------------------------
# #592 — the fractional-budget contract is documented honestly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [2048.5, 1024.9, 0.5])
def test_fractional_budget_is_not_forwarded(value):
    """A fractional budget resolves to None, not to a fractional wire value.

    Pins the behaviour the #389 fix chose (``coerce_int`` requires
    ``.is_integer()``) so the corrected docstring and the code cannot drift
    apart again. An integral float still passes — round 124 pins that.
    """
    assert ir.GenParams(thinking_budget=value).effective_thinking_budget() is None


def test_integral_float_budget_still_passes():
    """Guards the round-124 contract the docstring correction must not break."""
    assert ir.GenParams(thinking_budget=8000.0).effective_thinking_budget() == 8000


# ---------------------------------------------------------------------------
# #593 — carry_extras guards both arguments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("known", [None, 5, 1.5, True, object()])
def test_carry_extras_tolerates_unusable_known(known):
    """A non-container ``known`` carries everything rather than raising.

    Mirrors the ``source`` guard: the helper's contract is total, and both live
    callers pass a literal set today, so this closes the class for the next one.
    """
    assert tr.carry_extras({"a": 1, "b": 2}, known) == {"a": 1, "b": 2}


def test_carry_extras_denylist_still_applies():
    """The guard must not weaken the real denylist path."""
    known = frozenset({"model"})
    assert tr.carry_extras({"model": "m", "top_k": 3}, known) == {"top_k": 3}
    assert tr.carry_extras({"model": "m", 2: 3}, known) == {2: 3}
    # A list/set `known` is a usable container and keeps working.
    assert tr.carry_extras({"a": 1, "b": 2}, ["a"]) == {"b": 2}
