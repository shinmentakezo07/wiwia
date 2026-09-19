"""Round 88: Zen's free-tier 403 was a request-shape gate, not a credential gate.

AUDIT #174 concluded that OpenCode had retired keyless free-tier access and that
`*-free` models therefore needed a valid `OPENCODE_API_KEY` *and* a funded
workspace. That diagnosis was backwards, and it is why the `io` deployment kept
answering:

    io requires billing (403): OpenCode's free tier can only be used from within OpenCode

Live matrix 2026-09-19, `mimo-v2.5-free` against `POST /zen/v1/chat/completions`,
each row differing from the 200 row in exactly one factor:

| request                                   | result                |
|-------------------------------------------|-----------------------|
| keyless, stream, CLI session, bash+read   | 200 SSE               |
| `stream: false`                           | 403 FreeTierError     |
| no tools                                  | 403 FreeTierError     |
| only `bash` (no `read`)                   | 403 FreeTierError     |
| user tools only                           | 403 FreeTierError     |
| session `ses_` + 24 hex (the pre-fix id)  | 403 FreeTierError     |
| session 11-hex head + 15 upper            | 403 FreeTierError     |
| `User-Agent: opencode` (no version)       | 403 FreeTierError     |
| `User-Agent: opencode/1.16.0`             | 426 UpgradeRequired   |
| a real account Zen key                    | 429 FreeUsageLimitError |

The credential is irrelevant to the gate (keyless works; a real key only hits
the account's spent free quota), while the request properties above are
mandatory. Round 87 added the first (`force_stream`); this round adds the others
plus the session-reuse rule that stops the quota being spread over fresh
buckets.

Pinned below: the free-model classifier, the CLI id shapes (asserted against the
exported patterns rather than a re-typed copy), per-credential session reuse with
TTL/LRU bounds, decoy injection per route and its refusal to clobber a real
client tool, the allowlisted `tool_choice` collapse, paid traffic left
untouched, and gateway end-to-end cases proving the shape reaches the wire.
"""

from __future__ import annotations

import re
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
    RouterSettings,
    WiwiConfig,
)
from wiwi.ir import types as ir
from wiwi.providers import opencode_adapter as oa
from wiwi.providers import opencode_version as ov
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.opencode_adapter import (
    OPENCODE_REQUEST_RE,
    OPENCODE_SESSION_RE,
    OpencodeAdapter,
    canonical_request_id,
    canonical_session_id,
    is_free_model,
)
from wiwi.server.app import create_app
from wiwi.wire import openai_chat as oc

ZEN = "https://opencode.ai/zen/v1"
FREE_CHAT = "mimo-v2.5-free"
FREE_RESPONSES = "muse-spark-1.3-contributor-free"
FREE_RESPONSES_12 = "muse-spark-1.2-contributor-free"
PAID_CHAT = "glm-5.3-flash"


@pytest.fixture(autouse=True)
def _isolate_upstream_state():
    ov._set_cached_for_tests("1.18.31", time.monotonic())
    oa._reset_stable_sessions_for_tests()
    yield
    ov._set_cached_for_tests(None, 0.0)
    oa._reset_stable_sessions_for_tests()


def _req(*, stream: bool = False, tools: bool = False) -> ir.Request:
    body: dict = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    if stream:
        body["stream"] = True
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": "get_weather", "description": "d",
            "parameters": {"type": "object",
                           "properties": {"city": {"type": "string"}}}}}]
    return oc.decode_request(body)


def _keyed(model: str, req: ir.Request | None = None) -> dict:
    return OpencodeAdapter().encode_request(req or _req(), model, {})


def _names(tools: list[dict]) -> list[str]:
    return [t.get("name") or (t.get("function") or {}).get("name")
            for t in tools]


# -- free-model classification --------------------------------------------------


def test_is_free_model_reads_the_suffix_and_the_stealth_set():
    for m in [FREE_CHAT, "MUSE-SPARK-1.3-Contributor-Free", "big-pickle"]:
        assert is_free_model(m) is True, m
    for m in [PAID_CHAT, "gpt-5.5", "claude-sonnet-5", "gemini-3-flash", ""]:
        assert is_free_model(m) is False, m


# -- the CLI id shapes ----------------------------------------------------------


def test_canonical_ids_match_the_cli_patterns():
    for _ in range(25):
        assert OPENCODE_SESSION_RE.match(canonical_session_id())
        assert OPENCODE_REQUEST_RE.match(canonical_request_id())


