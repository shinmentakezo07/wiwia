"""Round-20 regression tests: SSE parser flush + streaming hardens.

Regression targets (see the SSE/streaming audit and the round-20 fix plan):

- ``LineSSEParser`` buffered the final SSE frame when a stream ended without a
  trailing blank line (DeepSeek/B.A.I send ``"data: [DONE]\\n"`` with no final
  ``"\\n\\n"``). The frame was never emitted, so a ``[DONE]``-terminated stream
  looked like a mid-stream drop and wiwi emitted ``StreamError`` instead of a
  clean ``finish_reason``. Fix: ``flush()`` on the parser + wiring at the two
  ``gateway.py`` consumers.
"""

from __future__ import annotations

import asyncio
from typing import Any

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
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import Router
from wiwi.server.app import _inject_id, create_app
from wiwi.streaming import deltas as dl
from wiwi.streaming.sse import LineSSEParser
from wiwi.wire import openai_chat as oc

# ---------------------------------------------------------------------------
# LineSSEParser.flush
# ---------------------------------------------------------------------------

def test_sse_parser_flush_emits_final_frame():
    """A stream ending with "data: [DONE]\\n" (no trailing blank line) must
    still yield the frame on flush() and clear its buffer."""
    p = LineSSEParser()
    assert p.feed_line("data: [DONE]") is None  # buffered, not emitted
    evt = p.flush()
    assert evt is not None
    assert evt.event == ""
    assert evt.data == "[DONE]"
    # buffer is drained; a second flush is a no-op.
    assert p.flush() is None


def test_sse_parser_flush_noop_after_blank_line():
    """A normal "data: x\\n\\n" stream already emitted on the blank line; the
    after-loop flush must not double-emit it."""
    p = LineSSEParser()
    evt = p.feed_line("data: x")
    assert evt is None
    evt2 = p.feed_line("")  # blank line -> emit
    assert evt2 is not None and evt2.data == "x"
    assert p.flush() is None  # nothing left to flush


def test_sse_parser_flush_joins_multiline_data():
    """Multi-line data (SSE \\n-joined) flushes as a single joined payload."""
    p = LineSSEParser()
    p.feed_line("data: a")
    p.feed_line("data: b")
    evt = p.flush()
    assert evt is not None and evt.data == "a\nb"


# ---------------------------------------------------------------------------
# _inject_id must preserve SSE frame terminators
# ---------------------------------------------------------------------------

def test_inject_id_preserves_single_frame_terminator():
    """A single SSE frame ending in b'\\n\\n' must keep its terminator so the
    next yielded chunk does not fuse onto it (SSE reconnect framing)."""
    out = _inject_id(b"data: one\n\n", 1)
    assert out == b"id: 1\ndata: one\n\n"
    assert out.endswith(b"\n\n")


def test_inject_id_preserves_terminator_across_two_chunks():
    """Two consecutively-yielded chunks each keep their own '\\n\\n' terminator,
    so the client's SSE parser sees two distinct frames (id:1 then id:2)."""
    chunk1 = _inject_id(b"data: one\n\n", 1)
    chunk2 = _inject_id(b"data: two\n\n", 2)
    concat = chunk1 + chunk2
    assert concat.endswith(b"\n\n")
    frames = concat.split(b"\n\n")
    assert frames == [b"id: 1\ndata: one", b"id: 2\ndata: two", b""]


def test_inject_id_tags_every_frame_in_multi_frame_chunk():
    """A chunk already containing two frames (blank-line-joined) tags each with
    the same id line, keeping each frame terminated."""
    chunk = b"data: one\n\ndata: two\n\n"
    out = _inject_id(chunk, 7)
    assert out.count(b"\n\n") == 2
    assert out.count(b"id: 7\n") == 2


# ---------------------------------------------------------------------------
# Gateway streaming: [DONE]-terminated stream with NO trailing blank line
# ---------------------------------------------------------------------------

