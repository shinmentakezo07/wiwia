"""Round 165: the SSE layer's ingest and replay.

Three findings from the SSE sweep of 2026-10-20:

* **#502** — the ingest parser's ``allow_unframed`` mode is a catch-all: ANY
  line that is not a comment, not ``event:`` and not ``data:`` is emitted as
  a data payload. That includes ``id:`` and ``retry:``, which are legal SSE
  *field* lines. Each field line therefore produced an EXTRA event whose
  payload was the literal text "id: 42" — a payload no adapter can parse, so
  it was decoded and discarded. The frame's own payload was never affected;
  the cost was a wasted decode per field line.
* **#503** — the reconnect-replay ``StreamingResponse`` omits
  ``x-accel-buffering: no``. The live path and the admin path both set it;
  the replay path builds a separate header dict that does not. A buffering
  proxy therefore holds the replayed stream until it closes, which is
  exactly the symptom the header exists to prevent — and on a replay it is
  worse, because a replay *tails* a still-running turn.
* **#504** — the replay tail loop emits nothing while it waits: no frame, no
  comment, no ping. The live path has a keep-alive for exactly this window
  (the upstream going quiet for ``stream_ping_interval_s``); the reconnecting
  client, which is by definition waiting on someone else's turn, is the one
  connection with no protection from an idle proxy.

Each test fails against the pre-fix tree.
"""

from __future__ import annotations

import json

import httpx
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
from wiwi.server.app import create_app
from wiwi.streaming.sse import LineSSEParser

AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}

# A frame whose `id:` field line sits BETWEEN its two data lines. Legal SSE.
#
# NOTE: the two data lines below are TWO SEPARATE FRAMES, not one frame with
# two payload lines. The blank line between them is what separates them, so
# each frame carries exactly one JSON document — the only shape an OpenAI-
# shaped upstream produces, and the only one the adapter can parse.
FRAME_WITH_ID_INSIDE = (
    'data: {"choices":[{"delta":{"content":"Hello"}}]}\n'
    "id: 42\n"
    "\n"
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
)

# The same stream with the `id:` line BEFORE the payload.
FRAME_WITH_ID_BEFORE = (
    "id: 42\n"
    'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
)

PLAIN_BODY = (
    'data: {"choices":[{"delta":{"role":"assistant","content":"He"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"y"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
)


def _cfg(tmp_path, **router) -> WiwiConfig:
    rs_kwargs = {
        "stream_journal_enabled": True,
        "stream_journal_dir": str(tmp_path / "journals"),
        "stream_journal_ttl_s": 600.0,
        "stream_journal_max_bytes": 1 << 20,
    }
    rs_kwargs.update(router)
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(**rs_kwargs),
    )


def _body() -> dict:
    return {"model": "gpt-4o", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}


def _deltas_of(text: str) -> list[dict]:
    """The JSON payloads of the client's SSE frames."""
    out = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            continue
        try:
            out.append(json.loads(payload))
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------------------
# #502 — an `id:` field line must not be read as a data payload
# ---------------------------------------------------------------------------


def test_parser_keeps_a_frame_whole_when_an_id_line_sits_inside_it():
    """An `id:` field line must not disturb the frames around it.

    The `id:` line is a FIELD of the frame, not a payload. Pre-fix the
    unframed catch-all claimed it and emitted it as its own event, so the
    parser produced one extra (junk) event per field line.

    The two ``data:`` lines here are ONE frame (no blank line between them),
    so the SSE spec joins them into a single multi-line payload — that is
    correct SSE and is what the pre-fix parser produced too. What must never
    happen is the ``id:`` field's own text appearing as a payload.
    """
    parser = LineSSEParser(allow_unframed=True)
    events = []
    for line in ('data: {"a":1}', "id: 42", 'data: {"b":2}', ""):
        evt = parser.feed_line(line)
        if evt is not None:
            events.append(evt)

    assert len(events) == 1, (
        "the `id:` field line produced an extra event: "
        f"{[e.data for e in events]!r}")
    assert events[0].data == '{"a":1}\n{"b":2}', (
        "the `id:` field line leaked into the frame's payload: "
        f"{events[0].data!r}")


def test_parser_does_not_emit_an_id_line_as_its_own_data_event():
    """An `id:` line is a field, never a payload.

    Pre-fix, the unframed catch-all emitted it as its own event, whose data
    was the literal text "id: 42" — a payload no adapter can parse.
    """
    parser = LineSSEParser(allow_unframed=True)
    events = []
    for line in ("id: 42", "retry: 3000"):
        evt = parser.feed_line(line)
        if evt is not None:
            events.append(evt)
    assert not events, (
        "an SSE field line was emitted as a data payload: "
        f"{[e.data for e in events]!r}")


def test_parser_still_accepts_a_bare_json_object_per_line():
    """The envelope providers (Cline, WorkBuddy) must keep working.

    `allow_unframed` exists for them: one whole JSON object per line, with
    no `data:` prefix. Narrowing the unframed branch must not break them.
    """
    parser = LineSSEParser(allow_unframed=True)
    events = []
    for line in ('{"d":"A"}', '{"d":"B"}'):
        evt = parser.feed_line(line)
        if evt is not None:
            events.append(evt)
    assert [json.loads(e.data) for e in events] == [{"d": "A"}, {"d": "B"}], (
        f"the unframed envelope shape regressed: {[e.data for e in events]!r}")


