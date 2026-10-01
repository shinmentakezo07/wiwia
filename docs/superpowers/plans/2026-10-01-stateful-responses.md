# Stateful Responses API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Store completed `/v1/responses` calls and accept `previous_response_id` on the
next one, plus `GET`/`DELETE /v1/responses/{id}`.

**Architecture:** A standalone `ResponseStore` over the existing SQLAlchemy engine (its own
`CREATE TABLE IF NOT EXISTS` + TTL sweeper in the lifespan), a body-rewrite hook in the
responses codec, and two optional parameters on `run_chat_like`
(`codec_history`, `store_response`) that the other three surfaces leave at their defaults.

**Tech Stack:** Python 3.12, SQLAlchemy core (`sa.text`), SQLite/PostgreSQL, pytest +
respx + asgi-lifespan.

**Spec:** `docs/superpowers/specs/2026-10-01-stateful-responses-design.md`

## Global Constraints

- `requires-python = ">=3.11"`; ruff `line-length = 100`, `target-version = "py311"`.
- Both SQLite and PostgreSQL must work; detect the dialect via `engine.dialect.name`.
- Library code uses `structlog`, never `print`.
- Gate before claiming done: `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`.
- Never commit `wiwi.yaml`, `wiwi.db`, `.env`, `key.md`, `opencode.json(c)`, `*.har`,
  `.wiwi/`, `.verify/`.
- Tests: bare `async def test_…`, no `conftest.py`, own fixtures, respx in **decorator**
  form. Regression file: next unused `tests/test_fix_roundN.py` (verify with
  `ls tests/test_fix_round*.py`).

---

### Task 1: `ResponseStore`

**Files:**
- Create: `wiwi/server/response_store.py`
- Test: `tests/test_response_store.py`

**Interfaces:**
- Consumes: `sqlalchemy.ext.asyncio.AsyncEngine`, `sqlalchemy as sa`.
- Produces:
  - `class StoredResponse(NamedTuple)` with fields `id, key_id, model_group, input_items, output`
  - `class ResponseStore(engine: AsyncEngine, ttl_s: float)` with
    `startup()`, `put(resp_id, key_id, model_group, input_items, output)`,
    `get(resp_id, key_id)`, `delete(resp_id, key_id)`, `sweep(now=None)`,
    `sweep_forever(interval_s=300.0)`, `start(interval_s=None)`, `stop()`.

- [ ] **Step 1: Write the failing tests** (`tests/test_response_store.py`)

```python
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


async def test_expired_row_is_not_returned_even_without_a_sweep():
    store = await _store(ttl_s=0.0)
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1"})
    assert await store.get("resp_1", "keyA") is None


async def test_sweep_removes_expired_rows():
    store = await _store(ttl_s=3600.0)
    await store.put("resp_old", "keyA", "grp", [], {"id": "resp_old"})
    removed = await store.sweep(now=time.time() + 7200)
    assert removed == 1
    assert await store.get("resp_old", "keyA") is None


async def test_delete_is_key_scoped():
    store = await _store()
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1"})
    assert await store.delete("resp_1", "keyB") is False
    assert await store.delete("resp_1", "keyA") is True
    assert await store.get("resp_1", "keyA") is None


async def test_put_overwrites_the_same_id():
    store = await _store()
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1", "status": "completed"})
    await store.put("resp_1", "keyA", "grp", [], {"id": "resp_1", "status": "incomplete"})
    got = await store.get("resp_1", "keyA")
    assert got is not None and got.output["status"] == "incomplete"
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_response_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wiwi.server.response_store'`.

- [ ] **Step 3: Implement** `wiwi/server/response_store.py`

