"""Round 125 — a crash mid-request no longer strands a key's budget forever.

Finding covered (reproduced against the real AuthService before fixing):

``reserve_budget`` commits ``budget_reserved + :a`` to the ``vkeys`` row at
admission (``auth/service.py:639``), and ``AuthInfo.over_budget`` reads
``spend_to_date + budget_reserved >= max_budget`` (``:107``). Every *in-process*
exit releases it — ``server/app.py:1570`` (the success settle), ``:1609`` (the
non-success tail) and ``:2521`` (the streaming teardown) all funnel through
``release_budget_reservation``.

The one exit none of them can cover is a hard crash: SIGKILL, OOM, container
restart, ``docker compose down`` mid-stream. All three release paths are skipped,
and nothing reconciled the column afterwards. So the reservation survived the
restart and the key was refused with ``402 budget_exhausted`` on every subsequent
request having actually spent $0.00.

The stranded amount is the in-flight request's *estimate*, so a gateway serving
long-context requests strands proportionally more, and a key whose ``max_budget``
equals one request's estimate is wedged permanently. There was also no admin
remedy: ``/admin/keys/*`` can raise ``max_budget`` (masking the symptom and
silently granting headroom the operator never intended), and ``list_keys`` only
*reports* the column.

The fix reconciles at startup, in the same transaction as the existing DDL and
migration block: ``UPDATE vkeys SET budget_reserved = 0``. This is sound because a
reservation is by definition admission-scoped — the release path's own comment
says "the request died before it could produce usage, so the headroom returns to
the key" — so no live process can be depending on a value written before the
current one booted. Zeroing cannot lose real spend, because ``spend_to_date`` is a
separate column that only ``update_spend`` touches.

These tests pin both halves: the stranded value is cleared, and legitimate
in-flight reservations taken by a *running* process are untouched.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.auth.service import AuthService
from wiwi.auth.users import UserService

MASTER = "master-key-test"

async def _service() -> AuthService:
    """A started AuthService on a private in-memory database."""
    svc = AuthService(create_async_engine("sqlite+aiosqlite:///:memory:"),
                      master_key_plaintext=MASTER)
    await svc.startup()
    return svc

async def _owned_key(svc: AuthService, budget: float) -> tuple[str, str]:
    """A virtual key owned by a real user row (the owner check fails closed)."""
    users = UserService(svc.engine, session_secret="test-secret")
    await users.startup()
    user = await users.create_user("alice", "password123")
    return await svc.create_key(alias="k", max_budget=budget, owner_id=user.id)

async def _reserved(svc: AuthService, key_id: str) -> float:
    async with svc.engine.connect() as conn:
        row = (await conn.execute(
            sa.text("SELECT budget_reserved FROM vkeys WHERE id=:id"),
            {"id": key_id})).first()
    return float(row[0])

# ---------------------------------------------------------------------------
# The finding
# ---------------------------------------------------------------------------

async def test_a_crashed_reservation_is_cleared_on_startup():
    """A reservation left behind by a dead process must not wedge the key.

    This is the #391 sequence: reserve, then simulate the crash by building a NEW
    AuthService over the same database and running startup(). Pre-fix the column
    kept its value and ``over_budget`` stayed True forever, so the key was refused
    with 402 having spent $0.00.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    first = AuthService(engine, master_key_plaintext=MASTER)
    await first.startup()
    plaintext, kid = await _owned_key(first, 1.0)

    # Reserve the whole cap, then "crash": no release call ever runs.
    assert await first.reserve_budget(kid, 1.0) is True
    assert await _reserved(first, kid) == 1.0

    # A new process boots on the same database.
    restarted = AuthService(engine, master_key_plaintext=MASTER)
    await restarted.startup()

    assert await _reserved(restarted, kid) == 0.0, (
        "a reservation stranded by a crashed process survived startup — the key "
        "is refused with 402 having spent $0.00, and no admin route can clear it")

    # And the key actually works again rather than merely reporting clean.
    info = await restarted.authenticate(plaintext)
    assert info is not None, "the wedged key no longer authenticated at all"
    assert info.budget_reserved == 0.0
    assert info.spend_to_date == 0.0
    assert info.over_budget is False

