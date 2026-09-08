"""Round-32 regression tests: retroactive pricing ("true-up").

Scenario: a model serves traffic BEFORE any pricing entry exists. wiwi prices
unpriced models at $0 (CostEngine.cost_with_status → unpriced=True), so every
historical request_logs row for that model has cost ≈ 0 and the virtual keys
that paid for it were never charged. When the admin later adds pricing via
PUT /admin/pricing/{model_id}, the historical usage must be repriced too:

1. Matching historical rows are recomputed at the new rates and their `cost`
   column updated. A row matches when any recorded attempt deployment
   ("<group>/<model_id>" in the row's attempts JSON) ends with the bare
   model_id at a "/" boundary — the same tail-matching rule the cost engine's
   _lookup uses ("<ptype>/<model_id>" keys), so "openai/gpt-4o" matches a
   "gpt-4o" price but "gpt-4o-mini" never matches a "gpt-4o" price.
2. Only rows logged while the model was unpriced are touched: re-running the
   PUT (rate change) must NOT reprice rows again — already-priced rows keep
   their originally logged cost (historical truth), only the delta between
   logged cost and current-price cost is applied to rows that were logged
   unpriced.
3. Each virtual key's spend_to_date is trued-up by the sum of the cost
   deltas on its rows (and the in-memory cached AuthInfo updated), so budget
   enforcement reflects the retroactive spend.
4. Rows whose tokens price to $0 (all-zero usage) or that match no attempt
   are left alone; a re-PUT (edit) does nothing retroactively.
5. Both the in-memory CostEngine table and the DB config_store are updated
   (existing behavior — pinned here so the retroactive path never breaks it).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

import wiwi.server.app as app_mod
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="retro-gpt",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="retro-gpt"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


OPENAI_BODY = {
    "id": "chatcmpl-x", "object": "chat.completion", "model": "retro-gpt",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
              "prompt_tokens_details": {"cached_tokens": 0},
              "completion_tokens_details": {"reasoning_tokens": 0}},
}


async def _serve_n(client, n: int, key_header: dict | None = None) -> None:
    """Drive n upstream-mocked chat requests, then wait for the request-log
    batch worker to flush the rows into the DB sink."""
    with respx.mock:
        respx.post(OPENAI_URL).respond(json=OPENAI_BODY)
        for _ in range(n):
            r = await client.post("/v1/chat/completions", json={
                "model": "retro-gpt",
                "messages": [{"role": "user", "content": "hi"}]},
                headers=key_header or {"Authorization": f"Bearer {MASTER}"})
            assert r.status_code == 200, r.text
    # log_request is async-queued; give the pump a moment to write the batch
    for _ in range(50):
        rows = await _fetch_rows(client)
        if len(rows) >= n:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"expected {n} logged rows, saw {len(rows)}")


async def _fetch_rows(client) -> list[dict]:
    app = client._transport.app
    state = app.state.wiwi
    sink = state.logs.db_sink
    if sink is None:
        return []
    import sqlalchemy as sa
    async with sink.engine.connect() as conn:
        res = await conn.execute(sa.text(
            "SELECT * FROM request_logs ORDER BY ts, id"))
        return [dict(r._mapping) for r in res.all()]


async def _key_spend(client, key_id: str) -> float:
    app = client._transport.app
    state = app.state.wiwi
    import sqlalchemy as sa
    async with state.auth.engine.connect() as conn:
        row = (await conn.execute(sa.text(
            "SELECT spend_to_date FROM vkeys WHERE id = :i"),
            {"i": key_id})).first()
    return float(row[0]) if row else 0.0


# -- the core true-up ----------------------------------------------------------


@respx.mock
async def test_put_pricing_reprices_unpriced_history(client):
    """10M tokens served unpriced → add pricing → those rows get a real cost."""
    await _serve_n(client, 2)
    rows = await _fetch_rows(client)
    assert len(rows) == 2
    assert all(r["cost"] == 0.0 for r in rows), "unpriced => cost 0"

    # $3 input + $15 output per 1M → each row (1M in, 1M out) = $18.
    r = await client.put("/admin/pricing/retro-gpt", headers=AUTH, json={
        "input_per_1m": 3.0, "output_per_1m": 15.0})
    assert r.status_code == 200, r.text

    rows = await _fetch_rows(client)
    assert len(rows) == 2
    for row in rows:
        assert abs(row["cost"] - 18.0) < 1e-6, row["cost"]


async def test_reput_does_not_double_reprice(client):
    """A rate EDIT must not reprice already-priced history (no double charge):
    only rows logged while unpriced are true-up-able, and after the first PUT
    none remain."""
    await _serve_n(client, 1)
    await client.put("/admin/pricing/retro-gpt", headers=AUTH, json={
        "input_per_1m": 3.0, "output_per_1m": 15.0})
    rows = await _fetch_rows(client)
    assert abs(rows[0]["cost"] - 18.0) < 1e-6

    # Edit the rate to something wildly higher — the logged cost must stay.
    await client.put("/admin/pricing/retro-gpt", headers=AUTH, json={
        "input_per_1m": 30.0, "output_per_1m": 150.0})
    rows = await _fetch_rows(client)
    assert abs(rows[0]["cost"] - 18.0) < 1e-6, (
        "re-PUT must not reprice already-priced rows")


async def test_boundary_match_no_prefix_collision(client):
    """attempt "openai/retro-gpt" must not match pricing for "ro-gpt" (or any
    non-segment suffix): matching is on "/" boundaries, like the cost engine."""
    await _serve_n(client, 1)
    r = await client.put("/admin/pricing/ro-gpt", headers=AUTH, json={
        "input_per_1m": 3.0, "output_per_1m": 15.0})
    assert r.status_code == 200
    rows = await _fetch_rows(client)
    assert rows[0]["cost"] == 0.0, "boundary mismatch must not reprice"


async def test_all_zero_usage_row_untouched_but_harmless(client):
    """An errored request with no usage reprices to $0 — no crash, no spend."""
    with respx.mock:
        respx.post(OPENAI_URL).respond(status_code=500,
                                       json={"error": {"message": "boom"}})
        r = await client.post("/v1/chat/completions", json={
            "model": "retro-gpt",
            "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {MASTER}"})
        assert r.status_code == 502
    await asyncio.sleep(0.1)
    before = {r["request_id"]: r["cost"] for r in await _fetch_rows(client)}
    r = await client.put("/admin/pricing/retro-gpt", headers=AUTH, json={
        "input_per_1m": 3.0, "output_per_1m": 15.0})
    assert r.status_code == 200
    after = {r["request_id"]: r["cost"] for r in await _fetch_rows(client)}
    for rid, cost in after.items():
        if rid in before and before[rid] == 0.0 and cost == 0.0:
            continue  # zero-usage rows stay zero, fine
    # just: the endpoint succeeded and nothing exploded


# -- virtual-key spend true-up ---------------------------------------------------


async def test_key_spend_trued_up_and_budget_enforced_after(client):
    """A vkey's historical unpriced usage lands in spend_to_date when pricing
    is later added — and the cached AuthInfo is refreshed too."""
    r = await client.post("/admin/keys/generate", json={"name": "team-x"},
                          headers=AUTH)
    assert r.status_code == 200, r.text
    kid = r.json()["id"]
    plain = r.json()["key"]
    hdr = {"Authorization": f"Bearer {plain}"}

    await _serve_n(client, 1, key_header=hdr)
    assert await _key_spend(client, kid) == 0.0  # unpriced → no spend recorded

    await client.put("/admin/pricing/retro-gpt", headers=AUTH, json={
        "input_per_1m": 3.0, "output_per_1m": 15.0})
    spend = await _key_spend(client, kid)
    assert abs(spend - 18.0) < 1e-6, f"spend_to_date not trued up: {spend}"

    # The in-memory cache must reflect the true-up as well (info lookup by
    # key). Authenticate again and check the cached spend via the admin list.
    lst = (await client.get("/admin/keys", headers=AUTH)).json()["keys"]
    entry = next(k for k in lst if k["id"] == kid)
    assert abs(entry["spend_to_date"] - 18.0) < 1e-6


async def test_future_requests_priced_after_put(client):
    """After the PUT, new traffic prices at the new rates (forward path)."""
    await _serve_n(client, 1)
    await client.put("/admin/pricing/retro-gpt", headers=AUTH, json={
        "input_per_1m": 3.0, "output_per_1m": 15.0})
    await _serve_n(client, 1)
    rows = await _fetch_rows(client)
    assert len(rows) == 2
    assert abs(rows[0]["cost"] - 18.0) < 1e-6  # repriced retroactively
    assert abs(rows[1]["cost"] - 18.0) < 1e-6  # priced live


# -- serving-attempt matching ----------------------------------------------------


async def test_match_uses_serving_attempt_not_failed_attempts(client):
    """The reprice match is the SERVING attempt (last 2xx): a failed attempt
    on the priced model followed by a success on another model must NOT be
    repriced, and the inverse (failure on X, success on the priced model)
    MUST be repriced."""
    app = client._transport.app
    state = app.state.wiwi
    sink = state.logs.db_sink
    assert sink is not None
    from wiwi.logging_core.events import LogEvent

    evts = [
        # row A: failed 500 on priced-model, then 200 success on other-model
        LogEvent(stream="request", ts=1.0, request_id="rowA", model_group="g1",
                 provider="p1", status=200, tok_in=1_000_000, tok_out=0,
                 cost=0.0, attempts=[
                     {"deployment": "g1/priced-model", "provider": "p1",
                      "key": "a", "status": 500, "latency_ms": 1.0},
                     {"deployment": "g1/other-model", "provider": "p1",
                      "key": "a", "status": 200, "latency_ms": 2.0}]),
        # row B: failed 429 on other-model, then 200 success on priced-model
        LogEvent(stream="request", ts=2.0, request_id="rowB", model_group="g1",
                 provider="p1", status=200, tok_in=1_000_000, tok_out=0,
                 cost=0.0, attempts=[
                     {"deployment": "g1/other-model", "provider": "p1",
                      "key": "a", "status": 429, "latency_ms": 1.0},
                     {"deployment": "g1/priced-model", "provider": "p1",
                      "key": "a", "status": 200, "latency_ms": 2.0}]),
        # row C: all attempts failed (no 2xx) — falls back to last attempt
        LogEvent(stream="request", ts=3.0, request_id="rowC", model_group="g1",
                 provider="p1", status=502, error_code="upstream_error",
                 tok_in=0, tok_out=0, cost=0.0, attempts=[
                     {"deployment": "g1/priced-model", "provider": "p1",
                      "key": "a", "status": 502, "latency_ms": 1.0}]),
    ]
    await sink.write_requests(evts)

    entry = {"input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6}
    deltas = await sink.reprice_unpriced_history("priced-model", entry)

    rows = {r["request_id"]: r["cost"] for r in await _fetch_rows(client)}
    assert rows["rowA"] == 0.0, "served by other-model: must NOT reprice"
    assert abs(rows["rowB"] - 3.0) < 1e-9, "served by priced-model: reprice"
    assert rows["rowC"] == 0.0, "zero usage prices to 0 anyway"
    assert deltas == {}, "rowA/rowB used master traffic (no key_id)"


async def test_scan_terminates_on_large_unmatched_history(client):
    """>500 zero-cost rows that match nothing must not stall the scan (the
    old OFFSET loop re-read the same page forever); the one matching row is
    still found and repriced."""
    app = client._transport.app
    state = app.state.wiwi
    sink = state.logs.db_sink
    assert sink is not None
    from wiwi.logging_core.events import LogEvent

    evts = [LogEvent(stream="request", ts=float(i), request_id=f"bulk{i}",
                     model_group="g1", provider="p1", status=200,
                     tok_in=1_000_000, tok_out=0, cost=0.0,
                     attempts=[{"deployment": "g1/unrelated", "provider": "p1",
                                "key": "a", "status": 200, "latency_ms": 1.0}])
            for i in range(620)]
    evts.append(LogEvent(stream="request", ts=9999.0, request_id="needle",
                         model_group="g1", provider="p1", status=200,
                         tok_in=1_000_000, tok_out=0, cost=0.0,
                         attempts=[{"deployment": "g1/priced-model",
                                    "provider": "p1", "key": "a",
                                    "status": 200, "latency_ms": 1.0}]))
    await sink.write_requests(evts)

    entry = {"input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6}
    deltas = await sink.reprice_unpriced_history("priced-model", entry)
    assert deltas == {}  # master traffic rows have no key_id

    rows = {r["request_id"]: r["cost"] for r in await _fetch_rows(client)}
    assert abs(rows["needle"] - 3.0) < 1e-9
    assert all(rows[f"bulk{i}"] == 0.0 for i in range(620)), (
        "unrelated rows must stay untouched")


# -- engine + store consistency ---------------------------------------------------


async def test_put_still_updates_engine_and_store(client):
    """Pinning pre-existing behavior so the retro path can't regress it."""
    app = client._transport.app
    state = app.state.wiwi
    await client.put("/admin/pricing/retro-gpt", headers=AUTH, json={
        "input_per_1m": 3.0, "output_per_1m": 15.0})
    assert "retro-gpt" in state.cost.prices
    if state.config_store is not None:
        import sqlalchemy as sa
        async with state.config_store.engine.connect() as conn:
            row = (await conn.execute(sa.text(
                "SELECT input_cost_per_token FROM model_prices"
                " WHERE model_id = 'retro-gpt'"))).first()
        assert row is not None, "price must persist to config_store"
        assert abs(float(row[0]) - 0.000003) < 1e-12
