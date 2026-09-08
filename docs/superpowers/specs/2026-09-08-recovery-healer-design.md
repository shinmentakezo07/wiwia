# Recovery & Health Healer — Design

Date: 2026-09-08
Status: Approved design (in-chat), spec for implementation planning
Scope: new `wiwi/core/recovery.py`, `wiwi/config.py` (`HealerSettings`, `RouterSettings`),
`wiwi/router/router.py` (primitives refactor + probation hooks + transformation
retries), `wiwi/providers/cline_auto_refresh.py` / `workbuddy_auto_refresh.py`
(circuit refactor), `wiwi/server/app.py` (lifespan wiring), new
`tests/test_recovery.py`. `wiwi/providers/base.py` is read, not modified.

## Goals

1. **Helper**: one shared module for the retry/recovery policy primitives that are
   currently duplicated — exponential-backoff math (3 sites: router retry sleep,
   Cline circuit breaker, WorkBuddy circuit breaker) and circuit-breaker state
   (2 sites).
2. **Healer**: a background service that *actively* restores sick targets
   (cooling / invalid keys, cooled-down deployments) by probing them with a
   1-token completion, instead of waiting out passive timers. Closes the gap that
   `mark_invalid()` keys can never recover today.
3. **Request-transformation retries**: one-shot self-healing of the request on
   `context_window_exceeded` (trim oldest messages) and on
   `invalid_request_error` (drop the offending param), reusing the existing
   failover loop.
4. Strict compatibility: all existing tests pass (except any that poke private
   `_circuit` dict internals, updated to the new API with identical external
   contract); default config keeps current behavior except where a knob is
   explicitly default-on; `wiwi.yaml` additions are optional.

## Non-goals (explicitly dropped)

- Full `RetryPolicy` extraction owning the `execute_with_retries` loop — the loop
  is battle-tested; extraction is deferred.
- Adaptive tick pacing / global probe-spend budget — fixed tick + per-sweep and
  per-target caps suffice for the self-hosted profile.
