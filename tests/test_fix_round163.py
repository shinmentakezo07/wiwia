"""Round 163: the replay cursor's unit — frames vs chunks.

Three findings from the SSE sweep of 2026-10-19:

* **#493** — the SSE ``id:`` line counts FRAMES (one id per frame, since
  #283) but the journal records one row per CHUNK, keyed by the chunk's LAST
  frame id. A reconnect whose cursor lands *inside* a multi-frame chunk
  therefore re-delivers the frames before the cursor: the client sees the
  finish chunk twice. #283's own trigger — "the client's connection dropping
  between sub-frames" — was fixed on the wire side only; the journal side was
  asserted to be consistent ("the journal records the same numbering", #283)
  and never checked.
* **#494** — ``stream_event_ids`` defaulted to ``false`` while
  ``stream_journal_enabled`` defaulted to ``true``, so the shipped
  configuration wrote a journal whose cursor the client could not express:
  no ``id:`` line was ever emitted, a reconnect could not send a meaningful
  ``Last-Event-ID``, and the server replayed the WHOLE response. The docs
  promise "replays missed frames".
* **#495** — ``docs/API_REFERENCE.md`` claimed the request id is "echoed on
  every SSE frame's ``id:`` field". It is a response header; the frame
  ``id:`` field carries a sequence number.

The encoders produce multi-frame chunks routinely: chat's ``final_frame()``
is the finish chunk plus the usage chunk, Anthropic's is
``content_block_stop`` plus ``message_delta``, Responses' ``_completed()`` is
several frames.
"""

from __future__ import annotations

import pathlib

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
from wiwi.streaming.tape_store import JournalStore

# An upstream turn whose final chunk carries usage — the shape that makes the
# chat encoder emit a TWO-frame final chunk (finish_reason + usage).
STREAM_BODY = (
    'data: {"choices":[{"delta":{"role":"assistant","content":"He"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"y"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
)


def _cfg(tmp_path, *, event_ids: bool | None = None) -> WiwiConfig:
    rs_kwargs = {}
    if event_ids is not None:
        rs_kwargs["stream_event_ids"] = event_ids
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(
            stream_journal_enabled=True,
            stream_journal_dir=str(tmp_path / "journals"),
            stream_journal_ttl_s=600.0,
            stream_journal_max_bytes=1 << 20,
            **rs_kwargs),
    )


def _body(*, include_usage: bool = True) -> dict:
    return {"model": "gpt-4o", "stream": True,
            "stream_options": {"include_usage": include_usage},
            "messages": [{"role": "user", "content": "hi"}]}


def _frames(raw: str) -> list[tuple[int | None, str]]:
    """Split a raw SSE body into (id, frame-text) pairs."""
    out: list[tuple[int | None, str]] = []
    for chunk in raw.split("\n\n"):
        if not chunk.strip():
            continue
        fid: int | None = None
        lines = chunk.splitlines()
        if lines and lines[0].startswith("id:"):
            fid = int(lines[0].split(":", 1)[1].strip())
        out.append((fid, chunk))
    return out


def _id_of(frame: str) -> int | None:
    lines = frame.splitlines()
    if lines and lines[0].startswith("id:"):
        return int(lines[0].split(":", 1)[1].strip())
    return None


# ---------------------------------------------------------------------------
# #493 — the journal must answer at frame granularity
# ---------------------------------------------------------------------------


async def test_journal_replay_slices_a_multi_frame_chunk(tmp_path):
    """A cursor inside a multi-frame chunk must not re-deliver earlier frames.

    The chunk below is what chat's ``final_frame()`` produces with
    ``include_usage``: the finish chunk and the usage chunk, journaled as ONE
    record. The client received frame 5 and dropped before frame 6, so the
    replay must contain frame 6 and nothing else.
    """
    store = JournalStore(tmp_path / "j", ttl_s=600, max_bytes=1 << 20)
    j = await store.open("s1")
    chunk = (b"id: 5\ndata: {\"finish\":\"stop\"}\n\n"
             b"id: 6\ndata: {\"usage\":{}}\n\n")
    await j.append(6, chunk, first_seq=5)
    await j.finish(6)

    replay = store.read_after("s1", 5)
    ids = [f.split(b"\n")[0] for _, f in replay]
    assert ids == [b"id: 6"], (
        "the replay re-delivered a frame the client already had "
        f"(got {ids})")


async def test_journal_replay_keeps_a_whole_chunk_when_the_cursor_is_below_it(
        tmp_path):
    """A cursor below the chunk's first frame still gets the whole chunk."""
    store = JournalStore(tmp_path / "j", ttl_s=600, max_bytes=1 << 20)
    j = await store.open("s2")
    chunk = (b"id: 5\ndata: {\"a\":1}\n\n"
             b"id: 6\ndata: {\"b\":2}\n\n")
    await j.append(6, chunk, first_seq=5)
    await j.finish(6)

    replay = store.read_after("s2", 4)
    ids = [f.split(b"\n")[0] for _, f in replay]
    assert ids == [b"id: 5", b"id: 6"], (
        f"a whole chunk must survive a cursor below it (got {ids})")


