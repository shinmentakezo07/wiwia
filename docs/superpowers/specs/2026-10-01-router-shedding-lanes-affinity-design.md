# Router load shedding, priority lanes and session affinity

**Date:** 2026-10-01
**Status:** Accepted — user approved 2026-10-01
**Sub-project:** D of five (A: `/v1/completions` · B: stateful Responses · C: tracing ·
**D: this** · E: Realtime WebSocket proxy)

## Goal

Three controls that let one gateway serve several very different clients — an
interactive coder and a nightly batch job, say — without either starving the other:

1. **Load shedding (G12).** Bound concurrency per deployment and refuse with `503`
   + `Retry-After` when the bound is reached, instead of queueing into an upstream
   that is already failing.
2. **Priority lanes (G19).** Let a request claim only the share of a deployment's
   concurrency that its lane is entitled to, so bulk traffic cannot consume the
   capacity an interactive client needs.
3. **Session affinity (G13).** Pin a client session to the deployment that served
   it, so prompt caches and provider-side session state stay warm — but drop the pin
   the moment that deployment is unhealthy or saturated.

All three are opt-in. With no configuration the router behaves exactly as it does
today.

## Context

`Deployment` already carries `inflight` (incremented in `Gateway._call`, decremented
in `finally`, and owned by the stream pump until the last delta) and the gateway
already maintains a shared `httpx.AsyncClient`. Nothing *reads* `inflight` for
admission: `pick_deployment` filters only on `available`, probation and the per-
deployment rpm/tpm windows. So:

- **There is no concurrency ceiling at all.** A gateway fronting an upstream with
  a 200-connection limit will open 200+ sockets, drive every one of them slow, and
  convert a capacity problem into a latency problem for every client. The rpm/tpm
  windows do not help: a deployment with no caps configured is unbounded by
  definition.
- **429 is the only overload signal.** `execute_with_retries` distinguishes "nothing
  healthy" (503) from "everything at its rpm/tpm cap" (429). Neither covers
  concurrency, so a saturated deployment is indistinguishable from a healthy one at
  admission time.
- **One client's load is every client's latency.** With weighted round-robin, a
  batch job and an interactive client draw from the same pool. Whichever arrives
  second sees the latency the first caused. The only lever is who gets refused
  first, and there is none.

## Design

### 1. Load shedding — `max_inflight`

New per-deployment cap, with a router-wide default.

```python
class RouterSettings(BaseModel):
    max_inflight: int | None = None       # per deployment; None = uncapped
    inflight_retry_after_s: float = 1.0   # Retry-After on a shed 503

class WiwiParams(BaseModel):
    max_inflight: int | None = None       # overrides router_settings for this model
```

`pick_deployment` gains one filter after the rpm/tpm filter:

```python
shed = [d for d in avail if d.inflight >= d.max_inflight]
avail = [d for d in avail if d.inflight < d.max_inflight]
```

`d.max_inflight` resolves per deployment: the model's `wiwi_params.max_inflight`
when set, else `router_settings.max_inflight`, else `None` (uncapped — the default,
so existing deployments are unaffected).

When the filter empties the candidate list, `execute_with_retries` raises
`WiwiError(503, "server_overloaded", ..., retry_after=...)`. **503, not 429**: the
distinction the router already draws stays sharp — 429 means "this key's quota is
spent, retry in N seconds", 503 means "this server is saturated right now, retry
elsewhere". `Retry-After` comes from the *least* saturated rejected deployment, so
the advice is "come back when the loadiest one frees a slot".

The check and the increment are both synchronous and happen between awaits, so the
existing no-lock argument in `Deployment._window` applies unchanged: no other
coroutine can interleave between the test and the reservation.

### 2. Priority lanes — `priority` on the virtual key

A key declares a lane; each lane is entitled to a share of any deployment's
concurrency.

```python
class RouterSettings(BaseModel):
    priority_lanes: dict[str, float] = {}   # lane -> share, e.g. {"interactive": 0.8, "bulk": 0.2}
    default_lane: str = "bulk"
```

`vkeys` gains `priority TEXT` (migration, like `budget_reserved`); `AuthInfo` gains
`priority: str | None = None`. Master keys are always the top lane: a request
authenticated with the master key is an operator's request and must never be
refused for capacity.

`pick_deployment` computes, per candidate, the ceiling for *this request's* lane:

```python
def _lane_ceiling(d, lane_share) -> int:
    """Concurrency this request's lane may use of *d*."""
    return max(1, math.ceil(d.max_inflight * lane_share))
```

