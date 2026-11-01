"""Round 191 — the SSE ingest split frames on characters that are legal inside a
JSON string, truncating any frame whose content contains one.

``httpx.Response.aiter_lines`` splits on ``str.splitlines``' full newline set —
``\\n \\r \\x0b \\x0c \\x1c \\x1d \\x1e \\x85 \\u2028 \\u2029`` — not on the SSE
wire's own line terminator. Three of those (``\\x85`` NEL, ``\\u2028`` LINE
SEPARATOR, ``\\u2029`` PARAGRAPH SEPARATOR) are legal *unescaped* inside a JSON
string, and ``orjson`` — the serializer every adapter's upstream uses, and the
one wiwi itself writes with — emits them raw. A model that emits U+2028 in its
reply therefore produces a ``data:`` line the transport tears in half: the
parser sees a truncated payload, every decoder answers ``[]`` for it (a bad
JSON frame is dropped, not raised), and the client silently loses that delta.

Two places read upstream SSE lines this way: the stream pump
(``Gateway.stream``, ``wiwi/core/gateway.py``) and the healer's probe
classifier (``probe_verdict``, ``wiwi/core/recovery.py``). Both are fixed by
splitting on the SSE terminator itself — :func:`wiwi.streaming.sse.iter_sse_lines`.
"""

from __future__ import annotations

import contextlib
import json

import httpx
import orjson
import pytest
from asgi_lifespan import LifespanManager
from respx import MockRouter

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app
from wiwi.streaming.sse import LineSSEParser, iter_sse_lines

H = {"Authorization": "Bearer sk-wiwi-master-test"}

RELAY_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
providers:
  - name: relay
    provider: anthropic
    base_url: https://relay.example.com
    keys:
      - {label: main, key: sk-test}
model_list:
  - model_name: gpt-test
    wiwi_params:
      provider: relay
      model: claude-x
"""

OPENAI_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
providers:
  - name: oai
    provider: openai
    base_url: https://chat.example.com/v1
    keys:
      - {label: main, key: sk-test}
model_list:
  - model_name: gpt-x
    wiwi_params:
      provider: oai
      model: gpt-x
"""


@contextlib.asynccontextmanager
async def client_for(cfg=None):
    app = create_app(cfg or load_config_from_string(RELAY_CFG))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            c.wiwi = app.state.wiwi
            yield c


@pytest.fixture
async def client():
    async with client_for() as c:
        yield c


# ---------------------------------------------------------------------------
# 1. The transport split itself — the root cause
# ---------------------------------------------------------------------------

# Every character Python's ``str.splitlines`` treats as a line boundary.
SPLITLINES_CHARS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
# The subset that is NOT an SSE line terminator \u2014 the ones that used to tear a
# frame apart wrongly. CR and LF are real terminators, covered separately.
NOT_A_TERMINATOR = "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
# Of those, the ones ``orjson`` emits RAW inside a JSON string, i.e. the ones a
# real upstream body can contain without the model author escaping anything.
ORJSON_RAW_CHARS = "\x85\u2028\u2029"


def test_the_character_sets_are_what_they_claim():
    """Guard the probe itself: these must really be splitlines boundaries."""
    for ch in SPLITLINES_CHARS:
        assert len(("a" + ch + "b").splitlines()) > 1, hex(ord(ch))
    for ch in NOT_A_TERMINATOR:
        assert ch not in "\r\n", hex(ord(ch))


@pytest.mark.parametrize("ch", NOT_A_TERMINATOR)
async def test_iter_sse_lines_does_not_split_on_a_legal_payload_character(ch):
    """A frame carrying *ch* inside its JSON string is delivered whole."""
    frame = 'data: {"content": "a' + ch + 'b"}\n\n'

    async def source():
        yield frame.encode()

    lines = [ln async for ln in iter_sse_lines(source())]
    # Exactly the frame's own two lines: the data line and the blank terminator.
    assert lines == ['data: {"content": "a' + ch + 'b"}', ""], repr(lines)


