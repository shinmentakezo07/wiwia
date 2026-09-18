"""Round-84 regression tests for AUDIT #169 and #170.

#169 — Cline's on-demand 401 refresh built its OWN ``ClineAutoRefresh`` worker,
so its ``_circuit`` was a different object from the sweeper's and there was no
lock at all. Cline refresh tokens are single-use: a sweeper tick and a client
401 landing in the same lead window both read the same ``refresh_token`` and
both POSTed it, and the loser's ``invalid_grant`` mapped to
``mark_dead`` — the provider stayed dead until a human re-authenticated.

#170 — WorkBuddy's sweeper used ``expires_within_lead`` as its only due-check,
and that returned True for ``expires_at <= 0`` (a missing/non-numeric
``expiresAt``). A stored record with no expiry was therefore "always due": one
refresh POST plus one DB write per ``TICK_S``, forever, each consuming a
rotating refresh token — and any 401/403 on the way marked a never-expired key
dead.

The interleavings below are driven with a signalling lock, not with sleeps: the
test observes that the second caller is *parked on the shared lock* before it
asserts the upstream POST count, so the result does not depend on timing luck.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import respx
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.providers.cline_auto_refresh import (
    ClineAutoRefresh,
)
from wiwi.providers.cline_auto_refresh import (
    refresh_for_provider as cline_hook_for,
)
from wiwi.providers.cline_oauth import CLINE_API_BASE
from wiwi.providers.workbuddy_auth import (
    REFRESH_LEAD_S,
    WorkBuddyAuth,
    parse_auth,
)
from wiwi.providers.workbuddy_auto_refresh import (
    WorkBuddyAutoRefresh,
    refresh_key_now,
)
from wiwi.providers.workbuddy_auto_refresh import (
    refresh_for_provider as workbuddy_hook_for,
)
from wiwi.router.router import Router
from wiwi.server.config_store import ConfigStore

MASTER = "sk-wiwi-master-test"
EXPIRY_SOON = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
EXPIRY_FUTURE = "2099-01-01T00:00:00Z"
CLINE_REFRESH = f"{CLINE_API_BASE}/auth/refresh"
WB_REFRESH_CN = "https://copilot.tencent.com/v2/plugin/auth/token/refresh"


@dataclass
class _State:
    """The subset of AppState the refresh workers touch."""

    router: Router
    config_store: ConfigStore
    cline_refresh: Any = None
    workbuddy_refresh: Any = None


class _SignallingLock(asyncio.Lock):
    """An ``asyncio.Lock`` that fires ``arrived`` when a caller reaches it.

    Used to prove the second caller *queues* behind the shared lock rather
    than racing into a second upstream POST: the test waits for ``arrived``
    (i.e. the caller has called ``acquire`` and is parked, because the first
    caller still holds the lock) and only then inspects the POST count.
    """

    def __init__(self, arrived: asyncio.Event) -> None:
        super().__init__()
        self.arrived = arrived

    async def acquire(self) -> bool:
        self.arrived.set()
        return await super().acquire()


async def _wait_for(event: asyncio.Event, timeout: float = 2.0) -> bool:
    """True when ``event`` fired within ``timeout``; never raises."""
    try:
        await asyncio.wait_for(event.wait(), timeout)
    except TimeoutError:
        return False
    return True


async def _first_to_fire(*events: asyncio.Event, timeout: float = 5.0) -> int:
    """Index of the first event to fire, or -1 if none did within ``timeout``.

    Lets a test observe *which* of two mutually exclusive things happened —
    the second caller queued on the shared lock, or it made a second upstream
    POST — without sleeping and hoping.
    """
    waits = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED,
                                     timeout=timeout)
    finally:
        for w in waits:
            w.cancel()
    for i, w in enumerate(waits):
        if w in done:
            return i
    return -1


def _cline_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="cline-prov", provider="cline",
                               base_url="https://api.cline.bot/api/v1",
                               keys=[KeyDef(label="default",
                                            key="workos:stale-access")])],
        model_list=[ModelEntry(
            model_name="cline-model",
            wiwi_params=DeploymentParams(provider="cline-prov",
                                         model="z-ai/glm-5.2"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url="sqlite+aiosqlite:///:memory:"),
    )


def _workbuddy_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="wb", provider="workbuddy",
                               keys=[KeyDef(label="k1", key="{}")])],
        model_list=[ModelEntry(
            model_name="wb-model",
            wiwi_params=DeploymentParams(provider="wb", model="x"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url="sqlite+aiosqlite:///:memory:"),
    )


async def _store() -> ConfigStore:
    cs = ConfigStore(create_async_engine("sqlite+aiosqlite:///:memory:"))
    await cs.startup()
    return cs


def _wb_secret(**auth: Any) -> str:
    """A stored WorkBuddy key secret in the plugin's nested shape."""
    return json.dumps({"auth": auth,
                       "account": {"uid": "u1", "nickname": "acct"}})


