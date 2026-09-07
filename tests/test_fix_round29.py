"""Round-29 regression tests: OpenCode Zen free-tier client-gate headers.

Zen tightened the free-tier gate after round 27: ``User-Agent`` alone no
longer unlocks ``-free`` models — the edge now requires the client session
header and rejects with ``400 MissingSessionID`` ("OpenCode's free tier can
only be used in OpenCode") otherwise (verified live 2026-09-07). The real
client (``packages/opencode/src/session/llm/request.ts``) sends
``x-opencode-session`` + ``x-opencode-client`` on every opencode-provider
request.

These tests pin:

- free models (``-free`` suffixed plus the unsuffixed stealth free model
  ``big-pickle``) get ``x-opencode-session``/``x-opencode-client`` headers
- paid models do NOT — session headers shard Zen's routing by session id
  (kimi-k2.7-code: ~50% of fresh session ids land on a broken replica) and
  paid models are not session-gated, so spoofing there is pure downside
- the session id is stable across header rebuilds within one request (the
  401-refresh retry path rebuilds headers) and fresh per request instance
- the gateway end-to-end sends the session header upstream for free models
  and omits it for paid ones, streaming included
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
from wiwi.providers.opencode_adapter import OpencodeAdapter, is_free_model
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


# -- free-model classification -------------------------------------------------


def test_is_free_model_matches_live_catalog():
    # Live catalog free models (verified 2026-09-07) + the unsuffixed stealth one.
    for m in ["deepseek-v4-flash-free", "ling-3.0-flash-fin-free", "mimo-v2.5-free",
              "muse-spark-1.2-contributor-free", "muse-spark-1.3-contributor-free",
              "nemotron-3-ultra-free", "nemotron-3.5-lightning-free", "big-pickle"]:
        assert is_free_model(m), m


def test_is_free_model_rejects_paid_and_non_suffix():
    for m in ["glm-5.3-flash", "kimi-k2.6", "deepseek-v4-flash", "claude-sonnet-5",
              "free-tier-v2", "gpt-5.5", "", "minimax-m3"]:
        assert not is_free_model(m), m


def test_is_free_model_case_insensitive():
    assert is_free_model("MIMO-V2.5-FREE")
    assert is_free_model("Big-Pickle")


# -- headers: free models carry the client session ------------------------------


def test_free_model_headers_include_session_and_client():
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "mimo-v2.5-free", {})
    h = a.headers(_key())
    assert h["User-Agent"] == "opencode/9.9.9"
    assert h["Authorization"] == "Bearer zen-key-123"
    assert h["x-opencode-client"] == "cli"
    ses = h["x-opencode-session"]
    assert ses.startswith("ses_") and len(ses) > len("ses_")


def test_big_pickle_headers_include_session():
    # Stealth free model: documented free, gated by MissingSessionID live,
    # but carries no -free suffix.
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "big-pickle", {})
    assert a.headers(_key())["x-opencode-session"].startswith("ses_")


def test_responses_route_free_model_headers_include_session():
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "muse-spark-1.3-contributor-free", {})
    assert a.headers(_key())["x-opencode-session"].startswith("ses_")


def test_session_id_stable_across_header_rebuilds():
    # The 401-refresh retry path rebuilds headers on the same adapter
    # instance; the session id must not change mid-request.
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "mimo-v2.5-free", {})
    s1 = a.headers(_key())["x-opencode-session"]
    s2 = a.headers(_key())["x-opencode-session"]
    assert s1 == s2


def test_session_id_fresh_per_request_instance():
    # Each request gets its own fresh adapter (fresh_adapter on the hot
    # path); each must present a distinct session id so a bad Zen replica
    # is never sticky across requests (cf. kimi-k2.7-code session sharding).
    a1 = OpencodeAdapter()
    a1.encode_request(_chat_req(), "mimo-v2.5-free", {})
    a2 = OpencodeAdapter()
    a2.encode_request(_chat_req(), "mimo-v2.5-free", {})
    assert (a1.headers(_key())["x-opencode-session"]
            != a2.headers(_key())["x-opencode-session"])


def test_build_url_alone_primes_free_model_headers():
    # The admin test-connection path calls build_url before headers without
    # encode_request; the model id learned there must still gate the spoof.
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "mimo-v2.5-free", True)
    assert a.headers(_key())["x-opencode-session"].startswith("ses_")


# -- headers: paid models carry no session --------------------------------------


def test_paid_model_headers_have_no_session():
    # Session ids shard Zen's upstream routing (kimi-k2.7-code: fresh session
    # ids fail ~50% on a broken replica) and paid models are not gated, so
    # the spoof headers must never leak to paid traffic.
    for m in ["glm-5.3-flash", "kimi-k2.6", "deepseek-v4-flash", "claude-sonnet-5",
              "gemini-3.1-pro", "gpt-5.5"]:
        a = OpencodeAdapter()
        a.encode_request(_chat_req(), m, {})
        h = a.headers(_key())
        assert "x-opencode-session" not in h, m
        assert "x-opencode-client" not in h, m


def test_headers_before_any_model_known_have_no_session():
    # No encode/build_url yet → model unknown → fail safe: no spoof headers.
    a = OpencodeAdapter()
    h = a.headers(_key())
    assert "x-opencode-session" not in h
    assert h["User-Agent"] == "opencode/9.9.9"


def test_reset_clears_free_model_state():
    a = OpencodeAdapter()
    a.encode_request(_chat_req(), "mimo-v2.5-free", {})
    assert "x-opencode-session" in a.headers(_key())
    a.reset()
    assert "x-opencode-session" not in a.headers(_key())


# -- anonymous sentinel ---------------------------------------------------------


def test_anonymous_key_omits_authorization_for_free_model():
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


def test_real_key_keeps_authorization_for_free_model():
    # A valid Zen key must keep flowing: with the session headers this is
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
async def test_gateway_paid_model_omits_session_header(zen_client):
    route = respx.post("https://opencode.ai/zen/v1/chat/completions").respond(
        json=_CHAT_COMPLETION)
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "zen-paid", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    assert route.called
    sent = route.calls[0].request.headers
    assert "x-opencode-session" not in sent
    assert "x-opencode-client" not in sent


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
