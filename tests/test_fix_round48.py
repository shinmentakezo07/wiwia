"""Round-48 regression tests — token-accounting honesty.

Four defects, each verified by source reading against the pre-fix tree:

- #130 ``/v1/messages/count_tokens`` counted only ``TextPart``. Tool schemas,
  ``tool_use``, ``tool_result`` and ``thinking`` blocks contributed zero, so a
  client that sends large tool definitions (Claude Code) was told a far smaller
  number than wiwi itself estimates when a provider omits usage. The endpoint
  also used a private ``len(text) // 4 + 1`` heuristic instead of the shared
  ``estimate_tokens`` (tiktoken where available), so the number reported to the
  client disagreed with the number wiwi uses internally for the same text.
- #131 the ``estimated`` flag was write-only. The streaming fallback estimator
  set ``UsageFinal.estimated``, the gateway carried it into ``ir.Usage`` as
  ``reasoning_estimated``, and nothing ever read it — so logs, stats, metrics
  and the DB all presented estimated token counts as provider-reported fact.
  The name was also wrong: the fallback estimates the *whole* usage (prompt
  included), not just reasoning.
- #121 ``RateLimiter.release()`` refunded the key's RPM reservation but leaked
  the ``global:rpm`` one that ``check()`` had taken, so every failed request
  burned a global slot for the rest of the window. Residual of the #70 fix.
- #31 (stale entry) the Redis limiter no longer counts requests where it should
  sum tokens — ``tests/test_fix_round17.py`` pins the fixed behaviour. The
  entry needs marking, not code.

Each test is discriminating: it fails against the pre-fix source and passes
after the fix. Controls are included wherever a naive fix would overcorrect.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import build_log_event
from wiwi.cost.pricing import estimate_tokens
from wiwi.ir import types as ir
from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent
from wiwi.ratelimit.memory import RateLimiter
from wiwi.server.app import create_app
from wiwi.server.metrics import render_metrics


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


# ---------------------------------------------------------------------------
# #130 — count_tokens must count every part kind, and use the shared estimator
# ---------------------------------------------------------------------------

@respx.mock
async def test_count_tokens_includes_tool_schemas(client):
    """A request whose bulk is its tool schemas must not report ~0.

    Pre-fix the endpoint walked only ``TextPart``, so tool definitions were
    invisible and a client sizing its context was told they were free.
    """
    big_schema = {
        "type": "object",
        "properties": {f"param_{i}": {"type": "string", "description": "x" * 50}
                       for i in range(20)},
    }
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "do_thing", "description": "does a thing",
                   "input_schema": big_schema}],
    }, headers={"x-api-key": "sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    total = r.json()["input_tokens"]
    # The schema alone is >1500 chars (~375 tokens); a TextPart-only count of
    # "hi" is 1.
    assert total > 200, (
        f"tool schemas contribute nothing to count_tokens (got {total})"
    )


@respx.mock
async def test_count_tokens_includes_tool_result_and_thinking(client):
    """Replayed agentic history must be counted, not just stray text blocks.

    Pre-fix ``tool_use``/``tool_result``/``thinking`` parts were invisible, so
    a long transcript was reported as the size of its text alone.
    """
    filler = "tool output line\n" * 100
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "let me think " * 60,
                 "signature": "sig"},
                {"type": "tool_use", "id": "t1", "name": "read_file",
                 "input": {"path": "/etc/hosts"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": filler},
            ]},
        ],
    }, headers={"x-api-key": "sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    total = r.json()["input_tokens"]
    # The tool_result filler alone is ~1700 chars (~425 tokens).
    assert total > 300, (
        f"tool_result/thinking parts are invisible to count_tokens (got {total})"
    )


@respx.mock
async def test_count_tokens_uses_the_shared_estimator(client):
    """The endpoint must not be a second, private token estimator.

    Pre-fix it hardcoded ``len(text) // 4 + 1``, which diverges from
    ``estimate_tokens`` (tiktoken where available) — so the number a client is
    told differs from the number wiwi uses internally for identical text.
    """
    text = "abcd" * 10
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": text}],
    }, headers={"x-api-key": "sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    assert r.json()["input_tokens"] == estimate_tokens(text, "gpt-4o"), (
        "count_tokens disagrees with the shared estimate_tokens helper"
    )


@respx.mock
async def test_count_tokens_empty_is_at_least_one(client):
    """Control: an empty request must still report a positive count."""
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "gpt-4o", "messages": [],
    }, headers={"x-api-key": "sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    assert r.json()["input_tokens"] >= 1


# ---------------------------------------------------------------------------
# #131 — estimated usage must be distinguishable downstream
# ---------------------------------------------------------------------------

def _ctx_with_usage(usage: ir.Usage) -> RequestContext:
    ctx = RequestContext(surface="chat",
                         ir_req=ir.Request(model="gpt-4o", messages=[]))
    ctx.usage = usage
    return ctx


def test_ir_usage_has_a_general_estimated_flag():
    """``ir.Usage`` must say whether its counts were estimated.

    Pre-fix the only flag was ``reasoning_estimated``, which the streaming
    fallback set from a whole-usage estimate — mislabelling a prompt estimate
    as a reasoning estimate.
    """
    u = ir.Usage(prompt_tokens=10, completion_tokens=5, estimated=True)
    assert u.estimated is True


def test_log_event_records_estimated_usage():
    """A log row built from estimated usage must say so, and only then."""
    evt = build_log_event(_ctx_with_usage(
        ir.Usage(prompt_tokens=10, completion_tokens=5, estimated=True)))
    assert evt.usage_estimated is True, (
        "LogEvent does not record that this request's usage was estimated"
    )

    real = build_log_event(_ctx_with_usage(
        ir.Usage(prompt_tokens=10, completion_tokens=5)))
    assert real.usage_estimated is False, (
        "provider-reported usage must not be flagged as estimated"
    )


def test_metrics_expose_estimated_requests():
    """Prometheus output must distinguish estimated from reported usage."""
    events = [
        LogEvent(stream="request", ts=0.0, tok_in=100, tok_out=10,
                 usage_estimated=True),
        LogEvent(stream="request", ts=0.0, tok_in=100, tok_out=10),
    ]
    text = render_metrics(events)
    assert "wiwi_usage_estimated_requests_total" in text, (
        "metrics cannot distinguish estimated usage from real usage"
    )
    assert "wiwi_usage_estimated_requests_total 1" in text


def test_merged_resume_usage_stays_estimated():
    """A merged total containing estimated tokens is itself estimated.

    ``merge_resume_context`` sums the pre-failure attempt's usage with the
    resumed attempt's. If either half was estimated, the sum is partly
    guessed — reporting it as provider-reported would overstate confidence
    in exactly the failover path where upstream already misbehaved.
    """
    from wiwi.core.gateway import merge_resume_context

    origin = _ctx_with_usage(ir.Usage(prompt_tokens=10, completion_tokens=5))
    resumed = _ctx_with_usage(
        ir.Usage(prompt_tokens=20, completion_tokens=7, estimated=True))
    merge_resume_context(origin, resumed)
    assert origin.usage.prompt_tokens == 30
    assert origin.usage.estimated is True, (
        "merged usage that includes estimated tokens is reported as real"
    )

    # Control: two provider-reported halves stay provider-reported.
    clean_origin = _ctx_with_usage(ir.Usage(prompt_tokens=10))
    clean_resumed = _ctx_with_usage(ir.Usage(prompt_tokens=20))
    merge_resume_context(clean_origin, clean_resumed)
    assert clean_origin.usage.estimated is False, (
        "fully provider-reported usage was flagged as estimated"
    )


async def test_db_round_trips_the_estimated_flag():
    """The flag must survive the DB write/read path, not just the in-memory one."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sink = DBSink(engine)
    await sink.startup()
    try:
        await sink.write_requests([
            LogEvent(stream="request", ts=1.0, request_id="r1",
                     tok_in=100, tok_out=10, usage_estimated=True),
            LogEvent(stream="request", ts=2.0, request_id="r2",
                     tok_in=100, tok_out=10),
        ])
        rows = await sink.read_requests(limit=10)
        by_id = {r["request_id"]: r for r in rows}
        assert by_id["r1"]["usage_estimated"] == 1, (
            f"estimated flag lost in the DB round-trip: {by_id['r1']!r}"
        )
        assert by_id["r2"]["usage_estimated"] == 0, (
            "a provider-reported row must not be flagged as estimated"
        )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# #121 — release() must refund the global RPM reservation too
