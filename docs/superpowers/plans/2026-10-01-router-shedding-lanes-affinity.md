# Router Shedding, Lanes and Affinity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound concurrency per deployment (503 + `Retry-After`), partition that
concurrency into priority lanes so bulk cannot strand interactive traffic, and pin a
client session to its deployment while that deployment is healthy.

**Architecture:** All three live in `Router.pick_deployment`, which already receives
`deps`, `ctx` and the `RouterSettings`, and already reserves at a single exit point.
`inflight` is already maintained by the gateway (`_call` for non-streaming, `_pump`
for streaming), so no new accounting is needed. Lane membership travels on
`ctx.auth.priority`; session identity on `ctx.metadata["session_id"]`.

**Tech Stack:** Python 3.12, Pydantic v2 config, pytest, respx.

**Spec:** `docs/superpowers/specs/2026-10-01-router-shedding-lanes-affinity-design.md`

## Global Constraints

- ruff `line-length = 100`, `target-version = "py311"`; ambient `python3` / `pytest` / `ruff`.
- Gate before claiming done: `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`.
- Bare `async def test_…`; no `conftest.py`; respx in decorator form.
- Regression file: next unused `tests/test_fix_roundN.py` — **116 is the next one**
  (verify with `ls tests/test_fix_round*.py`).
- **Off by default.** Every new setting has a value that leaves today's behaviour
  unchanged, and a test asserts that.
- Commit one logical change at a time. Never commit `wiwi.yaml`, `wiwi.db`, `.env`,
  `key.md`, `opencode.json(c)`, `*.har`, `.wiwi/`, `.verify/`.

---

### Task 1: Config — caps, lanes, affinity

**Files:**
- Modify: `wiwi/config.py` (`RouterSettings`, `WiwiParams`)
- Test: `tests/test_router.py` (append)

**Interfaces:**
- Produces: `RouterSettings.max_inflight: int | None`,
  `RouterSettings.inflight_retry_after_s: float`,
  `RouterSettings.priority_lanes: dict[str, float]`,
  `RouterSettings.default_lane: str`,
  `RouterSettings.session_affinity: bool`,
  `RouterSettings.session_affinity_ttl_s: float`,
  `WiwiParams.max_inflight: int | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_router_settings_defaults_leave_routing_uncapped():
    s = RouterSettings()
    assert s.max_inflight is None
    assert s.priority_lanes == {}
    assert s.session_affinity is False


def test_priority_lane_shares_are_validated():
    # A lane share outside (0, 1] is a typo, not a policy.
    with pytest.raises(ValidationError):
        RouterSettings(priority_lanes={"bulk": 0.0})
    with pytest.raises(ValidationError):
        RouterSettings(priority_lanes={"bulk": 1.5})
    # Over-subscription is rejected; under-subscription leaves headroom.
    with pytest.raises(ValidationError):
        RouterSettings(priority_lanes={"a": 0.7, "b": 0.7})
    assert RouterSettings(priority_lanes={"a": 0.5, "b": 0.3}).default_lane


def test_wiwi_params_accepts_a_per_model_cap():
    assert WiwiParams(max_inflight=8).max_inflight == 8
    assert WiwiParams().max_inflight is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_router.py -q -k "uncapped or lane_shares or per_model_cap"`
Expected: FAIL — no such attributes.

- [ ] **Step 3: Implement** in `RouterSettings`:

```python
    # Concurrency ceiling per deployment (None = uncapped, today's behaviour).
    max_inflight: int | None = None
    # Retry-After advertised when every candidate is shedding. Deliberately short:
    # a shed slot frees as soon as any in-flight request returns.
    inflight_retry_after_s: float = 1.0
    # Lane -> share of a deployment's concurrency. Shares must sum to <= 1.0.
    priority_lanes: dict[str, float] = Field(default_factory=dict)
    default_lane: str = "bulk"
    session_affinity: bool = False
    session_affinity_ttl_s: float = 600.0

    @field_validator("priority_lanes")
    @classmethod
    def _lane_shares_are_sane(cls, v: dict[str, float]) -> dict[str, float]:
        if not v:
            return v
        for name, share in v.items():
            if not name or not 0 < share <= 1:
                raise ValueError(
                    f"priority_lanes[{name!r}]={share}: share must be in (0, 1]")
        total = sum(v.values())
        if total > 1.0 + 1e-9:
            raise ValueError(
                f"priority_lanes shares sum to {total:.3f} > 1.0: lanes partition"
                " concurrency, they do not create it")
        return v
```

