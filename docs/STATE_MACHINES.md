# Implicit state machines — inventory and decision

The gateway has nine lifecycle authorities that were already state machines,
but only `wiwi/streaming/machine.py` declared its graph. The rest encoded
states as mutable fields and transitions as scattered `if`s. That is fine for
serving, but it makes three questions expensive: *what states exist*, *is this
transition legal*, and *why is this object in this state*.

`wiwi/state_machines.py` declares every graph once. It is **observational**:

- `fire()` records a transition, logs illegal events, and returns the state —
  it never raises and never writes an authority field.
- `explain(instance)` returns state + bounded history + the declared graph.
- `snapshot_all()` exposes every tracked instance for diagnostics.
- `max_instances` bounds per-instance tracking (responses: 4096).

Only the response store is wired per instance today. It is low-frequency and
already mutated in three places, so the tracking cost is negligible. The other
machines are declared for validation and have pure derived-state helpers, which
avoids adding work to hot paths (budget admission, deployment selection,
streaming).

## Inventory

| Authority | States | Authority (source of truth) | Current form | Machine |
|---|---|---|---|---|
| SSH stream deltas | start → content/tool → usage → finish → end/error | `wiwi/streaming/deltas.py`, `wiwi/streaming/machine.py` | **explicit** (binding contract) | unchanged |
| Provider key | active, cooling, invalid, disabled, probation | `wiwi/router/router.py:43-57`, transitions at `:150`, `:300-310` | explicit `status` + comment graph | `KEY` |
| Deployment | ready, degraded, cooling, probation, retired | fields `wiwi/router/router.py:470-483`; cooldown at `:667`, restore at `:704` | implicit flags | `DEPLOYMENT` + `deployment_state()` |
| Budget reservation | idle, reserved, settled, released, refused | `wiwi/auth/service.py:128-146` (fields), `:733` reserve, `:790` release, `:825` settle | implicit ledger | `BUDGET` + `budget_state()` |
| OAuth pending | idle, pending, connected, error, expired | `wiwi/server/app.py:1190-1203`, eviction `:1255-1265`, flows `:6736`, `:7103`, `:7705` | implicit dict + TTL | `OAUTH` + `oauth_state()` |
| Stored response | absent, stored, expired, deleted | `wiwi/server/response_store.py` (`put`/`get`/`delete`/`sweep`) | implicit TTL | **`RESPONSE` wired** + `response_state()` |
| Stream journal | absent, recording, complete, abandoned | `wiwi/streaming/tape_store.py:400`, `:619` replay; `wiwi/streaming/resume.py:36` | implicit file + active map | `JOURNAL` |
| Healer probe | idle, probing, ready, backing_off | `wiwi/core/recovery.py:57` breaker, `:294` healer, `:474` probe | explicit breaker + flags | `HEALER` |
| User session | anonymous, authenticated, expired | `wiwi/auth/users.py:135-183` signed cookie | implicit expiry | `SESSION` |
| Config overlay | absent, loaded | `wiwi/server/config_store.py` + admin routes | implicit DB row | `CONFIG_OVERLAY` |

Frontend counterparts (not modelled here; they are UI-only and already typed):

- Playground stream/session: `web/src/pages/Playground.tsx:903` (`ready`/`live`)
  and `:1501`.
- Login mode/error: `web/src/pages/Login.tsx`.
- HTTP error surface: `web/src/api/client.ts:4`.

## Decision

1. **Do not convert hot authorities to event-driven objects.** Budget admission,
   deployment selection, and streaming run per request; a wrapper there buys
   little and can change timing. Their machines stay declarative, and their
   state is *derived* from the existing fields (`deployment_state`,
   `budget_state`), so there is one source of truth.
2. **Wire only low-frequency authorities.** The response store now fires
   `put`/`expire`/`delete`; history is bounded and IDs are evicted past 4096.
   OAuth pending is the next candidate (tokens are TTL-bounded), but its create
   and pop sites are spread across three flows, so it is left declared until a
   single helper can own both ends.
3. **Illegal transitions are observable, not fatal.** `fire()` counts and logs
   them. A bug shows up as `state_machine.illegal` with the machine, instance,
   and attempted transition — never as a failed request.
4. **The existing explicit contracts stay authoritative.** The stream contract
   (`deltas.py`/`machine.py`) is not duplicated; the key graph mirrors the
   comment at the top of `router.py`, because that comment is already correct.
