"""Tests for the Cline adapter: WorkOS auth header, client fingerprint headers,
URL building, force-stream semantics, {success,data} envelope unwrap, and
mid-stream error handling.

Cline (api.cline.bot) is an OpenAI-compatible gateway that requires:
- Authorization: Bearer workos:<token> (prefix mandatory, auto-prepended)
- A full client-identification header set (HTTP-Referer, X-Title, X-CLIENT-*)
- Streaming-only upstream (non-streaming requests fail), so the adapter
  forces stream:True in the encoded body while the gateway re-assembles.
"""

import orjson

from wiwi.config import PROVIDER_TYPES
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.registry import get_adapter
from wiwi.streaming import deltas as dl
from wiwi.wire import openai_chat as oc

# -- fixtures -----------------------------------------------------------


def make_req(**overrides):
    body = {
        "model": "z-ai/glm-5.2",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
    body.update(overrides)
    return oc.decode_request(body)


def key(secret="abc123token"):
    return ProviderKeyRef(label="default", secret=secret)


# -- registration ---------------------------------------------------------


def test_cline_in_provider_types():
    assert "cline" in PROVIDER_TYPES


def test_registry_returns_cline_adapter():
    a = get_adapter("cline")
    assert a.provider_type == "cline"


# -- headers: workos prefix + fingerprint --------------------------------


def test_headers_add_workos_prefix():
    h = get_adapter("cline").headers(key("abc123token"))
    assert h["Authorization"] == "Bearer workos:abc123token"


def test_headers_preserve_existing_workos_prefix():
    h = get_adapter("cline").headers(key("workos:already"))
    assert h["Authorization"] == "Bearer workos:already"


def test_headers_include_client_fingerprint():
    h = get_adapter("cline").headers(key())
    assert h["HTTP-Referer"] == "https://cline.bot"
    assert h["X-Title"] == "Cline"
    assert h["X-CLIENT-TYPE"] == "wiwi"
    assert h["X-CLIENT-VERSION"] == "0.1.0"
    assert h["X-CORE-VERSION"] == "0.1.0"
    assert "X-PLATFORM" in h
    assert "X-PLATFORM-VERSION" in h
    assert h["X-IS-MULTIROOT"] == "false"


def test_headers_user_agent_cline_prefixed():
    h = get_adapter("cline").headers(key())
    assert h["User-Agent"].startswith("Cline/")


def test_headers_sanitize_newlines():
    """Header values with CR/LF/NUL are dropped, never forwarded."""
    a = get_adapter("cline")
    h = a.headers(key())
    a.set_header_context({"client_type": "evil\r\ninjected"})
    h = a.headers(key())
    assert "\r" not in h["X-CLIENT-TYPE"]
    assert "\n" not in h["X-CLIENT-TYPE"]


# -- URL -------------------------------------------------------------------


def test_build_url():
    a = get_adapter("cline")
    assert (a.build_url("https://api.cline.bot/api/v1", "z-ai/glm-5.2",
                        False, "chat")
            == "https://api.cline.bot/api/v1/chat/completions")


# -- encode: force-stream ---------------------------------------------------


def test_encode_forces_stream_upstream():
    """Cline upstream only implements streaming; even for stream=False client
    requests the encoded body must set stream:True (gateway re-assembles)."""
    req = make_req(stream=False)
    body = get_adapter("cline").encode_request(req, "z-ai/glm-5.2", {})
    assert body["stream"] is True


def test_encode_keeps_stream_true():
    req = make_req(stream=True)
    body = get_adapter("cline").encode_request(req, "z-ai/glm-5.2", {})
    assert body["stream"] is True


def test_encode_drops_stream_options():
    """stream_options was already forwarded for stream=False clients; upstream
    Cline rejects it, so it must not appear in the encoded body."""
    req = make_req(stream=True)
    req.stream_options_include_usage = True
    body = get_adapter("cline").encode_request(req, "z-ai/glm-5.2", {})
    assert "stream_options" not in body


# -- decode: {success,data} envelope unwrap ----------------------------------


def test_decode_unwraps_data_envelope():
    """Cline sometimes wraps the OpenAI payload in {success, data}."""
    inner = {
        "id": "x1", "choices": [{
            "message": {"role": "assistant", "content": "hi"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    wrapped = orjson.dumps({"success": True, "data": inner})
    turn = get_adapter("cline").decode_response(200, wrapped)
    assert turn.text == "hi"
    assert turn.usage.prompt_tokens == 3
    assert turn.usage.completion_tokens == 2


def test_decode_plain_body_passthrough():
    body = orjson.dumps({
        "choices": [{
            "message": {"role": "assistant", "content": "plain"},
            "finish_reason": "stop",
        }],
    })
    turn = get_adapter("cline").decode_response(200, body)
    assert turn.text == "plain"


def test_decode_stream_unwraps_data_envelope():
    """SSE chunks may also carry the {success,data} wrapping."""
    chunk = orjson.dumps({
        "success": True,
        "data": {"choices": [{"delta": {"content": "sse-hi"}}]},
    }).decode()
    deltas = get_adapter("cline").decode_stream_event("", chunk)
    assert any(isinstance(d, dl.TextDelta) and d.text == "sse-hi" for d in deltas)


# -- decode: reasoning + tool calls still work (inherited OpenAI decode) ------


def test_decode_reasoning_content():
    body = orjson.dumps({
        "choices": [{
            "message": {"role": "assistant", "content": "ans",
                        "reasoning_content": "because"},
            "finish_reason": "stop",
        }],
    })
    turn = get_adapter("cline").decode_response(200, body)
    assert turn.text == "ans"
    assert turn.thinking[0].text == "because"


def test_decode_tool_calls():
    body = orjson.dumps({
        "choices": [{
            "message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "t1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"p":"x"}'}}],
            },
            "finish_reason": "tool_calls",
        }],
    })
    turn = get_adapter("cline").decode_response(200, body)
    assert turn.tool_calls[0].name == "read_file"
    assert turn.stop_reason == "tool_call"


# -- mid-stream errors -------------------------------------------------------


def test_stream_mid_stream_error_surfaces():
    chunk = orjson.dumps({
        "error": {"message": "quota exhausted", "type": "insufficient_quota"},
    }).decode()
    deltas = get_adapter("cline").decode_stream_event("", chunk)
    assert any(isinstance(d, dl.StreamError) and "quota exhausted" in d.message
               for d in deltas)


def test_stream_done():
    assert get_adapter("cline").decode_stream_event("", "[DONE]") == [dl.StreamEnd()]


# -- header context pass-through (task id) ------------------------------------


def test_task_id_forwarded_when_provided():
    a = get_adapter("cline")
    a.set_header_context({"task_id": "task-42"})
    h = a.headers(key())
    assert h["X-Task-ID"] == "task-42"


def test_no_task_id_by_default():
    a = get_adapter("cline")
    h = a.headers(key())
    assert "X-Task-ID" not in h
