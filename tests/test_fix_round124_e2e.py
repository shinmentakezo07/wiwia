"""Round 124 end-to-end: both #387 and #388 driven through the real HTTP stack.

The adapter-level tests in ``test_fix_round124.py`` prove the IR helpers no longer
raise. This file proves the whole request path no longer breaks: ASGI app under a
real lifespan, an inbound dialect codec, auth, the router, the gateway, a provider
adapter, and the outbound dialect encoder — with the upstream mocked by ``respx``
so the request actually reaches ``encode_request`` and the captured body can be
inspected.

Two scenarios, both of which broke before the fix:

* **#387** — a client replays history containing a ``tool_use`` whose ``name`` is
  a JSON array. The Anthropic codec accepts it (it only validates the ``id``), the
  gateway carries it, and the Anthropic adapter's ``is_builtin_name`` read raised
  ``TypeError`` when ``block_type`` was falsy, which surfaced as a 500 with a
  healthy upstream blamed.
* **#388/#389** — a client sends ``thinking.budget_tokens`` as a non-numeric value.
  ``anthropic_messages.py`` coerces the documented shapes (digit strings, whole
  floats) but rejects anything else, so ``"abc"`` flowed through as a non-numeric
  budget into ``effective_thinking_budget`` and the adapter's ``max(budget, …)``.

Each is verified against the captured upstream body, so the tests also prove the
request still carried a well-formed thinking/tool config rather than silently
dropping it — and each has a control pinning that legitimate values still pass.
"""

from __future__ import annotations

import httpx
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
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"
ANTHROPIC_HEADERS = {"x-api-key": MASTER, "anthropic-version": "2023-06-01"}

def _anthropic_config() -> WiwiConfig:
    """An Anthropic-backed deployment, so the Anthropic inbound codec and the
    Anthropic outbound adapter are both on the request path."""
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="anthropic",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="claude-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="claude-x"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )

# A minimal valid Anthropic Messages response.
OK_BODY = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-x",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 5, "output_tokens": 2},
}

class _Client:
    """A lifespan-managed ASGI client plus its manager, for `async with`."""

    def __init__(self) -> None:
        self.app = create_app(_anthropic_config())
        self.manager = LifespanManager(self.app)

    async def __aenter__(self):
        await self.manager.__aenter__()
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        return self

    async def __aexit__(self, *exc) -> None:
        await self.http.aclose()
        await self.manager.__aexit__(*exc)

    async def post(self, body: dict, headers: dict | None = None) -> httpx.Response:
        return await self.http.post("/v1/messages", json=body,
                                    headers=headers or ANTHROPIC_HEADERS)

def _last_body(route: respx.Route) -> dict:
    """The captured upstream request body of the most recent call."""
    import orjson
    return orjson.loads(route.calls.last.request.read())

# ---------------------------------------------------------------------------
# #387 — container tool name in replayed history
# ---------------------------------------------------------------------------

@respx.mock
async def test_container_tool_name_in_replayed_history_is_encoded():
    """Replayed history with a container tool name must reach the upstream intact.

    Drives the Anthropic inbound codec (which accepts a non-string ``name``; it
    validates only ``id``) and asserts the upstream body carries the turn without
    the request 500-ing. Pre-fix this raised ``TypeError`` on the
    ``is_builtin_name`` fallback for IR parts built with a falsy ``block_type``.
    """
    route = respx.post("https://api.anthropic.com/v1/messages").respond(json=OK_BODY)
    async with _Client() as c:
        r = await c.post({
            "model": "claude-x", "max_tokens": 64,
            "messages": [
                {"role": "user", "content": "search for X"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_1",
                     "name": ["web_search"], "input": {"q": "X"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1",
                     "content": "found"}]},
            ],
        })
        assert r.status_code == 200, r.text
        assert r.json()["content"][0]["text"] == "ok"

        body = _last_body(route)
        # The assistant turn survived to the upstream with its tool_use block.
        # The inbound codec coerces a non-string name to "" (AUDIT #186), which
        # is what keeps this a well-formed request rather than a JSON array in a
        # string field — the coercion, not the raise, is what #387 needs here.
        assistant = next(m for m in body["messages"] if m["role"] == "assistant")
        assert assistant["content"][0]["type"] == "tool_use"
        assert assistant["content"][0]["id"] == "toolu_1"
        assert isinstance(assistant["content"][0]["name"], str)
        # The paired tool_result survived and still correlates.
        result = next(m for m in body["messages"]
                      if m["role"] == "user"
                      and any(isinstance(b, dict) and b.get("type") == "tool_result"
                              for b in m["content"]))
        assert result["content"][0]["tool_use_id"] == "toolu_1"

