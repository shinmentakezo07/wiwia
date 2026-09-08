# Recovery & Health Healer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `wiwi/core/recovery.py` with shared backoff/circuit-breaker primitives (deduplicating 3 sites) and an opt-in `HealthHealer` background service that actively restores sick keys/deployments via 1-token probes, with graduated (probation) recovery.

**Architecture:** One new core module holding `Backoff`, `CircuitBreaker`, `ProbeVerdict`/`probe_verdict`, `parse_retry_after`, `build_url`, and `HealthHealer`. Router dataclasses (`ProviderKey`, `Deployment`) gain probation semantics; the Cline/WorkBuddy auto-refresh services refactor onto `CircuitBreaker`; `lifespan` wires the healer exactly like the three existing refresh services. Config gains `HealerSettings` (default off).

**Tech Stack:** Python 3.12, asyncio, httpx, structlog, pytest 8 + pytest-asyncio (`asyncio_mode = "auto"`), respx.

**Spec:** `docs/superpowers/specs/2026-09-08-recovery-healer-design.md`

## Global Constraints

- Use ambient `python3` (3.12); NEVER `.venv/bin/python`. Gate: `python3 -m pytest tests/ -q && ruff check wiwi/ tests/` — both green before claiming done.
- Ruff: line-length 100, target py311. Tests: bare `async def test_*`, no `@pytest.mark.asyncio`; each test file self-contained; respx decorator form.
- No dialect/provider branching outside `wiwi/wire/` and `wiwi/providers/`. `wiwi/core/recovery.py` imports only stdlib + structlog + httpx + `wiwi.config` + `wiwi.ir.types` + `wiwi.providers.base` + `wiwi.providers.registry`. It must NEVER import `wiwi.router` or `wiwi.core.gateway` (they import recovery → cycle).
- The gateway never rewrites a client request. Retry payloads stay byte-identical to today.
- IR/streaming types are frozen; the healer mutates only router dataclasses via methods.
- structlog everywhere; never `print`.

---

### Task 1: `Backoff` + `CircuitBreaker` primitives

**Files:**
- Create: `wiwi/core/recovery.py`
- Create: `tests/test_recovery.py`

**Interfaces:**
- Produces (later tasks rely on these exact names):
  - `Backoff(base_s: float, cap_s: float, jitter_s: float = 0.0)` frozen dataclass, `.delay(attempt: int, retry_after: float | None = None) -> float`
  - `CircuitBreaker(base_s: float, cap_s: float, clock: Callable[[], float] = time.time)` with `.trip(target) -> None`, `.clear(target) -> None`, `.blocked(target) -> bool`, `.dead(target) -> bool`, `.mark_dead(target) -> None`, `.streak(target) -> int` (targets must be hashable)
  - Module logger: `log = structlog.get_logger("wiwi.recovery")`

- [ ] **Step 1: Write the failing tests** — create `tests/test_recovery.py` with exactly:

```python
"""Recovery primitives + HealthHealer: backoff, circuits, verdicts, probes, probation."""

import time

from wiwi.core.recovery import Backoff, CircuitBreaker


class TestBackoff:
    def test_monotone_in_attempt(self):
        b = Backoff(base_s=0.5, cap_s=30.0)
        delays = [b.delay(i) for i in range(6)]
        assert delays == sorted(delays)
        assert delays[0] == 0.5
        assert delays[3] == 4.0

    def test_cap_clamps(self):
        b = Backoff(base_s=0.5, cap_s=2.0)
        assert b.delay(10) == 2.0

    def test_jitter_bounds(self):
        b = Backoff(base_s=0.5, cap_s=30.0, jitter_s=0.25)
        for _ in range(20):
            assert 0.5 <= b.delay(0) <= 0.75

    def test_retry_after_floored_by_exponential_and_capped(self):
        b = Backoff(base_s=0.5, cap_s=30.0)
        assert b.delay(0, retry_after=10.0) == 10.0
        assert b.delay(0, retry_after=0.001) == 0.5
        assert b.delay(6, retry_after=99.0) == 30.0

    def test_negative_attempt_treated_as_zero(self):
        b = Backoff(base_s=0.5, cap_s=30.0)
        assert b.delay(-3) == 0.5

    def test_matches_router_inline_math(self):
        """Pins the exact expression this primitive replaces at the router's
        retry sleep: min(5.0, max(ra, 0.5 * 2**attempt)) + uniform(0, 0.25)."""
        b = Backoff(base_s=0.5, cap_s=5.0, jitter_s=0.25)
        expected = min(5.0, max(2.0, 0.5 * (2 ** 3)))  # 4.0
        d = b.delay(3, retry_after=2.0)
        assert expected <= d <= expected + 0.25


class TestCircuitBreaker:
    def test_trip_blocks_then_expires(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.trip("t")
        assert cb.blocked("t")
        now[0] += 61.0
        assert not cb.blocked("t")

    def test_streak_doubles_window(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.trip("t")
        now[0] += 61.0
        assert not cb.blocked("t")
        cb.trip("t")
        now[0] += 61.0   # second window is 120s: still blocked
        assert cb.blocked("t")
        now[0] += 61.0   # 122s past second trip: open again
        assert not cb.blocked("t")

    def test_cap(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=90.0, clock=lambda: now[0])
        cb.trip("t")            # 60s window
        now[0] += 61.0          # 1061
        cb.trip("t")            # 120 -> capped at 90 => until 1151
        now[0] += 89.0          # 1150: still blocked
        assert cb.blocked("t")
        now[0] += 2.0           # 1152: open
        assert not cb.blocked("t")

    def test_clear_resets(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.trip("t")
        cb.clear("t")
        assert not cb.blocked("t")
        assert cb.streak("t") == 0

    def test_mark_dead_is_permanent(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.mark_dead("t")
        now[0] += 10 ** 9
        assert cb.blocked("t")
        assert cb.dead("t")
        cb.clear("t")
        assert not cb.dead("t")

    def test_targets_are_independent(self):
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0)
        cb.mark_dead("a")
        assert cb.blocked("a")
        assert not cb.blocked("b")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_recovery.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wiwi.core.recovery'`

- [ ] **Step 3: Create `wiwi/core/recovery.py`** with exactly:

