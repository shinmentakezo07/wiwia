"""Round 112 regressions — AUDIT #320, #323, #324 (the three open criticals).

Each test was confirmed to fail against the code as it stood before the fix:

- #320: a journal whose owner line was lost with the dying process read as
  ``owner_of() is None`` after a restart, and the replay gate treated that as
  legacy-and-open — any key could replay any other key's response.
- #323: an unpriced model cost $0 forever, so a virtual key's ``max_budget``
  never tripped and nothing warned.
- #324: the budget cap was checked pre-flight (non-atomic read) and enforced
  only post-hoc, so N concurrent requests all passed headroom that covered
  one — measured 250x overspend at concurrency 500.
"""

from __future__ import annotations

import asyncio

import httpx
import orjson
import pytest
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
from wiwi.cost.pricing import CostEngine
from wiwi.server.app import create_app
from wiwi.streaming.tape_store import JournalStore

AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}

VICTIM = "0d76f7149cb048dc"  # a conforming 16-hex request id

OPENAI_BODY = {
    "id": "chatcmpl-c", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant",
                                         "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}


def _body() -> dict:
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def _sbody() -> dict:
    return {"model": "gpt-4o", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}


def _config(**gs) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:", **gs),
    )


# === #320: a lost owner write must not reopen the gate after restart ========


def _gate(store: JournalStore, request_id: str, caller_kid: str) -> bool:
    """The replay-gate predicate, verbatim from server/app.py."""
    jowner = store.owner_of(request_id)
    return (caller_kid == jowner
            or (jowner is None and not store.has_owner_intent(request_id))
            or (jowner == "master" and caller_kid == "master"))


@pytest.fixture
def store(tmp_path):
    return JournalStore(tmp_path / "journals", ttl_s=600.0, max_bytes=1_048_576)


def _simulate_restart(store: JournalStore) -> JournalStore:
    """A fresh store over the same directory, with its epoch AFTER the file
    was written — i.e. the old process died before this one started."""
    return JournalStore(store.dir, ttl_s=store.ttl_s, max_bytes=store.max_bytes)


STREAM_A = (
    'data: {"choices":[{"delta":{"role":"assistant","content":"ALICE-SECRET"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
    "data: [DONE]\n\n"
)
STREAM_B = (
    'data: {"choices":[{"delta":{"role":"assistant","content":"bob-fresh"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
    "data: [DONE]\n\n"
)


def _config_journaled(tmp_path) -> WiwiConfig:
    """_config with the journal dir inside tmp_path (defaults would write
    ./.wiwi/journals into the repo checkout)."""
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
            stream_journal_enabled=True,
            stream_journal_dir=str(tmp_path / "journals"),
            stream_journal_ttl_s=600.0,
            stream_journal_max_bytes=1 << 20),
    )


