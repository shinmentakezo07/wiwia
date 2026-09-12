"""Round-41 regression tests — AUDIT #69-#88.

Bugs found by an audit of paths the thematic suite does not cover. Each test
below fails against the pre-fix source and pins the observable contract, not
the implementation.

Covered here (the ones reproducible without a live upstream):
- #69 a transient 5xx storm permanently retires a provider key
- #70 failed requests leak their estimated TPM reservation
- #71 last-admin guard bypassed by a truthy non-boolean ``disabled``
- #72 login throttle keyed on an un-normalized, caller-controlled username
- #78 ``cycle_every_n`` counters are per-request, so the cadence never fires
- #80 non-string username/password on the public auth endpoints → 400 not 500
- #81 ``PATCH /admin/users`` with a non-numeric ``disabled`` → 400 not 500
- #82 ``{"enabled": "false"}`` must not silently leave a key enabled
- #83 GenParams numeric fields must not be forwarded unvalidated
- #84 StreamTape.head_evicted must be membership-exact
- #86 read_timeseries empty-key_ids shape must match the zero-row shape
"""

from __future__ import annotations

import time

import orjson
import pytest

from wiwi.ratelimit.memory import RateLimiter
from wiwi.router.router import ProviderKey
from wiwi.streaming import deltas as dl
from wiwi.streaming.resume import StreamTape
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp

DEFAULT_MAX_FAILS = 5


def _on_result_any_error(key: ProviderKey, status: int, *,
                         max_fails: int = DEFAULT_MAX_FAILS,
                         retry_after: float | None = None) -> None:
    """Mirror of ProviderAccount.on_result for failover_mode='any_error'."""
    key.err_count += 2 if status in (401, 403) else 1
    if key.err_count >= max_fails:
        # mirror of the production bounded window (router.py mark_invalid)
        key.mark_invalid(min(60.0 * (2 if status in (401, 403) else 1), 600.0))
    else:
        ra = retry_after if (retry_after and retry_after > 0) else 5.0
        key.mark_cooling(min(ra, 30.0))


# ---------------------------------------------------------------------------
# #69 — a transient failure must not retire a key permanently
# ---------------------------------------------------------------------------

async def test_transient_5xx_does_not_permanently_retire_key():
    """Five consecutive 5xx responses must not leave the key out of service
    forever. Pre-fix: status becomes 'invalid', which ``available`` excludes
    and ``recover()`` never clears (it only revives 'cooling')."""
    key = ProviderKey(label="only", secret="s")
    for _ in range(DEFAULT_MAX_FAILS):
        _on_result_any_error(key, 503)

    # Time passes well beyond any cooldown.
    key.cooldown_until = 0.0
    _expire_cooldowns(key)

    assert key.available, (
        "a key retired by transient 5xx responses never returns to service: "
        f"status={key.status!r} err_count={key.err_count}"
    )


def _expire_cooldowns(key: ProviderKey) -> None:
    """Simulate the passage of time for a bounded-retirement key, then let
    it recover. The window must be a positive elapsed timestamp: recover()
    deliberately never revives a terminal invalid (cooldown_until == 0.0)."""
    for _ in range(10):
        key.cooldown_until = time.monotonic() - 1.0
        key.recover()


async def test_success_clears_the_failure_streak():
    """A success must restore the key even after it was marked invalid."""
    key = ProviderKey(label="only", secret="s")
    for _ in range(DEFAULT_MAX_FAILS):
        _on_result_any_error(key, 503)
    key.mark_recovered()
    assert key.available, "healer restore must put the key back in rotation"


# ---------------------------------------------------------------------------
# #70 — a failed request must not leak its TPM reservation
# ---------------------------------------------------------------------------

async def test_failed_request_releases_tpm_reservation():
    """A reservation whose request never consumed tokens must not throttle
    unrelated later requests. Pre-fix: ``RateLimiter`` has no release API and
    no failure path reconciles, so the estimate survives the whole window."""
    rl = RateLimiter()
    key = "sk-wiwi-test"

    allowed, _ = await rl.check(key, key_tpm=1000, est_tokens=800,
                                request_id="req-failed")
    assert allowed

    # The upstream now fails, so zero real tokens were consumed. The gateway
    # must be able to release the reservation it took.
    release = getattr(rl, "release", None)
    assert release is not None, (
        "RateLimiter exposes no release/refund API, so a failed request's "
        "estimated TPM reservation can never be reclaimed"
    )
    await release(key, request_id="req-failed")

    allowed2, retry2 = await rl.check(key, key_tpm=1000, est_tokens=300,
                                      request_id="req-next")
    assert allowed2, (
        f"unrelated small request throttled by a dead request's reservation "
        f"(retry_after={retry2})"
    )


