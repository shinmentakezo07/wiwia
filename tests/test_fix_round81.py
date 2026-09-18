"""Round 81 — the server-layer findings from the round-68/75 sweeps.

Every test drives the real HTTP surface (``create_app`` through
``ASGITransport`` + ``LifespanManager``) and asserts what a caller or an
operator observes, never which branch ran:

* **#179** a failed ``update_spend`` reported the charge as a success, so a
  hard budget cap silently stopped being enforced with no counter, no
  ``/health`` field and no metric. Now the failure is loud (``error`` log with
  key id + cost, ``spend_charge_failures`` in ``/health``) and a charge that
  cannot be recorded at all refuses the response.
* **#181** admin provider delete/rename mutated in-memory routing *before* the
  DB write, so a failed write answered 500 while having already taken effect
  in memory — the provider (and its plaintext key) came back after a restart,
  and no audit row was written. Both paths now persist first, matching
  ``POST /admin/providers`` and ``DELETE /admin/keys/...``.
* **#183** ``/health`` returned the constant ``status: "ok"``, so a gateway
  with zero providers — which cannot serve a single request, and which the
  Docker ``HEALTHCHECK`` probes — stayed "healthy" forever. ``status`` is now
  derived from the router; the HTTP status stays 200 so liveness and readiness
  stay separable.
* **#199** admin key ``reset_status`` cleared ``status``/``cooldown_until`` but
  left ``err_count`` at its retirement value, so ``on_result`` re-retired the
  key on the very next non-200 — the operator's "retry this key" silently
  reverted.
* **#177** the Anthropic ``ping`` keep-alive never reached the client: the
  gateway pump yields it as raw ``bytes`` and ``_stream_response`` sent every
  item through ``encoder.feed``, whose isinstance checks are delta-only, so the
  frame was silently dropped and ``stream_ping_interval_s`` was a no-op.
"""

from __future__ import annotations

import asyncio

import httpx
import respx
from asgi_lifespan import LifespanManager

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

CHAT_BODY = {
    "id": "chatcmpl-1", "object": "chat.completion", "created": 1,
    "model": "gpt-4o",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
}


def _config(db_path: str, **router_overrides) -> WiwiConfig:
    """One provider, one deployment, one priced model."""
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://api.example.com/v1",
                               keys=[KeyDef(label="k1", key="sk-x")])],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=f"sqlite+aiosqlite:///{db_path}"),
        router_settings=RouterSettings(**router_overrides),
    )


def _empty_config(db_path: str) -> WiwiConfig:
    """A gateway that cannot serve a single request: no providers at all."""
    return WiwiConfig(
        providers=[], model_list=[],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=f"sqlite+aiosqlite:///{db_path}"),
    )


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test")


async def _priced_vkey(c) -> str:
    """Price gpt-4o so one completion costs $0.002, then mint a key for it."""
    r = await c.put("/admin/pricing/gpt-4o", headers=AUTH,
                    json={"input_per_1m": 1.0, "output_per_1m": 1.0})
    assert r.status_code == 200, r.text
    r = await c.post("/admin/keys/generate", headers=AUTH,
                     json={"name": "spender", "max_budget": 1000.0})
    assert r.status_code in (200, 201), r.text
    return r.json()["key"]


def _ok_upstream():
    respx.post(OPENAI_URL).respond(200, json=CHAT_BODY)


# ---------------------------------------------------------------------------
# #179 — a failed spend charge must not be reported as a success
# ---------------------------------------------------------------------------

