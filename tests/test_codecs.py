"""Codec round-trip tests: decode -> encode for all four dialects."""

import pytest

from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_completions as ocmpl
from wiwi.wire import openai_responses as orp


def _req():
    from wiwi.ir import types as ir
    return ir.Request(model="m", messages=[ir.Message(role="user",
                                                      parts=[ir.TextPart("x")])])


def test_openai_chat_basic_decode():
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
        "temperature": 0.5,
        "max_tokens": 100,
    })
    assert req.messages[0].parts[0].text == "be brief"
    assert req.messages[1].parts[0].text == "hi"
    assert req.gen_params.temperature == 0.5
    assert req.gen_params.max_tokens == 100
    assert not req.stream


def test_openai_chat_tool_roundtrip():
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
                                                  "function": {"name": "get_weather",
                                                               "arguments": '{"city":"SF"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny 20C"},
        ],
        "tools": [{"type": "function", "function": {"name": "get_weather",
                                                    "description": "w",
                                                    "parameters": {"type": "object"}}}],
    })
    assistant = req.messages[1]
    assert assistant.parts[0].name == "get_weather"
    assert assistant.parts[0].args == {"city": "SF"}
    tool_msg = req.messages[2]
    assert tool_msg.parts[0].tool_use_id == "call_1"
    assert req.tools[0].name == "get_weather"


def test_openai_chat_tool_null_content_becomes_empty_string():
    """A tool message with content=null must not be encoded as the literal 'None'.

    Regression: str(None) used to produce the four-character string 'None',
    silently corrupting the tool result sent to the upstream provider.
    """
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
                                                  "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": None},
        ],
    })
    tool_msg = req.messages[1]
    assert tool_msg.parts[0].content == "", (
        f"null tool content must decode to empty string, got {tool_msg.parts[0].content!r}"
    )


def test_openai_chat_tool_list_content_is_coerced_to_string():
    """A tool message with content=[{type:text,...}] (malformed for OpenAI
    Chat, common in Anthropic-shaped clients) must be coerced to a string.
    Without coercion, downstream adapters would serialize a list into a
    provider's tool_result content, which Anthropic (and others) reject.
    """
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
                                                  "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": [{"type": "text", "text": "hello world"}]},
        ],
    })
    tool_msg = req.messages[1]
    assert isinstance(tool_msg.parts[0].content, str), (
        f"list tool content must coerce to str, got {type(tool_msg.parts[0].content).__name__}"
    )
    assert "hello world" in tool_msg.parts[0].content


def test_openai_chat_decodes_reasoning_content_into_thinking_part():
    """Reasoning models emit a separate reasoning_content field on assistant
    messages. The decoder must lift it into a ThinkingPart so the IR carries
    the thinking context forward — without this, Anthropic extended-thinking
    breaks across turns when a Claude-Code-via-OpenAI-shape client echoes
    prior assistant messages with reasoning_content set.
    """
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "what is 2+2?"},
            {"role": "assistant", "content": "4",
             "reasoning_content": "The user asks what 2+2 is. The answer is 4."},
        ],
    })
    assistant = req.messages[1]
    # The thinking text must be in a ThinkingPart on the assistant message.
    from wiwi.ir.types import ThinkingPart
    thinking_parts = [p for p in assistant.parts if isinstance(p, ThinkingPart)]
    assert len(thinking_parts) == 1, (
        f"expected exactly one ThinkingPart, got {[type(p).__name__ for p in assistant.parts]}"
    )
    assert thinking_parts[0].text == "The user asks what 2+2 is. The answer is 4."


def test_anthropic_decode_system_and_tools():
    req = am.decode_request({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": "be brief",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "weather?",
                                          "cache_control": {"type": "ephemeral"}}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1",
                                               "name": "get_weather",
                                               "input": {"city": "SF"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1",
                                          "content": "sunny"}]},
        ],
        "tools": [{"name": "get_weather", "description": "w",
                   "input_schema": {"type": "object"}}],
    })
    assert req.messages[0].parts[0].text == "be brief"
    assert req.messages[1].parts[0].cache_control == {"type": "ephemeral"}
    assert req.messages[2].parts[0].name == "get_weather"
    assert req.messages[3].parts[0].content == "sunny"


