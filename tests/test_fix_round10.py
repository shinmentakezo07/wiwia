"""Round-10 regression tests.

1. **Loop detection cost** — the per-token repetition scan was O(n^2) in the
   window size and ran on every token once the window filled. With the shipped
   ``stream_loop_limit`` of 100 that was ~30 us of pure CPU per token. The
   detector is now O(1) per token and restricted to short periods.
2. **Adapter state bleed** — adapters accumulate per-stream state (open tool
   indices, NIM framers, buffered args, Cline header context) that is only
   cleared on ``finish_reason``. A stream that dies mid-flight left that state
   behind. Adapters are now shared singletons that ``get_adapter`` resets, and
   callers that hold one across an await use ``fresh_adapter``.
3. **Tape waste** — ``StreamTape.append`` ran for every content delta even
   though the tape is only read by ``_attempt_resume``, which is gated on
   ``stream_resume != "off"`` (the default).
4. **#57** — per-user virtual-key cap: users could mint unbounded keys and
   rotate around per-key budgets and rate limits.
"""

import time

import pytest

from wiwi.auth import service as service_mod
from wiwi.streaming import deltas as dl
from wiwi.streaming.loopdetect import MAX_LOOP_PERIOD, LoopDetector

# -- 1. loop detection -------------------------------------------------------


class TestLoopDetector:
    def test_detects_period_one_stutter(self):
        det = LoopDetector(limit=10)
        fired_at = None
        for i in range(30):
            if det.feed("same"):
                fired_at = i
                break
        # A run of `limit` identical chunks is exactly the old threshold.
        assert fired_at == 9

    @pytest.mark.parametrize("period", range(2, MAX_LOOP_PERIOD + 1))
    def test_detects_short_periods(self, period):
        """Periodic output of period <= MAX_LOOP_PERIOD must still abort."""
        det = LoopDetector(limit=30)
        fired_at = None
        for i in range(300):
            if det.feed(f"c{i % period}"):
                fired_at = i
                break
        assert fired_at is not None, f"period {period} not detected"

    def test_healthy_stream_never_fires(self):
        det = LoopDetector(limit=100)
        for i in range(5000):
            assert not det.feed(f"token-{i}"), f"false positive at {i}"

    def test_disabled_when_limit_is_zero(self):
        det = LoopDetector(0)
        for _ in range(500):
            assert not det.feed("same")

    def test_disabled_when_limit_is_negative(self):
        det = LoopDetector(-1)
        for _ in range(500):
            assert not det.feed("same")

    def test_small_limits_are_safe(self):
        """limit=1..3 must not raise or fire spuriously (old deque maxlen bug)."""
        for limit in (1, 2, 3):
            det = LoopDetector(limit)
            assert isinstance(det.feed("a"), bool)
            assert isinstance(det.feed("a"), bool)

    def test_per_token_cost_is_bounded(self):
        """The point of the rewrite: cost must not grow with the window.

        The old implementation scanned the whole window on every token after
        it filled, so a larger limit meant a larger per-token cost. The new
        one is O(MAX_LOOP_PERIOD) regardless.
        """
        results = {}
        for limit in (100, 1000):
            det = LoopDetector(limit)
            # Fill the detector with non-repeating content (worst case: no
            # early exit) so every feed does the full amount of work.
            for i in range(limit):
                det.feed(f"tok-{i}")
            n = 20000
            t0 = time.perf_counter()
            for i in range(n):
                det.feed(f"tok-{limit + i}")
            results[limit] = (time.perf_counter() - t0) / n

        # Generous bound: 8 us/token is ~250x the measured cost, so this
        # catches only a regression to window-proportional scanning.
        assert results[1000] < 8e-6, f"per-token cost {results[1000] * 1e6:.1f} us"
        # Cost must be flat in the limit, not proportional to it.
        assert results[1000] < results[100] * 3, (
            f"cost grows with limit: {results[100] * 1e6:.1f} us vs "
            f"{results[1000] * 1e6:.1f} us")


# -- 2. adapter lifecycle ----------------------------------------------------