def _gw_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://round20.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1")])],
        model_list=[ModelEntry(model_name="gpt-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-x"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=1,
                                       cooldown_time=60.0),
    )


def _gw_req() -> ir.Request:
    return ir.Request(model="gpt-x",
                      messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
                      stream=True)


def _text_chunk(text: str) -> str:
    return ('{"id":"c1","object":"chat.completion.chunk","model":"gpt-x",'
            f'"choices":[{{"index":0,"delta":{{"content":"{text}"}},'
            '"finish_reason":null}]}')


@respx.mock
async def test_done_no_blank_line_is_clean_completion():
    """DeepSeek/B.A.I close the stream with "data: [DONE]\\n" and NO trailing
    blank line. This must be a clean completion (synthesized Finish('stop')) —
    the un-flushed parser previously turned it into a StreamError."""
    # Last frame is missing its newline: "data: [DONE]\n" (closed by EOF, no
    # fence blank-line after it). Simulate via httpx body ending with "\n".
    body = (
        f"data: {_text_chunk('hello')}\n\n".encode()
        + b"data: [DONE]\n"  # no trailing blank line
    )
    gw = Gateway(Router(_gw_config()), CostEngine())
    try:
        respx.post("https://round20.example/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=body))
        ctx = RequestContext(surface="chat", ir_req=_gw_req(), group="gpt-x")
        out = []
        async for d in gw.stream(ctx):
            out.append(d)
        assert not any(isinstance(d, dl.StreamError) for d in out), \
            "a [DONE]-terminated stream without a trailing blank line must " \
            "not be treated as a mid-stream drop"
        assert any(isinstance(d, dl.Finish) and d.stop_reason == "stop"
                   for d in out)
        assert any(isinstance(d, dl.StreamEnd) for d in out)
        key = next(k for k in gw.router.providers["p1"].keys
                   if k.label == ctx.provider_key.label)
        assert key.status == "active"
    finally:
        await gw.aclose()


# ---------------------------------------------------------------------------
# _stream_response must aclose the pump stream even when the encoder raises
# ---------------------------------------------------------------------------

def _app_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://round20.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1")])],
        model_list=[ModelEntry(model_name="gpt-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-x"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=1,
                                       cooldown_time=60.0),
    )


class _CloseTracker:
    """Wrap the gateway's stream generator to record ``aclose()`` calls made on
    the wrapper object itself — exactly what ``_stream_response`` does to the
    ``stream`` it is handed. Unlike monkeypatching the generator's teardown,
    this only flips when the consumer explicitly acloses the stream."""

    def __init__(self, gen):
        self._gen = gen
        self.aclosed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._gen.__anext__()

    async def aclose(self):
        self.aclosed = True
        await self._gen.aclose()


@respx.mock
async def test_stream_response_closes_pump_on_encoder_error():
    """If encoder.feed() raises mid-stream (abnormal), the gateway pump's
    stream must be aclosed so its upstream connection is released — not left
    running to leak."""
    app = create_app(_app_config())
    held: dict[str, _CloseTracker] = {}

    async with LifespanManager(app):
        gw = app.state.wiwi.gateways["chat"]
        orig_stream = gw.stream

        def spy_stream(ctx):
            gen = orig_stream(ctx)
            tracker = _CloseTracker(gen)
            held["t"] = tracker
            return tracker

        gw.stream = spy_stream

        # Make the encoder raise on the SECOND feed() call (mid-stream).
        orig_feed = oc.ChatStreamEncoder.feed
        calls = {"n": 0}

        def boom_feed(self, d):
            if calls["n"] >= 1:
                raise RuntimeError("encoder exploded")
            calls["n"] += 1
            return orig_feed(self, d)

        oc.ChatStreamEncoder.feed = boom_feed

        try:
            respx.post("https://round20.example/v1/chat/completions").mock(
                return_value=httpx.Response(200, content=(
                    b"data: " + _text_chunk("hello").encode() + b"\n\n"
                    b"data: " + _text_chunk(" world").encode() + b"\n\n"
                )))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as c:
                # The encoder raises mid-stream; the ASGI app lets the
                # exception escape (it has already written a partial body), and
                # the important contract is that it acloses the pump stream so
                # the upstream connection is released.
                with pytest.raises(RuntimeError, match="encoder exploded"):
                    await c.post("/v1/chat/completions", json={
                        "model": "gpt-x", "stream": True,
                        "messages": [{"role": "user", "content": "hi"}]},
                        headers={"Authorization": "Bearer sk-wiwi-master-test"})
        finally:
            oc.ChatStreamEncoder.feed = orig_feed
            await gw.aclose()

    tracker = held.get("t")
    assert tracker is not None
    assert tracker.aclosed, \
        "the gateway pump stream was not aclosed after the encoder raised"


# ---------------------------------------------------------------------------
# Anthropic surface: an errored stream must still terminate with message_stop
# ---------------------------------------------------------------------------

@respx.mock
async def test_anthropic_error_path_ends_with_message_stop():
    """When the pump converts a mid-stream truncation (content, no finish, no
    [DONE]) to a StreamError, the inbound Anthropic encoder emits an ``error``
    frame — but the client's SSE reader is left waiting for the terminal
    ``message_stop``. The stream must still be well-formed: error then
    message_stop."""
    app = create_app(_app_config())
    # Upstream OpenAI chat: one content chunk, then the connection ends with no
    # finish_reason and no [DONE] (a real mid-stream drop).
    body = (
        b"data: " + _text_chunk("hello").encode() + b"\n\n"
    )
    async with LifespanManager(app):
        respx.post("https://round20.example/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=body))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.post("/v1/messages", json={
                "model": "gpt-x", "max_tokens": 100, "stream": True,
                "messages": [{"role": "user", "content": "hi"}]},
                headers={"x-api-key": "sk-wiwi-master-test",
                         "anthropic-version": "2023-06-01"})
        text = r.text
        # The stream must have produced an error frame AND then terminated with
        # a message_stop, so the dialec-correct SSE stream is well-formed.
        assert 'event: error' in text or '"type": "error"' in text, \
            f"expected an error frame, got:\n{text}"
        # Terminal frame must be message_stop, present after the error.
        assert '"type": "message_stop"' in text, \
            f"expected a terminating message_stop after the error, got:\n{text}"
        # message_stop is the LAST event in the stream (no dangling frames).
        assert text.rstrip().endswith('"type": "message_stop"}'), text