```python
"""Shared recovery primitives: backoff, circuit breakers.

Used by the router retry loop, the Cline/WorkBuddy auto-refresh services, and
the HealthHealer background service. Contracts only — no dialect or provider
branching (invariant: those live in wiwi/wire/ and wiwi/providers/). This
module must never import wiwi.router or wiwi.core.gateway: they import from
here, so the direction recovery -> router would create a cycle.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass

import structlog

log = structlog.get_logger("wiwi.recovery")


@dataclass(frozen=True)
class Backoff:
    """Exponential delay with jitter; honors upstream ``retry_after``.

    ``delay`` reproduces the router's historical inline math exactly:
    ``min(cap_s, max(retry_after or 0, base_s * 2**attempt)) + uniform(0, jitter_s)``
    — retry_after competes with the exponential floor and is capped.
    """

    base_s: float
    cap_s: float
    jitter_s: float = 0.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        exp = self.base_s * (2 ** max(0, attempt))
        ra = retry_after if (retry_after is not None and retry_after > 0) else 0.0
        d = min(self.cap_s, max(ra, exp))
        if self.jitter_s > 0:
            d += random.uniform(0.0, self.jitter_s)
        return d


class CircuitBreaker:
    """Per-target failure streak with exponential backoff and a permanent-dead state.

    Replaces the ``{"streak": n, "until": t}`` dict idiom previously duplicated
    in cline_auto_refresh and workbuddy_auto_refresh (streak >= 99 / until=inf
    meant permanently dead; that is ``mark_dead`` here). After a backoff window
    elapses the target is unblocked but its streak persists, so the next trip
    waits longer. Targets must be hashable.
    """

    def __init__(self, base_s: float, cap_s: float,
                 clock: Callable[[], float] = time.time) -> None:
        self._base = base_s
        self._cap = cap_s
        self._clock = clock
        self._streaks: dict[Hashable, int] = {}
        self._until: dict[Hashable, float] = {}
        self._dead: set[Hashable] = set()

    def trip(self, target: Hashable) -> None:
        streak = self._streaks.get(target, 0) + 1
        self._streaks[target] = streak
        window = min(self._cap, self._base * 2 ** (streak - 1))
        self._until[target] = self._clock() + window

    def clear(self, target: Hashable) -> None:
        self._streaks.pop(target, None)
        self._until.pop(target, None)
        self._dead.discard(target)

    def blocked(self, target: Hashable) -> bool:
        if target in self._dead:
            return True
        return self._clock() < self._until.get(target, 0.0)

    def dead(self, target: Hashable) -> bool:
        return target in self._dead

    def mark_dead(self, target: Hashable) -> None:
        self._dead.add(target)

    def streak(self, target: Hashable) -> int:
        return self._streaks.get(target, 0)
```

- [ ] **Step 4: Run tests to green**

Run: `python3 -m pytest tests/test_recovery.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add wiwi/core/recovery.py tests/test_recovery.py
git commit -m "feat: add Backoff and CircuitBreaker recovery primitives"
```

---

### Task 2: Route the router retry sleep through `Backoff`

**Files:**
- Modify: `wiwi/router/router.py` (retry sleep inside `execute_with_retries`, ~line 834-838; imports)

**Interfaces:**
- Consumes: `Backoff` from `wiwi.core.recovery` (Task 1).
- Produces: module constant `_RETRY_BACKOFF` on the router (no other API change).

- [ ] **Step 1: Add the import and constant.** In `wiwi/router/router.py`, add to the imports (after `from wiwi.server.stats import percentile`):

```python
from wiwi.core.recovery import Backoff
```

and a module constant right above `async def execute_with_retries`:

```python
# Historical retry sleep between failover attempts, extracted verbatim:
# min(5, max(retry_after, 0.5*2**attempt)) + uniform(0, 0.25).  Pinned by
# tests/test_recovery.py::TestBackoff::test_matches_router_inline_math.
_RETRY_BACKOFF = Backoff(base_s=0.5, cap_s=5.0, jitter_s=0.25)
```

- [ ] **Step 2: Replace the inline sleep.** Find (end of the `except WiwiError` branch in `execute_with_retries`):

```python
                fresh = any(d.available and id(d) not in tried_dep_ids for d in deps)
                if not fresh and attempt < router.settings.num_retries:
                    ra = e.retry_after or 0.0
                    await asyncio.sleep(min(5.0, max(ra, 0.5 * (2 ** attempt)))
                                        + random.uniform(0.0, 0.25))
```

Replace with:

```python
                fresh = any(d.available and id(d) not in tried_dep_ids for d in deps)
                if not fresh and attempt < router.settings.num_retries:
                    await asyncio.sleep(_RETRY_BACKOFF.delay(attempt, e.retry_after))
```

(`random` stays imported — still used by `pick_deployment`.)

- [ ] **Step 3: Verify behavior unchanged**

Run: `python3 -m pytest tests/test_router.py tests/test_fix_cycle_failover.py tests/test_recovery.py -q`
Expected: all PASS (no test edits needed — external behavior identical)

- [ ] **Step 4: Commit**

```bash
git add wiwi/router/router.py
git commit -m "refactor: route router retry sleep through shared Backoff primitive"
```

---

### Task 3: Refactor Cline/WorkBuddy circuits onto `CircuitBreaker`

**Files:**
- Modify: `wiwi/providers/cline_auto_refresh.py`
- Modify: `wiwi/providers/workbuddy_auto_refresh.py`
- Modify: `tests/test_cline_auto_refresh.py` (3 assertions that poke `_circuit` dict internals)

**Interfaces:**
- Consumes: `CircuitBreaker` from `wiwi.core.recovery` (Task 1) with `clock=time.time` (wall clock — matches current behavior in both services).
- Produces: `worker._circuit` is now a `CircuitBreaker`; external contract unchanged (blocked-while-backoff, permanent-dead on unrecoverable).

- [ ] **Step 1: Refactor `cline_auto_refresh.py`.** Replace the import line `from wiwi.providers import cline_oauth` with:

```python
from wiwi.core.recovery import CircuitBreaker
from wiwi.providers import cline_oauth
```

In `ClineAutoRefresh.__init__`, replace:

```python
        self._circuit: dict[str, dict[str, Any]] = {}
```

with:

```python
        self._circuit = CircuitBreaker(base_s=CIRCUIT_BASE_S, cap_s=CIRCUIT_CAP_S,
                                       clock=time.time)
```

Replace `_trip_circuit`'s body:

```python
    def _trip_circuit(self, name: str) -> None:
        self._circuit.trip(name)
```

Replace every occurrence of the blocked-check pattern:

```python
        cb = self._circuit.get(name)
        if cb and time.time() < cb.get("until", 0):
            return False
```

with:

```python
        if self._circuit.blocked(name):
            return False
```

(in the `hook` inside `refresh_for_provider` the variable is `provider_name`; keep `return False`.)

Replace every `self._circuit[provider_name] = {"streak": 99, "until": float("inf")}` (two sites: `_do_refresh` and `hook`) with:

```python
        self._circuit.mark_dead(provider_name)
```

Replace every `self._circuit.pop(name, None)` / `self._circuit.pop(provider_name, None)` (two sites) with:

```python
        self._circuit.clear(name)
```

(match the surrounding variable name). Fix the resulting `Any` import if now unused (`from typing import TYPE_CHECKING, Any` — `Any` is still used in the record dict type hints; leave as is if ruff is happy).

- [ ] **Step 2: Refactor `workbuddy_auto_refresh.py` the same way.** Add the same `CircuitBreaker` import; constructor: replace `self._circuit: dict[tuple[str, str], dict[str, Any]] = {}` with:

```python
        self._circuit = CircuitBreaker(base_s=CIRCUIT_BASE_S, cap_s=CIRCUIT_CAP_S,
                                       clock=time.time)
```

Then apply the same three transformations at every site (line numbers in the current file: blocked-checks at ~106 and ~189; `streak: 99` assignments at ~143, ~205, ~239; pops at ~152, ~210, ~244; `_trip_circuit` at ~169):

- `cb = self._circuit.get(ident)` + `if cb and time.time() < cb.get("until", 0):` → `if self._circuit.blocked(ident):`
- `self._circuit[ident] = {"streak": 99, "until": float("inf")}` → `self._circuit.mark_dead(ident)`
- `self._circuit.pop(ident, None)` → `self._circuit.clear(ident)`
- `_trip_circuit` body → `self._circuit.trip(ident)`

