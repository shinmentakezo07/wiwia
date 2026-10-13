"""Round 135: a realtime session must actually charge the virtual key.

Bug: ``_bill_realtime_session`` priced a realtime session, settled the
deployment's token counters, and wrote the request-log row — but charged the
key by calling ``state.auth.record_spend(...)``, a method ``AuthService`` never
had (its real surface is ``update_spend`` / ``apply_spend_trueup``). Every call
raised ``AttributeError``, and the surrounding ``contextlib.suppress(Exception)``
swallowed it with no log, no counter and no /health field. So an $11 session on
a $1-capped key was priced, logged and shown in the admin console while
``spend_to_date`` stayed at $0 — the budget cap never tripped (AUDIT #345).

The only reason this survived is the shape of the mistake: a typo'd method on a
well-typed object, hidden by a blanket ``suppress``. ``record_spend`` exists,
but as the nested closure inside ``create_app`` — never as an ``AuthService``
method — so nothing caught the wrong receiver at import or type-check time.

Fix: charge via ``state.auth.apply_spend_trueup`` (the unconditional true-up
``record_spend`` itself applies for an over-cap request — the upstream really
served these tokens, so the cap must record them and only block FUTURE
requests), with a counted, logged failure instead of a silent suppress.

These tests call ``_bill_realtime_session`` directly against a real
``AuthService`` over in-memory SQLite, because that is where the broken charge
lived; the WS relay itself is covered by ``test_realtime.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.auth.service import AuthService
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.router.router import Router
from wiwi.server.app import _bill_realtime_session

MASTER = "sk-wiwi-master-realtime-billing"
MODEL_KEY = "openai/gpt-4o-realtime"


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-up-abcdef123456")])],
        model_list=[ModelEntry(model_name="m",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o-realtime"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(),
    )


class _NoopLogs:
    def log_request(self, *_a, **_k) -> None:
        pass


@dataclass
class _State:
    """Minimal AppState stand-in: only what _bill_realtime_session reads."""

    auth: AuthService
    cost: CostEngine
    logs: _NoopLogs = field(default_factory=_NoopLogs)
    spend_charge_failures: int = 0


async def _make_auth():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    auth = AuthService(engine, MASTER)
    await auth.startup()
    return engine, auth


async def _make_key(auth, *, max_budget=None) -> tuple[str, str]:
    """Create a key and return ``(plaintext, key_id)``.

    The plaintext is what ``authenticate`` needs to mint the AuthInfo the real
    route hands the biller.
    """
    return await auth.create_key(alias="rt", max_budget=max_budget)


async def _spend(auth, key_id: str) -> float:
    row = await auth.get_key(key_id)
    return float(row["spend_to_date"]) if row else -1.0


async def _make_dep_and_ctx():
    config = _config()
    router = Router(config)
    dep = router.groups["m"][0]
    ctx = RequestContext(surface="chat", ir_req=ir.Request(
        model="m", messages=[], gen_params=ir.GenParams()),
        group="m", request_id="rt-req-1")
    return dep, ctx


# -- the core regression ------------------------------------------------------


@pytest.mark.asyncio
async def test_realtime_session_charges_the_virtual_key():
    """A session must move spend_to_date (AUDIT #345).

    Before the fix this was exactly the probe in AUDIT #345: the session was
    priced and logged, but the charge raised AttributeError inside a blanket
    ``suppress`` and spend_to_date stayed at 0.
    """
    engine, auth = await _make_auth()
    try:
        # A budget-capped key so we can prove the cap observes spend.
        plaintext, key_id = await _make_key(auth, max_budget=1.0)
        # authenticate() returns the AuthInfo the real route hands the
        # biller — .key_id is what the charge keys on.
        info = await auth.authenticate(plaintext)

        cost_engine = CostEngine()
        cost_engine.register(MODEL_KEY, 0.001, 0.001)
        state = _State(auth=auth, cost=cost_engine)
        dep, ctx = await _make_dep_and_ctx()

        assert await _spend(auth, key_id) == 0.0

        # 1000 in + 500 out at 0.001/token = $1.50, past the $1.00 cap — the
        # charge is unconditional, exactly like record_spend's over-cap true-up:
        # the upstream really served these tokens, so the cap records them and
        # only blocks FUTURE requests.
        await _bill_realtime_session(state, info, ctx, dep, (1000, 500))

        assert await _spend(auth, key_id) == pytest.approx(1.5)
        assert ctx.cost == pytest.approx(1.5)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_realtime_session_uses_peak_usage_not_sum():
    """The charge prices the peak tuple once, as the relay returns it."""
    engine, auth = await _make_auth()
    try:
        plaintext, key_id = await _make_key(auth)
        info = await auth.authenticate(plaintext)
        cost_engine = CostEngine()
        cost_engine.register(MODEL_KEY, 0.001, 0.001)
        state = _State(auth=auth, cost=cost_engine)
        dep, ctx = await _make_dep_and_ctx()

        await _bill_realtime_session(state, info, ctx, dep, (10, 5))
        # 15 tokens at 0.001 = $0.015, priced once.
        assert await _spend(auth, key_id) == pytest.approx(0.015)
    finally:
        await engine.dispose()


# -- the failure path is loud, not silent -------------------------------------


@pytest.mark.asyncio
async def test_realtime_charge_failure_is_counted_not_silent():
    """A charge that cannot be written is counted, and the session still
    completes (AUDIT #179/#345).

    The bug's accomplice was ``contextlib.suppress(Exception)``: the fix keeps
    the served session alive but increments ``spend_charge_failures`` so an
    unenforced cap surfaces in /health and /metrics instead of vanishing.
    """
    engine, auth = await _make_auth()
    try:
        plaintext, _key_id = await _make_key(auth)
        info = await auth.authenticate(plaintext)
        dep, ctx = await _make_dep_and_ctx()

        class _BrokenAuth:
            async def apply_spend_trueup(self, _key_id: str, _cost: float) -> None:
                raise RuntimeError("db is locked")

        state = _State(auth=_BrokenAuth(), cost=CostEngine())

        # _bill_realtime_session must not raise (a served session is never torn
        # down by an accounting failure), and must have counted the failure.
        await _bill_realtime_session(state, info, ctx, dep, (100, 100))
        assert state.spend_charge_failures == 1
    finally:
        await engine.dispose()