# ---------------------------------------------------------------------------
# Fix H1: concurrent streams must not corrupt each other's tool state
# ---------------------------------------------------------------------------

def _h1_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://round20-h1.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1"),
                                     KeyDef(label="k2", key="sk-2")])],
        model_list=[ModelEntry(model_name="gpt-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-x"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=1,
                                       cooldown_time=60.0),
    )


def _tool_chunk(index: int, args=None, cid=None, name=None) -> str:
    fn: dict[str, Any] = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    tc: dict[str, Any] = {"index": index, "function": fn}
    if cid is not None:
        tc["id"] = cid
    return orjson.dumps({"choices": [{"delta": {"tool_calls": [tc]}}]}).decode()


_H1_FINISH = orjson.dumps(
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}).decode()


def _sse(*parts: str) -> bytes:
    return b"".join(f"data: {p}\n\n".encode() for p in parts)


@respx.mock
async def test_concurrent_streams_do_not_corrupt_tool_state():
    """Two concurrent streams share one provider type. Stream A opens a tool
    call (id seen, args pending), then — before A's next upstream chunk
    arrives — stream B is acquired. With a shared adapter, B's acquisition
    reset wipes A's deferred open, so A emits args with no Open/Close at all.
    Each stream must own its decoding state."""
    gw = Gateway(Router(_h1_config()), CostEngine())

    async def body_a():
        # Chunk 1 opens the tool call and leaves its Open deferred (no args
        # yet). Then a window: B is acquired and would reset the shared state.
        yield _sse(_tool_chunk(0, cid="call_A", name="alpha"))
        await asyncio.sleep(0.15)
        yield _sse(_tool_chunk(0, args='{"a":1}'), _H1_FINISH, "[DONE]")

    async def body_b():
        yield _sse(_tool_chunk(0, cid="call_B", name="beta"),
                   _tool_chunk(0, args='{"b":2}'), _H1_FINISH, "[DONE]")

    async def drain(tag: str) -> tuple[str, list]:
        ctx = RequestContext(surface="chat", ir_req=_gw_req(), group="gpt-x")
        out = []
        async for d in gw.stream(ctx):
            out.append(d)
        return tag, out

    try:
        respx.post("https://round20-h1.example/v1/chat/completions").mock(
            side_effect=[httpx.Response(200, content=body_a()),
                         httpx.Response(200, content=body_b())])
        task_a = asyncio.create_task(drain("A"))
        # Let A connect and consume its first chunk, then start B — while A's
        # upstream is still mid-flight.
        await asyncio.sleep(0.05)
        _, out_b = await drain("B")
        _, out_a = await task_a

        for tag, out in (("A", out_a), ("B", out_b)):
            tool_deltas = [d for d in out if isinstance(
                d, (dl.ToolCallOpen, dl.ToolCallArgsDelta, dl.ToolCallClose))]
            opens = [d for d in tool_deltas if isinstance(d, dl.ToolCallOpen)]
            closes = [d for d in tool_deltas if isinstance(d, dl.ToolCallClose)]
            assert len(opens) == 1, \
                f"stream {tag} lost its ToolCallOpen: {tool_deltas}"
            assert len(closes) == 1, \
                f"stream {tag} lost its ToolCallClose: {tool_deltas}"
            expected = "call_A" if tag == "A" else "call_B"
            assert opens[0].id == expected, \
                f"stream {tag} got {opens[0].id!r}, expected {expected!r} — " \
                "another stream's tool call leaked in"
    finally:
        await gw.aclose()