@respx.mock
async def test_cross_tenant_replay_denied_after_restart(tmp_path):
    """The #320 core, end to end: key A's stream is journalled with its owner
    line DURABLE ON DISK; the process "restarts" (a fresh JournalStore over
    the same directory — the RAM intent map is gone, the file is not); key B
    presenting A's stream id must NOT get A's content."""
    app = create_app(_config_journaled(tmp_path))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            plain_a, kid_a = await app.state.wiwi.auth.create_key(alias="alice")
            plain_b, _kid_b = await app.state.wiwi.auth.create_key(alias="bob")
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.side_effect = [httpx.Response(200, text=STREAM_A),
                                 httpx.Response(200, text=STREAM_B)]

            # Alice streams once; the journal is written under her key.
            s = await c.post("/v1/chat/completions", json=_sbody(),
                             headers={"Authorization": f"Bearer {plain_a}"})
            assert s.status_code == 200
            rid = s.headers["x-wiwi-request-id"]
            # Mechanism: the owner line is the FIRST record on disk (durable
            # before any data frame, AUDIT #320).
            jpath = tmp_path / "journals" / f"{rid}.jsonl"
            first = orjson.loads(jpath.read_bytes().split(b"\n")[0])
            assert first["owner"] == kid_a and first["seq"] == 0

            # RESTART: same app, same disk journals, fresh store — every
            # piece of in-memory journal state is gone.
            app.state.wiwi.journals = JournalStore(
                tmp_path / "journals", ttl_s=600.0, max_bytes=1 << 20)

            # Bob reconnects with Alice's stream id: the gate reads the owner
            # from disk, refuses, and Bob gets a FRESH upstream answer —
            # never Alice's journal content.
            r = await c.post("/v1/chat/completions", json=_sbody(),
                             headers={"Authorization": f"Bearer {plain_b}",
                                      "x-wiwi-stream-id": rid,
                                      "last-event-id": "0"})
            assert r.status_code == 200, r.text
            assert r.headers.get("x-wiwi-stream-replay") is None, (
                "cross-tenant replay after restart must be denied")
            assert "ALICE-SECRET" not in r.text
            assert "bob-fresh" in r.text
            assert route.call_count == 2, "denial must fall through to dispatch"

            # Control: ALICE reconnecting to her own stream id is still
            # replayed from the journal — the fix must not break #67.
            r2 = await c.post("/v1/chat/completions", json=_sbody(),
                              headers={"Authorization": f"Bearer {plain_a}",
                                       "x-wiwi-stream-id": rid,
                                       "last-event-id": "0"})
            assert r2.status_code == 200, r2.text
            assert r2.headers.get("x-wiwi-stream-replay") == rid
            assert "ALICE-SECRET" in r2.text
            assert route.call_count == 2, "replay must not call upstream"


async def test_persist_owner_survives_restart(tmp_path):
    """The fix's positive half at the store level: an fsync'd owner line reads
    back through a FRESH store (the simulated crash)."""
    dead = JournalStore(tmp_path / "journals", ttl_s=600.0, max_bytes=1_048_576)
    j = await dead.open(VICTIM, key_id="kid-A")
    j.persist_owner_sync("kid-A")
    await j.append(1, b"data: x\n\n")
    await j.finish(1)

    fresh = _simulate_restart(dead)
    assert fresh.owner_of(VICTIM) == "kid-A"
    assert _gate(fresh, VICTIM, "kid-A") is True
    assert _gate(fresh, VICTIM, "kid-B") is False


async def test_genuinely_legacy_journal_stays_readable(tmp_path):
    """The documented restart-replay compat must survive: a pre-#67 journal
    (no owner line, mtime OLDER than the new process) stays open."""
    dead = JournalStore(tmp_path / "journals", ttl_s=600.0, max_bytes=1_048_576)
    j = await dead.open(VICTIM)  # the pre-#67 open() shape: no key_id
    await j.append(1, b"data: legacy\n\n")
    await j.finish(1)

    fresh = _simulate_restart(dead)
    assert fresh.owner_of(VICTIM) is None
    assert fresh.has_owner_intent(VICTIM) is False
    assert _gate(fresh, VICTIM, "kid-anyone") is True


async def test_owner_write_failure_this_process_still_fails_closed(store):
    """The #317 behaviour is preserved (in-memory intent, same process)."""
    j = await store.open(VICTIM, key_id="kid-A")
    store._owner_intent[VICTIM] = ("kid-A", j.path)  # write lost, intent kept
    assert _gate(store, VICTIM, "kid-B") is False
    assert _gate(store, VICTIM, "kid-A") is True


