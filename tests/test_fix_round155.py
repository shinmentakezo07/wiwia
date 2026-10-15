"""Round 155: TPS measured a window that excluded the tokens it charged for.

``build_log_event`` divides ``completion_tokens`` (the provider's count, which
includes every token of a tool call's arguments) by ``last_token_at -
first_token_at``. Round 133 stamped ``last_token_at`` on ``ToolCallOpen`` so a
tool-call-only stream had *some* generation window — but it deliberately left
``ToolCallArgsDelta`` out of the bound, on the reasoning that each args frame is
an SSE frame rather than a model token.

That reasoning holds for the *window* and not for the *ratio*. The numerator is
the provider's token count, and it includes the args tokens. So a stream whose
bulk of output is one large tool call divides 5000 tokens by the few hundred
milliseconds between the first text delta and the tool open, and reports a rate
that no amount of real generation could produce. Measured against the same
request's own round-trip rate, it is impossible by a factor of three or more,
and it grows with args size while the window does not — unbounded, not a fixed
offset.

Fix: stamp ``last_token_at`` on ``ToolCallArgsDelta`` too, so the window closes
on the last content-bearing delta of any kind. The round-133 concern about
transport time is preserved by leaving ``ToolCallClose`` out of the bound —
providers batch sibling closes at ``finish_reason``.

The other two tests here cover the AUDIT #396 / #397 grid bugs, which make the
two halves of one admin panel disagree about the same traffic:

* ``stats.timeseries`` (ring backend) computed a bucket-aligned grid whose first
  bucket sits *above* the window cutoff, and then dropped every row that landed
  in the leading gap — so a chart under-reported the panel's own totals.
* ``DBSink.read_timeseries`` (DB backend) zero-filled the grid correctly but
  emitted ``sorted(by_t)`` over *all* populated buckets, including the leading
  gap's, so it returned ``n_buckets + 1`` entries.
"""

import asyncio
import time

import httpx
import orjson
import pytest
import respx
import sqlalchemy.ext.asyncio as saa
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
from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent
from wiwi.server import stats
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# Past the 0.05s minimum generation window in build_log_event.
GAP = 0.30


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


@pytest.fixture
async def db():
    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    sink = DBSink(engine)
    await sink.startup()
    yield sink
    await engine.dispose()


def _chunk(delta, finish=None, usage=None) -> bytes:
    body = {"id": "c", "object": "chat.completion.chunk", "model": "gpt-4o",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return b"data: " + orjson.dumps(body) + b"\n\n"


def _tool(index: int, name: str = "write_file", args: str = "") -> dict:
    return {"tool_calls": [{"index": index, "id": f"call_{index}",
                            "type": "function",
                            "function": {"name": name, "arguments": args}}]}


async def _args_heavy_stream(completion_tokens: int, arg_frames: int):
    """One short text delta, then a tool call whose ARGS carry the bulk.

    The args stream over ``arg_frames`` paced frames, so the time spent
    producing them is real generation time. The tokens for them are inside
    ``completion_tokens``. Before the fix the window closed at the open and
    ignored all of it.
    """
    yield _chunk({"role": "assistant", "content": "ok"})
    await asyncio.sleep(GAP)
    yield _chunk(_tool(0, args='{"path":"' + "x" * 2000 + '"}'))
    for _ in range(arg_frames):
        await asyncio.sleep(0.02)
        yield _chunk(_tool(0, args="y" * 200))
    yield _chunk({}, finish="tool_calls", usage={
        "prompt_tokens": 5, "completion_tokens": completion_tokens,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0}})
    yield b"data: [DONE]\n\n"


