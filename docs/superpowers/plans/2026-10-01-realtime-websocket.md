# Realtime WebSocket Proxy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** `WS /v1/realtime` relays a client's Realtime session to an OpenAI-shaped
upstream, with wiwi's auth, budgets, routing, cooldowns, keys and concurrency caps
applied at upgrade time.

**Architecture:** One route in `app.py` does admission (auth → rate limit →
budget → model resolution → deployment + key pick) *before* opening the upstream
socket, then relays frames verbatim both ways. Billing observes usage-bearing
server events and charges the session max once at close.

**Tech Stack:** Python 3.12, FastAPI/Starlette WebSocket, `websockets` (new
runtime dep), pytest.

**Spec:** `docs/superpowers/specs/2026-10-01-realtime-websocket-design.md`

## Global Constraints

- ruff `line-length = 100`, `target-version = "py311"`. Gate: `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`.
- Bare `async def test_…`; no `conftest.py`.
- Regression file: next unused `tests/test_fix_roundN.py` — **117** (verify).
- Library code uses `structlog`, never `print`.
- Work directly on `main`.

---

### Task 1: The `websockets` dependency + adapter capability

**Files:**
- Modify: `pyproject.toml`
- Modify: `wiwi/providers/base.py` (`ProviderAdapter.realtime_url`)
- Modify: `wiwi/providers/openai_adapter.py` and the openai-compatible family

**Interfaces:**
- Produces: `ProviderAdapter.realtime_url: str | None` (`None` = unsupported).

- [ ] **Step 1: Add the dependency**

`pyproject.toml` `dependencies`: `websockets>=13.0`. Verify it is importable in the
ambient interpreter; if not, `pip install websockets` before continuing.

- [ ] **Step 2: Add the capability to the protocol**

In `wiwi/providers/base.py`:

```python
class ProviderAdapter(Protocol):
    provider_type: str
    ...
    # WebSocket URL for a Realtime-style session, or None when the provider
    # has no such surface. Read by the /v1/realtime route, which refuses a
    # provider with None rather than dialling a URL that does not exist.
    realtime_url: str | None
```

- [ ] **Step 3: Implement it on the OpenAI-shaped adapters**

`openai_adapter.py`: `self.realtime_url = "wss://api.openai.com/v1/realtime"`.
The `openai-compatible` family derives from the base URL instead — read the
existing `base_url` handling on that class rather than hardcoding.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml wiwi/providers/
git commit -m "Declare realtime capability on provider adapters"
```

---

### Task 2: `RealtimeSettings` config

**Files:**
- Modify: `wiwi/config.py`
- Test: `tests/test_realtime.py` (new)

- [ ] **Step 1: Write the failing test**

```python
def test_realtime_defaults_are_off():
    s = RealtimeSettings()
    assert s.enabled is False
    assert s.allow_key_in_query is False
    assert s.idle_timeout_s == 300.0
```

- [ ] **Step 2: Implement**

```python
class RealtimeSettings(BaseModel):
    enabled: bool = False
    # A `?key=` query parameter lands in proxy logs and browser history, so it
    # is refused unless the operator opts in. Header auth is always accepted.
    allow_key_in_query: bool = False
    # Close a session with no traffic in either direction for this long.
    idle_timeout_s: float = 300.0
    # Cap one session's lifetime, so a forgotten socket cannot hold a
    # deployment slot forever.
    max_session_s: float = 3600.0
```

- [ ] **Step 3: Verify, then commit** (`"Configure the realtime surface"`).

---

### Task 3: The relay

**Files:**
- Modify: `wiwi/server/app.py`
- Test: `tests/test_realtime.py` (append)

**Interfaces:**
- Consumes: `RealtimeSettings`, `ProviderAdapter.realtime_url` (Tasks 1–2).
- Produces: `async def _realtime_relay(client_ws, upstream_ws, session) -> None`

- [ ] **Step 1: Write the failing test**

A fake upstream: a `ThreadingHTTPServer` that answers `GET /v1/realtime` with
`101 Switching Protocols` and then echoes every text frame back. `websockets`'
client does the handshake, so a stdlib HTTP server is enough for the upstream
side — **no second WebSocket dependency in the test**.

```python
async def test_a_relayed_frame_arrives_unchanged():
    ...
```

- [ ] **Step 2: Run to verify it fails** — no route exists.

- [ ] **Step 3: Implement the route**

```python
    @app.websocket("/v1/realtime")
    async def realtime_endpoint(ws: WebSocket):
        # Admission happens entirely before the upstream socket exists: a
        # client that is refused after 101 has already seen a success it must
        # then unwind, and cannot read the status code.
        ...
```

Order: authenticate → rate limit → budget → `resolve_group` → `pick_deployment` →
`pick_key` → `ws.accept()` → open upstream → relay.

Refusals write a literal `websocket.http.response.start` + `.body` pair
**before** `accept()`. Writing `await ws.close(code=…)` is the tempting shortcut
and it is wrong: uvicorn answers 403 to *any* pre-accept close and discards the
code, so every refusal reaches the client as an identical 403. The literal
response is what makes a 401 readable.

- [ ] **Step 4: The relay itself**

```python
async def _realtime_relay(client_ws, upstream_ws) -> None:
    """Pump text frames both ways until either side closes.

    Frames are passed through byte-for-byte. The session protocol is stateful
    and ordered (session.update mutates state later turns depend on), so

    wiwi reads it and never rewrites it.
    """
```

Two tasks, `asyncio.gather`ed, each closing the other on the way out. Close
exactly once — a websocket that closes an already-closed peer raises.

- [ ] **Step 5: Verify, then commit** (`"Relay realtime sessions to the upstream"`).

---

### Task 4: Billing at close

**Files:**
- Modify: `wiwi/server/app.py`
- Test: `tests/test_realtime.py` (append)

- [ ] **Step 1: Write the failing test**

A fake upstream that emits two usage-bearing events for one response, asserting
the session is charged the **max**, not the sum — the events restate overlapping
windows of the same conversation, so summing inflates every multi-turn session.

- [ ] **Step 2: Implement**

Track `peak_total` across server events; on close price it once with the same
`cost/pricing.py` path HTTP uses, and write **one** request-log row for the
session (per-event rows would cost a DB write per audio chunk batch).

- [ ] **Step 3: Verify, then commit** (`"Bill a realtime session once at close"`).

---

### Task 5: Capacity, docs, smoke

**Files:**
- Modify: `docs/API_REFERENCE.md`, `docs/PROVIDERS.md`, `docs/CONFIG.md`, `wiwi.yaml.example`

- [ ] **Step 1: Assert a session holds its slot**

A test that a live session counts against `max_inflight`, so a second session on
a cap-1 deployment is shed with the same 503 as an HTTP request. The session
releases the slot in its `finally`, whatever closed it.

- [ ] **Step 2: Document** — the route, the auth rules (and that `?key=` is
opt-in and lands in logs), the billing rule, the 501 for a non-realtime provider,
and the `RealtimeSettings` table.

- [ ] **Step 3: Gate**

```bash
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
```

- [ ] **Step 4: Live smoke**

Boot against the fake upstream with `realtime.enabled: true`; connect a real
client, send `session.update` + `response.create`, confirm the frames come back
byte-identical, close one side and confirm the other closes, and confirm the
request log shows one session row with the peak usage.

- [ ] **Step 5: Commit** (`"Document the realtime surface"`).