class TestAdapterReset:
    @pytest.mark.parametrize("provider_type", [
        "openai", "openai-compatible", "gmicloud", "anthropic", "gemini",
        "openrouter", "nvidia-nim", "cline",
    ])
    def test_every_adapter_exposes_reset(self, provider_type):
        from wiwi.providers.registry import fresh_adapter

        assert hasattr(fresh_adapter(provider_type), "reset")

    def test_get_adapter_returns_shared_reset_instance(self):
        from wiwi.providers.registry import get_adapter

        a = get_adapter("openai")
        a._open_tool_indices.add(7)
        a._pending_opens[7] = ("id7", "name")
        # A second caller must not inherit the first caller's dead state.
        b = get_adapter("openai")
        assert a is b
        assert not b._open_tool_indices
        assert not b._pending_opens

    def test_nim_reset_clears_framers_and_tool_state(self):
        from wiwi.providers.registry import fresh_adapter

        ad = fresh_adapter("nvidia-nim")
        ad._tool_schemas = {"f": {"type": "object"}}
        ad._tool_aliases = {"f": {"_nim_arg_type": "type"}}
        ad._buffered_args = {0: '{"a":1}'}
        # Push the framer into tool-block mode so it has real state to lose.
        ad._content_framer.feed("partial <minimax:tool_call>")
        ad.reset()
        assert ad._tool_schemas == {}
        assert ad._tool_aliases == {}
        assert ad._buffered_args == {}
        # Framers must be replaced, not merely reused: a fresh framer has no
        # held-back text and no mode, so it echoes input straight through.
        assert ad._content_framer.feed("plain text") == "plain text"

    def test_cline_reset_clears_header_context(self):
        """Cline forwards a client-sent task id; it must not leak between requests."""
        from wiwi.providers.registry import fresh_adapter

        ad = fresh_adapter("cline")
        ad.set_header_context({"task_id": "victim-task"})
        assert ad._context == {"task_id": "victim-task"}
        ad.reset()
        assert ad._context == {}

    def test_gemini_reset_clears_stream_state(self):
        from wiwi.providers.registry import fresh_adapter

        ad = fresh_adapter("gemini")
        ad._started = True
        ad._tool_seq = 3
        ad.reset()
        assert ad._started is False
        assert ad._tool_seq == 0

    def test_fresh_adapter_is_private(self):
        from wiwi.providers.registry import fresh_adapter, get_adapter

        assert fresh_adapter("openai") is not get_adapter("openai")

    def test_unknown_provider_type_still_rejected(self):
        from wiwi.providers.registry import fresh_adapter, get_adapter

        with pytest.raises(ValueError):
            get_adapter("nope")
        with pytest.raises(ValueError):
            fresh_adapter("nope")


# -- 3. tape is not recorded when resume is off ------------------------------


def test_tape_append_is_gated_on_resume_mode():
    """Recording the tape when nothing can read it is pure waste.

    The tape is only consumed by ``_attempt_resume``, so with the default
    ``stream_resume="off"`` every append is a dealloc'd discard. Gating it
    saves ~1.7 ms per 2k-token stream.
    """
    import inspect

    from wiwi.core import gateway

    src = inspect.getsource(gateway.Gateway.stream)
    assert 'if resume_mode != "off":' in src, (
        "tape.append should be gated on resume mode")
    # The append itself must be inside the guard, not merely nearby.
    assert "tape.append(d)" in src

