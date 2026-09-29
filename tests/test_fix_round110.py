"""Round 110: journal owner-record write failure fails the #67 gate open.

One caller-reachable defect in the streaming package:

**Journal ownership fails open when the owner record cannot be written.**
``JournalStore.open`` wrote the ``owner`` line best-effort —

    try:
        await asyncio.to_thread(_write_owner)
    except OSError:
        pass  # best-effort: replay scoping degrades to none

— and the replay gate in ``server/app.py`` treats ``owner_of() is None`` as
"a journal written before #67, readable by anyone", which is the intended
compat behaviour for *legacy* files. A transient write failure (ENOSPC on the
Space's ``/data`` volume, EMFILE under fd pressure, a read-only mount) produces
the same indistinguishable ``None`` on a *brand-new* journal, so the gate
opened to every caller: key B could reconnect with ``x-wiwi-stream-id`` set to
key A's request and replay A's response content.

The root cause is that "no owner was ever recorded" and "the owner could not
be recorded" are the same on-disk state. ``open`` now records the *intent* in
memory (``_owner_intent``), cleared by ``release``/``sweep`` alongside the
active set, so the reader can tell them apart: an intent entry means an owner
was expected and the write lost, which is a hard deny; no intent and no record
means genuinely legacy, which keeps the documented restart-replay compat.

The write failure is also no longer silent — it logs at ``error`` with the
request id and key id, because an operator currently has no signal that replay
scoping degraded on a live stream.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from wiwi.streaming.tape_store import JournalStore

VICTIM = "0d76f7149cb048dc"  # a real 16-hex request id (core/context.py)


@pytest.fixture
def store(tmp_path):
    return JournalStore(tmp_path / "journals", ttl_s=600.0, max_bytes=1_048_576)


@pytest.fixture
def owner_write_fails(monkeypatch):
    """Make only the owner-record write raise OSError, as ENOSPC would."""
    real_to_thread = asyncio.to_thread

    async def fake_to_thread(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "_write_owner":
            raise OSError(28, "No space left on device")
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)


def _gate(store, request_id, caller_kid):
    """The replay-gate predicate from server/app.py:1463-1468."""
    jowner = store.owner_of(request_id)
    return (jowner is None
            or caller_kid == jowner
            or (jowner == "master" and caller_kid == "master"))


async def test_owner_write_failure_does_not_open_the_gate_to_other_keys(
        store, owner_write_fails):
    """The core defect: a lost owner write must not make A's journal readable
    by B. Before the fix ``owner_of`` returned None and the gate said True."""
    j = await store.open(VICTIM, key_id="kid-A")
    await j.append(1, b"data: SECRET-FROM-KEY-A\n\n")
    await j.finish(1)

    # The real owner is still recognised (the intent is remembered), and —
    # the actual defect — nobody else is.
    assert _gate(store, VICTIM, "kid-B") is False, \
        "a failed owner write must fail closed, not open"
    assert _gate(store, VICTIM, "master") is False
    assert _gate(store, VICTIM, "kid-anything-else") is False


async def test_owner_write_failure_still_allows_the_real_owner(
        store, owner_write_fails):
    """Failing closed must not brick the legitimate owner's own reconnect —
    the intent is remembered even though the file write was lost."""
    await store.open(VICTIM, key_id="kid-A")
    assert _gate(store, VICTIM, "kid-A") is True


async def test_healthy_owner_write_is_unchanged(store):
    """Control: the normal path still records the owner on disk, and the
    existing #67 / #191 behaviour is untouched."""
    j = await store.open(VICTIM, key_id="kid-A")
    await j.append(1, b"data: a\n\n")
    await j.finish(1)

    assert store.owner_of(VICTIM) == "kid-A"
    assert _gate(store, VICTIM, "kid-A") is True
    assert _gate(store, VICTIM, "kid-B") is False
    assert _gate(store, VICTIM, "master") is False


async def test_legacy_journal_with_no_intent_stays_readable(store):
    """A journal written before #67 has neither an intent entry nor an owner
    record. The documented restart-replay compat must survive: the reader
    cannot tell it from a lost write by disk state alone, which is exactly
    why the in-memory intent exists."""
    j = await store.open(VICTIM)  # no key_id — the pre-#67 open() shape
    await j.append(1, b"data: legacy\n\n")
    await j.finish(1)
    store.release(VICTIM)

    assert store.owner_of(VICTIM) is None
    assert _gate(store, VICTIM, "kid-anyone") is True, \
        "pre-scoping journals must stay replayable after a restart"


async def test_intent_outlives_release_and_is_reclaimed_by_sweep(
        store, owner_write_fails):
    """``release`` runs on every normal stream completion, so clearing the
    intent there would re-open the hole for the whole replay window. The
    intent must survive until ``sweep`` unlinks the file it describes — which
    is also what bounds the map."""
    await store.open(VICTIM, key_id="kid-A")
    store.release(VICTIM)  # sync — it just drops the active entry

    assert VICTIM in store._owner_intent, \
        "a finished journal is still replayable, so it still needs its gate"
    assert _gate(store, VICTIM, "kid-B") is False

    # Sweep past the TTL reclaims it, keeping the map bounded by the journals
    # actually on disk.
    await store.sweep_async(now=time.time() + store.ttl_s + 1)
    assert VICTIM not in store._owner_intent


async def test_sweep_never_mutates_intent_off_the_event_loop(store):
    """``sweep_forever`` hands the filesystem work to a worker thread, and a
    plain dict has no cross-thread safety — mutating ``_owner_intent`` there
    could drop a live intent (re-opening the fail-open) or corrupt the dict.
    The sync ``sweep`` must therefore not touch it at all."""
    j = await store.open(VICTIM, key_id="kid-A")
    await j.append(1, b"data: a\n\n")
    await j.finish(1)
    store.release(VICTIM)

    store.sweep(now=time.time() + store.ttl_s + 1)  # sync entry point

    assert VICTIM in store._owner_intent, \
        "sync sweep reclaimed an intent; the async path owns that mutation"
    assert _gate(store, VICTIM, "kid-B") is False


async def test_sweep_async_reclaims_only_unlinked_intents(store, owner_write_fails):
    """A live journal's intent survives a sweep that expired other files."""
    live = "aaaaaaaaaaaaaaaa"
    stale = "bbbbbbbbbbbbbbbb"
    await store.open(live, key_id="kid-A")
    old = await store.open(stale, key_id="kid-B")
    await old.append(1, b"data: b\n\n")
    await old.finish(1)
    store.release(stale)

    # Age only the stale journal past the TTL.
    import os
    p = store.path_for(stale)
    old_ts = time.time() - store.ttl_s - 10
    os.utime(p, (old_ts, old_ts))

    await store.sweep_async()

    assert stale not in store._owner_intent
    assert live in store._owner_intent
    assert _gate(store, live, "kid-B") is False
