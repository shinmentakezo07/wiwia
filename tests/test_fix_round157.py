"""Regression tests for AUDIT #476 — an upstream 529 reached the caller as an
opaque ``api_error``, so Claude Code could not tell an overloaded upstream from
a broken one and did not back off.

Two defects, one symptom:

1. ``providers/base.py:_classify_provider_status`` folded 529 into the same arm
   as 500/502/503 and returned ``WiwiError(502, "api_connection_error", ...)``,
   discarding both the status and the vendor's own type. The type survived on
   ``err.upstream_type``, which nothing on the error path reads.
2. ``wire/anthropic_messages.py:error_body`` ignored its ``status`` argument
   entirely, mapping only through a static ``etype`` table — so even a
   correctly-classified 529 could not reach the client as ``overloaded_error``.

The streaming twin (``_stream_error_type``) already got this right, which is
what made the pre-first-byte path the anomalous one.
"""

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
    WiwiConfig,
)
from wiwi.providers.base import error_from_provider_status
from wiwi.server.app import create_app
from wiwi.wire import anthropic_messages as am

HDRS = {
    "x-api-key": "sk-wiwi-master-test",
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="anthropic",
                               base_url="https://api.anthropic.com",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(
            model_name="claude-sonnet-4-5",
            wiwi_params=DeploymentParams(provider="p1", model="claude-sonnet-4-5"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
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


# -- layer 1: the classifier must not destroy the 529 -------------------------


def test_a_529_keeps_its_status_and_becomes_overloaded():
    """529 is Anthropic's own overload signal, not a generic 5xx.

    Folding it into ``(500, 502, 503)`` is what made the client-visible type
    unrecoverable: by the time the error reached a dialect encoder, both the
    status and the vendor type were gone.
    """
    err = error_from_provider_status(
        529, '{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
        "anthropic")
    assert err.status == 529, f"529 was rewritten to {err.status}"
    assert err.etype == "overloaded_error", f"529 typed as {err.etype!r}"
    assert err.retryable, "a saturated upstream must stay retryable/failover-able"


def test_the_sibling_5xx_statuses_are_untouched():
    """Only 529 changes; 500/502/503 keep their existing classification."""
    for status in (500, 502, 503):
        err = error_from_provider_status(status, "boom", "p1")
        assert err.status == 502
        assert err.etype == "api_connection_error"


def test_a_529_still_reports_to_the_key_pool():
    """Preserving the status must not stop the deployment cooling.

    ``status_for_key_pool`` keys off membership in ``RETRYABLE_STATUS``, which
    already contains 529 — so an overloaded upstream keeps feeding deployment
    cooldowns exactly as it did when it arrived rewritten as 502.
    """
    from wiwi.providers.base import status_for_key_pool
    err = error_from_provider_status(529, "Overloaded", "anthropic")
    assert status_for_key_pool(err) == 529


# -- layer 2: error_body must consult status, not just the etype table --------


def test_error_body_maps_529_to_overloaded_error():
    """The status argument is part of the signature for a reason.

    ``error_body`` accepted ``status`` and never read it, so a caller that
    classified correctly still could not render the right type.
    """
    assert am.error_body(529, "overloaded_error", "Overloaded")["error"]["type"] \
        == "overloaded_error"
    # And from the status alone, for an etype the table has never heard of.
    assert am.error_body(529, "something_new", "Overloaded")["error"]["type"] \
        == "overloaded_error"


def test_error_body_still_honours_the_etype_table():
    """Widening the status arm must not override an explicit etype."""
    assert am.error_body(400, "invalid_request_error", "bad")["error"]["type"] \
        == "invalid_request_error"
    assert am.error_body(401, "authentication_error", "bad key")["error"]["type"] \
        == "authentication_error"
    assert am.error_body(429, "rate_limit_error", "slow down")["error"]["type"] \
        == "rate_limit_error"


def test_vendor_native_error_types_are_not_flattened():
    """Anthropic's own vocabulary is valid in a response body.

    Claude Code switches on these strings, so collapsing them to ``api_error``
    removes the only signal it can act on.
    """
    for etype in ("billing_error", "invalid_tool_use", "request_too_large"):
        got = am.error_body(400, etype, "m")["error"]["type"]
        assert got == etype, f"{etype} was flattened to {got!r}"


# -- end to end: what the client actually receives -----------------------------


@respx.mock
async def test_a_529_reaches_the_client_as_overloaded(client):
    respx.post("https://api.anthropic.com/messages").respond(
        status_code=529,
        json={"type": "error",
              "error": {"type": "overloaded_error", "message": "Overloaded"}})
    r = await client.post("/v1/messages", headers=HDRS, json={
        "model": "claude-sonnet-4-5", "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}]})
    body = r.json()
    assert body["error"]["type"] == "overloaded_error", (
        f"an overloaded upstream reached the caller as "
        f"{body['error']['type']!r} — Claude Code does not back off from that")


@respx.mock
async def test_a_429_still_reaches_the_client_as_rate_limit(client):
    """The regression guard for the arm that already worked."""
    respx.post("https://api.anthropic.com/messages").respond(
        status_code=429,
        json={"type": "error",
              "error": {"type": "rate_limit_error", "message": "slow down"}})
    r = await client.post("/v1/messages", headers=HDRS, json={
        "model": "claude-sonnet-4-5", "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}]})
    assert r.json()["error"]["type"] == "rate_limit_error"


@respx.mock
async def test_the_streaming_twin_agrees_with_the_http_path(client):
    """Both surfaces must classify the same upstream failure identically.

    ``_stream_error_type`` already produced ``overloaded_error``; the HTTP path
    is what was wrong. This pins the two together so they cannot drift again.
    """
    respx.post("https://api.anthropic.com/messages").respond(
        status_code=529,
        json={"type": "error",
              "error": {"type": "overloaded_error", "message": "Overloaded"}})
    async with client.stream("POST", "/v1/messages", headers=HDRS, json={
            "model": "claude-sonnet-4-5", "max_tokens": 16, "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}) as r:
        raw = "".join([c async for c in r.aiter_text()])
    assert r.status_code == 529
    assert "overloaded_error" in raw, (
        f"the streaming surface lost the overload classification: {raw[:200]}")
