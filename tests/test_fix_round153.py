"""Round 153: the Hermes refresh worker's fallback cache must not pin AppStates.

AUDIT #471. Hermes' `_worker_for` fallback cached a module-global
`dict[int, HermesAutoRefresh]` keyed on `id(state)` — the shape AUDIT #451 had
already moved Cline and WorkBuddy off. Two distinct failures, both reproduced
before the fix:

1. **Leak.** `HermesAutoRefresh.__init__` holds `self._state = state`, and the
   module global held the worker, so the global rooted every AppState that ever
   took the fallback path. Dropping all local references and calling
   `gc.collect()` left every state alive — one pinned worker (with its
   config-store engine reference) per state built.

2. **Recycled-id inheritance.** An entry was reused on numeric address
   equality alone, with no check that the referent is still the same object, so
   a new state landing on a dead state's address inherited its `_locks`,
   `_generations` and `CircuitBreaker._dead`. For Hermes that is the worst case
   of the three refreshers: an inherited `mark_dead(("provider", "acct"))` makes
   a legitimately reconnected account read as permanently unreloginable, and the
   inherited generation counter is the one that guards the single-use refresh
   grant against double presentation (AUDIT #169).

The fix anchors the fallback worker on the state object itself (WorkBuddy's
shape), so it dies with the state and `id()` never appears at all.
"""

from __future__ import annotations

import gc
import weakref

from wiwi.providers import hermes_auto_refresh as har


class _MiniState:
    """Minimal stand-in for AppState (only what `_worker_for` touches).

    ``hermes_refresh`` is left unset so the fallback path is exercised — the
    lifespan sets the real worker at startup.
    """


def test_module_global_cache_is_gone():
    """The `id(state)`-keyed global must no longer exist — that dict was both
    failure modes at once."""
    assert not hasattr(har, "_workers"), (
        "hermes_auto_refresh still exposes a module-global _workers cache; "
        "the fallback must be anchored on the state (AUDIT #471)"
    )


def test_fallback_does_not_pin_states():
    """A dropped AppState must be collectable: the worker it created is rooted
    on the state, not in a module global, so the whole cycle is unreachable."""
    refs = []
    for _ in range(5):
        s = _MiniState()
        refs.append(weakref.ref(s))
        har._worker_for(s)          # take the fallback path
        del s
    gc.collect()
    alive = sum(1 for r in refs if r() is not None)
    assert alive == 0, (
        f"{alive}/5 AppStates stayed alive after the fallback worker was "
        "created and every local reference dropped — the worker cache roots "
        "the state (AUDIT #471)"
    )


def test_recycled_id_does_not_inherit_dead_worker_state():
    """A fresh state must never receive another state's worker.

    The old cache matched on `id()` alone, so a re-created state landing on a
    recycled address inherited the dead worker's per-account lock, generation
    counter and dead circuit. With the fallback anchored on the state, a new
    state always builds its own worker — so a poisoned `mark_dead` cannot make a
    reconnected account look permanently unreloginable.
    """
    first = _MiniState()
    w_first = har._worker_for(first)
    # Poison the first worker's per-account state.
    ident = ("prov", "acct@example.com")
    w_first._generations[ident] = 7
    w_first._circuit.mark_dead(ident)

    # A genuinely different state. The fix removes the id() dependency
    # entirely, so a distinct object must get a distinct worker.
    second = _MiniState()
    w_second = har._worker_for(second)

    assert w_second is not w_first, "a different AppState got the same worker"
    assert w_second._circuit.dead(ident) is False, (
        "a fresh state inherited a dead circuit for an unrelated account"
    )
    assert w_second._generations.get(ident) is None, (
        "a fresh state inherited another state's generation counter"
    )


def test_fallback_worker_is_stable_per_state():
    """Within one state the worker is still shared (that is the point — one
    lock and one circuit per account), so repeated resolution returns the same
    object rather than a fresh worker per call."""
    s = _MiniState()
    a = har._worker_for(s)
    b = har._worker_for(s)
    assert a is b


def test_lifespan_owned_worker_still_wins():
    """The real production path (state.hermes_refresh set at startup) must keep
    taking precedence over the fallback."""
    s = _MiniState()
    owned = har.HermesAutoRefresh(s)
    s.hermes_refresh = owned
    assert har._worker_for(s) is owned