- [ ] **Step 3: Update the 3 internal-poking assertions in `tests/test_cline_auto_refresh.py`:**

`test_unrecoverable_sets_circuit_breaker_forever` — replace:

```python
    assert "cline-prov" in worker._circuit
    assert worker._circuit["cline-prov"]["until"] == float("inf")
```

with:

```python
    assert worker._circuit.dead("cline-prov")
    assert worker._circuit.blocked("cline-prov")
```

`test_transient_failure_trips_backoff` — replace:

```python
    cb = worker._circuit["cline-prov"]
    assert cb["streak"] == 1
    assert cb["until"] > time.time()
```

with:

```python
    assert worker._circuit.streak("cline-prov") == 1
    assert worker._circuit.blocked("cline-prov")
```

`test_circuit_breaker_skips_when_in_backoff` — replace:

```python
    worker._circuit["cline-prov"] = {"streak": 2, "until": time.time() + 9999}
```

with:

```python
    worker._circuit.trip("cline-prov")
    worker._circuit.trip("cline-prov")
```

(two trips = streak 2, blocked for base 300s — longer than the sweep gap).

- [ ] **Step 4: Run affected suites**

Run: `python3 -m pytest tests/test_cline_auto_refresh.py tests/test_workbuddy_auto_refresh.py tests/test_recovery.py -q`
Expected: all PASS (`_circuit` no longer appears in any workbuddy test — verified by grep — so that file needs no edits)

- [ ] **Step 5: Commit**

```bash
git add wiwi/providers/cline_auto_refresh.py wiwi/providers/workbuddy_auto_refresh.py tests/test_cline_auto_refresh.py
git commit -m "refactor: unify Cline/WorkBuddy circuit breakers onto CircuitBreaker"
```

---

### Task 4: `HealerSettings` config

**Files:**
- Modify: `wiwi/config.py` (`HealerSettings` class + `WiwiConfig.healer` field)
- Test: append to `tests/test_recovery.py`

**Interfaces:**
- Produces: `from wiwi.config import HealerSettings`; `WiwiConfig().healer` with fields `enabled: bool = False`, `tick_s: float = 30.0`, `probe_timeout_s: float = 10.0`, `max_probes_per_sweep: int = 8`, `min_probe_interval_s: float = 30.0`, `probe_backoff_base_s: float = 60.0`, `probe_backoff_cap_s: float = 3600.0`, `probes_to_restore: int = 2`, `probation_weight: float = 0.5`.

- [ ] **Step 1: Add failing config test** — append to `tests/test_recovery.py`:

```python
from wiwi.config import HealerSettings, WiwiConfig


def test_healer_settings_defaults():
    c = WiwiConfig()
    assert c.healer.enabled is False
    assert c.healer.tick_s == 30.0
    assert c.healer.probes_to_restore == 2
    assert c.healer.probation_weight == 0.5


def test_healer_settings_yaml_section():
    c = WiwiConfig.model_validate({"healer": {"enabled": True, "tick_s": 5}})
    assert c.healer.enabled is True
    assert c.healer.tick_s == 5.0
```

Run: `python3 -m pytest tests/test_recovery.py -q`
Expected: FAIL — `ImportError: cannot import name 'HealerSettings'`

- [ ] **Step 2: Implement.** In `wiwi/config.py`, add right after the `CacheSettings` class:

```python
class HealerSettings(BaseModel):
    """HealthHealer (wiwi/core/recovery.py): background service that probes
    cooling/invalid keys and cooled deployments with a 1-token completion and
    restores them early into a probation state. Probes spend real provider
    money, so this is off by default (same opt-in ethos as CacheSettings)."""
    enabled: bool = False
    tick_s: float = 30.0            # sweep cadence
    probe_timeout_s: float = 10.0
    max_probes_per_sweep: int = 8   # blast-radius cap per tick
    min_probe_interval_s: float = 30.0  # earliest re-probe of the same target
    probe_backoff_base_s: float = 60.0  # per-target circuit base on failed probes
    probe_backoff_cap_s: float = 3600.0
    probes_to_restore: int = 2      # consecutive healthy probes before restore
    probation_weight: float = 0.5   # WRR weight multiplier while on probation
```

and add to `WiwiConfig` (after `cache_settings`):

```python
    healer: HealerSettings = Field(default_factory=HealerSettings)
```

- [ ] **Step 3: Run tests to green**

