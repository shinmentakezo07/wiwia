"""Round 156: an echoed tool-call id closed and reopened the call on every chunk.

Round 155 fixed the TPS window for a tool call whose arguments carry the bulk
of the output, by stamping ``last_token_at`` on ``ToolCallArgsDelta``. That fix
was only half-observable, because the OpenAI-shaped adapters were emitting one
``ToolCallOpen``/``ToolCallClose`` pair per *fragment* instead of per *call*.

``decode_stream_event`` treated any chunk carrying an ``id`` on an already-open
index as a new tool call: close the old one, open a new one, then stream the
arguments. OpenAI itself sends ``id``+``name`` only on a call's first chunk, so
the branch is only reached by the OpenAI-compatible upstreams that echo both on
*every* arguments chunk — and by this repository's own test fixtures, which had
all been written in that shape.

Two defects fell out of it:

* **The wire.** One call became N ``ToolCallOpen``/``Close`` triples, all
  carrying the same id. On the Anthropic surface the encoder renders each Open
  as its own ``content_block_start``, so a single tool call arrived as N
  ``tool_use`` blocks with duplicate ids — which Anthropic rejects outright, and
  which a lenient client would execute N times.
* **The meter.** ``last_token_at`` was stamped by the reopen churn, so the TPS
  window happened to end in the right place for these providers — for the wrong
  reason. Turn the round-155 stamp off and the numbers still looked right,
  which is exactly why the churn survived a round of metric work.

Fix: ``OpenAIAdapter._tool_call_continues()`` — a chunk whose ``id`` matches the
call already open on that index continues it. Only a *different* id (or an index
that is not open) starts a new call, so the existing close/reopen path for
genuine index reuse is untouched. The OpenRouter and NIM adapters share the
helper, since both subclass ``OpenAIAdapter``.
"""

import asyncio
import json
import time

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
from wiwi.providers.nim_adapter import NimAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.openrouter_adapter import OpenRouterAdapter
from wiwi.server.app import create_app
from wiwi.wire.anthropic_messages import AnthropicStreamEncoder

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# Past the 0.05s minimum generation window in build_log_event.
GAP = 0.30
ARG_GAP = 0.02
ARG_FRAMES = 20
TOKENS = 8000

ADAPTERS = [OpenAIAdapter, OpenRouterAdapter, NimAdapter]
IDS = ["openai", "openrouter", "nim"]


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(prometheus_enabled=True,
                                       prometheus_path="/metrics"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


def _chunk(delta, finish=None, usage=None) -> bytes:
    body = {"id": "c", "object": "chat.completion.chunk", "model": "gpt-4o",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return b"data: " + orjson.dumps(body) + b"\n\n"


def _tool(index: int, call_id: str | None, name: str = "write_file",
          args: str = "") -> dict:
    entry: dict = {"index": index, "type": "function",
                   "function": {"name": name, "arguments": args}}
    if call_id is not None:
        entry["id"] = call_id
    return {"tool_calls": [entry]}


def _decode(adapter, chunks: list[bytes]):
    deltas = []
    for raw in chunks:
        line = raw.decode().strip()
        if not line.startswith("data: ") or line[6:] == "[DONE]":
            continue
        deltas.extend(adapter.decode_stream_event("message", line[6:]))
    return deltas


def _kinds(deltas) -> list[str]:
    return [type(d).__name__ for d in deltas]


def _echoed_id_chunks(frames: int = 4, index: int = 0,
                      call_id: str = "call_1") -> list[bytes]:
    """A tool call whose every arguments chunk re-states ``id`` and ``name``."""
    out = [_chunk({"role": "assistant", "content": "ok"})]
    for _ in range(frames):
        out.append(_chunk(_tool(index, call_id, args='{"path":"' + "x" * 8 + '"}')))
    out.append(_chunk({}, finish="tool_calls"))
    return out


# -- one call, one Open/Close pair, in every OpenAI-shaped adapter ---------------


@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=IDS)
def test_echoed_tool_id_does_not_reopen_the_call(adapter_cls):
    """N arguments chunks echoing one id are ONE tool call, not N."""
    deltas = _decode(adapter_cls(), _echoed_id_chunks())
    kinds = _kinds(deltas)
    assert kinds.count("ToolCallOpen") == 1, kinds
    assert kinds.count("ToolCallClose") == 1, kinds
    assert kinds.count("ToolCallArgsDelta") == 4, kinds
    args = "".join(d.args_fragment for d in deltas
                   if type(d).__name__ == "ToolCallArgsDelta")
    assert args == '{"path":"xxxxxxxx"}' * 4, args


@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=IDS)
def test_open_name_is_complete_and_carries_the_id(adapter_cls):
    """The surviving Open names the call once, with the call's real id."""
    opens = [d for d in _decode(adapter_cls(), _echoed_id_chunks())
             if type(d).__name__ == "ToolCallOpen"]
    assert len(opens) == 1
    assert opens[0].id == "call_1", opens[0]
    assert opens[0].name == "write_file", opens[0]
    assert opens[0].index == 0, opens[0]