async def test_persist_owner_failure_is_survivable(store, monkeypatch):
    """A journal that cannot be durably owned must not break ``open`` — the
    in-process intent still scopes it and the gate fails closed."""
    real_to_thread = asyncio.to_thread

    async def failing(fn, *a, **kw):
        if getattr(fn, "__name__", "") == "persist_owner_sync":
            raise OSError(28, "No space left on device")
        return await real_to_thread(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", failing)
    j = await store.open(VICTIM, key_id="kid-A")
    try:
        j.persist_owner_sync("kid-A")
    except OSError:
        pass
    # Same-process gate: closed for everyone but the owner, via the intent.
    assert _gate(store, VICTIM, "kid-B") is False
    assert _gate(store, VICTIM, "kid-A") is True


async def test_owner_of_still_reads_a_healthy_line(store):
    """Control: the ordinary open() path still records the owner on disk."""
    await store.open(VICTIM, key_id="kid-A")
    assert store.owner_of(VICTIM) == "kid-A"
    assert _gate(store, VICTIM, "kid-B") is False


# === #323: unpriced models must not make the budget cap a no-op =============


def test_unpriced_model_is_detected():
    """The signal the gate reads: a model with no pricing row reports
    ``unpriced=True``; a priced one does not."""
    ce = CostEngine()
    assert ce.cost_with_status("gpt-who", 1000, 1000).unpriced is True
    ce.prices["gpt-who"] = {"input_cost_per_token": 1e-6,
                            "output_cost_per_token": 2e-6}
    st = ce.cost_with_status("gpt-who", 1000, 1000)
    assert st.unpriced is False and st.cost > 0


@respx.mock
async def test_unpriced_model_bypasses_budget_by_default_is_tagged():
    """Default policy `warn`: the bypass is back-compat but OBSERVABLE — the
    response carries `x-wiwi-unpriced-model` and the spend stays 0, so the
    silent part of #323 is gone."""
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            # Budget-capped but not AT the cap (an at-cap key is 402'd at
            # admission, round 50): the point here is that a key WITH a cap
            # is served on an unpriced model under `warn` — visibly.
            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="capped", max_budget=5.0)
            vk = {"Authorization": f"Bearer {plain}"}
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                json=OPENAI_BODY)
            r = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert r.status_code == 200, r.text
            assert r.headers.get("x-wiwi-unpriced-model") == "gpt-4o"
            info = await app.state.wiwi.auth.authenticate(plain)
            assert info.spend_to_date == 0.0


@respx.mock
async def test_reject_policy_refuses_budget_capped_key_on_unpriced_model():
    """Policy `reject`: fail closed with 503 until the model is priced."""
    app = create_app(_config(unpriced_model_policy="reject"))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="capped", max_budget=5.0)
            vk = {"Authorization": f"Bearer {plain}"}
            upstream = respx.post(
                "https://api.openai.com/v1/chat/completions").respond(
                json=OPENAI_BODY)
            r = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert r.status_code == 503, r.text
            assert r.json()["error"]["type"] == "unpriced_model_error"
            assert not upstream.called, "the request must be refused before dispatch"

            # Once priced, the same key sails through (and the cap applies).
            pr = await c.put("/admin/pricing/gpt-4o",
                             json={"input_per_1m": 1.0, "output_per_1m": 1.0},
                             headers=AUTH)
            assert pr.status_code == 200, pr.text
            r2 = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert r2.status_code == 200, r2.text


@respx.mock
async def test_reject_policy_never_touches_uncapped_or_master_keys():
    """The policy gates budget-capped virtual keys only — an uncapped key and
    the master key keep working on unpriced models."""
    app = create_app(_config(unpriced_model_policy="reject"))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                json=OPENAI_BODY)
            plain, _kid = await app.state.wiwi.auth.create_key(alias="free")
            r = await c.post("/v1/chat/completions", json=_body(),
                             headers={"Authorization": f"Bearer {plain}"})
            assert r.status_code == 200, r.text
            r2 = await c.post("/v1/chat/completions", json=_body(), headers=AUTH)
            assert r2.status_code == 200, r2.text


# === #324: the budget cap must bind at admission, not post-hoc ==============


