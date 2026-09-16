"""Round-29 regression tests: OpenCode Zen client metadata headers.

Zen gates its free tier behind the official client's session header: the edge
rejects with ``400 MissingSessionID`` ("OpenCode's free tier can only be used
in OpenCode") when it is absent (verified live 2026-09-07, re-verified
2026-09-16 on ``union-alpha``). The real client
(``packages/opencode/src/session/llm/request.ts``) sends the full metadata set
on every request to an ``opencode``-provider model, paid or free:

- ``x-opencode-session`` — per-session id
- ``x-opencode-request`` — per-request id
- ``x-opencode-client`` — ``cli``
- ``x-opencode-project`` — project id (the CLI's ``global`` fallback)

Zen consumes them for metrics and sticky routing on every model, so the
adapter sends all four unconditionally. Round 29 originally gated them to
free models only; that left paid traffic without the client fingerprint and
sent ``union-alpha`` (free, Messages route) to the wrong endpoint entirely
(see round 63).

These tests pin:

- every model gets the four metadata headers, anonymous key or real key
- the ids are stable across header rebuilds within one request (the
  401-refresh retry path rebuilds headers) and fresh per request instance
- ``reset()`` rotates both ids
- the gateway end-to-end sends them upstream, streaming included
"""

from __future__ import annotations

import time

import httpx
import orjson
import pytest
import pytest_asyncio
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
from wiwi.providers import opencode_version as ov
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.opencode_adapter import OpencodeAdapter
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire import openai_chat as oc


def _chat_req(text: str = "hi") -> ir.Request:
    body = {"model": "m", "messages": [{"role": "user", "content": text}]}
    return oc.decode_request(body)


def _key(secret: str = "zen-key-123") -> ProviderKeyRef:
    return ProviderKeyRef(label="main", secret=secret)


@pytest.fixture(autouse=True)
def _seed_version():
    ov._set_cached_for_tests("9.9.9", time.monotonic())
    yield
    ov._set_cached_for_tests(None, 0.0)


# -- client metadata headers (every model) --------------------------------------


def _metadata(h: dict[str, str]) -> dict[str, str]:
    return {k: h[k] for k in ("x-opencode-session", "x-opencode-request",
                              "x-opencode-client", "x-opencode-project")}


def test_every_model_carries_client_metadata():
    # Paid and free alike: the CLI sends the full set on every opencode-model
    # request, and Zen reads it for metrics/sticky routing.
    for m in ["mimo-v2.5-free", "big-pickle", "glm-5.3-flash", "kimi-k2.6",
              "claude-sonnet-5", "gemini-3.1-pro", "gpt-5.5", "union-alpha"]:
        a = OpencodeAdapter()
        a.encode_request(_chat_req(), m, {})
        h = a.headers(_key())
        assert h["User-Agent"] == "opencode/9.9.9", m
        assert h["x-opencode-client"] == "cli", m
        assert h["x-opencode-project"] == "global", m
        assert h["x-opencode-session"].startswith("ses_"), m
        assert h["x-opencode-request"].startswith("msg_"), m
        assert h["x-opencode-session"] != h["x-opencode-request"], m


def test_headers_before_any_model_known_still_carry_metadata():
    # The admin test-connection path calls headers() with no model learned;
    # the client fingerprint does not depend on the model, so it is present.
    a = OpencodeAdapter()
    h = a.headers(_key())
    assert h["x-opencode-client"] == "cli"
    assert h["x-opencode-session"].startswith("ses_")
    assert h["x-opencode-request"].startswith("msg_")
    assert h["User-Agent"] == "opencode/9.9.9"


def test_ids_stable_across_header_rebuilds():
    # The 401-refresh retry path rebuilds headers on the same adapter
    # instance; neither id may change mid-request.
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "mimo-v2.5-free", {})
    first = _metadata(a.headers(_key()))
    second = _metadata(a.headers(_key()))
    assert first == second


def test_ids_fresh_per_request_instance():
    # Each request gets its own fresh adapter (fresh_adapter on the hot path);
    # a bad Zen replica must never be sticky across requests.
    a1 = OpencodeAdapter()
    a1.encode_request(_chat_req(), "mimo-v2.5-free", {})
    a2 = OpencodeAdapter()
    a2.encode_request(_chat_req(), "mimo-v2.5-free", {})
    assert (a1.headers(_key())["x-opencode-session"]
            != a2.headers(_key())["x-opencode-session"])
    assert (a1.headers(_key())["x-opencode-request"]
            != a2.headers(_key())["x-opencode-request"])


