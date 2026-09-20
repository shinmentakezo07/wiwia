"""Shared canonical translation helpers: finish_reason maps and extras merging.

These helpers live in ``wiwi/ir/`` because both ``wiwi/wire/`` (inbound codecs)
and ``wiwi/providers/`` (outbound adapters) import them. Anything placed in
``wiwi/wire/`` would force the providers package to import a dialect module,
which the layering rule forbids.
"""
from wiwi.ir import translation as tr


class TestFinishReason:
    def test_openai_to_ir_covers_standard_spellings(self):
        assert tr.normalize_finish_reason("stop") == "stop"
        assert tr.normalize_finish_reason("length") == "length"
        assert tr.normalize_finish_reason("tool_calls") == "tool_call"
        assert tr.normalize_finish_reason("function_call") == "tool_call"
        assert tr.normalize_finish_reason("content_filter") == "content_filter"

    def test_openai_to_ir_covers_nonstandard_spellings(self):
        assert tr.normalize_finish_reason("tool_use") == "tool_call"
        assert tr.normalize_finish_reason("max_tokens") == "length"
        assert tr.normalize_finish_reason("end_turn") == "stop"
        assert tr.normalize_finish_reason("stop_sequence") == "stop_sequence"

    def test_normalize_is_total(self):
        # Never raise, always a legal StopReason.
        assert tr.normalize_finish_reason(None) == "stop"
        assert tr.normalize_finish_reason(5) == "stop"
        assert tr.normalize_finish_reason(["length"]) == "stop"
        assert tr.normalize_finish_reason("who_knows") == "stop"

    def test_ir_to_openai_is_the_inverse_on_the_shared_vocabulary(self):
        assert tr.ir_to_openai_finish("stop") == "stop"
        assert tr.ir_to_openai_finish("length") == "length"
        assert tr.ir_to_openai_finish("tool_call") == "tool_calls"
        assert tr.ir_to_openai_finish("content_filter") == "content_filter"

    def test_ir_to_openai_is_total_for_anthropic_only_reasons(self):
        # Anthropic-only IR reasons have no OpenAI spelling; they must not
        # crash and must land on something an OpenAI client accepts.
        for sr in ("pause_turn", "stop_sequence", "context_window_exceeded",
                   "compaction", "nope"):
            assert isinstance(tr.ir_to_openai_finish(sr), str)

    def test_roundtrip_through_ir_is_stable(self):
        for raw in ("stop", "length", "tool_calls", "function_call",
                    "content_filter", "tool_use", "max_tokens"):
            once = tr.normalize_finish_reason(raw)
            assert tr.normalize_finish_reason(tr.ir_to_openai_finish(once)) == once


class TestCarryExtras:
    def test_denylist_shape(self):
        known = frozenset({"model", "messages"})
        assert tr.carry_extras({"model": "m", "messages": [], "top_k": 3},
                               known) == {"top_k": 3}

    def test_typed_wrong_source_is_empty_not_a_crash(self):
        known = frozenset({"model"})
        assert tr.carry_extras({}, known) == {}
        assert tr.carry_extras({"model": 1}, known) == {}

    def test_empty_known_returns_everything(self):
        assert tr.carry_extras({"a": 1}, frozenset()) == {"a": 1}


class TestMergeExtras:
    def test_later_wins(self):
        assert tr.merge_extras({"a": 1, "b": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_skips_non_dict_layers(self):
        assert tr.merge_extras(None, {"a": 1}, [1, 2], "x", {}) == {"a": 1}

    def test_none_layer_does_not_erase(self):
        # `{**None}` raises; this must not.
        assert tr.merge_extras({"a": 1}, None) == {"a": 1}


def test_helpers_are_importable_from_the_providers_layer():
    """Layering guard: `wiwi/ir/translation.py` is the only place both
    `wiwi/wire/` and `wiwi/providers/` may import a shared translation helper
    from. This test lives beside the helpers so a future move is caught."""
    import importlib
    mod = importlib.import_module("wiwi.ir.translation")
    assert mod is not None
    assert not hasattr(mod, "_WIRE_ONLY_MARKER")


class TestUnmappedLogSetIsBounded:
    """The dedup set holds upstream-controlled strings, so it must not grow
    without limit — an unbounded set is a slow leak on a long-lived process
    fed junk finish reasons."""

    def test_the_cap_is_finite_and_positive(self):
        assert 0 < tr._UNMAPPED_LOGGED_CAP < 10**6

    def test_the_set_stops_growing_at_the_cap(self):
        tr._UNMAPPED_LOGGED.clear()
        try:
            for i in range(tr._UNMAPPED_LOGGED_CAP + 50):
                tr.normalize_finish_reason(f"bogus-reason-{i}")
            assert len(tr._UNMAPPED_LOGGED) == tr._UNMAPPED_LOGGED_CAP
        finally:
            tr._UNMAPPED_LOGGED.clear()

    def test_a_known_reason_is_never_recorded(self):
        tr._UNMAPPED_LOGGED.clear()
        try:
            for known in ("stop", "length", "tool_calls", "content_filter"):
                tr.normalize_finish_reason(known)
            assert tr._UNMAPPED_LOGGED == set()
        finally:
            tr._UNMAPPED_LOGGED.clear()