and in `WiwiParams`:

```python
    max_inflight: int | None = None  # overrides router_settings.max_inflight
```

- [ ] **Step 4: Verify pass**

Run: `python3 -m pytest tests/test_router.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add wiwi/config.py tests/test_router.py
git commit -m "Configure concurrency caps, priority lanes and affinity"
```

---

### Task 2: Shedding in `pick_deployment`

**Files:**
- Modify: `wiwi/router/router.py`
- Test: `tests/test_fix_round116.py` (new)

**Interfaces:**
- Consumes: `RouterSettings.max_inflight`, `inflight_retry_after_s`, `WiwiParams.max_inflight` (Task 1).
- Produces: `Deployment.effective_max_inflight(router_settings)`, and
  `ctx.metadata["shed_reason"]` set when a pick is refused for capacity.

- [ ] **Step 1: Write the failing test** — `tests/test_fix_round116.py`

```python
"""Round 116 — router load shedding, priority lanes and session affinity."""

# Build deployments through the existing helper the router tests use (read
# tests/test_router.py for its `_dep` / `_router` construction and reuse it —
# do not invent a second convention).

def test_uncapped_deployments_are_never_shed():
    ...

def test_a_deployment_at_its_cap_is_refused_with_retry_after():
    ...

def test_a_sibling_below_its_cap_is_preferred_over_refusing():
    ...
```

Fill in the bodies with the repo's existing deployment/router construction. The
assertions that matter: at the cap `pick_deployment(...) is None`; below it a pick
happens; with two deployments where one is saturated the *other* is chosen.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_fix_round116.py -q`
Expected: FAIL — no cap is enforced today, so the pick succeeds.

- [ ] **Step 3: Implement**

`Deployment`:

```python
    def effective_max_inflight(self, settings: RouterSettings) -> int | None:
        """Resolve this deployment's concurrency ceiling.

        The model's own ``max_inflight`` wins over the router-wide default, so a
        single expensive model can be capped harder than its siblings. ``None``
        means uncapped, which is the default and keeps today's behaviour.
        """
        own = self.max_inflight
        return own if own is not None else settings.max_inflight
```

(`max_inflight: int | None = None` goes in the `Deployment` dataclass next to
`inflight`; the field is populated from `wiwi_params` where the other per-model
limits are read — grep `rpm=` in the deployment-construction code.)

`Router.pick_deployment`, immediately after the rpm/tpm filter and before the
probation preference:

```python
        # Concurrency shedding. Tested and reserved at the same synchronous
        # point as the rpm/tpm windows above, so no other coroutine can slip
        # between the check and the increment in ``reserve_slot``'s caller.
        lane_ceiling = self._lane_ceiling
        shed: list[Deployment] = []
        kept: list[Deployment] = []
        for d in avail:
            ceiling = lane_ceiling(d, ctx)
            if ceiling is not None and d.inflight >= ceiling:
                shed.append(d)
            else:
                kept.append(d)
        if not kept and shed:
            # Everything is full for *this* request's lane. Record why for the
            # caller so the 503 can say which bound refused it.
            ctx.metadata["shed_reason"] = "inflight"
            return None
        avail = kept
```

- [ ] **Step 4: The 503, in `execute_with_retries`**

Beside the existing rpm/tpm-cap branch (the one that builds
`WiwiError(429, "rate_limit_error", ...)`), add the shed branch:

```python
                    elif ctx.metadata.get("shed_reason") == "inflight":
                        last_err = WiwiError(
                            503, "server_overloaded",
                            f"all deployments for '{group_name}' are at their"
                            f" concurrency limit", retryable=True,
                            retry_after=self.router.settings.inflight_retry_after_s)
