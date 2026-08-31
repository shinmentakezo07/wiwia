"""WorkBuddy provider port tests: auth parsing, headers, encode quirks,
SSE envelope errors, refresh flow (respx), and registry wiring."""

from __future__ import annotations

import json
import time

import httpx
import pytest
import respx

from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef, WiwiError
from wiwi.providers.registry import fresh_adapter, get_adapter
from wiwi.providers.workbuddy_adapter import (
    WorkBuddyAdapter,
    _normalize_tool_choice,
    _sanitize_text,
)
from wiwi.providers.workbuddy_auth import (
    WorkBuddyAuthError,
    chat_headers,
    parse_auth,
    refresh_headers,
    refresh_token,
)
from wiwi.streaming import deltas as dl

NESTED_SECRET = json.dumps({
    "auth": {
        "accessToken": "at-1",
        "refreshToken": "rt-1",
        "expiresAt": int(time.time()) + 3600,
        "domain": "workbuddy.ai",
    },
    "account": {
        "uid": "10001",
        "enterpriseId": "ent-9",
        "nickname": "wb-user",
    },
})

FLAT_SECRET = json.dumps({
    "accessToken": "at-2",
    "refreshToken": "rt-2",
    "expiresAt": int(time.time()) + 3600,
    "domain": "copilot.tencent.com",
    "uid": "20002",
    "enterpriseId": "ent-7",
})


def _req(**over) -> ir.Request:
    base = {
        "model": "test-model",
        "messages": [ir.Message(role="user", parts=[ir.TextPart(text="hi")])],
        "stream": False,
    }
    base.update(over)
    return ir.Request(**base)


# -- auth parsing ------------------------------------------------------------

def test_parse_nested_and_flat_shapes():
    a = parse_auth(NESTED_SECRET)
    assert (a.access_token, a.refresh_token, a.uid,
            a.enterprise_id, a.nickname) == ("at-1", "rt-1", "10001", "ent-9", "wb-user")
    f = parse_auth(FLAT_SECRET)
    assert (f.access_token, f.uid, f.enterprise_id) == ("at-2", "20002", "ent-7")


def test_parse_rejects_missing_access_token():
    with pytest.raises(WorkBuddyAuthError):
        parse_auth(json.dumps({"refreshToken": "rt-1"}))
    with pytest.raises(WorkBuddyAuthError):
        parse_auth("")


def test_region_routing():
    g = parse_auth(NESTED_SECRET)
    assert g.region() == "global"
    assert g.chat_base() == "https://www.workbuddy.ai"
    f = parse_auth(FLAT_SECRET)
    assert f.region() == "cn"
    assert f.chat_base() == "https://copilot.tencent.com"
    empty = parse_auth(json.dumps({"accessToken": "x"}))
    assert empty.region() == "cn"


def test_region_handles_url_shaped_domain():
    """The upstream plugin writes domain as a full URL (auths/ files)."""
    url_domain = parse_auth(json.dumps({
        "accessToken": "x",
        "domain": "https://www.workbuddy.ai",
    }))
    assert url_domain.region() == "global"
    cn_url = parse_auth(json.dumps({
        "accessToken": "x",
        "domain": "https://www.codebuddy.cn",
    }))
    assert cn_url.region() == "cn"


def test_needs_refresh_unknown_expiry_is_due():
    a = parse_auth(json.dumps({"accessToken": "x"}))
    assert a.needs_refresh(60)
    soon = parse_auth(json.dumps(
        {"accessToken": "x", "expiresAt": int(time.time()) + 30}))
    assert soon.needs_refresh(60)
    far = parse_auth(json.dumps(
        {"accessToken": "x", "expiresAt": int(time.time()) + 10_000}))
    assert not far.needs_refresh(60)


def test_roundtrip_secret_preserves_fields():
    a = parse_auth(NESTED_SECRET)
    again = parse_auth(a.to_secret())
    assert again == a


# -- headers ------------------------------------------------------------------

def test_chat_headers_global_account():
    h = chat_headers(parse_auth(NESTED_SECRET))
    assert h["Authorization"] == "Bearer at-1"
    assert h["X-User-Id"] == "10001"
    assert h["X-Enterprise-Id"] == "ent-9"
    assert h["X-Domain"] == "workbuddy.ai"
    assert h["Origin"] == "https://www.workbuddy.ai"
    assert h["X-Product"] == "SaaS"
    assert "X-Refresh-Token" not in h  # red line: never on chat requests


def test_chat_headers_x_no_placeholders():
    h = chat_headers(parse_auth(json.dumps({"accessToken": "at"})))
    assert h["X-No-User-Id"] == "1"
    assert h["X-No-Enterprise-Id"] == "1"
    assert h["X-No-Department-Info"] == "1"
    assert h["Authorization"] == "Bearer at"
    assert h["Origin"] == "https://www.codebuddy.cn"


