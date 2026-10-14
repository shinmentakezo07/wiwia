"""Resolve an OAuth token response's expiry into an absolute ``expires_at``.

Every OAuth provider wiwi supports (Cline, WorkBuddy, Hermes) hits the same
trap: the wire contract for an access-token lifetime is *not* guaranteed. A
response that omits ``expires_in`` — or returns ``0`` / a non-number — must
never be recorded as *already expired*.

``time.time() + 0`` lands in the past, and every provider's due-check
(``expires_within_lead(past, now)`` is ``True``) then treats the token as
permanently due. The sweeper re-presents the **single-use** refresh grant on
every tick until the upstream finally answers ``invalid_grant`` and the
provider is marked dead. That is AUDIT #425 for Hermes, and the identical
shape was later found in Cline and WorkBuddy, where the old code went one step
further and *preserved the already-stale* expiry on a missing field.

So all three resolve the field here instead. A missing or unusable value falls
back to a forward window **strictly beyond the lead** (``2 * lead_s``), which
makes the very next due-check return ``False``: one absent field cannot become
a refresh storm. "Unusable" means anything no larger than the lead itself —
``None``, ``0``, a non-number, a ``bool`` (``isinstance(True, int)`` is True,
but ``expires_in: true`` is a portal bug, not "1 second"), or a positive value
so short the token is inside its own refresh window the moment it lands. A
value comfortably larger than the lead is honoured verbatim. The fallback is
deliberately only two lead windows, not "never expires" — a token whose real
lifetime is unknown should still be probed again soon.

This lives in its own module rather than in one provider's ``*_oauth.py`` so
that adding a fourth OAuth provider picks up the safe behaviour by importing it
instead of re-deriving (and re-mis-deriving) the rule.
"""

from __future__ import annotations

import time

__all__ = ["compute_expires_at"]


def compute_expires_at(expires_in: object, now: float | None = None,
                       fallback_window: float | None = None,
                       *, lead_s: float) -> float:
    """Resolve *expires_in* into an absolute epoch-seconds ``expires_at``.

    ``expires_in`` is whatever the upstream sent — seconds from now, possibly
    ``None``, possibly a string, possibly ``0``. ``lead_s`` is the calling
    provider's refresh lead; the fallback window is ``2 * lead_s`` unless
    *fallback_window* overrides it, and must be strictly greater than
    ``lead_s`` or the token is due again immediately.
    """
    now = time.time() if now is None else now
    if fallback_window is None:
        fallback_window = 2 * lead_s
    if isinstance(expires_in, bool):
        return now + fallback_window
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return now + fallback_window
    # A value no larger than the lead is as useless as a missing one: the token
    # is inside its own refresh window the instant it is recorded, so the next
    # due-check fires immediately and the single-use grant gets burned on a
    # schedule the operator never asked for. Treat it as unusable.
    if seconds <= lead_s:
        return now + fallback_window
    return now + seconds