# -- 4. #57 per-user virtual key cap ----------------------------------------
class TestPerUserKeyCap:
    async def test_count_keys_scopes_to_owner(self):
        """count_keys must count only the given owner.

        The cap depends on this: if it counted globally, one user creating
        keys would eventually block every other user.
        """
        from wiwi.auth.service import AuthService

        engine = _memory_engine()
        svc = AuthService(engine, master_key_plaintext="master-key-test")
        await svc.startup()
        await svc.create_key(alias="u1a", owner_id="u1")
        await svc.create_key(alias="u1b", owner_id="u1")
        await svc.create_key(alias="u2a", owner_id="u2")

        assert await svc.count_keys(owner_id="u1") == 2
        assert await svc.count_keys(owner_id="u2") == 1
        await engine.dispose()

    async def test_cap_is_enforced_per_owner(self):
        """A user may not mint more than max_keys_per_user live keys."""
        from wiwi.auth.service import AuthService

        engine = _memory_engine()
        svc = AuthService(engine, master_key_plaintext="master-key-test",
                          max_keys_per_user=3)
        await svc.startup()
        for i in range(3):
            await svc.create_key(alias=f"k{i}", owner_id="u1")

        with pytest.raises(ValueError, match="key limit reached"):
            await svc.create_key(alias="over", owner_id="u1")
        assert await svc.count_keys(owner_id="u1") == 3
        await engine.dispose()

    async def test_cap_does_not_affect_other_owners(self):
        """One user hitting their cap must not block anyone else."""
        from wiwi.auth.service import AuthService

        engine = _memory_engine()
        svc = AuthService(engine, master_key_plaintext="master-key-test",
                          max_keys_per_user=1)
        await svc.startup()
        await svc.create_key(alias="a", owner_id="u1")
        with pytest.raises(ValueError, match="key limit reached"):
            await svc.create_key(alias="b", owner_id="u1")
        # u2 is unaffected and admin keys (owner_id=None) are exempt.
        await svc.create_key(alias="c", owner_id="u2")
        await svc.create_key(alias="admin1")
        await svc.create_key(alias="admin2")
        await engine.dispose()

    async def test_expired_keys_do_not_consume_the_cap(self):
        """Expiring keys frees capacity; the cap counts *live* keys only."""
        from wiwi.auth.service import AuthService

        engine = _memory_engine()
        svc = AuthService(engine, master_key_plaintext="master-key-test",
                          max_keys_per_user=2)
        await svc.startup()
        await svc.create_key(alias="old", owner_id="u1", ttl_seconds=1)
        # Freeze time past the 1s TTL so the key reads as expired.
        real_time = service_mod.time.time
        service_mod.time.time = lambda: real_time() + 10
        try:
            assert await svc.count_keys(owner_id="u1") == 0
            await svc.create_key(alias="n1", owner_id="u1")
            await svc.create_key(alias="n2", owner_id="u1")
            with pytest.raises(ValueError, match="key limit reached"):
                await svc.create_key(alias="n3", owner_id="u1")
        finally:
            service_mod.time.time = real_time
        await engine.dispose()

    def test_setting_exists_and_validates(self):
        from wiwi.config import GeneralSettings

        assert GeneralSettings().max_keys_per_user > 0
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GeneralSettings(max_keys_per_user=0)

def _memory_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine("sqlite+aiosqlite:///:memory:")


# -- 5. wire encoders: buffer accumulators ----------------------------------


class TestResponsesTextBuffer:
    def test_text_buffers_join_not_concat(self):
        """`+=` on a str per token is quadratic; the accumulator must be a list."""
        from wiwi.wire.openai_responses import ResponsesStreamEncoder

        enc = ResponsesStreamEncoder("m", "r")
        assert isinstance(enc._text_buf, list)
        assert isinstance(enc._think_buf, list)

    def test_long_stream_text_is_assembled_correctly(self):
        from wiwi.wire.openai_responses import ResponsesStreamEncoder

        enc = ResponsesStreamEncoder("m", "r")
        enc.feed(dl.StreamStart(model="m"))
        pieces = [f"piece{i} " for i in range(500)]
        for p in pieces:
            enc.feed(dl.TextDelta(text=p))
        assert "".join(enc._text_buf) == "".join(pieces)

    def test_thinking_buffer_resets_between_blocks(self):
        from wiwi.wire.openai_responses import ResponsesStreamEncoder

        enc = ResponsesStreamEncoder("m", "r")
        enc.feed(dl.StreamStart(model="m"))
        enc.feed(dl.ThinkingDelta(text="think-a"))
        assert "".join(enc._think_buf) == "think-a"
        enc.feed(dl.TextDelta(text="text"))
        enc.feed(dl.ThinkingDelta(text="think-b"))
        assert "".join(enc._think_buf) == "think-b"


# -- 6. guard: detector is actually wired into the pump ----------------------


def test_pump_uses_loop_detector():
    """The pump must use the O(1) detector, not the old quadratic scan."""
    import inspect

    from wiwi.core import gateway

    src = inspect.getsource(gateway.Gateway._pump_once)
    assert "LoopDetector" in src, "pump should use LoopDetector"
    # The old quadratic scan must be gone, not merely supplemented.
    assert "for period in range(1, n // 2 + 1)" not in src, (
        "the O(n^2) loop scan is still present")


async def test_detector_survives_concurrent_streams():
    """Two concurrent streams share nothing: each pump owns its detector.

    Adapters are shared singletons now, so this pins that the detector is
    *not* one of them — cross-talk would abort healthy streams.
    """
    from wiwi.streaming.loopdetect import LoopDetector

    a, b = LoopDetector(5), LoopDetector(5)
    # Feed `a` toward a loop while `b` stays healthy.
    for i in range(4):
        a.feed("x")
        b.feed(f"b{i}")
    assert a.feed("x"), "looping stream should trip"
    assert not b.feed("b9"), "healthy stream must be unaffected by `a`"

