"""Round 72: the Messages route authenticated with the wrong header scheme.

Zen exposes three protocol surfaces under one base URL, and they do **not**
share an auth scheme:

- ``/chat/completions`` and ``/responses`` are OpenAI-wire and read
  ``Authorization: Bearer <key>``.
- ``/messages`` is Anthropic-wire and reads ``x-api-key: <key>``.

``OpencodeAdapter`` overrode ``headers()`` and hand-built one header dict for
every route, always emitting ``Authorization: Bearer``. On the Messages route
that token is invisible to Zen's Anthropic-wire front end, which answers
``401 {"type":"error","error":{"type":"AuthError","message":"Missing API
key."}}`` — "Missing", not "Invalid": the credential never arrived.

Verified live 2026-09-17 against ``https://opencode.ai/zen/v1`` with a real
``sk-…`` Zen key, model ``claude-sonnet-5``:

| headers on ``/messages``      | result                                   |
|-------------------------------|------------------------------------------|
| ``Authorization: Bearer``     | 401 ``AuthError`` "Missing API key."     |
| ``x-api-key``                 | 401 ``CreditsError`` "No payment method" |
| ``x-api-key`` + ``Bearer``    | 401 ``CreditsError`` "No payment method" |

``CreditsError`` is the *next* gate, past authentication: it proves the
request cleared auth and reached billing. ``AuthError`` means it never did.

Two further findings from the same probe matrix, both pinned below because
they bound what a fix can achieve:

- The account is on an **unbilled workspace** — all six ``io`` keys return
  ``CreditsError`` on a paid model — so paid models stay unreachable until a
  payment method is added. That is an account condition, not a code one.
- Free models are refused with ``403 FreeTierError`` on every route and every
  live key (9/9 free models probed), so the free tier is gated account-wide
  and is likewise not recoverable in code.

The bug is therefore a latent correctness defect rather than the cause of the
current free-tier 403s: it is invisible until the account is funded, at which
point every Messages-route model (``union-alpha``, ``claude-*``, ``qwen*``)
would fail with a misleading "Missing API key." while Chat and Responses
worked. Pinned now so it does not have to be rediscovered then.

Note the earlier rounds missed this because every Messages-route test in the
suite configured ``key="anonymous"`` (``tests/test_fix_round63.py``), which
correctly omits credentials altogether — so the wrong-scheme branch was never
exercised with a real key.
"""

from __future__ import annotations

import time

import httpx
import pytest_asyncio
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
from wiwi.ir import types as ir
from wiwi.providers import opencode_version as ov
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.opencode_adapter import OpencodeAdapter, route_for_model
from wiwi.server.app import create_app
from wiwi.wire import openai_chat as oc

ZEN = "https://opencode.ai/zen/v1"


def _key(secret: str = "sk-zen-real-abc") -> ProviderKeyRef:
    return ProviderKeyRef(label="main", secret=secret)


def _req() -> ir.Request:
    return oc.decode_request(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]})


def _headers_for(model: str, secret: str = "sk-zen-real-abc") -> dict[str, str]:
    """Headers the adapter would send for ``model``, via the hot-path order."""
    a = OpencodeAdapter()
    a.encode_request(_req(), model, {})
    a.build_url(ZEN, model, False)  # sets _last_route, as the gateway does
    return a.headers(_key(secret))


def setup_function(_func) -> None:
    ov._set_cached_for_tests("1.18.31", time.monotonic())


def teardown_function(_func) -> None:
    ov._set_cached_for_tests(None, 0.0)


# -- unit: the auth scheme follows the route, not the adapter -------------------


def test_messages_route_uses_x_api_key_not_bearer():
    # Pre-fix: {"Authorization": "Bearer …"} -> Zen 401 AuthError
    # "Missing API key." Post-fix: x-api-key reaches the auth check.
    h = _headers_for("union-alpha")
    assert h["x-api-key"] == "sk-zen-real-abc"
    assert "Authorization" not in h


