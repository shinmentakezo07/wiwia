"""Persisted Responses-API state: ``store`` + ``previous_response_id``.

The Responses surface is stateful server-side, but wiwi re-encodes every response
and hands the client its own ``resp_<request-id>`` (``openai_responses.py:408``) —
an id the upstream has never seen. So wiwi owns the state: a completed response is
saved here, and a later request carrying our id is answered from here and translated
back into input items before the next hop.

A response id is **not** a capability token: every read and delete is scoped to the
presenting key, and only the master key may cross that boundary. Expiry is enforced
on read as well as by the sweeper, so a row past its TTL is never served just because
the sweeper has not run yet.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, NamedTuple

import orjson
import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

log = structlog.get_logger()

RESPONSE_DDL = """
CREATE TABLE IF NOT EXISTS stored_responses (
  id            TEXT PRIMARY KEY,
  created_at    DOUBLE PRECISION NOT NULL,
  key_id        TEXT NOT NULL,
  surface       TEXT NOT NULL DEFAULT 'responses',
  model_group   TEXT NOT NULL DEFAULT '',
  input_json    TEXT NOT NULL,
  output_json   TEXT NOT NULL
);
"""

INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_stored_responses_created
  ON stored_responses (created_at);
"""


class StoredResponse(NamedTuple):
    id: str
    key_id: str
    model_group: str
    input_items: list[dict[str, Any]]
    output: dict[str, Any]


class ResponseStore:
    """Key-scoped store for Responses-API state, SQLite and PostgreSQL alike."""

    def __init__(self, engine: AsyncEngine, ttl_s: float) -> None:
        self.engine = engine
        self.ttl_s = ttl_s
        self._sweeper: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(RESPONSE_DDL))
            await conn.execute(sa.text(INDEX_DDL))

    async def put(self, resp_id: str, key_id: str, model_group: str,
                  input_items: list[dict[str, Any]],
                  output: dict[str, Any]) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO stored_responses"
                " (id, created_at, key_id, surface, model_group,"
                "  input_json, output_json)"
                " VALUES (:id, :ts, :key, 'responses', :grp, :inp, :out)"
                " ON CONFLICT (id) DO UPDATE SET"
                " created_at = :ts, key_id = :key, model_group = :grp,"
                " input_json = :inp, output_json = :out"),
                {"id": resp_id, "ts": time.time(), "key": key_id, "grp": model_group,
                 "inp": orjson.dumps(input_items).decode(),
                 "out": orjson.dumps(output).decode()})

    async def get(self, resp_id: str, key_id: str) -> StoredResponse | None:
        async with self.engine.begin() as conn:
            row = (await conn.execute(sa.text(
                "SELECT id, created_at, key_id, model_group, input_json, output_json"
                " FROM stored_responses WHERE id = :id"), {"id": resp_id})).first()
        if row is None:
            return None
        # Expiry on read, not only in the sweeper: a row past its TTL must never
        # be served just because the sweeper has not run yet.
        if self.ttl_s > 0 and time.time() - row[1] > self.ttl_s:
            return None
        if key_id != "master" and row[2] != key_id:
            return None  # another key's transcript: indistinguishable from absent
        return StoredResponse(id=row[0], key_id=row[2], model_group=row[3],
                              input_items=orjson.loads(row[4]),
                              output=orjson.loads(row[5]))

    async def delete(self, resp_id: str, key_id: str) -> bool:
        async with self.engine.begin() as conn:
            if key_id == "master":
                res = await conn.execute(sa.text(
                    "DELETE FROM stored_responses WHERE id = :id"), {"id": resp_id})
            else:
                res = await conn.execute(sa.text(
                    "DELETE FROM stored_responses WHERE id = :id AND key_id = :key"),
                    {"id": resp_id, "key": key_id})
        return (res.rowcount or 0) > 0

    async def sweep(self, now: float | None = None) -> int:
        if self.ttl_s <= 0:
            return 0
        cutoff = (now if now is not None else time.time()) - self.ttl_s
        async with self.engine.begin() as conn:
            res = await conn.execute(sa.text(
                "DELETE FROM stored_responses WHERE created_at < :cutoff"),
                {"cutoff": cutoff})
        return res.rowcount or 0

    async def sweep_forever(self, interval_s: float = 300.0) -> None:
        """Background sweeper body; see :meth:`start` / :meth:`stop`."""
        while True:
            await asyncio.sleep(interval_s)
            try:
                removed = await self.sweep()
            except Exception:  # the sweeper must never die
                log.warning("stored_responses_sweep_failed", exc_info=True)
                continue
            if removed:
                log.info("stored_responses_swept", removed=removed)

    def start(self, interval_s: float = 300.0) -> None:
        """Start the background TTL sweeper task (idempotent)."""
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self.sweep_forever(interval_s))

    async def stop(self) -> None:
        """Cancel the background sweeper started by :meth:`start`."""
        task, self._sweeper = self._sweeper, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
