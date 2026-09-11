"""Shared recovery primitives: backoff, circuit breakers.

Used by the router retry loop, the Cline/WorkBuddy auto-refresh services, and
the HealthHealer background service. Contracts only — no dialect or provider
branching (invariant: those live in wiwi/wire/ and wiwi/providers/). This
module must never import wiwi.router or wiwi.core.gateway: they import from
here, so the direction recovery -> router would create a cycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
import structlog

from wiwi.config import HealerSettings
from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef, error_from_provider_status
from wiwi.providers.registry import fresh_adapter

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


class ProbeVerdict(Enum):
    """Classification of a HealthHealer probe outcome (see specs/
    2026-09-08-recovery-healer-design.md, Part B verdict table)."""

    HEALTHY = "healthy"
    ALIVE_THROTTLED = "alive_throttled"
    CREDS_VALID_MODEL_BAD = "creds_valid_model_bad"
    CREDS_REJECTED = "creds_rejected"
    UNREACHABLE = "unreachable"


def probe_verdict(status: int | None, body: bytes | str | None = None) -> ProbeVerdict:
    """Classify a probe HTTP outcome; ``status=None`` means transport failure.

    A 200 is only HEALTHY when its body is a real completion. Some providers
    (WorkBuddy) ride business errors on HTTP 200 as a ``{"code": N, "msg": …}``
    envelope — a dead session would otherwise be declared healthy and restored
    into rotation (AUDIT #96).
    """
    if status == 200:
        if _body_is_error_envelope(body):
            return ProbeVerdict.UNREACHABLE
        return ProbeVerdict.HEALTHY
    if status == 429:
        return ProbeVerdict.ALIVE_THROTTLED
    if status in (401, 403):
        return ProbeVerdict.CREDS_REJECTED
    if status in (400, 404):
        return ProbeVerdict.CREDS_VALID_MODEL_BAD
    return ProbeVerdict.UNREACHABLE


def _body_is_error_envelope(body: bytes | str | None) -> bool:
    """True when a 200 probe body is a business-error envelope, not content.

    Recognizes the ``{"code": <non-zero>, "msg": …}`` shape some providers
    (WorkBuddy) use for dead sessions / exhausted credit on an HTTP 200.
    """
    if body is None:
        return False
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    code = data.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        return False
    return code != 0


def build_url(adapter, base_url: str, model_id: str, provider_type: str,
              stream: bool, key) -> str:
    """Resolve the upstream URL for a call, appending the credential for
    providers that need it in the querystring (Gemini) and honoring adapter-
    declared ``build_url_for_key`` (WorkBuddy CN vs global domains). Extracted
    verbatim from gateway._build_url so the healer and the gateway share one
    implementation; ``key`` is a ProviderKeyRef."""
    build = getattr(adapter, "build_url_for_key", None)
    if build is not None:
        return build(base_url, model_id, stream, key)
    url = adapter.build_url(base_url, model_id, stream)
    if provider_type == "gemini" and url.endswith(("?key=", "&key=")):
        url += key.secret
    return url


def parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP ``Retry-After`` header: delta-seconds or an HTTP-date
    (RFC 7231). Returns seconds from now (>= 0), or None when absent/garbage."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    # RFC 7231 also allows an HTTP-date (e.g. "Wed, 21 Oct 2026 07:28:00 GMT").
    # Parse it and compute seconds from now; clamp to >= 0.
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        pass
    return None


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

    def __init__(self, router: Any, settings: HealerSettings,
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
        """One probe pass over filtered candidates. Returns probes issued.

        Multiple sick keys of one provider each get their own probe against
        the same deployment, but only the first probe of a deployment in a
        sweep credits the deployment's restore streak (``credit_dep``), so a
        deployment heals over ``probes_to_restore`` sweeps, not probes.
        """
        batch = self._collect_pairs()[: self._s.max_probes_per_sweep]
        if not batch:
            return 0
        sem = asyncio.Semaphore(_PROBE_CONCURRENCY)
        credited: set[tuple[str, str, str]] = set()

        async def run_one(dep, key):
            did = (dep.group, dep.provider.name, dep.model_id)
            credit_dep = did not in credited
            credited.add(did)
            async with sem:
                await self._probe_pair(dep, key, credit_dep=credit_dep)

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
                if not k.enabled:
                    continue  # admin-disabled: never probed, never restored
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

    async def _probe_pair(self, dep, key, credit_dep: bool = True) -> None:
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
            if credit_dep:
                dst.streak += 1
            log.info("healer_probe_ok", provider=dep.provider.name,
                     key=key.label, group=dep.group,
                     key_streak=kst.streak, dep_streak=dst.streak)
            self._maybe_restore_key(dep, key, kst)
            if (dst.streak >= self._s.probes_to_restore
                    and now < dep.cooldown_until):
                dep.mark_recovered()
                self._circuits["dep"].clear(did)
                dst.streak = 0
                self._announce(f"healer restored deployment"
                               f" '{dep.group}/{dep.model_id}' (probation)")
            return

        if verdict is not ProbeVerdict.CREDS_VALID_MODEL_BAD:
            # CREDS_REJECTED / UNREACHABLE: streaks reset.
            kst.streak = 0
        if credit_dep:
            dst.streak = 0

        if verdict is ProbeVerdict.CREDS_REJECTED:
            self._circuits["key"].trip(kid)
            log.warning("healer_probe_creds_rejected",
                        provider=dep.provider.name, key=key.label,
                        detail=detail)
        elif verdict is ProbeVerdict.CREDS_VALID_MODEL_BAD:
            # Creds were *not rejected*, but the probe never completed a
            # generation, so it proves nothing about whether the key can serve
            # traffic. Growing the key's restore streak here restored keys that
            # were never exercised (AUDIT #97); reset it instead. The
            # deployment still escalates toward dead.
            kst.streak = 0
            if credit_dep:
                self._circuits["dep"].trip(did)
                if self._circuits["dep"].streak(did) >= MODEL_BAD_DEAD_STREAK:
                    self._circuits["dep"].mark_dead(did)
                    log.error("healer_deployment_marked_dead",
                              provider=dep.provider.name, group=dep.group,
                              model=dep.model_id, detail=detail)
                    return
                log.warning("healer_probe_model_bad",
                            provider=dep.provider.name, key=key.label,
                            group=dep.group, detail=detail)
        else:  # UNREACHABLE
            if credit_dep:
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
        params: dict[str, Any] = {"max_tokens": PROBE_MAX_TOKENS,
                                  "extra_body": {}, "drop_params": True,
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
            verdict = probe_verdict(resp.status_code, resp.content)
            if verdict is ProbeVerdict.HEALTHY:
                return ProbeVerdict.HEALTHY, "", None
            # A 200 whose body is a business-error envelope (dead session):
            # surface it as unreachable rather than healthy (AUDIT #96).
            return verdict, "error envelope in 200 body", None
        err = error_from_provider_status(resp.status_code, resp.text,
                                         dep.provider.name)
        ra = parse_retry_after(resp.headers.get("retry-after"))
        detail = f"retry_after={ra}" if ra is not None else err.message
        return probe_verdict(resp.status_code, resp.content), detail, ra

    def _announce(self, message: str) -> None:
        log.info("healer_restored", message=message)
        try:
            self._log_proxy("info", message)
        except Exception as e:  # noqa: BLE001 — announce is best-effort
            log.debug("healer_announce_failed", err=str(e))