# ---------------------------------------------------------------------------
# #71 — the last-admin guard must not be bypassable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("disabled", [True, 1, "1", 1.0, "true"])
async def test_last_admin_guard_cannot_be_bypassed_by_truthy_value(disabled):
    """Any value that disables an admin must also arm the guard.

    Pre-fix the guard tested ``disabled is True`` while the write path coerced
    with ``int()``, so 1/"1"/1.0 skipped the guard and still disabled the
    account. The endpoint must normalize the value to a bool *before* the
    guard, so the last enabled admin can never be locked out.
    """
    import httpx
    from asgi_lifespan import LifespanManager

    import wiwi.server.app as app_mod
    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            auth = {"Authorization": f"Bearer {master}"}
            # Mint exactly one DB admin.
            reg = await c.post("/auth/signup",
                               json={"username": "solo", "password": "password1"})
            assert reg.status_code == 201, reg.text
            uid = reg.json()["user"]["id"]
            promoted = await c.patch(f"/admin/users/{uid}", headers=auth,
                                     json={"role": "admin"})
            assert promoted.status_code == 200, promoted.text
            # Now attempt to disable the sole admin with each truthy shape.
            resp = await c.patch(f"/admin/users/{uid}", headers=auth,
                                 json={"disabled": disabled})
            assert resp.status_code == 400, (
                f"disabled={disabled!r} left the last admin disabled: "
                f"{resp.status_code} {resp.text}"
            )
            me = await c.get("/admin/users", headers=auth)
            assert me.status_code == 200
            row = next(u for u in me.json()["users"] if u["id"] == uid)
            assert row["disabled"] is False


# ---------------------------------------------------------------------------
# #72 — the login throttle key must be a normalized account identifier
# ---------------------------------------------------------------------------

async def test_login_throttle_keyed_on_normalized_username():
    """Case variants of one account must share a throttle bucket.

    Pre-fix the bucket was the raw body value, so ``alice``/``ALICE``/``Alice``
    each got a fresh failure budget against the same account. Drive the real
    endpoint and prove that mutating the case does not reset the budget.
    """
    import httpx
    from asgi_lifespan import LifespanManager

    import wiwi.server.app as app_mod
    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            # One real account, so a case variant resolves to it.
            reg = await c.post("/auth/signup",
                               json={"username": "alice", "password": "password1"})
            assert reg.status_code == 201, reg.text
            # Burn through the per-account failure budget, alternating case.
            statuses = []
            for i in range(12):
                variant = ["alice", "ALICE", "Alice"][i % 3]
                resp = await c.post("/auth/login",
                                    json={"username": variant,
                                          "password": "wrongpass"})
                statuses.append(resp.status_code)
            # If case variants had separate buckets, no request would ever be
            # throttled inside this run: each spelling gets its own budget.
            assert 429 in statuses, (
                "case variants of one account never shared a throttle bucket: "
                f"{statuses}"
            )


# ---------------------------------------------------------------------------
# #78 — cycle_every_n must actually rotate picks
# ---------------------------------------------------------------------------

async def test_cycle_every_n_rotates_under_skewed_weights():
    """With weights 10:1 and cycle_every_n=1, the strong key must yield.

    Pre-fix the counters lived in per-request ``ctx.metadata``, so they reset
    before every pick and the exclusion never engaged. Drive the real
    ``execute_with_retries`` across separate requests and assert the cadence
    changes which key is picked.
    """
    from wiwi.config import (
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        RouterSettings,
        WiwiConfig,
    )
    from wiwi.core.context import RequestContext
    from wiwi.ir.types import Request
    from wiwi.router.router import Router, execute_with_retries

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="strong", key="sk-aaaaaaaaaaaaaaaa", weight=10),
                                     KeyDef(label="weak", key="sk-bbbbbbbbbbbbbbbb", weight=1)])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params={"provider": "p1", "model": "gpt-4o"})],
        router_settings=RouterSettings(cycle_every_n=1),
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    router = Router(cfg)

    def ctx_for(rid: str) -> RequestContext:
        c = RequestContext(surface="chat",
                           ir_req=Request(model="gpt-4o", messages=[]),
                           request_id=rid)
        c.group = "gpt-4o"
        return c

    async def call_one(dep, key, ctx):
        return key.label

    # Simulate separate requests (fresh context each time) so a per-request
    # counter resets while the router-level counter persists.
    picks = [await execute_with_retries(router, ctx_for(f"r{i}"), call_one)
             for i in range(4)]

    assert len(set(picks)) > 1, (
        f"cycle_every_n had no effect across requests: always {picks[0]!r}"
    )


