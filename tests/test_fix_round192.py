"""Round 192 — performance modules.

Covers the batched spend buffer, singleflight dedupe, buffered stream
journals, the NIM tool-schema memo, prompt-prefix affinity, and the
shared-database reservation reset. Assertions are behavioural so a later
refactor cannot silently re-open the defect each test pins.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import respx
import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.auth.service import AuthInfo, AuthService
from wiwi.auth.spend_buffer import SpendBuffer
from wiwi.auth.users import UserService
from wiwi.cache.singleflight import FlightResult, SingleFlight
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
    WiwiSettings,
)
from wiwi.core.context import RequestContext
from wiwi.ir import types as ir
from wiwi.providers.nim_tool_schema import sanitize_and_declare
from wiwi.ratelimit.memory import RateLimiter
from wiwi.router.router import Router
from wiwi.server.app import create_app
from wiwi.streaming.tape_store import JournalStore

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


async def _service(**kwargs) -> AuthService:
    svc = AuthService(create_async_engine("sqlite+aiosqlite:///:memory:"),
                      master_key_plaintext=MASTER, **kwargs)
    await svc.startup()
    return svc


async def _owned_key(svc: AuthService, budget: float) -> tuple[str, str]:
    users = UserService(svc.engine, session_secret="test-secret")
    await users.startup()
    user = await users.create_user("alice", "password123")
    return await svc.create_key(alias="k", max_budget=budget,
                                owner_id=user.id)


async def _reserved(svc: AuthService, key_id: str) -> float:
    async with svc.engine.connect() as conn:
        row = (await conn.execute(
            sa.text("SELECT budget_reserved FROM vkeys WHERE id=:id"),
            {"id": key_id})).first()
    return float(row[0])


async def _spend(svc: AuthService, key_id: str) -> float:
    async with svc.engine.connect() as conn:
        row = (await conn.execute(
            sa.text("SELECT spend_to_date FROM vkeys WHERE id=:id"),
            {"id": key_id})).first()
    return float(row[0])


# ---------------------------------------------------------------------------
# Spend buffer
# ---------------------------------------------------------------------------

async def test_spend_buffer_aggregates_and_releases_reservation():
    svc = await _service()
    plaintext, kid = await _owned_key(svc, 10.0)
    assert await svc.reserve_budget(kid, 1.0)
    info = await svc.authenticate(plaintext)
    assert info is not None and info.budget_reserved == 1.0

    buf = SpendBuffer(svc, interval_s=60.0)
    assert buf.charge(info, 2.0, 1.0) is True
    # The process-local gate is exact immediately.
    assert info.spend_to_date == 2.0
    assert info.budget_reserved == 0.0
    # The DB still holds the reservation until the flush (crash-safe).
    assert await _reserved(svc, kid) == 1.0
    assert await _spend(svc, kid) == 0.0

    await buf.flush()
    assert await _reserved(svc, kid) == 0.0
    assert await _spend(svc, kid) == 2.0


async def test_spend_buffer_charge_crossing_cap_is_recorded():
    buf = SpendBuffer(AuthInfo(key_id="k", key_type="virtual"), interval_s=60.0)
    info = AuthInfo(key_id="k", key_type="virtual", max_budget=1.0,
                    spend_to_date=0.9)
    assert buf.charge(info, 0.2, 0.0) is False
    assert info.spend_to_date == 1.1
    assert buf.pending_dollars == 0.2


async def test_spend_buffer_master_is_a_noop():
    buf = SpendBuffer(AuthInfo(key_id="master", key_type="master"))
    info = AuthInfo(key_id="master", key_type="master")
    assert buf.charge(info, 5.0, 0.0) is True
    assert buf.pending_dollars == 0.0


class _FakeAuth:
    def __init__(self) -> None:
        self.settled: list[tuple[str, float, float]] = []

    async def settle_spend(self, key_id: str, cost: float,
                           reserved: float) -> None:
        self.settled.append((key_id, cost, reserved))


async def test_spend_buffer_flushes_at_the_pending_limit():
    fake = _FakeAuth()
    buf = SpendBuffer(fake, max_pending=2, max_dollars_per_key=100.0)
    info = AuthInfo(key_id="k", key_type="virtual")
    buf.charge(info, 0.01, 0.0)
    assert not buf.should_flush_now(info, 0.01)
    buf.charge(info, 0.01, 0.0)
    assert buf.should_flush_now(info, 0.01)
    await buf.maybe_flush_near_cap(info, 0.01)
    assert fake.settled == [("k", 0.02, 0.0)]
    assert buf.pending_dollars == 0.0


async def test_spend_buffer_flushes_at_the_dollar_limit():
    fake = _FakeAuth()
    buf = SpendBuffer(fake, max_pending=1000, max_dollars_per_key=1.0)
    info = AuthInfo(key_id="k", key_type="virtual")
    buf.charge(info, 0.6, 0.0)
    assert not buf.should_flush_now(info, 0.6)
    buf.charge(info, 0.6, 0.0)
    assert buf.should_flush_now(info, 0.6)
    await buf.maybe_flush_near_cap(info, 0.6)
    assert fake.settled == [("k", 1.2, 0.0)]


async def test_spend_buffer_retries_a_failed_flush(monkeypatch):
    svc = await _service()
    _plaintext, kid = await _owned_key(svc, 10.0)
    buf = SpendBuffer(svc, interval_s=60.0)
    info = AuthInfo(key_id=kid, key_type="virtual", max_budget=10.0)
    assert buf.charge(info, 1.5, 0.0)

    async def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "settle_spend", _boom)
    await buf.flush()
    assert buf.flush_failures == 1
    assert buf.pending_dollars == 1.5

    monkeypatch.undo()
    await buf.flush()
    assert buf.pending_dollars == 0.0
    assert await _spend(svc, kid) == 1.5


# ---------------------------------------------------------------------------
# Singleflight
# ---------------------------------------------------------------------------

async def test_singleflight_dedupes_concurrent_identical_calls():
    sf = SingleFlight()
    calls = 0

    async def factory() -> FlightResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return FlightResult(payload={"ok": True})

    results = await asyncio.gather(*[sf.run("same", factory) for _ in range(8)])
    assert calls == 1
    assert sum(1 for _r, joined in results if joined) == 7
    assert sf.leaders == 1


async def test_singleflight_does_not_merge_different_keys():
    sf = SingleFlight()
    calls = 0

    async def factory() -> FlightResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return FlightResult(payload={})

    await asyncio.gather(sf.run("a", factory), sf.run("b", factory))
    assert calls == 2


async def test_singleflight_propagates_leader_failure():
    sf = SingleFlight()

    async def factory() -> FlightResult:
        await asyncio.sleep(0.01)
        raise ValueError("upstream refused")

    async def waiter():
        try:
            await sf.run("k", factory)
        except ValueError as e:
            return str(e)
        return None

    assert await asyncio.gather(waiter(), waiter()) == [
        "upstream refused", "upstream refused"]


# ---------------------------------------------------------------------------
# Journal buffering
# ---------------------------------------------------------------------------

async def test_journal_buffers_until_read_or_finish(tmp_path):
    store = JournalStore(tmp_path / "j", ttl_s=600, max_bytes=1 << 20)
    j = await store.open("r1")
    await j.append(1, b"data: a\n\n")
    # The record is still in memory, but a sync reader merges the active
    # buffer so it does not see a stale file.
    assert store.read_after("r1", 0) == [(1, b"data: a\n\n")]
    state = await store.replay_state("r1", 0)
    assert [seq for seq, _ in state.frames] == [1]
    await j.finish(1)
    assert store.is_complete("r1")
    assert [seq for seq, _ in store.read_after("r1", 0)] == [1]


async def test_journal_flushes_on_byte_cap(tmp_path):
    store = JournalStore(tmp_path / "j", ttl_s=600, max_bytes=1 << 20)
    j = await store.open("r2")
    big = b"x" * (70 * 1024)
    await j.append(1, big)
    # Crossing the 64 KiB buffer flushed without an explicit read/finish.
    assert store.read_after("r2", 0)
    await j.aclose()


# ---------------------------------------------------------------------------
# NIM tool-schema memo
# ---------------------------------------------------------------------------

def test_nim_schema_memo_returns_isolated_copies():
    tools = [{
        "type": "function",
        "function": {
            "name": "f",
            "parameters": {
                "type": "object",
                "properties": {"type": {"type": "string"}},
                "additionalProperties": True,
            },
        },
    }]
    first, declared = sanitize_and_declare(tools)
    second, _ = sanitize_and_declare(tools)
    assert first == second
    assert "additionalProperties" not in first[0]["function"]["parameters"]
    assert declared == {"f": {"type"}}

    # Mutating a returned copy must not poison the memo.
    first[0]["function"]["parameters"]["properties"]["leak"] = {"type": "string"}
    third, _ = sanitize_and_declare(tools)
    assert "leak" not in third[0]["function"]["parameters"]["properties"]


def test_nim_schema_cache_respects_configured_size():
    from wiwi.providers import nim_tool_schema as nts

    def _tool(name: str):
        return [{"type": "function", "function": {
            "name": name,
            "parameters": {"type": "object", "properties": {}}}}]

    try:
        nts.configure_schema_cache(1)
        nts.sanitize_and_declare(_tool("a"))
        nts.sanitize_and_declare(_tool("b"))
        assert len(nts._SANITIZE_CACHE) == 1

        nts.configure_schema_cache(0)
        clean, _ = nts.sanitize_and_declare(_tool("c"))
        assert clean[0]["function"]["name"] == "c"
        assert len(nts._SANITIZE_CACHE) == 0
    finally:
        nts.configure_schema_cache(256)


# ---------------------------------------------------------------------------
# Rate-limiter sharding
# ---------------------------------------------------------------------------

async def test_sharded_limiter_keeps_per_key_isolation():
    lim = RateLimiter()
    assert (await lim.check("a", key_rpm=1))[0]
    assert not (await lim.check("a", key_rpm=1))[0]
    # A different key hashes to its own shard/window and is unaffected.
    assert (await lim.check("b", key_rpm=1))[0]


# ---------------------------------------------------------------------------
# Prompt-prefix affinity
# ---------------------------------------------------------------------------

def _router_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai", keys=[KeyDef(key="k1")]),
            ProviderDef(name="p2", provider="openai", keys=[KeyDef(key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="g", wiwi_params=DeploymentParams(
                provider="p1", model="m1")),
            ModelEntry(model_name="g", wiwi_params=DeploymentParams(
                provider="p2", model="m2")),
        ],
        router_settings=RouterSettings(prefix_affinity=True,
                                       prefix_affinity_ttl_s=600),
    )


def _ctx() -> RequestContext:
    req = ir.Request(
        model="g",
        messages=[ir.Message(role="system", parts=[ir.TextPart("stable")])],
    )
    return RequestContext(surface="chat", ir_req=req, group="g")


def test_prefix_affinity_pins_after_success_and_yields_when_unhealthy():
    router = Router(_router_config())
    deps = router.groups["g"]
    assert len(deps) == 2
    ctx = _ctx()

    router._pin_prefix(ctx, deps[0])
    picked = router.pick_deployment(deps, ctx)
    assert picked is deps[0]

    # A pin must never resurrect a deployment the health filters rejected.
    deps[0].cooldown_until = time.monotonic() + 60
    assert router.pick_deployment(deps, ctx) is deps[1]


# ---------------------------------------------------------------------------
# Shared-database reservation reset
# ---------------------------------------------------------------------------

async def test_shared_db_startup_keeps_a_live_siblings_reservation():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    first = AuthService(engine, master_key_plaintext=MASTER,
                        shared_database=True, reservation_stale_s=600)
    await first.startup()
    _plaintext, kid = await _owned_key(first, 10.0)
    assert await first.reserve_budget(kid, 1.0)

    second = AuthService(engine, master_key_plaintext=MASTER,
                         shared_database=True, reservation_stale_s=600)
    await second.startup()
    assert await _reserved(second, kid) == 1.0


async def test_shared_db_startup_reclaims_a_stale_reservation():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    first = AuthService(engine, master_key_plaintext=MASTER,
                        shared_database=True, reservation_stale_s=600)
    await first.startup()
    _plaintext, kid = await _owned_key(first, 10.0)
    assert await first.reserve_budget(kid, 1.0)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "UPDATE vkeys SET reserved_at = :t WHERE id = :id"),
            {"t": time.time() - 1200, "id": kid})

    second = AuthService(engine, master_key_plaintext=MASTER,
                         shared_database=True, reservation_stale_s=600)
    await second.startup()
    assert await _reserved(second, kid) == 0.0


async def test_single_process_startup_still_reclaims_immediately():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    first = AuthService(engine, master_key_plaintext=MASTER)
    await first.startup()
    _plaintext, kid = await _owned_key(first, 10.0)
    assert await first.reserve_budget(kid, 1.0)

    second = AuthService(engine, master_key_plaintext=MASTER)
    await second.startup()
    assert await _reserved(second, kid) == 0.0


# ---------------------------------------------------------------------------
# End-to-end: the batched branch in record_spend
# ---------------------------------------------------------------------------

OPENAI_BODY = {
    "id": "chatcmpl-1", "object": "chat.completion", "created": 1,
    "model": "gpt-4o",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
}


def _app_config(db_path) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://api.example.com/v1",
                               keys=[KeyDef(label="k1", key="sk-x")])],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=f"sqlite+aiosqlite:///{db_path}"),
        wiwi_settings=WiwiSettings(spend_batching_enabled=True,
                                   perf_timings=True),
    )


def test_batching_is_off_by_default():
    assert WiwiSettings().spend_batching_enabled is False


@respx.mock
async def test_batched_spend_lands_after_flush_and_emits_timings(tmp_path):
    respx.post("https://api.example.com/v1/chat/completions").respond(
        json=OPENAI_BODY)
    app = create_app(_app_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        assert state.spend is not None
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.put("/admin/pricing/gpt-4o", headers=AUTH,
                            json={"input_per_1m": 1.0, "output_per_1m": 1.0})
            assert r.status_code == 200, r.text
            r = await c.post("/admin/keys/generate", headers=AUTH,
                             json={"name": "spender", "max_budget": 10.0})
            assert r.status_code in (200, 201), r.text
            sk = r.json()["key"]

            r = await c.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {sk}"},
                json={"model": "gpt-4o",
                      "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 200, r.text
            assert "x-wiwi-timings" in r.headers

            # The charge is buffered, not yet visible to a fresh DB read.
            before = await state.auth.authenticate(sk)
            assert before is not None and before.spend_to_date == 0.0
            assert before.budget_reserved > 0.0

            await state.spend.flush()
            after = await state.auth.authenticate(sk)
            assert after is not None
            assert abs(after.spend_to_date - 0.002) < 1e-9
            assert after.budget_reserved == 0.0
            assert state.spend.flushes == 1

            health = await c.get("/health")
            assert health.json()["spend_flush_failures"] == 0