A deployment is admissible when `d.inflight < _lane_ceiling(d, share)`. Two
properties fall out, and both matter:

- **Bulk cannot eat interactive capacity.** At `max_inflight: 10` with
  `{"interactive": 0.8, "bulk": 0.2}`, bulk is refused at 2 inflight and
  interactive runs to 10. The interactive lane's share is *unused* bulk capacity,
  not capacity taken away from anyone — a deployment with 2 inflight is admissible
  to an interactive request.
- **A single lane never deadlocks.** `max(1, …)` guarantees a lane can always take
  one slot, so `{"interactive": 0.01}` with `max_inflight: 10` still serves one
  request at a time rather than refusing everything.

If *every* candidate is above its ceiling for this lane, the request is refused with
the same 503 as plain shedding — the message names the lane, because the operator's
question is "why is my batch job refused" and "why is my coder refused" have
different answers.

An unknown or unset lane falls back to `default_lane`. Unknown lane *names* in
`priority_lanes` are a config error caught at load: `RouterSettings` validates that
every key is a non-empty string with a share in `(0, 1]`, and that the shares sum to
at most 1.0 (over-subscription is a typo, not a feature; under-subscription is
fine and means the top lane leaves headroom for burst).

Lane weights are read from `ctx.auth.priority`. `pick_deployment` therefore keeps
its current signature — the priority travels on the context that is already
threaded through.

### 3. Session affinity — `session_affinity`

`router_settings.session_affinity: bool = False` plus `session_affinity_ttl_s`
(default 600).

The client supplies a session id in a header (`x-wiwi-session-id`, or a query
parameter for clients that cannot set headers). `RequestContext` carries it;
`Router` keeps an in-process `dict[str, tuple[int, float]]` mapping
`session_id -> (id(deployment), expiry)`.

On pick, in order:

1. If affinity is off, or there is no session id, do nothing (today's behaviour).
2. If a pin exists, is unexpired, and its deployment is still in the candidate set,
   still `available`, and below the ceiling for this request's lane → **use it**.
3. Otherwise, ignore the pin, pick normally, and re-pin to the result.

The pin is never honoured past the health check, which is the whole point: a stale
pin to a cooling deployment is exactly the failure mode affinity is supposed to
avoid. `TTL` bounds the memory (entries are also dropped on read when expired, so an
idle map does not grow without bound) and how long a session can be pinned to one
upstream after it has rotated — prompt caches beyond that are not worth the
stickiness.

Affinities are per-router in-process state: a multi-instance deployment pins
per instance, which is the same single-instance-target caveat as the in-memory rate
limiter and cache, and is documented rather than pretended away.

### 4. What this does **not** do

- No queueing. A shed request is refused immediately; there is no waiting room and
  no background replay. A queue in front of an already-saturated upstream only
  converts 503s into timeouts.
- No per-lane *rate* limits (rpm/tpm stay per deployment). Lanes partition
  *concurrency*, which is what strands interactive traffic.
- No cross-instance state for lanes' ceilings — the ceiling is derived from config
  and each process's own `inflight` count.

## Testing

1. **Uncapped by default** — a deployment with no `max_inflight` and 50 in flight is
   still picked; `_lane_ceiling` on an uncapped deployment never rejects.
2. **Shedding** — at the cap, `pick_deployment` returns `None` and
   `execute_with_retries` surfaces 503 with a `Retry-After`; below the cap it picks
   normally; a sibling deployment below its cap is preferred over refusing.
3. **Per-deployment override beats the router default**, in both directions
   (a stricter model sheds earlier, a looser one later).
4. **Lanes** — exact inflight sequences against a `max_inflight: 4`,
   `{"interactive": 0.5, "bulk": 0.25}` pool: bulk is refused at 1, interactive at 2.
   `max(1, …)` is asserted directly (a 1% lane on a cap of 2 still admits one).
   Unknown lane falls back to `default_lane`; master key is always admissible.
5. **Affinity** — the pin is honoured while the deployment is healthy and below its
   ceiling; a pin into a cooling deployment is dropped and re-pointed; an expired pin
   is ignored; `session_affinity: false` leaves `pick_deployment` byte-identical to
   today.
6. Full gate + a live smoke that fills the cap concurrently and shows 503 +
   `Retry-After`, then a lane-protected request succeeding on the same deployment.

## Non-goals

- Queueing / backpressure buffers.
- Lane-aware rpm/tpm windows.
- Affinity across instances (needs shared state; the in-memory limiter and cache set
  the precedent for documenting this instead).