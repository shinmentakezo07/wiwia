"""Round 140 — the last bare ``contextlib.suppress`` in ``record_spend``.

Commit 8ee5676 fixed the *realtime* copy of the AUDIT #179 true-up (round 135)
and left this one. Same method, two call sites, opposite handling: the sibling
block a few lines above ``if not recorded:`` does ``try/except``, logs
``spend_charge_unrecorded`` and increments ``state.spend_charge_failures`` —
while the over-cap true-up two lines below it sat under a bare
``with contextlib.suppress(Exception):``. Both halves of AUDIT #179's sentence
("a failed charge is reported as a success, so a budget cap silently stops
being enforced … ``apply_spend_trueup`` on the same path is wrapped in
``contextlib.suppress``, so the true-up is lost too") applied to exactly one of
the two paths.

Failure scenario for #1: a key near its cap → ``update_spend``'s conditional
UPDATE refuses (returns ``False``) → the true-up raises (``database is locked``,
a Postgres failover, a connection reset) → the charge is silently lost,
``spend_to_date`` stays *below* ``max_budget``, ``over_budget`` never trips, and
the key is admitted indefinitely — with no log line and
``spend_charge_failures`` stuck at 0. The money was really spent; only the
record is missing. The ``return recorded`` semantics are deliberately
unchanged (this is lost telemetry, not a behaviour change): the caller still
refuses *this* response with 402.

Failure scenario for #2: the admission reservation release raised, leaving a
stranded reservation. ``budget_reserved`` is an *additive* term in
``over_budget`` (``wiwi/auth/service.py``), so a stranded one permanently
inflates the apparent spend of that key and shrinks its headroom for the life
of the process — silently, with nothing on any operator surface. The fix counts
and logs it, and critically does **not** zero ``ctx.budget_reserved`` on
failure: the reservation really is still held in the database, so clearing the
flag would make it invisible to every later release path and unrecoverable.

Tests drive the real HTTP surface (``create_app`` through ``ASGITransport`` +
``LifespanManager``) and assert what an operator observes — a log event, a
``/health`` counter, the persisted reservation — never which branch ran.
"""

from __future__ import annotations

import httpx
import pytest
import respx
import structlog
from asgi_lifespan import LifespanManager

import wiwi.server.app as app_mod
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}

OPENAI_URL = "https://api.example.com/v1/chat/completions"

# 1000 + 1000 tokens at $1/1M each == $0.002 per completion.
CHAT_BODY = {
    "id": "chatcmpl-1", "object": "chat.completion", "created": 1,
    "model": "gpt-4o",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
}


def _config(db_path: str) -> WiwiConfig:
    """One provider, one deployment, one model we can price."""
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://api.example.com/v1",
                               keys=[KeyDef(label="k1", key="sk-x")])],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=f"sqlite+aiosqlite:///{db_path}"),
        router_settings=RouterSettings(),
    )


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test")


def _ok_upstream():
    respx.post(OPENAI_URL).respond(200, json=CHAT_BODY)


def _body() -> dict:
    return {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


async def _vkey(c, max_budget: float) -> str:
    """Price gpt-4o at $0.002/completion, then mint a key with *max_budget*."""
    r = await c.put("/admin/pricing/gpt-4o", headers=AUTH,
                    json={"input_per_1m": 1.0, "output_per_1m": 1.0})
    assert r.status_code == 200, r.text
    r = await c.post("/admin/keys/generate", headers=AUTH,
                     json={"name": "spender", "max_budget": max_budget})
    assert r.status_code in (200, 201), r.text
    return r.json()["key"]


def _events(logs, name: str) -> list[dict]:
    return [e for e in logs if e.get("event") == name]


# ---------------------------------------------------------------------------
# #1 — a lost over-cap true-up must be counted and logged
# ---------------------------------------------------------------------------

@respx.mock
async def test_over_cap_trueup_failure_is_counted_and_logged(tmp_path):
    """The over-cap true-up failing is a lost charge, not a silent no-op.

    ``max_budget`` is half of one completion, so ``update_spend``'s conditional
    UPDATE refuses and ``record_spend`` takes the ``not recorded`` path. When
    the true-up then raises — the *second* half of AUDIT #179, previously
    wrapped in a bare ``contextlib.suppress`` — the charge is gone: the money
    was really spent, ``spend_to_date`` stays below the cap, and the key is
    admitted forever. Nothing observed that.

    The observable contract: ``spend_charge_failures`` moves, a
    ``spend_charge_unrecorded`` log names the key and cost, and the response is
    still refused — the caller must not be told the cap crossing was fine.
    """
    _ok_upstream()
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            sk = await _vkey(c, 0.001)  # one completion costs $0.002

            async def _trueup_boom(_key_id, _cost):
                raise RuntimeError("database is locked")

            # Only the true-up fails: update_spend still refuses cleanly, so
            # this is the ``not recorded`` branch and not the raise branch.
            state.auth.apply_spend_trueup = _trueup_boom  # type: ignore[assignment]

            with structlog.testing.capture_logs() as logs:
                r = await c.post("/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {sk}"},
                                 json=_body())
            health = (await c.get("/health")).json()

    assert r.status_code == 402, (
        f"a refused charge was reported as success: {r.status_code} {r.text}")
    assert health["spend_charge_failures"] == 1, (
        "the lost true-up is invisible to /health — the cap is still enforced "
        f"only because this one request was refused: {health}")

    unrecorded = _events(logs, "spend_charge_unrecorded")
    assert len(unrecorded) == 1, f"the lost true-up was not logged: {logs}"
    assert unrecorded[0]["cost"] > 0, unrecorded[0]
    assert unrecorded[0]["key_id"], unrecorded[0]
    assert unrecorded[0]["error"] == "RuntimeError", unrecorded[0]
    assert "locked" in unrecorded[0]["detail"], unrecorded[0]


