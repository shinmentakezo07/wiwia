"""Round 65 — retroactive repricing must key on the model, not a truncated suffix.

The reprice true-up identifies a row's model from the serving attempt's
``"<group>/<model_id>"`` deployment string. Two separate truncations collapse
distinct models onto one key:

1. ``DBSink.rollup_and_prune`` (db_sink.py:421) stores
   ``dep.split("/")[-1]`` as ``serving_model``.
2. ``PUT /admin/pricing/{model_id}`` (app.py:3382) passes
   ``model_id.split("/")[-1]`` as the match tail.

Both are lossy for any model id that itself contains a slash — which is the
normal OpenRouter / gateway form, and what the shipped ``wiwi.yaml`` uses
(``model: stealth/ox-alpha``). ``stealth/ox-alpha`` and ``vendor/ox-alpha``
then both reduce to ``ox-alpha``, so pricing one model reprices the other's
history at the wrong rate and charges its virtual key.

These tests pin the collision on BOTH storage paths.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx
import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

import wiwi.server.app as app_mod
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent

# Two models whose *distinct* ids share a last path segment. Both are real
# shapes: the first is the shipped wiwi.yaml model, the second a plain
# vendor-prefixed id on another group.
MODEL_A_GROUP, MODEL_A_ID = "stealth/ox-alpha", "stealth/ox-alpha"
MODEL_B_GROUP, MODEL_B_ID = "vendor-x", "vendor/ox-alpha"

RATE_B = {"input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6}


def _unpriced_row(tag: str, group: str, model_id: str,
                  record_model_id: bool = True) -> LogEvent:
    """A cost-0 row served by ``group/model_id`` (the pre-price state).

    ``record_model_id=False`` omits the ``model_id`` field, reproducing a row
    logged before that field existed — the legacy shape the fallback covers.
    """
    attempt = {"deployment": f"{group}/{model_id}", "provider": "openrouter",
               "provider_key_label": "k", "status": "ok", "latency_ms": 1}
    if record_model_id:
        attempt["model_id"] = model_id
    return LogEvent(
        stream="request", ts=time.time(), status=200, key_id=f"key-{tag}",
        model_group=group, provider="openrouter",
        tok_in=1_000_000, tok_out=0, cost=0.0,
        attempts=[attempt],
    )


@pytest.fixture
async def sink():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    s = DBSink(engine)
    await s.startup()
    yield s
    await engine.dispose()


async def _write_collision_pair(sink: DBSink) -> None:
    """Model A and model B, both unpriced, both sharing the tail ``ox-alpha``."""
    await sink.write_requests([
        _unpriced_row("A", MODEL_A_GROUP, MODEL_A_ID),
        _unpriced_row("B", MODEL_B_GROUP, MODEL_B_ID),
    ])


def _rate_for(_provider: str) -> dict:
    return RATE_B


# -- storage path 1: the rollup ------------------------------------------------


async def test_rollup_preserves_slash_bearing_serving_model(sink):
    """``serving_model`` must be the whole model id, not its last segment.

    RED: ``dep.split("/")[-1]`` reduces ``stealth/ox-alpha/stealth/ox-alpha``
    to ``ox-alpha``, so the rollup row loses which model actually served it and
    retroactive pricing can no longer find it by name.
    """
    await _write_collision_pair(sink)
    await sink.rollup_and_prune(cutoff_ts=time.time() + 3600)

    async with sink.engine.connect() as conn:
        stored = {r[0] for r in (await conn.execute(sa.text(
            "SELECT serving_model FROM request_rollups"))).all()}

    assert MODEL_A_ID in stored, (
        f"rollup stored {stored} — the slash-bearing model id "
        f"{MODEL_A_ID!r} was truncated to a bare tail")
    assert MODEL_B_ID in stored, (
        f"rollup stored {stored} — {MODEL_B_ID!r} was truncated")


async def test_rollup_reprice_does_not_charge_a_different_model(sink):
    """Pricing model B must not reprice model A's rolled-up history.

    RED: both rollup rows carried ``serving_model='ox-alpha'``, so the
    ``WHERE serving_model = :tail`` match in ``_reprice_rolled_up`` selected
    both and model A's key was charged at model B's rate.
    """
    await _write_collision_pair(sink)
    await sink.rollup_and_prune(cutoff_ts=time.time() + 3600)

    # The caller passes the FULL pricing key, as PUT /admin/pricing does.
    deltas = await sink.reprice_unpriced_history(MODEL_B_ID, _rate_for)

    assert "key-A" not in deltas, (
        "model A (stealth/ox-alpha) was never priced, but its key was charged "
        f"{deltas.get('key-A')} by the model-B reprice — models sharing a last "
        "path segment were conflated")
    assert deltas.get("key-B", 0) > 0, (
        "model B was priced, so its own history must be repriced")


async def test_rollup_reprice_survives_a_bare_tail_key(sink):
    """Pricing by a bare slash-tail must still match its full id.

    The cost engine's ``_lookup`` tries successive slash-tails, so a price
    registered as ``claude-sonnet-4`` applies to
    ``anthropic/claude-sonnet-4``. The reprice match must follow the same
    rule, or retroactive pricing silently stops working for the id shapes the
    pricing UI actually accepts.
    """
    await sink.write_requests([
        _unpriced_row("E", "vendor-x", "anthropic/claude-sonnet-4"),
    ])
    await sink.rollup_and_prune(cutoff_ts=time.time() + 3600)

    deltas = await sink.reprice_unpriced_history("claude-sonnet-4", _rate_for)

    assert deltas.get("key-E", 0) > 0, (
        "a price registered under the bare tail 'claude-sonnet-4' must still "
        "reprice history served by 'anthropic/claude-sonnet-4'")


async def test_legacy_rows_without_model_id_are_not_conflated(sink):
    """Rows logged before ``model_id`` existed must still be told apart.

    The deployment string alone is ambiguous, but the row's own ``model_group``
    gives the exact prefix to strip, so the legacy fallback recovers the full
    model id rather than the lossy last segment.
    """
    await sink.write_requests([
        _unpriced_row("A", MODEL_A_GROUP, MODEL_A_ID, record_model_id=False),
        _unpriced_row("B", MODEL_B_GROUP, MODEL_B_ID, record_model_id=False),
    ])

    deltas = await sink.reprice_unpriced_history(MODEL_B_ID, _rate_for)

    assert "key-A" not in deltas, (
        "legacy row for stealth/ox-alpha was charged by the vendor/ox-alpha "
        "reprice — the group-prefix fallback failed")
    assert deltas.get("key-B", 0) > 0


# -- storage path 2: the raw rows ---------------------------------------------


async def test_raw_reprice_does_not_charge_a_different_model(sink):
    """Same collision, before any rollup: the ``_serving_attempt`` suffix rule.

    ``_serving_attempt`` matched ``dep.endswith(f"/{match_tail}")``, so the tail
    ``ox-alpha`` matched both ``.../stealth/ox-alpha`` and ``.../vendor/ox-alpha``.
    """
    await _write_collision_pair(sink)

    deltas = await sink.reprice_unpriced_history(MODEL_B_ID, _rate_for)

    assert "key-A" not in deltas, (
        "model A was charged by the model-B reprice via the "
        "endswith('/ox-alpha') suffix rule")
    assert deltas.get("key-B", 0) > 0


# -- control: slash-free ids must keep working ---------------------------------


async def test_slash_free_model_ids_still_reprice(sink):
    """Control: the ordinary case (no slashes anywhere) must be unaffected."""
    await sink.write_requests([
        _unpriced_row("C", "gpt-4o", "gpt-4o"),
    ])
    deltas = await sink.reprice_unpriced_history("gpt-4o", _rate_for)
    assert deltas.get("key-C", 0) > 0, "plain model ids must still reprice"


async def test_boundary_match_still_rejects_a_shorter_tail(sink):
    """Control: ``gpt-4o-mini`` must never be matched by a ``gpt-4o`` reprice.

    This is the property the "/"-boundary rule exists to protect; the fix for
    the collision above must not regress it.
    """
    await sink.write_requests([
        _unpriced_row("D", "gpt-4o-mini", "gpt-4o-mini"),
    ])
    deltas = await sink.reprice_unpriced_history("gpt-4o", _rate_for)
    assert "key-D" not in deltas, (
        "gpt-4o-mini was repriced by a gpt-4o reprice — suffix boundary broken")


# -- end-to-end through the real admin pricing route ---------------------------

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _e2e_config() -> WiwiConfig:
    """Two models sharing the last path segment ``ox-alpha``, one provider."""
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[
            ModelEntry(model_name="stealth/ox-alpha",
                       wiwi_params=DeploymentParams(provider="p1",
                                                    model="stealth/ox-alpha")),
            ModelEntry(model_name="vendor/ox-alpha",
                       wiwi_params=DeploymentParams(provider="p1",
                                                    model="vendor/ox-alpha")),
        ],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def e2e_client():
    app = app_mod.create_app(_e2e_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


def _completion_body(model: str) -> dict:
    return {
        "id": "chatcmpl-x", "object": "chat.completion", "model": model,
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": "hi"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0,
                  "prompt_tokens_details": {"cached_tokens": 0},
                  "completion_tokens_details": {"reasoning_tokens": 0}},
    }


async def _rows(client) -> list[dict]:
    state = client._transport.app.state.wiwi
    async with state.logs.db_sink.engine.connect() as conn:
        res = await conn.execute(sa.text(
            "SELECT model_group, cost FROM request_logs ORDER BY id"))
        return [dict(r._mapping) for r in res.all()]


async def _serve(client, model: str, n: int = 1) -> None:
    with respx.mock:
        respx.post(OPENAI_URL).respond(json=_completion_body(model))
        for _ in range(n):
            r = await client.post("/v1/chat/completions",
                                  json={"model": model,
                                        "messages": [{"role": "user",
                                                      "content": "hi"}]},
                                  headers=AUTH)
            assert r.status_code == 200, r.text
    for _ in range(60):
        if len(await _rows(client)) >= n:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("request log did not flush")


async def test_put_pricing_does_not_reprice_a_sibling_model(e2e_client):
    """Pricing ``vendor/ox-alpha`` must not bill ``stealth/ox-alpha`` traffic.

    RED at the route level: ``PUT /admin/pricing/{model_id}`` truncated the key
    to its last segment before matching, so pricing ``vendor/ox-alpha`` also
    repriced every ``stealth/ox-alpha`` row at the wrong rate.
    """
    await _serve(e2e_client, "stealth/ox-alpha")
    await _serve(e2e_client, "vendor/ox-alpha")

    r = await e2e_client.put("/admin/pricing/vendor/ox-alpha",
                             json={"input_per_1m": 3.0, "output_per_1m": 15.0},
                             headers=AUTH)
    assert r.status_code == 200, r.text

    costs = {row["model_group"]: row["cost"] for row in await _rows(e2e_client)}
    assert costs["vendor/ox-alpha"] > 0, (
        "the priced model's own history must be repriced")
    assert costs["stealth/ox-alpha"] == 0.0, (
        f"stealth/ox-alpha was never priced but was billed "
        f"{costs['stealth/ox-alpha']} by the vendor/ox-alpha reprice")


async def test_put_pricing_reprices_its_own_slash_bearing_model(e2e_client):
    """The priced model's own history IS repriced (control for the above).

    Guards against "fixing" the collision by disabling retroactive pricing for
    slash-bearing ids entirely.
    """
    await _serve(e2e_client, "stealth/ox-alpha")

    r = await e2e_client.put("/admin/pricing/stealth/ox-alpha",
                             json={"input_per_1m": 3.0, "output_per_1m": 15.0},
                             headers=AUTH)
    assert r.status_code == 200, r.text

    costs = {row["model_group"]: row["cost"] for row in await _rows(e2e_client)}
    assert costs["stealth/ox-alpha"] == pytest.approx(3.0), (
        "a slash-bearing model's own history must still be repriced")
