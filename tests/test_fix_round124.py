"""Round 124 — two non-total helpers on the ``wiwi/ir/`` boundary (AUDIT #387, #388).

Both findings are the same shape: a helper on the IR boundary is typed for one input
but has no runtime guard, so a typed-wrong value raises ``TypeError`` out of the
helper and into a path that cannot recover from it. AUDIT #351 fixed the forward
direction of the effort↔budget pair with the reasoning "the type is not enforced at
the IR boundary … so the guard belongs here"; these are the two siblings that never
got the same guard.

#387 — ``is_builtin_name`` (``ir/builtin_tools.py:136``)
    ``return name in BUILTIN_TOOL_TYPES`` hashes the key, so a ``list``/``dict``
    tool-call name raises ``TypeError``. Reachable through the unguarded
    ``name_fragment = fn.get("name", "")`` at ``openai_adapter.py:612`` /
    ``nim_adapter.py:430`` (AUDIT #385): the deferred ``ToolCallOpen`` carries the
    raw name, the gateway folds it into ``ir.ToolUsePart`` — whose ``__post_init__``
    coerces the *id* but not the *name* — and ``anthropic_adapter.py:373`` then
    either emits ``{"name": ["web_search"]}`` upstream (a 400) or crashes on the
    ``is_builtin_name`` fallback when ``block_type`` is falsy. The sibling
    ``canonical_for`` in the same module already guards its input; this is the one
    unguarded read.

#388 — ``thinking_budget_to_effort`` (``ir/types.py:328``)
    ``if budget <= 0`` raises on ``str``/``None``/``list``. Its inverse,
    ``effort_to_thinking_budget`` two hundred lines above, was made total by #351
    and says so in its docstring, so the class #351 closed was only half closed.
    No current wire decoder reaches it (``anthropic_messages.py:415-427`` coerces
    ``budget_tokens`` to ``int``, and the other surfaces never set the field), so
    it stays medium — but ``effective_reasoning_effort`` runs inside
    ``encode_request`` on six adapters, and any future caller that sets the field
    without coercion gets a 500 instead of a sane default.

Both fixes keep the existing behaviour for every well-typed input; the tests below
pin the boundaries that were already correct alongside the new tolerance.
"""

from __future__ import annotations

import json

from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.providers import registry
from wiwi.streaming import deltas as dl

# Every typed-wrong name an upstream could plausibly send. ``None`` and ``""`` are
# NOT in this list: those are #385's shape (a JSON null name) and degrade to "" on
# their own. What crashes is a container.
_NON_STR_NAMES = [["web_search"], {"a": 1}]

# Non-numeric budgets. ``8000.0`` is deliberately absent — a float IS accepted by
# design (the ladder only compares), so the guard must not reject it.
_NON_NUMERIC_BUDGETS = ["8000", "abc", None, [8000], {"a": 1}, True]

# ---------------------------------------------------------------------------
# #387 — is_builtin_name must be total
# ---------------------------------------------------------------------------

def test_is_builtin_name_rejects_a_container_name_instead_of_raising():
    """A list/dict tool-call name must read as "not a builtin", not raise.

    Pre-fix ``name in BUILTIN_TOOL_TYPES`` hashed the key and raised
    ``TypeError: unhashable type``. The call site
    (``anthropic_adapter.py:373``) is inside ``encode_request``, so the raise
    became a 500 with a healthy upstream blamed for it.
    """
    for name in _NON_STR_NAMES:
        try:
            result = bt.is_builtin_name(name)
        except TypeError as exc:  # pragma: no cover - the pre-fix behaviour
            raise AssertionError(
                f"is_builtin_name({name!r}) raised instead of returning a bool"
            ) from exc
        assert result is False, (
            f"is_builtin_name({name!r}) returned {result!r}, expected False")

def test_is_builtin_name_still_recognises_every_canonical():
    """The guard must not break the lookup it guards."""
    for canonical in bt.BUILTIN_TOOL_TYPES:
        assert bt.is_builtin_name(canonical) is True, canonical
    # A caller may legitimately define a FUNCTION tool called ``web_search``; the
    # name-only predicate answering True for it is by design (the comment at
    # ir/types.py ToolUsePart.builtin explains why builtin-ness must not be
    # inferred from the name alone at the *suppression* layer).
    assert bt.is_builtin_name("web_search") is True
    assert bt.is_builtin_name("get_weather") is False
    assert bt.is_builtin_name("") is False