@respx.mock
async def test_an_upstream_id_line_does_not_cost_the_client_its_answer(
        tmp_path):
    """E2E #502: the content must reach the client.

    The upstream frame carries an `id:` field line between its two data
    lines. Pre-fix the frame's payloads were joined into invalid JSON, the
    adapter dropped the frame, and the client received a 200 with a
    finish_reason, a [DONE] and no answer at all.
    """
    app = create_app(_cfg(tmp_path))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                text=FRAME_WITH_ID_INSIDE)
            r = await c.post("/v1/chat/completions", json=_body(),
                             headers=AUTH)
    assert r.status_code == 200, r.text
    text = "".join(
        d.get("choices", [{}])[0].get("delta", {}).get("content", "")
        for d in _deltas_of(r.text))
    assert "Hello" in text, (
        "the frame's content was lost to the `id:` line — the client got a "
        f"complete-looking response with no answer: {r.text!r}")


@respx.mock
async def test_an_upstream_id_line_before_the_payload_still_works(tmp_path):
    """The benign ordering must keep working (it is legal SSE too)."""
    app = create_app(_cfg(tmp_path))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                text=FRAME_WITH_ID_BEFORE)
            r = await c.post("/v1/chat/completions", json=_body(),
                             headers=AUTH)
    assert r.status_code == 200, r.text
    text = "".join(
        d.get("choices", [{}])[0].get("delta", {}).get("content", "")
        for d in _deltas_of(r.text))
    assert "Hello" in text, r.text


# ---------------------------------------------------------------------------
# #503 — the replay response must carry the anti-buffering header
# ---------------------------------------------------------------------------


@respx.mock
async def test_the_replay_response_carries_the_anti_buffering_header(
        tmp_path):
    """A reconnect behind a buffering proxy must not be held back.

    The live path sets ``x-accel-buffering: no``; the replay path must set
    it too, or a proxy buffers the replayed stream into one burst — and on a
    replay that means the client waits for the whole turn before it sees
    anything.
    """
    app = create_app(_cfg(tmp_path))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                text=PLAIN_BODY)
            live = await c.post("/v1/chat/completions", json=_body(),
                                headers=AUTH)
            assert live.status_code == 200, live.text
            assert live.headers.get("x-accel-buffering") == "no", (
                "the live stream does not set the anti-buffering header")

            rid = live.headers["x-wiwi-request-id"]
            replay = await c.post(
                "/v1/chat/completions", json=_body(),
                headers={**AUTH, "x-wiwi-stream-id": rid, "Last-Event-ID": "0"})
    assert replay.status_code == 200, replay.text
    assert replay.headers.get("x-wiwi-stream-replay") == rid, (
        "the request was not served as a replay")
    assert replay.headers.get("x-accel-buffering") == "no", (
        "the replay response omits x-accel-buffering: no, so a buffering "
        "proxy holds the whole replayed stream until it closes "
        f"(headers: {dict(replay.headers)})")


# ---------------------------------------------------------------------------
# #504 — the replay tail must keep the connection alive
# ---------------------------------------------------------------------------


@respx.mock
async def test_a_tailing_replay_emits_a_keepalive(tmp_path):
    """A reconnect that is waiting on a still-running turn must not sit silent.

    The tail loop polls the journal while the original turn is still
    running. While it waits it must emit something, or an idle proxy reaps
    the connection and the client reconnects into the same silence.

    The journal TTL is set to 2 s so the tail ENDS on its own: pre-fix the
    loop yields nothing for the whole TTL and then closes, which is the
    silence this test is about. The keep-alive must arrive well inside it.
    """
    app = create_app(_cfg(tmp_path, stream_ping_interval_s=0.2,
                          stream_journal_ttl_s=2.0))
    state = app.state.wiwi
    async with LifespanManager(app):
        # A journal that is open (no done record) is what the replay gate
        # treats as "the original stream is still running in this process".
        journal = await state.journals.open("tail-stream", key_id="master")
        await journal.append(1, b'id: 1\ndata: {"x":1}\n\n', first_seq=1)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            request = c.build_request(
                "POST", "/v1/chat/completions", json=_body(),
                headers={**AUTH, "x-wiwi-stream-id": "tail-stream",
                         "Last-Event-ID": "0"})
            response = await c.send(request, stream=True)
            assert response.status_code == 200
            chunks: list[str] = []
            try:
                async for chunk in response.aiter_text():
                    chunks.append(chunk)
                    if "keep-alive" in "".join(chunks):
                        break
            except (httpx.ReadTimeout, TimeoutError):
                pass
            await response.aclose()
    body = "".join(chunks)
    assert "keep-alive" in body, (
        "the tailing replay emitted nothing while it waited — the client is "
        f"left in silence for the whole journal TTL: {body!r}")