def _h2_config(max_resumes: int) -> WiwiConfig:
    """Two providers in one model group plus a fallback group, with mid-stream
    resume enabled so a failed pump can be superseded by a new one."""
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://round20-h2a.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1")]),
                   ProviderDef(name="p2", provider="openai",
                               base_url="https://round20-h2b.example/v1",
                               keys=[KeyDef(label="k2", key="sk-2")])],
        model_list=[ModelEntry(model_name="gpt-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-x")),
                    ModelEntry(model_name="gpt-x-fb",
                               wiwi_params=DeploymentParams(provider="p2",
                                                            model="gpt-x"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(
            num_retries=0, allowed_fails=1, cooldown_time=60.0,
            stream_resume="enabled", stream_resume_max_retries=max_resumes,
            stream_idle_timeout_s=0.2,
            fallbacks={"gpt-x": ["gpt-x-fb", "gpt-x"]}),
    )


async def _h2_drain(hang_primary: bool, hang_fallback: bool) -> list:
    """Drive one stream whose upstream hangs if *hang_primary*, resuming onto a
    fallback that hangs if *hang_fallback*. Returns the emitted deltas."""
    gw = Gateway(Router(_h2_config(max_resumes=2)), CostEngine())

    async def hang_body():
        # One content chunk, then never finish -> idle timeout -> StreamError.
        yield _sse(_text_chunk("partial "))
        await asyncio.sleep(30)

    async def good_body():
        yield _sse(_text_chunk("resumed"),
                   orjson.dumps({"choices": [{"delta": {},
                                              "finish_reason": "stop"}]}).decode(),
                   "[DONE]")

    primary = hang_body if hang_primary else good_body
    fallback = hang_body if hang_fallback else good_body
    try:
        respx.post("https://round20-h2a.example/v1/chat/completions").mock(
            side_effect=[httpx.Response(200, content=primary())
                         for _ in range(4)])
        respx.post("https://round20-h2b.example/v1/chat/completions").mock(
            side_effect=[httpx.Response(200, content=fallback())
                         for _ in range(4)])
        ctx = RequestContext(surface="chat", ir_req=_gw_req(), group="gpt-x")
        out = []
        async for d in gw.stream(ctx):
            out.append(d)
        return out
    finally:
        await gw.aclose()


def _assert_single_logical_stream(out: list, label: str) -> None:
    """The IR contract allows exactly one StreamStart and exactly one terminal
    (StreamEnd xor StreamError), no matter how many pumps ran."""
    starts = [d for d in out if isinstance(d, dl.StreamStart)]
    assert len(starts) == 1, \
        f"{label}: expected exactly one StreamStart, got {len(starts)} " \
        f"(two pumps emitted deltas for one logical stream)"
    terminals = [d for d in out
                 if isinstance(d, (dl.StreamEnd, dl.StreamError))]
    assert len(terminals) == 1, \
        f"{label}: expected exactly one terminal, got {len(terminals)}: " \
        f"{[type(t).__name__ for t in terminals]}"
    # A terminal must be the last delta — no deltas after it.
    assert out[-1] is terminals[0], \
        f"{label}: deltas emitted after the terminal: " \
        f"{[type(d).__name__ for d in out]}"


@respx.mock
async def test_resume_onto_healthy_fallback_keeps_one_logical_stream():
    """Primary dies mid-stream, resume connects to a healthy fallback. The
    superseded pump must not contribute deltas: exactly one StreamStart and
    one terminal, and the resumed text continues the original."""
    out = await _h2_drain(hang_primary=True, hang_fallback=False)
    _assert_single_logical_stream(out, "resume-succeeded")
    assert not any(isinstance(d, dl.StreamError) for d in out), \
        "a successful resume must not surface the superseded pump's error"
    text = "".join(d.text for d in out if isinstance(d, dl.TextDelta))
    assert text == "partial resumed", text


@respx.mock
async def test_exhausted_resumes_still_emit_one_terminal():
    """Every deployment hangs, so resumes are exhausted. The stream must still
    end with exactly ONE terminal (StreamError) and one StreamStart — repeated
    pumps must not each contribute their own."""
    out = await _h2_drain(hang_primary=True, hang_fallback=True)
    _assert_single_logical_stream(out, "resumes-exhausted")
    assert isinstance(out[-1], dl.StreamError)

# ---------------------------------------------------------------------------
# Fix M5: ToolCallOpen must precede ToolCallArgsDelta when a provider omits id
# ---------------------------------------------------------------------------


def _tool_chunk(index: int, args=None, cid=None, name=None) -> str:
    fn: dict[str, Any] = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    tc: dict[str, Any] = {"index": index, "function": fn}
    if cid is not None:
        tc["id"] = cid
    return orjson.dumps({"choices": [{"delta": {"tool_calls": [tc]}}]}).decode()


def test_openai_adapter_emits_open_before_args_when_id_omitted():
    """Some OpenAI-compatible providers send ``arguments`` with no ``id`` on the
    first tool chunk (the id arrives later, or never). That must still produce
    a ``ToolCallOpen`` *before* the ``ToolCallArgsDelta`` — the contract
    requires strict Open -> ArgsDelta* -> Close nesting per index."""
    ad = OpenAIAdapter()
    ad.reset()
    deltas = ad.decode_stream_event("", _tool_chunk(index=0, args='{"a":1}'))
    kinds = [type(d).__name__ for d in deltas]
    assert "ToolCallArgsDelta" in kinds, kinds
    assert "ToolCallOpen" in kinds, \
        f"args emitted with no preceding ToolCallOpen (contract violation): {kinds}"
    assert kinds.index("ToolCallOpen") < kinds.index("ToolCallArgsDelta"), \
        f"ToolCallOpen must precede ToolCallArgsDelta, got {kinds}"
    # The synthesized open must carry the same index as the args delta.
    assert next(d for d in deltas if isinstance(d, dl.ToolCallOpen)).index == 0


def test_openai_adapter_no_second_open_when_id_arrives_after_args():
    """The id arriving on a later chunk must not emit a duplicate Open for an
    index that is already open — the synthesized one stands in for it."""
    ad = OpenAIAdapter()
    ad.reset()
    first = ad.decode_stream_event("", _tool_chunk(index=0, args='{"a":1}'))
    second = ad.decode_stream_event(
        "", _tool_chunk(index=0, args='"x"}', cid="call_1", name="f"))
    all_deltas = first + second
    opens = [d for d in all_deltas if isinstance(d, dl.ToolCallOpen)]
    assert len(opens) == 1, \
        f"expected exactly one ToolCallOpen for index 0, got {len(opens)}"
    # Args fragments must survive intact and in order.
    frags = "".join(d.args_fragment for d in all_deltas
                    if isinstance(d, dl.ToolCallArgsDelta))
    assert frags == '{"a":1}"x"}', frags


def test_openai_adapter_flushes_superseded_open_on_same_index():
    """A provider reusing the same index for a new tool call closes the previous
    one. If that previous call's Open was still deferred (id seen, no args
    yet), it must be flushed first — otherwise the stream carries a Close for
    an index that was never opened."""
    ad = OpenAIAdapter()
    ad.reset()
    # id seen, name complete, but no args yet -> Open is deferred.
    first = ad.decode_stream_event(
        "", _tool_chunk(index=0, cid="call_old", name="old"))
    # A new id on the same index supersedes it.
    second = ad.decode_stream_event(
        "", _tool_chunk(index=0, cid="call_new", name="new"))
    seq = first + second
    kinds = [type(d).__name__ for d in seq]
    assert kinds == ["ToolCallOpen", "ToolCallClose"], \
        f"superseded call emitted a Close with no Open: {kinds}"
    opened = seq[0]
    assert opened.id == "call_old", \
        f"the flushed Open must belong to the superseded call, got {opened.id!r}"


def test_openai_adapter_superseded_call_closes_at_finish():
    """End-to-end on the supersede path: old call completes Open -> Close, then
    the new one opens and closes at finish_reason. No orphaned Close, every
    Open matched by a Close."""
    ad = OpenAIAdapter()
    ad.reset()
    out = []
    out += ad.decode_stream_event(
        "", _tool_chunk(index=0, cid="call_old", name="old"))
    out += ad.decode_stream_event(
        "", _tool_chunk(index=0, cid="call_new", name="new"))
    out += ad.decode_stream_event("", _tool_chunk(index=0, args='{"a":1}'))
    out += ad.decode_stream_event("", _H1_FINISH)
    kinds = [type(d).__name__ for d in out]
    assert kinds == [
        "ToolCallOpen", "ToolCallClose",
        "ToolCallOpen", "ToolCallArgsDelta", "ToolCallClose", "Finish",
    ], kinds
    opens = [d for d in out if isinstance(d, dl.ToolCallOpen)]
    closes = [d for d in out if isinstance(d, dl.ToolCallClose)]
    assert [o.id for o in opens] == ["call_old", "call_new"]
    assert len(closes) == 2


def test_openai_adapter_normal_id_then_args_still_works():
    """The ordinary id-first ordering is unchanged: Open then args, one Open."""
    ad = OpenAIAdapter()
    ad.reset()
    d1 = ad.decode_stream_event("", _tool_chunk(index=0, cid="call_1", name="f"))
    d2 = ad.decode_stream_event("", _tool_chunk(index=0, args='{"a":1}'))
    all_deltas = d1 + d2
    kinds = [type(d).__name__ for d in all_deltas]
    assert kinds == ["ToolCallOpen", "ToolCallArgsDelta"], kinds