def test_container_tool_name_survives_the_anthropic_replay_path():
    """End to end: a streamed container name must not 500 the replay request.

    Drives the real chain the finding describes — an OpenAI-wire upstream chunk
    whose ``function.name`` is a list, through the real adapter's deferred-open
    path, into the gateway's ``ToolUsePart`` construction, into the Anthropic
    adapter's ``encode_request`` where ``is_builtin_name`` is consulted.
    """
    for name in _NON_STR_NAMES:
        adapter = registry.fresh_adapter("openai")
        chunk = {"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "id": "c1", "type": "function",
            "function": {"name": name}}]}}]}
        # No ``arguments`` on the chunk: the Open is deferred and flushed at
        # [DONE], which is the path that carries the raw name through.
        out = adapter.decode_stream_event("message", json.dumps(chunk))
        out += adapter.decode_stream_event("message", "[DONE]")
        opens = [d for d in out if isinstance(d, dl.ToolCallOpen)]
        assert opens, f"expected a deferred ToolCallOpen to flush, got {out}"

        # The gateway's fold (core/gateway.py:812, streaming/resume.py:170):
        part = ir.ToolUsePart(id=opens[0].id, name=opens[0].name)
        req = ir.Request(model="claude-x",
                         messages=[ir.Message(role="user", parts=[part])])
        # block_type=None exercises the ``is_builtin_name`` fallback at
        # anthropic_adapter.py:373, which is the read that raised pre-fix.
        part.block_type = None
        body = registry.fresh_adapter("anthropic").encode_request(
            req, "claude-x", {})
        assert body, "anthropic encode_request returned an empty body"

# ---------------------------------------------------------------------------
# #388 — thinking_budget_to_effort must be total
# ---------------------------------------------------------------------------

def test_thinking_budget_to_effort_rejects_a_non_numeric_budget():
    """A non-numeric budget must map to "none" (thinking off), not raise.

    "none" is the safe degradation: it matches ``effort_to_thinking_budget``'s
    documented rule that an unusable value must leave thinking OFF rather than
    enable it at an arbitrary budget.
    """
    for budget in _NON_NUMERIC_BUDGETS:
        try:
            result = ir.thinking_budget_to_effort(budget)
        except TypeError as exc:  # pragma: no cover - the pre-fix behaviour
            raise AssertionError(
                f"thinking_budget_to_effort({budget!r}) raised instead of "
                "returning an effort"
            ) from exc
        assert result == "none", (
            f"thinking_budget_to_effort({budget!r}) -> {result!r}, expected 'none'")

def test_thinking_budget_to_effort_keeps_its_well_typed_boundaries():
    """The guard must not move a single existing boundary.

    These are the boundaries ``test_providers.py`` and ``test_fix_round31.py``
    already pin, including the two documented quirks: 0 → "none", and floats are
    accepted because the ladder only compares.
    """
    assert ir.thinking_budget_to_effort(0) == "none"
    assert ir.thinking_budget_to_effort(-1) == "none"
    assert ir.thinking_budget_to_effort(1) == "low"
    assert ir.thinking_budget_to_effort(1024) == "low"
    assert ir.thinking_budget_to_effort(2048) == "low"
    assert ir.thinking_budget_to_effort(2049) == "medium"
    assert ir.thinking_budget_to_effort(5000) == "medium"
    assert ir.thinking_budget_to_effort(16000) == "medium"
    assert ir.thinking_budget_to_effort(32000) == "high"
    assert ir.thinking_budget_to_effort(48000) == "high"
    assert ir.thinking_budget_to_effort(64000) == "xhigh"
    assert ir.thinking_budget_to_effort(8000.0) == "medium"
    # "minimal" and "max" alias "low"/"xhigh" in the forward map and are
    # deliberately unreachable from here.
    assert ir.thinking_budget_to_effort(1024) != "minimal"

def test_effective_reasoning_effort_survives_a_non_numeric_budget():
    """The GenParams accessor must not propagate the raise to its six callers.

    ``effective_reasoning_effort`` runs inside ``encode_request`` on openai, bai,
    nim, opencode, openrouter and workbuddy. A raise here is a 500 before any
    upstream call. With no ``effort``/``reasoning_effort`` set, a broken budget
    must degrade to the effort ``"none"`` (thinking off) rather than raise —
    the same "leave thinking OFF, never enable it at an arbitrary budget" rule
    ``effort_to_thinking_budget`` documents.

    ``None`` is excluded from the set: it is not a *broken* budget, it is an
    *absent* one, and ``effective_reasoning_effort``'s ``is not None`` test
    correctly returns ``None`` (meaning "no reasoning control", distinct from
    the string "none" meaning "thinking disabled"). Every other unusable value
    is present-but-unusable, so it must resolve to the string "none" — which is
    how every adapter spells "thinking disabled" — and never enable thinking at
    an arbitrary budget.
    """
    broken = [b for b in _NON_NUMERIC_BUDGETS if b is not None]
    for budget in broken:
        g = ir.GenParams(thinking_budget=budget)
        assert g.effective_reasoning_effort() == "none", budget
        # An explicit effort still outranks the budget-derived guess.
        g2 = ir.GenParams(thinking_budget=budget, effort="high")
        assert g2.effective_reasoning_effort() == "high", budget

    # The absent-budget control: still None, so a caller that set nothing gets
    # no reasoning control rather than a forced "thinking off".
    assert ir.GenParams(thinking_budget=None).effective_reasoning_effort() is None

