"""IFM (MBZUAI) adapter: request shaping, registry wiring and end-to-end path.

IFM's hosted API is OpenAI Chat Completions compatible, but its K2 Horizon /
Jais 3 templates impose requirements a plain OpenAI body does not meet. These
tests pin each one against the documented behaviour (docs.ifm.ai):

* every assistant history message replays ``reasoning_content`` — an empty
  trace included — or the next turn is rejected with a 400;
* message content is a text string, so parts arrays are flattened and
  non-text parts are reported;
* ``reasoning_effort`` rides ``chat_template_kwargs`` at IFM's three levels;
* ``X-Session-ID`` is stable per conversation;
* ``stop`` is capped at four sequences and ``top_k`` is forwarded.

The last test drives a real Anthropic-inbound request through the app against
a mocked IFM endpoint, which is what proves the wiring (config validation,
router deployment, adapter, dialect re-encoding) rather than the adapter alone.
"""

from __future__ import annotations

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
from wiwi.providers import registry
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.ifm import IFM_BASE_URL
from wiwi.providers.ifm_adapter import IFMAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import BUILTIN_PROVIDER_TYPES
from wiwi.streaming import deltas as dl

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
IFM_URL = f"{IFM_BASE_URL}/chat/completions"
MODEL = "IFM/K2-Horizon-375B-A23B"


# -- helpers -------------------------------------------------------------------

def _req(messages: list[ir.Message] | None = None,
         gen_params: ir.GenParams | None = None,
         extras: dict | None = None,
         stream: bool = False) -> ir.Request:
    return ir.Request(
        model=MODEL,
        messages=messages or [ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=gen_params or ir.GenParams(),
        extras=extras or {},
        stream=stream,
    )


def _conversation() -> list[ir.Message]:
    return [
        ir.Message(role="system", parts=[ir.TextPart("be terse")]),
        ir.Message(role="user", parts=[ir.TextPart("first question")]),
        ir.Message(role="assistant", parts=[ir.TextPart("first answer")]),
        ir.Message(role="user", parts=[ir.TextPart("second question")]),
    ]


def _encode(req: ir.Request, params: dict | None = None) -> tuple[dict, IFMAdapter]:
    adapter = IFMAdapter()
    return adapter.encode_request(req, MODEL, params or {}), adapter


# -- registry + catalog --------------------------------------------------------

def test_ifm_is_a_registered_provider_type():
    from wiwi.config import PROVIDER_TYPES
    assert "ifm" in PROVIDER_TYPES


def test_registry_serves_a_shared_and_a_private_ifm_adapter():
    shared = registry.get_adapter("ifm")
    assert isinstance(shared, IFMAdapter)
    assert registry.get_adapter("ifm") is shared
    private = registry.fresh_adapter("ifm")
    assert isinstance(private, IFMAdapter)
    assert registry.fresh_adapter("ifm") is not private


def test_ifm_adapter_is_an_openai_wire_adapter():
    assert issubclass(IFMAdapter, OpenAIAdapter)
    assert IFMAdapter().provider_type == "ifm"


def test_catalog_carries_an_ifm_card():
    card = next(p for p in BUILTIN_PROVIDER_TYPES if p["provider_type"] == "ifm")
    assert card["default_base_url"] == IFM_BASE_URL
    assert "IFM/K2-Horizon-375B-A23B" in card["latest_models"]


def test_build_url_is_chat_completions_and_no_realtime_surface():
    adapter = IFMAdapter()
    assert adapter.build_url(IFM_BASE_URL, MODEL, stream=True) == IFM_URL
    assert adapter.realtime_url(IFM_BASE_URL) is None


async def test_admin_api_accepts_and_catalogs_the_ifm_type():
    """The admin surface derives from PROVIDER_TYPES, so the type must work
    there without a second registry: create an account, then read it back."""
    from wiwi.server.app import create_app

    app = create_app(_config())
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app), httpx.AsyncClient(
            transport=transport, base_url="http://test") as c:
        catalog = await c.get("/admin/provider-catalog", headers=AUTH)
        assert catalog.status_code == 200, catalog.text
        card = next(p for p in catalog.json() if p["provider_type"] == "ifm")
        assert card["default_base_url"] == IFM_BASE_URL

        created = await c.post("/admin/providers", headers=AUTH, json={
            "name": "ifm-second", "provider_type": "ifm",
            "key": "sk-ifm-second", "label": "main",
        })
        assert created.status_code == 200, created.text
        providers = await c.get("/admin/providers", headers=AUTH)
        entry = next(p for p in providers.json()["providers"]
                     if p["name"] == "ifm-second")
        assert entry["provider_type"] == "ifm"
        # No explicit base_url was sent: the catalog default is applied.
        assert entry["base_url"] == IFM_BASE_URL