def test_reset_rotates_both_ids():
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "mimo-v2.5-free", {})
    before = _metadata(a.headers(_key()))
    a.reset()
    after = _metadata(a.headers(_key()))
    assert after["x-opencode-session"] != before["x-opencode-session"]
    assert after["x-opencode-request"] != before["x-opencode-request"]


# -- anonymous sentinel ---------------------------------------------------------


def test_anonymous_key_omits_authorization():
    # Free models serve anonymous traffic; a keyless setup (config requires
    # non-empty keys) declares intent with the literal `anonymous` sentinel,
    # and the adapter then omits Authorization entirely — a placeholder
    # bearer would be 401 Invalid API key.
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "mimo-v2.5-free", {})
    h = a.headers(_key("anonymous"))
    assert "Authorization" not in h
    assert h["x-opencode-session"].startswith("ses_")
    assert h["User-Agent"] == "opencode/9.9.9"


def test_anonymous_key_omits_authorization_case_insensitive():
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "big-pickle", {})
    assert "Authorization" not in a.headers(_key("Anonymous"))


def test_real_key_keeps_authorization():
    # A valid Zen key must keep flowing: with the metadata headers this is
    # byte-for-byte what the official client sends, preserving per-account
    # attribution and quotas.
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "mimo-v2.5-free", {})
    h = a.headers(_key("sk-zen-real-abc"))
    assert h["Authorization"] == "Bearer sk-zen-real-abc"
    assert h["x-opencode-session"].startswith("ses_")


# -- end-to-end through the gateway ---------------------------------------------


def _zen_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="zen", provider="opencode",
                               base_url="https://opencode.ai/zen/v1",
                               keys=[KeyDef(label="main", key="zen-secret")])],
        model_list=[
            ModelEntry(model_name="zen-free",
                       wiwi_params=DeploymentParams(provider="zen",
                                                    model="mimo-v2.5-free")),
            ModelEntry(model_name="zen-paid",
                       wiwi_params=DeploymentParams(provider="zen",
                                                    model="glm-5.3-flash")),
        ],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest_asyncio.fixture