```

- [ ] **Step 5: Verify**

Run: `python3 -m pytest tests/test_router.py tests/test_fix_round116.py tests/test_round_robin.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add wiwi/router/router.py tests/test_fix_round116.py
git commit -m "Shed requests over a deployment's concurrency limit"
```

---

### Task 3: Priority lanes

**Files:**
- Modify: `wiwi/auth/service.py` (DDL, migration, SELECT, `AuthInfo`)
- Modify: `wiwi/router/router.py` (`_lane_ceiling`)
- Test: `tests/test_fix_round116.py` (append), `tests/test_admin_api.py` if the key
  admin surface needs the column

**Interfaces:**
- Consumes: `RouterSettings.priority_lanes`, `default_lane` (Task 1).
- Produces: `AuthInfo.priority: str | None`, `Router._lane_ceiling(d, ctx)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_bulk_is_refused_before_interactive_on_the_same_deployment():
    """The exact sequence that makes lanes worth having.

    max_inflight 4, {"interactive": 0.5, "bulk": 0.25}:
    bulk may hold 1 concurrent request, interactive 2.
    """
    # drive _dep.inflight through 0,1,2 and assert bulk is refused at 2
    # while interactive is still admitted at 2, and refused at 3.


def test_a_lane_is_always_admitted_at_least_one_slot():
    # max(1, ceil(...)) — a 1% lane on a cap of 2 must still serve one request.
    ...

def test_master_key_is_never_shed_for_lane_share():
    ...

def test_unknown_lane_falls_back_to_the_default():
    ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_fix_round116.py -q -k lane`
Expected: FAIL.

- [ ] **Step 3: Implement the lane ceiling** in `Router`:

```python
    def _lane_ceiling(self, d: Deployment, ctx: RequestContext) -> int | None:
        """Concurrency this request's lane may hold on *d*, or None if uncapped.

        A lane's share is capacity it may *use*, not capacity reserved against
        it: an interactive request is still admitted onto a deployment that a
        bulk request is already occupying. ``max(1, ...)`` keeps a tiny lane
        usable instead of deadlocking it out of a small pool.
        """
        settings = self.settings
        cap = d.effective_max_inflight(settings)
        if cap is None:
            return None
        lanes = settings.priority_lanes
        if not lanes:
            return cap
        auth = ctx.auth
        if auth is not None and getattr(auth, "key_type", "") == "master":
            # An operator's request outranks every lane.
            return cap
        lane = getattr(auth, "priority", None) or settings.default_lane
        share = lanes.get(lane, lanes.get(settings.default_lane))
        if share is None:
            return cap
        return max(1, math.ceil(cap * share))
```

- [ ] **Step 4: Plumb `priority` through the key**

`wiwi/auth/service.py`:
- `AuthInfo`: `priority: str | None = None`
- `CREATE_SQL`: `priority TEXT,` next to `owner_id`
- migration: the `if "budget_reserved" not in cols:` pattern →
  `if "priority" not in cols: await conn.execute(text("ALTER TABLE vkeys ADD COLUMN priority TEXT"))`
- SELECT: add `v.priority` and index it (`row[11]`), passing `priority=row[11]`.

- [ ] **Step 5: Verify**

Run: `python3 -m pytest tests/test_fix_round116.py tests/test_admin_api.py tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add wiwi/auth/service.py wiwi/router/router.py tests/test_fix_round116.py
git commit -m "Partition deployment concurrency into priority lanes"
```

---

### Task 4: Session affinity

**Files:**
- Modify: `wiwi/router/router.py`
- Modify: `wiwi/core/context.py` (`session_id`)
- Modify: `wiwi/server/app.py` (read the header into `ctx.metadata["session_id"]`)
- Test: `tests/test_fix_round116.py` (append)

**Interfaces:**
- Consumes: `RouterSettings.session_affinity`, `session_affinity_ttl_s` (Task 1).
- Produces: `Router._affinity: dict[str, tuple[int, float]]`, `RequestContext.session_id`.

- [ ] **Step 1: Write the failing tests**

```python
def test_affinity_holds_a_healthy_session_on_its_deployment():
    ...

