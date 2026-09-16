"""Round 66 — two rate-limiter defects in the sliding-window memory backend.

1. ``RateLimiter.release`` refunds an RPM slot with no identity check: it prunes
   the window, then pops the *newest* remaining event. If the releasing
   request's own RPM event has already aged out of the 60 s window (a long
   stream, or any request admitted more than a minute ago), the pop removes a
   *different*, still-in-flight request's slot — admitting a request over the
   configured cap.

2. ``RateLimiter._sweep_windows`` never actually caps the window map. It only
   deletes windows that are empty *after pruning*, so a burst of distinct
   rate-limited keys leaves the map permanently above ``_max_windows`` and every
   subsequent admission scans the whole map while holding the global lock.
"""

from __future__ import annotations

import time

from wiwi.ratelimit.memory import RateLimiter

# -- 1. release() must not refund a slot it does not own ------------------------


async def test_release_of_aged_out_request_does_not_free_a_live_slot():
    """A long request that fails must not free a *live* request's RPM slot.

    RED: ``release`` prunes (dropping the aged-out event) and then pops the
    newest remaining event, so ``long``'s refund removes ``live1``'s slot.
    """
    limiter = RateLimiter()
    cap = 3
    await limiter.check("k1", key_rpm=cap, est_tokens=10, request_id="long")
    for i in range(2):
        await limiter.check("k1", key_rpm=cap, est_tokens=10, request_id=f"live{i}")

    window = limiter._windows["k1:rpm"]
    assert window.total == cap, "all three should be occupying the window"

    # 'long' has been streaming for over a minute, so its own RPM event has
    # aged out of the 60 s window. It now fails upstream and is released.
    window.events[0].ts -= 61.0
    await limiter.release("k1", request_id="long")

    # The two `live` requests are still in flight and still own their slots:
    # exactly one slot was refunded, and it was 'long's own.
    assert window.total == 2, (
        f"expected 2 live slots to remain, saw {window.total} — the release "
        "freed a slot belonging to a request that is still in flight")
    assert {e.request_id for e in window.events} == {"live0", "live1"}, (
        "the refund removed the wrong request's event")

    # The freed slot is reusable: 2 live + 1 new == the cap of 3.
    allowed, _ = await limiter.check("k1", key_rpm=cap, est_tokens=10,
                                     request_id="extra")
    assert allowed, "the genuinely freed slot should be reusable"
    assert window.total == cap

    # But nothing beyond it: the cap still binds at 3 in flight.
    allowed, _ = await limiter.check("k1", key_rpm=cap, est_tokens=10,
                                     request_id="over")
    assert not allowed, (
        "admission went past the rpm cap — more slots were freed than the one "
        "request that actually failed")


async def test_release_without_a_matching_reservation_is_a_no_op():
    """Releasing an id that never reserved must not free anyone's slot.

    RED: with no matching event the code still pops the newest event.
    """
    limiter = RateLimiter()
    cap = 3
    for i in range(cap):
        await limiter.check("k1", key_rpm=cap, est_tokens=10, request_id=f"hold{i}")

    window = limiter._windows["k1:rpm"]
    await limiter.release("k1", request_id="never-admitted")

    assert window.total == cap, (
        f"a release for an unreserved request freed a live slot "
        f"({cap} -> {window.total})")


async def test_release_is_idempotent_for_rpm():
    """Releasing the same request twice must refund one slot, not two.

    RED: each call pops another event, so the second release frees a slot that
    belongs to a different in-flight request.
    """
    limiter = RateLimiter()
    cap = 3
    for i in range(cap):
        await limiter.check("k1", key_rpm=cap, est_tokens=10, request_id=f"hold{i}")

    window = limiter._windows["k1:rpm"]
    await limiter.release("k1", request_id="hold0")
    after_first = window.total
    await limiter.release("k1", request_id="hold0")
    after_second = window.total

    assert after_first == cap - 1
    assert after_second == after_first, (
        f"a second release for the same request refunded another slot "
        f"({after_first} -> {after_second})")


async def test_global_rpm_slot_is_not_freed_by_an_unrelated_release():
    """The global scope must not be refunded for a request that never reserved.

    The global RPM window is shared by every key, so a spurious refund there
    raises the effective global cap, not just one key's.
    """
    limiter = RateLimiter(global_rpm=2)
    await limiter.check("ka", est_tokens=10, request_id="r-ka")
    await limiter.check("kb", est_tokens=10, request_id="r-kb")

    await limiter.release("kz", request_id="never-reserved")

    allowed, _ = await limiter.check("kc", est_tokens=10, request_id="r-kc")
    assert not allowed, (
        "a release for an unreserved request freed a global rpm slot, "
        "admitting a third request against a global cap of 2")


async def test_release_still_refunds_the_owning_request():
    """Control: an ordinary failure must still refund its own slot.

    Guards against "fixing" the over-refund by making release a no-op, which
    would reintroduce the leak AUDIT #70/#121 fixed.
    """
    limiter = RateLimiter()
    cap = 3
    await limiter.check("k1", key_rpm=cap, est_tokens=10, request_id="fail")
    for i in range(2):
        await limiter.check("k1", key_rpm=cap, est_tokens=10, request_id=f"hold{i}")

    window = limiter._windows["k1:rpm"]
    assert window.total == cap

    await limiter.release("k1", request_id="fail")

    assert window.total == cap - 1, (
        "a failed request's own rpm slot must be refunded, or it leaks "
        "(AUDIT #70)")
    allowed, _ = await limiter.check("k1", key_rpm=cap, est_tokens=10,
                                     request_id="replacement")
    assert allowed, "the refunded slot should be reusable"


