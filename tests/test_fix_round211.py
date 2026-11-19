"""Round 211 — implicit lifecycle state must be observable, never breaking.

The gateway encodes several lifecycles as loose flags. This round declares them
once in ``wiwi.state_machines`` and wires only the low-frequency response store
as an observer. The contract pinned here:

* a legal transition advances the tracked state and records history;
* an illegal event is counted/logged and leaves the state unchanged — it never
  raises into a serving path;
* the response store's put/expire/delete paths report through the machine;
* derived-state helpers read the existing authority fields without mutating
  them.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import sqlalchemy.ext.asyncio as saa

from wiwi.server.response_store import ResponseStore
from wiwi.state_machines import (
    BUDGET,
    MACHINES,
    OAUTH,
    RESPONSE,
    StateMachine,
    Transition,
    budget_state,
    deployment_state,
    oauth_state,
    response_state,
    snapshot_all,
)


def test_machine_legal_and_illegal_transitions_never_raise():
    m = StateMachine("test", ["a", "b", "c"], "a",
                     [Transition("a", "go", "b")])
    assert m.fire("go", instance="x") == "b"
    assert m.state("x") == "b"
    assert m.fire("nope", instance="x") == "b"
    assert m.state("x") == "b"
    assert m.illegal == 1
    assert m.explain("x")["history"] == [
        {"from": "a", "event": "go", "to": "b"}]


def test_machine_instance_cap_stays_bounded():
    m = StateMachine("bounded", ["a", "b"], "a",
                     [Transition("a", "go", "b")], max_instances=2)
    for i in range(10):
        m.fire("go", instance=f"i{i}")
    assert len(m.snapshot()) <= 2


async def test_response_store_reports_lifecycle():
    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    store = ResponseStore(engine, ttl_s=0.01)
    await store.startup()
    try:
        await store.put("resp_state_1", "keyA", "grp", [], {"id": "resp_state_1"})
        assert RESPONSE.state("resp_state_1") == "stored"
        await asyncio.sleep(0.03)
        assert await store.get("resp_state_1", "keyA") is None
        assert RESPONSE.state("resp_state_1") == "expired"
        assert await store.delete("resp_state_1", "keyA") is True
        assert RESPONSE.state("resp_state_1") == "deleted"
    finally:
        await engine.dispose()


def test_derived_states_read_authority_fields():
    dep = SimpleNamespace(cooldown_until=0.0, probation=False, fails=[])
    assert deployment_state(dep, now=100.0) == "ready"
    dep.cooldown_until = 200.0
    assert deployment_state(dep, now=100.0) == "cooling"
    dep.cooldown_until = 0.0
    dep.probation = True
    assert deployment_state(dep, now=100.0) == "probation"
    dep.fails = [95.0]
    dep.probation = False
    assert deployment_state(dep, now=100.0) == "degraded"

    assert budget_state(SimpleNamespace(disabled=True)) == "disabled"
    assert budget_state(SimpleNamespace(budget_reserved=2.0)) == "reserved"
    assert budget_state(SimpleNamespace(max_budget=10.0, spend_to_date=10.0)) == "exhausted"
    assert budget_state(SimpleNamespace(max_budget=10.0, spend_to_date=1.0)) == "idle"

    assert oauth_state(None) == "absent"
    assert oauth_state({"created_at": 100.0, "ttl_s": 10.0}, now=105.0) == "pending"
    assert oauth_state({"created_at": 100.0, "ttl_s": 10.0}, now=111.0) == "expired"

    assert response_state(created_at=100.0, ttl_s=10.0, now=105.0) == "stored"
    assert response_state(created_at=100.0, ttl_s=10.0, now=111.0) == "expired"
    assert response_state(created_at=1.0, ttl_s=0.0, now=10**9) == "stored"


def test_catalog_snapshot_covers_every_authority():
    names = set(MACHINES)
    assert {"deployment", "key", "budget", "oauth", "response",
            "journal", "healer_probe", "session", "config_overlay"} <= names
    snap = snapshot_all()
    assert set(snap) == names
    assert BUDGET.explain()["state"] == "idle"
    assert OAUTH.explain()["state"] == "idle"
