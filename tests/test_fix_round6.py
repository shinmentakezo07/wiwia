"""Regression tests for bug-fix round 6 (2026-08-25 review).

Covers:
- Fix #1 (H1+M4+M7): auth/service.py budget race + stale cache + unpriced
  model flagging
- Fix #2 (H4): plaintext provider keys in admin API responses
- Fix #3 (H5-H8): streaming tool-arg size caps, PII-safe logging, repair depth
- Fix #4 (H2+H3): router on_result + memory rate limiter locking
- Fix #5 (H10): Responses codec parallel tool-call corruption
"""

from __future__ import annotations

import asyncio
import json

import pytest

# ============================================================================
# Fix #1: H1+M4+M7 — budget race + stale cache + unpriced-model flagging
# ============================================================================

# -- H1: conditional UPDATE rejects spend that would push past max_budget --

async def test_update_spend_rejects_overbudget_atomic():
    """update_spend must use a conditional UPDATE so two concurrent spends
    cannot both succeed when their sum would exceed max_budget."""
    import sqlalchemy.ext.asyncio as saa

    from wiwi.auth.service import AuthService

    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    svc = AuthService(engine, "sk-wiwi-master-test")
    await svc.startup()
    plaintext, kid = await svc.create_key("ci", max_budget=10.0)

    # The conditional UPDATE only succeeds when the new value would still
    # be <= max_budget. A second $6 spend on a $10 budget with $5 spent
    # already must be rejected.
    result = await svc.update_spend(kid, 5.0)
    assert result is True  # first call succeeds (5 <= 10)
    result = await svc.update_spend(kid, 6.0)
    assert result is False  # would push 5+6=11 > 10
    info = await svc.authenticate(plaintext)
    # spend must equal the successful $5 only — no partial overage
    assert info.spend_to_date == 5.0
    await engine.dispose()


async def test_update_spend_master_noop():
    """update_spend on the master key must not touch the DB."""
    import sqlalchemy.ext.asyncio as saa

    from wiwi.auth.service import AuthService

    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    svc = AuthService(engine, "sk-wiwi-master-test")
    await svc.startup()
    # master is special: never tracked, never blocks
    result = await svc.update_spend("master", 9999.0)
    assert result is True
    await engine.dispose()


# -- M4: keys with max_budget must not use the 60s cache (stale read) --

async def test_budget_cache_bypassed_when_max_budget_set():
    """A virtual key with max_budget must reflect DB spend immediately
    so over-budget requests are rejected without waiting for the TTL."""
    import sqlalchemy.ext.asyncio as saa

    from wiwi.auth.service import AuthService

    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    svc = AuthService(engine, "sk-wiwi-master-test")
    await svc.startup()
    plaintext, kid = await svc.create_key("ci", max_budget=10.0)

    # First lookup populates the cache at 0.0 spend.
    info = await svc.authenticate(plaintext)
    assert info.spend_to_date == 0.0
    assert info.over_budget is False

    # Directly mutate the DB to simulate a parallel request's update_spend
    # that already happened. Because this key has a max_budget, the next
    # authenticate() must NOT return a cached AuthInfo — it must re-read.
    import sqlalchemy as sa
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("UPDATE vkeys SET spend_to_date=10.0 WHERE id=:id"),
            {"id": kid},
        )

    info2 = await svc.authenticate(plaintext)
    # Without the fix this would still report 0.0 from the cache.
    assert info2.spend_to_date == 10.0
    assert info2.over_budget is True
    await engine.dispose()


async def test_budget_cache_used_when_no_max_budget():
    """Keys without max_budget can still use the TTL cache — it does not
    affect any budget decision because there is no budget limit."""
    import sqlalchemy.ext.asyncio as saa

    from wiwi.auth.service import AuthService

    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    svc = AuthService(engine, "sk-wiwi-master-test")
    await svc.startup()
    plaintext, kid = await svc.create_key("ci")  # no max_budget

    info = await svc.authenticate(plaintext)
    assert info.spend_to_date == 0.0

    # Mutate DB; cached value should remain 0.0 (no max_budget => cache allowed).
    import sqlalchemy as sa
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("UPDATE vkeys SET spend_to_date=999.0 WHERE id=:id"),
            {"id": kid},
        )
    info2 = await svc.authenticate(plaintext)
    assert info2.spend_to_date == 0.0  # served from cache
    await engine.dispose()


# -- M7: unpriced model flagged instead of silent $0 --

