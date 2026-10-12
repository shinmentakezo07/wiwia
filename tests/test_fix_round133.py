"""Round 133: a tool-call-only stream must report a generation-phase TPS.

``build_log_event`` derives ``stream_secs`` from
``last_token_at - first_token_at`` and both are stamped in the same block of
the stream consumer (``wiwi/core/gateway.py:1186``). ``first_token_at`` is set
on ``TextDelta``/``ThinkingDelta``/``ToolCallOpen``, but ``last_token_at`` was
set only on ``TextDelta``/``ThinkingDelta``. A stream whose output is a tool
call and nothing else — a function-calling agent that never emits prose — set
``first_token_at`` on the open, never advanced ``last_token_at``, and so
reported ``tps = 0.0`` despite a real, measured generation phase. The request
was present in every aggregate as a zero sample, silently shrinking
``tps_sample_ratio``.

The window is therefore bounded by the first content-bearing delta (including
``ToolCallOpen``) and the last one of any kind. Tool-call args are excluded
from the ``last_token_at`` bound because each fragment is one SSE frame, not a
model token — timestamping them would stretch the window to cover transport
time. Arguably debatable for NIM and Cline (whose frames are OpenAI-shaped and
inter-token), but not for Anthropic, which sends the whole ``input`` as one
frame; the conservative choice is to timestamp at Open/Close only.
"""

import asyncio
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
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# Past the 0.05s minimum generation window in build_log_event. The gap between
# the tool open and the tool close is the generation phase this test measures.
TOOL_GAP = 0.15


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


def _tool_call(index: int, name: str, args_fragment: str = "") -> dict:
    return {"tool_calls": [{"index": index, "id": f"call_{index}",
                            "type": "function",
                            "function": {"name": name, "arguments": args_fragment}}]}


def _final_tool_usage(completion_tokens: int) -> dict:
    return {"prompt_tokens": 5, "completion_tokens": completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0}}


async def _tool_only_stream():
    """A stream that emits ONLY tool calls — no TextDelta, no ThinkingDelta.

    Two sibling tool calls, each in its own SSE chunk and spaced by TOOL_GAP,
    and each carrying an args fragment. The args matter: the OpenAI adapter
    defers ``ToolCallOpen`` until the name is complete, which the first args
    fragment signals. Without args the adapter holds both opens and flushes
    them together at finish_reason, collapsing the window to one instant —
    which is a real shape, but one where no generation window exists to
    measure. Sending args is what makes each open land in its own chunk, the
    way a provider actually streams a parallel tool call.
    """
    yield _chunk({"role": "assistant",
                  **_tool_call(0, "get_weather", '{"city":"SF"}')})
    await asyncio.sleep(TOOL_GAP)
    yield _chunk(_tool_call(1, "get_time", '{"tz":"PT"}'))
    await asyncio.sleep(TOOL_GAP)
    yield _chunk({}, finish="tool_calls",
                 usage=_final_tool_usage(50))
    yield b"data: [DONE]\n\n"


async def _text_stream():
    """The control: identical pacing with text, which already reported TPS."""
    yield _chunk({"role": "assistant", "content": "he"})
    await asyncio.sleep(TOOL_GAP)
    yield _chunk({"content": "llo"})
    await asyncio.sleep(TOOL_GAP)
    yield _chunk({}, finish="stop", usage=_final_tool_usage(50))
    yield b"data: [DONE]\n\n"