@respx.mock
async def test_failed_spend_charge_is_logged_and_counted(tmp_path):
    """A raising ``update_spend`` must be visible even when the true-up repairs it.

    Pre-fix ``record_spend`` returned True straight out of its bare ``except``:
    the write failure was indistinguishable from a successful charge — no
    counter, no ``/health`` field, no log line — so a hard budget cap stopped
    being enforced with nothing to show for it (AUDIT #179).

    The observable contract: the operator can see the incident (an ``error``
    log naming the key and cost, and ``spend_charge_failures`` in ``/health``)
    and the charge is still recorded (``apply_spend_trueup``), so the response
    is served — the upstream already billed it.
    """
    import structlog

    _ok_upstream()
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            sk = await _priced_vkey(c)

            async def _boom(_key_id, _cost):
                raise RuntimeError("database is locked")

            state.auth.update_spend = _boom  # type: ignore[assignment]

            with structlog.testing.capture_logs() as logs:
                r = await c.post("/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {sk}"},
                                 json={"model": "gpt-4o",
                                       "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 200, r.text

            health = (await c.get("/health")).json()

    failures = [e for e in logs if e.get("event") == "spend_charge_failed"]
    assert len(failures) == 1, f"the lost charge was not logged: {logs}"
    assert failures[0]["cost"] > 0, failures[0]
    assert failures[0]["key_id"], failures[0]
    assert health["spend_charge_failures"] == 1, (
        f"the charge failure is invisible to /health: {health}")


@respx.mock
async def test_unrecordable_spend_charge_refuses_the_response(tmp_path):
    """When the true-up fails too, the charge is gone — report it, do not claim success.

    This is the fail-open the finding names: the write path is down and the
    unconditional retry could not repair it, so ``spend_to_date`` never moves
    and the cap will not stop the next request. Pre-fix this returned True and
    the caller saw a 200 (AUDIT #179).
    """
    import structlog

    _ok_upstream()
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            sk = await _priced_vkey(c)

            async def _boom(*_a, **_kw):
                raise RuntimeError("pool exhausted")

            state.auth.update_spend = _boom  # type: ignore[assignment]
            state.auth.apply_spend_trueup = _boom  # type: ignore[assignment]

            with structlog.testing.capture_logs() as logs:
                r = await c.post("/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {sk}"},
                                 json={"model": "gpt-4o",
                                       "messages": [{"role": "user", "content": "hi"}]})
            health = (await c.get("/health")).json()

    assert r.status_code == 402, (
        f"an unrecorded charge was reported as success: {r.status_code} {r.text}")
    assert health["spend_charge_failures"] == 1, health
    assert any(e.get("event") == "spend_charge_unrecorded" for e in logs), logs