def test_cost_marks_unpriced_model():
    """When a model's pricing is missing, CostEngine must report the
    unpriced state so the gateway can log/flag it rather than silently
    treating usage as $0."""
    from wiwi.cost.pricing import CostEngine

    ce = CostEngine()  # empty overrides; nothing priced
    state = ce.cost_with_status("openai/never-priced", 1000, 500)
    assert state.unpriced is True
    assert state.cost == 0.0  # raw $0 but unpriced flag is set


def test_cost_marks_priced_model():
    from wiwi.cost.pricing import CostEngine

    ce = CostEngine()
    ce.register("openai/gpt-priced", 0.000001, 0.000002)
    state = ce.cost_with_status("openai/gpt-priced", 1000, 500)
    assert state.unpriced is False
    # 1000 * 1e-6 + 500 * 2e-6 = 0.001 + 0.001 = 0.002
    assert abs(state.cost - 0.002) < 1e-9


# ============================================================================
# Fix #2: H4 — plaintext provider keys in admin API responses
# ============================================================================

async def test_admin_providers_does_not_leak_plaintext_secrets():
    """The /admin/providers list response must NOT include plaintext
    provider keys in the 'secret' field of each key entry — only the
    masked form is safe to expose to the admin UI."""
    import httpx
    from asgi_lifespan import LifespanManager

    from wiwi.config import (
        DeploymentParams, GeneralSettings, KeyDef, ModelEntry, ProviderDef,
        RouterSettings, WiwiConfig,
    )
    from wiwi.server.app import create_app

    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-LEAK-1234567890ABCDE")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(),
    )
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/admin/providers",
                                 headers={"Authorization": "Bearer sk-wiwi-master-test"})
            assert r.status_code == 200
            body = r.json()
            for prov in body["providers"]:
                for k in prov.get("keys", []):
                    # Plaintext must NOT appear in the response at all
                    assert "sk-LEAK-1234567890ABCDE" not in json.dumps(k), \
                        f"plaintext secret leaked: {k!r}"
                    # The 'secret' key itself must not be present
                    assert "secret" not in k, \
                        f"key response contains 'secret' field: {k!r}"
                    # Masked form must still be present
                    assert "masked" in k


# ============================================================================
# Fix #3: H5-H8 — streaming tool-arg size caps, PII-safe logging, repair depth
# ============================================================================

# -- H5: cap raw_args size in validate_tool_args --

def test_validate_tool_args_caps_oversize():
    from wiwi.streaming.validation import validate_tool_args

    huge = "x" * (2 * 1024 * 1024)  # 2 MiB, above the 1 MiB cap
    valid, msg = validate_tool_args("t", huge, {"type": "string"})
    assert valid is False
    assert "too large" in msg.lower() or "cap" in msg.lower()


def test_validate_tool_args_under_cap_works():
    from wiwi.streaming.validation import validate_tool_args

    # Under the cap and matches schema
    valid, _msg = validate_tool_args("t", '"hi"', {"type": "string"})
    assert valid is True


# -- H6: stop logging raw tool args --

def test_validate_tool_args_does_not_log_raw(caplog):
    """validate_tool_args must log length + fingerprint, never raw args."""
    caplog.set_level("WARNING", logger="wiwi.streaming.validation")
    from wiwi.streaming.validation import validate_tool_args

    secret = '"super-secret-password-from-tool-args-1234"'
    valid, _msg = validate_tool_args("t", secret, {"type": "integer"})
    assert valid is False
    rendered = " ".join(r.getMessage() for r in caplog.records)
    assert "super-secret-password" not in rendered
    # Length and fingerprint are expected to be present.
    assert "len=" in rendered or "length=" in rendered or "fingerprint" in rendered


# -- H7: partial_json buffer cap --

def test_partial_json_buffer_caps_size():
    from wiwi.streaming.partial_json import PartialJSONParser

    p = PartialJSONParser()
    huge = "x" * (2 * 1024 * 1024)
    p.feed(huge)
    # Once the cap is hit, further feed() must not grow the buffer unbounded.
    for _ in range(3):
        p.feed("more")
    assert len(p.raw) <= 1024 * 1024 + 64  # cap + a tiny slack for the trailing fragment


def test_partial_json_repair_depth_capped():
    """_repair_truncated_json must bound its container stack so deeply-nested
    pathological input doesn't blow the call stack or spend unbounded time."""
    from wiwi.streaming.partial_json import _repair_truncated_json

    # 100k open braces — pathological but legal to receive
    deep = "{" * 100_000
    out = _repair_truncated_json(deep)
    # Output must be a string and the repair must terminate.
    assert isinstance(out, str)
    # The number of closing braces appended must be at most the cap,
    # not all 100_000 of them.
    assert out.count("}") <= 4096 + 1  # cap + the unmatched top-level opener may be closed


