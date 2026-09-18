"""Round 69: the journal-replay branch never refunds its admission reservation.

A reconnect served from the journal (``x-wiwi-stream-id`` + ``Last-Event-ID``)
consumes zero upstream tokens, but it passes through ``enforce_rate_limit``
like any other request, which reserves an RPM event and an estimated TPM event
in both the key and global scopes. The replay branch then returns a
``StreamingResponse`` built from the journal without calling
``_release_tpm_reservation`` — so both reservations stay in the window for the
full 60 s, throttling unrelated traffic.

The cache-hit path (``app.py``) states the rule and follows it: "served
locally: the upstream consumed zero tokens, so the estimated reservation taken
at admission must be refunded". The replay path serves even more locally — it
makes no upstream call at all — and is the one path that never refunds.

This is AUDIT #120, still live. Round 66 (``_find_refund``) sharpened it: the
old identity-blind refund fell back to "pop the newest estimated event", which
would incidentally have reclaimed a leaked replay reservation. Refunds are now
strictly identity-matched, so nothing can reclaim it — the reservation is
permanent for the window. The strictness is correct; the missing refund is the
defect, and these tests pin the refund rather than loosening the finder.

The leak is reachable without any upstream failure: a client reconnecting after
a dropped stream is the ordinary case (Claude Code does exactly this), so every
reconnect silently burns a slot.
"""

from __future__ import annotations

import asyncio
import time

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

STREAM_BODY = (
    'data: {"choices":[{"delta":{"role":"assistant","content":"He"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"y"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
)

HEADERS = {"Authorization": "Bearer sk-wiwi-master-test"}


def _cfg(tmp_path, *, global_rpm: int | None = None,
         global_tpm: int | None = None) -> WiwiConfig:
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
            global_rpm=global_rpm,
            global_tpm=global_tpm,
            stream_journal_enabled=True,
            stream_journal_dir=str(tmp_path / "journals"),
            stream_journal_ttl_s=600.0,
            stream_journal_max_bytes=1 << 20),
    )


def _body() -> dict:
    return {"model": "gpt-4o", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}


async def _first_stream(client, route):
    """Run one streaming request and return its request id (the journal id)."""
    s = await client.post("/v1/chat/completions", json=_body(), headers=HEADERS)
    assert s.status_code == 200
    assert route.call_count == 1
    return s.headers["x-wiwi-request-id"]


@respx.mock
async def test_replay_does_not_consume_a_global_rpm_slot(tmp_path):
    """A replay must leave the global RPM window as it found it.

    Behavioral form: with ``global_rpm=2`` the original stream holds one slot.
    The replay is admitted (it takes the second) and must give it straight
    back, so a *third* request still finds room. Pre-fix the replay keeps its
    slot and the third request is refused with 429.
    """
    cfg = _cfg(tmp_path, global_rpm=2)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(text=STREAM_BODY)

            rid = await _first_stream(c, route)

            replay_headers = {**HEADERS, "x-wiwi-stream-id": rid,
                              "last-event-id": "0"}
            r = await c.post("/v1/chat/completions", json=_body(),
                             headers=replay_headers)
            assert r.status_code == 200
            assert r.headers.get("x-wiwi-stream-replay") == rid
            assert route.call_count == 1, "replay must not call upstream"

            # The replay served zero upstream tokens, so it must not be
            # holding a slot. Only the original stream's slot is live.
            third = await c.post("/v1/chat/completions", json=_body(),
                                 headers=HEADERS)
            assert third.status_code == 200, (
                "a replay permanently consumed a global RPM slot: the next "
                "request was refused with "
                f"{third.status_code} {third.text[:200]!r}")
            assert third.headers.get("x-wiwi-stream-replay") is None


@respx.mock
async def test_replay_does_not_consume_global_tpm(tmp_path):
    """A replay must not hold its estimated TPM reservation either.

    ``global_tpm`` is set so the original stream's actual usage plus one
    body-size estimate exceeds the cap: pre-fix the replay's phantom estimate
    is what pushes a later request over, and it 429s.
    """
    cfg = _cfg(tmp_path, global_tpm=1000)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(text=STREAM_BODY)

            rid = await _first_stream(c, route)

            replay_headers = {**HEADERS, "x-wiwi-stream-id": rid,
                              "last-event-id": "0"}
            r = await c.post("/v1/chat/completions", json=_body(),
                             headers=replay_headers)
            assert r.status_code == 200
            assert route.call_count == 1

            # Inspect the window directly: the replay's estimate is the only
            # thing that could have inflated it. The original stream's real
            # usage (3+2) is all that may remain.
            window = app.state.wiwi.limiter._windows.get("global:tpm")
            held = window.total if window is not None else 0
            assert held <= 5, (
                f"global:tpm holds {held} tokens after a replay; the original "
                "stream used 5 and the replay consumed none, so the replay's "
                "estimated reservation was never refunded")


@respx.mock
async def test_replay_appears_in_the_request_log(tmp_path):
    """A served replay is a request: it must be visible to ``/admin/stats``.

    The replay branch returns before ``log_request``, so reconnects are
    invisible to the console's request log and to every rollup built on it.
    """
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(text=STREAM_BODY)

            rid = await _first_stream(c, route)

            replay_headers = {**HEADERS, "x-wiwi-stream-id": rid,
                              "last-event-id": "0"}
            r = await c.post("/v1/chat/completions", json=_body(),
                             headers=replay_headers)
            assert r.status_code == 200
            replay_rid = r.headers["x-wiwi-request-id"]

            # The logging pump is async; poll the ring until the row lands.
            deadline = time.monotonic() + 5.0
            seen = False
            while not seen and time.monotonic() < deadline:
                events = [e for _, e in
                          await app.state.wiwi.logs.sse.replay("request", 0)]
                seen = any(e.request_id == replay_rid for e in events)
                if not seen:
                    await asyncio.sleep(0.05)
            assert seen, (
                "the replay produced no request-log row, so reconnects are "
                "invisible to /admin/stats and every rollup built on it")


@respx.mock
async def test_replay_log_row_records_zero_upstream_cost(tmp_path):
    """Control: the replay's log row must not bill the client for a replay.

    Pins that the refund added for the leak does not also fabricate usage —
    a replay is served from disk and costs nothing.
    """
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(text=STREAM_BODY)

            rid = await _first_stream(c, route)
            replay_headers = {**HEADERS, "x-wiwi-stream-id": rid,
                              "last-event-id": "0"}
            r = await c.post("/v1/chat/completions", json=_body(),
                             headers=replay_headers)
            replay_rid = r.headers["x-wiwi-request-id"]

            deadline = time.monotonic() + 5.0
            row = None
            while row is None and time.monotonic() < deadline:
                events = [e for _, e in
                          await app.state.wiwi.logs.sse.replay("request", 0)]
                row = next((e for e in events if e.request_id == replay_rid),
                           None)
                if row is None:
                    await asyncio.sleep(0.05)
            assert row is not None
            assert not row.tok_in and not row.tok_out, (
                f"replay billed tokens: in={row.tok_in} out={row.tok_out}")
            assert not row.cost, f"replay billed cost {row.cost}"
            assert route.call_count == 1
