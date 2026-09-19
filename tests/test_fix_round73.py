"""Round 73: Zen's Gemini route authenticated with the wrong header scheme.

Round 72 fixed the Messages route (``x-api-key``). The same class of defect was
still live on a *fourth* route: Zen's Gemini front end reads ``x-goog-api-key``,
and ``OpencodeAdapter.headers()`` emitted ``Authorization: Bearer`` for it —
the one scheme Gemini's wire does not read.

The module docstring, the inline comment, and AUDIT #176 all claimed the Gemini
route "carries its key in the querystring". That claim is false and was load
bearing: ``wiwi/core/recovery.py:build_url`` appends a querystring key only when
``provider_type == "gemini"``, and this deployment's type is ``"opencode"``, so
no key was appended either. ``OpencodeAdapter.build_url`` returns a bare
``...:generateContent`` with no ``?key=`` placeholder, so the
``url.endswith(("?key=", "&key="))`` branch never fires. The route therefore
sent a credential that upstream could not read.

Verified live 2026-09-17 against ``https://opencode.ai/zen/v1`` with a real
``sk-…`` Zen key, model ``gemini-3-flash``, ``POST /models/gemini-3-flash:generateContent``:

| credential                       | result                                   |
|----------------------------------|------------------------------------------|
| ``Authorization: Bearer``        | 401 ``AuthError`` "Missing API key."     |
| ``x-goog-api-key``               | 401 ``CreditsError`` "No payment method" |
| ``?key=`` querystring            | 401 ``AuthError`` "Missing API key."     |
| none                             | 401 ``AuthError`` "Missing API key."     |

``CreditsError`` is the *next* gate, past authentication: it proves
``x-goog-api-key`` cleared auth while Bearer, the querystring, and no-credential
all failed identically. "Missing", not "Invalid" — the credential never arrived.
This is the same error signature that identified the Messages-route bug, so the
method is the same one that proved it.

The route is live, not dead code: ``GET /models`` lists seven Gemini models
(``gemini-3.6-flash``, ``gemini-3.8-flash``, ``gemini-3.7-flash``,
``gemini-3.5-flash-lite``, ``gemini-3.5-flash``, ``gemini-3.1-pro``,
``gemini-3-flash``), so any deployment that routes one of them hits this.

Also pinned here: the streaming Messages path. Round 72's end-to-end coverage
used the non-streaming ``_call_once`` site, but the stream pump
(``wiwi/core/gateway.py``) builds headers at its own site with its own
401-refresh rebuild, and it is the path every Claude Code client takes. The
pre-existing streaming messages test (``tests/test_fix_round63.py``) configures
``key="anonymous"``, so it could not have caught a scheme regression — the exact
blind spot that let the original bug ship.
"""

from __future__ import annotations

import time

import httpx
import orjson
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


def _req(stream: bool = False) -> ir.Request:
    return oc.decode_request(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}],
         "stream": stream})


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


# -- unit: the Gemini route reads x-goog-api-key -------------------------------


def test_gemini_route_uses_x_goog_api_key_not_bearer():
    # Pre-fix: {"Authorization": "Bearer …"} -> Zen 401 AuthError
    # "Missing API key." Post-fix: x-goog-api-key reaches the auth check.
    h = _headers_for("gemini-3-flash")
    assert h["x-goog-api-key"] == "sk-zen-real-abc"
    assert "Authorization" not in h


def test_gemini_route_never_sends_the_other_two_schemes():
    # x-api-key is the *Messages* scheme and Bearer is the OpenAI-wire scheme;
    # leaking either onto Gemini is the same defect in the other direction.
    for model in ["gemini-3-flash", "gemini-3.1-pro", "gemini-3.5-flash-lite"]:
        h = _headers_for(model)
        assert "x-api-key" not in h, model
        assert "Authorization" not in h, model
        assert route_for_model(model) == "gemini", model


def test_non_gemini_routes_never_send_x_goog_api_key():
    # The Gemini fix must not bleed onto the other three routes.
    for model in ["union-alpha", "claude-sonnet-5", "mimo-v2.5-free",
                  "muse-spark-1.3-contributor-free"]:
        h = _headers_for(model)
        assert "x-goog-api-key" not in h, model


def test_anonymous_sentinel_omits_x_goog_api_key_too():
    # The sentinel means "send no credential at all", on every route including
    # this one.
    h = _headers_for("gemini-3-flash", "anonymous")
    assert "Authorization" not in h
    assert "x-api-key" not in h
    assert "x-goog-api-key" not in h


def test_gemini_route_still_carries_the_client_fingerprint():
    # The route-scoped auth change must not disturb the round-29 fingerprint.
    h = _headers_for("gemini-3-flash")
    assert h["x-opencode-session"].startswith("ses_")
    assert h["x-opencode-client"] == "cli"
    assert h["User-Agent"] == "opencode/1.18.31"
    # anthropic-version names Anthropic's Messages endpoint: Gemini must not
    # carry it (round-71 scoping).
    assert "anthropic-version" not in h


