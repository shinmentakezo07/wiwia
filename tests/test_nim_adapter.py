"""Tests for the NVIDIA NIM adapter: reasoning translation, tool schema
sanitization, native MiniMax tool-stream normalization, and registry wiring."""

import json

from wiwi.ir import types as ir
from wiwi.providers.nim_adapter import NimAdapter
from wiwi.providers.nim_native_tools import (
    _NAMESPACE,
    _TOOL_BLOCK_END,
    _TOOL_BLOCK_START,
    MiniMaxFramer,
    NimToolProtocolError,
    parse_tool_block,
)
from wiwi.providers.nim_tool_schema import (
    collect_nim_tool_aliases,
    sanitize_nim_tool_schemas,
    unalias_nim_tool_args,
)
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

# -- registry ------------------------------------------------------------------

def test_registry_returns_nim_adapter():
    from wiwi.providers.registry import get_adapter
    adapter = get_adapter("nvidia-nim")
    assert isinstance(adapter, NimAdapter)


def test_provider_type_attribute():
    assert NimAdapter().provider_type == "nvidia-nim"


def test_build_url_chat():
    ad = NimAdapter()
    assert ad.build_url("https://integrate.api.nvidia.com/v1",
                        "nvidia/nemotron", False, "chat") == (
        "https://integrate.api.nvidia.com/v1/chat/completions")


def test_build_url_embeddings():
    ad = NimAdapter()
    assert ad.build_url("https://integrate.api.nvidia.com/v1",
                        "nvidia/nemotron", False, "embeddings") == (
        "https://integrate.api.nvidia.com/v1/embeddings")


def test_headers():
    ad = NimAdapter()
    key_ref = type("K", (), {"label": "x", "secret": "sk-test"})()
    h = ad.headers(key_ref)
    assert h == {"Authorization": "Bearer sk-test"}


# -- encode: reasoning translation ---------------------------------------------

def test_reasoning_effort_high_maps_to_chat_template_kwargs():
    """OpenAI reasoning_effort -> NIM chat_template_kwargs.thinking=True + budget."""
    req = oc.decode_request({
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "messages": [{"role": "user", "content": "think"}],
        "reasoning_effort": "high",
        "stream": True,
    })
    body = NimAdapter().encode_request(req, "nvidia/nemotron-3-super-120b-a12b",
                                       {"provider_type": "nvidia-nim"})
    ctk = body["extra_body"]["chat_template_kwargs"]
    assert ctk["thinking"] is True
    assert ctk["enable_thinking"] is True
    # high -> 32000 per the IR effort->budget map
    assert ctk["reasoning_budget"] == 32000
    assert "reasoning_effort" not in body


def test_reasoning_effort_none_disables_thinking():
    """reasoning_effort='none' -> chat_template_kwargs.thinking=False."""
    req = oc.decode_request({
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "messages": [{"role": "user", "content": "quick"}],
        "reasoning_effort": "none",
    })
    body = NimAdapter().encode_request(req, "nvidia/nemotron-3-super-120b-a12b",
                                       {"provider_type": "nvidia-nim"})
    ctk = body["extra_body"]["chat_template_kwargs"]
    assert ctk["thinking"] is False
    assert ctk["enable_thinking"] is False


def test_thinking_budget_maps_to_reasoning_budget():
    """Anthropic thinking.budget_tokens -> NIM reasoning_budget."""
    req = am.decode_request({
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": "think"}],
        "thinking": {"type": "enabled", "budget_tokens": 10000},
    })
    body = NimAdapter().encode_request(req, "nvidia/nemotron-3-super-120b-a12b",
                                       {"provider_type": "nvidia-nim"})
    ctk = body["extra_body"]["chat_template_kwargs"]
    assert ctk["thinking"] is True
    assert ctk["enable_thinking"] is True
    assert ctk["reasoning_budget"] == 10000
    assert "reasoning_effort" not in body