def test_the_pre_fix_id_shape_fails_the_pattern_the_upstream_checks():
    # The pre-fix minter produced `ses_` + uuid4().hex[:24]: a valid alphabet
    # but a 24-char tail where the edge wants 26, so it 403s. A two-days-old
    # canonical session still returns 200 (probed) — the timestamp is never
    # decoded, only the shape is checked.
    legacy = "ses_" + "0123456789abcdef01234567"       # 24-char tail
    canonical = "ses_" + "0123456789ab" + "g" * 14     # 12 hex + 14 tail
    assert len(legacy) == 28 and len(canonical) == 30
    assert not OPENCODE_SESSION_RE.match(legacy)
    assert OPENCODE_SESSION_RE.match(canonical)


def test_the_pattern_and_the_minter_cannot_drift():
    # The minter builds from the same constants the pattern asserts.
    assert re.fullmatch(r"ses_[0-9a-f]{12}[0-9A-Za-z]{14}", canonical_session_id())
    assert re.fullmatch(r"msg_[0-9a-f]{12}[0-9A-Za-z]{14}", canonical_request_id())


# -- session reuse --------------------------------------------------------------


def test_session_is_shared_across_adapter_instances_of_one_credential():
    # Free quota is accounted per session, so a fresh adapter per request must
    # not mint a fresh session.
    a = OpencodeAdapter().headers(ProviderKeyRef(label="anon", secret="anonymous"))
    b = OpencodeAdapter().headers(ProviderKeyRef(label="anon", secret="anonymous"))
    assert a["x-opencode-session"] == b["x-opencode-session"]
    assert a["x-opencode-request"] != b["x-opencode-request"]


def test_session_is_isolated_per_credential():
    a = OpencodeAdapter().headers(ProviderKeyRef(label="one", secret="sk-a"))
    b = OpencodeAdapter().headers(ProviderKeyRef(label="two", secret="sk-b"))
    assert a["x-opencode-session"] != b["x-opencode-session"]


def test_an_expired_bucket_is_replaced():
    first = oa.stable_session_id("zen:main:anonymous")
    key = next(iter(oa._stable_sessions))
    sid, _ = oa._stable_sessions[key]
    oa._stable_sessions[key] = (sid, time.monotonic() - oa._SESSION_TTL_S - 1)
    assert oa.stable_session_id("zen:main:anonymous") != first


def test_the_session_store_is_bounded():
    for i in range(oa._MAX_SESSION_BUCKETS + 50):
        oa.stable_session_id(f"credential-{i}")
    assert len(oa._stable_sessions) <= oa._MAX_SESSION_BUCKETS


# -- decoy tools ---------------------------------------------------------------


def test_free_chat_model_with_no_tools_gets_both_decoys():
    body = _keyed(FREE_CHAT)
    assert set(_names(body["tools"])) == {"bash", "read"}
    assert all("must not be used" in t["function"]["description"]
               for t in body["tools"])


def test_free_chat_decoys_are_added_without_replacing_client_tools():
    body = _keyed(FREE_CHAT, _req(tools=True))
    assert _names(body["tools"]) == ["get_weather", "bash", "read"]


def test_a_client_tool_named_bash_wins_over_the_decoy():
    req = _req(tools=True)
    req.tools.append(ir.Tool(name="bash", description="the client's own bash",
                             parameters_json_schema={"type": "object",
                                                     "properties": {}}))
    body = _keyed(FREE_CHAT, req)
    bash = [t for t in body["tools"] if t["function"]["name"] == "bash"]
    assert len(bash) == 1
    assert bash[0]["function"]["description"] == "the client's own bash"


def test_free_responses_model_uses_the_flat_tool_shape():
    body = _keyed(FREE_RESPONSES)
    assert {"bash", "read"} <= set(_names(body["tools"]))
    assert all("function" not in t and "parameters" in t for t in body["tools"])


def test_empty_tools_list_is_treated_as_no_tools():
    req = _req()
    req.tools = []
    body = _keyed(FREE_CHAT, req)
    assert {"bash", "read"} <= set(_names(body["tools"]))


# -- tool_choice ---------------------------------------------------------------


def _named_choice_request() -> ir.Request:
    req = _req(tools=True)
    req.tool_choice = ir.ToolChoiceNamed(name="get_weather")
    return req


def test_named_tool_choice_collapses_only_on_the_allowlisted_models():
    for model in (FREE_RESPONSES, FREE_RESPONSES_12):
        assert _keyed(model, _named_choice_request())["tool_choice"] == "auto", model
    # A Responses-route paid model keeps what the client asked for.
    assert _keyed("gpt-5.5", _named_choice_request())["tool_choice"] == {
        "type": "function", "name": "get_weather"}

