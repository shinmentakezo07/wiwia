"""Round-17 regression tests: TPM limiter rejection + Redis token accounting.

Regression targets (issue.md NEW #62 and AUDIT #31):

- ``RateLimiter.check`` rejected an over-limit request by reading
  ``w.events[0].ts`` to compute ``retry_after``. AUDIT #59's fix guarded only
  ``limit <= 0``; a *positive* limit with ``est_tokens > limit`` on an empty
  sliding window (first request of the minute, or a request whose token
  estimate alone exceeds the key's full TPM allowance) still raised
  ``IndexError: deque index out of range`` — surfacing as HTTP 500 instead of
  a clean 429. Fix: when the window has no events left after pruning, the
  retry horizon is the full 60 s window (nothing ages out sooner) instead of
  indexing an empty deque. Both the per-key and global TPM scopes take the
  same path.
- ``RedisRateLimiter`` enforced TPM as a request *count* (``zcard``) instead
  of a token sum, ``zadd`` member collisions (same second + same cost) silently
  dropped concurrent reservations, and ``record_tokens`` never reconciled the
  Redis-side estimate. Fix: members carry ``"{ts}:{cost}:{uid}"`` so admission
  sums per-member token costs with collision-free uids, and a request-tagged
  reservation is rewritten to the actual usage after the response.
- ``PUT /admin/pricing`` accepted no cache-write rate, so Anthropic
  cache-creation tokens (the ~1.25x input premium) could never be priced
  distinctly (AUDIT #10 residual). Fix: the endpoint round-trips
  ``cache_creation_per_1m`` like ``cache_read_per_1m``.

Also pins the mid-window behaviour: with prior events present, ``retry_after``
is still derived from the oldest event's age, so the value only shrinks as
the window drains.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
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
from wiwi.ratelimit.memory import RateLimiter
from wiwi.ratelimit.redis import RedisRateLimiter


async def test_oversized_first_request_rejects_cleanly() -> None:
    """est_tokens > key_tpm on a fresh window: 429 semantics, never IndexError."""
    rl = RateLimiter()
    allowed, retry_after = await rl.check("k", key_tpm=1000, est_tokens=5000)
    assert allowed is False
    assert 0 < retry_after <= 60


async def test_oversized_global_tpm_rejects_cleanly() -> None:
    rl = RateLimiter(global_tpm=100)
    allowed, retry_after = await rl.check("k", est_tokens=500)
    assert allowed is False
    assert 0 < retry_after <= 60


async def test_first_request_rpm_burst_rejection_clean() -> None:
    """Same rejection path via rpm: exhaust a tiny limit, confirm the
    non-empty-window rejection stays well-formed."""
    rl = RateLimiter()
    ok, _ = await rl.check("k", key_rpm=2)
    ok2, _ = await rl.check("k", key_rpm=2)
    assert ok and ok2
    allowed, retry_after = await rl.check("k", key_rpm=2)
    assert allowed is False
    assert 0 < retry_after <= 60


async def test_midwindow_retry_after_derived_from_oldest_event() -> None:
    """With events in the window, retry_after shrinks as the oldest event ages;
    this pins the existing non-empty-window formula so the empty-window fix
    does not change that behaviour."""
    rl = RateLimiter()
    await rl.check("k", key_tpm=10, est_tokens=8)  # fills the window
    # Age the sole event by 55 s so the retry horizon is ~5 s.
    w = rl._windows["k:tpm"]
    w.events[0].ts -= 55.0
    allowed, retry_after = await rl.check("k", key_tpm=10, est_tokens=8)
    assert allowed is False
    assert 1 <= retry_after <= 6


async def test_concurrent_oversized_checks_do_not_crash() -> None:
    rl = RateLimiter()
    results = await asyncio.gather(*[
        rl.check("k", key_tpm=1000, est_tokens=5000) for _ in range(8)
    ])
    assert all(allowed is False and 0 < retry <= 60 for allowed, retry in results)


# -- Redis backend (AUDIT #31): token-sum TPM, collision-free, reconciliation --

class _FakePipeline:
    def __init__(self, r: _FakeRedis) -> None:
        self._r = r
        self._ops: list[tuple] = []

    def zremrangebyscore(self, key: str, lo: float, hi: float) -> _FakePipeline:
        self._ops.append(("zrbs", key, lo, hi))
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> _FakePipeline:
        self._ops.append(("zadd", key, mapping))
        return self

    def zrange(self, key: str, start: int, stop: int) -> _FakePipeline:
        self._ops.append(("zrange", key, start, stop))
        return self

    def zrem(self, key: str, *members: str) -> _FakePipeline:
        self._ops.append(("zrem", key, members))
        return self

    async def execute(self) -> list:
        out = []
        for op in self._ops:
            if op[0] == "zrbs":
                _, key, lo, hi = op
                z = self._r.zsets.setdefault(key, {})
                for m, s in list(z.items()):
                    if lo <= s <= hi:
                        z.pop(m)
                out.append(None)
            elif op[0] == "zadd":
                _, key, mapping = op
                self._r.zsets.setdefault(key, {}).update(mapping)
                out.append(1)
            elif op[0] == "zrange":
                _, key, start, stop = op
                out.append(await self._r.zrange(key, start, stop))
            elif op[0] == "zrem":
                _, key, members = op
                z = self._r.zsets.setdefault(key, {})
                for m in members:
                    z.pop(m, None)
                out.append(1)
        return out


class _FakeRedis:
    """Minimal async sorted-set surface backed by a plain dict."""

    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def zrange(self, key: str, start: int, stop: int,
                     withscores: bool = False) -> list:
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        window = items[start:] if stop == -1 else items[start:stop + 1]
        if withscores:
            return list(window)
        return [m for m, _ in window]

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zrem(self, key: str, *members: str) -> int:
        z = self.zsets.setdefault(key, {})
        removed = 0
        for m in members:
            if z.pop(m, None) is not None:
                removed += 1
        return removed

    async def aclose(self) -> None:
        pass


def _redis_limiter(**kw) -> tuple[RedisRateLimiter, _FakeRedis]:
    limiter = RedisRateLimiter(redis_url="redis://unused", **kw)
    fake = _FakeRedis()
    limiter._redis = fake
    return limiter, fake


async def test_redis_tpm_enforces_token_sum_not_request_count() -> None:
    """TPM 100 with two 60-token requests: the second must be rejected.
    The old zcard implementation counted requests (2 <= 100) and allowed it."""
    rl, fake = _redis_limiter()
    ok1, _ = await rl.check("k", key_tpm=100, est_tokens=60, request_id="r1")
    ok2, retry = await rl.check("k", key_tpm=100, est_tokens=60, request_id="r2")
    assert ok1 is True
    assert ok2 is False
    assert 0 < retry <= 60
    # The rejected reservation was undone across all scopes.
    assert fake.zsets.get("k:tpm") is not None
    assert not any(m.split(":")[2] == "r2" for m in fake.zsets["k:tpm"])


async def test_redis_reservations_do_not_collide() -> None:
    """Concurrent same-second, same-cost reservations (no request id) must all
    count. The old "{ts}:{cost}" member format let zadd overwrite siblings."""
    rl, fake = _redis_limiter()
    ok1, _ = await rl.check("k", key_tpm=100, est_tokens=40)
    ok2, _ = await rl.check("k", key_tpm=100, est_tokens=40)
    assert ok1 and ok2
    members = fake.zsets["k:tpm"]
    assert len(members) == 2  # distinct members, both alive
    assert sum(int(m.split(":")[1]) for m in members) == 80


async def test_redis_record_tokens_reconciles_estimate() -> None:
    """A request-tagged estimate is rewritten to actual usage, so the next
    admission check sums real consumption instead of the estimate."""
    rl, fake = _redis_limiter()
    ok, _ = await rl.check("k", key_tpm=100, est_tokens=80, request_id="r1")
    assert ok
    await rl.record_tokens("k", 10, request_id="r1")
    member = next(m for m in fake.zsets["k:tpm"] if m.split(":")[2] == "r1")
    assert member.split(":")[1] == "10"
    ok2, _ = await rl.check("k", key_tpm=100, est_tokens=80, request_id="r2")
    assert ok2  # 10 actual + 80 new estimate = 90 <= 100


async def test_redis_rpm_scope_still_counts_requests() -> None:
    rl, _ = _redis_limiter()
    ok1, _ = await rl.check("k", key_rpm=2, est_tokens=999)
    ok2, _ = await rl.check("k", key_rpm=2, est_tokens=999)
    ok3, _ = await rl.check("k", key_rpm=2, est_tokens=999)
    assert ok1 and ok2 and ok3 is False


# -- admin pricing: cache-creation (write) rate (AUDIT #10 residual) ----------

MASTER = "sk-wiwi-master-test"


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


async def test_pricing_put_accepts_cache_creation_rate() -> None:
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            auth = {"Authorization": f"Bearer {MASTER}"}
            r = await client.put("/admin/pricing/claude-x", headers=auth, json={
                "input_per_1m": 3.0, "output_per_1m": 15.0,
                "cache_read_per_1m": 0.3, "cache_creation_per_1m": 3.75,
            })
            assert r.status_code == 200
            body = r.json()
            assert body["cache_read_per_1m"] == pytest.approx(0.3)
            assert body["cache_creation_per_1m"] == pytest.approx(3.75)

            # GET round-trips both cache rates.
            r = await client.get("/admin/pricing", headers=auth)
            entry = next(m for m in r.json()["models"]
                         if m["model_id"] == "claude-x")
            assert entry["cache_read_per_1m"] == pytest.approx(0.3)
            assert entry["cache_creation_per_1m"] == pytest.approx(3.75)