def test_no_reasoning_config_omits_chat_template_kwargs():
    """No reasoning config -> no chat_template_kwargs injected."""
    req = ir.Request(
        model="nvidia/nemotron",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(),
    )
    body = NimAdapter().encode_request(req, "nvidia/nemotron",
                                       {"provider_type": "nvidia-nim"})
    eb = body.get("extra_body", {})
    assert "chat_template_kwargs" not in eb


# -- encode: tool schema sanitization ------------------------------------------

def test_boolean_subschema_stripped():
    """NIM rejects boolean JSON Schema subschemas; they must be removed."""
    tools = [
        {"type": "function", "function": {
            "name": "search",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "additionalProperties": True,  # boolean -> must be stripped
            },
        }},
    ]
    sanitized = sanitize_nim_tool_schemas(tools)
    params = sanitized[0]["function"]["parameters"]
    assert "additionalProperties" not in params
    assert params["properties"]["query"] == {"type": "string"}


def test_unsafe_param_name_aliased():
    """A param named 'type' must be aliased to '_nim_arg_type'."""
    tools = [
        {"type": "function", "function": {
            "name": "create",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["a", "b"]},
                    "name": {"type": "string"},
                },
                "required": ["type"],
            },
        }},
    ]
    sanitized = sanitize_nim_tool_schemas(tools)
    params = sanitized[0]["function"]["parameters"]
    assert "_nim_arg_type" in params["properties"]
    assert "type" not in params["properties"]
    assert "_nim_arg_type" in params["required"]
    assert "type" not in params["required"]


def test_collect_and_unalias():
    """collect_nim_tool_aliases finds the alias, unalias reverses it."""
    tools = [
        {"type": "function", "function": {
            "name": "create",
            "parameters": {
                "type": "object",
                "properties": {
                    "_nim_arg_type": {"type": "string"},
                    "name": {"type": "string"},
                },
            },
        }},
    ]
    aliases = collect_nim_tool_aliases(tools)
    assert aliases == {"create": {"_nim_arg_type": "type"}}
    unaliased = unalias_nim_tool_args({"_nim_arg_type": "foo", "name": "bar"},
                                      aliases["create"])
    assert unaliased == {"type": "foo", "name": "bar"}


def test_encode_request_sanitizes_tools():
    """encode_request should sanitize tool definitions for NIM."""
    req = oc.decode_request({
        "model": "nvidia/nemotron",
        "messages": [{"role": "user", "content": "search"}],
        "tools": [{"type": "function", "function": {
            "name": "search",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "additionalProperties": True,
            },
        }}],
    })
    body = NimAdapter().encode_request(req, "nvidia/nemotron",
                                       {"provider_type": "nvidia-nim"})
    tool = body["tools"][0]
    assert "additionalProperties" not in tool["function"]["parameters"]


# -- decode: non-streaming response -------------------------------------------

def test_decode_response_plain_text():
    """Plain text response decodes normally (no native markup)."""
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "hello"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    turn = NimAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.text == "hello"
    assert turn.stop_reason == "stop"
    assert turn.usage.prompt_tokens == 5


def test_decode_response_reasoning_content():
    """NIM returns reasoning_content in the message body."""
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "answer",
                                "reasoning_content": "step by step"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    turn = NimAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.text == "answer"
    assert len(turn.thinking) == 1
    assert turn.thinking[0].text == "step by step"


def test_decode_response_structured_tool_calls():
    """Standard OpenAI tool_calls in the response decode normally."""
    payload = {
        "choices": [{"message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {
                "name": "search", "arguments": '{"q": "test"}'}}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10},
    }
    turn = NimAdapter().decode_response(200, json.dumps(payload).encode())
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "search"
    assert turn.tool_calls[0].args == {"q": "test"}


# -- decode: streaming with native MiniMax tool normalization ------------------