def test_effective_thinking_budget_drops_a_non_numeric_budget():
    """The sibling accessor must return None, not the raw unvalidated field.

    Fixing only ``thinking_budget_to_effort`` left this half of the pair open:
    ``effective_thinking_budget`` forwarded the field verbatim, so every caller
    doing arithmetic on the result still raised — ``max(budget, 1024)`` in the
    Anthropic adapter and ``max(g.thinking_budget, 1024)`` in OpenRouter's, both
    inside ``encode_request``. None is what those callers already treat as "no
    resolvable budget — leave thinking OFF", so a malformed budget now degrades
    exactly like an unknown effort instead of 500ing.
    """
    for budget in _NON_NUMERIC_BUDGETS:
        g = ir.GenParams(thinking_budget=budget)
        assert g.effective_thinking_budget() is None, (
            f"effective_thinking_budget({budget!r}) forwarded an unusable value")

def test_effective_thinking_budget_keeps_well_typed_values():
    """The guard must pass through every value the callers can actually use.

    A float survives (the callers only compare and add, so ``8000.0`` has always
    been usable), and 0 survives — it is the documented "thinking disabled"
    signal the Anthropic and OpenRouter adapters branch on.
    """
    assert ir.GenParams(thinking_budget=0).effective_thinking_budget() == 0
    assert ir.GenParams(thinking_budget=1024).effective_thinking_budget() == 1024
    assert ir.GenParams(thinking_budget=32000).effective_thinking_budget() == 32000
    assert ir.GenParams(thinking_budget=8000.0).effective_thinking_budget() == 8000.0
    # Derived spellings are unaffected: the field is absent, so the effort map
    # resolves it.
    assert ir.GenParams(reasoning_effort="high").effective_thinking_budget() == 32000
    assert ir.GenParams(effort="high").effective_thinking_budget() == 32000
    assert ir.GenParams(reasoning_effort="none").effective_thinking_budget() is None

# ---------------------------------------------------------------------------
# Both findings, end to end through every adapter's encode path
# ---------------------------------------------------------------------------

# Every provider type whose encode_request consults one of the two helpers.
_ADAPTERS = (
    ("openai", "gpt-5"),
    ("bai", "deepseek-v4"),
    ("nvidia-nim", "nvidia/m"),
    ("opencode", "opencode/m"),
    ("openrouter", "anthropic/claude-x"),
    ("workbuddy", "m"),
    ("anthropic", "claude-x"),
    ("gemini", "gemini-x"),
)

def _encode_every_adapter(gen_params: ir.GenParams, parts: list) -> None:
    """Drive every adapter's encode_request; any raise fails the test."""
    req = ir.Request(model="m", messages=[ir.Message(role="user", parts=parts)],
                     gen_params=gen_params)
    for provider, model_id in _ADAPTERS:
        registry.fresh_adapter(provider).encode_request(req, model_id, {})

def test_broken_thinking_budget_never_raises_in_any_adapter():
    """A malformed budget must not 500 on any of the eight provider types.

    This is the end-to-end pin for #388. Fixing the IR helper alone was not
    enough: the Anthropic adapter's ``max(budget, MIN_THINKING_BUDGET)`` and
    OpenRouter's ``max(g.thinking_budget, 1024)`` each did their own arithmetic
    and raised before the IR guard could help them.
    """
    parts = [ir.TextPart("hi")]
    for budget in _NON_NUMERIC_BUDGETS:
        _encode_every_adapter(
            ir.GenParams(thinking_budget=budget, max_tokens=4096), parts)

def test_container_tool_name_never_raises_in_any_adapter():
    """The end-to-end pin for #387 across every provider that replays history."""
    for name in _NON_STR_NAMES:
        _encode_every_adapter(
            ir.GenParams(), [ir.ToolUsePart(id="c1", name=name)])

def test_openrouter_keeps_effort_precedence_over_a_derived_budget():
    """An effort-only request must still reach OpenRouter as ``{"effort": …}``.

    A near-miss while fixing #389: routing OpenRouter's read through
    ``effective_thinking_budget`` (which *derives* a budget from a named effort)
    promoted ``reasoning_effort: high`` to ``{"max_tokens": 32000}``, losing the
    provider's own effort level. The adapter documents the opposite precedence —
    a *direct* budget wins over a named level, because OpenRouter can express it
    exactly while an effort name would be rounded through the global map. So the
    raw field is read (coerced, per #388) and the derived accessor is not.
    """
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter
    from wiwi.wire import openai_chat as oc

    req = oc.decode_request({
        "model": "openai/o3-mini",
        "messages": [{"role": "user", "content": "think"}],
        "reasoning_effort": "high",
    })
    body = OpenRouterAdapter().encode_request(req, "openai/o3-mini", {})
    assert body["reasoning"] == {"effort": "high"}, body["reasoning"]

    # A direct budget still wins, and still gets clamped to the 1024 minimum.
    req2 = ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=8000))
    body2 = OpenRouterAdapter().encode_request(req2, "openai/o3-mini", {})
    assert body2["reasoning"] == {"max_tokens": 8000}, body2["reasoning"]
