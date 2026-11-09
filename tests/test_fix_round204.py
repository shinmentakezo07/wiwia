"""Round 204: the implicit state machines, made explicit and checkable.

Three machines were real but only inferable from code or prose:

* the delta-stream contract (documented in streaming/deltas.py, enforced
  nowhere) - now a DeltaStreamState that reports violations instead of
  letting a malformed client stream pass silently;
* the provider-key lifecycle (five statuses mutated from six sites, partly
  by hand) - now declared once and routed through recover(force=True);
* the stream encoders' per-turn state (three private vocabularies for the
  same concept) - now one stable explain() summary.
"""

import pytest

from wiwi.router.router import KEY_STATUSES, ProviderKey
from wiwi.streaming import deltas as dl
from wiwi.streaming.machine import DeltaStreamState, explain_encoder, validate_stream


def _legal() -> list:
    return [
        dl.StreamStart("m"),
        dl.TextDelta("hi"),
        dl.UsageFinal(),
        dl.Finish("stop"),
        dl.StreamEnd(),
    ]


# -- the delta-stream machine -------------------------------------------------

def test_legal_stream_has_no_problem():
    assert validate_stream(_legal()) is None


def test_parallel_siblings_are_legal_and_may_close_out_of_order():
    """The contract says parallel calls are siblings, not nested."""
    seq = [
        dl.StreamStart("m"),
        dl.ToolCallOpen(0, "a", {}),
        dl.ToolCallOpen(1, "b", {}),
        dl.ToolCallArgsDelta(1, "{}"),
        dl.ToolCallClose(1),
        dl.ToolCallArgsDelta(0, "{}"),
        dl.ToolCallClose(0),
        dl.Finish("tool_call"),
        dl.StreamEnd(),
    ]
    assert validate_stream(seq) is None


def test_repeated_usage_before_late_content_is_legal():
    """UsageFinal may repeat and may precede the last content delta."""
    seq = [
        dl.StreamStart("m"),
        dl.UsageFinal(),
        dl.TextDelta("late"),
        dl.UsageFinal(),
        dl.Finish("stop"),
        dl.StreamEnd(),
    ]
    assert validate_stream(seq) is None


def test_stream_error_is_legal_from_any_phase():
    """The abnormal terminal needs no Finish, and may follow one."""
    for seq in ([dl.StreamStart("m"), dl.StreamError("x", "api_error")],
                [dl.StreamStart("m"), dl.Finish("stop"),
                 dl.StreamError("x", "api_error")]):
        assert validate_stream(seq) is None


@pytest.mark.parametrize("seq,needle", [
    ([dl.TextDelta("x")], "before StreamStart"),
    ([dl.StreamStart("m"), dl.StreamStart("m")], "StreamStart repeated"),
    ([dl.StreamStart("m"), dl.ToolCallArgsDelta(3, "{}")], "closed tool index 3"),
    ([dl.StreamStart("m"), dl.ToolCallClose(0)], "closed tool index 0"),
    ([dl.StreamStart("m"), dl.StreamEnd()], "without Finish"),
    ([dl.StreamStart("m"), dl.Finish("stop"), dl.StreamEnd(),
      dl.TextDelta("late")], "already ended"),
    ([dl.StreamStart("m"), dl.Finish("stop"), dl.StreamStart("m")],
     "after the stream ended"),
])
def test_violations_are_reported_with_a_specific_reason(seq, needle):
    problem = validate_stream(seq)
    assert problem is not None
    assert needle in problem


def test_a_stream_that_never_ends_is_reported():
    assert "no StreamEnd" in (validate_stream([dl.StreamStart("m")]) or "")


def test_observe_never_raises_and_keeps_the_first_problem():
    """The observer is on a live path: it cannot raise, and the FIRST
    violation is the useful one."""
    st = DeltaStreamState()
    assert st.observe(dl.TextDelta("x")) == "TextDelta before StreamStart"
    # Every later delta is ignored once a problem is recorded.
    assert st.observe(dl.StreamStart("m")) is None
    assert st.problem == "TextDelta before StreamStart"
    assert "INVALID" in st.explain()