def test_anthropic_stream_encoder_sequence():
    from wiwi.streaming import deltas as dl
    enc = am.AnthropicStreamEncoder("claude-x", "abc123")
    frames = []
    for d in [dl.StreamStart("claude-x"), dl.TextDelta("Hel"), dl.TextDelta("lo"),
              dl.ToolCallOpen(0, "tu_9", "f"), dl.ToolCallArgsDelta(0, '{"a":'),
              dl.ToolCallArgsDelta(0, '1}'), dl.ToolCallClose(0),
              dl.UsageFinal(prompt=10, output=5), dl.Finish("tool_call"),
              dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            frames.append(chunk.decode())
    frames.append(enc.final_frame().decode())  # message_delta w/ stop_reason + usage
    frames.append(b'event: message_stop\ndata: {"type": "message_stop"}\n\n'.decode())  # caller emits sentinel
    blob = "".join(frames)
    assert "message_start" in blob
    assert "text_delta" in blob and '"Hel"' in blob
    assert "tool_use" in blob and "tu_9" in blob
    assert "input_json_delta" in blob
    assert "message_delta" in blob and '"tool_use"' in blob  # stop_reason
    assert "message_stop" in blob
    # blocks: text(1) + tool_use(1); each start = one "event: content_block_start" line
    assert blob.count("event: content_block_start") == 2


def test_chat_stream_encoder_tool_args():
    from wiwi.streaming import deltas as dl
    enc = oc.ChatStreamEncoder("gpt-4o", "abc", include_usage=True)
    frames = []
    for d in [dl.StreamStart("gpt-4o"), dl.ToolCallOpen(0, "call_1", "f"),
              dl.ToolCallArgsDelta(0, '{"x":'), dl.ToolCallArgsDelta(0, "1}"),
              dl.ToolCallClose(0),
              dl.UsageFinal(prompt=8, output=3), dl.Finish("tool_call"),
              dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            frames.append(chunk)
    frames.append(enc.final_frame())  # usage+finish frame, then caller sends [DONE]
    frames.append(b"data: [DONE]\n\n")
    blob = b"".join(frames).decode()
    assert '"tool_calls"' in blob
    assert '"name":"f"' in blob.replace(" ", "") or '"name": "f"' in blob
    assert "[DONE]" in blob
    # final usage frame carries token counts
    assert "prompt_tokens" in blob.split("[DONE]")[0][-600:]

def test_responses_decode_stateless():
    req = orp.decode_request({
        "model": "claude-sonnet",
        "instructions": "be terse",
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "hi"}]},
        ],
        "tools": [{"type": "function", "name": "f", "description": "",
                   "parameters": {"type": "object"}}],
    })
    assert req.messages[0].parts[0].text == "be terse"
    assert req.messages[1].parts[0].text == "hi"
    assert req.tools[0].name == "f"


def test_responses_rejects_previous_response_id():
    import pytest
    with pytest.raises(oc.DialectError):
        orp.decode_request({"model": "x", "previous_response_id": "resp_old",
                            "input": []})


# -- legacy completions surface -------------------------------------------------


def test_completions_string_prompt_decodes_to_one_user_message():
    req = ocmpl.decode_request({
        "model": "gpt-3.5-turbo-instruct",
        "prompt": "say hi",
        "max_tokens": 5,
        "temperature": 0.2,
    })
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"
    assert req.messages[0].parts[0].text == "say hi"
    assert req.gen_params.max_tokens == 5
    assert req.gen_params.temperature == 0.2
    assert req.stream is False


def test_completions_single_element_prompt_array_is_accepted():
    req = ocmpl.decode_request({"model": "m", "prompt": ["only"]})
    assert req.messages[0].parts[0].text == "only"


def test_completions_multi_prompt_array_is_rejected():
    with pytest.raises(ocmpl.DialectError):
        ocmpl.decode_request({"model": "m", "prompt": ["a", "b"]})


def test_completions_token_id_prompt_is_rejected():
    with pytest.raises(ocmpl.DialectError):
        ocmpl.decode_request({"model": "m", "prompt": [1, 2, 3]})