def test_stream_plain_text():
    """Plain text streaming passes through the framer unchanged."""
    ad = NimAdapter()
    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"content": "hello "}}]}),
        json.dumps({"choices": [{"delta": {"content": "world"}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2}}),
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))
    kinds = [type(d).__name__ for d in deltas_out]
    text_deltas = [d for d in deltas_out if isinstance(d, dl.TextDelta)]
    assert "".join(d.text for d in text_deltas) == "hello world"
    assert "UsageFinal" in kinds
    assert "Finish" in kinds


def test_stream_reasoning_content():
    """Streaming reasoning_content produces ThinkingDelta."""
    ad = NimAdapter()
    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"reasoning_content": "thinking..."}}]}),
        json.dumps({"choices": [{"delta": {"content": "answer"}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))
    thinking = [d for d in deltas_out if isinstance(d, dl.ThinkingDelta)]
    assert len(thinking) == 1
    assert thinking[0].text == "thinking..."
    text_deltas = [d for d in deltas_out if isinstance(d, dl.TextDelta)]
    assert any(d.text == "answer" for d in text_deltas)


def test_stream_native_tool_block():
    """Native MiniMax tool markup in stream content -> structured ToolCall deltas."""
    ad = NimAdapter()
    # Set up tool context as the gateway would after encode_request.
    body = {
        "tools": [{"type": "function", "function": {
            "name": "get_weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }}],
    }
    ad.set_tool_context(body)

    # Build a native tool block: ]<]minimax[>[<invoke name="get_weather">...body...</invoke>
    invoke = (
        f'{_NAMESPACE}<invoke name="get_weather">'
        f'{_NAMESPACE}<city>London{_NAMESPACE}</city>'
        f'{_NAMESPACE}</invoke>'
    )
    full_block = _TOOL_BLOCK_START + invoke + _TOOL_BLOCK_END

    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"content": full_block}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))

    kinds = [type(d).__name__ for d in deltas_out]
    assert "ToolCallOpen" in kinds, f"got: {kinds}"
    assert "ToolCallArgsDelta" in kinds
    assert "ToolCallClose" in kinds
    assert "Finish" in kinds

    open_deltas = [d for d in deltas_out if isinstance(d, dl.ToolCallOpen)]
    assert len(open_deltas) == 1
    assert open_deltas[0].name == "get_weather"

    args_deltas = [d for d in deltas_out if isinstance(d, dl.ToolCallArgsDelta)]
    args_json = "".join(d.args_fragment for d in args_deltas)
    assert json.loads(args_json) == {"city": "London"}


def test_stream_native_tool_block_split_across_chunks():
    """A native tool block split across multiple stream chunks is reassembled."""
    ad = NimAdapter()
    body = {
        "tools": [{"type": "function", "function": {
            "name": "calc",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        }}],
    }
    ad.set_tool_context(body)

    invoke = (
        f'{_NAMESPACE}<invoke name="calc">'
        f'{_NAMESPACE}<x>42{_NAMESPACE}</x>'
        f'{_NAMESPACE}</invoke>'
    )
    full_block = _TOOL_BLOCK_START + invoke + _TOOL_BLOCK_END

    # Split the block into 3 arbitrary chunks.
    split1 = full_block[:10]
    split2 = full_block[10:30]
    split3 = full_block[30:]

    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"content": split1}}]}),
        json.dumps({"choices": [{"delta": {"content": split2}}]}),
        json.dumps({"choices": [{"delta": {"content": split3}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
        "[DONE]",
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))

    open_deltas = [d for d in deltas_out if isinstance(d, dl.ToolCallOpen)]
    assert len(open_deltas) == 1
    assert open_deltas[0].name == "calc"
    args_deltas = [d for d in deltas_out if isinstance(d, dl.ToolCallArgsDelta)]
    args_json = "".join(d.args_fragment for d in args_deltas)
    assert json.loads(args_json) == {"x": 42}


def test_stream_text_then_native_tool_block():
    """Visible text before a native tool block is emitted as TextDelta."""
    ad = NimAdapter()
    body = {
        "tools": [{"type": "function", "function": {
            "name": "fn",
            "parameters": {"type": "object", "properties": {}},
        }}],
    }
    ad.set_tool_context(body)

    invoke = f'{_NAMESPACE}<invoke name="fn">{_NAMESPACE}</invoke>'
    full_block = _TOOL_BLOCK_START + invoke + _TOOL_BLOCK_END

    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"content": "Calling tool: " + full_block}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
        "[DONE]",
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))

    text_deltas = [d for d in deltas_out if isinstance(d, dl.TextDelta)]
    assert "".join(d.text for d in text_deltas) == "Calling tool: "
    assert any(isinstance(d, dl.ToolCallOpen) for d in deltas_out)