def test_affinity_drops_a_pin_into_a_cooling_deployment():
    ...

def test_affinity_drops_an_expired_pin():
    ...

def test_affinity_off_is_byte_identical_to_today():
    ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_fix_round116.py -q -k affinity`
Expected: FAIL.

- [ ] **Step 3: Implement**

`Router.__init__`: `self._affinity: dict[str, tuple[int, float]] = {}`

`pick_deployment`, before the strategy call:

```python
        pinned = self._affinity_pin(ctx, avail, exclude)
        if pinned is not None:
            pinned.reserve_slot(getattr(ctx, "request_id", ""), est)
            return pinned
```

plus the helper (health-checked, never a stale pin):

```python
    def _affinity_pin(self, ctx, avail, exclude) -> Deployment | None:
        """The deployment this session is pinned to, if that pin is still valid.

        A pin is honoured only while the deployment is in the candidate set,
        healthy, and below this request's lane ceiling. A stale pin to a cooling
        deployment is the failure this exists to prevent, so the health check is
        not optional and there is no "sticky anyway" path.
        """
        if not self.settings.session_affinity:
            return None
        session = ctx.session_id
        if not session:
            return None
        now = time.monotonic()
        entry = self._affinity.get(session)
        if entry is None:
            return None
        dep_id, expiry = entry
        if expiry <= now:
            self._affinity.pop(session, None)
            return None
        for d in avail:
            if id(d) == dep_id and id(d) not in exclude and d.available:
                ceiling = self._lane_ceiling(d, ctx)
                if ceiling is None or d.inflight < ceiling:
                    return d
        return None
```

and re-pin after a successful pick (`chosen = self._choose(...)` … before
`reserve_slot`):

```python
        if self.settings.session_affinity and getattr(ctx, "session_id", None):
            self._affinity[ctx.session_id] = (
                id(chosen), time.monotonic() + self.settings.session_affinity_ttl_s)
```

`RequestContext`: `session_id: str | None = None`.
`app.py`, beside where `ctx` is built: read `x-wiwi-session-id` from the request
(and the `session_id` query parameter for header-less clients), truncated to 128
chars and rejected if empty.

- [ ] **Step 4: Verify**

Run: `python3 -m pytest tests/test_fix_round116.py tests/test_router.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add wiwi/router/router.py wiwi/core/context.py wiwi/server/app.py tests/test_fix_round116.py
git commit -m "Pin client sessions to healthy deployments"
```

---

### Task 5: Docs and live smoke

**Files:**
- Modify: `docs/CONFIG.md`, `docs/PROVIDERS.md`, `wiwi.yaml.example`

- [ ] **Step 1: Document**

- `docs/CONFIG.md`: a `router_settings` block covering all six new fields, with the
  lane-ceiling arithmetic and the 503-vs-429 rule spelled out (a client author needs
  to know which status they can retry).
- `docs/PROVIDERS.md`: the `max_inflight` per-model override and one worked lane
  example.
- `wiwi.yaml.example`: a commented `router_settings:` block.

- [ ] **Step 2: Gate**

```bash
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
```

- [ ] **Step 3: Live smoke**

Boot against the fake upstream with `router_settings.max_inflight: 1` and
`priority_lanes: {interactive: 0.9, bulk: 0.1}`; fire two concurrent requests with
two virtual keys in different lanes against a slow upstream and show one 503 with
`Retry-After` while the other completes; then re-fire with the interactive key alone
and show it succeeding where bulk was refused.

- [ ] **Step 4: Commit**

```bash
git add docs/ wiwi.yaml.example
git commit -m "Document concurrency caps, lanes and affinity"
```