@respx.mock
async def test_streamed_container_tool_name_survives_full_round_trip():
    """Upstream streams a container tool name; the client must still get a turn.

    The complete #387 chain over HTTP: the upstream emits a streaming
    ``tool_use`` whose ``name`` is a list, the gateway folds the delta into an IR
    ``ToolUsePart`` (which coerces the ``id`` but not the ``name``), and the
    Anthropic *inbound encoder* renders it back to the client. Pre-fix the
    ``is_builtin_name`` read on the replay leg raised ``TypeError``.
    """
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        text=(
            'data: {"type":"message_start","message":{"id":"msg_1","role":"assistant",'
            '"model":"claude-x","content":[],"usage":{"input_tokens":5,"output_tokens":0}}}\n\n'
            'data: {"type":"content_block_start","index":0,'
            '"content_block":{"type":"tool_use","id":"toolu_1","name":["web_search"],"input":{}}}\n\n'
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"input_json_delta","partial_json":"{\\"q\\": \\"X\\"}"}}\n\n'
            'data: {"type":"content_block_stop","index":0}\n\n'
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
            '"usage":{"output_tokens":7}}\n\n'
            'data: {"type":"message_stop"}\n\n'))
    async with _Client() as c:
        r = await c.post({
            "model": "claude-x", "max_tokens": 64, "stream": True,
            "messages": [{"role": "user", "content": "search for X"}],
        })
        assert r.status_code == 200, r.text
        # A tool_use block must reach the client; the stream must terminate.
        assert "content_block_start" in r.text
        assert '"tool_use"' in r.text
        assert "message_stop" in r.text
        # No synthetic error frame was appended.
        assert '"type":"error"' not in r.text
        assert route.called

# ---------------------------------------------------------------------------
# #388 / #389 — non-numeric thinking budget
# ---------------------------------------------------------------------------