async def _logs(client) -> list[dict]:
    r = await client.get("/admin/logs/requests", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["logs"]


async def _one_row(client, timeout: float = 8.0) -> dict:
    rows = await _all_rows(client, 1, timeout)
    return rows[0]


async def _all_rows(client, expected: int, timeout: float = 8.0) -> list[dict]:
    """Poll the log endpoint until *expected* rows have landed.

    The log sink is written by a background pump off an ``asyncio.Queue``, so a
    row is not there the instant the response ends.
    """
    deadline = time.time() + timeout
    rows: list[dict] = []
    while time.time() < deadline:
        rows = await _logs(client)
        if len(rows) >= expected:
            return rows
        await asyncio.sleep(0.02)
    raise AssertionError(f"log pump wrote {len(rows)} of {expected} rows")


# -- the TPS window must cover the tokens it is charged for ----------------------


@respx.mock
async def test_tool_args_tokens_are_inside_the_tps_window(client):
    """A tool call whose args dominate the output cannot report an impossible rate.

    Before the fix this reported ~26,000 tok/s for a request whose own
    round-trip rate was ~11,000 tok/s. The generation window is a subset of the
    round trip, so ``tps`` may legitimately sit a little ABOVE the round-trip
    average — prefill is excluded from it — but only by the share of the round
    trip prefill occupies, which here is single-digit milliseconds. A 3x gap
    means the denominator covered only a fraction of the numerator's generation,
    and before the fix it grew without bound as the args grew while the window
    did not.
    """
    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"},
        content=_args_heavy_stream(completion_tokens=8000, arg_frames=20))
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "stream": True,
        "messages": [{"role": "user", "content": "write the file"}]}, headers=AUTH)
    assert r.status_code == 200, r.text
    await r.aread()

    row = await _one_row(client)
    assert row["status"] == 200, row
    assert row["tok_out"] == 8000, row
    assert row["tps"] > 0, f"args-heavy stream lost its TPS entirely: {row}"
    round_trip_rate = row["tok_out"] / (row["latency_ms"] / 1000)
    assert row["tps"] <= round_trip_rate * 1.5, (
        f"tps {row['tps']} is {row['tps'] / round_trip_rate:.1f}x the request's "
        f"own round-trip rate {round_trip_rate:.1f} tok/s; the window excluded "
        f"the tool-args generation the numerator charges for: {row}")
    # And the window is now the whole paced generation span — first text delta
    # to last args fragment — rather than just its head.
    paced = 8000 / (GAP + 20 * 0.02)
    assert row["tps"] <= paced * 1.5, (
        f"tps {row['tps']} exceeds the paced generation rate {paced:.1f} by "
        f"more than slack allows: {row}")


async def test_tps_scales_with_args_volume_not_against_it(client):
    """The implied generation window must cover the args, at every size.

    ``tok_out / tps`` reconstructs the window the gateway divided by. Before
    the fix it collapsed to the pacing of the first two frames (~0.30 s) no
    matter how many args frames followed, so a bigger tool call divided more
    tokens by the same small denominator. The invariant is therefore about the
    *window*, not about the rate: the two fixtures legitimately report different
    rates, because the fixed opening gap is amortized over a different number
    of tokens in each.
    """
    for tokens, frames in ((2000, 5), (8000, 20)):
        # A fresh mock per request: ``content=`` takes a single-use async
        # generator, and reusing one leaves the second request reading an
        # exhausted stream.
        with respx.mock:
            respx.post(OPENAI_URL).respond(
                200, headers={"content-type": "text/event-stream"},
                content=_args_heavy_stream(tokens, frames))
            r = await client.post("/v1/chat/completions", json={
                "model": "gpt-4o", "stream": True,
                "messages": [{"role": "user", "content": f"write {tokens}"}]},
                headers=AUTH)
            assert r.status_code == 200, r.text
            await r.aread()
    # Selected by token count, not by position: the log endpoint does not
    # promise an order, and pairing the wrong row to the wrong pacing would
    # make this test assert nonsense.
    logged = await _all_rows(client, 2)
    rates = [next(row for row in logged if row["tok_out"] == t)
             for t in (2000, 8000)]

    small, large = rates
    assert small["tok_out"] == 2000 and large["tok_out"] == 8000, rates
    for row, frames in ((small, 5), (large, 20)):
        assert row["tps"] > 0, row
        paced_window = GAP + frames * 0.02
        window = row["tok_out"] / row["tps"]
        assert window >= paced_window * 0.8, (
            f"the window behind tps {row['tps']} is {window:.3f}s, shorter than "
            f"the {paced_window:.3f}s the stream spent generating its "
            f"{row['tok_out']} tokens: {row}")
        paced = row["tok_out"] / paced_window
        assert row["tps"] <= paced * 1.5, (
            f"tps {row['tps']} exceeds its own paced generation rate "
            f"{paced:.1f}: {row}")


@respx.mock
async def test_text_only_stream_tps_is_unchanged_by_the_fix(client):
    """The ordinary prose path must not move.

    A text-only stream has no args deltas, so the extra stamp never fires. This
    pins that the fix is scoped to tool calls rather than shifting every TPS
    figure on the dashboard.
    """
    async def text_stream():
        yield _chunk({"role": "assistant", "content": "he"})
        await asyncio.sleep(0.12)
        yield _chunk({"content": "llo"})
        await asyncio.sleep(0.12)
        yield _chunk({}, finish="stop", usage={
            "prompt_tokens": 5, "completion_tokens": 2,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0}})
        yield b"data: [DONE]\n\n"

    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"}, content=text_stream())
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "stream": True,
        "messages": [{"role": "user", "content": "hi"}]}, headers=AUTH)
    assert r.status_code == 200, r.text
    await r.aread()

    row = await _one_row(client)
    # 2 tokens over the single 0.12s gap between the first and last text
    # delta (~16.7 tok/s): the generation phase, neither the whole round trip
    # (which also carries prefill) nor a window inflated by args deltas.
    assert 8.0 <= row["tps"] <= 30.0, (
        f"text-only TPS {row['tps']} is outside the paced generation band "
        f"(~16.7 tok/s): {row}")