# -- paid and gemini traffic --------------------------------------------------

def test_paid_models_are_never_cloaked():
    for model in [PAID_CHAT, "gpt-5.5", "claude-sonnet-5"]:
        body = _keyed(model)
        assert "tools" not in body, model
        assert "tool_choice" not in body, model


def test_paid_chat_still_gets_the_usage_opt_in():
    body = _keyed(PAID_CHAT)
    # stream stays forced (round 87: Zen answers as a stream) and the usage
    # opt-in applies to every chat-route request — what is free-tier-only is
    # the decoy tool set and the tool_choice collapse, asserted above.
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


def test_gemini_route_never_carries_a_body_stream_field():
    for model in ["gemini-3-flash", "gemini-3.1-pro"]:
        body = _keyed(model)
        assert "stream" not in body, model
        assert "tools" not in body, model


def test_streaming_client_body_is_unchanged_apart_from_the_decoys():
    body = _keyed(FREE_CHAT, _req(stream=True))
    assert body["stream"] is True
    # The client asked for the stream itself, so nothing is invented for it.
    assert "stream_options" not in body


# -- end-to-end: the shape reaches the wire -------------------------------------


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="zen", provider="opencode", base_url=ZEN,
                               keys=[KeyDef(label="anon", key="anonymous")])],
        model_list=[
            ModelEntry(model_name="free-chat",
                       wiwi_params=DeploymentParams(provider="zen", model=FREE_CHAT)),
            ModelEntry(model_name="free-resp",
                       wiwi_params=DeploymentParams(provider="zen",
                                                    model=FREE_RESPONSES)),
        ],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0),
    )


@pytest_asyncio.fixture
async def zen_client():
    app = create_app(_config())
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer sk-wiwi-master-test"},
    ) as client:
        yield client


_CHAT_SSE = b"".join([
    (b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
     b'[{"index":0,"delta":{"role":"assistant","content":"ok"},'
     b'"finish_reason":null}]}\n\n'),
    (b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
     b'[{"index":0,"delta":{},"finish_reason":"stop"}],'
     b'"usage":{"prompt_tokens":5,"completion_tokens":1}}\n\n'),
    b"data: [DONE]\n\n"])
_SSE = {"Content-Type": "text/event-stream"}


@respx.mock
async def test_free_chat_request_carries_every_gate_factor(zen_client):
    route = respx.post(f"{ZEN}/chat/completions").respond(content=_CHAT_SSE,
                                                          headers=_SSE)
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "free-chat", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    req = route.calls[0].request
    body = orjson.loads(req.content)
    assert body["stream"] is True                            # factor 1
    assert {"bash", "read"} <= set(_names(body["tools"]))    # factor 3
    session = req.headers["x-opencode-session"]
    assert OPENCODE_SESSION_RE.match(session), session       # factor 2
    assert req.headers["user-agent"] == "opencode/1.18.31"   # factor 4
    # Keyless is the working free-tier path: no credential on the wire.
    assert "authorization" not in req.headers
    assert r.json()["choices"][0]["message"]["content"] == "ok"


@respx.mock
async def test_free_responses_request_carries_the_flat_tool_shape(zen_client):
    resp_sse = b"".join(b"data: " + orjson.dumps(e) + b"\n\n" for e in [
        {"type": "response.created",
         "response": {"id": "r1", "status": "in_progress", "output": []}},
        {"type": "response.output_text.delta", "delta": "ok"},
        {"type": "response.completed",
         "response": {"status": "completed",
                      "usage": {"input_tokens": 4, "output_tokens": 1}}},
    ]) + b"data: [DONE]\n\n"
    route = respx.post(f"{ZEN}/responses").respond(content=resp_sse, headers=_SSE)
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "free-resp", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    body = orjson.loads(route.calls[0].request.content)
    assert body["stream"] is True
    assert body["tool_choice"] == "auto"
    assert {"bash", "read"} <= set(_names(body["tools"]))
    assert r.json()["choices"][0]["message"]["content"] == "ok"


@respx.mock
async def test_consecutive_requests_reuse_one_session(zen_client):
    seen: list[str] = []

    def _capture(request):
        seen.append(request.headers["x-opencode-session"])
        return httpx.Response(200, content=_CHAT_SSE, headers=_SSE)

    respx.post(f"{ZEN}/chat/completions").mock(side_effect=_capture)
    for _ in range(3):
        r = await zen_client.post("/v1/chat/completions", json={
            "model": "free-chat", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200, r.text
    assert len(seen) == 3
    assert len(set(seen)) == 1, seen