# ============================================================================
# Fix #4: H2+H3 — router on_result + memory rate limiter locking
# ============================================================================

# -- H2: ProviderAccount.on_result must serialize under _rr_lock --

async def test_on_result_serialized_with_pick_key():
    """If on_result mutates the same state pick_key reads, it must take
    the round-robin lock; otherwise pick_key can observe a mid-update key
    and a concurrent on_result can corrupt the smooth-WRR state."""
    from wiwi.router.router import ProviderAccount, ProviderKey

    acct = ProviderAccount(
        name="p", provider_type="openai", base_url="http://x",
        keys=[ProviderKey(label="a", secret="k", weight=1),
              ProviderKey(label="b", secret="k", weight=1)],
    )
    # Run pick_key and on_result concurrently; without the lock the two
    # can interleave such that current_weight is corrupted.
    async def pick_loop() -> None:
        for _ in range(200):
            await acct.pick_key()

    async def result_loop() -> None:
        for _ in range(200):
            for k in acct.keys:
                acct.on_result(k, 429, 1.0)
            for k in acct.keys:
                acct.on_result(k, 200, None)

    await asyncio.gather(pick_loop(), result_loop())
    # After the storm, no key should be left in a state where current_weight
    # is wildly out of range — bounded by sum of weights (here 2).
    for k in acct.keys:
        assert -1e6 < k.current_weight < 1e6, \
            f"current_weight corruption: {k.current_weight}"


# -- H3: RateLimiter.check + record_tokens must be lock-protected --

async def test_rate_limiter_concurrent_check_under_limit():
    """check() must be atomic against itself, otherwise concurrent callers
    can both pass when only one slot is free."""
    from wiwi.ratelimit.memory import RateLimiter

    rl = RateLimiter(global_rpm=10)
    # 50 concurrent checks at 1 each — only the first 10 must succeed.
    results = await asyncio.gather(*(rl.check("k") for _ in range(50)))
    allowed = sum(1 for ok, _ in results if ok)
    assert allowed == 10


async def test_rate_limiter_record_tokens_atomic():
    """record_tokens must be atomic with check(); the estimated reservation
    must be replaced, not appended alongside, otherwise the next check
    double-counts."""
    from wiwi.ratelimit.memory import RateLimiter

    rl = RateLimiter(global_tpm=1000)
    # 5 concurrent checks each reserving 200 estimated, then recording
    # actual 100. Total confirmed usage must be 5*100=500, not 5*300=1500.
    async def flow() -> None:
        ok, _ = rl.check("k", est_tokens=200)
        assert ok
        rl.record_tokens("k", 100)

    await asyncio.gather(*(flow() for _ in range(5)))
    # One more check should still be allowed (1000 - 500 = 500 left),
    # not denied (1000 - 1500 = negative).
    ok, _ = rl.check("k", est_tokens=100)
    assert ok is True


# ============================================================================
# Fix #5: H10 — Responses codec parallel tool-call corruption
# ============================================================================

def test_responses_encoder_parallel_tool_calls_preserved():
    """Two tool calls in flight (ToolCallOpen for index 0, then for index 1)
    must each keep their own name/call_id/args — the encoder must use a
    per-index dict, not a single _tool_n/_tool_name/_args_buf."""
    from wiwi.streaming import deltas as dl
    from wiwi.wire.openai_responses import ResponsesStreamEncoder

    enc = ResponsesStreamEncoder("model", "req1")
    enc.feed(dl.StreamStart(model="m", group="g"))
    enc.feed(dl.ToolCallOpen(index=0, id="callA", name="toolA"))
    enc.feed(dl.ToolCallArgsDelta(index=0, args_fragment='{"x":1}'))
    enc.feed(dl.ToolCallOpen(index=1, id="callB", name="toolB"))
    enc.feed(dl.ToolCallArgsDelta(index=1, args_fragment='{"y":2}'))
    out_a = enc.feed(dl.ToolCallClose(index=0))
    out_b = enc.feed(dl.ToolCallClose(index=1))
    assert out_a is not None
    assert out_b is not None
    # Each close event must reference its OWN call_id and name.
    assert b"callA" in out_a
    assert b"toolA" in out_a
    assert b"callB" in out_b
    assert b"toolB" in out_b
    # And neither close event should mention the other tool.
    assert b"callB" not in out_a
    assert b"toolB" not in out_a
    assert b"callA" not in out_b
    assert b"toolA" not in out_b