async def test_concurrent_reserves_cannot_oversubscribe_headroom():
    """The atomicity unit: N concurrent reserves against headroom for one
    must admit exactly one. The old shape (a plain read at admission) let
    every one of them through."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from wiwi.auth.service import AuthService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    svc = AuthService(engine, master_key_plaintext="master-key-test")
    await svc.startup()
    plain, kid = await svc.create_key(alias="tiny", max_budget=0.001)
    info = await svc.authenticate(plain)
    assert info is not None and info.max_budget == 0.001

    # est_spend=0.0006: two fit individually (2*0.0006 <= 0.001 is false, but
    # ONE reserve plus the post-hoc check used to pass); use 0.0006 so that
    # exactly one reserve succeeds and the second is refused.
    results = await asyncio.gather(*[svc.reserve_budget(kid, 0.0006)
                                     for _ in range(5)])
    assert results.count(True) == 1, (
        f"headroom for exactly one reserve admitted {results.count(True)}: "
        f"{results}")
    # The one winner's reservation leaves 0.0004 of headroom: filling it
    # pushes spend+reserved to exactly the cap, which must flip the
    # admission gate (spend_to_date alone is still 0).
    assert await svc.reserve_budget(kid, 0.0004) is True
    after = await svc.authenticate(plain)
    assert after.over_budget is True, (
        "a key whose headroom is fully reserved must fail the admission gate")


async def test_reserve_refund_round_trip():
    """Reconcile converts reserved into spend exactly once; refund gives the
    headroom back when the request died before producing usage."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from wiwi.auth.service import AuthService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    svc = AuthService(engine, master_key_plaintext="master-key-test")
    await svc.startup()
    plain, kid = await svc.create_key(alias="r", max_budget=1.0)

    assert await svc.reserve_budget(kid, 0.4) is True
    mid = await svc.authenticate(plain)
    assert mid.budget_reserved == 0.4
    # Admission is where the cap binds now: 0.4 reserved + 0.7 asked > 1.0
    # must be refused BEFORE any upstream work, though either alone fits.
    assert await svc.reserve_budget(kid, 0.7) is False

    # The actual cost 0.7 is charged through the normal conditional
    # update_spend — the caller releases the reservation around it, so the
    # charge is tested against spend alone and the reservation never
    # double-counts (this is exactly the record_spend convention).
    await svc.release_budget_reservation(kid, 0.4)
    assert await svc.update_spend(kid, 0.7) is True
    mid2 = await svc.authenticate(plain)
    assert mid2.spend_to_date == 0.7 and mid2.budget_reserved == 0.0
    assert mid2.over_budget is False
    assert await svc.reserve_budget(kid, 0.4) is False  # 0.7+0.4 > 1.0
    assert await svc.reserve_budget(kid, 0.3) is True   # 0.7+0.3 <= 1.0
    mid3 = await svc.authenticate(plain)
    assert mid3.over_budget is True  # headroom now fully committed

    # A fresh key: release-without-usage gives the headroom back wholesale.
    plain2, kid2 = await svc.create_key(alias="r2", max_budget=1.0)
    assert await svc.reserve_budget(kid2, 0.5) is True
    await svc.release_budget_reservation(kid2, 0.5)  # died before usage
    mid4 = await svc.authenticate(plain2)
    assert mid4.spend_to_date == 0.0 and mid4.budget_reserved == 0.0
    assert await svc.reserve_budget(kid2, 0.5) is True


