"""Round 70: ``record_tokens`` mis-attributes a late reconciliation (AUDIT #32).

``_find_reservation`` prefers an exact request-id match but falls back to the
*newest estimated* reservation whenever the id does not match. That fallback
was written for callers predating request ids. On the modern id-carrying path
it fires for a different reason: the caller's own reservation is no longer in
the window, because it aged out of the 60 s window while the request was still
streaming.

When it fires, ``record_tokens`` takes over **another live request's**
reservation — it overwrites that event's token count and flips it to
``estimated=False``. Two things break at once:

1. The window under-counts. The still-in-flight request's reservation is gone
   and has been replaced by the finished request's actual usage, so admission
   lets a new request in against a cap that is really already full. This is the
   same over-admission class round 66 fixed on the *refund* path, reached
   through the *reconciliation* path instead — which is exactly why ``release``
   had to be made strict and ``record_tokens`` was left lenient.

2. The rightful owner can no longer be refunded. Its event is now
   ``estimated=False``, and ``_find_refund`` (round 66) refuses to refund
   confirmed usage — so when that request fails upstream, its refund silently
   no-ops and the phantom reservation stays for the whole window.

``Deployment.settle_tokens`` (``wiwi/router/router.py``) already handles this
shape correctly and is the precedent: match the id, else match any event for
that id, else **append** — never adopt a different request's estimate. These
tests pin the memory limiter to that behaviour.

Also covered: the empty-``key_id`` guard in ``release()`` (round 66) returns
before *both* scope loops, so a reservation taken in the shared ``global:*``
windows by an empty-id admission can never be refunded. Only the key-scoped
``":rpm"``/``":tpm"`` windows are the phantom that guard exists to prevent.
"""

from __future__ import annotations

from wiwi.ratelimit.memory import RateLimiter


async def test_late_reconciliation_does_not_overwrite_a_live_reservation():
    """A request whose own reservation aged out must not adopt another's.

    RED: ``_find_reservation`` falls back to the newest estimated event, so
    ``long``'s actual usage is written onto ``live``'s still-in-flight
    reservation and ``live``'s 300 tokens vanish from the window.
    """
    limiter = RateLimiter()
    await limiter.check("k1", key_tpm=1000, est_tokens=600, request_id="long")
    await limiter.check("k1", key_tpm=1000, est_tokens=300, request_id="live")
    window = limiter._windows["k1:tpm"]
    assert window.total == 900

    # 'long' has been streaming for over a minute: its own reservation aged
    # out of the 60 s window while it was still in flight.
    window.events[0].ts -= 61.0

    # It now completes and reconciles its real usage.
    await limiter.record_tokens("k1", 700, request_id="long")

    # 'live' is still in flight and still owns its 300-token reservation.
    # The window must hold long's 700 actual PLUS live's 300 estimate.
    assert window.total == 1000, (
        f"window holds {window.total}, expected 1000 (700 actual + 300 still "
        "in flight) — the late reconciliation overwrote a live reservation")

    live = [e for e in window.events if e.request_id == "live"]
    assert len(live) == 1, "'live' lost its event to the reconciliation"
    assert live[0].tokens == 300 and live[0].estimated, (
        f"'live' should still hold an estimated 300-token reservation, "
        f"saw tokens={live[0].tokens} estimated={live[0].estimated}")


async def test_live_request_is_still_refundable_after_a_late_reconciliation():
    """The over-admission's second half: a swallowed refund is a permanent leak.

    RED: the mis-attributed event is flipped to ``estimated=False``, so the
    rightful owner's ``release`` finds nothing (round 66's finder refuses to
    refund confirmed usage) and silently no-ops.
    """
    limiter = RateLimiter()
    await limiter.check("k1", key_tpm=1000, est_tokens=600, request_id="long")
    await limiter.check("k1", key_tpm=1000, est_tokens=300, request_id="live")
    window = limiter._windows["k1:tpm"]
    window.events[0].ts -= 61.0
    await limiter.record_tokens("k1", 700, request_id="long")

    # 'live' now fails upstream and is refunded.
    await limiter.release("k1", request_id="live")

    assert not any(e.request_id == "live" for e in window.events), (
        "'live' still holds an event after its own refund — the reconciliation "
        "had already consumed it, so the refund silently no-op'd")
    assert window.total == 700, (
        f"window holds {window.total} after refunding the only in-flight "
        "request; only long's 700 actual usage should remain")