@respx.mock
async def test_non_numeric_thinking_budget_does_not_500():
    """A malformed ``thinking.budget_tokens`` must not 500 before the upstream call.

    ``anthropic_messages.py`` coerces digit strings and whole floats but rejects
    anything else, so these flowed through as a non-numeric budget. Pre-fix that
    reached ``effective_thinking_budget`` (which forwarded it raw) and the
    adapter's ``max(budget, MIN_THINKING_BUDGET)``, raising ``TypeError``.
    """
    route = respx.post("https://api.anthropic.com/v1/messages").respond(json=OK_BODY)
    async with _Client() as c:
        for budget in ("abc", True, [8000], {"a": 1}, 8000.5):
            r = await c.post({
                "model": "claude-x", "max_tokens": 4096,
                "thinking": {"type": "enabled", "budget_tokens": budget},
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert r.status_code == 200, f"budget={budget!r}: {r.text}"

        # The malformed budget must be dropped, not emitted as a bogus
        # budget_tokens value the API would reject.
        body = _last_body(route)
        assert "thinking" not in body, (
            f"a malformed budget reached the upstream: {body.get('thinking')}")

@respx.mock
async def test_well_typed_thinking_budget_still_reaches_upstream():
    """Control: the coercion must not swallow a legitimate budget.

    A digit-string budget is a documented, legal client shape, and it must still
    produce a real ``thinking`` block clamped to the API's 1024 minimum.
    """
    route = respx.post("https://api.anthropic.com/v1/messages").respond(json=OK_BODY)
    async with _Client() as c:
        r = await c.post({
            "model": "claude-x", "max_tokens": 4096,
            "thinking": {"type": "enabled", "budget_tokens": "8000"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert r.status_code == 200, r.text
        body = _last_body(route)
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 8000}

@respx.mock
async def test_effort_only_request_still_reaches_anthropic_verbatim():
    """Control: ``output_config.effort`` must survive to the upstream (AUDIT #156)."""
    route = respx.post("https://api.anthropic.com/v1/messages").respond(json=OK_BODY)
    async with _Client() as c:
        r = await c.post({
            "model": "claude-x", "max_tokens": 4096,
            "output_config": {"effort": "high"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert r.status_code == 200, r.text
        body = _last_body(route)
        assert body["output_config"] == {"effort": "high"}

# ---------------------------------------------------------------------------
# #390 — the genuinely HTTP-reachable half of #387, found while verifying
# ---------------------------------------------------------------------------

def _openai_config() -> WiwiConfig:
    """An OpenAI-wire deployment, which is where the unguarded read lives."""
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )

# Chunk 1 carries the id plus a container name (deferred Open); chunk 2 carries a
# *bare* name fragment with no id, which is what reaches the accumulating branch.
FRAGMENTED_LIST_NAME_SSE = (
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function",'
    '"function":{"name":["web_search"]}}]}}]}\n\n'
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
    '"function":{"name":"X"}}]}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
    "data: [DONE]\n\n")

@respx.mock
async def test_fragmented_container_tool_name_does_not_500_over_http():
    """The HTTP-reachable half of #387: a fragmented container tool name.

    Found while verifying the fix end to end. The adapter-level tests could not
    catch this because they sent the name in ONE chunk; sent fragmented, the
    first chunk poisons ``_tool_names`` and the second hits
    ``"" + ["web_search"]``, raising ``TypeError`` out of ``decode_stream_event``.

    Pre-fix the client received ``200``, a ``tool_use`` block, then a synthetic
    ``{"type":"error","error":{"message":"unhashable type: 'list'"}}`` frame and
    a ``502`` in the request log — with a healthy upstream blamed for it.
    """
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        text=FRAGMENTED_LIST_NAME_SSE)
    app = create_app(_openai_config())
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test") as c:
        r = await c.post("/v1/messages", json={
            "model": "gpt-4o", "max_tokens": 64, "stream": True,
            "messages": [{"role": "user", "content": "hi"}]},
            headers={"x-api-key": MASTER, "anthropic-version": "2023-06-01"})
        assert r.status_code == 200, r.text
        # No synthetic error frame: the stream must terminate cleanly.
        assert '"type":"error"' not in r.text, r.text
        assert "message_stop" in r.text
        # The tool call still reached the client, with a string name.
        assert '"tool_use"' in r.text

@respx.mock
async def test_fragmented_null_tool_name_does_not_500_over_http():
    """The #385 shape over HTTP: an explicit JSON ``null`` name, fragmented.

    ``fn.get("name", "")`` defaults only a *missing* key, so ``null`` flowed
    through as ``None`` and the accumulating branch did ``"" + None``. Same
    ``decode_stream_event`` raise, same healthy-upstream-blamed consequence.
    """
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        text=(
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function",'
            '"function":{"name":null}}]}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"name":"X"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            "data: [DONE]\n\n"))
    app = create_app(_openai_config())
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test") as c:
        r = await c.post("/v1/messages", json={
            "model": "gpt-4o", "max_tokens": 64, "stream": True,
            "messages": [{"role": "user", "content": "hi"}]},
            headers={"x-api-key": MASTER, "anthropic-version": "2023-06-01"})
        assert r.status_code == 200, r.text
        assert '"type":"error"' not in r.text, r.text
        assert "message_stop" in r.text