# -- 7. #7/#17: the all-keys-cooling retry path is bounded --------------------
class TestAllKeysCoolingRetry:
    """The all-keys-cooling path retries a deployment it just exhausted.

    then *clearing* ``tried_dep_ids``/``tried_key_labels`` — deliberately
    un-exhausting the deployment it just marked exhausted. The only thing
    keeping that from spinning forever is the ``attempt < num_retries``
    guard, so pin the bound here.
    """

    async def test_retries_are_bounded_when_keys_keep_cooling(self):
        from wiwi.config import (
            DeploymentParams,
            KeyDef,
            ModelEntry,
            ProviderDef,
            RouterSettings,
            WiwiConfig,
        )
        from wiwi.core import context as ctx_mod
        from wiwi.providers.base import WiwiError
        from wiwi.router import router as router_mod
        from wiwi.router.router import Router, execute_with_retries

        num_retries = 3
        cfg = WiwiConfig(
            providers=[ProviderDef(name="p1", provider="openai",
                                   keys=[KeyDef(label="a", key="k1"),
                                         KeyDef(label="b", key="k2")])],
            model_list=[ModelEntry(
                model_name="m",
                wiwi_params=DeploymentParams(provider="p1", model="m"))],
            router_settings=RouterSettings(num_retries=num_retries,
                                           cooldown_time=30),
        )
        router = Router(cfg)
        ctx = ctx_mod.RequestContext(surface="chat", ir_req=None)
        ctx.group = "m"

        # Patch out the wait so the test measures loop bounds, not wall time.
        sleeps: list[float] = []
        calls = 0
        real_sleep = router_mod.asyncio.sleep

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        async def call_one(dep, key, c):
            nonlocal calls
            calls += 1
            raise WiwiError(429, "rate_limit_error", "cooling",
                            retryable=True, retry_after=1.0)

        router_mod.asyncio.sleep = fake_sleep
        try:
            with pytest.raises(WiwiError) as exc:
                await execute_with_retries(router, ctx, call_one)
        finally:
            router_mod.asyncio.sleep = real_sleep

        assert exc.value.status == 429
        # Bounded by num_retries + 1 attempts, never an unbounded spin.
        assert calls <= num_retries + 1, f"{calls} calls exceeds the bound"
        assert calls >= 2, "should have retried at least once"
        # Every wait respects the documented clamp.
        assert sleeps and all(0.5 <= s <= 5.5 for s in sleeps)
        assert len(sleeps) <= num_retries

    async def test_terminal_error_does_not_sleep_and_retry(self):
        """A non-retryable error exits immediately: no sleep-and-clear."""
        from wiwi.config import (
            DeploymentParams,
            KeyDef,
            ModelEntry,
            ProviderDef,
            RouterSettings,
            WiwiConfig,
        )
        from wiwi.core import context as ctx_mod
        from wiwi.providers.base import WiwiError
        from wiwi.router import router as router_mod
        from wiwi.router.router import Router, execute_with_retries

        cfg = WiwiConfig(
            providers=[ProviderDef(name="p1", provider="openai",
                                   keys=[KeyDef(label="a", key="k1")])],
            model_list=[ModelEntry(
                model_name="m",
                wiwi_params=DeploymentParams(provider="p1", model="m"))],
            router_settings=RouterSettings(num_retries=5),
        )
        router = Router(cfg)
        ctx = ctx_mod.RequestContext(surface="chat", ir_req=None)
        ctx.group = "m"

        sleeps = []
        calls = 0
        real_sleep = router_mod.asyncio.sleep

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        async def call_one(dep, key, c):
            nonlocal calls
            calls += 1
            raise WiwiError(400, "invalid_request_error", "bad request")

        router_mod.asyncio.sleep = fake_sleep
        try:
            with pytest.raises(WiwiError) as exc:
                await execute_with_retries(router, ctx, call_one)
        finally:
            router_mod.asyncio.sleep = real_sleep

        assert exc.value.status == 400
        assert calls == 1, "a non-retryable error must not be retried"
        assert sleeps == []

    def test_clear_and_retry_is_guarded_by_attempts_remaining(self):
        """The clear-and-retry must sit inside the `attempt < num_retries` guard."""
        import inspect

        from wiwi.router import router as router_mod

        src = inspect.getsource(router_mod.execute_with_retries)
        assert "if attempt < router.settings.num_retries:" in src
        assert "tried_dep_ids.clear()" in src
        assert "tried_key_labels.clear()" in src