def test_completions_unsupported_params_are_rejected():
    for key, value in (("logprobs", 3), ("best_of", 2), ("echo", True)):
        with pytest.raises(ocmpl.DialectError):
            ocmpl.decode_request({"model": "m", "prompt": "x", key: value})


def test_completions_missing_model_is_rejected():
    with pytest.raises(ocmpl.DialectError):
        ocmpl.decode_request({"prompt": "x"})


def test_completions_suffix_is_appended_to_the_prompt():
    req = ocmpl.decode_request({"model": "m", "prompt": "def f():", "suffix": "return 1"})
    assert req.messages[0].parts[0].text == "def f():return 1"


def test_completions_encode_response_shape():
    from wiwi.core.context import RequestContext
    from wiwi.ir import types as ir
    turn = ir.AssistantTurn(text="hello", stop_reason="stop",
                            usage=ir.Usage(prompt_tokens=3, completion_tokens=2))
    out = ocmpl.encode_response(RequestContext(surface="completions", ir_req=_req()),
                                turn, "gpt-3.5-turbo-instruct", "abc123")
    assert out["id"] == "cmpl-abc123"
    assert out["object"] == "text_completion"
    assert out["choices"] == [{"index": 0, "text": "hello", "logprobs": None,
                               "finish_reason": "stop"}]
    assert out["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_completions_encode_response_never_reports_tool_calls():
    from wiwi.core.context import RequestContext
    from wiwi.ir import types as ir
    turn = ir.AssistantTurn(text="", stop_reason="tool_calls",
                            tool_calls=[ir.ToolUsePart(id="c1", name="f")])
    ctx = RequestContext(surface="completions", ir_req=_req())
    out = ocmpl.encode_response(ctx, turn, "m", "r1")
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["choices"][0]["text"] == ""


def _frames(enc, deltas):
    out = b""
    for d in deltas:
        chunk = enc.feed(d)
        if chunk:
            out += chunk
    return out


def test_completions_stream_emits_text_chunks_and_finish():
    from wiwi.streaming import deltas as dl
    enc = ocmpl.CompletionStreamEncoder("m", "r1")
    body = _frames(enc, [dl.StreamStart(model="m"), dl.TextDelta("he"),
                         dl.TextDelta("llo"), dl.UsageFinal(prompt=1, output=2),
                         dl.Finish(stop_reason="stop"), dl.StreamEnd()])
    text = body.decode()
    assert '"object":"text_completion"' in text
    assert '"text":"he"' in text and '"text":"llo"' in text
    finish = enc.final_frame().decode()
    assert '"finish_reason":"stop"' in finish
    # No ``[DONE]`` here: the shared stream wrapper appends it for style "chat".
    assert "[DONE]" not in finish
    # Usage rides only when the client asked for it.
    assert '"usage"' not in finish


def test_completions_stream_usage_frame_only_when_requested():
    from wiwi.streaming import deltas as dl
    enc = ocmpl.CompletionStreamEncoder("m", "r1", include_usage=True)
    enc.feed(dl.StreamStart(model="m"))
    enc.feed(dl.TextDelta("x"))
    enc.feed(dl.UsageFinal(prompt=7, output=3))
    enc.feed(dl.Finish(stop_reason="stop"))
    finish = enc.final_frame().decode()
    assert '"usage"' in finish and '"prompt_tokens":7' in finish


def test_completions_stream_drops_thinking_and_tool_deltas():
    from wiwi.streaming import deltas as dl
    enc = ocmpl.CompletionStreamEncoder("m", "r1")
    enc.feed(dl.StreamStart(model="m"))
    assert enc.feed(dl.ThinkingDelta("hmm")) is None
    assert enc.feed(dl.ToolCallOpen(index=0, id="c1", name="f")) is None
    assert enc.feed(dl.ToolCallArgsDelta(index=0, args_fragment="{}")) is None
    assert enc.feed(dl.ToolCallClose(index=0)) is None
    enc.feed(dl.Finish(stop_reason="tool_calls"))
    assert '"finish_reason":"stop"' in enc.final_frame().decode()