async def zen_client():
    app = create_app(_zen_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


_CHAT_COMPLETION = {
    "id": "chatcmpl-x", "object": "chat.completion", "model": "m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}


@respx.mock
async def test_gateway_free_model_sends_session_header(zen_client):
    route = respx.post("https://opencode.ai/zen/v1/chat/completions").respond(
        json=_CHAT_COMPLETION)
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "zen-free", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    assert route.called
    sent = route.calls[0].request.headers
    assert sent.get("x-opencode-session", "").startswith("ses_")
    assert sent.get("x-opencode-client") == "cli"
    assert sent.get("user-agent") == "opencode/9.9.9"
    assert sent.get("authorization") == "Bearer zen-secret"


@respx.mock
async def test_gateway_paid_model_sends_session_header(zen_client):
    # Zen reads the client metadata for metrics/sticky routing on every
    # model; the CLI sends it unconditionally, so paid traffic carries it too.
    route = respx.post("https://opencode.ai/zen/v1/chat/completions").respond(
        json=_CHAT_COMPLETION)
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "zen-paid", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    assert route.called
    sent = route.calls[0].request.headers
    assert sent.get("x-opencode-session", "").startswith("ses_")
    assert sent.get("x-opencode-request", "").startswith("msg_")
    assert sent.get("x-opencode-client") == "cli"
    assert sent.get("x-opencode-project") == "global"


@respx.mock
async def test_responses_stream_done_is_cumulative_not_a_fragment(zen_client):
    """Live Zen shape (captured 2026-09-07 against a free muse-spark model):

    ``response.function_call_arguments.delta`` events carry incremental
    fragments; ``response.function_call_arguments.done`` and
    ``response.output_item.done`` then carry the *cumulative* full-args
    string. Re-emitting that full string as another ToolCallArgsDelta makes
    the client accumulate ``{...}{...}`` → \"arguments could not be parsed
    as JSON\" (the reported bug). The args must reach the client exactly
    once, via the fragments only.
    """
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    deltas: list = []
    for evt in [
        {"type": "response.output_item.added",
         "item": {"type": "function_call", "id": "fc_1",
                  "call_id": "call_1", "name": "run_commands"}},
        {"type": "response.function_call_arguments.delta",
         "item_id": "fc_1", "delta": '{"commands":["pwd; ls '},
        {"type": "response.function_call_arguments.done",
         "item_id": "fc_1", "arguments": '{"commands":["pwd; ls -la"]}'},
        {"type": "response.output_item.done",
         "item": {"type": "function_call", "id": "fc_1",
                  "call_id": "call_1", "name": "run_commands",
                  "arguments": '{"commands":["pwd; ls -la"]}'}},
    ]:
        deltas.extend(a.decode_stream_event("", orjson.dumps(evt).decode()))
    opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
    args = [d for d in deltas if isinstance(d, dl.ToolCallArgsDelta)]
    closes = [d for d in deltas if isinstance(d, dl.ToolCallClose)]
    assert len(opens) == 1 and opens[0].name == "run_commands"
    # The exact reported failure: full args string duplicated as a fragment
    # after the incremental delta — concatenated fragments must stay valid.
    joined = "".join(d.args_fragment for d in args)
    assert joined == '{"commands":["pwd; ls -la"]}', joined
    assert len(closes) == 1
    assert args[0].index == opens[0].index == closes[0].index


def test_responses_stream_done_only_no_deltas():
    """Some Responses upstreams send only ``.done`` with the full args and no
    incremental deltas — the single-shot case must still deliver the args
    exactly once (first fragment wins; .done's cumulative copy is ignored)."""
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    deltas: list = []
    for evt in [
        {"type": "response.output_item.added",
         "item": {"type": "function_call", "id": "fc_1",
                  "call_id": "call_1", "name": "w"}},
        {"type": "response.function_call_arguments.done",
         "item_id": "fc_1", "arguments": '{"q":"x"}'},
        {"type": "response.completed",
         "response": {"usage": {"input_tokens": 3, "output_tokens": 1}}},
    ]:
        deltas.extend(a.decode_stream_event("", orjson.dumps(evt).decode()))
    args = [d for d in deltas if isinstance(d, dl.ToolCallArgsDelta)]
    closes = [d for d in deltas if isinstance(d, dl.ToolCallClose)]
    joined = "".join(d.args_fragment for d in args)
    assert joined == '{"q":"x"}', joined
    assert len(closes) == 1


def test_responses_stream_multi_fragment_reassembly():
    """Multiple incremental deltas must concatenate cleanly with .done ignored."""
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    deltas: list = []
    for evt in [
        {"type": "response.output_item.added",
         "item": {"type": "function_call", "id": "fc_1",
                  "call_id": "call_1", "name": "w"}},
        {"type": "response.function_call_arguments.delta",
         "item_id": "fc_1", "delta": '{"a":'},
        {"type": "response.function_call_arguments.delta",
         "item_id": "fc_1", "delta": '"x","b":'},
        {"type": "response.function_call_arguments.delta",
         "item_id": "fc_1", "delta": '"y"}'},
        {"type": "response.function_call_arguments.done",
         "item_id": "fc_1", "arguments": '{"a":"x","b":"y"}'},
        {"type": "response.completed",
         "response": {"usage": {"input_tokens": 3, "output_tokens": 1}}},
    ]:
        deltas.extend(a.decode_stream_event("", orjson.dumps(evt).decode()))
    args = [d for d in deltas if isinstance(d, dl.ToolCallArgsDelta)]
    joined = "".join(d.args_fragment for d in args)
    assert joined == '{"a":"x","b":"y"}', joined


@respx.mock
async def test_gateway_free_model_stream_sends_session_header(zen_client):
    # The stream pump builds headers after encode_request on its own fresh
    # adapter — same gating must apply on the streaming path.
    lines = [
        (b'data: {"id":"x","object":"chat.completion.chunk","model":"m","choices":['
         b'{"index":0,"delta":{"role":"assistant","content":"hi"},'
         b'"finish_reason":null}]}'),
        (b'data: {"id":"x","object":"chat.completion.chunk","model":"m","choices":['
         b'{"index":0,"delta":{},"finish_reason":"stop"}]}'),
        b"data: [DONE]",
    ]
    route = respx.post("https://opencode.ai/zen/v1/chat/completions").respond(
        status_code=200, content=b"\n\n".join(lines) + b"\n\n",
        headers={"Content-Type": "text/event-stream"})
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "zen-free", "messages": [{"role": "user", "content": "hi"}],
        "stream": True},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    assert route.called
    sent = route.calls[0].request.headers
    assert sent.get("x-opencode-session", "").startswith("ses_")