# ---------------------------------------------------------------------------
# #73 — rotating X-Forwarded-For must not mint a fresh throttle bucket
# ---------------------------------------------------------------------------

async def test_repeated_signup_with_rotating_xff_is_throttled():
    """A direct client rotating ``X-Forwarded-For`` must still be capped.

    Pre-fix `_client_ip` trusted XFF unconditionally, so each request landed
    in its own bucket and the signup cap never engaged.
    """
    import httpx
    from asgi_lifespan import LifespanManager

    import wiwi.server.app as app_mod
    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            statuses = []
            for i in range(40):
                # Rotate XFF on every request, as a spoofer would.
                resp = await c.post(
                    "/auth/signup",
                    headers={"x-forwarded-for": f"10.0.0.{i % 250 + 1}"},
                    json={"username": f"user{i}", "password": "password1"})
                statuses.append(resp.status_code)
            assert 429 in statuses, (
                "rotating X-Forwarded-For bypassed the signup cap (no 429 in "
                f"{len(statuses)} attempts): {sorted(set(statuses))}"
            )


# ---------------------------------------------------------------------------
# #80 / #81 — malformed auth input must be 400, not 500
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("username", [1, True, 1.5, []])
def test_non_string_username_is_a_client_error(username):
    """A non-string username must raise ValueError (→400), never TypeError /
    AttributeError (→500 with a traceback)."""
    from wiwi.auth.users import _validate_username

    with pytest.raises(ValueError):
        _validate_username(username)


@pytest.mark.parametrize("disabled", [[], {}, [1, 2], "false"])
async def test_patch_user_non_numeric_disabled_is_a_client_error(disabled):
    """A malformed ``disabled`` must return 400, never 500.

    Pre-fix ``int(disabled)`` raised TypeError for a list/dict, and the
    handler caught only ValueError — so ``{"disabled": []}`` returned an
    internal server error for the same malformed field that ``"false"``
    rejected with a 400.
    """
    import httpx
    from asgi_lifespan import LifespanManager

    import wiwi.server.app as app_mod
    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            auth = {"Authorization": f"Bearer {master}"}
            reg = await c.post("/auth/signup",
                               json={"username": "bob", "password": "password1"})
            assert reg.status_code == 201, reg.text
            uid = reg.json()["user"]["id"]
            resp = await c.patch(f"/admin/users/{uid}", headers=auth,
                                 json={"disabled": disabled})
            assert resp.status_code == 400, (
                f"disabled={disabled!r} returned {resp.status_code} "
                f"(expected 400): {resp.text}"
            )


# ---------------------------------------------------------------------------
# #82 — a string "false" must not silently enable a key
# ---------------------------------------------------------------------------

async def test_provider_key_enabled_string_false_rejected():
    """``{"enabled": "false"}`` must be rejected, not persisted as enabled.

    Pre-fix the PATCH path did ``key.enabled = bool(body["enabled"])``, and
    ``bool("false")`` is True — so the key the admin disabled kept serving and
    the response echoed ``enabled: true`` (AUDIT #82).
    """
    import httpx
    from asgi_lifespan import LifespanManager

    import wiwi.server.app as app_mod
    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            auth = {"Authorization": f"Bearer {master}"}
            resp = await c.patch("/admin/providers/p1/keys/a", headers=auth,
                                 json={"enabled": "false"})
            assert resp.status_code == 400, (
                f'`{{"enabled": "false"}}` returned {resp.status_code}: '
                f"{resp.text}"
            )
            # And the key must still be enabled (the request was rejected).
            listing = await c.get("/admin/providers", headers=auth)
            assert listing.status_code == 200, listing.text
            provider = next(p for p in listing.json()["providers"]
                            if p["name"] == "p1")
            row = next(k for k in provider["keys"] if k["label"] == "a")
            assert row["enabled"] is True
            # A real bool still works.
            ok = await c.patch("/admin/providers/p1/keys/a", headers=auth,
                               json={"enabled": False})
            assert ok.status_code == 200, ok.text
            assert ok.json()["key"]["enabled"] is False