def test_stream_structured_tool_calls_pass_through():
    """Standard OpenAI tool_calls deltas pass through without framer interference."""
    ad = NimAdapter()
    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1",
            "function": {"name": "search", "arguments": '{"q": "test"}'},
        }]}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))
    open_deltas = [d for d in deltas_out if isinstance(d, dl.ToolCallOpen)]
    assert len(open_deltas) == 1
    assert open_deltas[0].name == "search"
    args_deltas = [d for d in deltas_out if isinstance(d, dl.ToolCallArgsDelta)]
    assert json.loads(args_deltas[0].args_fragment) == {"q": "test"}


def test_stream_structured_tool_call_args_unaliased():
    """Aliased params (a param named 'type') are un-aliased on the structured path."""
    ad = NimAdapter()
    tools = [{"type": "function", "function": {
        "name": "create",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["a", "b"]},
                "name": {"type": "string"},
            },
            "required": ["type"],
        },
    }}]
    ad.set_tool_context({"tools": sanitize_nim_tool_schemas(tools)})

    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1",
            "function": {"name": "create", "arguments": '{"_nim_arg_t'},
        }]}}]}),
        json.dumps({"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "function": {"arguments": 'ype": "a", "name": "x"}'},
        }]}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))
    args_deltas = [d for d in deltas_out if isinstance(d, dl.ToolCallArgsDelta)]
    args_json = "".join(d.args_fragment for d in args_deltas)
    assert json.loads(args_json) == {"type": "a", "name": "x"}


def test_stream_namespace_in_plain_text_degrades_to_text():
    """A namespace-like string in plain text must not abort the stream."""
    ad = NimAdapter()
    # _NAMESPACE not followed by a tool-block start raises in the framer;
    # the adapter must degrade and emit the chunk as visible text.
    chunk = "echoing: " + _NAMESPACE + "oops not a tool"
    deltas_out = ad.decode_stream_event(
        "", json.dumps({"choices": [{"delta": {"content": chunk}}]}))
    text_deltas = [d for d in deltas_out if isinstance(d, dl.TextDelta)]
    assert "".join(d.text for d in text_deltas) == chunk


def test_stream_trailing_text_after_tool_block_degrades():
    """Text after a completed tool block degrades to text instead of StreamError."""
    ad = NimAdapter()
    invoke = f'{_NAMESPACE}<invoke name="fn">{_NAMESPACE}</invoke>'
    chunk = _TOOL_BLOCK_START + invoke + _TOOL_BLOCK_END + "Done!"
    deltas_out = ad.decode_stream_event(
        "", json.dumps({"choices": [{"delta": {"content": chunk}}]}))
    text_deltas = [d for d in deltas_out if isinstance(d, dl.TextDelta)]
    assert "Done!" in "".join(d.text for d in text_deltas)


def test_sanitize_if_then_else_boolean_subschemas():
    """Boolean subschemas under if/then/else must be stripped too."""
    tools = [{"type": "function", "function": {
        "name": "cond",
        "parameters": {
            "type": "object",
            "properties": {"mode": {"type": "string"}},
            "if": {"properties": {"mode": {"const": "x"}}},
            "then": True,
        },
    }}]
    sanitized = sanitize_nim_tool_schemas(tools)
    params = sanitized[0]["function"]["parameters"]
    assert "then" not in params
    assert params["if"] == {"properties": {"mode": {"const": "x"}}}