async def test_admission_still_binds_after_a_late_reconciliation():
    """Behavioural consequence: the cap must still bind in-flight requests.

    With the window under-counting, a third request is admitted against a cap
    that two in-flight requests already fill.
    """
    limiter = RateLimiter()
    await limiter.check("k1", key_tpm=1000, est_tokens=600, request_id="long")
    await limiter.check("k1", key_tpm=1000, est_tokens=300, request_id="live")
    window = limiter._windows["k1:tpm"]
    window.events[0].ts -= 61.0
    await limiter.record_tokens("k1", 700, request_id="long")

    # Truth: 'live' holds 300 and long's 700 actual is recorded, so the window
    # is at the 1000 cap. Any further admission must be refused — and the
    # 300-token probe is exactly the one the BUG admitted: pre-fix the window
    # read only 700 (live's reservation swallowed), so 700 + 300 fit at the
    # cap and a request walked in against a full provider.
    allowed, _ = await limiter.check("k1", key_tpm=1000, est_tokens=300,
                                     request_id="fits")
    assert not allowed, (
        "admitted past the cap — the late reconciliation erased an in-flight "
        "reservation from the window")

    allowed, _ = await limiter.check("k1", key_tpm=1000, est_tokens=400,
                                     request_id="over")
    assert not allowed, (
        "admitted past the cap — the late reconciliation erased an in-flight "
        "reservation from the window")


async def test_matching_id_still_replaces_its_own_reservation():
    """Control: the ordinary path must keep reconciling in place, not append."""
    limiter = RateLimiter()
    await limiter.check("k1", key_tpm=1000, est_tokens=400, request_id="a")
    await limiter.record_tokens("k1", 100, request_id="a")
    window = limiter._windows["k1:tpm"]
    assert window.total == 100, (
        f"expected the estimate replaced by the actual 100, saw {window.total}")
    assert len(window.events) == 1, "reconciliation appended instead of replacing"


async def test_idless_caller_keeps_the_lenient_fallback():
    """Control: a caller predating request ids must keep working.

    Only the id-carrying path is made strict. An id-less call still replaces
    the newest estimated reservation (round 66 pins the same invariant).
    """
    limiter = RateLimiter()
    await limiter.check("k1", key_tpm=1000, est_tokens=100)
    window = limiter._windows["k1:tpm"]
    assert window.total == 100

    await limiter.record_tokens("k1", 250)

    assert window.total == 250, (
        f"the id-less fallback regressed: expected 250, saw {window.total}")
    assert len(window.events) == 1


async def test_repeated_reconciliation_of_one_request_is_idempotent():
    """A second reconcile for the same id must adjust, not double-count."""
    limiter = RateLimiter()
    await limiter.check("k1", key_tpm=1000, est_tokens=400, request_id="a")
    await limiter.record_tokens("k1", 100, request_id="a")
    await limiter.record_tokens("k1", 100, request_id="a")
    window = limiter._windows["k1:tpm"]
    assert window.total == 100, (
        f"a repeated reconcile double-counted: window holds {window.total}")
    assert len(window.events) == 1


# -- the empty-key_id guard must not swallow the global scopes -----------------


async def test_empty_key_id_still_refunds_the_global_windows():
    """RED: the guard returns before both loops, so ``global:*`` never refunds.

    ``check("")`` reserves in the shared global windows; ``release("")`` must
    give that back. Only the key-scoped ``":rpm"``/``":tpm"`` windows are the
    phantom the guard exists to prevent.
    """
    limiter = RateLimiter(global_rpm=2)
    await limiter.check("", est_tokens=10, request_id="r1")
    assert len(limiter._windows["global:rpm"].events) == 1

    await limiter.release("", request_id="r1")

    assert len(limiter._windows["global:rpm"].events) == 0, (
        "the global RPM slot was not refunded for an empty key_id — the guard "
        "returned before the global loop")


async def test_empty_key_id_never_creates_phantom_key_windows():
    """Control: the guard's actual purpose must survive.

    An empty key_id must not build the literal scopes ``":rpm"``/``":tpm"``,
    which are real dict keys that a later release would touch.
    """
    limiter = RateLimiter(global_rpm=2, global_tpm=1000)
    await limiter.check("", est_tokens=10, request_id="r1")
    await limiter.release("", request_id="r1")

    assert ":rpm" not in limiter._windows
    assert ":tpm" not in limiter._windows