# ---------------------------------------------------------------------------
# #83 — GenParams numeric fields must be validated on decode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [{"a": 1}, [], "abc", True])
def test_responses_non_numeric_max_output_tokens_rejected(bad):
    """A non-numeric max_output_tokens must be rejected locally, not forwarded
    upstream verbatim."""
    req = orp.decode_request({"model": "m", "max_output_tokens": bad, "input": "x"})
    assert req.gen_params.max_tokens is None, (
        f"max_output_tokens={bad!r} forwarded as "
        f"{req.gen_params.max_tokens!r} instead of being rejected"
    )


@pytest.mark.parametrize("bad", [{"a": 1}, [], "abc", True])
def test_chat_non_numeric_max_tokens_rejected(bad):
    req = oc.decode_request({"model": "m", "max_tokens": bad,
                             "messages": [{"role": "user", "content": "x"}]})
    assert req.gen_params.max_tokens is None, (
        f"max_tokens={bad!r} forwarded as {req.gen_params.max_tokens!r}"
    )


# ---------------------------------------------------------------------------
# #84 — head_evicted must be membership-exact
# ---------------------------------------------------------------------------

def test_head_evicted_is_membership_exact():
    """``head_evicted`` must report eviction of any seq the caller needs.

    Pre-fix it tests ``first > last_seq + 1``, which is a contiguity check that
    is only valid when ``last_seq`` is adjacent to the survivor set.
    """
    tape = StreamTape(max_bytes=60)
    for _ in range(20):
        tape.append(dl.TextDelta(text="x" * 10))

    survivors = {e.seq for e in tape._entries}
    first = min(survivors)
    assert first > 1, "precondition: eviction actually dropped the head"

    # last_seq=0: everything replay(0) would need is partly gone.
    assert tape.head_evicted(0) is True

    # A last_seq strictly below the survivors, non-adjacent: also evicted.
    mid = first - 3
    assert tape.head_evicted(mid) is True, (
        f"head_evicted({mid}) returned False while seqs {mid + 1}..{first - 1} "
        "are gone — replay would return a silently partial prefix"
    )


# ---------------------------------------------------------------------------
# #86 — zero-traffic shapes must agree
# ---------------------------------------------------------------------------

