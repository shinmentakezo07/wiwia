"""Round 201 — Responses continuation and streamed-store regressions."""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx
import respx
from asgi_lifespan import LifespanManager
from starlette.requests import ClientDisconnect

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app

_AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}
_UPSTREAM = "https://relay.example.com/messages"

_CONFIG = """
general_settings:
  master_key: sk-wiwi-master-test
  database_url: "sqlite+aiosqlite:///:memory:"
providers:
  - name: relay
    provider: anthropic
    base_url: https://relay.example.com
    keys: [{label: main, key: sk-test}]
model_list:
  - model_name: gpt-test
    wiwi_params: {provider: relay, model: claude-x}
"""


def _reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "msg_x", "type": "message", "role": "assistant",
        "model": "claude-x",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    })


@respx.mock
async def test_third_chained_response_keeps_the_first_turn():
    """A continuation stores the complete history, not just its immediate input."""
    bodies: list[dict] = []

    def reply(request):
        bodies.append(json.loads(request.content))
        return _reply(f"A{len(bodies)}")

    respx.post(_UPSTREAM).mock(side_effect=reply)
    app = create_app(load_config_from_string(_CONFIG))
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test", headers=_AUTH) as client:
        first = await client.post("/v1/responses", json={
            "model": "gpt-test", "input": "Q1"})
        second = await client.post("/v1/responses", json={
            "model": "gpt-test", "input": "Q2",
            "previous_response_id": first.json()["id"]})
        third = await client.post("/v1/responses", json={
            "model": "gpt-test", "input": "Q3",
            "previous_response_id": second.json()["id"]})
        assert third.status_code == 200, third.text

    assert len(bodies) == 3
    transcript = json.dumps(bodies[2])
    assert "Q1" in transcript, "turn 1 disappeared from the third turn"
    assert "Q2" in transcript
    assert "Q3" in transcript


async def test_cancelled_stream_does_not_store_a_completed_partial_response():
    """An abandoned stream must not leave a completed continuation handle."""
    app = create_app(load_config_from_string(_CONFIG))
    writes: list[tuple[tuple, dict]] = []
    response_id = ""
    async with LifespanManager(app):
        state = app.state.wiwi
        original_put = state.response_store.put
        write_attempted = asyncio.Event()

        async def record_put(*args, **kwargs):
            writes.append((args, kwargs))
            write_attempted.set()
            await original_put(*args, **kwargs)

        state.response_store.put = record_put

        class _Gateway:
            async def stream(self, ctx):
                from wiwi.streaming import deltas as dl

                yield dl.StreamStart(model="gpt-test", group="gpt-test")
                yield dl.TextDelta("unfinished")
                await asyncio.Event().wait()

        state.gateways["chat"].stream = _Gateway().stream
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": "/v1/responses", "raw_path": b"/v1/responses",
            "query_string": b"", "root_path": "", "state": {},
            "headers": [(k.lower().encode(), v.encode()) for k, v in _AUTH.items()]
                       + [(b"content-type", b"application/json")],
            "client": ("testclient", 0), "server": ("testserver", 80),
        }
        messages = [
            {"type": "http.request", "body": json.dumps({
                "model": "gpt-test", "input": "unfinished", "stream": True,
            }).encode(), "more_body": False},
            {"type": "http.disconnect"},
        ]

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_id
            body = message.get("body", b"")
            if message["type"] == "http.response.start":
                response_id = next(
                    value.decode() for key, value in message["headers"]
                    if key == b"x-wiwi-request-id")
            if message["type"] == "http.response.body" and b"output_text.delta" in body:
                raise ClientDisconnect

        task = asyncio.create_task(app(scope, receive, send))
        with contextlib.suppress(ClientDisconnect, asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert response_id, "the stream never exposed its response id"
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(write_attempted.wait(), timeout=1)
        assert not writes, f"a cancelled stream attempted to persist a partial response: {writes!r}"
