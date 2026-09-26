"""Playground authoritative request-metrics regressions."""

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
    WiwiConfig,
)
from wiwi.logging_core.events import LogEvent
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"


def _config(tmp_path):
    return WiwiConfig(
        providers=[
            ProviderDef(
                name="openai",
                provider="openai",
                keys=[KeyDef(label="main", key="sk-upstream-fake")],
            )
        ],
        model_list=[
            ModelEntry(
                model_name="gpt-4o",
                wiwi_params=DeploymentParams(provider="openai", model="gpt-4o"),
            )
        ],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
        ),
    )


async def test_playground_metrics_route_returns_exact_ring_event(tmp_path):
    app = create_app(_config(tmp_path))
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        signup = await client.post(
            "/auth/signup", json={"username": "metrics-user", "password": "password1"}
        )
        assert signup.status_code == 201
        user_id = signup.json()["user"]["id"]
        keys = await app.state.wiwi.auth.list_keys_for_owner(user_id)
        event = LogEvent(
            stream="request",
            ts=time.time(),
            request_id="owned-request-1",
            key_id=keys[0]["id"],
            tok_in=11,
            tok_out=7,
            tps=3.5,
            ttft_ms=123.4,
            latency_ms=456.7,
            usage_estimated=True,
        )
        await app.state.wiwi.logs.sse.publish("request", event)

        response = await client.get("/admin/logs/requests/owned-request-1/metrics")

        assert response.status_code == 200
        assert response.json() == {
            "request_id": "owned-request-1",
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "tps": 3.5,
            "ttft_ms": 123.4,
            "latency_ms": 456.7,
            "usage_estimated": True,
        }


async def test_playground_metrics_route_reads_db_event_and_marks_estimate(tmp_path):
    app = create_app(_config(tmp_path))
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        signup = await client.post(
            "/auth/signup", json={"username": "db-user", "password": "password1"}
        )
        user_id = signup.json()["user"]["id"]
        keys = await app.state.wiwi.auth.list_keys_for_owner(user_id)
        await app.state.wiwi.logs.db_sink.write_requests([
            LogEvent(
                stream="request",
                ts=time.time(),
                request_id="db-request-1",
                key_id=keys[0]["id"],
                tok_in=20,
                tok_out=8,
                tps=4.25,
                ttft_ms=210.0,
                latency_ms=810.0,
                usage_estimated=True,
            )
        ])

        response = await client.get("/admin/logs/requests/db-request-1/metrics")

        assert response.status_code == 200
        body = response.json()
        assert body["prompt_tokens"] == 20
        assert body["completion_tokens"] == 8
        assert body["total_tokens"] == 28
        assert body["usage_estimated"] is True


async def test_playground_metrics_route_does_not_expose_another_user_event(tmp_path):
    app = create_app(_config(tmp_path))
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/auth/signup", json={"username": "owner-user", "password": "password1"}
        )
        first_id = first.json()["user"]["id"]
        first_key = (await app.state.wiwi.auth.list_keys_for_owner(first_id))[0]["id"]
        await client.post("/auth/logout")
        second = await client.post(
            "/auth/signup", json={"username": "other-user", "password": "password1"}
        )
        assert second.status_code == 201
        await app.state.wiwi.logs.sse.publish(
            "request",
            LogEvent(
                stream="request",
                ts=time.time(),
                request_id="private-request-1",
                key_id=first_key,
                tok_in=99,
                tok_out=88,
                tps=77.0,
                ttft_ms=66.0,
                latency_ms=55.0,
            ),
        )

        response = await client.get("/admin/logs/requests/private-request-1/metrics")

        assert response.status_code == 404


@respx.mock
async def test_streamed_request_metrics_match_response_request_id(tmp_path):
    app = create_app(_config(tmp_path))
    upstream_sse = (
        'data: {"choices":[{"delta":{"role":"assistant","content":"hello"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        text=upstream_sse,
        headers={"content-type": "text/event-stream"},
    )
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        signup = await client.post(
            "/auth/signup", json={"username": "stream-user", "password": "password1"}
        )
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {signup.json()['playground_key']}"},
            json={
                "model": "gpt-4o",
                "stream": True,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status_code == 200
        request_id = response.headers["x-wiwi-request-id"]

        metrics_response = None
        for _ in range(50):
            metrics_response = await client.get(
                f"/admin/logs/requests/{request_id}/metrics"
            )
            if metrics_response.status_code == 200:
                break
            await asyncio.sleep(0.02)

        assert metrics_response is not None
        assert metrics_response.status_code == 200
        assert metrics_response.json()["request_id"] == request_id
        assert metrics_response.json()["prompt_tokens"] == 4
        assert metrics_response.json()["completion_tokens"] == 2


async def test_admin_can_read_exact_metrics_without_owner_scope(tmp_path):
    app = create_app(_config(tmp_path))
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await app.state.wiwi.logs.sse.publish(
            "request",
            LogEvent(
                stream="request",
                ts=time.time(),
                request_id="admin-request-1",
                tok_in=5,
                tok_out=3,
                tps=1.5,
                ttft_ms=20.0,
                latency_ms=30.0,
                usage_estimated=False,
            ),
        )

        response = await client.get(
            "/admin/logs/requests/admin-request-1/metrics",
            headers={"Authorization": f"Bearer {MASTER}"},
        )

        assert response.status_code == 200
        assert response.json()["usage_estimated"] is False
        assert response.json()["total_tokens"] == 8


async def test_playground_metrics_route_requires_authentication(tmp_path):
    app = create_app(_config(tmp_path))
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/admin/logs/requests/any-request/metrics")

        assert response.status_code == 401
