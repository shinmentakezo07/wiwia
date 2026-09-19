"""Round 87: the OpenCode Zen transport never declared ``forceStream``.

Zen answers as an event stream, so a request that asks for a single JSON body
does not get one — the reply arrives as SSE and the plain (non-pump) decode
path reads no deltas at all, handing the caller an empty ``chat.completion``
with zero usage. The official client's provider entry says this out loud on the
transport::

    transport: { baseUrl: "https://opencode.ai", forceStream: true, ... }

``OpencodeAdapter`` is the wiwi transport for the same gateway, and it shipped
``force_stream = False``: the declaration was missing, so a non-streaming
caller was routed to ``Gateway._call_once`` (JSON decode) instead of
``_complete_via_stream`` (SSE pump + reassembly) — the path Cline and
WorkBuddy take because their upstreams are streaming-only too.

Declaring the flag alone is not enough. The pump asks ``build_url`` for the
streaming URL but encodes the *client's* request, whose ``stream`` field is
still false, so the upstream would be told "don't stream" on a connection the
gateway then parses as SSE. Both halves land here, exactly as in
``cline_adapter.encode_request`` / ``workbuddy_adapter.encode_request``:

- ``force_stream = True`` on the adapter (the transport declaration), and
- ``encode_request`` forcing ``stream: true`` on every body that carries the
  field — chat, responses and messages. The Gemini route is excluded: its wire
  is selected by the URL (``:streamGenerateContent?alt=sse``), and a ``stream``
  key in a ``generateContent`` body is an unknown field the endpoint rejects.

Pinned below: the declaration, the per-route body force, the Gemini body/URL
split, and two gateway end-to-end reassembly cases — the free-tier Responses
model the bug was reported against, plus a free chat model — with a streaming
client as the control that the pump still streams.
"""

from __future__ import annotations

import time

import httpx
import orjson
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.ir import types as ir
from wiwi.providers import opencode_version as ov
from wiwi.providers.opencode_adapter import OpencodeAdapter, route_for_model
from wiwi.providers.registry import get_adapter
from wiwi.server.app import create_app
from wiwi.wire import openai_chat as oc

ZEN = "https://opencode.ai/zen/v1"
_SSE = {"Content-Type": "text/event-stream"}


def _chat_req(text: str = "hi") -> ir.Request:
    # A non-streaming client: this is the request shape the bug hides behind.
    return oc.decode_request({"model": "m",
                              "messages": [{"role": "user", "content": text}]})


@pytest.fixture(autouse=True)
def _seed_version():
    ov._set_cached_for_tests("1.18.31", time.monotonic())
    yield
    ov._set_cached_for_tests(None, 0.0)


# -- the transport declaration --------------------------------------------------


def test_transport_declares_force_stream():
    assert OpencodeAdapter.force_stream is True
    # Hot path uses fresh_adapter; the admin/shared singleton reads the same
    # attribute, so both must agree.
    assert getattr(get_adapter("opencode"), "force_stream", False) is True


def test_declaration_is_scoped_to_zen():
    # Zen's own transport forces SSE; the shared OpenAI wire it delegates to
    # must not inherit that, or every openai/openai-compatible provider would
    # silently switch to the pump too.
    assert getattr(get_adapter("openai"), "force_stream", False) is False


# -- the body must ask for the stream the pump parses ---------------------------


@pytest.mark.parametrize("model,route", [
    ("mimo-v2.5-free", "chat"),
    ("glm-5.3-flash", "chat"),
    ("muse-spark-1.3-contributor-free", "responses"),
    ("gpt-5.5", "responses"),
    ("union-alpha", "messages"),
    ("claude-sonnet-5", "messages"),
])
def test_encode_request_forces_sse_even_for_a_non_streaming_client(model, route):
    assert route_for_model(model) == route  # control: the route table moved
    a = OpencodeAdapter()
    body = a.encode_request(_chat_req(), model, {})
    assert body["stream"] is True


def test_gemini_body_has_no_stream_field_but_the_url_streams():
    # Gemini selects the wire from the URL; a `stream` key in a
    # generateContent body is an unknown field the endpoint rejects.
    a = OpencodeAdapter()
    body = a.encode_request(_chat_req(), "gemini-3-flash", {})
    assert "stream" not in body
    assert a.build_url(ZEN, "gemini-3-flash", True) == (
        f"{ZEN}/models/gemini-3-flash:streamGenerateContent?alt=sse")
    # build_url itself stays honest about the flag it is handed: only the
    # transport's force_stream decides that the pump always passes True.
    assert a.build_url(ZEN, "gemini-3-flash", False) == (
        f"{ZEN}/models/gemini-3-flash:generateContent")