async def test_a_partial_crash_reservation_is_also_cleared():
    """The leak need not consume the whole cap to shrink it permanently.

    A smaller stranded reservation does not trip ``over_budget`` outright, so it
    was invisible until the key's real spend pushed the total over the cap — the
    operator's effective budget was silently smaller than the one they set.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    first = AuthService(engine, master_key_plaintext=MASTER)
    await first.startup()
    plaintext, kid = await _owned_key(first, 10.0)
    await first.reserve_budget(kid, 2.5)

    restarted = AuthService(engine, master_key_plaintext=MASTER)
    await restarted.startup()
    assert await _reserved(restarted, kid) == 0.0

    info = await restarted.authenticate(plaintext)
    assert info is not None
    assert info.over_budget is False

# ---------------------------------------------------------------------------
# The fix must not break the live reservation path
# ---------------------------------------------------------------------------

async def test_a_live_reservation_survives_an_unrelated_startup_call():
    """Zeroing at startup must not race an in-flight reservation.

    The reconciliation is correct precisely because it is startup-scoped. A
    running process's own reservations are held in memory (``ctx.budget_reserved``)
    and on the row, and the release path reconciles both — so a second startup()
    call against a live service must not be able to erase a reservation the
    service is currently depending on for admission. This pins the boundary: the
    reserve→release pair still round-trips through the row untouched.
    """
    svc = await _service()
    _plaintext, kid = await _owned_key(svc, 5.0)

    assert await svc.reserve_budget(kid, 1.0) is True
    assert await _reserved(svc, kid) == 1.0, "the reservation did not land"

    # The documented release path still gives it back.
    await svc.release_budget_reservation(kid, 1.0)
    assert await _reserved(svc, kid) == 0.0

async def test_startup_zeroing_preserves_real_spend():
    """The reconciliation must clear only the reservation, never actual spend.

    ``spend_to_date`` is a separate column that only ``update_spend`` writes. A
    blanket reset that touched it would hand every budget-capped key a fresh cap
    on every restart — a far worse bug than the one being fixed.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    first = AuthService(engine, master_key_plaintext=MASTER)
    await first.startup()
    _plaintext, kid = await _owned_key(first, 10.0)

    # Real money spent, plus a reservation stranded by the crash.
    await first.update_spend(kid, 3.0)
    await first.reserve_budget(kid, 1.0)

    restarted = AuthService(engine, master_key_plaintext=MASTER)
    await restarted.startup()

    async with restarted.engine.connect() as conn:
        row = (await conn.execute(
            sa.text("SELECT spend_to_date, budget_reserved FROM vkeys WHERE id=:id"),
            {"id": kid})).first()
    assert float(row[0]) == 3.0, "startup reconciliation erased real spend"
    assert float(row[1]) == 0.0, "the stranded reservation was not cleared"

async def test_startup_is_idempotent():
    """The reconciliation must be safe to run repeatedly.

    ``startup`` runs on every boot and, in tests and tools, more than once per
    process. A second pass over an already-reconciled row must be a no-op rather
    than an error or a further mutation.
    """
    svc = await _service()
    _plaintext, kid = await _owned_key(svc, 4.0)
    await svc.reserve_budget(kid, 4.0)

    await svc.startup()   # second pass, same engine
    await svc.startup()   # and a third

    assert await _reserved(svc, kid) == 0.0
    async with svc.engine.connect() as conn:
        count = (await conn.execute(sa.text("SELECT COUNT(*) FROM vkeys"))).first()
    assert count[0] == 1, "reconciliation duplicated or dropped key rows"