# -- the diagnostic log line ---------------------------------------------------


def test_credential_scheme_is_logged_per_route():
    # A credential in the wrong scheme is silently not read upstream, and the
    # 401 is indistinguishable from sending nothing. The log line naming the
    # route + scheme is what makes that a one-line diagnosis, so pin it.
    import structlog

    for model, want_route, want_scheme in [
        ("gemini-3-flash", "gemini", "x-goog-api-key"),
        ("union-alpha", "messages", "x-api-key"),
        ("claude-sonnet-5", "messages", "x-api-key"),
        ("mimo-v2.5-free", "chat", "Authorization"),
        ("muse-spark-1.3-contributor-free", "responses", "Authorization"),
    ]:
        with structlog.testing.capture_logs() as logs:
            _headers_for(model)
        hits = [e for e in logs if e.get("event") == "opencode_credential_scheme"]
        assert len(hits) == 1, (model, logs)
        assert hits[0]["route"] == want_route, model
        assert hits[0]["scheme"] == want_scheme, model
        # The secret must never reach the log.
        assert "sk-zen-real-abc" not in orjson.dumps(logs).decode(), model


def test_anonymous_sentinel_logs_the_omission():
    # "Why is there no credential on this request" is the same diagnostic
    # question as "which scheme did we use", so the sentinel path logs too.
    import structlog

    with structlog.testing.capture_logs() as logs:
        _headers_for("gemini-3-flash", "anonymous")
    hits = [e for e in logs if e.get("event") == "opencode_credential_omitted"]
    assert len(hits) == 1, logs
    assert hits[0]["route"] == "gemini"
    assert hits[0]["reason"] == "anonymous_sentinel"


# -- end-to-end: the real gateway hot path -------------------------------------


def _zen_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="zen", provider="opencode", base_url=ZEN,
                               keys=[KeyDef(label="main", key="sk-zen-real-abc")])],
        model_list=[
            ModelEntry(model_name="zen-gemini",
                       wiwi_params=DeploymentParams(provider="zen",
                                                    model="gemini-3-flash")),
            ModelEntry(model_name="zen-messages",
                       wiwi_params=DeploymentParams(provider="zen",
                                                    model="union-alpha")),
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


# The transport declares force_stream, so a non-streaming client's Gemini call
# is issued as `:streamGenerateContent?alt=sse` (the body carries no `stream`
# field — the URL selects the wire) and the gateway reassembles the answer.
_GEMINI_SSE = b"".join(
    b"data: " + orjson.dumps(p) + b"\n\n" for p in [
        {"candidates": [{"content": {"parts": [{"text": "hello"}]}}],
         "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 0}},
        {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
         "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2}},
    ])


@respx.mock
async def test_gateway_gemini_route_sends_x_goog_api_key(zen_client):
    route = respx.post(
        f"{ZEN}/models/gemini-3-flash:streamGenerateContent?alt=sse").respond(
        content=_GEMINI_SSE, headers={"Content-Type": "text/event-stream"})
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "zen-gemini", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert route.called
    sent = route.calls[0].request.headers
    assert sent.get("x-goog-api-key") == "sk-zen-real-abc"
    assert "authorization" not in sent
    # The key must not ride the querystring either: recovery.build_url appends
    # one only for provider_type == "gemini", and this deployment is "opencode".
    assert "key=" not in str(route.calls[0].request.url)
    assert r.json()["choices"][0]["message"]["content"] == "hello"


# -- streaming messages path (the site round 72 left uncovered) ----------------


_MESSAGES_SSE = [
    {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "union-alpha", "content": [], "stop_reason": None,
        "usage": {"input_tokens": 5, "output_tokens": 0}}},
    {"type": "content_block_start", "index": 0,
     "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "text_delta", "text": "hello"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
     "usage": {"output_tokens": 2}},
    {"type": "message_stop"},
]


@respx.mock
async def test_gateway_messages_stream_sends_x_api_key(zen_client):
    # The stream pump builds headers at its own site with its own 401-refresh
    # rebuild. Every Claude Code client takes this path, and the pre-existing
    # streaming messages test used key="anonymous", so this scheme was never
    # exercised on it.
    content = b"".join(
        b"event: " + e["type"].encode() + b"\ndata: " + orjson.dumps(e) + b"\n\n"
        for e in _MESSAGES_SSE)
    route = respx.post(f"{ZEN}/messages").respond(
        content=content, headers={"Content-Type": "text/event-stream"})
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "zen-messages", "messages": [{"role": "user", "content": "hi"}],
        "stream": True})
    assert r.status_code == 200, r.text
    assert route.called
    sent = route.calls[0].request.headers
    assert sent.get("x-api-key") == "sk-zen-real-abc"
    assert "authorization" not in sent