async def test_iter_sse_lines_keeps_crlf_and_bare_cr_terminators():
    """The wire terminator itself still splits: CRLF and bare CR both count."""

    async def source():
        yield b"data: one\r\n\r\n"
        yield b"data: two\r\r"

    lines = [ln async for ln in iter_sse_lines(source())]
    assert lines == ["data: one", "", "data: two", ""], repr(lines)


async def test_iter_sse_lines_reassembles_a_line_split_across_chunks():
    """A terminator straddling two network chunks is still one line."""
    async def source():
        yield b'data: {"a"'
        yield b': 1}\n'
        yield b"\ndata: [DONE]\n\n"

    lines = [ln async for ln in iter_sse_lines(source())]
    assert lines == ['data: {"a": 1}', "", "data: [DONE]", ""], repr(lines)


async def test_iter_sse_lines_emits_a_trailing_unterminated_line():
    """A body closing on its last data line with no terminator keeps it.

    This is the DeepSeek/B.A.I ``"data: [DONE]"``-at-EOF shape that
    ``LineSSEParser.flush`` exists for; the splitter must not swallow it.
    """
    async def source():
        yield b'data: {"a": 1}\n\ndata: [DONE]'

    lines = [ln async for ln in iter_sse_lines(source())]
    assert lines == ['data: {"a": 1}', "", "data: [DONE]"], repr(lines)


async def test_iter_sse_lines_handles_a_multibyte_char_split_across_chunks():
    """UTF-8 sequence boundaries are respected, not decoded per chunk."""
    payload = 'data: {"t": "café \u2028 ok"}\n\n'
    raw = payload.encode()
    # Cut inside the 3-byte U+2028 sequence.
    cut = raw.index(b"\xe2\x80\xa8") + 1

    async def source():
        yield raw[:cut]
        yield raw[cut:]

    lines = [ln async for ln in iter_sse_lines(source())]
    assert lines == ['data: {"t": "café \u2028 ok"}', ""], repr(lines)


# ---------------------------------------------------------------------------
# 2. The parser accepts the reassembled frame
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ch", ORJSON_RAW_CHARS)
async def test_a_frame_carrying_a_raw_control_char_parses(ch):
    """End of the chain: the split lines produce a decodable event."""
    chunk = orjson.dumps({"content": "a" + ch + "b"})
    assert ch.encode() in chunk, "orjson no longer emits this char raw"

    async def source():
        yield b"data: " + chunk + b"\n\n"

    events = []
    parser = LineSSEParser()
    async for line in iter_sse_lines(source()):
        evt = parser.feed_line(line)
        if evt is not None:
            events.append(evt)
    evt = parser.flush()
    if evt is not None:
        events.append(evt)

    assert [e.data for e in events] == [chunk.decode()]
    assert orjson.loads(events[0].data) == {"content": "a" + ch + "b"}


# ---------------------------------------------------------------------------
# 3. End to end — the delta reaches the client
# ---------------------------------------------------------------------------

ANTHROPIC_SSE_HEAD = (
    'data: {"type":"message_start","message":{"model":"claude-x",'
    '"usage":{"input_tokens":5}}}\n\n'
    'data: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}\n\n'
)