# ---------------------------------------------------------------------------

async def test_release_refunds_global_rpm_reservation():
    """A failed request must not permanently consume a global RPM slot.

    Pre-fix ``release()`` popped only ``{key_id}:rpm`` while ``check()`` also
    reserved in ``global:rpm``, so each failed request burned one global slot
    for the rest of the window.
    """
    rl = RateLimiter(global_rpm=2)
    await rl.check("k1", request_id="a")
    await rl.check("k2", request_id="b")
    allowed, _ = await rl.check("k3", request_id="c")
    assert not allowed, "control: global_rpm=2 must refuse a third request"

    # k1's upstream call now fails: its reservation is refunded.
    await rl.release("k1", request_id="a")

    allowed2, retry2 = await rl.check("k3", request_id="c2")
    assert allowed2, (
        f"released global RPM slot was never refunded (retry_after={retry2})"
    )


async def test_release_still_refunds_the_key_rpm_reservation():
    """Control: the key-scoped refund from the #70 fix must keep working."""
    rl = RateLimiter()
    await rl.check("k1", key_rpm=1, request_id="a")
    allowed, _ = await rl.check("k1", key_rpm=1, request_id="b")
    assert not allowed, "control: key_rpm=1 must refuse a second request"
    await rl.release("k1", request_id="a")
    allowed2, _ = await rl.check("k1", key_rpm=1, request_id="c")
    assert allowed2, "key-scoped RPM refund regressed"


async def test_release_does_not_refund_confirmed_usage():
    """Control: release() must only refund *estimated* reservations.

    ``app.py`` documents this invariant — release is safe to call after
    ``_record_tpm_usage`` because it never removes reconciled usage.
    """
    rl = RateLimiter(global_rpm=5)
    await rl.check("k1", key_tpm=1000, est_tokens=400, request_id="a")
    await rl.record_tokens("k1", 100, request_id="a")
    await rl.release("k1", request_id="a")
    window = rl._windows["k1:tpm"]
    assert window.total == 100, (
        "release() removed confirmed usage — it must only refund estimates"
    )