# -- AUDIT #396: the ring timeseries must not drop leading-edge rows -------------


def _evt(ts: float, **kw) -> LogEvent:
    defaults = {"stream": "request", "ts": ts, "request_id": f"r{ts}",
                "surface": "chat", "status": 200, "model_group": "g",
                "tok_in": 100, "tok_out": 10, "tps": 50.0, "ttft_ms": 100.0,
                "latency_ms": 500.0}
    defaults.update(kw)
    return LogEvent(**defaults)


def test_ring_timeseries_keeps_every_in_window_row():
    """A row inside the window must appear in some bucket.

    ``window_events`` admits ``ts >= now - minutes*60``; the grid is
    bucket-aligned, so when ``now`` falls mid-bucket its first bucket starts
    *above* the cutoff. The leading rows then computed a negative index and were
    dropped by the bounds guard — the chart under-reporting the very totals the
    panel above it prints.
    """
    now = 1_000_030.0  # mid-minute, so the grid's first bucket sits above the cutoff
    size = 60
    cutoff = now - 3600
    first_t = int(now // size) * size - (3600 // size - 1) * size
    assert first_t > cutoff, "fixture no longer exercises the leading gap"
    events = [_evt(cutoff + 5), _evt(now - 10)]  # one in the gap, one well inside

    ts = stats.timeseries(events, "minute", "tokens", 60, now=now)
    ov = stats.overview(events, 60, now=now)

    assert len(ts["buckets"]) == 60, len(ts["buckets"])
    assert sum(b["tok_in"] for b in ts["buckets"]) == ov["tok_in"], (
        f"timeseries accounts for {sum(b['tok_in'] for b in ts['buckets'])} of "
        f"the {ov['tok_in']} tokens overview counts for the same window")


def test_ring_timeseries_tps_coverage_matches_overview():
    """The same traffic, measured for TPS, must land in the chart too."""
    now = 1_000_030.0
    cutoff = now - 3600
    events = [_evt(cutoff + 5), _evt(now - 10)]

    ts = stats.timeseries(events, "minute", "tps", 60, now=now)
    ov = stats.overview(events, 60, now=now)

    assert ov["tps_sample_ratio"] == 1.0, ov
    populated = [b for b in ts["buckets"] if b["tps_avg"] > 0]
    assert len(populated) == 2, (
        f"only {len(populated)} of 2 in-window TPS samples reached the chart")


# -- AUDIT #397: the DB timeseries must honour the fixed grid --------------------


async def test_db_timeseries_emits_exactly_the_fixed_grid(db):
    """The DB backend must return the same bucket count as the ring backend."""
    now = time.time()
    cutoff = now - 3595  # in-window, but below the grid's first bucket
    await db.write_requests([_evt(cutoff, tok_in=7), _evt(now - 30, tok_in=11)])

    ts = await db.read_timeseries(60, "tokens", 60)
    assert len(ts["buckets"]) == 60, (
        f"DB path returned {len(ts['buckets'])} buckets for a 60-bucket grid")


async def test_db_timeseries_keeps_the_leading_gap_traffic(db):
    """Clamping the grid must not throw the clamped rows away.

    The extra bucket existed because those rows were real. Folding them into
    the first grid bucket keeps the token totals equal to the overview's, which
    is the property #397's shape bug was hiding.
    """
    now = time.time()
    await db.write_requests([_evt(now - 3595, tok_in=7), _evt(now - 30, tok_in=11)])

    ts = await db.read_timeseries(60, "tokens", 60)
    ov = await db.read_overview(60)
    assert sum(b["tok_in"] for b in ts["buckets"]) == ov["tok_in"] == 18, (
        f"timeseries tok_in {sum(b['tok_in'] for b in ts['buckets'])} != "
        f"overview {ov['tok_in']}")


@pytest.mark.parametrize("minutes,bucket_seconds", [(60, 60), (1440, 60), (1441, 3600)])
async def test_db_timeseries_grid_is_exact_at_every_bucket_size(db, minutes,
                                                               bucket_seconds):
    """The contract holds across all four bucket widths, not just the default."""
    now = time.time()
    await db.write_requests([_evt(now - 30, tok_in=5)])
    grid = max(1, minutes * 60 // bucket_seconds)
    ts = await db.read_timeseries(bucket_seconds, "tokens", minutes)
    assert len(ts["buckets"]) == grid, (
        f"minutes={minutes} bs={bucket_seconds}: {len(ts['buckets'])} buckets, "
        f"grid is {grid}")