def test_paid_messages_model_uses_x_api_key():
    # claude-* routes to Messages too; the scheme must not be model-specific.
    h = _headers_for("claude-sonnet-5")
    assert h["x-api-key"] == "sk-zen-real-abc"
    assert "Authorization" not in h


def test_openai_wire_routes_keep_bearer_and_never_send_x_api_key():
    # Chat and Responses are OpenAI-wire: Bearer is correct there, and
    # leaking x-api-key onto them would be a second wrong-scheme bug.
    for model in ["mimo-v2.5-free", "big-pickle", "muse-spark-1.3-contributor-free"]:
        # Self-checking control: a prefix-table change that moved a case to
        # another route would otherwise leave this test passing vacuously.
        assert route_for_model(model) in ("chat", "responses"), model
        h = _headers_for(model)
        assert h["Authorization"] == "Bearer sk-zen-real-abc", model
        assert "x-api-key" not in h, model


def test_gemini_route_does_not_send_x_api_key():
    # The Messages fix must not bleed onto Gemini. (Gemini's *own* scheme was
    # wrong too and is fixed in round 73 — it reads x-goog-api-key, not the
    # querystring this comment used to claim.)
    h = _headers_for("gemini-3-flash")
    assert "x-api-key" not in h


def test_anonymous_sentinel_still_omits_both_schemes():
    # The sentinel means "send no credential at all", on every route. This is
    # what tests/test_fix_round63.py asserts for union-alpha, and it must keep
    # holding: the sentinel is not a key, so it cannot authenticate either way.
    for model in ["union-alpha", "mimo-v2.5-free"]:
        h = _headers_for(model, "anonymous")
        assert "Authorization" not in h, model
        assert "x-api-key" not in h, model


def test_messages_route_still_carries_the_client_fingerprint():
    # The route-scoped auth change must not disturb the round-29 fingerprint
    # or the round-71 anthropic-version scoping.
    h = _headers_for("union-alpha")
    assert h["anthropic-version"] == "2023-06-01"
    assert h["x-opencode-session"].startswith("ses_")
    assert h["x-opencode-request"].startswith("msg_")
    assert h["x-opencode-client"] == "cli"
    assert h["x-opencode-project"] == "global"
    assert h["User-Agent"] == "opencode/1.18.31"


# -- end-to-end: the real gateway hot path -------------------------------------


def _zen_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="zen", provider="opencode", base_url=ZEN,
                               keys=[KeyDef(label="main", key="sk-zen-real-abc")])],
        model_list=[
            ModelEntry(model_name="zen-messages",
                       wiwi_params=DeploymentParams(provider="zen",
                                                    model="union-alpha")),
            ModelEntry(model_name="zen-chat",
                       wiwi_params=DeploymentParams(provider="zen",
                                                    model="mimo-v2.5-free")),
        ],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0),
    )


@pytest_asyncio.fixture
async def zen_client(monkeypatch):
    monkeypatch.setattr(ov, "_cached_version", "1.18.31")
    monkeypatch.setattr(ov, "_fetched_at", time.monotonic())
    app = create_app(_zen_config())
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer sk-wiwi-master-test"},
    ) as client:
        yield client


_MESSAGES_RESPONSE = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "union-alpha",
    "content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn",
    "usage": {"input_tokens": 3, "output_tokens": 2},
}

_CHAT_RESPONSE = {
    "id": "chatcmpl-x", "object": "chat.completion", "model": "m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}


@respx.mock
async def test_gateway_messages_route_sends_x_api_key(zen_client):
    route = respx.post(f"{ZEN}/messages").respond(json=_MESSAGES_RESPONSE)
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "zen-messages", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert route.called
    sent = route.calls[0].request.headers
    assert sent.get("x-api-key") == "sk-zen-real-abc"
    assert "authorization" not in sent


@respx.mock
async def test_gateway_chat_route_keeps_bearer(zen_client):
    route = respx.post(f"{ZEN}/chat/completions").respond(json=_CHAT_RESPONSE)
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "zen-chat", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert route.called
    sent = route.calls[0].request.headers
    assert sent.get("authorization") == "Bearer sk-zen-real-abc"
    assert "x-api-key" not in sent