# -- #169: one lock, one circuit, one presented refresh token ----------------


@respx.mock
async def test_sweeper_and_on_demand_hook_present_the_token_once():
    """A sweeper tick and a 401 landing together must not both POST.

    Pre-fix the hook owned a private worker (private lock, private circuit),
    so it POSTed the same single-use token the sweeper had in flight.
    """
    cs = await _store()
    try:
        state = _State(router=Router(_cline_config()), config_store=cs)
        worker = ClineAutoRefresh(state)
        state.cline_refresh = worker  # the lifespan-owned worker (app.py)
        arrived = asyncio.Event()
        worker._locks["cline-prov"] = _SignallingLock(arrived)
        await cs.set_setting("cline_oauth:cline-prov",
                             {"refresh_token": "ref-1",
                              "expires_at": EXPIRY_SOON, "email": "u@x.io"})

        upstream_started = asyncio.Event()
        second_post = asyncio.Event()
        release = asyncio.Event()
        presented: list[str] = []

        async def refresh(request: httpx.Request) -> httpx.Response:
            presented.append(json.loads(request.content)["refreshToken"])
            if len(presented) == 1:
                upstream_started.set()
            else:
                # A second POST of a single-use token is the bug itself.
                second_post.set()
            await release.wait()
            return httpx.Response(200, json={"data": {
                "accessToken": "acc-fresh", "refreshToken": "ref-2",
                "expiresAt": EXPIRY_FUTURE}})

        route = respx.post(CLINE_REFRESH).mock(side_effect=refresh)

        record = await cs.get_setting("cline_oauth:cline-prov")
        sweep = asyncio.create_task(
            worker._check_provider("cline-prov", record))
        assert await _wait_for(upstream_started), "sweeper never refreshed"

        # Now the 401 hook arrives while the sweeper holds the wire. Exactly
        # one of these can happen: it queues on the shared lock, or it POSTs
        # the token the sweeper is already consuming.
        arrived.clear()
        hook = cline_hook_for(state)
        hook_task = asyncio.create_task(hook("cline-prov", "default"))
        assert await _first_to_fire(arrived, second_post) == 0, (
            "the on-demand 401 hook burned the sweeper's in-flight refresh "
            f"token instead of sharing its lock (POSTs={presented})")

        # Parked on the shared lock: the token has been presented exactly once.
        # (respx bumps ``route.call_count`` only after the handler returns, so
        # the in-flight assertion uses our own POST log.)
        assert presented == ["ref-1"]
        assert not hook_task.done()

        release.set()
        await asyncio.wait_for(sweep, 5)
        # The hook reports "fresh, retry" without a second POST: it observes
        # the sweeper's rotation instead of burning the consumed token.
        assert await asyncio.wait_for(hook_task, 5) is True
        assert route.call_count == 1
        assert presented == ["ref-1"]

        record = await cs.get_setting("cline_oauth:cline-prov")
        assert record["refresh_token"] == "ref-2"
        assert state.router.providers["cline-prov"].keys[0].secret == "acc-fresh"
    finally:
        await cs.engine.dispose()


