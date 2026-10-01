# Stateful Responses API (`previous_response_id` + `store`)

**Date:** 2026-10-01
**Status:** Accepted — user approved 2026-10-01
**Sub-project:** B of five (A: `/v1/completions` · **B: stateful Responses** · C: OpenTelemetry ·
D: router shedding/lanes/affinity · E: Realtime WebSocket proxy)

## Goal

Make `/v1/responses` stateful, the way the real API is: a completed response is
stored, and a later request may carry `previous_response_id` instead of resending
the transcript. Add `GET /v1/responses/{id}` and `DELETE /v1/responses/{id}`.

## Why (context)

`wiwi/wire/openai_responses.py:1` states the design ("Stateless mode: every request
is self-contained (Codex sends full history with store:false)") and `:240` rejects
`previous_response_id` with `DialectError`. That was a deliberate MVP shortcut, not
a wire-format impossibility: the Responses API is stateful by default server-side
(`store: true`), and clients that never resend history — the OpenAI Agents SDK's
`Response` objects, `previous_response_id` chains — cannot work against wiwi at all.

The complication is that this is the one surface where the upstream may *also* be
stateful. A response we forward to the real OpenAI Responses API is stored there
under *its* id; but wiwi re-encodes every response and hands the client its own
`resp_<wiwi-request-id>` (`openai_responses.py:408`). A client that replays our id
to us must be served by *our* store, not forwarded, because the upstream has never
seen that id. So wiwi must own the state, and translate the history back into
input items on the next hop — exactly the hub-and-spoke contract.

## Design

### 1. Store — `wiwi/server/response_store.py` (new)

A small SQLAlchemy-core store over the same engine `ConfigStore` uses, created at
startup with `CREATE TABLE IF NOT EXISTS` and pruned by a lifespan sweeper (same
shape as `wiwi/logging_core/db_sink.py` and `wiwi/streaming/tape_store.py`).

```sql
CREATE TABLE IF NOT EXISTS stored_responses (
  id            TEXT PRIMARY KEY,          -- wiwi's ``resp_<request-id>``
  created_at    DOUBLE PRECISION NOT NULL,
  key_id        TEXT NOT NULL,             -- owning virtual key (or "master")
  surface       TEXT NOT NULL DEFAULT 'responses',
  model_group   TEXT NOT NULL DEFAULT '',
  request_json  TEXT NOT NULL,             -- decoded IR input items (wire items)
  output_json   TEXT NOT NULL              -- dialect response object, verbatim
);
CREATE INDEX IF NOT EXISTS idx_stored_responses_created
  ON stored_responses (created_at);
```

```python
class StoredResponse(NamedTuple):
    id: str; key_id: str; model_group: str
    input_items: list[dict[str, Any]]
    output: dict[str, Any]

class ResponseStore:
    def __init__(self, engine: AsyncEngine, ttl_s: float) -> None: ...
    async def startup(self) -> None                       # DDL
    async def put(self, resp_id, key_id, model_group, input_items, output) -> None
    async def get(self, resp_id: str) -> StoredResponse | None   # None when expired
    async def delete(self, resp_id: str, key_id: str) -> bool
    async def sweep(self, now: float | None = None) -> int
    async def sweep_forever(self, interval_s: float = 300.0) -> None
    def start(self, interval_s: float | None = None) -> None
    async def stop(self) -> None
```

**Expiry is enforced on read, not only by the sweeper** — a row that is past its TTL
must never be returned even if the sweeper has not run yet.

### 2. Key scoping (a response id is not a capability token)

`get`/`delete` take the caller's `key_id` and match it. A client presenting another
key's response id gets **404 `not_found_error`** with a message naming the id — never
the other key's transcript. Master (`key_id == "master"`) may retrieve any row, which
is the documented admin override.

### 3. Chaining — `previous_response_id`

`wiwi/wire/openai_responses.py`:

- `decode_request(body, previous_output: list[dict] | None = None)` — gains the
  optional second argument. With `previous_output` provided, the stored output items
  are decoded **through the same item loop** (`function_call`, `reasoning`, `message`)
  and prepended to `input`, and `previous_response_id` is accepted. Without it the
  field is still refused (unchanged behaviour for a caller who bypasses the store).
- `WithHistory` is not a function; the surface supplies a *body rewrite*:

```python
def with_history(body: dict[str, Any], previous: list[dict[str, Any]]) -> dict[str, Any]:
    """Prepend a stored response's output items to this request's ``input``."""
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

`wiwi/server/app.py`:

- `run_chat_like(..., codec_history=None, store_response=False)` — two optional
  parameters, both defaulted so the other three surfaces are untouched.
- When `codec_history` is given and the body carries `previous_response_id`,
  the **model is read from the raw body, auth runs first** (a stored id is only
  meaningful for the key that owns it), the row is fetched key-scoped, and the body
  is rewritten with `codec_history` before decode. Missing/expired/foreign → 404.
- On completion (both the non-streaming path and the stream `_teardown_tail`),
  when `store_response` is set and the effective `store` is not `False`, the
  response object and the decoded input items are written with the response id
  `resp_<request-id>` that the encoder already emitted.

### 4. Routes

```python
@app.get("/v1/responses/{response_id}")     -> stored dialect response object (200) / 404
@app.delete("/v1/responses/{response_id}")  -> {"id": …, "object": "response.deleted", "deleted": true}
```

Both authenticate like every other route and scope by the caller's key.

### 5. Config

`wiwi_settings: { store_responses: true, response_store_ttl_s: 86400 }`. With
`store_responses: false` the surface behaves exactly as it does today (the field is
accepted and the request is served, but nothing is persisted and `previous_response_id`
404s) — that keeps a stateless deployment possible without a code path fork.

## Testing

1. Store unit tests: put/get/delete, TTL enforced on read with the sweeper stopped,
   key scoping (a second key gets `None`), master override.
2. Codec: `decode_request` with `previous_output` prepends the items and accepts the
   id; without it still refuses; `with_history` merge for str and list input.
3. End-to-end (`tests/test_fix_round114.py`): two-turn chain through `create_app` with
   a respx-mocked upstream — turn 2 must send the turn-1 assistant message and the new
   input **in the upstream body** (assert the recorded request), GET returns the stored
   object, DELETE removes it, a different key gets 404, an expired id gets 404.

## Non-goals

- `store: false` per-request *forwarding* semantics to an upstream that is itself
  stateful (wiwi remains the only state holder).
- Streamed `response.created` id stability across a reconnect beyond what the tape
  already provides.
- `metadata` search, response listing, or `conversation` objects.

## Risks

- **Reordering auth before decode** on the `previous_response_id` path only: a
  malformed body with a bad key answers 401 where the normal path answers 400. This
  is the correct precedence (identity first) and is confined to that path.
- **Growth.** `response_json` holds the full transcript; the TTL sweeper plus the
  read-time expiry cap it. Default TTL 24 h, matching the real API's retention order
  of magnitude.