# -- reasoning replay ----------------------------------------------------------

def test_every_assistant_turn_carries_reasoning_content():
    """The 400 this adapter exists to prevent: a stripped trace on turn 2."""
    body, _ = _encode(_req(_conversation()))
    assistants = [m for m in body["messages"] if m["role"] == "assistant"]
    assert assistants, "the conversation must contain an assistant turn"
    for m in assistants:
        assert isinstance(m.get("reasoning_content"), str)
    # The caller's transcript had no trace for this turn: padded with "",
    # which is the documented legal value (null is not).
    assert assistants[0]["reasoning_content"] == ""


def test_an_existing_trace_is_replayed_verbatim():
    msgs = [
        ir.Message(role="user", parts=[ir.TextPart("q")]),
        ir.Message(role="assistant",
                   parts=[ir.TextPart("a"), ir.ThinkingPart("step one")]),
        ir.Message(role="user", parts=[ir.TextPart("again")]),
    ]
    body, _ = _encode(_req(msgs))
    assistant = next(m for m in body["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "step one"


def test_tool_call_only_assistant_turn_gets_string_content_and_reasoning():
    msgs = [
        ir.Message(role="user", parts=[ir.TextPart("look it up")]),
        ir.Message(role="assistant", parts=[
            ir.ToolUsePart(id="call_1", name="lookup", args={"id": "A-1"}),
        ]),
        ir.Message(role="tool", parts=[
            ir.ToolResultPart(tool_use_id="call_1", content="shipped"),
        ]),
    ]
    body, _ = _encode(_req(msgs))
    assistant = next(m for m in body["messages"] if m["role"] == "assistant")
    # The base adapter leaves content null on a tool-call-only turn; IFM's
    # template reads content as a string (the docs' tool-loop example uses "").
    assert assistant["content"] == ""
    assert assistant["reasoning_content"] == ""
    tool = next(m for m in body["messages"] if m["role"] == "tool")
    assert isinstance(tool["content"], str)


# -- text-only content ---------------------------------------------------------

def test_content_parts_are_flattened_and_non_text_parts_reported():
    msgs = [ir.Message(role="user", parts=[
        ir.TextPart("describe "),
        ir.ImagePart(url="https://example.com/x.png"),
        ir.TextPart("this"),
    ])]
    body, adapter = _encode(_req(msgs))
    user = next(m for m in body["messages"] if m["role"] == "user")
    assert user["content"] == "describe this"
    warnings = " ".join(adapter.translation_warnings)
    assert "text content only" in warnings
    assert "image_url" in warnings


# -- reasoning effort ----------------------------------------------------------

def test_effort_rides_chat_template_kwargs_not_the_body_top_level():
    body, _ = _encode(_req(gen_params=ir.GenParams(reasoning_effort="medium")))
    assert "reasoning_effort" not in body
    assert body["chat_template_kwargs"] == {"reasoning_effort": "medium"}


@pytest.mark.parametrize("requested,expected", [
    ("low", "low"),
    ("minimal", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "high"),
    ("max", "high"),
])
def test_effort_is_clamped_to_ifms_three_levels(requested, expected):
    body, _ = _encode(_req(gen_params=ir.GenParams(reasoning_effort=requested)))
    assert body["chat_template_kwargs"]["reasoning_effort"] == expected


def test_anthropic_thinking_budget_becomes_an_ifm_level():
    body, _ = _encode(_req(gen_params=ir.GenParams(thinking_budget=8000)))
    assert body["chat_template_kwargs"]["reasoning_effort"] == "medium"


def test_disabled_thinking_is_dropped_with_a_caller_warning():
    body, adapter = _encode(_req(gen_params=ir.GenParams(reasoning_effort="none")))
    assert "reasoning_effort" not in body
    assert "chat_template_kwargs" not in body
    assert any("always reasons" in w for w in adapter.translation_warnings)


def test_deployment_template_kwargs_survive():
    body, _ = _encode(
        _req(gen_params=ir.GenParams(reasoning_effort="high")),
        {"extra_body": {"chat_template_kwargs": {"other_knob": 1}}},
    )
    assert body["chat_template_kwargs"] == {
        "other_knob": 1, "reasoning_effort": "high"}


# -- sampling limits -----------------------------------------------------------

def test_stop_is_capped_at_four_sequences():
    body, adapter = _encode(
        _req(gen_params=ir.GenParams(stop=["a", "b", "c", "d", "e"])))
    assert body["stop"] == ["a", "b", "c", "d"]
    assert any("at most 4 stop" in w for w in adapter.translation_warnings)


def test_top_k_is_forwarded_without_a_dropped_parameter_warning():
    body, adapter = _encode(_req(gen_params=ir.GenParams(top_k=20)))
    assert body["top_k"] == 20
    assert not any("top_k" in w for w in adapter.translation_warnings)


def test_undocumented_openai_platform_params_are_dropped_and_reported():
    body, adapter = _encode(_req(extras={"store": True, "metadata": {"k": "v"}}))
    assert "store" not in body
    assert "metadata" not in body
    warnings = " ".join(adapter.translation_warnings)
    assert "'store' is not supported by IFM" in warnings
    assert "'metadata' is not supported by IFM" in warnings


# -- session affinity ----------------------------------------------------------

def test_no_session_header_before_a_request_is_encoded():
    headers = IFMAdapter().headers(ProviderKeyRef(
        label="main", secret="sk-ifm-test"))
    assert "X-Session-ID" not in headers


def test_session_id_is_stable_per_conversation_and_opaque():
    key = ProviderKeyRef(label="main", secret="sk-ifm-test")
    first = IFMAdapter()
    first.encode_request(_req(_conversation()), MODEL, {})
    h1 = first.headers(key)

    # The next turn appends to the same transcript: the shared prefix (and so
    # the affinity key) must not move.
    grown = _conversation() + [
        ir.Message(role="assistant", parts=[ir.TextPart("second answer")]),
        ir.Message(role="user", parts=[ir.TextPart("third question")]),
    ]
    second = IFMAdapter()
    second.encode_request(_req(grown), MODEL, {})
    h2 = second.headers(key)

    assert h1["X-Session-ID"] == h2["X-Session-ID"]
    assert h1["X-Session-ID"].startswith("conv_")
    assert "first question" not in h1["X-Session-ID"]

    other = IFMAdapter()
    other.encode_request(_req([ir.Message(role="user",
                                          parts=[ir.TextPart("different")])]),
                         MODEL, {})
    assert other.headers(key)["X-Session-ID"] != h1["X-Session-ID"]


def test_a_client_session_id_wins_over_the_derived_one():
    key = ProviderKeyRef(label="main", secret="sk-ifm-test")
    adapter = IFMAdapter()
    adapter.encode_request(_req(_conversation()), MODEL,
                           {"session_id": "client-session-1"})
    assert adapter.headers(key)["X-Session-ID"] == "client-session-1"


def test_reset_clears_the_session_id():
    adapter = IFMAdapter()
    adapter.encode_request(_req(), MODEL, {})
    adapter.reset()
    headers = adapter.headers(ProviderKeyRef(
        label="main", secret="sk-ifm-test"))
    assert "X-Session-ID" not in headers


# -- decoding ------------------------------------------------------------------

def test_non_streaming_reasoning_content_becomes_a_thinking_part():
    body = {
        "id": "chatcmpl-1",
        "model": MODEL,
        "choices": [{
            "message": {"role": "assistant", "content": "hello",
                        "reasoning_content": "the user greeted me",
                        "reasoning": "the user greeted me",
                        "tool_calls": []},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 11, "completion_tokens": 214,
                  "total_tokens": 225,
                  "prompt_tokens_details": {"cached_tokens": 0},
                  "completion_tokens_details": {"reasoning_tokens": 184}},
    }
    turn = IFMAdapter().decode_response(200, orjson.dumps(body))
    assert turn.text == "hello"
    assert [t.text for t in turn.thinking] == ["the user greeted me"]
    assert turn.usage.reasoning_tokens == 184


def test_streamed_reasoning_content_becomes_a_thinking_delta():
    adapter = IFMAdapter()
    events = adapter.decode_stream_event(
        "", '{"choices":[{"delta":{"reasoning_content":"think "}}]}')
    assert events == [dl.ThinkingDelta("think ")]
    # The mirror field works too (older clients read `reasoning`).
    events = adapter.decode_stream_event(
        "", '{"choices":[{"delta":{"reasoning":"again"}}]}')
    assert events == [dl.ThinkingDelta("again")]
    events = adapter.decode_stream_event(
        "", '{"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}')
    assert dl.TextDelta("answer") in events
    assert dl.Finish("stop") in events


# -- end to end ----------------------------------------------------------------

def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="ifm-main", provider="ifm",
                               keys=[KeyDef(label="main", key="sk-ifm-test")])],
        model_list=[ModelEntry(
            model_name=MODEL,
            wiwi_params=DeploymentParams(provider="ifm-main", model=MODEL))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(),
    )


@respx.mock
async def test_messages_request_round_trips_through_ifm():
    """Anthropic in, IFM out, Anthropic back — the whole point of the adapter."""
    upstream = {
        "id": "chatcmpl-9", "model": MODEL,
        "choices": [{
            "message": {"role": "assistant", "content": "second answer",
                        "reasoning_content": "consider the second question",
                        "tool_calls": []},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5,
                  "completion_tokens_details": {"reasoning_tokens": 3}},
    }
    route = respx.post(IFM_URL).respond(json=upstream)
    from wiwi.server.app import create_app
    app = create_app(_config())
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app), httpx.AsyncClient(
            transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/v1/messages",
            headers={**AUTH, "anthropic-version": "2023-06-01"},
            json={
                "model": MODEL, "max_tokens": 256,
                "system": "be terse",
                "messages": [
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "first answer"},
                    {"role": "user", "content": "second question"},
                ],
            },
        )
        assert r.status_code == 200, r.text

    sent = orjson.loads(route.calls.last.request.content)
    # 1. The history assistant turn replays reasoning_content ("" here).
    assistant = next(m for m in sent["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == ""
    assert assistant["content"] == "first answer"
    # 2. Content is a plain string on every message.
    assert all(isinstance(m["content"], str) for m in sent["messages"])
    # 3. Session affinity rides the header.
    assert route.calls.last.request.headers["X-Session-ID"].startswith("conv_")
    # 4. The reply is re-encoded in the caller's dialect, thinking included.
    payload = r.json()
    assert payload["content"][0]["type"] == "thinking"
    assert payload["content"][0]["thinking"] == "consider the second question"
    assert payload["content"][1] == {"type": "text", "text": "second answer"}