@respx.mock
async def test_successful_charge_still_succeeds_and_counts_nothing(tmp_path):
    """Control: a working write path is unchanged and the counter stays 0.

    Guards against the fix overcorrecting into "any accounting hiccup refuses
    a response the upstream already served" (AUDIT #24).
    """
    _ok_upstream()
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app), _client(app) as c:
        sk = await _priced_vkey(c)
        r = await c.post("/v1/chat/completions",
                         headers={"Authorization": f"Bearer {sk}"},
                         json={"model": "gpt-4o",
                               "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200, r.text
        health = (await c.get("/health")).json()
    assert health["spend_charge_failures"] == 0, health


@respx.mock
async def test_repaired_charge_failure_still_enforces_the_cap(tmp_path):
    """The point of the fix: a failed charge must not disable the budget cap.

    Pre-fix the exception path returned True and recorded nothing, so
    ``spend_to_date`` never moved and every later request passed the admission
    check — a hard cap became advisory for as long as the write path failed
    (AUDIT #179). The unconditional ``apply_spend_trueup`` retry records the
    charge, so the *next* request is refused at admission with 402.
    """
    _ok_upstream()
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            r = await c.put("/admin/pricing/gpt-4o", headers=AUTH,
                            json={"input_per_1m": 1.0, "output_per_1m": 1.0})
            assert r.status_code == 200, r.text
            # One completion costs $0.002; the cap is half of that.
            r = await c.post("/admin/keys/generate", headers=AUTH,
                             json={"name": "tiny", "max_budget": 0.001})
            assert r.status_code in (200, 201), r.text
            sk = r.json()["key"]
            hdr = {"Authorization": f"Bearer {sk}"}
            body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}

            async def _boom(_key_id, _cost):
                raise RuntimeError("database is locked")

            state.auth.update_spend = _boom  # type: ignore[assignment]

            first = await c.post("/v1/chat/completions", headers=hdr, json=body)
            assert first.status_code == 200, first.text
            second = await c.post("/v1/chat/completions", headers=hdr, json=body)
            health = (await c.get("/health")).json()

    assert second.status_code == 402, (
        f"the cap stopped being enforced after a failed charge: {second.status_code}"
        f" {second.text}")
    assert health["spend_charge_failures"] == 1, health


# ---------------------------------------------------------------------------
# #181 — admin delete/rename must persist before mutating in-memory routing
# ---------------------------------------------------------------------------

async def _failing_store(state, method: str):
    async def _boom(*_a, **_kw):
        raise RuntimeError("database is gone")

    setattr(state.config_store, method, _boom)


async def test_failed_provider_delete_leaves_routing_unchanged(tmp_path):
    """A 500 from the DB write must not leave the provider deleted in memory.

    Pre-fix the handler did ``del state.router.providers[name]`` (plus the
    alias and price-scope cleanup) *before* the DB write, so a failed write
    answered 500 while the running process had already dropped the provider —
    and, because the DB row survived, the provider and its plaintext key came
    back on the next restart. ``log_audit`` was never reached either, so the
    half-mutation left no trace (AUDIT #181).
    """
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            # A second provider that is NOT referenced by a group, so the
            # delete is allowed (the group check would 409 otherwise).
            r = await c.post("/admin/providers", headers=AUTH, json={
                "name": "ghost", "provider_type": "openai",
                "base_url": "https://ghost.test/v1", "key": "sk-ghost-secret"})
            assert r.status_code == 200, r.text
            before = (await c.get("/admin/providers", headers=AUTH)).json()

            await _failing_store(state, "delete_provider")
            r = await c.delete("/admin/providers/ghost", headers=AUTH)
            assert r.status_code >= 500, (
                f"a failed DB write must surface as an error: {r.status_code} {r.text}")

            after = (await c.get("/admin/providers", headers=AUTH)).json()

    assert r.status_code != 200
    assert "ghost" in state.router.providers, (
        "the provider was deleted in memory despite the failed DB write")
    assert "ghost" in {p["name"] for p in after["providers"]}, (
        f"routing state changed on a failed delete: {before} -> {after}")
    assert state.router.providers["ghost"].keys[0].secret == "sk-ghost-secret"


async def test_failed_provider_rename_leaves_routing_unchanged(tmp_path):
    """The mirror image: PATCH must not rename in memory before persisting.

    Pre-fix the rename (and the provider_type/base_url/round_robin edits) were
    applied to the live account first, so a failed DB write returned 500 while
    the process routed and billed under a name the DB had never heard of — and
    a restart silently reverted it (AUDIT #181).
    """
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            await _failing_store(state, "update_provider")
            r = await c.patch("/admin/providers/p1", headers=AUTH,
                              json={"name": "p1-renamed",
                                    "base_url": "https://elsewhere.test/v1",
                                    "provider_type": "anthropic"})
            assert r.status_code >= 500, (
                f"a failed DB write must surface as an error: {r.status_code} {r.text}")
            after = (await c.get("/admin/providers", headers=AUTH)).json()

    names = {p["name"] for p in after["providers"]}
    assert "p1" in state.router.providers, "the provider vanished from memory"
    assert "p1-renamed" not in state.router.providers, (
        "the rename took effect in memory despite the failed DB write")
    assert names == {"p1"}, f"routing state changed on a failed rename: {names}"
    acct = state.router.providers["p1"]
    assert acct.name == "p1"
    assert acct.base_url == "https://api.example.com/v1", (
        "base_url was mutated in memory despite the failed DB write")
    assert acct.provider_type == "openai", (
        "provider_type was mutated in memory despite the failed DB write")


async def test_successful_provider_rename_still_lands_and_audits(tmp_path):
    """Control: the persist-first order must not break the happy path.

    The rename has to move the account, the alias map and the price scopes in
    memory exactly as before, and still write its audit row (which the pre-fix
    failure path never reached).
    """
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            r = await c.post("/admin/providers", headers=AUTH, json={
                "name": "aliased", "provider_type": "openai",
                "base_url": "https://a.test/v1", "key": "sk-a", "alias_id": "al"})
            assert r.status_code == 200, r.text
            audits: list[tuple[str, str]] = []
            state.logs.log_audit = (  # type: ignore[assignment]
                lambda actor, action, target, diff=None: audits.append(
                    (action, target)) or asyncio.sleep(0))

            r = await c.patch("/admin/providers/aliased", headers=AUTH,
                              json={"name": "renamed"})
            assert r.status_code == 200, r.text
            after = (await c.get("/admin/providers", headers=AUTH)).json()

    assert "renamed" in state.router.providers
    assert "aliased" not in state.router.providers
    assert state.router.alias_to_provider == {"al": "renamed"}, (
        f"the alias map did not follow the rename: {state.router.alias_to_provider}")
    assert {p["name"] for p in after["providers"]} == {"p1", "renamed"}
    assert ("provider.update", "aliased→renamed") in audits, audits


# ---------------------------------------------------------------------------
# #183 — /health must not report "ok" for a gateway that cannot serve
# ---------------------------------------------------------------------------

async def test_health_is_degraded_with_zero_providers_but_still_200(tmp_path):
    """A gateway with no provider cannot serve; the probe must see that.

    Pre-fix ``status`` was the constant ``"ok"``, so this container was marked
    healthy forever and the Docker ``HEALTHCHECK`` (which only tests for a 200)
    kept it in rotation (AUDIT #183). The HTTP status deliberately stays 200:
    the process is alive, it simply cannot route, and a 503 here would make an
    orchestrator restart a perfectly healthy process.
    """
    app = create_app(_empty_config(tmp_path / "s.db"))
    async with LifespanManager(app), _client(app) as c:
        r = await c.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["providers"] == 0, body
    assert body["status"] != "ok", f"zero providers reported healthy: {body}"
    assert body["status"] == "degraded", body


async def test_health_is_degraded_when_no_group_has_an_available_deployment(tmp_path):
    """Providers exist but none can serve — still degraded, still 200."""
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            assert (await c.get("/health")).json()["status"] == "ok"
            for k in state.router.providers["p1"].keys:
                k.mark_cooling(60.0)
            r = await c.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["providers"] == 1, body
    assert body["available_groups"] == 0, body
    assert body["status"] == "degraded", body


async def test_health_surfaces_log_loss_counters(tmp_path):
    """The #171/#173 loss counters are exposed, and none of them is derivable
    from the request-log ring (a lost event is absent from it by definition)."""
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            state.logs.dropped_request_logs = 3
            state.logs.failed_request_log_writes = 5
            state.logs.dropped_proxy_logs = 7
            state.logs.failed_audit_log_writes = 11
            body = (await c.get("/health")).json()
    assert body["dropped_request_logs"] == 3, body
    assert body["failed_request_log_writes"] == 5, body
    assert body["dropped_proxy_logs"] == 7, body
    assert body["failed_audit_log_writes"] == 11, body
    assert body["dropped_log_events"] == 26, body


async def test_metrics_surface_the_spend_charge_failure_counter(tmp_path):
    """#179 must be observable from Prometheus too, not only /health.

    A counter that only exists in a JSON blob cannot drive an alert; the
    finding's fix sketch asks for both.
    """
    app = create_app(_config(tmp_path / "s.db", prometheus_enabled=True))
    async with LifespanManager(app):
        app.state.wiwi.spend_charge_failures = 4
        async with _client(app) as c:
            r = await c.get("/metrics", headers=AUTH)
    assert r.status_code == 200, r.text
    assert "wiwi_spend_charge_failures_total 4" in r.text, r.text


# ---------------------------------------------------------------------------
# #199 — reset_status must clear the fail streak, not just the status
# ---------------------------------------------------------------------------

@respx.mock
async def test_reset_status_clears_the_error_streak(tmp_path):
    """After an admin reset, ONE more failure must not re-retire the key.

    Pre-fix the handler set ``status="active"`` and ``cooldown_until=0.0`` but
    left ``err_count`` at its retirement value, so ``on_result``'s
    ``err_count >= key_max_consecutive_fails`` fired on the very next non-200
    and re-retired the key — the operator's "retry this key" silently reverted
    (AUDIT #199). ``ProviderKey.recover(force=True)`` resets the streak.
    """
    respx.post(OPENAI_URL).respond(500, json={"error": {"message": "upstream broke"}})
    # num_retries=0 so each request costs the key exactly one consecutive fail.
    app = create_app(_config(tmp_path / "s.db", num_retries=0))
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            sk = await _priced_vkey(c)
            hdr = {"Authorization": f"Bearer {sk}"}
            body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}

            # Drive the key to retirement the way a 5xx storm does. The
            # request path cannot do this on its own here: the key cools after
            # its first failure, so no second request is dispatched to it and
            # the streak never grows.
            acct = state.router.providers["p1"]
            key = acct.keys[0]
            for _ in range(5):
                acct.on_result(key, 500, None, failover_mode="any_error",
                               key_max_consecutive_fails=5)
            assert key.status == "invalid", (
                f"the key was not retired by the failure streak: {key.status!r}")

            r = await c.patch("/admin/providers/p1/keys/k1", headers=AUTH,
                              json={"reset_status": True})
            assert r.status_code == 200, r.text
            assert r.json()["key"]["err_count"] == 0, (
                f"reset_status left the fail streak behind: {r.json()}")

            # One more failure: cooled, not retired.
            await c.post("/v1/chat/completions", headers=hdr, json=body)
            after = (await c.get("/admin/providers", headers=AUTH)).json()

    k = after["providers"][0]["keys"][0]
    assert k["status"] != "invalid", (
        f"one failure after the admin reset re-retired the key: {k}")
    assert k["err_count"] == 1, (
        f"the fail streak was not cleared by reset_status: {k}")