@respx.mock
async def test_concurrent_on_demand_hooks_present_the_token_once():
    """N concurrent 401s serialize on the shared lock instead of going N-way."""
    cs = await _store()
    try:
        state = _State(router=Router(_cline_config()), config_store=cs)
        worker = ClineAutoRefresh(state)
        state.cline_refresh = worker
        arrived = asyncio.Event()
        worker._locks["cline-prov"] = _SignallingLock(arrived)
        await cs.set_setting("cline_oauth:cline-prov",
                             {"refresh_token": "ref-1",
                              "expires_at": EXPIRY_FUTURE, "email": "u@x.io"})

        upstream_started = asyncio.Event()
        second_post = asyncio.Event()
        release = asyncio.Event()
        presented: list[str] = []

        async def refresh(request: httpx.Request) -> httpx.Response:
            presented.append(json.loads(request.content)["refreshToken"])
            if len(presented) == 1:
                upstream_started.set()
            else:
                second_post.set()
            await release.wait()
            return httpx.Response(200, json={"data": {
                "accessToken": "acc-fresh", "refreshToken": "ref-2",
                "expiresAt": EXPIRY_FUTURE}})

        route = respx.post(CLINE_REFRESH).mock(side_effect=refresh)
        hook = cline_hook_for(state)

        first = asyncio.create_task(hook("cline-prov", "default"))
        assert await _wait_for(upstream_started), "first 401 never refreshed"

        arrived.clear()
        second = asyncio.create_task(hook("cline-prov", "default"))
        assert await _first_to_fire(arrived, second_post) == 0, (
            "the second 401 went N-way instead of queueing on the shared lock "
            f"(POSTs={presented})")
        assert presented == ["ref-1"]
        assert not second.done()

        release.set()
        assert await asyncio.wait_for(first, 5) is True
        assert await asyncio.wait_for(second, 5) is True
        assert route.call_count == 1
        assert presented == ["ref-1"]
    finally:
        await cs.engine.dispose()


@respx.mock
async def test_on_demand_hook_rotates_when_expiry_is_far_off():
    """A 401 outranks the expiry clock: the hook rotates regardless of it.

    The sweeper only acts inside the lead window; the 401 path exists for
    tokens the upstream rejected early, so it must not become expiry-gated.
    This is a guard against over-fixing #169 (green pre-fix by design — the
    bug was the missing lock, not a missing rotation).
    """
    cs = await _store()
    try:
        state = _State(router=Router(_cline_config()), config_store=cs)
        state.cline_refresh = ClineAutoRefresh(state)
        await cs.set_setting("cline_oauth:cline-prov",
                             {"refresh_token": "ref-1",
                              "expires_at": EXPIRY_FUTURE, "email": "u@x.io"})
        route = respx.post(CLINE_REFRESH).respond(
            json={"data": {"accessToken": "acc-fresh", "refreshToken": "ref-2",
                           "expiresAt": EXPIRY_FUTURE}})

        hook = cline_hook_for(state)
        assert await hook("cline-prov", "default") is True
        assert route.call_count == 1
        assert state.router.providers["cline-prov"].keys[0].secret == "acc-fresh"
    finally:
        await cs.engine.dispose()


@respx.mock
async def test_on_demand_hook_honors_a_dead_circuit():
    """A permanently dead provider is not retried by the 401 hook either."""
    cs = await _store()
    try:
        state = _State(router=Router(_cline_config()), config_store=cs)
        worker = ClineAutoRefresh(state)
        state.cline_refresh = worker
        worker._circuit.mark_dead("cline-prov")
        await cs.set_setting("cline_oauth:cline-prov",
                             {"refresh_token": "ref-1",
                              "expires_at": EXPIRY_SOON, "email": "u@x.io"})
        route = respx.post(CLINE_REFRESH)

        hook = cline_hook_for(state)
        assert await hook("cline-prov", "default") is False
        assert not route.called
    finally:
        await cs.engine.dispose()


