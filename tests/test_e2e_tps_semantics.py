"""End-to-end verification of the round-117 TPS changes.

Where the round-117 tests stop: they call ``build_log_event`` and the stats
sinks directly. This file drives the whole chain the way a client does —

    POST /v1/chat/completions  ->  wire codec  ->  gateway  ->  adapter
      ->  LogEvent  ->  logging subsystem  ->  DB row  ->  /metrics + admin API

— and asserts the new semantics survive every hop. A TPS figure that is
correct in the gateway but silently rescaled, dropped or mislabelled by the
time it reaches a dashboard is exactly the failure round 117 existed to kill,
so every assertion is made against a rendered HTTP response, never against an
in-process value.

Two properties of the harness matter:

- Log rows are written by a background pump off an ``asyncio.Queue``, so
  every read polls until the expected row count lands rather than sleeping a
  guessed interval.
- TPS is only recorded when the generation phase exceeds 0.05s, so the
  streaming fixture deliberately paces its chunks past that threshold. An
  instant stream correctly reports ``tps = 0.0``, which would make these
  tests pass for the wrong reason.
"""

import asyncio
import time

import httpx
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
# Comfortably past the 0.05s minimum generation window in build_log_event.
STREAM_GAP = 0.12


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
        # /metrics is off by default; without this the SPA catch-all answers
        # /metrics with index.html and every exposition assertion would be
        # comparing against markup.
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


def _non_stream_body(completion_tokens: int = 2) -> dict:
    return {
        "id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-4o",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": "hello"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": completion_tokens,
                  "prompt_tokens_details": {"cached_tokens": 0},
                  "completion_tokens_details": {"reasoning_tokens": 0}},
    }


def _chunk(delta: str, finish: str | None = None) -> bytes:
    import orjson
    body = {"id": "c", "object": "chat.completion.chunk", "model": "gpt-4o",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return b"data: " + orjson.dumps(body) + b"\n\n"


async def _paced_stream():
    """A paced OpenAI SSE stream, so the generation phase is measurable."""
    yield _chunk({"role": "assistant", "content": "he"})
    await asyncio.sleep(STREAM_GAP)
    yield _chunk({"content": "llo"})
    await asyncio.sleep(STREAM_GAP)
    import orjson
    final = {"id": "c", "object": "chat.completion.chunk", "model": "gpt-4o",
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                       "prompt_tokens_details": {"cached_tokens": 0},
                       "completion_tokens_details": {"reasoning_tokens": 0}}}
    yield b"data: " + orjson.dumps(final) + b"\n\n"
    yield b"data: [DONE]\n\n"