def test_refresh_headers_carry_refresh_token():
    h = refresh_headers(parse_auth(NESTED_SECRET))
    assert h["X-Refresh-Token"] == "rt-1"
    assert h["X-Auth-Refresh-Source"] == "workbuddy"
    assert h["X-Enterprise-Id"] == "ent-9"


# -- adapter -------------------------------------------------------------------

def test_headers_bare_token_fallback():
    a = WorkBuddyAdapter()
    h = a.headers(ProviderKeyRef(label="k", secret="raw-token-123"))
    assert h["Authorization"] == "Bearer raw-token-123"
    assert h["X-Product"] == "SaaS"


def test_build_url_uses_v2_path():
    a = WorkBuddyAdapter()
    assert a.build_url("https://copilot.tencent.com", "m", True) \
        == "https://copilot.tencent.com/v2/chat/completions"
    assert a.build_url("", "m", True) \
        == "https://copilot.tencent.com/v2/chat/completions"


def test_build_url_for_key_routes_by_auth_domain():
    """Global tokens 401 on the CN host and vice versa — the account's own
    domain must pick the upstream (mirrors Go chatBase(auth))."""
    a = WorkBuddyAdapter()
    g = a.build_url_for_key("https://copilot.tencent.com", "m", True,
                            ProviderKeyRef(label="k", secret=NESTED_SECRET))
    assert g == "https://www.workbuddy.ai/v2/chat/completions"
    c = a.build_url_for_key("https://copilot.tencent.com", "m", True,
                            ProviderKeyRef(label="k", secret=FLAT_SECRET))
    assert c == "https://copilot.tencent.com/v2/chat/completions"
    bare = a.build_url_for_key("https://my-proxy.example", "m", True,
                               ProviderKeyRef(label="k", secret="raw-token"))
    assert bare == "https://my-proxy.example/v2/chat/completions"


def test_encode_forces_stream_and_strips_stream_options():
    a = WorkBuddyAdapter()
    body = a.encode_request(_req(stream=True, stream_options_include_usage=True),
                            "glm-5.3", {"max_tokens": 10, "extra_body": {},
                                        "drop_params": True})
    assert body["stream"] is True
    assert "stream_options" not in body


def test_encode_history_reasoning_stripped():
    a = WorkBuddyAdapter()
    msg = ir.Message(role="assistant", parts=[
        ir.ThinkingPart(text="thoughts"), ir.TextPart(text="answer")])
    body = a.encode_request(_req(messages=[ir.Message(role="user", parts=[ir.TextPart(text="q")]),
                                           msg]),
                            "kimi-k3", {"max_tokens": 10, "extra_body": {},
                                        "drop_params": True})
    assert "reasoning_content" not in body["messages"][1]


def test_tool_choice_named_collapses_to_name_string():
    a = WorkBuddyAdapter()
    tool = ir.Tool(name="get_weather", description="d", parameters_json_schema={})
    body = a.encode_request(_req(tools=[tool], tool_choice=ir.ToolChoiceNamed("get_weather")),
                            "m", {"max_tokens": 10, "extra_body": {}, "drop_params": True})
    assert body["tool_choice"] == "get_weather"
    assert body["stream"] is True


def test_normalize_tool_choice_none_suppresses_tools():
    body = {"tool_choice": "none", "tools": [{"type": "function"}]}
    _normalize_tool_choice(body)
    assert "tool_choice" not in body
    assert "tools" not in body

    body = {"tool_choice": {"type": "none"}, "tools": [{"type": "function"}]}
    _normalize_tool_choice(body)
    assert "tool_choice" not in body
    assert "tools" not in body


def test_normalize_tool_choice_objects_to_strings():
    assert _normalize({"tool_choice": {"type": "auto"}}) == "auto"
    assert _normalize({"tool_choice": {"type": "required"}}) == "required"
    assert _normalize({"tool_choice": {
        "type": "function", "function": {"name": "f"}}}) == "f"
    assert _normalize({"tool_choice": {"type": "function"}}) == "auto"
    assert "tool_choice" not in _normalize_raw({"tool_choice": {"type": "weird"}})


def _normalize(body):
    out = dict(body)
    _normalize_tool_choice(out)
    return out["tool_choice"]


def _normalize_raw(body):
    out = dict(body)
    _normalize_tool_choice(out)
    return out