# -- #170: an unknown expiry is not "refresh now" -----------------------------


@respx.mock
async def test_sweeper_skips_unknown_expiry_and_refreshes_a_real_one():
    """Unknown expiry (absent or non-numeric) is not due; a real expiry is."""
    cs = await _store()
    try:
        state = _State(router=Router(_workbuddy_config()), config_store=cs)
        worker = WorkBuddyAutoRefresh(state)
        key = state.router.providers["wb"].keys[0]
        route = respx.post(WB_REFRESH_CN).respond(
            json={"code": 0, "msg": "ok", "data": {
                "accessToken": "at-2", "refreshToken": "rt-2", "expiresIn": 7200}})

        # No expiresAt at all.
        key.secret = _wb_secret(accessToken="at", refreshToken="rt")
        await worker._sweep()
        assert route.call_count == 0, "a key with no expiry must not be refreshed"

        # expiresAt present but not a number (parse_auth coerces it to 0).
        key.secret = _wb_secret(accessToken="at", refreshToken="rt",
                                expiresAt="not-a-timestamp")
        await worker._sweep()
        assert route.call_count == 0, "an unparseable expiry must not be refreshed"

        # A genuinely expiring key still rotates — the fix must not be a
        # blanket "never refresh".
        key.secret = _wb_secret(accessToken="at", refreshToken="rt",
                                expiresAt=int(time.time() + 60))
        await worker._sweep()
        assert route.call_count == 1
        assert parse_auth(key.secret).refresh_token == "rt-2"
    finally:
        await cs.engine.dispose()


@respx.mock
async def test_sweeper_never_marks_a_never_expired_key_dead():
    """A failing refresh on an unknown-expiry key must not kill it.

    Pre-fix the sweep POSTed such a key every tick, so a 401/403 or a
    "session dead" body marked dead a key that was never actually expired.
    """
    cs = await _store()
    try:
        state = _State(router=Router(_workbuddy_config()), config_store=cs)
        worker = WorkBuddyAutoRefresh(state)
        key = state.router.providers["wb"].keys[0]
        key.secret = _wb_secret(accessToken="at", refreshToken="rt")
        route = respx.post(WB_REFRESH_CN).respond(
            401, text='{"code":12153,"msg":"offline user session"}')

        await worker._sweep()
        assert route.call_count == 0
        assert not worker._circuit.dead(("wb", "k1"))
        assert not worker._circuit.blocked(("wb", "k1"))
    finally:
        await cs.engine.dispose()


def test_admin_listing_still_flags_an_unknown_expiry():
    """The operator-facing convention is unchanged: unknown = flagged.

    ``expires_within_lead`` (the sweeper's due-check) and ``needs_refresh``
    (the admin listing) deliberately disagree on an unknown expiry — one must
    never rotate on a guess, the other should surface it for attention.
    Green pre-fix by design: this pins the half of the behaviour the fix
    must NOT change.
    """
    unknown = parse_auth(_wb_secret(accessToken="at", refreshToken="rt"))
    assert unknown.expires_at == 0
    assert unknown.needs_refresh(REFRESH_LEAD_S) is True
    far = WorkBuddyAuth(access_token="at", refresh_token="rt",
                        expires_at=int(time.time() + 3600))
    assert far.needs_refresh(REFRESH_LEAD_S) is False


# -- #170's sibling: WorkBuddy's 401 hook shares the same lock ----------------