```python
"""Persisted Responses-API state: ``store`` + ``previous_response_id``.

The Responses surface is stateful server-side, but wiwi re-encodes every response
and hands the client its own ``resp_<request-id>`` (openai_responses.py:408) — an id
the upstream has never seen. So wiwi must own the state: a completed response is
saved here, and a later request carrying our id is answered from here and translated
back into input items before the next hop.

A response id is NOT a capability token: every read and delete is scoped to the
presenting key, and only the master key may cross that boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, NamedTuple

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
    def __init__(self, engine: AsyncEngine, ttl_s: float) -> None:
        self.engine = engine
        self.ttl_s = ttl_s
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(RESPONSE_DDL))
            await conn.execute(sa.text(INDEX_DDL))

    async def put(self, resp_id: str, key_id: str, model_group: str,
                  input_items: list[dict[str, Any]],
                  output: dict[str, Any]) -> None:
        import orjson
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO stored_responses"
                " (id, created_at, key_id, surface, model_group, input_json, output_json)"
                " VALUES (:id, :ts, :key, 'responses', :grp, :inp, :out)"
                " ON CONFLICT (id) DO UPDATE SET"
                " created_at = :ts, key_id = :key, model_group = :grp,"
                " input_json = :inp, output_json = :out"),
                {"id": resp_id, "ts": time.time(), "key": key_id, "grp": model_group,
                 "inp": orjson.dumps(input_items).decode(),
                 "out": orjson.dumps(output).decode()})

    async def get(self, resp_id: str, key_id: str) -> StoredResponse | None:
        import orjson
        async with self.engine.begin() as conn:
            row = (await conn.execute(sa.text(
                "SELECT id, created_at, key_id, model_group, input_json, output_json"
                " FROM stored_responses WHERE id = :id"), {"id": resp_id})).first()
        if row is None:
            return None
        # Expiry is enforced on read, not only by the sweeper: a row past its TTL
        # must never be served just because the sweeper has not run yet.
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
        while not self._stopping.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=interval_s)
                return
            with contextlib.suppress(Exception):
                n = await self.sweep()
                if n:
                    log.info("stored_responses_swept", removed=n)
            if self._stopping.is_set():
                return

    def start(self, interval_s: float | None = None) -> None:
        self._task = asyncio.create_task(
            self.sweep_forever(interval_s if interval_s is not None else 300.0))

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await self._task
            self._task = None
```

- [ ] **Step 4: Verify pass**

Run: `python3 -m pytest tests/test_response_store.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add wiwi/server/response_store.py tests/test_response_store.py
git commit -m "Add a key-scoped store for persisted Responses state"
```

---

### Task 2: Codec — accept history and rewrite the body

**Files:**
- Modify: `wiwi/wire/openai_responses.py` (the `decode_request` guard at :240)
- Modify: `tests/test_codecs.py` (append)

**Interfaces:**
- Produces: `with_history(body, previous) -> dict[str, Any]`;
  `decode_request(body, previous_output: list[dict] | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_responses_previous_output_is_decoded_into_history():
    out = [{"type": "message", "id": "msg_1", "role": "assistant",
            "content": [{"type": "output_text", "text": "prior answer"}]},
           {"type": "function_call", "id": "fc_1", "call_id": "call_1",
            "name": "lookup", "arguments": "{\"q\": 1}"}]
    req = orp.decode_request(
        {"model": "x", "previous_response_id": "resp_old", "input": "next"},
        previous_output=out)
    assert req.messages[0].parts[0].text == "prior answer"
    assert any(m.parts and getattr(m.parts[0], "name", "") == "lookup"
               for m in req.messages)


def test_responses_without_previous_output_still_refuses_the_id():
    with pytest.raises(oc.DialectError):
        orp.decode_request({"model": "x", "previous_response_id": "resp_old",
                            "input": []})


def test_with_history_merges_a_string_and_a_list_input():
    prev = [{"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "a"}]}]
    body = orp.with_history({"model": "x", "previous_response_id": "resp_1",
                             "input": "b"}, prev)
    assert body["input"][0] is prev[0]
    assert body["input"][1]["content"] == "b"
    assert "previous_response_id" not in body
    body2 = orp.with_history({"model": "x", "input": [{"type": "message"}]}, prev)
    assert len(body2["input"]) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_codecs.py -q -k "responses and (previous or history)"`
Expected: FAIL — `decode_request() got an unexpected keyword argument`.

- [ ] **Step 3: Implement**

In `wiwi/wire/openai_responses.py`, replace the guard line:

```python
    if body.get("previous_response_id"):
        raise DialectError("previous_response_id is not supported yet; send full input")
```