def test_decode_response_preserves_text_around_markup():
    """Non-streaming: visible text around a native block is kept, not wiped."""
    invoke = f'{_NAMESPACE}<invoke name="fn">{_NAMESPACE}</invoke>'
    block = _TOOL_BLOCK_START + invoke + _TOOL_BLOCK_END
    payload = {
        "choices": [{"message": {"role": "assistant",
                                "content": "See below: " + block},
                     "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    turn = NimAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.text == "See below:"
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "fn"


def test_decode_response_unaliases_structured_args():
    """Non-streaming: structured tool_calls args are un-aliased."""
    ad = NimAdapter()
    tools = [{"type": "function", "function": {
        "name": "create",
        "parameters": {
            "type": "object",
            "properties": {"type": {"type": "string"}},
        },
    }}]
    ad.set_tool_context({"tools": sanitize_nim_tool_schemas(tools)})
    payload = {
        "choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "function": {
                "name": "create", "arguments": '{"_nim_arg_type": "foo"}'}}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10},
    }
    turn = ad.decode_response(200, json.dumps(payload).encode())
    assert turn.tool_calls[0].args == {"type": "foo"}
    assert json.loads(turn.tool_calls[0].raw_args) == {"type": "foo"}


# -- framer unit tests ---------------------------------------------------------

def test_framer_plain_text():
    """Framer passes through plain text with no markers."""
    framer = MiniMaxFramer()
    assert framer.feed("hello world") == "hello world"
    assert framer.finish() == ""