# -- end-to-end: a non-streaming caller gets one aggregated completion ----------


def _config(*, model_id: str, group: str) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="zen", provider="opencode", base_url=ZEN,
                               keys=[KeyDef(label="main", key="sk-zen-real-abc")])],
        model_list=[ModelEntry(model_name=group,
                               wiwi_params=DeploymentParams(provider="zen",
                                                            model=model_id))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0),
    )


async def _client(config: WiwiConfig):
    app = create_app(config)
    transport = httpx.ASGITransport(app=app)
    return LifespanManager(app), httpx.AsyncClient(
        transport=transport, base_url="http://test",
        headers={"Authorization": "Bearer sk-wiwi-master-test"})


_RESPONSES_SSE = b"".join(
    b"data: " + orjson.dumps(e) + b"\n\n" for e in [
        {"type": "response.created",
         "response": {"id": "resp_1", "status": "in_progress", "output": []}},
        {"type": "response.output_text.delta", "delta": "hello"},
        {"type": "response.output_text.done", "text": "hello"},
        {"type": "response.completed",
         "response": {"status": "completed",
                      "usage": {"input_tokens": 7, "output_tokens": 3}}},
    ]) + b"data: [DONE]\n\n"

_CHAT_SSE = b"".join([
    (b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
     b'[{"index":0,"delta":{"role":"assistant","content":"hel"},"finish_reason":null}]}\n\n'),
    (b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
     b'[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}],'
     b'"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'),
    b"data: [DONE]\n\n"])


@respx.mock
async def test_free_tier_responses_model_aggregates_for_a_non_streaming_caller():
    """The reported shape: `muse-spark-1.3-contributor-free`, stream omitted.

    Pre-fix the JSON decode path saw an SSE body, produced no text and priced
    nothing; now the pump folds the stream into one completion.
    """
    mgr, client = await _client(_config(model_id="muse-spark-1.3-contributor-free",
                                        group="zen-resp"))
    route = respx.post(f"{ZEN}/responses").respond(content=_RESPONSES_SSE,
                                                   headers=_SSE)
    async with mgr, client:
        r = await client.post("/v1/chat/completions", json={
            "model": "zen-resp", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert route.called
    assert orjson.loads(route.calls[0].request.content)["stream"] is True
    choice = r.json()["choices"][0]
    assert choice["message"]["content"] == "hello"
    assert choice["finish_reason"] == "stop"
    # The Responses usage frame is the source: 7 in / 3 out, no invention.
    assert r.json()["usage"]["prompt_tokens"] == 7
    assert r.json()["usage"]["completion_tokens"] == 3


@respx.mock
async def test_free_tier_chat_model_aggregates_for_a_non_streaming_caller():
    mgr, client = await _client(_config(model_id="mimo-v2.5-free",
                                        group="zen-chat"))
    route = respx.post(f"{ZEN}/chat/completions").respond(content=_CHAT_SSE,
                                                           headers=_SSE)
    async with mgr, client:
        r = await client.post("/v1/chat/completions", json={
            "model": "zen-chat", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert route.called
    body = orjson.loads(route.calls[0].request.content)
    assert body["stream"] is True
    # A non-streaming client never asked for stream_options, and without it
    # Zen's chat route omits the usage frame, so the aggregated turn would
    # price on the estimator. The forced-stream path adds it (probed live:
    # Zen accepts the field and answers with a real usage frame).
    assert body["stream_options"] == {"include_usage": True}
    assert r.json()["choices"][0]["message"]["content"] == "hello"


@respx.mock
async def test_streaming_client_is_still_streamed():
    """Control: force_stream must not buffer a client that asked for SSE."""
    mgr, client = await _client(_config(model_id="mimo-v2.5-free",
                                        group="zen-chat"))
    route = respx.post(f"{ZEN}/chat/completions").respond(content=_CHAT_SSE,
                                                           headers=_SSE)
    async with mgr, client:
        r = await client.post("/v1/chat/completions", json={
            "model": "zen-chat", "messages": [{"role": "user", "content": "hi"}],
            "stream": True})
    assert r.status_code == 200, r.text
    assert route.called
    assert r.headers["content-type"].startswith("text/event-stream")
    text = "".join(orjson.loads(line[6:])["choices"][0]["delta"].get("content", "")
                   for line in r.text.splitlines()
                   if line.startswith("data: ") and line != "data: [DONE]")
    assert text == "hello"