- Admin UI page, Prometheus counters — structlog + proxy-log events only (YAGNI).
- Persistence of healer state across restarts — in-memory, cold start is fine
  (matches the rest of the router's health model).
- Relationship to `2026-08-31-router-health-overhaul-design.md`: that spec is a
  separate, unimplemented proposal (no `wiwi/router/health.py` exists). This
  design neither depends on nor conflicts with it.

## Part A — Shared primitives in `wiwi/core/recovery.py`

| Unit | Responsibility | Interface |
|---|---|---|
| `Backoff` | Exponential delay with jitter; honors upstream `retry_after` | frozen dataclass `Backoff(base_s, cap_s, jitter_s, clock)`; `.delay(attempt, retry_after=None) -> float` |
| `CircuitBreaker` | Per-target failure streak → temporary block, escalating to permanent | `.trip(t)`, `.clear(t)`, `.blocked(t) -> bool`, `.dead(t) -> bool`, `.mark_dead(t)` |
| `probe_verdict(status, msg) -> ProbeVerdict` | Pure classification of a probe HTTP outcome | `ProbeVerdict` enum: `HEALTHY`, `ALIVE_THROTTLED`, `CREDS_VALID_MODEL_BAD`, `CREDS_REJECTED`, `UNREACHABLE` |
| `trim_for_context(ir_req) -> Request` | Build a trimmed copy: retain system messages plus the most recent `ceil(n/2)` non-system messages (`n` = original non-system count); empty result keeps the newest message | pure function |
| `droppable_param(msg) -> str \| None` | Extract a known droppable param name from a provider 400 message | pure function |
| `drop_ir_param(ir_req, name) -> Request` | Copy of the IR request with the named field removed | pure function |

Constants: `DROPPABLE_PARAMS = ("tools", "tool_choice", "temperature", "top_p",
"response_format", "thinking", "reasoning", "max_tokens", "stop")`.

Import direction: `core/recovery.py` imports only `providers.base` (contracts
seam — same as `core/gateway.py` today). No dialect/provider branching. No
imports from `router` or `gateway`, so `router → core.recovery → providers.base`
introduces no cycle.

### Refactors (mechanical, behavior-preserving)

1. `router.py` retry sleep (currently `min(5.0, max(ra, 0.5 * 2**attempt)) +
   uniform(0, 0.25)`) → `Backoff(base_s=0.5, cap_s=5.0, jitter_s=0.25)`.
2. `cline_auto_refresh.py` / `workbuddy_auto_refresh.py`: the `_circuit` dict
   idiom (`streak`/`until`, `streak >= 99` = dead) → `CircuitBreaker(base_s=300,
   cap_s=14400)`; the `refresh_for_provider` hooks read the same instance.
   Module constants (`CIRCUIT_BASE_S`, `CIRCUIT_CAP_S`) keep their values.

## Part B — HealthHealer

Same lifecycle shape as `ClineAutoRefresh`: `.start() / .stop()`, stop-event,
`asyncio.Task(name="health-healer")`, exception-swallowing sweep loop. Lives in
`wiwi/core/recovery.py`; constructed in `lifespan` with
`(router, settings, log_proxy=router.log_proxy)`.

### Config (`HealerSettings`, mounted as `healer:` on `WiwiConfig`)

```yaml
healer:
  enabled: false                # opt-in: probes spend real provider money
  tick_s: 30.0
  probe_timeout_s: 10.0
  max_probes_per_sweep: 8
  min_probe_interval_s: 30.0
  probe_backoff_base_s: 60.0
  probe_backoff_cap_s: 3600.0
  probes_to_restore: 2          # consecutive successes required to restore
  probation_weight: 0.5         # WRR weight multiplier during probation
```

Probe `max_tokens` is a module constant (1).

### Sweep algorithm

1. **Collect candidates** (live reads of `router.providers` / `router.groups`,
   so admin mutations are picked up):
   - Keys with `status == "cooling"` and `cooldown_until` in the future, or
     `status == "invalid"`.
   - Deployments with `cooldown_until` in the future.
   - Never touch `disabled` keys or keys whose cooldown already expired
     (`recover()` handles those naturally).
2. **Filters**: circuit breaker blocked → skip; `min_probe_interval_s` since
   last probe → skip; global `max_probes_per_sweep` cap; bounded concurrency
   via semaphore.
3. **Probe** = one 1-token completion per **(deployment, key) pair**:
   `fresh_adapter(type)` → `encode_request` (IR `Request`: one `"ping"` user
   message, `max_tokens=1`) → `build_url` → POST on the healer's own
   short-timeout `httpx.AsyncClient`. No `RequestContext`, no retries, no
   rate-limit/billing interaction. For `force_stream` adapters (Cline,
   WorkBuddy) the probe sends `stream=True` and treats `200` + first SSE bytes
   as healthy — a non-streaming probe would false-negative every key there.
4. **Verdict** (`probe_verdict`) → action:

   | Verdict | Key | Deployment |
   |---|---|---|
   | `HEALTHY` | +1 success streak (restore at `probes_to_restore`) | +1 success streak |
   | `ALIVE_THROTTLED` (429) | keep/extend cooling per `retry_after`; never a probe failure | same |
   | `CREDS_VALID_MODEL_BAD` (400/404) | restore-eligible (creds proven) | stays sick; circuit escalates toward `mark_dead` (a missing model won't self-heal) |
   | `CREDS_REJECTED` (401/403) | circuit trips | untouched |
   | `UNREACHABLE` (transport, 5xx, 529) | untouched | circuit trips |

5. **Restore** via new dataclass methods (all mutation stays on the dataclasses):
   - `ProviderKey.mark_recovered()`: `status="probation"`, `cooldown_until=0`,
     `err_count=0`, `current_weight=0.0`.
   - `Deployment.mark_recovered()`: probation flag `True`, `cooldown_until=0`,
     `fails.clear()`.
6. **Logging**: structlog events `healer_probe_ok` / `healer_probe_fail` /
   `healer_restored` / `healer_probation_graduated` with provider/key/group
   fields; restores also announced via `router.log_proxy("info", …)`.

### Graduated recovery + probation

- Restore requires `probes_to_restore` **consecutive** successful probes,
  spaced by `min_probe_interval_s`. Any failure resets the streak.
- Probation (new key status `"probation"`, new deployment flag):
  - `ProviderKey.available` includes `probation`; `pick_key` weights it at
    `weight × probation_weight`.
  - `on_result` success while on probation → graduate to `"active"` (log
    `healer_probation_graduated`). Failure follows the existing
    `any_error`/`standard` demotion path unchanged.
  - `pick_deployment` prefers non-probation deployments when any exist;
    success in `execute_with_retries` graduates, `record_fail` demotes
    (clears probation, re-arms cooldown path).
  - Default `probation_weight: 0.5`; admin-set weights are untouched once
    graduated.

## Part C — Request-transformation retries (in the router retry loop)

Knobs on `RouterSettings`: `retry_context_trim: bool = True`,
`retry_param_drop: bool = True` (default-on, mirroring the `drop_params=True`
gateway default).

1. **Context trim**: on `WiwiError` with `etype == "context_window_exceeded"`,
   if `not ctx.metadata.get("wiwi_context_trimmed")` and the knob is on:
   replace `ctx.ir_req` with `trim_for_context(ctx.ir_req)`, set the flag,
   `continue` the attempt loop (counts as an attempt; system prompt preserved).
   The existing `context_window_fallbacks` walk still happens if the trimmed
   retry also fails.
2. **Param drop**: on `etype == "invalid_request_error"` (non-retryable),
   if `droppable_param(e.message)` matches, the knob is on, and the param was
   not already dropped: replace `ctx.ir_req` with `drop_ir_param(...)`, set
   `wiwi_dropped_params` flag/list, `continue`. If nothing droppable is
   identified, behavior is unchanged (raise).

Transformations are one-shot per request (metadata flags), never applied twice,
and never mutate the original IR request (pure copies).

## Error handling

- Healer loop swallows and logs per-target exceptions (pattern of
  `ClineAutoRefresh._sweep`); a broken probe never affects serving traffic.
- Probe client never shares the gateway's httpx client or connection pool.
- All restored state is in-memory; a restart cold-starts (existing router
  semantics).
- Transformation retries can only reduce request content; both knobs disable
  the behavior for callers needing bit-exact forwarding.

## Testing & verification (`tests/test_recovery.py`, new thematic file)

1. `Backoff`: monotone in attempt, cap clamp, jitter bounds, `retry_after` wins.
2. `CircuitBreaker`: trip → blocked, doubling streak, cap, `clear`, `mark_dead` permanent.
3. `probe_verdict`: every row of the verdict table.
4. Healer (respx-mocked upstream):
   - 200 × `probes_to_restore` → cooling key restored to probation, deployment
     cooldown cleared.
   - Single 200 ≠ restore (streak enforced).
   - 429 → key stays cooling, circuit not tripped, `retry_after` honored.
   - 400/404 → key restore-eligible, deployment circuit escalates to dead.
   - 401 → key stays invalid, circuit trips, next sweep skipped.
   - `disabled` key never probed; per-sweep cap respected;
     `force_stream` adapter probed with `stream=True`.
   - `enabled=false` → no task started; start/stop lifecycle clean.
5. Probation: half-weight WRR placement, graduation on success, demotion on
   failure, `pick_deployment` prefers non-probation.
6. Transformations: trim fires once and preserves system messages; param drop
   removes only the matched param and fires once; knobs off → unchanged
   errors; trimming too small a request leaves it unchanged and re-raises.
7. Regression: existing `test_router.py` / cline / workbuddy suites stay green.

## Verification gate

`python3 -m pytest tests/ -q && ruff check wiwi/ tests/` — both green before
claiming done; live smoke test of the healer with a mocked upstream before merge.