with:

```python
    if body.get("previous_response_id") and previous_output is None:
        # Reachable only when the caller bypasses the store (e.g. the surface
        # has state disabled). The server passes ``previous_output`` after
        # loading and key-checking the row.
        raise DialectError("previous_response_id is unknown or expired; send full input")
```

and add `previous_output: list[dict[str, Any]] | None = None` to the signature, then
prepend the decoded history to `items` right after the `items` list is built:

```python
    if previous_output:
        items = list(previous_output) + items
```

Finally add the module-level rewrite used by the surface:

```python
def with_history(body: dict[str, Any],
                 previous: list[dict[str, Any]]) -> dict[str, Any]:
    """Prepend a stored response's output items to this request's ``input``.

    ``previous_response_id`` is dropped: the caller's next hop must carry the
    literal history, because the upstream never saw our id.
    """
    merged = dict(body)
    merged.pop("previous_response_id", None)
    existing = body.get("input")
    items = list(previous)
    if isinstance(existing, str):
        items.append({"type": "message", "role": "user", "content": existing})
    elif isinstance(existing, list):
        items.extend(existing)
    merged["input"] = items
    return merged
```

- [ ] **Step 4: Verify pass**

Run: `python3 -m pytest tests/test_codecs.py -q -k "responses and (previous or history)"`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add wiwi/wire/openai_responses.py tests/test_codecs.py
git commit -m "Accept stored history on the Responses surface"
```

---

### Task 3: Store wiring, config, and the chaining path in `run_chat_like`

**Files:**
- Modify: `wiwi/config.py` (`WiwiSettings`)
- Modify: `wiwi/server/app.py` (`AppState`, lifespan, `run_chat_like`)

**Interfaces:**
- Consumes: `wiwi.server.response_store.ResponseStore` (Task 1),
  `wiwi.wire.openai_responses.with_history` (Task 2).
- Produces: `AppState.response_store: ResponseStore | None`;
  `run_chat_like(..., codec_history=None, store_response=False)`.

- [ ] **Step 1: Add the config fields**

`wiwi/config.py`, in `class WiwiSettings`, beside `store_prompts_in_spend_logs`:

```python
    # Responses-API state. With ``store_responses`` False the surface behaves as
    # it did before persistence existed: the field is accepted, nothing is saved,
    # and ``previous_response_id`` 404s.
    store_responses: bool = True
    response_store_ttl_s: float = 86400.0
```

- [ ] **Step 2: Construct and run the store**

`wiwi/server/app.py` — import beside `config_store`:

```python
from wiwi.server.response_store import ResponseStore
```

`AppState.__init__`, beside `self.config_store: ConfigStore | None = None`:

```python
        self.response_store: ResponseStore | None = None
```

In `init_db` (right after `await self.config_store.startup()`):

```python
        if config.wiwi_settings.store_responses:
            self.response_store = ResponseStore(
                aengine, config.wiwi_settings.response_store_ttl_s)
            await self.response_store.startup()
```

In the lifespan shutdown block, beside the other service stops:

```python
    if state.response_store is not None:
        await state.response_store.stop()
```

and at startup, after `init_db` builds it:

```python
    if state.response_store is not None:
        state.response_store.start()
```
(Place both exactly where the existing `healer`/journal start/stop calls live — grep
`state.healer` to find them.)

- [ ] **Step 3: Wire the chaining path**

`run_chat_like` gains the two parameters and, immediately after the body/`json_body`
callers hand in their `body` and *before* `codec_decode`, this block:

```python
        # A stored response id is only meaningful to the key that owns it, so
        # identity is established before the transcript is read (AUDIT-shaped:
        # never let an id be a capability token).
        if codec_history is not None and body.get("previous_response_id"):
            info0, err0 = await authenticate(request, str(body.get("model") or ""),
                                             surface, bearer_token=bearer_token)
            if err0:
                return err0
            store = state_.response_store
            prev = (await store.get(body["previous_response_id"], info0.key_id)
                    if store is not None else None)
            if prev is None:
                return _err(404, "not_found_error",
                            f"response '{body['previous_response_id']}' not found",
                            request, surface)
            body = codec_history(body, prev.output.get("output", []))
            if store_response:
                _reuse_info = info0  # the later authenticate() reuses the cache