async def test_timeseries_empty_key_ids_matches_no_rows_shape(tmp_path):
    """``key_ids=[]`` must return the same bucket shape as the no-rows path."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from wiwi.logging_core.db_sink import DBSink

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    sink = DBSink(engine)
    await sink.startup()

    rows_shape = await sink.read_timeseries(60, "tokens", 60)
    empty_shape = await sink.read_timeseries(60, "tokens", 60, key_ids=[])
    await engine.dispose()

    assert len(empty_shape["buckets"]) == len(rows_shape["buckets"]), (
        f"key_ids=[] returned {len(empty_shape['buckets'])} buckets but the "
        f"no-rows path returns {len(rows_shape['buckets'])}"
    )


# ---------------------------------------------------------------------------
# #74 — OpenRouter must flush a deferred open before closing a reused index
# ---------------------------------------------------------------------------

def test_openrouter_reused_index_flushes_deferred_open():
    """A Close must never be emitted for an index whose Open is still deferred.

    Pre-fix the reused-index branch emitted ``ToolCallClose`` without first
    flushing ``_pending_opens``, so the encoders dropped the Close and the
    re-opened call never terminated (AUDIT #74).
    """
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    a = OpenRouterAdapter()
    # First chunk: id + name, no args -> Open is deferred.
    first = a.decode_stream_event("", orjson.dumps({
        "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "foo", "arguments": ""}}]}}],
    }).decode())
    assert first == [], f"open should be deferred, got {first}"

    # Second chunk reuses index 0 with a real id and args.
    second = a.decode_stream_event("", orjson.dumps({
        "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_2",
             "function": {"name": "bar", "arguments": "{}"}}]}}],
    }).decode())

    # The deferred Open must appear before its Close, and no Close may precede
    # the first Open in the stream.
    opens = [i for i, d in enumerate(second) if isinstance(d, dl.ToolCallOpen)]
    closes = [i for i, d in enumerate(second) if isinstance(d, dl.ToolCallClose)]
    assert opens, f"no ToolCallOpen emitted: {second}"
    assert closes, f"no ToolCallClose emitted: {second}"
    assert opens[0] < closes[0], (
        f"ToolCallClose emitted before any Open for the reused index: {second}"
    )


# ---------------------------------------------------------------------------
# #75 — NIM native markup must not collide with a structured open index
# ---------------------------------------------------------------------------

def test_nim_native_markup_does_not_collide_with_structured_index():
    """A native call must not reuse an index the structured path holds open.

    Pre-fix ``native_calls_to_deltas`` closed the structured call and reused
    index 0, concatenating both calls' args into one buffer (AUDIT #75).
    """
    from wiwi.providers.nim_native_tools import NativeToolCall, native_calls_to_deltas

    open_indices = {0}  # structured path already opened index 0
    tool_names: dict[int, str] = {0: "structured"}
    calls = (NativeToolCall(index=0, name="native", arguments={"x": 1}),)
    deltas = native_calls_to_deltas(calls, open_indices, tool_names)

    opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
    closes = [d for d in deltas if isinstance(d, dl.ToolCallClose)]
    assert len(opens) == 1, f"expected one native open, got {deltas}"
    assert opens[0].index != 0, (
        f"native call reused structured index 0: {deltas}"
    )
    # The structured call must not be closed by the native path.
    assert all(d.index != 0 for d in closes), (
        f"native path closed the structured call: {deltas}"
    )


# ---------------------------------------------------------------------------
# #76 — Gemini usage without finishReason must complete cleanly
# ---------------------------------------------------------------------------

def test_gemini_usage_without_finish_reason_completes_cleanly():
    """A terminal frame with usage but no finishReason must emit a full tail.

    Pre-fix the decoder returned ``[]``, so the pump reported a mid-stream
    failure and cooled a healthy deployment (AUDIT #76).
    """
    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    a.decode_stream_event("", orjson.dumps({
        "candidates": [{"content": {"parts": [{"text": "hi"}]}}]}).decode())
    tail = a.decode_stream_event("", orjson.dumps({
        "candidates": [{"content": {"parts": []}}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
    }).decode())

    kinds = [type(d).__name__ for d in tail]
    assert any(isinstance(d, dl.UsageFinal) for d in tail), (
        f"usage-bearing terminal frame emitted no UsageFinal: {kinds}"
    )
    assert any(isinstance(d, dl.Finish) for d in tail), (
        f"usage-bearing terminal frame emitted no Finish: {kinds}"
    )
    assert any(isinstance(d, dl.StreamEnd) for d in tail), (
        f"usage-bearing terminal frame emitted no StreamEnd: {kinds}"
    )


# ---------------------------------------------------------------------------
# #77 — OpenCode must clear per-stream tool state on response.failed
# ---------------------------------------------------------------------------

def test_opencode_failed_clears_resp_tools():
    """``response.failed`` must clear ``_resp_tools``/``_resp_next_index``.

    Pre-fix the branch set ``_resp_ended`` but left the tool map populated, so
    a reused adapter instance carried stale entries and indices (AUDIT #77).
    """
    from wiwi.providers.opencode_adapter import OpencodeAdapter

    a = OpencodeAdapter()
    # Select the Responses route through the public entry point.
    a.build_url("https://opencode.ai/zen/v1", "gpt-5-codex", True)
    assert a._last_route == "responses"
    a.decode_stream_event("", orjson.dumps({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "id": "fc_1", "name": "foo"},
    }).decode())
    assert a._resp_tools, "precondition: a tool was registered"

    a.decode_stream_event("", orjson.dumps({
        "type": "response.failed",
        "response": {"error": {"message": "boom"}},
    }).decode())

    assert a._resp_tools == {}, f"stale tool state leaked: {a._resp_tools}"
    assert a._resp_next_index == 0, (
        f"index counter leaked: {a._resp_next_index}")


# ---------------------------------------------------------------------------
# #79 — a streaming success must graduate a probation deployment
# ---------------------------------------------------------------------------

async def test_streaming_success_graduates_probation_deployment():
    """A cleanly completed stream must clear ``dep.probation``.

    Pre-fix graduation lived only in ``execute_with_retries``, which streams
    skip (``ctx._defer_key_credit`` is True), so a healer-restored deployment
    that served only streams stayed in probation (and demoted by the fresh
    filter) forever (AUDIT #79). Drive a real streaming request through the app
    and assert the pump graduates the deployment.
    """
    import httpx
    import respx
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

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-aaaaaaaaaaaaaaaa")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        router_settings=RouterSettings(num_retries=0),
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(cfg)
    body = (
        'data: {"id":"x","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{"content":"hi"},'
        '"finish_reason":null}]}\n\n'
        'data: {"id":"x","object":"chat.completion.chunk",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, text=body,
                headers={"content-type": "text/event-stream"}))
        async with LifespanManager(app):
            router = app.state.wiwi.router
            dep = router.groups["gpt-4o"][0]
            dep.probation = True
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as c:
                resp = await c.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {master}"},
                    json={"model": "gpt-4o", "stream": True,
                          "messages": [{"role": "user", "content": "hi"}]})
                assert resp.status_code == 200, resp.text
                assert "data:" in resp.text

    assert dep.probation is False, (
        "a cleanly completed stream left the deployment in probation"
    )


# ---------------------------------------------------------------------------
# #85 — a non-string provider key secret must be rejected
# ---------------------------------------------------------------------------

async def test_provider_key_non_string_secret_rejected():
    """``{"key": {"nested": true}}`` must be rejected with 400, not persisted.

    Pre-fix ``str({...})`` stored a literal ``"{'nested': True}"`` as the
    upstream credential (AUDIT #85).
    """
    import httpx
    from asgi_lifespan import LifespanManager

    import wiwi.server.app as app_mod
    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            auth = {"Authorization": f"Bearer {master}"}
            resp = await c.post("/admin/providers/p1/keys", headers=auth,
                                json={"label": "bad", "key": {"nested": True}})
            assert resp.status_code == 400, (
                f"non-string key returned {resp.status_code}: {resp.text}")
            listing = await c.get("/admin/providers", headers=auth)
            provider = next(p for p in listing.json()["providers"]
                            if p["name"] == "p1")
            assert all(k["label"] != "bad" for k in provider["keys"])


# ---------------------------------------------------------------------------
# #87 — an unknown WorkBuddy business envelope must be retryable
# ---------------------------------------------------------------------------

def test_workbuddy_unknown_envelope_is_retryable():
    """An unknown ``{code, msg}`` envelope must be retryable so failover runs.

    Pre-fix it was non-retryable, failing the client immediately instead of
    trying another deployment/key (AUDIT #87).
    """
    from wiwi.providers.workbuddy_adapter import _envelope_error

    err = _envelope_error({"code": 11102, "msg": "transient upstream hiccup"})
    assert err.retryable is True, (
        f"unknown envelope code was {err.status} retryable={err.retryable}")
    # Credit exhaustion stays non-retryable and maps to 402.
    credit = _envelope_error({"code": 402, "msg": "insufficient credit"})
    assert credit.status == 402 and credit.retryable is False


# ---------------------------------------------------------------------------
# #88 — NIM must adopt a synthesized open when a real id arrives later
# ---------------------------------------------------------------------------

def test_nim_synthesized_open_adopts_later_real_id():
    """Args-before-id must synthesize exactly one Open; a later id adopts it.

    Pre-fix NIM closed the synthesized call and reopened it — two Opens for one
    index, breaking the nested contract (AUDIT #88).
    """
    from wiwi.providers.nim_adapter import NimAdapter

    a = NimAdapter()
    # First chunk: args with no id -> synthesize an Open.
    first = a.decode_stream_event("", orjson.dumps({
        "choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"a\":1}"}}]}}],
    }).decode())
    opens = [d for d in first if isinstance(d, dl.ToolCallOpen)]
    assert len(opens) == 1, f"expected a synthesized open: {first}"
    assert opens[0].id == ""

    # Second chunk: the real id arrives for the same index.
    second = a.decode_stream_event("", orjson.dumps({
        "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_real", "function": {"name": "foo"}}]}}],
    }).decode())
    assert not any(isinstance(d, dl.ToolCallOpen) for d in second), (
        f"adopting the real id re-opened the call: {second}"
    )
    assert not any(isinstance(d, dl.ToolCallClose) for d in second), (
        f"adopting the real id closed the call: {second}"
    )