async def _overview(client) -> dict:
    r = await client.get("/admin/stats/overview?minutes=60", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


async def _await_requests(client, expected: int, timeout: float = 5.0) -> dict:
    """Wait for the log pump, then read the overview exactly once.

    The wait polls ``/admin/logs/requests``, NOT the overview:
    ``DBSink.read_overview`` memoises its result as soon as it has any rows
    (``_cache_put`` is guarded by ``if result.get("requests")``), so polling it
    caches the first partial answer and every later poll replays that stale
    count forever. Reading the row list — which is not cached — to detect
    completion, then touching the overview once, avoids that entirely.
    """
    deadline = time.time() + timeout
    logs = await _logs(client)
    while len(logs) < expected and time.time() < deadline:
        await asyncio.sleep(0.02)
        logs = await _logs(client)
    assert len(logs) >= expected, (
        f"log pump never wrote {expected} rows; saw {len(logs)}")
    return await _overview(client)


async def _logs(client) -> list[dict]:
    r = await client.get("/admin/logs/requests", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["logs"]


async def _slow_non_stream_response(request) -> httpx.Response:
    """A non-streaming upstream that takes real time.

    The old round-trip fallback was guarded by ``latency_ms > 50``. An instant
    in-process mock completes in ~0ms, so the fallback never fired and the
    non-streaming assertions passed *for the wrong reason*: with the fallback
    restored by mutation, only 1 of these 11 tests failed. Delaying the mock
    past the threshold is what lets them fail — restoring the same fallback
    now fails 5 of 11.
    """
    await asyncio.sleep(0.15)
    return httpx.Response(200, json=_non_stream_body())


async def _non_stream(client, n: int = 1):
    for _ in range(n):
        respx.post(OPENAI_URL).side_effect = _slow_non_stream_response
        r = await client.post("/v1/chat/completions", json={
            "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers=AUTH)
        assert r.status_code == 200, r.text


async def _stream(client, n: int = 1):
    for _ in range(n):
        respx.post(OPENAI_URL).respond(
            200, headers={"content-type": "text/event-stream"},
            content=_paced_stream())
        r = await client.post("/v1/chat/completions", json={
            "model": "gpt-4o", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}, headers=AUTH)
        assert r.status_code == 200, r.text
        await r.aread()


# -- non-streaming requests contribute no TPS, end to end -----------------------


@respx.mock
async def test_e2e_non_streaming_request_reports_no_tps(client):
    """A non-streaming request surfaces tps == 0.0 all the way to the DB row.

    Before round 117 this reported 100 tokens / total latency — a round-trip
    figure sharing a quantile family with generation-phase stream values.
    """
    await _non_stream(client)
    await _await_requests(client, 1)
    logged = await _logs(client)
    assert logged, "no request log row was written"
    assert all(row["tps"] == 0.0 for row in logged), \
        f"non-streaming row reported a round-trip TPS: {logged}"


@respx.mock
async def test_e2e_non_streaming_request_absent_from_tps_aggregates(client):
    """And its absence is disclosed rather than silent."""
    await _non_stream(client)
    data = await _await_requests(client, 1)
    assert data["requests"] == 1
    assert data["tps_avg"] == 0.0
    assert data["tps_p95"] == 0.0
    assert data["tps_sample_ratio"] == 0.0


# -- streaming requests still report generation-phase TPS -----------------------


@respx.mock
async def test_e2e_streaming_request_still_reports_tps(client):
    """The generation-phase path survives the full trip intact."""
    await _stream(client)
    await _await_requests(client, 1)
    logged = await _logs(client)
    assert logged, "no request log row was written"
    # 2 tokens over a generation phase of >= 0.12s, so a small positive
    # number. The assertion is that a value survived, not its exact size.
    assert logged[0]["tps"] > 0, f"streaming row lost its TPS in transit: {logged}"


@respx.mock
async def test_e2e_streaming_tps_excludes_prefill(client):
    """Generation speed, not round-trip speed.

    TTFT here is roughly one STREAM_GAP (the first sleep precedes the second
    content chunk), and the generation phase spans two of them. If the old
    round-trip fallback were still in play the reported value would be
    materially lower than the generation-phase one.
    """
    await _stream(client)
    await _await_requests(client, 1)
    row = (await _logs(client))[0]
    ttft_s = row["ttft_ms"] / 1000
    latency_s = row["latency_ms"] / 1000
    # tokens / generation_phase, where generation is a strict subset of
    # round-trip time — so tokens/latency must be strictly smaller.
    assert row["tps"] > 0
    assert row["tps"] >= (row["tok_out"] / latency_s), (
        f"tps {row['tps']} is below the round-trip rate "
        f"{row['tok_out'] / latency_s}; it should exclude prefill")
    assert latency_s > ttft_s >= 0


@respx.mock
async def test_e2e_mixed_traffic_reports_partial_coverage(client):
    """1 streaming + 2 non-streaming -> ratio 1/3 over the one sample.

    The case the old code got wrong in both directions: the two non-streaming
    requests contributed a round-trip-derived value, so the "average
    throughput" described traffic that never streamed at all.
    """
    await _stream(client)
    await _non_stream(client, n=2)
    data = await _await_requests(client, 3)
    assert data["requests"] == 3
    assert data["tps_sample_ratio"] == round(1 / 3, 4)
    assert data["tps_avg"] > 0


# -- the Prometheus exposition --------------------------------------------------


@respx.mock
async def test_e2e_metrics_exposes_p99_and_sample_ratio(client):
    await _stream(client)
    await _non_stream(client)
    await _await_requests(client, 2)
    m = await client.get("/metrics", headers=AUTH)
    assert m.status_code == 200, m.text
    body = m.text
    assert 'wiwi_tps{quantile="0.99"}' in body, "p99 missing from the exposition"
    assert 'wiwi_tps{quantile="0.5"}' in body
    assert 'wiwi_tps{quantile="0.95"}' in body
    assert "wiwi_tps_sample_ratio 0.5" in body, body


@respx.mock
async def test_e2e_metrics_tps_quantiles_are_ordered(client):
    await _stream(client, n=4)
    await _await_requests(client, 4)
    m = await client.get("/metrics", headers=AUTH)
    assert m.status_code == 200, m.text
    values = {}
    for line in m.text.splitlines():
        if line.startswith("wiwi_tps{"):
            q = line.split("quantile=")[1].split("}")[0].strip('"')
            values[q] = float(line.rsplit(" ", 1)[1])
    assert {"0.5", "0.95", "0.99"} <= set(values), values
    assert values["0.5"] <= values["0.95"] <= values["0.99"], values


@respx.mock
async def test_e2e_metrics_tps_absent_when_nothing_streams(client):
    """No streaming request -> no TPS series at all, and a 0.0 ratio.

    Quantile series are only emitted when their sample list is non-empty, so
    an all-non-streaming gateway must not publish a wiwi_tps summary that a
    dashboard would render as a throughput figure.
    """
    await _non_stream(client, n=2)
    await _await_requests(client, 2)
    m = await client.get("/metrics", headers=AUTH)
    body = m.text
    assert "wiwi_tps{" not in body, body
    assert "wiwi_tps_sample_ratio 0.0" in body, body


# -- the timeseries path --------------------------------------------------------


@respx.mock
async def test_e2e_timeseries_buckets_carry_the_ratio(client):
    await _stream(client)
    await _non_stream(client)
    await _await_requests(client, 2)
    ts = await client.get(
        "/admin/stats/timeseries?bucket=minute&metric=tps&minutes=60", headers=AUTH)
    assert ts.status_code == 200, ts.text
    buckets = ts.json()["buckets"]
    assert buckets, "no buckets returned"
    for bucket in buckets:
        assert "tps_sample_ratio" in bucket, f"bucket missing the ratio key: {bucket}"
        assert 0.0 <= bucket["tps_sample_ratio"] <= 1.0
    populated = [b for b in buckets if b["tps_avg"] > 0]
    assert populated, "no populated TPS bucket despite a streaming request"


# -- the other inbound dialects, end to end --------------------------------------


@respx.mock
async def test_e2e_anthropic_surface_streaming_also_reports_tps(client):
    """The semantics are dialect-independent: it is a gateway-core property.

    Anthropic Messages in, OpenAI out, streaming — the TPS recorded must obey
    the same generation-phase rule as the OpenAI Chat surface.
    """
    respx.post(OPENAI_URL).respond(
        200, headers={"content-type": "text/event-stream"},
        content=_paced_stream())
    r = await client.post("/v1/messages", json={
        "model": "gpt-4o", "max_tokens": 100, "stream": True,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]},
        headers={"x-api-key": MASTER, "anthropic-version": "2023-06-01"})
    assert r.status_code == 200, r.text
    await r.aread()
    data = await _await_requests(client, 1)
    assert data["tps_sample_ratio"] == 1.0
    assert data["tps_avg"] > 0


@respx.mock
async def test_e2e_anthropic_surface_non_streaming_reports_no_tps(client):
    respx.post(OPENAI_URL).respond(json=_non_stream_body())
    r = await client.post("/v1/messages", json={
        "model": "gpt-4o", "max_tokens": 100,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]},
        headers={"x-api-key": MASTER, "anthropic-version": "2023-06-01"})
    assert r.status_code == 200, r.text
    data = await _await_requests(client, 1)
    assert data["tps_avg"] == 0.0
    assert data["tps_sample_ratio"] == 0.0