```

Then, at the two places the response object is finished — the non-streaming success
path next to `ctx.metadata["response_body"] = _serialize_turn(turn, payload)`
(app.py:1836) and the stream `_teardown_tail` (app.py:1989) — add:

```python
            if store_response and state_.response_store is not None:
                if body.get("store") is not False:
                    await state_.response_store.put(
                        f"resp_{ctx.request_id}", ctx.auth.key_id if ctx.auth else "master",
                        ctx.model_group or ir_req.model, _stored_input_items(body),
                        payload)
```

with the module-level helper:

```python
def _stored_input_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The request's decoded input items, for replay on the next turn.

    ``input`` is normalised to a list so a bare string prompt replays as a
    message item; the id field is dropped because the next hop rebuilds it.
    """
    raw = body.get("input")
    if isinstance(raw, str):
        return [{"type": "message", "role": "user", "content": raw}]
    if isinstance(raw, list):
        return [i for i in raw if isinstance(i, dict)]
    return []
```

- [ ] **Step 4: Point the route at it**

```python
    @app.post("/v1/responses")
    async def responses_api(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        return await run_chat_like(request, "responses", body, orp.decode_request,
                                   orp.encode_response,
                                   codec_history=orp.with_history,
                                   store_response=True)
```

- [ ] **Step 5: Smoke it**

Run: `python3 -m pytest tests/test_codecs.py tests/test_response_store.py -q`
Expected: PASS. (The end-to-end proof is Task 4.)

- [ ] **Step 6: Commit**

```bash
git add wiwi/config.py wiwi/server/app.py
git commit -m "Persist Responses state and accept previous_response_id"
```

---

### Task 4: Retrieve and delete routes, end to end

**Files:**
- Modify: `wiwi/server/app.py` (two routes)
- Create: `tests/test_fix_round114.py` (next unused number — verify first)

- [ ] **Step 1: Write the failing tests**

```python
"""Regression round 114 — stateful Responses (spec B)."""

import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app

_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
  database_url: "sqlite+aiosqlite:///:memory:"
providers:
  - name: openai
    provider: openai
    keys: [{label: default, key: sk-upstream}]
model_list:
  - model_name: gpt-4o
    wiwi_params: {provider: openai, model: gpt-4o}
"""

_UP = "https://api.openai.com/v1/chat/completions"


