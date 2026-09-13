"""Round-50 regression tests — budget exhaustion must answer 402, not 429.

One defect, found by running the gateway end to end against a fake upstream
rather than through the ASGI test client:

- #142 a key already at its cap was refused with **429 ``budget_exceeded``**
  by the pre-flight check in ``authenticate``, while the post-hoc check in
  ``run_chat_like`` refuses the same condition with **402 ``budget_exceeded``**.
  Three documents pin the contract — ``docs/API_REFERENCE.md`` ("``402``
  (budget cap exceeded) … ``429`` (rate limit)"), ``docs/ADMIN.md``
  ("exceeding a cap yields ``402`` on subsequent requests"), and
  ``docs/ARCHITECTURE.md`` ("budget cap → 402"). 429 is also the status that
  tells an SDK to *back off and retry*, which is the wrong instruction for a
  cap that will never clear on its own.

The existing suite could not see it: ``test_cache_never_serves_over_budget_payload``
uses ``max_budget=1e-9``, so the first request passes the pre-flight check
(``0.0 >= 1e-9`` is false) and is caught by the post-hoc 402 instead. Only a
key minted at ``max_budget=0`` — or any key refused *after* its first
overspend — reaches the pre-flight branch.
"""

from __future__ import annotations

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
from wiwi.server.app import create_app

AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}

OPENAI_BODY = {
    "id": "chatcmpl-c", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant",
                                         "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}


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


def _body() -> dict:
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


@respx.mock
async def test_preflight_over_budget_is_402_not_429():
    """A key minted at its cap must be refused with 402.

    Pre-fix the pre-flight check returned 429 ``budget_exceeded`` — a status
    that means "slow down and retry", for a condition that never clears.
    """
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="capped", max_budget=0.0)
            vk = {"Authorization": f"Bearer {plain}"}
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                json=OPENAI_BODY)

            r = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert r.status_code == 402, (
                f"a key at its cap must be refused with 402 (budget cap "
                f"exceeded), got {r.status_code}: {r.text[:200]}"
            )
            assert r.json()["error"]["type"] == "budget_exceeded"


@respx.mock
async def test_posthoc_over_budget_still_402():
    """Control: the post-hoc path's 402 must not regress to 429.

    ``max_budget=1e-9`` passes the pre-flight check on the first request and
    is caught by ``update_spend`` returning False. That path already returned
    402; pinning it here keeps the two branches from being "fixed" in
    opposite directions.

    Pricing is set explicitly: an unpriced model costs 0, ``update_spend``
    then succeeds, and the request returns a plain 200 — the assertion would
    fail for a reason that has nothing to do with the status code under test.
    """
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            pr = await c.put("/admin/pricing/gpt-4o",
                             json={"input_per_1m": 1.0, "output_per_1m": 1.0},
                             headers=AUTH)
            assert pr.status_code == 200, pr.text
            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="tiny", max_budget=1e-9)
            vk = {"Authorization": f"Bearer {plain}"}
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                json=OPENAI_BODY)

            r = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert r.status_code == 402, (
                f"the post-hoc budget refusal must stay 402, got "
                f"{r.status_code}: {r.text[:200]}"
            )
            assert r.json()["error"]["type"] == "budget_exceeded"


@respx.mock
async def test_rate_limit_is_still_429():
    """Control: a genuine rate limit must keep its own 429.

    The fix changes the *budget* status only. If it were implemented by
    relaxing the rate-limit branch, callers would lose the one signal that
    tells them to retry after ``Retry-After``.
    """
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="throttled", rpm=1)
            vk = {"Authorization": f"Bearer {plain}"}
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                json=OPENAI_BODY)

            first = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert first.status_code == 200, first.text
            second = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert second.status_code == 429, (
                f"a genuine rate limit must stay 429, got {second.status_code}"
            )
            assert second.json()["error"]["type"] == "rate_limit_error"
            assert "Retry-After" in second.headers