@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=IDS)
def test_a_different_id_on_the_same_index_still_starts_a_new_call(adapter_cls):
    """Index reuse by a genuinely different call must still close and reopen.

    The round-156 guard: ``_tool_call_continues`` keys on the id, so a provider
    that streams two calls down one index keeps both.
    """
    chunks = [_chunk(_tool(0, "call_A", args='{"p":"aaa"}')),
              _chunk(_tool(0, "call_B", args='{"p":"bbb"}')),
              _chunk({}, finish="tool_calls")]
    deltas = _decode(adapter_cls(), chunks)
    kinds = _kinds(deltas)
    assert kinds.count("ToolCallOpen") == 2, kinds
    assert kinds.count("ToolCallClose") == 2, kinds
    ids = [d.id for d in deltas if type(d).__name__ == "ToolCallOpen"]
    assert ids == ["call_A", "call_B"], ids


@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=IDS)
def test_parallel_calls_on_separate_indices_are_untouched(adapter_cls):
    """Two indices in flight at once keep their own Open/Close."""
    chunks = [_chunk(_tool(0, "call_A", args='{"p":"aaa"}')),
              _chunk(_tool(1, "call_B", args='{"p":"bbb"}')),
              _chunk({}, finish="tool_calls")]
    deltas = _decode(adapter_cls(), chunks)
    kinds = _kinds(deltas)
    assert kinds.count("ToolCallOpen") == 2, kinds
    assert kinds.count("ToolCallClose") == 2, kinds


@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=IDS)
def test_id_only_on_the_first_chunk_still_opens_exactly_once(adapter_cls):
    """OpenAI's own shape — id on the first chunk only — is unchanged."""
    chunks = [_chunk(_tool(0, "call_1", args='{"p":"aaa"}'))]
    chunks += [_chunk(_tool(0, None, name="", args="bbb}"))]
    chunks += [_chunk({}, finish="tool_calls")]
    deltas = _decode(adapter_cls(), chunks)
    kinds = _kinds(deltas)
    assert kinds.count("ToolCallOpen") == 1, kinds
    assert kinds.count("ToolCallClose") == 1, kinds


@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=IDS)
def test_reset_clears_the_open_call_ids(adapter_cls):
    """A reused adapter must not read the previous request's ids.

    ``get_adapter`` hands out a shared singleton that is ``reset()`` on every
    hand-out, so a leftover id would make the first chunk of the NEXT request
    look like a continuation of a call that never existed on the wire.
    """
    adapter = adapter_cls()
    _decode(adapter, _echoed_id_chunks())
    adapter.reset()
    kinds = _kinds(_decode(adapter, _echoed_id_chunks()))
    assert kinds.count("ToolCallOpen") == 1, kinds
    assert kinds.count("ToolCallClose") == 1, kinds


# -- the Anthropic surface sees one tool_use block, with a unique id -----------


def _anthropic_events(chunks: list[bytes]) -> list[dict]:
    encoder = AnthropicStreamEncoder("gpt-4o", "req-test")
    events: list[dict] = []

    def absorb(raw: bytes | None) -> None:
        if not raw:
            return
        for line in bytes(raw).decode().strip().splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    for delta in _decode(OpenAIAdapter(), chunks):
        absorb(encoder.feed(delta))
    absorb(encoder.final_frame())
    return events


def test_anthropic_surface_renders_one_tool_use_block_for_an_echoed_call():
    """The wire-level consequence: duplicate ``tool_use`` ids are rejected."""
    events = _anthropic_events(_echoed_id_chunks())
    starts = [e for e in events
              if e.get("type") == "content_block_start"
              and e["content_block"].get("type") == "tool_use"]
    assert len(starts) == 1, [e["content_block"] for e in starts]
    ids = [e["content_block"]["id"] for e in starts]
    assert len(set(ids)) == len(ids), ids
    # Every arguments fragment landed in the one block that exists.
    deltas = [e for e in events if e.get("type") == "content_block_delta"
              and e["delta"].get("type") == "input_json_delta"]
    assert len(deltas) == 4, deltas
    assert {e["index"] for e in deltas} == {starts[0]["index"]}, deltas


