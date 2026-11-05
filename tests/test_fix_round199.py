"""Round 199 — reject non-finite numeric parameters at the IR boundary."""

import pytest

from wiwi.ir import translation as tr
from wiwi.ir import types as ir


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_sampling_values_are_dropped(value):
    assert tr.coerce_float(value) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_thinking_budget_does_not_enable_reasoning(value):
    assert ir.thinking_budget_to_effort(value) == "none"
    assert ir.GenParams(thinking_budget=value).effective_reasoning_effort() == "none"