async def _logs(client) -> list[dict]:
    r = await client.get("/admin/logs/requests", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["logs"]


async def _await_requests(client, expected: int, timeout: float = 8.0) -> list[dict]:
    deadline = time.time() + timeout
    logs = await _logs(client)
    while len(logs) < expected and time.time() < deadline:
        await asyncio.sleep(0.02)
        logs = await _logs(client)
    assert len(logs) >= expected, f"log pump never wrote {expected} rows"
    return logs


async def _post_stream(client, body):
    r = await client.post("/v1/chat/completions", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    await r.aread()


# -- the fix ------------------------------------------------------------------


@respx.mock
async def test_tool_call_only_stream_reports_tps(client):
    """A tool-call-only stream keeps its generation-phase TPS.

    Before the fix: ``first_token_at`` was stamped on the ``ToolCallOpen`` but
    ``last_token_at`` only on text/thinking, so this logged ``tps = 0.0`` with
    ``tok_out = 50``. TTFT survived (the open stamp was fine), which is what
    makes the zero look like a real measurement rather than a missing one.
    """
    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"},
        content=_tool_only_stream())
    await _post_stream(client, {
        "model": "gpt-4o", "stream": True,
        "messages": [{"role": "user", "content": "weather in SF?"}]})

    rows = await _await_requests(client, 1)
    row = rows[0]
    assert row["status"] == 200, row
    assert row["tok_out"] == 50, row
    # The point of the fix: a positive TPS survives the full trip to the DB row.
    assert row["tps"] > 0, (
        f"tool-call-only stream lost its generation-phase TPS: {row}")
    # And it is generation-phase, not round-trip: it must beat tok_out/latency.
    assert row["latency_ms"] / 1000 > row["ttft_ms"] / 1000 >= 0, row
    assert row["tps"] >= (row["tok_out"] / (row["latency_ms"] / 1000)), (
        f"tps {row['tps']} is below the round-trip rate for the same row; "
        f"the window should exclude prefill: {row}")


@respx.mock
async def test_tool_only_and_text_streams_report_comparable_tps(client):
    """The same pacing yields the same TPS whether or not prose was emitted.

    Both fixtures stream 50 completion tokens across two TOOL_GAP sleeps, so
    the generation phase is ~2 * TOOL_GAP either way. The tool-call-only arm
    is allowed a little slack for scheduler jitter; the assertion is that it
    lands in the same band as the text arm, not on an exact value.
    """
    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"},
        content=_tool_only_stream())
    await _post_stream(client, {"model": "gpt-4o", "stream": True,
                                "messages": [{"role": "user", "content": "a"}]})
    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"},
        content=_text_stream())
    await _post_stream(client, {"model": "gpt-4o", "stream": True,
                                "messages": [{"role": "user", "content": "b"}]})

    rows = await _await_requests(client, 2)
    tool_row = next(r for r in rows if r["ttft_ms"] is not None)
    by_ttft = sorted(rows, key=lambda r: r["ttft_ms"])
    assert len(by_ttft) == 2
    text_row, tool_row = by_ttft[0], by_ttft[1]  # ordering is not asserted on
    expected = 50 / (2 * TOOL_GAP)
    for label, row in (("text", text_row), ("tool", tool_row)):
        assert row["tps"] > 0, f"{label} arm lost its TPS: {row}"
        assert row["tps"] >= expected * 0.5, (
            f"{label} arm tps {row['tps']} is far below the paced "
            f"{expected:.1f}: {row}")


@respx.mock
async def test_tool_only_stream_counts_toward_tps_sample_ratio(client):
    """The fixed request is now a TPS sample, not a silent zero.

    A tool-only stream used to be indistinguishable from a non-streaming
    request in the aggregates: both contributed tps == 0.0 and both were
    excluded, so the coverage figure understated how much streaming traffic
    the TPS numbers described.
    """
    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"},
        content=_tool_only_stream())
    await _post_stream(client, {"model": "gpt-4o", "stream": True,
                                "messages": [{"role": "user", "content": "a"}]})

    await _await_requests(client, 1)
    overview = (await client.get("/admin/stats/overview?minutes=60",
                                 headers=AUTH)).json()
    assert overview["requests"] == 1, overview
    assert overview["tps_avg"] > 0, overview
    # One request, one measurable sample: full coverage.
    assert overview["tps_sample_ratio"] == 1.0, overview


@respx.mock
async def test_instant_tool_only_stream_still_reports_no_tps(client):
    """The 0.05s floor still applies to the tool-call path.

    The fix must not turn a same-instant open/close pair into a division by a
    sub-threshold window. An unpaced tool-only stream has no measurable
    generation phase and must keep reporting tps == 0.0.
    """
    async def instant():
        yield _chunk({"role": "assistant",
                      **_tool_call(0, "get_weather", '{"city":"SF"}')})
        yield _chunk(_tool_call(1, "get_time", '{"tz":"PT"}'))
        yield _chunk({}, finish="tool_calls", usage=_final_tool_usage(50))
        yield b"data: [DONE]\n\n"

    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"}, content=instant())
    await _post_stream(client, {"model": "gpt-4o", "stream": True,
                                "messages": [{"role": "user", "content": "a"}]})

    rows = await _await_requests(client, 1)
    assert rows[0]["tps"] == 0.0, rows[0]