async def test_tpm_release_without_a_matching_reservation_is_a_no_op():
    """The TPM path had the same defect: an unmatched release freed live tokens.

    ``_find_reservation`` falls back to the newest estimated reservation when
    the id does not match, which is correct for ``record_tokens`` (replacing an
    estimate) but wrong for ``release`` (removing capacity).
    """
    limiter = RateLimiter()
    await limiter.check("k1", key_tpm=1000, est_tokens=300, request_id="live")
    tpm = limiter._windows["k1:tpm"]
    assert tpm.total == 300

    await limiter.release("k1", request_id="never-admitted")

    assert tpm.total == 300, (
        "a release for an unreserved request refunded a live request's tpm "
        "reservation")


async def test_tpm_refund_still_works():
    """Control: the TPM refund path must still refund the matching request."""
    limiter = RateLimiter()
    await limiter.check("k1", key_tpm=1000, est_tokens=400, request_id="a")
    await limiter.check("k1", key_tpm=1000, est_tokens=400, request_id="b")
    tpm = limiter._windows["k1:tpm"]
    assert tpm.total == 800

    await limiter.release("k1", request_id="a")
    assert tpm.total == 400, "the matching tpm reservation must be refunded"


async def test_record_tokens_still_reconciles_its_own_reservation():
    """Control: ``record_tokens`` must keep its newest-estimated fallback.

    ``release`` and ``record_tokens`` need opposite strictness: release removes
    capacity (so it must be identity-exact), while record_tokens replaces an
    estimate with the real count for a request that succeeded (so falling back
    to another in-flight estimate keeps the total right). Tightening release
    must not tighten this.
    """
    limiter = RateLimiter()
    await limiter.check("k1", key_tpm=1000, est_tokens=100, request_id="a")
    tpm = limiter._windows["k1:tpm"]
    assert tpm.total == 100

    # No request_id supplied (a caller predating ids): the newest estimated
    # reservation is replaced, not appended alongside.
    await limiter.record_tokens("k1", 250)

    assert tpm.total == 250, (
        f"record_tokens should have replaced the 100-token estimate with 250, "
        f"not appended (total={tpm.total})")


# -- 2. the window map must actually be capped ---------------------------------


async def test_sweep_runs_at_most_once_per_interval():
    """The full sweep must not run on every admission once over the cap.

    RED: ``_sweep_windows`` scanned every window on each call, and it is called
    from ``check`` — so past ``_max_windows`` every admission paid an O(n) scan
    while holding the limiter's lock.
    """
    limiter = RateLimiter()
    limiter._max_windows = 10

    # Fill past the cap, then note the sweep watermark.
    for i in range(50):
        await limiter.check(f"key{i}", key_rpm=100, est_tokens=10)
    limiter._last_sweep = 0.0

    for i in range(50):
        await limiter.check(f"more{i}", key_rpm=100, est_tokens=10)

    assert limiter._last_sweep > 0.0, (
        "the sweep never ran, so the map cannot be reclaimed at all")
    first = limiter._last_sweep

    # Immediately after, further admissions must NOT trigger another sweep.
    for i in range(50):
        await limiter.check(f"burst{i}", key_rpm=100, est_tokens=10)

    assert limiter._last_sweep == first, (
        "a second full sweep ran within the interval — every admission is "
        "still paying an O(n) scan under the lock")


async def test_admission_stays_fast_with_many_windows():
    """Admission must not degrade linearly with the window count."""
    limiter = RateLimiter()
    limiter._max_windows = 100
    for i in range(300):
        await limiter.check(f"key{i}", key_rpm=100, key_tpm=10_000_000,
                            est_tokens=10)

    start = time.perf_counter()
    for i in range(200):
        await limiter.check(f"hot{i}", key_rpm=100, key_tpm=10_000_000,
                            est_tokens=10)
    per_check_ms = (time.perf_counter() - start) / 200 * 1000

    # Baseline is ~0.005 ms; an unthrottled 600-window scan measured ~6 ms.
    # 1 ms leaves generous headroom for a slow CI box while still failing
    # loudly if every admission is scanning the whole map.
    assert per_check_ms < 1.0, (
        f"admission took {per_check_ms:.2f} ms per check with "
        f"{len(limiter._windows)} windows — the sweep is not throttled")


async def test_active_key_survives_sweep_pressure():
    """Control: the sweep must never drop a window that is actively limiting.

    Evicting a live window would reset that key's count and admit it over its
    cap — the sweep may only reclaim *empty* windows.
    """
    limiter = RateLimiter()
    limiter._max_windows = 10

    for _ in range(3):
        allowed, _ = await limiter.check("hot", key_rpm=3, est_tokens=10)
        assert allowed
    allowed, _ = await limiter.check("hot", key_rpm=3, est_tokens=10)
    assert not allowed, "the hot key's own cap must still be enforced"

    # Flood with distinct keys to put the map well over the cap, forcing sweeps.
    for i in range(200):
        await limiter.check(f"flood{i}", key_rpm=100, est_tokens=10)
        limiter._last_sweep = 0.0  # let each admission sweep if it wants to

    allowed, _ = await limiter.check("hot", key_rpm=3, est_tokens=10)
    assert not allowed, (
        "after sweep pressure the hot key was admitted over its cap — the "
        "sweep dropped a window that was actively rate limiting")