async def test_u2028_in_content_reaches_the_client_intact(client):
    """A model reply containing U+2028 must not lose its delta.

    Pre-fix the transport tore the ``data:`` line at U+2028, the Anthropic
    decoder answered ``[]`` for the unparseable fragment and for the orphaned
    tail, and the client received the message with that text missing — a
    silently corrupted turn with a clean ``message_stop``.
    """
    text = "before\u2028after"
    delta = orjson.dumps({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }).decode()
    assert "\u2028" in delta, "orjson escaped it; the probe is void"

    tail = (
        f"data: {delta}\n\n"
        'data: {"type":"content_block_stop","index":0}\n\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":3}}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )

    with MockRouter() as router:
        router.post("https://relay.example.com/messages").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=(ANTHROPIC_SSE_HEAD + tail).encode()))
        async with client.stream(
            "POST", "/v1/messages", headers=H,
            json={"model": "gpt-test", "max_tokens": 100, "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]},
        ) as r:
            body = b"".join([chunk async for chunk in r.aiter_bytes()])

    out = body.decode(errors="replace")
    assert "before\u2028after" in out, out
    assert '"error"' not in out, out


async def test_u2028_in_a_chat_completion_reaches_the_client_intact():
    """The same corruption on the OpenAI-Chat wire (``orjson`` upstream)."""
    text = "x\u2029y"
    chunk = orjson.dumps({
        "id": "c1", "object": "chat.completion.chunk", "created": 1,
        "model": "gpt-x",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }).decode()

    upstream = (
        f"data: {chunk}\n\n"
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,'
        '"model":"gpt-x","choices":[{"index":0,"delta":{},'
        '"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )

    cfg = load_config_from_string(OPENAI_CFG)
    async with client_for(cfg) as client:
        with MockRouter() as router:
            router.post("https://chat.example.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200, headers={"content-type": "text/event-stream"},
                    content=upstream.encode()))
            async with client.stream(
                "POST", "/v1/chat/completions", headers=H,
                json={"model": "gpt-x", "max_tokens": 100, "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]},
            ) as r:
                body = b"".join([chunk_ async for chunk_ in r.aiter_bytes()])

    out = body.decode(errors="replace")
    assert "x\u2029y" in out, out
    assert '"error"' not in out, out


# ---------------------------------------------------------------------------
# 4. The healer's probe classifier reads the same body shape
# ---------------------------------------------------------------------------

async def test_probe_verdict_sees_an_error_envelope_whose_message_has_u2028():
    """A 200 probe whose *error* frame carries U+2028 still reads as an error.

    ``_body_is_error_envelope`` split the body with ``str.splitlines()``, so an
    error envelope containing U+2028 was torn in two: neither half parsed, the
    classifier found no error object, and the probe reported HEALTHY \u2014 which is
    how the healer restores a key that is still failing. The U+2028 has to be
    inside the ERROR frame for this to be a real test; a separator in a content
    frame is harmless because the frame after it still parses.
    """
    from wiwi.core.recovery import ProbeVerdict, probe_verdict

    body = (
        'data: {"type": "error", "error": {"message": "dead\u2028link"}}\n\n'
    ).encode()
    assert probe_verdict(200, body) is ProbeVerdict.UNREACHABLE


async def test_probe_verdict_sees_an_openai_error_envelope_with_u2028():
    """The OpenAI-shaped sibling: ``{"error": {"message": \u2026}}``."""
    from wiwi.core.recovery import ProbeVerdict, probe_verdict

    body = 'data: {"error": {"message": "bad\u2029key", "type": "auth"}}\n\n'.encode()
    assert probe_verdict(200, body) is ProbeVerdict.UNREACHABLE


async def test_probe_verdict_still_reads_a_healthy_sse_body():
    from wiwi.core.recovery import ProbeVerdict, probe_verdict

    body = 'data: {"content": "a\u2028b"}\n\n'.encode()
    assert probe_verdict(200, body) is ProbeVerdict.HEALTHY


# ---------------------------------------------------------------------------
# 5. The control — a plain body is unaffected
# ---------------------------------------------------------------------------

async def test_plain_ascii_body_is_unchanged(client):
    with MockRouter() as router:
        router.post("https://relay.example.com/messages").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=(ANTHROPIC_SSE_HEAD
                         + 'data: {"type":"content_block_delta","index":0,'
                           '"delta":{"type":"text_delta","text":"plain"}}\n\n'
                         + 'data: {"type":"content_block_stop","index":0}\n\n'
                         + 'data: {"type":"message_delta","delta":'
                           '{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
                         + 'data: {"type":"message_stop"}\n\n').encode()))
        async with client.stream(
            "POST", "/v1/messages", headers=H,
            json={"model": "gpt-test", "max_tokens": 100, "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]},
        ) as r:
            body = b"".join([c async for c in r.aiter_bytes()])

    out = body.decode(errors="replace")
    assert "plain" in out, out
    assert '"error"' not in out, out
    # Sanity: the body is still well-formed SSE with JSON payloads.
    for frame in out.split("\n\n"):
        for line in frame.split("\n"):
            if line.startswith("data: "):
                json.loads(line[6:])