@respx.mock
async def test_over_cap_trueup_success_is_unchanged(tmp_path):
    """Control: a *working* over-cap true-up still counts nothing.

    The refusal is a real thing that happens on every crossing; only a true-up
    that actually failed is a defect. This guards against the fix
    overcorrecting into "every cap crossing is an error" (which would spam
    ``/health`` on perfectly normal budget enforcement) — and pins the
    accounting: the charge is recorded even though it was refused.
    """
    _ok_upstream()
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            sk = await _vkey(c, 0.001)

            with structlog.testing.capture_logs() as logs:
                r = await c.post("/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {sk}"},
                                 json=_body())
            health = (await c.get("/health")).json()
            info = await state.auth.authenticate(sk)

    assert r.status_code == 402, r.text
    assert health["spend_charge_failures"] == 0, (
        f"a normal budget crossing was reported as a failure: {health}")
    assert _events(logs, "spend_charge_unrecorded") == [], (
        f"a normal budget crossing logged a lost charge: {logs}")
    assert info.spend_to_date == pytest.approx(0.002), (
        "the true-up must still record a refused charge — the upstream billed it")


@respx.mock
async def test_healthy_charge_counts_nothing(tmp_path):
    """Control: the ordinary under-cap path is untouched by either fix."""
    _ok_upstream()
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app), _client(app) as c:
        sk = await _vkey(c, 1000.0)
        with structlog.testing.capture_logs() as logs:
            r = await c.post("/v1/chat/completions",
                             headers={"Authorization": f"Bearer {sk}"},
                             json=_body())
        health = (await c.get("/health")).json()

    assert r.status_code == 200, r.text
    assert health["spend_charge_failures"] == 0, (
        f"a clean charge incremented the loss counter: {health}")
    assert _events(logs, "spend_charge_failed") == [], logs
    assert _events(logs, "spend_charge_unrecorded") == [], logs
    assert _events(logs, "spend_reservation_release_failed") == [], logs


# ---------------------------------------------------------------------------
# #2 — a stranded reservation release must be counted, logged, and recoverable
# ---------------------------------------------------------------------------

@respx.mock
async def test_reservation_release_failure_is_counted_logged_and_recoverable(
        tmp_path):
    """A failed release strands ``budget_reserved``, an additive term in
    ``over_budget`` — it silently shrinks the key's headroom forever.

    Three things must hold:

    1. the failure is counted and logged (``spend_reservation_release_failed``),
       because the release is the only thing standing between a reservation and
       a permanently reduced cap;
    2. the request is still served and still charged — the release failing is
       an accounting problem, not a reason to 500 a turn the upstream billed;
    3. ``ctx.budget_reserved`` is **not** zeroed. The reservation really is
       still held in the database; clearing the flag would make it invisible to
       every later release path (``_release_tpm_reservation``, the stream
       teardown) and unrecoverable short of a manual DB edit.
    """
    _ok_upstream()
    app = create_app(_config(tmp_path / "s.db"))

    # Grab the live RequestContext so the post-settle state of
    # ``ctx.budget_reserved`` is observable from outside.
    seen: list = []
    real_build = app_mod.build_log_event

    def _spy(ctx):
        seen.append(ctx)
        return real_build(ctx)

    app_mod.build_log_event = _spy
    try:
        async with LifespanManager(app):
            state = app.state.wiwi
            async with _client(app) as c:
                sk = await _vkey(c, 1000.0)
                hdr = {"Authorization": f"Bearer {sk}"}

                real_release = state.auth.release_budget_reservation

                async def _release_boom(_key_id, _amount):
                    raise RuntimeError("pool exhausted")

                state.auth.release_budget_reservation = _release_boom  # type: ignore[assignment]

                with structlog.testing.capture_logs() as logs:
                    r = await c.post("/v1/chat/completions", headers=hdr,
                                     json=_body())
                health = (await c.get("/health")).json()

                info = await state.auth.authenticate(sk)
                stranded = info.budget_reserved
                ctx = next((x for x in reversed(seen) if x.auth is not None), None)

                # The reservation is genuinely still held — which is exactly why
                # the flag must not have been cleared, and why a later retry can
                # free it.
                state.auth.release_budget_reservation = real_release  # type: ignore[assignment]
                await state.auth.release_budget_reservation(info.key_id, stranded)
                recovered = await state.auth.authenticate(sk)
    finally:
        app_mod.build_log_event = real_build

    assert r.status_code == 200, (
        f"a failed release broke a served turn: {r.status_code} {r.text}")
    assert health["spend_charge_failures"] == 1, (
        f"a stranded reservation is invisible to /health: {health}")

    failed = _events(logs, "spend_reservation_release_failed")
    assert len(failed) == 1, f"the failed release was not logged: {logs}"
    assert failed[0]["reserved"] > 0, failed[0]
    assert failed[0]["key_id"] == info.key_id, failed[0]
    assert failed[0]["error"] == "RuntimeError", failed[0]

    assert stranded > 0, (
        "the reservation should still be held after a failed release, so the "
        f"key's headroom is stranded (got {stranded})")
    assert ctx is not None, "the RequestContext was never captured"
    assert ctx.budget_reserved == pytest.approx(stranded), (
        "ctx.budget_reserved was cleared even though the release failed — the "
        "stranded reservation is now invisible to every later release path "
        f"(ctx={ctx.budget_reserved}, db={stranded})")
    assert recovered.budget_reserved == 0.0, (
        "a retry of the release must still be able to free the headroom")
    assert recovered.spend_to_date == pytest.approx(0.002), (
        "the charge must still be recorded when only the release failed")