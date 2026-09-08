"""Round-30 regression tests: Anthropic type-less block, Responses error shape.

Two defects at the inbound-dialect boundary:

1. ``wire/anthropic_messages.decode_request`` dispatched content blocks with
   ``btype = b.get("type")`` and then reached for
   ``btype.endswith("_tool_result")`` to cover Anthropic's server-tool result
   family. A block with no ``type`` key (or a non-string one) made that call
   raise ``AttributeError`` — which ``run_chat_like``'s
   ``except (DialectError, ValueError)`` does not catch — so one malformed
   block turned a client request into a gateway 500. Every sibling branch
   compares with ``==``; only this line dereferenced the value, and the guard
   two lines above it already states the module's policy: "skip rather than
   500 on .get".

2. ``app._err`` collapsed every non-Anthropic surface onto ``oc.error_body``,
   so ``/v1/responses`` errors came back in Chat Completions shape and
   ``orp.error_body`` — which the Responses route dutifully passed into
   ``run_chat_like`` — was never called. The Responses API documents
   ``{"error": {message, type, param, code}}``; ``param`` was missing. The
   same path-inference gap made ``/v1/messages/count_tokens`` (which does not
   *end* with ``/messages``) answer body errors in Chat shape to an Anthropic
   client.
"""

from __future__ import annotations

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
from wiwi.server.app import create_app
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_responses as orp

H = {"Authorization": "Bearer sk-wiwi-master-test"}

OPENAI_BODY = {
    "id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


# -- 1. type-less content blocks must not 500 ---------------------------------

def test_am_block_without_type_is_skipped_not_fatal():
    """A content block missing ``type`` is skipped; its siblings still decode."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": [
            {"text": "type-less block"},
            {"type": "text", "text": "kept"},
        ]}],
    })
    parts = req.messages[0].parts
    assert len(parts) == 1
    assert isinstance(parts[0], ir.TextPart)
    assert parts[0].text == "kept"


def test_am_block_with_non_string_type_is_skipped_not_fatal():
    """A numeric ``type`` has no ``.endswith`` either — same crash, same guard."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": [
            {"type": 7, "text": "junk"},
            {"type": "text", "text": "kept"},
        ]}],
    })
    assert [p.text for p in req.messages[0].parts] == ["kept"]


def test_am_typeless_block_in_assistant_turn_does_not_drop_the_turn():
    """Type-less block in an assistant turn: the well-formed sibling still lands."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                {"id": "toolu_1", "name": "get_weather", "input": {"city": "SF"}},
                {"type": "tool_use", "id": "toolu_2", "name": "get_time",
                 "input": {"tz": "UTC"}},
            ]},
        ],
    })
    turn = req.messages[1]
    assert [p.name for p in turn.parts if isinstance(p, ir.ToolUsePart)] == ["get_time"]


def test_am_plain_tool_result_still_decodes():
    """The guard must not narrow the branch it was written beside."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "sunny"},
        ]}],
    })
    p = req.messages[0].parts[0]
    assert isinstance(p, ir.ToolResultPart)
    assert (p.tool_use_id, p.content, p.block_type) == ("toolu_1", "sunny", "tool_result")


def test_am_server_tool_result_family_still_decodes():
    """``*_tool_result`` suffix matching must survive the isinstance guard."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": [
            {"type": "web_search_tool_result", "tool_use_id": "srvu_1",
             "content": [{"type": "text", "text": "a result"}]},
        ]}],
    })
    p = req.messages[0].parts[0]
    assert isinstance(p, ir.ToolResultPart)
    assert p.block_type == "web_search_tool_result"
    assert p.content == "a result"


@respx.mock
async def test_messages_endpoint_survives_typeless_block(client):
    """End to end: the request reaches the upstream instead of 500-ing."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_BODY)
    r = await client.post("/v1/messages", json={
        "model": "gpt-4o", "max_tokens": 100,
        "messages": [{"role": "user", "content": [{"text": "no type key"}]}],
    }, headers={"x-api-key": "sk-wiwi-master-test"})
    assert r.status_code == 200, r.text


# -- 2. each surface's errors must be its own dialect's shape -----------------

def _assert_responses_error(data: dict, etype: str) -> None:
    assert data["error"]["type"] == etype, data
    assert data["error"]["param"] is None, f"missing param: {data}"


async def test_responses_bad_json_is_responses_shaped(client):
    """Body-parse failures happen before the codec runs, so they lean on path
    inference — which had no case for /v1/responses."""
    r = await client.post("/v1/responses", content=b"{not json",
                          headers={**H, "content-type": "application/json"})
    assert r.status_code == 400, r.text
    _assert_responses_error(r.json(), "invalid_request_error")


async def test_responses_non_object_body_is_responses_shaped(client):
    r = await client.post("/v1/responses", json=[1, 2, 3], headers=H)
    assert r.status_code == 400, r.text
    _assert_responses_error(r.json(), "invalid_request_error")


async def test_responses_unknown_model_is_responses_shaped(client):
    r = await client.post("/v1/responses", json={"model": "nope", "input": "hi"},
                          headers=H)
    assert r.status_code == 404, r.text
    _assert_responses_error(r.json(), "not_found_error")


async def test_responses_missing_key_is_responses_shaped(client):
    r = await client.post("/v1/responses", json={"model": "gpt-4o", "input": "hi"})
    assert r.status_code == 401, r.text
    _assert_responses_error(r.json(), "authentication_error")


async def test_responses_error_body_matches_module(client):
    """The shape the route advertises is the shape the server returns."""
    r = await client.post("/v1/responses", json={"model": "nope", "input": "hi"},
                          headers=H)
    msg = r.json()["error"]["message"]
    assert r.json() == orp.error_body(404, "not_found_error", msg)


async def test_chat_errors_keep_openai_shape(client):
    """Unchanged: Chat's error body has no ``param`` key."""
    r = await client.post("/v1/chat/completions",
                          json={"model": "nope",
                                "messages": [{"role": "user", "content": "hi"}]},
                          headers=H)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["type"] == "not_found_error"
    assert "param" not in r.json()["error"]


async def test_messages_errors_keep_anthropic_shape(client):
    r = await client.post("/v1/messages", json={"model": "nope", "max_tokens": 10,
                                                "messages": [{"role": "user",
                                                              "content": "hi"}]},
                          headers={"x-api-key": "sk-wiwi-master-test"})
    assert r.status_code == 404, r.text
    data = r.json()
    assert data["type"] == "error"
    assert data["error"]["type"] == "not_found_error"


async def test_count_tokens_errors_are_anthropic_shaped(client):
    """``/v1/messages/count_tokens`` does not *end* with ``/messages``, so the
    path inference missed the Anthropic dialect for it too."""
    r = await client.post("/v1/messages/count_tokens", content=b"{not json",
                          headers={"content-type": "application/json",
                                   "x-api-key": "sk-wiwi-master-test"})
    assert r.status_code == 400, r.text
    data = r.json()
    assert data.get("type") == "error", data
    assert data["error"]["type"] == "invalid_request_error"
