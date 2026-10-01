"""ResponseStore unit tests (spec B): key scoping, TTL, sweep, delete."""

import asyncio
import time

import sqlalchemy.ext.asyncio as saa

from wiwi.server.response_store import ResponseStore


async def _store(ttl_s: float = 3600.0) -> ResponseStore:
    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    store = ResponseStore(engine, ttl_s)
    await store.startup()
    return store


async def test_put_then_get_returns_the_stored_items():
    store = await _store()
    await store.put("resp_1", "keyA", "grp",
                    [{"type": "message", "role": "user", "content": "hi"}],
                    {"id": "resp_1", "object": "response", "status": "completed"})
    got = await store.get("resp_1", "keyA")
    assert got is not None
    assert got.key_id == "keyA"
    assert got.model_group == "grp"
    assert got.input_items[0]["content"] == "hi"
    assert got.output["status"] == "completed"


async def test_another_key_cannot_read_the_row():
    store = await _store()
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1"})
    assert await store.get("resp_1", "keyB") is None


async def test_master_may_read_any_row():
    store = await _store()
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1"})
    assert await store.get("resp_1", "master") is not None


async def test_zero_ttl_disables_expiry():
    # ttl_s <= 0 is the documented "keep forever" switch: neither the read-time
    # check nor the sweeper may drop the row.
    store = await _store(ttl_s=0.0)
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1"})
    assert (await store.get("resp_1", "keyA")) is not None
    assert await store.sweep(now=time.time() + 10**9) == 0
    assert (await store.get("resp_1", "keyA")) is not None


async def test_expired_row_is_not_returned_after_the_ttl_elapses():
    store = await _store(ttl_s=0.05)
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1"})
    assert (await store.get("resp_1", "keyA")) is not None
    await asyncio.sleep(0.06)
    assert await store.get("resp_1", "keyA") is None


async def test_sweep_removes_expired_rows():
    store = await _store(ttl_s=3600.0)
    await store.put("resp_old", "keyA", "grp", [], {"id": "resp_old"})
    removed = await store.sweep(now=time.time() + 7200)
    assert removed == 1
    assert await store.get("resp_old", "keyA") is None


async def test_sweep_keeps_live_rows():
    store = await _store(ttl_s=3600.0)
    await store.put("resp_live", "keyA", "grp", [], {"id": "resp_live"})
    assert await store.sweep() == 0
    assert await store.get("resp_live", "keyA") is not None


async def test_delete_is_key_scoped():
    store = await _store()
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1"})
    assert await store.delete("resp_1", "keyB") is False
    assert await store.get("resp_1", "keyA") is not None
    assert await store.delete("resp_1", "keyA") is True
    assert await store.get("resp_1", "keyA") is None


async def test_put_overwrites_the_same_id():
    store = await _store()
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1", "status": "completed"})
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1", "status": "incomplete"})
    got = await store.get("resp_1", "keyA")
    assert got is not None and got.output["status"] == "incomplete"


async def test_get_missing_row_is_none():
    store = await _store()
    assert await store.get("resp_absent", "keyA") is None
