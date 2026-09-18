"""Round 85 (the round-68 tests, renumbered): the legacy model-id fallback corrupts ids for slash-bearing groups.

``_model_id_from_attempt`` recovers a provider-native model id from a legacy
attempt's ``"<group>/<model_id>"`` deployment string by stripping the FIRST
``/``-segment. That is only correct when the *group* half contains no ``/``.

A model group whose name contains a slash is normal here — the shipped
``wiwi.yaml`` uses ``model_name: stealth/ox-alpha`` and
``model_name: minimax/minimax-m3``, and the project's own round-65 fixtures
use ``stealth/ox-alpha`` / ``vendor/ox-alpha``. For those groups the strip
removes only the group's *first* segment and leaves the rest of the group
glued to the front of the model id:

    group="minimax/minimax-m3", model_id="MiniMax-M3"
    deployment="minimax/minimax-m3/MiniMax-M3"
    recovered ="minimax-m3/MiniMax-M3"      <-- wrong

The corrupted id matches no price row, so the rollup stores it unpriced and
the row's cost stays 0 forever (it can never be repriced, because
``reprice_unpriced_history`` matches on the real model id).

Round 65 fixed truncation of slash-bearing *model ids*; this is the same
truncation class on slash-bearing *groups*, and it is the more common shape
because group names in the shipped config carry slashes while the underlying
native ids mostly do not.
"""

from __future__ import annotations

import time

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent

# The real shipped shape: group and model id BOTH carry a slash, and they are
# not equal — the case the round-65 fixtures miss (there MODEL_A_GROUP ==
# MODEL_A_ID == "stealth/ox-alpha", so a corrupted id never collides with the
# sibling and the conflation assertion passes vacuously).
GROUP = "minimax/minimax-m3"
MODEL_ID = "MiniMax-M3/air"


@pytest.fixture
async def sink():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    s = DBSink(engine)
    await s.startup()
    yield s
    await engine.dispose()


def _legacy_row(tag: str, group: str, model_id: str) -> LogEvent:
    """A cost-0 row written before attempts recorded ``model_id``."""
    return LogEvent(
        stream="request", ts=time.time(), status=200, key_id=f"key-{tag}",
        model_group=group, provider="openai-compatible",
        tok_in=1_000_000, tok_out=0, cost=0.0,
        attempts=[{"deployment": f"{group}/{model_id}",
                   "provider": "openai-compatible",
                   "provider_key_label": "k", "status": "ok", "latency_ms": 1}],
    )


async def test_legacy_fallback_keeps_full_id_for_slash_bearing_group(sink):
    """A slash-bearing group must not leak its tail into the recovered id."""
    got = DBSink._model_id_from_attempt({"deployment": f"{GROUP}/{MODEL_ID}"},
                                        GROUP)
    assert got == MODEL_ID, (
        f"recovered model id {got!r} for group {GROUP!r} + model {MODEL_ID!r}; "
        "stripping the first '/' segment leaves the rest of the slash-bearing "
        "group glued to the front of the id")


async def test_deployment_with_no_group_prefix_is_the_whole_model_id(sink):
    """A deployment with no "/" at all is already the model id.

    A guard test, not a regression test: the pre-fix code returned the same
    value here (``split("/")[-1]`` on a slash-free string is the string), so
    this pins the branch that the fix introduced rather than proving a defect.
    """
    assert DBSink._model_id_from_attempt({"deployment": "priced-model"},
                                         "some-group") == "priced-model"


async def test_group_that_is_a_string_prefix_but_not_a_path_boundary(sink):
    """Group "A" must not strip from deployment "AB/x" — the boundary is "/".

    Guard test: the pre-fix code also handled this (it compared
    ``f"{group}/"`` too). Pinned so the exact-strip branch cannot regress into
    a bare ``startswith(group)``.

    A bare ``startswith(group)`` would strip ``len("A") + 1 == 2`` characters
    from ``"AB/x"`` and return ``"/x"`` — a value naming no model, which is the
    same class of corruption as the slash-bearing-group defect above. Both
    correct branches (exact-strip and first-segment fallback) happen to agree
    here, so this pins the boundary rather than the branch.
    """
    assert DBSink._model_id_from_attempt({"deployment": "AB/x"}, "A") == "x"


async def test_rollup_stores_the_real_id_for_a_slash_bearing_group(sink):
    """End to end: the rollup must store the model's real id, not a glued one.

    ``serving_model`` is the value the console groups by and the key retroactive
    pricing matches on. Pricing happens to survive a corrupted value because
    ``_shares_model_tail`` still finds the real id among the corrupted string's
    slash-tails — but the stored dimension is what other readers consume, so it
    must be the real id.
    """
    await sink.write_requests([_legacy_row("A", GROUP, MODEL_ID)])
    await sink.rollup_and_prune(cutoff_ts=time.time() + 3600)

    async with sink.engine.connect() as conn:
        stored = (await conn.execute(sa.text(
            "SELECT serving_model FROM request_rollups"))).scalar()

    assert stored == MODEL_ID, (
        f"rollup stored serving_model={stored!r} — the true model id is "
        f"{MODEL_ID!r}; the slash-bearing group's tail was left glued to the front")


async def test_legacy_row_does_not_split_the_rollup_dimension(sink):
    """A legacy row must land in the SAME rollup bucket as a modern one.

    ``serving_model`` is part of the rollup's unique key
    (``bucket_ts, key_id, model_group, provider, serving_model``), so a
    corrupted value does not just mislabel the row — it creates a *second*
    bucket for the same model in the same window. The console then shows one
    model twice, once under a name that is not a real model id.

    Note this is the damage that survives: ``reprice_unpriced_history`` still
    matches via ``_shares_model_tail``, so retroactive pricing recovers. The
    stored dimension is what stays wrong.
    """
    modern = _legacy_row("A", GROUP, MODEL_ID)
    modern.attempts[0]["model_id"] = MODEL_ID      # the shape written today
    await sink.write_requests([_legacy_row("A", GROUP, MODEL_ID), modern])
    await sink.rollup_and_prune(cutoff_ts=time.time() + 3600)

    async with sink.engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT serving_model FROM request_rollups"))).all()

    stored = sorted(r[0] for r in rows)
    assert stored == [MODEL_ID], (
        f"one model produced {len(stored)} rollup buckets {stored!r} — the "
        f"legacy row's serving_model was corrupted and split the dimension")