async def test_reservation_does_not_double_count_spend():
    """The gate reads spend + reserved; reporting reads spend only. If the
    reconcile double-counted, operators would see money never billed."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from wiwi.auth.service import AuthService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    svc = AuthService(engine, master_key_plaintext="master-key-test")
    await svc.startup()
    _plain, kid = await svc.create_key(alias="acc", max_budget=2.0)
    await svc.reserve_budget(kid, 0.3)
    # Actual cost 0.1 charged through update_spend after the reservation is
    # released: reporting sees the real spend only, never the estimate.
    await svc.release_budget_reservation(kid, 0.3)
    assert await svc.update_spend(kid, 0.1) is True
    row = next(k for k in await svc.list_keys() if k["id"] == kid)
    assert row["spend_to_date"] == 0.1, (
        f"spend reports the reserved amount too: {row['spend_to_date']}")
    assert row["budget_reserved"] == 0.0


@respx.mock
async def test_admission_reservation_blocks_second_concurrent_request():
    """End-to-end: with the cap nearly exhausted, a second concurrent request
    is refused at admission with 402 while the first is still in flight."""
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="hot", max_budget=1.0)
            # A reservation from an in-flight request that leaves less
            # headroom than one admission estimate (est = size/4*1e-6
            # + 0.0001 ~= 0.000104 for this tiny body). The next admission
            # must be refused BEFORE dispatch — the old post-hoc gate passed
            # it and billed at the end.
            assert await app.state.wiwi.auth.reserve_budget(_kid, 0.9999) is True
            vk = {"Authorization": f"Bearer {plain}"}
            route = respx.post(
                "https://api.openai.com/v1/chat/completions").respond(
                json=OPENAI_BODY)
            r2 = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert r2.status_code == 402, r2.text
            assert r2.json()["error"]["type"] == "budget_exceeded"
            assert not route.called, (
                "a request refused at admission must never reach upstream")


@respx.mock
async def test_failed_upstream_call_releases_the_reservation():
    """A request whose upstream died before usage must give its reserved
    headroom back — otherwise every failure permanently shrank the cap."""
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="flaky", max_budget=1.0)
            vk = {"Authorization": f"Bearer {plain}"}
            up = respx.post("https://api.openai.com/v1/chat/completions")
            # A healthy first call warms the router's cooldown state, so the
            # 500 then surfaces as 502 from the request under test instead of
            # "no healthy deployment" (a 503 about nothing).
            up.side_effect = [httpx.Response(200, json=OPENAI_BODY),
                              httpx.Response(500, json={"error": {"message": "boom"}})]
            warm = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert warm.status_code == 200, warm.text
            r = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert r.status_code == 502, r.text
            info = await app.state.wiwi.auth.authenticate(plain)
            assert info.budget_reserved == 0.0, (
                "the reservation leaked: the failed request keeps headroom "
                "hostage for its TTL")
            # And the headroom is actually usable again.
            up.side_effect = [httpx.Response(200, json=OPENAI_BODY)]
            for dep in app.state.wiwi.router.groups["gpt-4o"]:
                dep.cooldown_until = 0.0
                dep.fails.clear()  # the 500 cooled the deployment off
            for acct in app.state.wiwi.router.providers.values():
                for k in acct.keys:
                    k.recover(force=True)  # and the provider key with it
            app.state.wiwi.cost.prices["openai/gpt-4o"] = {
                "input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}
            r2 = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert r2.status_code == 200, r2.text


@respx.mock
async def test_successful_request_reconciles_reservation_to_actual_cost():
    """Happy path: reserved converts into spend exactly once — the response
    is billed for the REAL cost, not estimate + actual."""
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            # Pricing set explicitly: the point is that the ACTUAL cost lands
            # (and only once). Unpriced would bill 0 and the assertion below
            # would hold for the wrong reason.
            pr = await c.put("/admin/pricing/gpt-4o",
                             json={"input_per_1m": 1.0, "output_per_1m": 2.0},
                             headers=AUTH)
            assert pr.status_code == 200, pr.text
            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="rec", max_budget=10.0)
            vk = {"Authorization": f"Bearer {plain}"}
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                json=OPENAI_BODY)
            r = await c.post("/v1/chat/completions", json=_body(), headers=vk)
            assert r.status_code == 200, r.text
            info = await app.state.wiwi.auth.authenticate(plain)
            # 5 in + 2 out at $1/$2 per 1M = 9e-6 — and NOT the estimate
            # plus that. A double-count would show ~ (est + 9e-6).
            assert abs(info.spend_to_date - 9e-6) < 1e-9, info.spend_to_date
            assert info.budget_reserved == 0.0