def test_framer_holds_partial_marker():
    """Framer holds back text that might be the start of a marker."""
    framer = MiniMaxFramer()
    ns_half = _NAMESPACE[:len(_NAMESPACE) // 2]
    out1 = framer.feed("text" + ns_half)
    assert out1 == "text"
    # Feed the rest — it's not a tool block start, so the held text is flushed
    out2 = framer.feed(" more")
    assert ns_half in out2


def test_framer_separates_text_and_tool_block():
    """Framer emits visible text and captures the tool block."""
    framer = MiniMaxFramer()
    invoke = f'{_NAMESPACE}<invoke name="fn">{_NAMESPACE}</invoke>'
    block = _TOOL_BLOCK_START + invoke + _TOOL_BLOCK_END
    visible = framer.feed("before " + block)
    assert visible == "before "
    assert framer.tool_block == invoke


def test_framer_rejects_content_after_tool_block():
    """Content after the tool block end marker is an error."""
    import pytest
    framer = MiniMaxFramer()
    invoke = f'{_NAMESPACE}<invoke name="fn">{_NAMESPACE}</invoke>'
    block = _TOOL_BLOCK_START + invoke + _TOOL_BLOCK_END + "trailing"
    with pytest.raises(NimToolProtocolError):
        framer.feed(block)


def test_framer_drops_incomplete_marker_on_finish():
    """An incomplete marker at finish is dropped, not emitted as text."""
    framer = MiniMaxFramer()
    framer.feed("text " + _NAMESPACE)
    tail = framer.finish()
    # The namespace is in the tail so it's dropped
    assert tail == ""


# -- parser unit tests ---------------------------------------------------------

def test_parse_tool_block_simple():
    """Parse a simple invoke with one string argument."""
    block = (
        f'{_NAMESPACE}<invoke name="get_weather">'
        f'{_NAMESPACE}<city>London{_NAMESPACE}</city>'
        f'{_NAMESPACE}</invoke>'
    )
    schemas = {"get_weather": {"type": "object",
                               "properties": {"city": {"type": "string"}}}}
    calls = parse_tool_block(block, schemas, {})
    assert len(calls) == 1
    assert calls[0].name == "get_weather"
    assert calls[0].arguments == {"city": "London"}


def test_parse_tool_block_integer_coercion():
    """Integer arguments are coerced from text."""
    block = (
        f'{_NAMESPACE}<invoke name="calc">'
        f'{_NAMESPACE}<x>42{_NAMESPACE}</x>'
        f'{_NAMESPACE}</invoke>'
    )
    schemas = {"calc": {"type": "object",
                        "properties": {"x": {"type": "integer"}}}}
    calls = parse_tool_block(block, schemas, {})
    assert calls[0].arguments == {"x": 42}
    assert isinstance(calls[0].arguments["x"], int)


def test_parse_tool_block_boolean_coercion():
    """Boolean arguments are coerced from text."""
    block = (
        f'{_NAMESPACE}<invoke name="set">'
        f'{_NAMESPACE}<flag>true{_NAMESPACE}</flag>'
        f'{_NAMESPACE}</invoke>'
    )
    schemas = {"set": {"type": "object",
                       "properties": {"flag": {"type": "boolean"}}}}
    calls = parse_tool_block(block, schemas, {})
    assert calls[0].arguments == {"flag": True}


def test_parse_tool_block_unaliases_args():
    """Parser un-aliases unsafe param names when aliases are provided."""
    block = (
        f'{_NAMESPACE}<invoke name="create">'
        f'{_NAMESPACE}<_nim_arg_type>foo{_NAMESPACE}</_nim_arg_type>'
        f'{_NAMESPACE}</invoke>'
    )
    schemas = {"create": {"type": "object",
                          "properties": {"_nim_arg_type": {"type": "string"}}}}
    aliases = {"create": {"_nim_arg_type": "type"}}
    calls = parse_tool_block(block, schemas, aliases)
    assert calls[0].arguments == {"type": "foo"}


def test_parse_tool_block_nested_object():
    """Nested object arguments parse correctly."""
    block = (
        f'{_NAMESPACE}<invoke name="create_user">'
        f'{_NAMESPACE}<profile>'
        f'{_NAMESPACE}<name>Alice{_NAMESPACE}</name>'
        f'{_NAMESPACE}<age>30{_NAMESPACE}</age>'
        f'{_NAMESPACE}</profile>'
        f'{_NAMESPACE}</invoke>'
    )
    schemas = {"create_user": {"type": "object", "properties": {
        "profile": {"type": "object", "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        }},
    }}}
    calls = parse_tool_block(block, schemas, {})
    assert calls[0].arguments == {"profile": {"name": "Alice", "age": 30}}


def test_parse_tool_block_array():
    """Array arguments parse correctly."""
    block = (
        f'{_NAMESPACE}<invoke name="search">'
        f'{_NAMESPACE}<tags>'
        f'{_NAMESPACE}<item>python{_NAMESPACE}</item>'
        f'{_NAMESPACE}<item>testing{_NAMESPACE}</item>'
        f'{_NAMESPACE}</tags>'
        f'{_NAMESPACE}</invoke>'
    )
    schemas = {"search": {"type": "object", "properties": {
        "tags": {"type": "array", "items": {"type": "string"}},
    }}}
    calls = parse_tool_block(block, schemas, {})
    assert calls[0].arguments == {"tags": ["python", "testing"]}


def test_parse_tool_block_multiple_invokes():
    """Multiple invokes in one block produce multiple calls."""
    block = (
        f'{_NAMESPACE}<invoke name="a">{_NAMESPACE}</invoke>'
        f'{_NAMESPACE}<invoke name="b">{_NAMESPACE}</invoke>'
    )
    calls = parse_tool_block(block, {}, {})
    assert len(calls) == 2
    assert calls[0].name == "a"
    assert calls[1].name == "b"
    assert calls[0].index == 0
    assert calls[1].index == 1


def test_parse_tool_block_empty_raises():
    """An empty tool block raises NimToolProtocolError."""
    import pytest
    with pytest.raises(NimToolProtocolError):
        parse_tool_block("", {}, {})


def test_parse_tool_block_malformed_raises():
    """Malformed invoke tags raise NimToolProtocolError."""
    import pytest
    with pytest.raises(NimToolProtocolError):
        parse_tool_block("not a tool block", {}, {})


def test_parse_tool_block_bad_typed_value_raises_protocol_error():
    """An argument value that fails type coercion raises NimToolProtocolError,
    not a bare ValueError/JSONDecodeError that would escape callers."""
    import pytest
    block = (
        f'{_NAMESPACE}<invoke name="calc">'
        f'{_NAMESPACE}<x>not-a-number{_NAMESPACE}</x>'
        f'{_NAMESPACE}</invoke>'
    )
    schemas = {"calc": {"type": "object",
                        "properties": {"x": {"type": "integer"}}}}
    with pytest.raises(NimToolProtocolError):
        parse_tool_block(block, schemas, {})


def test_emit_native_calls_bad_value_degrades_to_text():
    """Malformed typed arguments degrade to a TextDelta, not a stream abort."""
    ad = NimAdapter()
    body = {
        "tools": [{"type": "function", "function": {
            "name": "calc",
            "parameters": {"type": "object",
                           "properties": {"x": {"type": "integer"}}},
        }}],
    }
    ad.set_tool_context(body)
    block = (
        f'{_NAMESPACE}<invoke name="calc">'
        f'{_NAMESPACE}<x>oops{_NAMESPACE}</x>'
        f'{_NAMESPACE}</invoke>'
    )
    deltas_out = ad.decode_stream_event(
        "",
        json.dumps({"choices": [{"delta": {
            "content": _TOOL_BLOCK_START + block + _TOOL_BLOCK_END}}]}))
    assert any(isinstance(d, dl.TextDelta) for d in deltas_out)
    assert not any(isinstance(d, dl.ToolCallOpen) for d in deltas_out)


# -- cross-dialect: Anthropic client -> NIM provider ---------------------------

def test_cross_dialect_anthropic_to_nim():
    """Claude Code sends thinking budget; NIM receives reasoning_budget."""
    req = am.decode_request({
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": "think hard"}],
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "stream": True,
    })
    body = NimAdapter().encode_request(req, "nvidia/nemotron-3-super-120b-a12b",
                                       {"provider_type": "nvidia-nim"})
    ctk = body["extra_body"]["chat_template_kwargs"]
    assert ctk["reasoning_budget"] == 10000
    assert ctk["thinking"] is True
    assert "reasoning_effort" not in body


def test_cross_dialect_openai_to_nim():
    """OpenAI client sends reasoning_effort; NIM receives chat_template_kwargs."""
    req = oc.decode_request({
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "messages": [{"role": "user", "content": "think"}],
        "reasoning_effort": "medium",
        "stream": True,
    })
    body = NimAdapter().encode_request(req, "nvidia/nemotron-3-super-120b-a12b",
                                       {"provider_type": "nvidia-nim"})
    ctk = body["extra_body"]["chat_template_kwargs"]
    assert ctk["thinking"] is True
    # medium -> 8000 per the IR effort->budget map
    assert ctk["reasoning_budget"] == 8000
    assert "reasoning_effort" not in body


# -- catalog entry -----------------------------------------------------------

def test_catalog_has_nim_entry():
    """BUILTIN_PROVIDER_TYPES includes nvidia-nim with correct base URL."""
    from wiwi.router.router import BUILTIN_PROVIDER_TYPES
    nim = next(p for p in BUILTIN_PROVIDER_TYPES
               if p["provider_type"] == "nvidia-nim")
    assert nim["default_base_url"] == "https://integrate.api.nvidia.com/v1"
    assert "nemotron" in nim["latest_models"][0]


def test_config_provider_types_includes_nim():
    from wiwi.config import PROVIDER_TYPES
    assert "nvidia-nim" in PROVIDER_TYPES