def test_state_explain_names_the_open_tools():
    st = DeltaStreamState()
    st.observe(dl.StreamStart("m"))
    st.observe(dl.ToolCallOpen(0, "a", {}))
    assert "open_tools=[0]" in st.explain()
    assert st.terminal is False


# -- the provider-key machine ------------------------------------------------

def test_key_statuses_are_declared():
    assert {"active", "cooling", "invalid", "disabled", "probation"} == KEY_STATUSES


def test_recover_force_clears_the_streak_that_retired_the_key():
    """AUDIT #199: the hand-rolled reset left err_count, so the next failure
    re-retired the key immediately."""
    k = ProviderKey("a", "s")
    k.err_count = 7
    k.mark_invalid(None)
    k.recover(force=True)
    assert k.status == "active"
    assert k.err_count == 0
    assert k.cooldown_until == 0.0


def test_oauth_resets_go_through_recover_force_not_hand_setting():
    """Six sites used to set status/cooldown by hand; every one must now call
    recover(force=True), which is the only transition that also clears the
    failure streak."""
    import inspect

    from wiwi.providers import cline_auto_refresh, hermes_auto_refresh, workbuddy_auto_refresh
    for mod in (cline_auto_refresh, hermes_auto_refresh, workbuddy_auto_refresh):
        src = inspect.getsource(mod)
        assert 'status = "active"' not in src, mod.__name__
        assert "recover(force=True)" in src, mod.__name__


@pytest.mark.parametrize("setup,needle", [
    (lambda k: None, "active"),
    (lambda k: k.mark_invalid(None), "until an operator or the healer"),
    (lambda k: k.mark_cooling(30.0), "cooling for"),
    (lambda k: k.mark_invalid(600.0), "revives in"),
    (lambda k: k.mark_recovered(), "probation"),
    (lambda k: setattr(k, "enabled", False), "disabled"),
])
def test_key_explain_names_the_derived_state(setup, needle):
    k = ProviderKey("a", "s")
    setup(k)
    assert needle in k.explain()


def test_timed_and_terminal_invalid_are_distinguishable():
    """Both are status="invalid"; only the window tells them apart, which is
    exactly the ambiguity explain() removes."""
    timed, terminal = ProviderKey("t", "s"), ProviderKey("p", "s")
    timed.mark_invalid(600.0)
    terminal.mark_invalid(None)
    assert timed.status == terminal.status == "invalid"
    assert timed.explain() != terminal.explain()


# -- the encoder state summary -----------------------------------------------

def test_every_encoder_exposes_explain():
    from wiwi.wire.anthropic_messages import AnthropicStreamEncoder
    from wiwi.wire.openai_chat import ChatStreamEncoder
    from wiwi.wire.openai_completions import CompletionStreamEncoder
    from wiwi.wire.openai_responses import ResponsesStreamEncoder
    for cls in (ChatStreamEncoder, AnthropicStreamEncoder,
                ResponsesStreamEncoder, CompletionStreamEncoder):
        out = cls("m", "r").explain()
        assert isinstance(out, str) and out, cls.__name__
        assert "open=" in out and "usage=" in out, cls.__name__


def test_chat_explain_shows_open_siblings_and_deferred_prose():
    from wiwi.wire.openai_chat import ChatStreamEncoder
    enc = ChatStreamEncoder("m", "r")
    for d in (dl.StreamStart("m"), dl.ToolCallOpen(0, "a", {}),
              dl.ToolCallOpen(1, "b", {}), dl.TextDelta("held")):
        enc.feed(d)
    out = enc.explain()
    assert "open_tools=[0, 1]" in out
    assert "deferred=1item" in out


def test_explain_encoder_formats_consistently():
    assert explain_encoder("tool", {2, 0}, 3, 120, 2, True) == (
        "open=tool tool_calls=2 open_tools=[0, 2] deferred=3item/120ch usage=buffered")