def test_anthropic_surface_still_renders_two_blocks_for_index_reuse():
    """Two calls down one index remain two dispatchable ``tool_use`` blocks."""
    chunks = [_chunk(_tool(0, "call_A", args='{"p":"aaa"}')),
              _chunk(_tool(0, "call_B", args='{"p":"bbb"}')),
              _chunk({}, finish="tool_calls")]
    starts = [e for e in _anthropic_events(chunks)
              if e.get("type") == "content_block_start"
              and e["content_block"].get("type") == "tool_use"]
    assert [e["content_block"]["id"] for e in starts] == ["call_A", "call_B"]


# -- the meter stops riding the churn ------------------------------------------


async def _echoed_stream(completion_tokens: int = TOKENS, frames: int = ARG_FRAMES):
    """One tool call streamed as sliced fragments, ``id`` echoed on each chunk.

    The fragments concatenate to a single valid JSON document — the shape a
    real arguments stream has — and every chunk re-states ``id``+``name``,
    which is the OpenAI-compatible echo this round is about.
    """
    body = '{"path":"' + "x" * 2000 + "y" * (200 * frames) + '"}'
    head, rest = body[:2011], body[2011:]
    yield _chunk({"role": "assistant", "content": "ok"})
    await asyncio.sleep(GAP)
    yield _chunk(_tool(0, "call_0", args=head))
    for i in range(frames):
        await asyncio.sleep(ARG_GAP)
        yield _chunk(_tool(0, "call_0", args=rest[i * 200:(i + 1) * 200]))
    yield _chunk({}, finish="tool_calls", usage={
        "prompt_tokens": 5, "completion_tokens": completion_tokens,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0}})
    yield b"data: [DONE]\n\n"


async def _one_row(client, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = await client.get("/admin/logs/requests", headers=AUTH)
        assert r.status_code == 200, r.text
        rows = r.json()["logs"]
        if rows:
            return rows[0]
        await asyncio.sleep(0.02)
    raise AssertionError("log pump wrote no row")


@respx.mock
async def test_tps_window_for_an_echoed_id_stream_spans_the_arguments(client):
    """With the churn gone, the arguments themselves must close the window.

    ``tok_out / tps`` reconstructs the window the gateway divided by. The
    arguments stream over ``ARG_FRAMES * ARG_GAP`` of real generation time, and
    the round-155 stamp on ``ToolCallArgsDelta`` is the only thing that can
    report it now — the reopen churn that used to cover for it emits one Open
    per call.
    """
    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"},
        content=_echoed_stream())
    r = await client.post("/v1/messages",
                          json={"model": "gpt-4o", "max_tokens": 4096,
                                "stream": True,
                                "messages": [{"role": "user", "content": "go"}]},
                          headers={**AUTH, "anthropic-version": "2023-06-01"})
    assert r.status_code == 200, r.text
    await r.aread()
    row = await _one_row(client)
    assert row["tps"] > 0, row
    window = row["tok_out"] / row["tps"]
    paced = ARG_FRAMES * ARG_GAP
    assert window >= paced * 0.8, (
        f"the window behind tps {row['tps']} is {window:.3f}s, shorter than the "
        f"{paced:.2f}s the arguments took to stream — the churn is still "
        f"carrying the meter")


@respx.mock
async def test_echoed_id_stream_yields_exactly_one_tool_use(client):
    """End to end through /v1/messages: one block, one id, all the arguments."""
    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"},
        content=_echoed_stream(frames=3))
    r = await client.post("/v1/messages",
                          json={"model": "gpt-4o", "max_tokens": 4096,
                                "stream": True,
                                "messages": [{"role": "user", "content": "go"}]},
                          headers={**AUTH, "anthropic-version": "2023-06-01"})
    assert r.status_code == 200, r.text
    body = await r.aread()
    starts, args = [], []
    for line in body.decode().splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            continue
        event = json.loads(payload)
        if event.get("type") == "content_block_start" \
                and event["content_block"].get("type") == "tool_use":
            starts.append(event["content_block"])
        elif event.get("type") == "content_block_delta" \
                and event["delta"].get("type") == "input_json_delta":
            args.append(event["delta"]["partial_json"])
    assert len(starts) == 1, starts
    assert {s["id"] for s in starts} == {starts[0]["id"]}, starts
    assert len(args) == 1 + 3, args
    parsed = json.loads("".join(args))
    assert parsed["path"] == "x" * 2000 + "y" * (200 * 3), len(parsed["path"])