Run: `python3 -m pytest tests/test_recovery.py tests/test_config.py -q`
Expected: all PASS (if `tests/test_config.py` doesn't exist, just run the first file)

- [ ] **Step 4: Commit**

```bash
git add wiwi/config.py tests/test_recovery.py
git commit -m "feat: add HealerSettings config section (default off)"
```

---

### Task 5: `ProbeVerdict` + `parse_retry_after`

**Files:**
- Modify: `wiwi/core/recovery.py` (append)
- Modify: `wiwi/core/gateway.py` (`_parse_retry_after` delegates)
- Test: append to `tests/test_recovery.py`

**Interfaces:**
- Produces:
  - `ProbeVerdict(enum.Enum)` with values `HEALTHY`, `ALIVE_THROTTLED`, `CREDS_VALID_MODEL_BAD`, `CREDS_REJECTED`, `UNREACHABLE`
  - `probe_verdict(status: int | None) -> ProbeVerdict` (`None` = transport failure)
  - `parse_retry_after(value: str | None) -> float | None`

- [ ] **Step 1: Add failing tests** — append to `tests/test_recovery.py`:

```python
from wiwi.core.recovery import ProbeVerdict, probe_verdict


class TestProbeVerdict:
    def test_table(self):
        assert probe_verdict(200) is ProbeVerdict.HEALTHY
        assert probe_verdict(429) is ProbeVerdict.ALIVE_THROTTLED
        assert probe_verdict(401) is ProbeVerdict.CREDS_REJECTED
        assert probe_verdict(403) is ProbeVerdict.CREDS_REJECTED
        assert probe_verdict(400) is ProbeVerdict.CREDS_VALID_MODEL_BAD
        assert probe_verdict(404) is ProbeVerdict.CREDS_VALID_MODEL_BAD
        for s in (None, 408, 500, 502, 503, 504, 529, 418):
            assert probe_verdict(s) is ProbeVerdict.UNREACHABLE


def test_parse_retry_after():
    from wiwi.core.recovery import parse_retry_after
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("1.5") == 1.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after("") is None
```

Run: `python3 -m pytest tests/test_recovery.py -q`
Expected: FAIL — `ImportError: cannot import name 'ProbeVerdict'`

- [ ] **Step 2: Implement in `wiwi/core/recovery.py`.** Add `from enum import Enum` to the imports and append:

```python
class ProbeVerdict(Enum):
    """Classification of a HealthHealer probe outcome (see specs/
    2026-09-08-recovery-healer-design.md, Part B verdict table)."""

    HEALTHY = "healthy"
    ALIVE_THROTTLED = "alive_throttled"
    CREDS_VALID_MODEL_BAD = "creds_valid_model_bad"
    CREDS_REJECTED = "creds_rejected"
    UNREACHABLE = "unreachable"


def probe_verdict(status: int | None) -> ProbeVerdict:
    """Classify a probe HTTP outcome; ``status=None`` means transport failure."""
    if status == 200:
        return ProbeVerdict.HEALTHY
    if status == 429:
        return ProbeVerdict.ALIVE_THROTTLED
    if status in (401, 403):
        return ProbeVerdict.CREDS_REJECTED
    if status in (400, 404):
        return ProbeVerdict.CREDS_VALID_MODEL_BAD
    return ProbeVerdict.UNREACHABLE


def parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP ``Retry-After`` seconds header; None when absent/garbage."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
```

- [ ] **Step 3: Delegate the gateway's private parser.** In `wiwi/core/gateway.py`, the existing `_parse_retry_after` (near line 1065) becomes a thin wrapper — replace its body:

```python
def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    # (HTTP-date form is intentionally unsupported, as before)
```

with:

```python
def _parse_retry_after(value: str | None) -> float | None:
    return parse_retry_after(value)
```

and add `from wiwi.core.recovery import parse_retry_after` to the gateway's `wiwi.core`/`wiwi.providers` import block. (Check the original body first: if it ends with `return None` instead of `pass`, keep the delegation — the external contract is identical either way.)

- [ ] **Step 4: Run to green**

Run: `python3 -m pytest tests/test_recovery.py tests/test_gateway.py tests/test_router.py -q`
Expected: all PASS (substitute whatever gateway/router test files exist; the full suite in Task 9 is the real gate)

- [ ] **Step 5: Commit**

```bash
git add wiwi/core/recovery.py wiwi/core/gateway.py tests/test_recovery.py
git commit -m "feat: add ProbeVerdict classification and shared parse_retry_after"
```

---

### Task 6: Probation semantics in the router

**Files:**
- Modify: `wiwi/router/router.py` (`ProviderKey`, `Deployment`, `ProviderAccount.pick_key`/`on_result`, `Router.__init__`/`pick_deployment`, `execute_with_retries`)
- Test: append to `tests/test_recovery.py`

**Interfaces:**
- Consumes: `HealerSettings.probation_weight` (Task 4).
- Produces:
  - `ProviderKey.mark_recovered() -> None` (status → `"probation"`, cooldown/err_count/WRR reset)
  - `Deployment.probation: bool` field, `Deployment.mark_recovered() -> None`
  - `ProviderAccount.pick_key(exclude_labels=None, probation_weight: float = 1.0)`
  - `Router.probation_weight: float`

- [ ] **Step 1: Add failing tests** — append to `tests/test_recovery.py`:

```python
import pytest

from wiwi.config import DeploymentParams, KeyDef, ModelEntry, ProviderDef
from wiwi.core.context import RequestContext
from wiwi.ir.types import Message, Request, TextPart
from wiwi.router.router import Router, execute_with_retries


def _router_config(n_keys: int = 2) -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label=f"k{i}", key=f"secret{i}")
                              for i in range(n_keys)]),
            ProviderDef(name="p2", provider="openai",
                        keys=[KeyDef(label="p2k", key="p2secret")]),
        ],
        model_list=[
            ModelEntry(model_name="g",
                       wiwi_params=DeploymentParams(provider="p1", model="m")),
            ModelEntry(model_name="g",
                       wiwi_params=DeploymentParams(provider="p2", model="m2")),
        ],
    )


def _ctx(group: str = "g") -> RequestContext:
    ir_req = Request(model=group,
                     messages=[Message(role="user", parts=[TextPart(text="hi")])])
    return RequestContext(surface="chat", ir_req=ir_req, group=group)


class TestProbation:
    async def test_probation_key_available_and_half_weight(self):
        r = Router(_router_config())
        acct = r.providers["p1"]
        acct.keys[0].mark_recovered()
        assert acct.keys[0].status == "probation"
        assert acct.keys[0].available
        picks = {"k0": 0, "k1": 0}
        for _ in range(100):
            k, _ = await acct.pick_key(probation_weight=0.5)
            picks[k.label] += 1
        # smooth WRR over effective weights (1.0 vs 0.5) is exactly 2:1
        assert abs(picks["k0"] / picks["k1"] - 2.0) < 0.2

    async def test_probation_key_graduates_on_success(self):
        r = Router(_router_config())
        acct = r.providers["p1"]
        k = acct.keys[0]
        k.mark_recovered()
        acct.on_result(k, 200, None)
        assert k.status == "active"

    async def test_probation_key_demotes_on_failure(self):
        r = Router(_router_config())
        acct = r.providers["p1"]
        k = acct.keys[0]
        k.mark_recovered()
        acct.on_result(k, 500, None, failover_mode="any_error")
        assert k.status == "cooling"

    async def test_active_key_stays_active_on_success(self):
        r = Router(_router_config())
        acct = r.providers["p1"]
        acct.on_result(acct.keys[0], 200, None)
        assert acct.keys[0].status == "active"

    async def test_pick_deployment_prefers_non_probation(self):
        r = Router(_router_config())
        sick = r.groups["g"][0]
        sick.probation = True
        for _ in range(10):
            assert r.pick_deployment(r.groups["g"], _ctx()) is not sick

    async def test_pick_deployment_falls_back_to_probation_alone(self):
        r = Router(_router_config())
        dep = r.groups["g"][0]
        dep.probation = True
        # group "g" has two deployments; exclude the healthy one
        healthy = r.groups["g"][1]
        assert r.pick_deployment(r.groups["g"], _ctx(),
                                 exclude={id(healthy)}) is dep

    async def test_deployment_graduates_via_execute_with_retries(self):
        r = Router(_router_config())
        dep = r.groups["g"][0]
        dep.probation = True

        async def call_one(d, key, c):
            return "ok"

        await execute_with_retries(r, _ctx(), call_one)
        assert dep.probation is False

    def test_record_fail_clears_probation(self):
        r = Router(_router_config())
        dep = r.groups["g"][0]
        dep.probation = True
        dep.record_fail(allowed_fails=3, cooldown_time=30.0)
        assert dep.probation is False
```

Run: `python3 -m pytest tests/test_recovery.py -q`
Expected: FAIL — `AttributeError: 'ProviderKey' object has no attribute 'mark_recovered'`

- [ ] **Step 2: Implement in `wiwi/router/router.py`:**

(a) Add `import structlog` and module logger after the existing imports:

```python
log = structlog.get_logger("wiwi.router")
```

(b) `ProviderKey`: update the status comment to `# active | cooling | invalid | disabled | probation`, change `available` to:

```python
    @property
    def available(self) -> bool:
        return (self.enabled and self.status in ("active", "cooling", "probation")
                and not (self.status == "cooling"
                         and time.monotonic() < self.cooldown_until))
```

and add after `recover()`:

```python
    def mark_recovered(self) -> None:
        """Healer restore: enter probation with a fresh slate (cooldown cleared,
        fail streak and WRR deficit reset). Graduates to active on the first
        credited success; a failure demotes via the normal cooldown path."""
        self.status = "probation"
        self.cooldown_until = 0.0
        self.err_count = 0
        self.current_weight = 0.0
```

(c) `ProviderAccount.pick_key` — new signature and WRR block:

```python
    async def pick_key(self, exclude_labels: set[str] | None = None,
                       probation_weight: float = 1.0) -> tuple[ProviderKey | None, float]:
```

(docstring gains one line: ``probation_weight`` scales the effective WRR weight of keys in ``probation`` status.) Replace the smooth-WRR section (from `# Smooth WRR (nginx algorithm)...` through `return best, 0.0`) with:

```python
            # Smooth WRR (nginx algorithm) over the (possibly excluded) avail
            # list.  Probation keys carry a reduced effective weight so a
            # just-healed key earns back traffic gradually.
            weights = [k.weight * (probation_weight if k.status == "probation" else 1.0)
                       for k in avail]
            total = sum(weights)
            for k, w in zip(avail, weights):
                k.current_weight += w
            best = max(avail, key=lambda k: k.current_weight)
            best.current_weight -= total
            best.last_used = time.monotonic()
            return best, 0.0
```

(d) `ProviderAccount.on_result` — the 200 branch becomes:

```python
        if status == 200:
            key.req_count += 1
            # any consecutive-fail streak is broken on success
            key.err_count = 0
            if key.status == "probation":
                key.status = "active"
                log.info("healer_probation_graduated", kind="key",
                         provider=self.name, key=key.label)
            return
```

(e) `Deployment` — add the field after `inflight: int = 0`:

```python
    # Probation: set by the HealthHealer on restore. pick_deployment prefers
    # non-probation deployments; execute_with_retries graduates on success,
    # record_fail demotes.
    probation: bool = False
```

add `mark_recovered` after `record_fail`:

```python
    def mark_recovered(self) -> None:
        """Healer restore: clear the cooldown and enter probation."""
        self.probation = True
        self.cooldown_until = 0.0
        self.fails.clear()
```

and make `record_fail` demote first:

```python
    def record_fail(self, allowed_fails: int, cooldown_time: float) -> None:
        self.probation = False  # demoted: the cooldown path re-arms from here
        now = time.monotonic()
```

(f) `Router.__init__` — after `self.settings: RouterSettings = config.router_settings` add:

```python
        # WRR multiplier for keys in probation status (from HealerSettings).
        self.probation_weight = config.healer.probation_weight
```

(g) `pick_deployment` — insert the preference filter between the `if not avail: return None` and `strategy = self.settings.routing_strategy` lines:

```python
        # Prefer fully-healthy deployments; probation ones only serve when no
        # fresh sibling exists (the healer restored them on a trial basis).
        fresh = [d for d in avail if not d.probation]
        if fresh:
            avail = fresh
```

(h) `execute_with_retries` — pass the multiplier at the pick_key call:

```python
            key, retry_in = await dep.provider.pick_key(
                exclude_labels={lbl for (pn, lbl) in tried_key_labels if pn == dep.provider.name},
                probation_weight=getattr(router, "probation_weight", 1.0),
            )
```

and in the success branch, extend the `if not getattr(ctx, "_defer_key_credit", False):` block (after the existing `on_result_locked` call):

```python
                    if dep.probation:
                        dep.probation = False
                        _proxy("info",
                               f"deployment '{dep.group}/{dep.model_id}'"
                               f" graduated from probation")
                        log.info("healer_probation_graduated", kind="deployment",
                                 provider=dep.provider.name, group=dep.group)
```

- [ ] **Step 3: Run to green (new + existing router suites)**

Run: `python3 -m pytest tests/test_recovery.py tests/test_router.py tests/test_round_robin.py tests/test_fix_cycle_failover.py tests/test_fix_round6.py -q`
Expected: all PASS (pick_key default `probation_weight=1.0` keeps every existing call site behavior-identical)

- [ ] **Step 4: Commit**

```bash
git add wiwi/router/router.py tests/test_recovery.py
git commit -m "feat: probation state for keys and deployments (half-weight WRR, graduation)"
```

---

### Task 7: `build_url` extraction + `HealthHealer`

**Files:**
- Modify: `wiwi/core/recovery.py` (append `build_url`, `PROBE_MAX_TOKENS`, `_probe_request`, `_TargetState`, `HealthHealer`)
- Modify: `wiwi/core/gateway.py` (`_build_url` becomes a wrapper)
- Test: append to `tests/test_recovery.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6; `error_from_provider_status`, `ProviderKeyRef` from `wiwi.providers.base`; `fresh_adapter` from `wiwi.providers.registry`; `HealerSettings` from `wiwi.config`; IR types from `wiwi.ir.types`.
- Produces:
  - `build_url(adapter, base_url: str, model_id: str, provider_type: str, stream: bool, key: ProviderKeyRef) -> str`
  - `HealthHealer(router, settings: HealerSettings, log_proxy=None)` with `.start()`, `.stop()`, `await ._sweep() -> int` (public-for-tests), `._circuits: dict[str, CircuitBreaker]` with keys `"key"`/`"dep"` (public-for-tests), per-target idents: key `("p1", "k0")`, dep `("g", "p1", "m")`
  - Constant `MODEL_BAD_DEAD_STREAK = 3`, `PROBE_MAX_TOKENS = 1`

- [ ] **Step 1: Add failing tests** — append to `tests/test_recovery.py`:

```python
import httpx
import respx

from wiwi.core.recovery import HealthHealer
from wiwi.server.app import create_app
from asgi_lifespan import LifespanManager

PROBE_OK_BODY = {
    "id": "chatcmpl-probe", "object": "chat.completion", "model": "m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _sick_router() -> tuple[Router, object, object]:
    """Router with key0 cooling and the p1 deployment cooled down."""
    r = Router(_router_config())
    key = r.providers["p1"].keys[0]
    key.mark_cooling(999.0)
    dep = r.groups["g"][0]
    dep.cooldown_until = time.monotonic() + 999.0
    return r, key, dep


def _healer(router, **overrides) -> HealthHealer:
    from wiwi.config import HealerSettings
    s = HealerSettings(enabled=True, tick_s=0.05, min_probe_interval_s=0.0,
                       **overrides)
    return HealthHealer(router, s)


class TestHealthHealer:
    @respx.mock
    async def test_restores_after_consecutive_probes(self):
        r, key, dep = _sick_router()
        respx.post(OPENAI_URL).respond(json=PROBE_OK_BODY)
        h = _healer(r, probes_to_restore=2)
        assert await h._sweep() == 1
        assert key.status == "cooling"          # 1 of 2 successes: no restore yet
        await h._sweep()
        assert key.status == "probation"
        assert key.available
        assert dep.cooldown_until <= time.monotonic()
        assert dep.probation is True
        await h.stop()

    @respx.mock
    async def test_429_extends_cooling_without_trip(self):
        r, key, dep = _sick_router()
        key.mark_cooling(5.0)
        respx.post(OPENAI_URL).respond(status_code=429,
                                       headers={"retry-after": "120"})
        h = _healer(r)
        await h._sweep()
        assert key.status == "cooling"
        assert key.cooldown_until > time.monotonic() + 100.0
        assert not h._circuits["key"].blocked(("p1", "k0"))
        assert not h._circuits["dep"].blocked(("g", "p1", "m"))
        await h.stop()

    @respx.mock
    async def test_401_trips_key_circuit_and_next_sweep_skips(self):
        r, key, _ = _sick_router()
        key.status = "invalid"
        route = respx.post(OPENAI_URL).respond(status_code=401, text="nope")
        h = _healer(r)
        await h._sweep()
        assert key.status == "invalid"
        assert h._circuits["key"].blocked(("p1", "k0"))
        await h._sweep()
        assert route.call_count == 1            # skipped while blocked
        await h.stop()

    @respx.mock
    async def test_400_restores_key_and_kills_deployment(self):
        r, key, dep = _sick_router()
        key.status = "invalid"
        respx.post(OPENAI_URL).respond(
            status_code=400, json={"error": {"message": "model not found"}})
        # zero-base circuits keep the escalation deterministic in-test
        h = _healer(r, probes_to_restore=1, probe_backoff_base_s=0.0)
        await h._sweep()
        assert key.status == "probation"        # creds proven -> restore-eligible
        assert not h._circuits["key"].blocked(("p1", "k0"))
        await h._sweep()
        await h._sweep()
        assert h._circuits["dep"].dead(("g", "p1", "m"))
        await h.stop()

    @respx.mock
    async def test_never_probes_disabled_keys(self):
        r = Router(_router_config())
        key = r.providers["p1"].keys[0]
        key.enabled = False
        key.status = "cooling"
        key.mark_cooling(999.0)
        route = respx.post(OPENAI_URL).respond(json=PROBE_OK_BODY)
        h = _healer(r)
        assert await h._sweep() == 0
        assert not route.called
        await h.stop()

    @respx.mock
    async def test_respects_per_sweep_cap(self):
        r = Router(_router_config(n_keys=3))
        for k in r.providers["p1"].keys:
            k.mark_cooling(999.0)
        respx.post(OPENAI_URL).respond(json=PROBE_OK_BODY)
        h = _healer(r, max_probes_per_sweep=2)
        assert await h._sweep() == 2
        await h.stop()

    @respx.mock
    async def test_force_stream_probes_with_stream_true(self):
        cfg = WiwiConfig(
            providers=[ProviderDef(name="cl", provider="cline",
                                   keys=[KeyDef(label="a", key="tok")])],
            model_list=[ModelEntry(model_name="g",
                                   wiwi_params=DeploymentParams(provider="cl",
                                                                model="m"))],
        )
        r = Router(cfg)
        key = r.providers["cl"].keys[0]
        key.mark_cooling(999.0)
        route = respx.post("https://api.cline.bot/api/v1/chat/completions").respond(
            status_code=200, text="data: [DONE]\n\n")
        h = _healer(r, probes_to_restore=1)
        await h._sweep()
        assert key.status == "probation"
        import orjson
        body = orjson.loads(route.calls.last.request.content)
        assert body["stream"] is True
        await h.stop()

    async def test_disabled_start_is_a_noop(self):
        from wiwi.config import HealerSettings
        h = HealthHealer(Router(_router_config()), HealerSettings(enabled=False))
        h.start()
        assert h._task is None
        await h.stop()


class TestLifespanWiring:
    async def test_healer_starts_and_stops_with_app(self):
        cfg = WiwiConfig(
            providers=[ProviderDef(name="p1", provider="openai",
                                   keys=[KeyDef(label="a", key="k")])],
            model_list=[ModelEntry(model_name="g",
                                   wiwi_params=DeploymentParams(provider="p1",
                                                                model="m"))],
            general_settings=__import__("wiwi.config", fromlist=["GeneralSettings"])
            .GeneralSettings(master_key="sk-wiwi-master-test",
                             database_url="sqlite+aiosqlite:///:memory:"),
            healer=HealerSettings(enabled=True, tick_s=0.05),
        )
        app = create_app(cfg)
        async with LifespanManager(app):
            state = app.state.wiwi
            assert state.healer is not None
            assert state.healer._task is not None and not state.healer._task.done()
        assert state.healer._task is None
```

(If `HealerSettings` isn't imported at top of the file yet, add it to the `wiwi.config` import line.)

Run: `python3 -m pytest tests/test_recovery.py -q`
Expected: FAIL — `ImportError: cannot import name 'HealthHealer'`

- [ ] **Step 2: Extract `build_url` into recovery.** Append to `wiwi/core/recovery.py`:

```python
def build_url(adapter, base_url: str, model_id: str, provider_type: str,
              stream: bool, key) -> str:
    """Resolve the upstream URL for a call, appending the credential for
    providers that need it in the querystring (Gemini) and honoring adapter-
    declared ``build_url_for_key`` (WorkBuddy CN vs global domains). Extracted
    verbatim from gateway._build_url so the healer and the gateway share one
    implementation; the key argument is a ProviderKeyRef."""
    build = getattr(adapter, "build_url_for_key", None)
    if build is not None:
        return build(base_url, model_id, stream, key)
    url = adapter.build_url(base_url, model_id, stream)
    if provider_type == "gemini" and url.endswith(("?key=", "&key=")):
        url += key.secret
    return url
```

Then in `wiwi/core/gateway.py`, replace the whole `_build_url` function (near line 1051) with:

```python
def _build_url(adapter, dep: Deployment, key: ProviderKeyRef, stream: bool) -> str:
    """Build the upstream URL — delegates to the shared recovery.build_url."""
    return build_url(adapter, dep.provider.base_url, dep.model_id,
                     dep.provider.provider_type, stream, key)
```

and extend the gateway's `from wiwi.core.recovery import parse_retry_after` import (Task 5) to:

```python
from wiwi.core.recovery import build_url, parse_retry_after
```

- [ ] **Step 3: Implement `HealthHealer`.** Append to `wiwi/core/recovery.py` (also add these imports at the top of the file: `import asyncio`, `import contextlib`, `import httpx`, `from enum import Enum` already added in Task 5, plus
`from wiwi.ir import types as ir`, `from wiwi.providers.base import ProviderKeyRef, error_from_provider_status`, `from wiwi.providers.registry import fresh_adapter`, `from wiwi.config import HealerSettings`):

```python
MODEL_BAD_DEAD_STREAK = 3  # consecutive creds-valid/model-bad probes before a
                           # deployment circuit is marked permanently dead
PROBE_MAX_TOKENS = 1
_PROBE_CONCURRENCY = 4


def _probe_request(stream: bool) -> ir.Request:
    """Minimal 1-token completion request; cheap on every provider."""
    return ir.Request(
        model="wiwi-health-probe",
        messages=[ir.Message(role="user", parts=[ir.TextPart(text="ping")])],
        gen_params=ir.GenParams(max_tokens=PROBE_MAX_TOKENS),
        stream=stream,
    )


@dataclass
class _TargetState:
    streak: int = 0          # consecutive healthy probes
    last_probe: float = 0.0  # monotonic timestamp of last probe


class HealthHealer:
    """Background service that probes sick keys/deployments and restores them.

    A probe is one 1-token completion against a (deployment, key) pair built
    through the adapter seam (fresh_adapter -> encode_request -> build_url ->
    POST) on this service's own short-timeout httpx client — no RequestContext,
    no retries, no rate-limit or billing interaction, no client-request
    rewriting. Streaming-only upstreams (force_stream adapters: Cline,
    WorkBuddy) are probed with stream=True; HTTP 200 is the healthy signal in
    every case (the body is never decoded). A restore puts the target into
    probation; see the design spec for the verdict table. Lifecycle contract
    matches ClineAutoRefresh: start()/stop() around a named, cancellable task.
    """

    def __init__(self, router, settings: HealerSettings,
                 log_proxy: Callable[..., None] | None = None) -> None:
        self._router = router
        self._s = settings
        self._log_proxy = log_proxy or (lambda *a, **k: None)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._circuits = {
            "key": CircuitBreaker(settings.probe_backoff_base_s,
                                  settings.probe_backoff_cap_s,
                                  clock=time.monotonic),
            "dep": CircuitBreaker(settings.probe_backoff_base_s,
                                  settings.probe_backoff_cap_s,
                                  clock=time.monotonic),
        }
        self._state: dict[tuple, _TargetState] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if not self._s.enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="health-healer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self._s.tick_s)  # settle at startup
            while not self._stop.is_set():
                try:
                    await self._sweep()
                except Exception:
                    log.exception("healer_sweep_error")
                try:
                    await asyncio.wait_for(self._stop.wait(),
                                           timeout=self._s.tick_s)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    # -- sweep ---------------------------------------------------------------

    async def _sweep(self) -> int:
        """One probe pass over filtered candidates. Returns probes issued."""
        batch = self._collect_pairs()[: self._s.max_probes_per_sweep]
        if not batch:
            return 0
        sem = asyncio.Semaphore(_PROBE_CONCURRENCY)

        async def run_one(dep, key):
            async with sem:
                await self._probe_pair(dep, key)

        await asyncio.gather(*(run_one(d, k) for d, k in batch))
        return len(batch)

    def _collect_pairs(self) -> list[tuple]:
        """(deployment, key) pairs for every sick key and cooled deployment.

        Sick keys pair with the provider's first deployment; cooled
        deployments pair with the provider's first available key. Circuit/
        interval filters drop candidates that must not be probed yet. Reads
        router state live so admin mutations are picked up each sweep.
        """
        now = time.monotonic()
        all_deps = [d for deps in self._router.groups.values() for d in deps]
        deps_by_provider: dict[str, list] = {}
        for d in all_deps:
            deps_by_provider.setdefault(d.provider.name, []).append(d)
        pairs: list[tuple] = []
        seen: set[tuple[str, str, str]] = set()

        def ok(kind: str, ident: tuple) -> bool:
            if self._circuits[kind].blocked(ident):
                return False
            st = self._state.get((kind, ident))
            return st is None or now - st.last_probe >= self._s.min_probe_interval_s

        for pname, pdeps in deps_by_provider.items():
            acct = self._router.providers.get(pname)
            if acct is None or not pdeps:
                continue
            for k in acct.keys:
                if k.status not in ("cooling", "invalid"):
                    continue
                if k.status == "cooling" and now >= k.cooldown_until:
                    continue  # recover() handles natural expiry
                if not ok("key", (pname, k.label)):
                    continue
                ident = (pdeps[0].group, pname, k.label)
                if ident in seen:
                    continue
                seen.add(ident)
                pairs.append((pdeps[0], k))

        for dep in all_deps:
            if not (now < dep.cooldown_until):
                continue
            key = next((k for k in dep.provider.keys if k.available), None)
            if key is None:
                continue
            if not ok("dep", (dep.group, dep.provider.name, dep.model_id)):
                continue
            ident = (dep.group, dep.provider.name, key.label)
            if ident in seen:
                continue
            seen.add(ident)
            pairs.append((dep, key))
        return pairs

    # -- probe + verdict -----------------------------------------------------

    async def _probe_pair(self, dep, key) -> None:
        """Probe one (deployment, key) pair and apply the verdict to both."""
        verdict, detail, retry_after = await self._probe(dep, key)
        now = time.monotonic()
        kid = (dep.provider.name, key.label)
        did = (dep.group, dep.provider.name, dep.model_id)
        kst = self._state.setdefault(("key", kid), _TargetState())
        dst = self._state.setdefault(("dep", did), _TargetState())
        kst.last_probe = dst.last_probe = now

        if verdict is ProbeVerdict.ALIVE_THROTTLED:
            # Alive but exhausted: extend the key's cooling per retry_after.
            # Never counts as a probe failure — streaks and circuits untouched.
            self._extend_cooling(key, retry_after)
            log.info("healer_probe_throttled", provider=dep.provider.name,
                     key=key.label, group=dep.group, retry_after=retry_after)
            return

        if verdict is ProbeVerdict.HEALTHY:
            kst.streak += 1
            dst.streak += 1
            log.info("healer_probe_ok", provider=dep.provider.name,
                     key=key.label, group=dep.group,
                     key_streak=kst.streak, dep_streak=dst.streak)
            self._maybe_restore_key(dep, key, kst)
            if dst.streak >= self._s.probes_to_restore and now < dep.cooldown_until:
                dep.mark_recovered()
                self._circuits["dep"].clear(did)
                dst.streak = 0
                self._announce(f"healer restored deployment"
                               f" '{dep.group}/{dep.model_id}' (probation)")
            return

        if verdict is not ProbeVerdict.CREDS_VALID_MODEL_BAD:
            # CREDS_REJECTED / UNREACHABLE: streaks reset.
            kst.streak = 0
        dst.streak = 0

        if verdict is ProbeVerdict.CREDS_REJECTED:
            self._circuits["key"].trip(kid)
            log.warning("healer_probe_creds_rejected",
                        provider=dep.provider.name, key=key.label,
                        detail=detail)
        elif verdict is ProbeVerdict.CREDS_VALID_MODEL_BAD:
            # Creds proven: the key's restore streak still grows…
            kst.streak += 1
            self._maybe_restore_key(dep, key, kst)
            # …but the deployment won't self-heal: escalate toward dead.
            self._circuits["dep"].trip(did)
            if self._circuits["dep"].streak(did) >= MODEL_BAD_DEAD_STREAK:
                self._circuits["dep"].mark_dead(did)
                log.error("healer_deployment_marked_dead",
                          provider=dep.provider.name, group=dep.group,
                          model=dep.model_id, detail=detail)
            else:
                log.warning("healer_probe_model_bad",
                            provider=dep.provider.name, key=key.label,
                            group=dep.group, detail=detail)
        else:  # UNREACHABLE
            self._circuits["dep"].trip(did)
            log.warning("healer_probe_fail", provider=dep.provider.name,
                        key=key.label, group=dep.group, detail=detail)

    def _maybe_restore_key(self, dep, key, kst: _TargetState) -> None:
        if kst.streak < self._s.probes_to_restore:
            return
        if key.status not in ("cooling", "invalid"):
            return  # already serving (e.g. probation): nothing to restore
        key.mark_recovered()
        self._circuits["key"].clear((dep.provider.name, key.label))
        kst.streak = 0
        self._announce(f"healer restored key '{key.label}' on provider"
                       f" '{dep.provider.name}' (probation)")

    @staticmethod
    def _extend_cooling(key, retry_after: float | None) -> None:
        """Keep/extend a cooling window per upstream retry_after (never
        shortens the remaining window; caps at 300s)."""
        now = time.monotonic()
        remaining = max(0.0, key.cooldown_until - now)
        window = max(remaining, min(max(retry_after or 30.0, 1.0), 300.0))
        key.mark_cooling(window)

    async def _probe(self, dep, key) -> tuple[ProbeVerdict, str, float | None]:
        """One 1-token completion. Returns (verdict, detail, retry_after)."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._s.probe_timeout_s, connect=5.0))
        adapter = fresh_adapter(dep.provider.provider_type)
        stream = bool(getattr(adapter, "force_stream", False))
        key_ref = ProviderKeyRef(label=key.label, secret=key.secret)
        params: dict = {"max_tokens": PROBE_MAX_TOKENS, "extra_body": {},
                        "drop_params": True,
                        "provider_type": dep.provider.provider_type}
        body = adapter.encode_request(_probe_request(stream), dep.model_id, params)
        url = build_url(adapter, dep.provider.base_url, dep.model_id,
                        dep.provider.provider_type, stream, key_ref)
        headers = {**adapter.headers(key_ref), **dep.provider.extra_headers,
                   **dep.extra_headers}
        try:
            resp = await self._client.post(url, json=body, headers=headers)
        except httpx.TransportError as e:
            return ProbeVerdict.UNREACHABLE, type(e).__name__, None
        if resp.status_code == 200:
            return ProbeVerdict.HEALTHY, "", None
        err = error_from_provider_status(resp.status_code, resp.text,
                                         dep.provider.name)
        ra = parse_retry_after(resp.headers.get("retry-after"))
        detail = f"retry_after={ra}" if ra is not None else err.message
        return probe_verdict(resp.status_code), detail, ra

    def _announce(self, message: str) -> None:
        log.info("healer_restored", message=message)
        try:
            self._log_proxy("info", message)
        except Exception:
            pass
```

- [ ] **Step 4: Run to green**

Run: `python3 -m pytest tests/test_recovery.py -q`
Expected: all PASS

- [ ] **Step 5: Run the gateway suites too (build_url extraction is shared)**

Run: `python3 -m pytest tests/test_gateway.py tests/test_gemini_adapter.py tests/test_workbuddy_adapter.py -q`
Expected: all PASS (substitute the files that exist; `ls tests | grep -E "gateway|gemini|workbuddy"` to find them)

- [ ] **Step 6: Commit**

```bash
git add wiwi/core/recovery.py wiwi/core/gateway.py tests/test_recovery.py
git commit -m "feat: HealthHealer — probe sick keys/deployments, restore via probation"
```

---

### Task 8: Lifespan wiring

**Files:**
- Modify: `wiwi/server/app.py` (`AppState.__init__`, `lifespan`, teardown)

**Interfaces:**
- Consumes: `HealthHealer` (Task 7), `state.config.healer` (Task 4).
- Produces: `AppState.healer: Any` attribute; app lifecycle owns the healer task.

Note: the `TestLifespanWiring` test already exists from Task 7 — this task makes it pass.

- [ ] **Step 1: AppState field.** In `wiwi/server/app.py`, in `AppState.__init__` next to `self.opencode_refresh: Any = None` (line ~531), add:

```python
        self.healer: Any = None
```

- [ ] **Step 2: Start in lifespan.** In `lifespan`, after the `state.opencode_refresh.start()` block (line ~789), add:

```python
    # Health healer: probes cooling/invalid keys and cooled deployments and
    # restores them early into probation. Opt-in via the ``healer:`` config
    # section; start() is a no-op when disabled.
    state.healer = HealthHealer(state.router, state.config.healer,
                                log_proxy=state.router.log_proxy)
    state.healer.start()
```

and add the top-level import next to the other `wiwi.core` imports at the top of the file:

```python
from wiwi.core.recovery import HealthHealer
```

- [ ] **Step 3: Stop in teardown.** In the `yield`-after teardown block, before `await state.cline_refresh.stop()` (line ~808), add:

```python
    if state.healer is not None:
        await state.healer.stop()
```

- [ ] **Step 4: Run the wiring test + integration suite**

Run: `python3 -m pytest tests/test_recovery.py tests/test_integration.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add wiwi/server/app.py
git commit -m "feat: wire HealthHealer into app lifespan (opt-in via healer config)"
```

---

### Task 9: Full gate + live smoke

**Files:** none (verification only)

- [ ] **Step 1: Full suite + lint**

Run: `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`
Expected: both green, zero failures, zero lint errors

- [ ] **Step 2: Live smoke with a mocked upstream.** Create a throwaway config at `/tmp/opencode/healer-smoke.yaml`:

```yaml
general_settings:
  master_key: sk-wiwi-smoke
  database_url: "sqlite+aiosqlite:////tmp/opencode/healer-smoke.db"
healer:
  enabled: true
  tick_s: 2.0
  min_probe_interval_s: 0.5
  probes_to_restore: 2
providers:
  - name: fake
    provider: openai-compatible
    base_url: "http://127.0.0.1:9377/v1"
    keys: [{label: a, key: dummy}]
model_list:
  - model_name: g
    wiwi_params: {provider: fake, model: m}
```

Start a stub upstream (leave running): `python3 -m http.server 9377` won't speak JSON — instead use this one-liner stub in a second shell:

```bash
python3 - <<'EOF'
import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.dumps({"id": "x", "object": "chat.completion", "model": "m",
                           "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", 9377), H).serve_forever()
EOF
```

Then run: `python3 -m wiwi.main --config /tmp/opencode/healer-smoke.yaml --port 9378` — wait for startup, hit the server once with an intentionally bad base URL variant? No — instead verify the healer's own log line appears: the stub returns 200, and since nothing is sick there are no probes; so flip the smoke: temporarily set `probe_backoff_base_s: 0.0` and stop the stub for the first seconds to see `healer_probe_fail` warnings, restart the stub, and observe `healer restored key 'a' on provider 'fake' (probation)` in the logs within ~3 sweeps. Kill the server afterwards. Cleanup: `rm -rf /tmp/opencode/healer-smoke.*`.

Expected observable result: structlog lines `healer_probe_fail` while the stub is down and `healer_restored` (proxy + structlog) after it returns; the gateway still serves `POST /v1/chat/completions` on port 9378 with a virtual key path erroring 401 (no keys minted) — the healer log lines are the smoke criterion, not request serving.

- [ ] **Step 3: Final commit (if smoke surfaced any fix)**

```bash
git add -A && git commit -m "fix: address findings from healer live smoke test"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** Part A primitives → Tasks 1–3; `probe_verdict` → Task 5; probation/graduated recovery → Task 6; Part B healer + verdict table + logging → Task 7; lifespan wiring → Task 8; test matrix items 1–7 → spread across Tasks 1–7; verification gate → Task 9. Spec's "probe client never shares the gateway's client" → dedicated client in `_probe`. No request rewriting anywhere.
- **Placeholder scan:** none — every step carries exact code or exact edit instructions with anchors.
- **Type consistency:** `CircuitBreaker.blocked/dead/streak/trip/clear/mark_dead` used identically in Tasks 3, 7; `HealerSettings` fields match between Task 4, 6 (`probation_weight`), and 7; `build_url(adapter, base_url, model_id, provider_type, stream, key)` consistent between Task 7 Step 2 and Step 3; target idents `(provider, label)` and `(group, provider, model)` consistent between `_collect_pairs`, `_probe_pair`, and the tests.
- **Deliberate deviations from the spec text (noted here):** (1) `build_url` and `parse_retry_after` live in `recovery.py` with `gateway.py` delegating — required to avoid the `recovery → gateway` import cycle; (2) `HealthHealer` creates its httpx client lazily in `_probe` so unit tests can call `_sweep()` directly without `start()`.
