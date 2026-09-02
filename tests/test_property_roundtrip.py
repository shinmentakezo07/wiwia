"""Property-based tests for IR round-trip fidelity and streaming delta legality.

Uses Hypothesis to generate random inputs and verify invariants that would
catch exactly the class of translation bugs fixed in
test_translation_enhancements.py — but across the entire input space, not
just the hand-picked cases.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from wiwi.ir import types as ir
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

# -- Strategies: generate valid IR objects ------------------------------------

text_strategy = st.text(min_size=0, max_size=200)

tool_name_strategy = st.text(min_size=1, max_size=50, alphabet=st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="_-"))

json_value_strategy = st.one_of(
    st.text(max_size=100),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
)

json_args_strategy = st.dictionaries(
    keys=st.text(min_size=1, max_size=30, alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="_")),
    values=json_value_strategy,
    max_size=5,
)

role_strategy = st.sampled_from(["system", "user", "assistant", "tool"])

part_strategy = st.one_of(
    st.builds(ir.TextPart, text=text_strategy),
    st.builds(ir.ToolUsePart, id=st.text(min_size=1, max_size=20),
              name=tool_name_strategy, args=json_args_strategy),
    st.builds(ir.ToolResultPart, tool_use_id=st.text(min_size=1, max_size=20),
              content=text_strategy),
    st.builds(ir.ThinkingPart, text=text_strategy,
              signature=st.one_of(st.none(), st.text(max_size=50))),
)

message_strategy = st.builds(
    ir.Message,
    role=role_strategy,
    parts=st.lists(part_strategy, min_size=1, max_size=5),
)

messages_strategy = st.lists(message_strategy, min_size=1, max_size=10)

effort_strategy = st.sampled_from(["none", "low", "medium", "high", "xhigh"])

gen_params_strategy = st.builds(
    ir.GenParams,
    temperature=st.one_of(st.none(), st.floats(min_value=0, max_value=2)),
    top_p=st.one_of(st.none(), st.floats(min_value=0, max_value=1)),
    max_tokens=st.one_of(st.none(), st.integers(min_value=1, max_value=100000)),
    stop=st.lists(st.text(max_size=20), max_size=4),
    seed=st.one_of(st.none(), st.integers()),
    reasoning_effort=st.one_of(st.none(), effort_strategy),
    thinking_budget=st.one_of(st.none(), st.integers(min_value=1024, max_value=64000)),
)

request_strategy = st.builds(
    ir.Request,
    model=st.text(min_size=1, max_size=50),
    messages=messages_strategy,
    gen_params=gen_params_strategy,
    stream=st.booleans(),
)


# -- Property 1: OpenAI chat decode → encode preserves text content -----------

@given(req=request_strategy)
@settings(max_examples=100)
def test_openai_roundtrip_preserves_text(req: ir.Request):
    """Text from user/assistant TextParts must survive encode → decode → encode."""
    from wiwi.providers.openai_adapter import OpenAIAdapter
    adapter = OpenAIAdapter()
    encoded = adapter.encode_request(req, req.model, {"provider_type": "openai"})

    # Collect original text from user and assistant messages only
    # (tool and system roles have different encode/decode semantics)
    original_texts = [p.text for m in req.messages for p in m.parts
                      if isinstance(p, ir.TextPart) and m.role in ("user", "assistant")]

    # Decode back from OpenAI body
    decoded = oc.decode_request({
        "model": req.model,
        "messages": encoded["messages"],
    })

    # Collect decoded text from user and assistant messages
    decoded_texts = [p.text for m in decoded.messages for p in m.parts
                     if isinstance(p, ir.TextPart) and m.role in ("user", "assistant")]

    # All non-empty original texts should be preserved
    for orig in original_texts:
        if orig:
            assert any(orig in d or d in orig for d in decoded_texts), \
                f"Text '{orig}' lost in round-trip. Decoded: {decoded_texts}"


# -- Property 2: Anthropic decode → encode preserves text content ------------

@given(req=request_strategy)
@settings(max_examples=100)
def test_anthropic_roundtrip_preserves_text(req: ir.Request):
    """Text from TextParts must survive Anthropic encode → decode."""
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    adapter = AnthropicAdapter()
    encoded = adapter.encode_request(req, req.model, {})

    # Skip if no messages (system-only)
    if not encoded.get("messages"):
        return

    # Decode back
    decoded = am.decode_request({
        "model": req.model,
        "max_tokens": encoded.get("max_tokens", 4096),
        "messages": encoded["messages"],
    })

    original_texts = [p.text for m in req.messages for p in m.parts
                      if isinstance(p, ir.TextPart) and m.role in ("user", "assistant")]
    decoded_texts = [p.text for m in decoded.messages for p in m.parts
                     if isinstance(p, ir.TextPart) and m.role in ("user", "assistant")]

    for orig in original_texts:
        if orig:
            assert any(orig in d or d in orig for d in decoded_texts), \
                f"Text '{orig}' lost in Anthropic round-trip"


# -- Property 3: No null content without tool_calls in OpenAI body -----------

@given(req=request_strategy)
@settings(max_examples=100)
def test_no_null_content_without_tool_calls(req: ir.Request):
    """OpenAI-format messages must never have content: null unless tool_calls present."""
    from wiwi.providers.openai_adapter import OpenAIAdapter
    adapter = OpenAIAdapter()
    encoded = adapter.encode_request(req, req.model, {"provider_type": "openai"})

    for msg in encoded["messages"]:
        if msg.get("content") is None and "tool_calls" not in msg:
            assert False, f"Null content without tool_calls: {msg}"


# -- Property 4: OpenRouter body has no reasoning_effort (uses reasoning) -----

@given(req=request_strategy)
@settings(max_examples=50)
def test_openrouter_never_sends_reasoning_effort(req: ir.Request):
    """OpenRouter adapter must never include reasoning_effort; it uses reasoning."""
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter
    adapter = OpenRouterAdapter()
    encoded = adapter.encode_request(req, req.model, {"provider_type": "openrouter"})
    assert "reasoning_effort" not in encoded, \
        "reasoning_effort leaked into OpenRouter body"
    # If reasoning is present, it must be a dict with valid keys
    if "reasoning" in encoded:
        r = encoded["reasoning"]
        assert isinstance(r, dict)
        valid_keys = {"effort", "max_tokens", "enabled", "exclude"}
        assert set(r.keys()).issubset(valid_keys), f"Invalid reasoning keys: {set(r.keys())}"


# -- Property 5: OpenRouter uses max_completion_tokens, not max_tokens --------

@given(req=request_strategy)
@settings(max_examples=50)
def test_openrouter_uses_max_completion_tokens(req: ir.Request):
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter
    adapter = OpenRouterAdapter()
    encoded = adapter.encode_request(req, req.model, {"provider_type": "openrouter"})
    assert "max_tokens" not in encoded, "max_tokens should be renamed to max_completion_tokens"


# -- Property 6: IRStreamDelta sequence legality -----------------------------

# A legal stream: StreamStart → content* → UsageFinal → Finish → StreamEnd
legal_delta_sequence = st.lists(
    st.one_of(
        st.builds(dl.TextDelta, text=text_strategy),
        st.builds(dl.ThinkingDelta, text=text_strategy),
    ),
    min_size=0,
    max_size=10,
)


@given(content_deltas=legal_delta_sequence)
@settings(max_examples=100)
def test_chat_stream_encoder_produces_valid_sse(content_deltas):
    """ChatStreamEncoder must produce well-formed SSE for any legal delta sequence."""
    enc = oc.ChatStreamEncoder("test-model", "req-123", include_usage=True)
    frames = []

    start = enc.feed(dl.StreamStart(model="test-model"))
    if start:
        frames.append(start)

    for d in content_deltas:
        chunk = enc.feed(d)
        if chunk:
            frames.append(chunk)

    # Add usage + finish (legal stream requires these)
    usage = enc.feed(dl.UsageFinal(prompt=10, output=5))
    if usage:
        frames.append(usage)

    finish = enc.feed(dl.Finish("stop"))
    if finish:
        frames.append(finish)

    final = enc.final_frame()
    frames.append(final)

    blob = b"".join(frames)

    # Must contain finish_reason
    assert b"finish_reason" in blob
    # Must contain usage
    assert b"prompt_tokens" in blob or b"prompt" in blob
    # No malformed JSON (each frame should be valid SSE: data: {...}\n\n)
    for frame in frames:
        assert frame.endswith(b"\n\n"), f"Frame not SSE-terminated: {frame[:50]}"