def _chat(text: str) -> Response:
    return Response(200, json={
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


async def _client(headers=None):
    app = create_app(load_config_from_string(_CFG))
    mgr = LifespanManager(app)
    await mgr.__aenter__()
    h = {"Authorization": "Bearer sk-wiwi-master-test"}
    h.update(headers or {})
    return mgr, AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                            headers=h)


@respx.mock
async def test_previous_response_id_chains_the_history_upstream():
    route = respx.post(_UP).mock(side_effect=[_chat("first"), _chat("second")])
    mgr, client = await _client()
    try:
        r1 = await client.post("/v1/responses",
                               json={"model": "gpt-4o", "input": "one"})
        rid = r1.json()["id"]
        r2 = await client.post("/v1/responses",
                               json={"model": "gpt-4o", "input": "two",
                                     "previous_response_id": rid})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r2.status_code == 200
    sent = route.calls[1].request.content.decode()
    assert "first" in sent and "two" in sent


@respx.mock
async def test_get_and_delete_round_trip():
    respx.post(_UP).mock(return_value=_chat("hi"))
    mgr, client = await _client()
    try:
        rid = (await client.post("/v1/responses",
                                 json={"model": "gpt-4o", "input": "x"})).json()["id"]
        got = await client.get(f"/v1/responses/{rid}")
        deleted = await client.delete(f"/v1/responses/{rid}")
        gone = await client.get(f"/v1/responses/{rid}")
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert got.status_code == 200 and got.json()["object"] == "response"
    assert deleted.json()["deleted"] is True
    assert gone.status_code == 404


@respx.mock
async def test_unknown_previous_response_id_is_404():
    respx.post(_UP).mock(return_value=_chat("hi"))
    mgr, client = await _client()
    try:
        r = await client.post("/v1/responses",
                              json={"model": "gpt-4o", "input": "x",
                                    "previous_response_id": "resp_nope"})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_fix_round114.py -q`
Expected: FAIL — the retrieve/delete calls 404 while the first two tests fail on chaining.

- [ ] **Step 3: Implement the routes** (beside `responses_api`)

```python
    @app.get("/v1/responses/{response_id}")
    async def responses_get(request: Request, response_id: str):
        state_ = app.state.wiwi
        info, err = await authenticate(request, "", "responses")
        if err:
            return err
        st = state_.response_store
        row = (await st.get(response_id, info.key_id)) if st is not None else None
        if row is None:
            return _err(404, "not_found_error", f"response '{response_id}' not found",
                        request, "responses")
        return JSONResponse(row.output)

    @app.delete("/v1/responses/{response_id}")
    async def responses_delete(request: Request, response_id: str):
        state_ = app.state.wiwi
        info, err = await authenticate(request, "", "responses")
        if err:
            return err
        st = state_.response_store
        deleted = (await st.delete(response_id, info.key_id)) if st is not None else False
        if not deleted:
            return _err(404, "not_found_error", f"response '{response_id}' not found",
                        request, "responses")
        return {"id": response_id, "object": "response.deleted", "deleted": True}
```

Match the exact `authenticate(...)` call shape and error/JSON return conventions already
used by neighbouring routes (grep `authenticate(request` in `app.py`); import
`JSONResponse` only if it is not already imported.

- [ ] **Step 4: Verify pass**

Run: `python3 -m pytest tests/test_fix_round114.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: The gate**

```bash
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
```

Expected: both green. Record the pass count.

- [ ] **Step 6: Live smoke** (two turns against a local fake upstream)

```bash
RID=$(curl -sS localhost:4111/v1/responses -H 'Authorization: Bearer <key>' \
  -H 'content-type: application/json' \
  -d '{"model":"<group>","input":"My name is Ada."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -sS localhost:4111/v1/responses -H 'Authorization: Bearer <key>' \
  -H 'content-type: application/json' \
  -d "{\"model\":\"<group>\",\"input\":\"What is my name?\",\"previous_response_id\":\"$RID\"}"
curl -sS "localhost:4111/v1/responses/$RID" -H 'Authorization: Bearer <key>'
curl -sS -X DELETE "localhost:4111/v1/responses/$RID" -H 'Authorization: Bearer <key>'
```

Expected: turn 2 answers from the chained history, GET returns the object, DELETE reports
`deleted: true`.

- [ ] **Step 7: Commit**

```bash
git add wiwi/server/app.py tests/test_fix_round114.py
git commit -m "Serve stored Responses back over GET and DELETE"
```

---

### Task 5: Docs

**Files:**
- Modify: `docs/API_REFERENCE.md`, `docs/CONFIG.md`, `wiwi.yaml.example`, `AUDIT.md`

- [ ] **Step 1: Update**

- `docs/API_REFERENCE.md`: the `/v1/responses` section gains `previous_response_id`,
  `store`, and the `GET`/`DELETE /v1/responses/{id}` pair — including the key-scoping
  rule and the 404 behaviour.
- `docs/CONFIG.md` + `wiwi.yaml.example`: document `store_responses` and
  `response_store_ttl_s` under `wiwi_settings`.
- `AUDIT.md`: record the removal of the stateless-mode restriction (the codec docstring
  claim at `openai_responses.py:1` is now stale — fix it in Task 2's file too).

- [ ] **Step 2: Fix the stale codec docstring**

`wiwi/wire/openai_responses.py` header, replace the "Stateless mode … previous_response_id
is rejected with a clear error (post-MVP)" paragraph with a two-line statement that state
lives in `wiwi/server/response_store.py` and the codec accepts history via
`previous_output` / `with_history`.

- [ ] **Step 3: Gate and commit**

```bash
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
git add docs/ wiwi.yaml.example wiwi/wire/openai_responses.py
git commit -m "Document stateful Responses"
```