@respx.mock
async def test_admin_refresh_overrides_a_tripped_circuit():
    """The manual admin refresh stays reachable when the circuit is dead.

    The automatic paths (sweeper, 401 hook) respect the breaker; the admin
    endpoint is the documented way for an operator to force a rotation out of
    a stuck provider, so it must not be gated by it — while still sharing the
    lock.
    """
    cs = await _store()
    try:
        state = _State(router=Router(_workbuddy_config()), config_store=cs)
        worker = WorkBuddyAutoRefresh(state)
        state.workbuddy_refresh = worker
        worker._circuit.mark_dead(("wb", "k1"))
        key = state.router.providers["wb"].keys[0]
        key.secret = _wb_secret(accessToken="at", refreshToken="rt",
                                expiresAt=int(time.time() + 3600))
        route = respx.post(WB_REFRESH_CN).respond(
            json={"code": 0, "msg": "ok", "data": {
                "accessToken": "at-2", "refreshToken": "rt-2", "expiresIn": 7200}})

        result = await refresh_key_now(state, "wb", "k1")
        assert result == {"ok": True, "error": ""}
        assert route.call_count == 1
        assert not worker._circuit.blocked(("wb", "k1"))
        assert parse_auth(key.secret).refresh_token == "rt-2"
    finally:
        await cs.engine.dispose()


@respx.mock
async def test_admin_refresh_reports_why_it_did_not_rotate():
    """A refusal still names its reason (the admin UI surfaces it verbatim)."""
    cs = await _store()
    try:
        state = _State(router=Router(_workbuddy_config()), config_store=cs)
        state.workbuddy_refresh = WorkBuddyAutoRefresh(state)
        route = respx.post(WB_REFRESH_CN)

        unknown = await refresh_key_now(state, "wb", "ghost")
        assert unknown["ok"] is False
        assert "ghost" in unknown["error"]

        key = state.router.providers["wb"].keys[0]
        key.secret = _wb_secret(accessToken="at")  # no refreshToken
        no_token = await refresh_key_now(state, "wb", "k1")
        assert no_token["ok"] is False
        assert "refreshToken" in no_token["error"]
        assert not route.called
    finally:
        await cs.engine.dispose()


@respx.mock
async def test_workbuddy_sweeper_and_hook_share_one_lock():
    """WorkBuddy's 401 hook must queue behind the sweeper, not rotate twice."""
    cs = await _store()
    try:
        state = _State(router=Router(_workbuddy_config()), config_store=cs)
        worker = WorkBuddyAutoRefresh(state)
        state.workbuddy_refresh = worker
        arrived = asyncio.Event()
        worker._locks[("wb", "k1")] = _SignallingLock(arrived)
        key = state.router.providers["wb"].keys[0]
        key.secret = _wb_secret(accessToken="at", refreshToken="rt",
                                expiresAt=int(time.time() + 60))

        upstream_started = asyncio.Event()
        second_post = asyncio.Event()
        release = asyncio.Event()
        presented: list[str] = []

        async def refresh(request: httpx.Request) -> httpx.Response:
            presented.append(request.headers["X-Refresh-Token"])
            if len(presented) == 1:
                upstream_started.set()
            else:
                second_post.set()
            await release.wait()
            return httpx.Response(200, json={"code": 0, "msg": "ok", "data": {
                "accessToken": "at-2", "refreshToken": "rt-2",
                "expiresIn": 7200}})

        route = respx.post(WB_REFRESH_CN).mock(side_effect=refresh)

        sweep = asyncio.create_task(worker._check_key("wb", "k1"))
        assert await _wait_for(upstream_started), "sweeper never refreshed"

        arrived.clear()
        hook = workbuddy_hook_for(state)
        hook_task = asyncio.create_task(hook("wb", "k1"))
        assert await _first_to_fire(arrived, second_post) == 0, (
            "the WorkBuddy 401 hook burned the sweeper's in-flight refresh "
            f"token instead of sharing its lock (POSTs={presented})")
        assert presented == ["rt"]
        assert not hook_task.done()

        release.set()
        await asyncio.wait_for(sweep, 5)
        assert await asyncio.wait_for(hook_task, 5) is True
        assert route.call_count == 1
        assert presented == ["rt"]
        assert parse_auth(key.secret).refresh_token == "rt-2"
    finally:
        await cs.engine.dispose()