@respx.mock
async def test_reconnect_does_not_re_deliver_a_frame_the_client_already_had(
        tmp_path):
    """E2E #493: the client's own frame must not come back on reconnect.

    The upstream's last chunk carries usage, so the chat encoder's
    ``final_frame()`` emits the finish chunk and the usage chunk TOGETHER.
    The client receives the finish frame and its connection drops before the
    usage frame; on reconnect it must get the usage frame and NOT the finish
    frame again.
    """
    app = create_app(_cfg(tmp_path, event_ids=True))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(text=STREAM_BODY)
            headers = {"Authorization": "Bearer sk-wiwi-master-test"}
            s = await c.post("/v1/chat/completions", json=_body(),
                             headers=headers)
            assert s.status_code == 200
            rid = s.headers["x-wiwi-request-id"]

            live = _frames(s.text)
            # Every chat chunk carries a "finish_reason" KEY (null until the
            # end), so the terminating chunk is the one that says "stop".
            finish = [f for _, f in live if '"finish_reason":"stop"' in f]
            assert finish, f"no finish frame in the live stream: {live!r}"
            cursor = _id_of(finish[0])
            assert cursor is not None, "the live stream carried no id: line"

            r = await c.post("/v1/chat/completions", json=_body(),
                             headers={**headers, "x-wiwi-stream-id": rid,
                                      "Last-Event-ID": str(cursor)})
            assert r.status_code == 200
            assert r.headers.get("x-wiwi-stream-replay") == rid
            assert route.call_count == 1, "the replay re-dispatched upstream"

            replayed = _frames(r.text)
            replay_ids = [i for i, _ in replayed if i is not None]
            assert replayed, "the replay served nothing"
            assert min(replay_ids) > cursor, (
                f"the replay re-delivered a frame at or before the cursor "
                f"{cursor} (ids {replay_ids})")
            assert not [f for _, f in replayed if '"finish_reason":"stop"' in f], (
                "the finish frame the client already had was replayed")
            assert [f for _, f in replayed if "usage" in f], (
                "the usage frame the client had NOT seen was lost")


# ---------------------------------------------------------------------------
# #494 — the cursor must be reachable in the shipped configuration
# ---------------------------------------------------------------------------


def test_event_ids_are_on_by_default():
    """The journal is on by default, so the cursor must be on by default too.

    With ``stream_event_ids`` off the stream carries no ``id:`` line at all,
    a client cannot name the frame it stopped at, and a reconnect replays the
    whole response — the opposite of the documented "replays missed frames".
    """
    assert RouterSettings().stream_event_ids is True


@respx.mock
async def test_default_config_emits_a_cursor_on_every_frame(tmp_path):
    """E2E #494: the default configuration must emit SSE event ids."""
    app = create_app(_cfg(tmp_path))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                text=STREAM_BODY)
            s = await c.post(
                "/v1/chat/completions", json=_body(),
                headers={"Authorization": "Bearer sk-wiwi-master-test"})
            assert s.status_code == 200
            ids = [i for i, _ in _frames(s.text) if i is not None]
            assert ids, (
                "the default configuration emitted no id: line, so a "
                "reconnecting client cannot name the frame it stopped at")
            assert ids == sorted(ids) and len(set(ids)) == len(ids), (
                f"the event ids are not strictly monotonic (got {ids})")


@respx.mock
async def test_reconnect_with_a_cursor_replays_only_the_missed_frames(tmp_path):
    """E2E #494: a cursor names the frame; only later frames come back."""
    app = create_app(_cfg(tmp_path))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(text=STREAM_BODY)
            headers = {"Authorization": "Bearer sk-wiwi-master-test"}
            s = await c.post("/v1/chat/completions", json=_body(),
                             headers=headers)
            rid = s.headers["x-wiwi-request-id"]
            live = _frames(s.text)
            ids = [i for i, _ in live if i is not None]
            assert ids, "the live stream carried no id: line"

            # The client saw the first two frames, then dropped.
            cursor = ids[1]
            r = await c.post("/v1/chat/completions", json=_body(),
                             headers={**headers, "x-wiwi-stream-id": rid,
                                      "Last-Event-ID": str(cursor)})
            assert route.call_count == 1, "the replay re-dispatched upstream"
            replay_ids = [i for i, _ in _frames(r.text) if i is not None]
            assert replay_ids, "the replay served nothing"
            assert min(replay_ids) > cursor, (
                f"the replay re-delivered frames at or before the cursor "
                f"{cursor} (ids {replay_ids})")


# ---------------------------------------------------------------------------
# #495 — the API reference must not describe a header as a frame field
# ---------------------------------------------------------------------------


def test_api_reference_does_not_call_the_request_id_a_frame_id():
    """The request id is a response header, not the SSE frame ``id:`` field."""
    doc = pathlib.Path("docs/API_REFERENCE.md").read_text()
    rows = [ln for ln in doc.splitlines()
            if ln.startswith("| `x-wiwi-request-id`")]
    assert rows, "the API reference lost its x-wiwi-request-id row"
    for row in rows:
        assert "frame" not in row.lower(), (
            "the API reference still claims the request id is echoed on a "
            f"frame's id: field — the row is: {row}")