async def test_reset_status_revives_a_terminal_invalid_key(tmp_path):
    """A terminal ``invalid`` (no cooldown window) must be revivable by an admin.

    ``mark_invalid(None)`` leaves ``cooldown_until == 0.0``, which plain
    ``recover()`` deliberately refuses to revive — only the admin/healer path
    may. If the reset cannot clear that state, "retry this key" has no effect
    on exactly the keys an operator most needs it for.
    """
    app = create_app(_config(tmp_path / "s.db"))
    async with LifespanManager(app):
        state = app.state.wiwi
        key = state.router.providers["p1"].keys[0]
        key.mark_invalid(None)  # terminal: no timed cooldown
        key.err_count = 5
        assert not key.available
        async with _client(app) as c:
            r = await c.patch("/admin/providers/p1/keys/k1", headers=AUTH,
                              json={"reset_status": True})
            assert r.status_code == 200, r.text
            view = r.json()["key"]
    assert view["status"] == "active", view
    assert view["err_count"] == 0, view
    assert key.available, "the admin reset left the key out of rotation"


# ---------------------------------------------------------------------------
# #177 — the Anthropic ping keep-alive must reach the client
# ---------------------------------------------------------------------------

ANTHROPIC_URL = "https://api.anthropic.test/v1/messages"


