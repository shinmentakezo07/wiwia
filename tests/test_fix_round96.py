"""Regression tests for OpenRouter request translation.

The first slice exercises the real two-turn boundary: OpenRouter returns a
truncated tool-argument object, wiwi repairs it for the caller, the caller
replays that tool call, and the next OpenRouter request must contain valid
JSON rather than the original truncated text.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.ir import types as ir
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.opencode_adapter import _encode_responses_request
from wiwi.providers.openrouter_adapter import OpenRouterAdapter
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orr


def _truncated_tool_turn() -> ir.AssistantTurn:
    return ir.AssistantTurn(tool_calls=[ir.ToolUsePart(
        id="call_1", name="Read", args={"path": "/tmp/notes.txt"},
        raw_args='{"path":"/tmp/notes.txt"',
    )])


def test_truncated_openrouter_response_round_trips_valid_arguments():
    adapter = OpenRouterAdapter()
    upstream = {
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "arguments": '{"path":"/tmp/notes.txt"',
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    }

    turn = adapter.decode_response(200, json.dumps(upstream).encode())
    client_response = oc.encode_response(
        ctx=None, turn=turn, model="stealth/ox-alpha", req_id="r1")
    replay = oc.decode_request({
        "model": "stealth/ox-alpha",
        "messages": [
            {"role": "user", "content": "Read the file"},
            client_response["choices"][0]["message"],
            {"role": "tool", "tool_call_id": "call_1", "content": "contents"},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "Read",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        }],
    })
    outbound = adapter.encode_request(
        replay, "stealth/ox-alpha", {"provider_type": "openrouter"})

    call = outbound["messages"][1]["tool_calls"][0]
    assert call["function"]["arguments"] == '{"path": "/tmp/notes.txt"}'
    assert json.loads(call["function"]["arguments"]) == {"path": "/tmp/notes.txt"}


def test_repaired_tool_arguments_are_returned_as_valid_json_to_client():
    response = oc.encode_response(
        ctx=None, turn=_truncated_tool_turn(), model="stealth/ox-alpha", req_id="r1")

    arguments = response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"path": "/tmp/notes.txt"}


def test_ir_repaired_tool_arguments_replay_as_valid_json_to_openrouter():
    replay = ir.Request(model="stealth/ox-alpha", messages=[
        ir.Message(role="assistant", parts=[_truncated_tool_turn().tool_calls[0]]),
    ])
    outbound = OpenRouterAdapter().encode_request(
        replay, "stealth/ox-alpha", {"provider_type": "openrouter"})

    arguments = outbound["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"path": "/tmp/notes.txt"}


def test_responses_response_encodes_repaired_tool_arguments_as_valid_json():
    from wiwi.wire import openai_responses as responses

    response = responses.encode_response(
        ctx=None, turn=_truncated_tool_turn(), model="stealth/ox-alpha", req_id="r1")

    arguments = response["output"][0]["arguments"]
    assert json.loads(arguments) == {"path": "/tmp/notes.txt"}


def test_openai_repaired_tool_arguments_replay_as_valid_json():
    req = ir.Request(model="gpt-4o", messages=[
        ir.Message(role="assistant", parts=[_truncated_tool_turn().tool_calls[0]]),
    ])
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {"provider_type": "openai"})
    arguments = body["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"path": "/tmp/notes.txt"}



@pytest.mark.parametrize("effort", ["hight", True, ["high"]])
def test_openai_invalid_reasoning_effort_is_omitted(effort):
    req = ir.Request(
        model="gpt-4o",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort=effort),
    )
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {"provider_type": "openai"})
    assert "reasoning_effort" not in body


def test_opencode_repaired_tool_arguments_replay_as_valid_json():
    req = ir.Request(model="zen", messages=[
        ir.Message(role="assistant", parts=[_truncated_tool_turn().tool_calls[0]]),
    ])
    body = _encode_responses_request(req, "zen", {})
    arguments = body["input"][0]["arguments"]
    assert json.loads(arguments) == {"path": "/tmp/notes.txt"}


def test_openrouter_error_metadata_shape_never_raises():
    from wiwi.providers.base import error_from_provider_status

    for metadata in ("bad", 5, [], None, {"raw": 5}):
        body = json.dumps({"error": {
            "message": "Provider returned error", "metadata": metadata,
        }})
        error = error_from_provider_status(400, body, "openrouter")
        assert "Provider returned error" in error.message


@pytest.mark.parametrize("usage", ["bad", 5, True, []])
def test_openrouter_sync_typed_wrong_usage_does_not_raise(usage):
    turn = OpenRouterAdapter().decode_response(
        200, json.dumps({"choices": [], "usage": usage}).encode())
    assert turn.usage.prompt_tokens == 0


@pytest.mark.parametrize("function", [None, "bad", 5, []])
def test_openrouter_sync_typed_wrong_tool_function_does_not_raise(function):
    turn = OpenRouterAdapter().decode_response(200, json.dumps({
        "choices": [{"message": {"tool_calls": [{
            "id": "call_1", "function": function,
        }]}}],
    }).encode())
    assert [tc.name for tc in turn.tool_calls] == [""]


def test_openrouter_sync_array_content_flattens_text_parts():
    turn = OpenRouterAdapter().decode_response(200, json.dumps({
        "choices": [{"message": {"content": [
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ]}}],
    }).encode())
    assert turn.text == "hello world"


@pytest.mark.parametrize("effort", ["hight", True, ["high"]])
def test_openrouter_invalid_reasoning_effort_is_omitted(effort):
    request = ir.Request(
        model="stealth/ox-alpha",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort=effort),
    )
    body = OpenRouterAdapter().encode_request(
        request, "stealth/ox-alpha", {"provider_type": "openrouter"})
    assert "reasoning" not in body


def test_openrouter_sync_reasoning_details_coerce_non_string_text():
    turn = OpenRouterAdapter().decode_response(200, json.dumps({
        "choices": [{"message": {"reasoning_details": [
            {"type": "reasoning.text", "text": 5, "signature": 7},
            {"type": "reasoning.summary", "summary": True},
            {"type": "reasoning.encrypted", "data": [], "id": 9},
        ]}}],
    }).encode())
    assert [(part.text, part.signature) for part in turn.thinking] == [
        ("", None), ("", None), ("", None),
    ]
    response = oc.encode_response(ctx=None, turn=turn, model="m", req_id="r")
    assert response["choices"][0]["message"]["reasoning_content"] == ""


def test_openrouter_stream_reasoning_details_drop_non_string_text():
    out = OpenRouterAdapter().decode_stream_event("", json.dumps({
        "choices": [{"delta": {"reasoning_details": [
            {"type": "reasoning.text", "text": 5, "signature": 7},
            {"type": "reasoning.summary", "summary": True},
            {"type": "reasoning.encrypted", "data": [], "id": 9},
        ]}}],
    }))
    assert not any(isinstance(delta, dl.ThinkingDelta) for delta in out)


@pytest.mark.parametrize("value", [None, 5, ["bad"]])
def test_openrouter_sync_tool_id_and_name_are_coerced(value):
    turn = OpenRouterAdapter().decode_response(200, json.dumps({
        "choices": [{"message": {"tool_calls": [{
            "id": value, "function": {"name": value, "arguments": "{}"},
        }]}}],
    }).encode())
    assert (turn.tool_calls[0].id, turn.tool_calls[0].name) == ("", "")


@pytest.mark.parametrize("value", [None, 5, ["bad"]])
def test_openrouter_stream_tool_id_and_name_are_coerced(value):
    out = OpenRouterAdapter().decode_stream_event("", json.dumps({
        "choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": value, "function": {"name": value},
        }]}, "finish_reason": "tool_calls"}],
    }))
    assert not any(isinstance(delta, dl.ToolCallOpen) for delta in out)


def test_openrouter_hosted_tool_choice_uses_server_tool_type():
    req = orr.decode_request({
        "model": "openai/gpt-5",
        "input": "Search for current news",
        "tools": [{"type": "web_search"}],
        "tool_choice": {"type": "web_search"},
    })
    body = OpenRouterAdapter().encode_request(req, "openai/gpt-5", {})
    assert body["tool_choice"] == {"type": "openrouter:web_search"}


def test_openrouter_client_function_named_web_search_stays_function_choice():
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="web_search", parameters_json_schema={"type": "object"})],
        tool_choice=ir.ToolChoiceNamed("web_search"),
    )
    body = OpenRouterAdapter().encode_request(req, "m", {})
    assert body["tool_choice"] == {
        "type": "function", "function": {"name": "web_search"},
    }


def test_openrouter_reasoning_budget_stays_below_completion_limit():
    req = ir.Request(
        model="anthropic/claude-sonnet-4",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(max_tokens=1000, thinking_budget=8000),
    )
    body = OpenRouterAdapter().encode_request(req, "anthropic/claude-sonnet-4", {})
    assert body["reasoning"]["max_tokens"] < body["max_completion_tokens"]


@respx.mock
async def test_openrouter_truncated_tool_replay_through_asgi_is_valid_json():
    config = WiwiConfig(
        providers=[ProviderDef(
            name="openrouter", provider="openrouter",
            base_url="https://openrouter.test/api/v1",
            keys=[KeyDef(label="test", key="test-key")],
        )],
        model_list=[ModelEntry(
            model_name="test-model",
            wiwi_params=DeploymentParams(provider="openrouter", model="test-model"),
        )],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:",
        ),
    )
    route = respx.post("https://openrouter.test/api/v1/chat/completions")
    route.side_effect = [
        httpx.Response(200, json={
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "Read", "arguments": '{"path":"/tmp/notes.txt"'},
                }]},
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }),
        httpx.Response(200, json={
            "choices": [{
                "index": 0, "message": {"role": "assistant", "content": "done"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 8, "completion_tokens": 1},
        }),
    ]
    headers = {"Authorization": "Bearer sk-wiwi-master-test"}
    app = create_app(config)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/v1/chat/completions", json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Read the file"}],
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "parameters": {"type": "object", "properties": {
                            "path": {"type": "string"},
                        }},
                    },
                }],
            }, headers=headers)
            assert first.status_code == 200, first.text
            assistant = first.json()["choices"][0]["message"]
            second = await client.post("/v1/chat/completions", json={
                "model": "test-model",
                "messages": [
                    {"role": "user", "content": "Read the file"},
                    assistant,
                    {"role": "tool", "tool_call_id": "call_1", "content": "contents"},
                ],
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "parameters": {"type": "object", "properties": {
                            "path": {"type": "string"},
                        }},
                    },
                }],
            }, headers=headers)

    assert second.status_code == 200, second.text
    outbound = json.loads(route.calls[1].request.content)
    arguments = outbound["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"path": "/tmp/notes.txt"}
