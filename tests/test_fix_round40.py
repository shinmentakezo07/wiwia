"""Round-40 regression tests: runtime journal TTL sweep (task #9).

AUDIT addendum finding: journals were swept only at startup —
``server/app.py``'s lifespan comment even said "the sweep is not run again
while serving" — while ``tape_store.py``'s docstring promised a periodic
background timer. A long-lived server therefore accumulated expired
journals until restart; the TTL was never enforced after startup.

Fixes under test:
- ``JournalStore.sweep_forever()`` / ``start()`` / ``stop()`` — the
  background sweeper, same worker convention as ClineAutoRefresh.
- The lifespan in ``server/app.py`` starts it when journaling is enabled
  and stops it at shutdown.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.streaming.tape_store import JournalStore


@pytest.fixture
def store(tmp_path):
    return JournalStore(tmp_path / "js", ttl_s=600, max_bytes=1 << 20)


def _mk_journal(store: JournalStore, rid: str, age_s: float) -> None:
    """Write a journal record with a faked mtime *age_s* in the past."""
    j = store.path_for(rid)
    j.parent.mkdir(parents=True, exist_ok=True)
    j.write_bytes(b'{"seq": 1, "ts": 0, "data": "aGk=", "done": false}\n')
    old = time.time() - age_s
    os.utime(j, (old, old))


# ---------------------------------------------------------------------------
# 1. The sweeper body expires journals while running
# ---------------------------------------------------------------------------

async def test_sweep_forever_removes_expired_keeps_fresh(tmp_path):
    s = JournalStore(tmp_path / "js", ttl_s=2, max_bytes=1 << 20)
    _mk_journal(s, "stale", age_s=3600)   # long past TTL
    _mk_journal(s, "fresh", age_s=0)     # just written
    task = asyncio.create_task(s.sweep_forever(interval_s=0.05))
    # The sweeper sleeps first, then sweeps; wait past one interval + margin.
    deadline = time.monotonic() + 2.0
    while (s.path_for("stale")).exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert not s.path_for("stale").exists(), \
        "sweeper must remove a journal older than TTL"
    assert s.path_for("fresh").exists(), \
        "sweeper must not remove a journal inside TTL"


# ---------------------------------------------------------------------------
# 2. start/stop lifecycle
# ---------------------------------------------------------------------------

async def test_start_stop_lifecycle(tmp_path):
    s = JournalStore(tmp_path / "js", ttl_s=1, max_bytes=1 << 20)
    _mk_journal(s, "stale", age_s=3600)
    s.start(interval_s=0.05)
    assert s._sweeper is not None and not s._sweeper.done()
    # starting twice must not spawn a second task
    first = s._sweeper
    s.start(interval_s=0.05)
    assert s._sweeper is first
    await s.stop()
    assert s._sweeper is None
    # stop is idempotent
    await s.stop()
    # after stop, no more sweeping happens
    _mk_journal(s, "stale2", age_s=3600)
    await asyncio.sleep(0.2)
    assert s.path_for("stale2").exists()


# ---------------------------------------------------------------------------
# 3. The lifespan actually runs the sweeper (integration)
# ---------------------------------------------------------------------------

def _config(tmp: Path) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(
            stream_journal_dir=str(tmp / "journals"),
            stream_journal_ttl_s=600.0,
        ),
    )


async def test_lifespan_starts_and_stops_sweeper(tmp_path):
    from asgi_lifespan import LifespanManager

    from wiwi.server.app import create_app
    app = create_app(_config(tmp_path))
    async with LifespanManager(app):
        state = app.state.wiwi
        assert state.journals is not None
        assert state.journals._sweeper is not None, \
            "lifespan must start the journal sweeper when journaling is on"
        assert not state.journals._sweeper.done()
    # after lifespan exit the sweeper task is cancelled
    assert state.journals._sweeper is None


async def test_lifespan_skips_sweeper_when_journaling_disabled(tmp_path):
    from asgi_lifespan import LifespanManager

    from wiwi.server.app import create_app
    cfg = _config(tmp_path)
    cfg.router_settings.stream_journal_enabled = False
    app = create_app(cfg)
    async with LifespanManager(app):
        state = app.state.wiwi
        # store still exists (replay of pre-existing journals stays possible)
        assert state.journals is not None
        # but the background sweeper is not running
        assert state.journals._sweeper is None
