# Router Health & Performance Overhaul — Design

Date: 2026-08-31
Status: Approved design (in-chat), spec for implementation planning
Scope: `wiwi/router/router.py`, new `wiwi/router/health.py`, `wiwi/config.py` (RouterSettings), new `tests/test_router_health.py`

## Goals

1. **Routing logic**: smarter deployment/key selection via an additive, knob-gated health
   scoring model (EWMA latency + success rate), and adaptive key cooldowns.
2. **Performance**: behavior-neutral hot-path fixes in the router.
3. **Strict compatibility**: all existing tests pass unmodified; `wiwi.yaml` semantics
   stable; new behavior is opt-in via config knobs.

## Non-goals (explicitly dropped)

- Lock-free / sharded WRR state — no evidence of contention at target concurrency
  (single-user / low-traffic self-hosted profile).
- Circuit breakers, concurrency caps, request queueing — out of scope.
- Strategy plugin architecture — YAGNI.
- WRR jitter — smooth WRR already de-bursts deterministically; jitter would break the
  exact-proportion semantics pinned by `tests/test_router.py:54`.
- Persistence of health state across restarts — cold-start exploration covers warmup
  (existing pinned semantic, `tests/test_fix_round3.py`).

## Part A — Health scoring model (opt-in)

### Config knobs (all optional, in `RouterSettings`)

```yaml
router_settings:
  health_model: none        # none (default, bit-identical legacy) | scored
  health_ewma_alpha: 0.2
  health_window: 32
  adaptive_cooldown: false  # only meaningful with health_model: scored
```

Pydantic v2 fields on `RouterSettings` with defaults matching the above; validation
rejects unknown `health_model` values.

### New module `wiwi/router/health.py`

| Unit | Responsibility | Interface |
|---|---|---|
| `LatencyEWMA` | Exponentially weighted latency average | `.sample(ms)`, `.value`, `.cold` |
| `SuccessWindow` | Bounded deque (default 32) of pass/fail outcomes | `.record(ok)`, `.rate`, `.cold` |
| `HealthState` | Per-deployment composite; owns both of the above; `score()` | attached to `Deployment` as a plain field |

Semantics:

- `LatencyEWMA`: standard EWMA, `value = alpha*x + (1-alpha)*value` seeded on first
  sample. Zero/negative samples ignored (defensive, no exceptions).
- `SuccessWindow`: fixed-capacity deque; `rate` = fraction of successes; cold until at
  least one sample.
- `HealthState.score()`: composite in `[0, 1]` —
  `score = latency_factor × success_rate` where `latency_factor = 1 - min(ewma_ms / 10_000, 1)`
  (10s normalization bound; a deployment averaging ≥10s scores 0 on the latency axis).
  Cold state scores as fully healthy (`latency_factor = 1`, `success_rate = 1`) so cold
  deployments are still explored.

### Wiring

- `Deployment` gains a `health: HealthState` field (default-constructed).
- Sampling happens at the two existing accounting sites only:
  - `execute_with_retries` success/failure handling (`router.py` post-`call_one`),
  - the stream-pump completion path in `core/gateway.py` where inflight/latency
    accounting already lives.
- No new HTTP paths. Health state is in-memory only; admin mutations that remove a
  deployment drop its health state (removed = cold anyway).

### Decision points changed under `health_model: scored`

1. **`latency-based`**: pick by `HealthState.score()` instead of raw p95 deque.
   Cold deployments still explored randomly (preserves `tests/test_fix_round3.py`).
2. **`simple-shuffle`**: effective weight = `weight × health_multiplier`, clamped to
   `[0.05, 1.0]` — unhealthy deployments are de-prioritized but never starved (still
   probed). Cold = multiplier 1.0.
3. **`_CrossProviderWRR`**: same clamped multiplier applied to per-provider weights.
4. **`least-busy`**: unchanged, except health score breaks ties when `inflight` is equal.

When `health_model: none` (default), every decision point uses the legacy code path
bit-identically.

### Adaptive cooldown (only when `health_model: scored` and `adaptive_cooldown: true`)

- Repeated 5xx failures on the same key grow the cool window:
  `min(base × 2^(streak-1), 60s)`; streak = consecutive failures (reset on success,
  which already resets `err_count`).
- 429 always honors upstream `Retry-After` (capped at 30s, as today); adaptation never
  lowers a provider-specified window.
- 401/403 → `mark_invalid` immediately, unchanged.
- Deployment-level `record_fail` / `cooldown_until` semantics untouched — adaptation
  only affects `ProviderKey.mark_cooling` inputs.

## Part B — Unconditional performance fixes (behavior-neutral)

1. **Alias index in `resolve_group`**: today every alias lookup flattens all groups ×
   deployments per request. Maintain `_alias_dep_index: dict[str, list[Deployment]]`
   built in `_build()` and refreshed by `rebuild_cross_provider_pools()` (already
   invoked by the admin API on every deployment mutation). Lookup becomes O(1).
2. **Incremental p95 on `Deployment`**: keep the 50-sample deque; store a lazily
   recomputed sorted snapshot invalidated on append. O(1) amortized per pick; exact
   same `percentile()` math, bit-identical p95 semantics.
3. **`record_fail` fast path**: prune the timestamp window only when
   `len(self.fails) >= allowed_fails` (the only point where pruning can change the
   outcome). No allocation on the common path.
4. **`pick_key` recover() short-circuit**: skip the per-key `recover()` loop when no
   key is cooling (guard with the existing monotonic timestamps).

## Error handling

- Health state in-memory only; restart = cold start; exploration covers it.
- Malformed latency samples (`ms <= 0`) ignored; no new exception paths.
- Admin pool rebuilds drop health state for removed deployments only.

## Testing & verification

1. New `tests/test_router_health.py`:
   - EWMA math (alpha decay, cold flag, zero-sample rejection)
   - Success-window rate and cold flag
   - Scored-mode selection vs legacy mode for all three strategies
   - Weight multiplier clamping bounds ([0.05, 1.0], cold = 1.0)
   - Adaptive cooldown growth, 60s cap, Retry-After precedence, streak reset on success
2. Full suite green **unmodified** (strict-compat gate):
   `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`
3. Optional: `bench.py` before/after against a live gateway for the perf delta.

## Risks

- Scored mode changes selection outcomes by design — mitigated by opt-in default-off
  and unit tests for both modes.
- Health state attached to `Deployment` dataclass: default factory keeps legacy
  construction sites working.