def test_sanitize_strips_fingerprints():
    dirty = ("x-anthropic-billing-header: a=1; "
             "You are Claude Code, Anthropic's official CLI for Claude.")
    clean = _sanitize_text(dirty)
    assert "x-anthropic-billing-header" not in clean
    assert "official CLI tool" in clean
    plain = _sanitize_text("totally normal prompt")
    assert plain == "totally normal prompt"


# -- envelope + SSE -------------------------------------------------------------

def test_decode_response_unwraps_envelope_and_maps_error():
    a = WorkBuddyAdapter()
    with pytest.raises(WiwiError) as e:
        a.decode_response(200, json.dumps({"code": 11101, "msg": "no stream"}).encode())
    assert e.value.status == 502

    ok = {"choices": [{"message": {"role": "assistant", "content": "hi"},
                       "finish_reason": "stop"}]}
    wrapped = {"code": 0, "msg": "ok", "data": ok}
    turn = a.decode_response(200, json.dumps(wrapped).encode())
    assert turn.text == "hi"


def test_decode_stream_event_envelope_error():
    a = WorkBuddyAdapter()
    deltas = a.decode_stream_event("message", json.dumps(
        {"code": 11102, "msg": "service info not found"}))
    assert len(deltas) == 1
    assert isinstance(deltas[0], dl.StreamError)
    assert deltas[0].kind == "status"


def test_decode_stream_event_session_dead_maps_to_auth():
    a = WorkBuddyAdapter()
    deltas = a.decode_stream_event("message", json.dumps(
        {"code": 12153, "msg": "Offline user session not found"}))
    assert isinstance(deltas[0], dl.StreamError)
    assert "session dead" in deltas[0].message


def test_decode_stream_event_normal_chunks_pass_through():
    a = WorkBuddyAdapter()
    chunk = json.dumps({"choices": [{"delta": {"content": "he"}}]})
    deltas = a.decode_stream_event("message", chunk)
    assert isinstance(deltas[0], dl.TextDelta)
    assert deltas[0].text == "he"


def test_envelope_error_credit_markers():
    a = WorkBuddyAdapter()
    with pytest.raises(WiwiError) as e:
        a.decode_response(200, json.dumps({"code": 7, "msg": "积分不足"}).encode())
    assert e.value.etype == "budget_exceeded"
    assert e.value.status == 402


# -- refresh flow (respx) --------------------------------------------------------

@respx.mock
async def test_refresh_token_success_rotates():
    auth = parse_auth(NESTED_SECRET)
    route = respx.post("https://www.workbuddy.ai/v2/plugin/auth/token/refresh") \
        .respond(json={"code": 0, "msg": "ok", "data": {
            "accessToken": "at-new", "refreshToken": "rt-new",
            "expiresIn": 7200, "domain": "workbuddy.ai"}})
    async with httpx.AsyncClient() as client:
        outcome = await refresh_token(auth, client)
    assert route.called
    assert outcome.ok and outcome.auth is not None
    assert outcome.auth.access_token == "at-new"
    assert outcome.auth.refresh_token == "rt-new"
    assert outcome.auth.expires_at > int(time.time())
    req = route.calls[0].request
    assert req.headers["X-Refresh-Token"] == "rt-1"
    assert req.headers["X-Enterprise-Id"] == "ent-9"
    assert req.headers["X-Auth-Refresh-Source"] == "workbuddy"
    # original object untouched (refresh returns a new record)
    assert auth.access_token == "at-1"


@respx.mock
async def test_refresh_token_session_dead_unrecoverable():
    auth = parse_auth(NESTED_SECRET)
    respx.post("https://www.workbuddy.ai/v2/plugin/auth/token/refresh") \
        .respond(401, json={"code": 12153, "msg": "Offline user session not found"})
    outcome = await refresh_token(auth)
    assert not outcome.ok
    assert outcome.unrecoverable


@respx.mock
async def test_refresh_token_preserves_expiry_when_missing():
    auth = parse_auth(NESTED_SECRET)
    respx.post("https://www.workbuddy.ai/v2/plugin/auth/token/refresh") \
        .respond(json={"code": 0, "data": {"accessToken": "at-2"}})
    outcome = await refresh_token(auth)
    assert outcome.ok and outcome.auth.expires_at == auth.expires_at


async def test_refresh_without_refresh_token_is_unrecoverable():
    auth = parse_auth(json.dumps({"accessToken": "at"}))
    outcome = await refresh_token(auth)
    assert not outcome.ok and outcome.unrecoverable


# -- registry wiring ---------------------------------------------------------------

def test_registry_returns_workbuddy_adapter():
    a = get_adapter("workbuddy")
    assert a.provider_type == "workbuddy"
    assert a.force_stream is True
    fresh = fresh_adapter("workbuddy")
    assert fresh is not a