def _anthropic_config(db_path: str, ping_s: float) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="ant", provider="anthropic",
                               base_url="https://api.anthropic.test/v1",
                               keys=[KeyDef(label="default", key="sk-ant-test")])],
        model_list=[ModelEntry(
            model_name="claude-test",
            wiwi_params=DeploymentParams(provider="ant", model="claude-test"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=f"sqlite+aiosqlite:///{db_path}"),
        # The idle timeout must outlast the ping interval, otherwise the
        # gateway aborts the stream before the keep-alive can fire.
        router_settings=RouterSettings(stream_ping_interval_s=ping_s,
                                       stream_idle_timeout_s=10.0),
    )


async def _slow_anthropic_sse():
    """A thinking phase: no upstream bytes for well over one ping interval."""
    await asyncio.sleep(0.8)
    frames = (
        ('event: message_start\ndata: {"type":"message_start","message":'
         '{"model":"claude-test","usage":{"input_tokens":5}}}\n\n'),
        ('event: content_block_start\ndata: {"type":"content_block_start",'
         '"index":0,"content_block":{"type":"text","text":""}}\n\n'),
        ('event: content_block_delta\ndata: {"type":"content_block_delta",'
         '"index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n'),
        ('event: message_delta\ndata: {"type":"message_delta",'
         '"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'),
        'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    )
    for frame in frames:
        yield frame.encode()


@respx.mock
async def test_anthropic_ping_keepalive_reaches_the_client(tmp_path):
    """``stream_ping_interval_s`` must produce a client-visible ``ping`` frame.

    Pre-fix ``_stream_response`` sent every item through ``encoder.feed``,
    whose isinstance checks are delta-only, so the pump's raw ``bytes``
    keep-alive fell through every branch and returned None: the frame was
    silently discarded and a long thinking turn was still reaped by an idle
    proxy/ALB (AUDIT #177). The real timeout makes a hang FAIL the test rather
    than hang the suite.
    """
    respx.post(ANTHROPIC_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, content=_slow_anthropic_sse(),
            headers={"content-type": "text/event-stream"}))
    app = create_app(_anthropic_config(tmp_path / "s.db", ping_s=0.2))
    async with LifespanManager(app), _client(app) as c:
        r = await asyncio.wait_for(
            c.post("/v1/messages", headers=AUTH, json={
                "model": "claude-test", "max_tokens": 64, "stream": True,
                "messages": [{"role": "user", "content": "think hard"}]}),
            timeout=20)
    assert r.status_code == 200, r.text
    body = r.text
    assert 'event: ping\ndata: {"type": "ping"}\n\n' in body, (
        f"the keep-alive never reached the client:\n{body}")
    # The turn still completes normally around the keep-alive.
    assert "message_stop" in body, body
    assert '"text":"hi"' in body, body
