"""Round 55 — the deep-audit findings (see ``AUDIT_REPORT.md``).

Every test here pins a defect that the suite could not see because the
thematic files each covered a *neighbouring* site. The through-line is
asymmetry: one path was fixed and its copy was not, so the bug survived a
green suite.

* **C1** budget caps were advisory — ``update_spend`` refuses the charge that
  would cross ``max_budget`` and leaves ``spend_to_date`` untouched, while the
  upstream had already served and billed the request. Non-streaming refused
  after billing; streaming never refused at all.
* **C2** the response-cache key omitted ``Request.extras``, so two requests
  differing only in a *forwarded* parameter collided and the second got the
  first's answer for the whole TTL.
* **C3** ``is_admin`` is a master-key bearer compare, so an authenticated
  ``role=admin`` *session* was refused by 33 of 48 ``/admin/*`` routes.
* **H1** a provider rename left ``alias_to_provider`` pointing at the old name.
* **H2/H3** the ``force_stream`` non-streaming path let a mid-body transport
  error escape as a raw ``httpx`` exception (no retry/failover/health
  accounting) and billed zero with ``estimated=False``.
* **H4-H8** codec and adapter decode gaps: malformed-but-plausible client
  bodies, and upstream frames the sibling adapters already handled.
* **H9** ``expire_keys`` expired the DB row without evicting the auth cache.
* **M3/M4** a non-bool ``disabled`` was coerced (inverting ``"false"``), and
  the Redis backend was selected even when the extra was missing.

Assertions are behavioural on purpose: they must hold for any correct
implementation, so a later refactor cannot silently re-open these.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.auth.service import AuthService
from wiwi.config import (
    CacheSettings,
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.ir import types as ir
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}

OPENAI_BODY = {
    "id": "chatcmpl-1", "object": "chat.completion", "created": 1,
    "model": "gpt-4o",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
}

OPENAI_SSE = (
    'data: {"id":"1","object":"chat.completion.chunk","created":1,'
    '"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"hi"},'
    '"finish_reason":null}]}\n\n'
    'data: {"id":"1","object":"chat.completion.chunk","created":1,'
    '"model":"gpt-4o","choices":[{"index":0,"delta":{},'
    '"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":1000,"completion_tokens":1000}}\n\n'
    "data: [DONE]\n\n"
)


def _config(db_path: str, **kwargs) -> WiwiConfig:
    """One provider, one deployment, priced later by the test that needs it."""
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://api.example.com/v1",
                               keys=[KeyDef(label="k1", key="sk-x")])],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=f"sqlite+aiosqlite:///{db_path}"),
        **kwargs,
    )


async def _client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# C1 — budget caps must hold on both response paths
# ---------------------------------------------------------------------------

async def _mint_capped_key(client, budget: float) -> str:
    """Price gpt-4o so one completion costs $0.002, then mint a capped key."""
    r = await client.put("/admin/pricing/gpt-4o", headers=AUTH,
                         json={"input_per_1m": 1.0, "output_per_1m": 1.0})
    assert r.status_code == 200, r.text
    r = await client.post("/admin/keys/generate", headers=AUTH,
                          json={"name": "capped", "max_budget": budget})
    assert r.status_code in (200, 201), r.text
    return r.json()["key"]


async def _spend_of(client, key_id: str) -> float:
    r = await client.get("/admin/keys", headers=AUTH)
    return next(k for k in r.json()["keys"] if k["id"] == key_id)["spend_to_date"]


@respx.mock
async def test_non_streaming_budget_charge_is_recorded_when_it_crosses_the_cap(
        tmp_path):
    """A charge that crosses the cap is still real money.

    Pre-fix ``update_spend`` returned False and left ``spend_to_date`` at its
    old value, so the request was refused *after* the upstream billed it and
    the spend was lost. Repeated requests then passed the pre-flight check
    forever (AUDIT_REPORT C1, previously AUDIT.md #52).
    """
    respx.post("https://api.example.com/v1/chat/completions").respond(
        json=OPENAI_BODY)
    app = create_app(_config(f"{tmp_path}/c1a.db"))
    async with LifespanManager(app), await _client(app) as c:
        key = await _mint_capped_key(c, 0.005)
        keys = (await c.get("/admin/keys", headers=AUTH)).json()["keys"]
        kid = keys[0]["id"]
        auth = {"Authorization": f"Bearer {key}"}

        # Each request costs $0.002. Two fit under $0.005; the third
        # crosses it.
        for i in range(2):
            r = await c.post("/v1/chat/completions", headers=auth,
                             json={"model": "gpt-4o",
                                   "messages": [{"role": "user",
                                                 "content": f"q{i}"}]})
            assert r.status_code == 200, f"request {i}: {r.status_code} {r.text}"

        before = await _spend_of(c, kid)
        assert before == pytest.approx(0.004), (
            f"two $0.002 requests should have recorded $0.004, got {before}")

        r = await c.post("/v1/chat/completions", headers=auth,
                         json={"model": "gpt-4o",
                               "messages": [{"role": "user", "content": "q3"}]})
        assert r.status_code == 402, (
            f"a request that crosses the cap must be refused, got {r.status_code}")

        after = await _spend_of(c, kid)
        assert after > before, (
            "the upstream served and billed this request, so its cost must be "
            f"recorded even though the response was refused; spend stayed at {after}")
        assert after >= 0.005, (
            f"the recorded spend must reflect the real charge, got {after}")

        # And the key must stay refused — the cap is a ceiling on new work.
        r = await c.post("/v1/chat/completions", headers=auth,
                         json={"model": "gpt-4o",
                               "messages": [{"role": "user", "content": "q4"}]})
        assert r.status_code == 402, (
            f"an over-budget key must stay refused, got {r.status_code}")


@respx.mock
async def test_streaming_budget_is_enforced_not_just_recorded(tmp_path):
    """The streaming path never tripped the cap at all.

    Pre-fix every request returned 200 with content while ``spend_to_date``
    froze at the first crossing, so a capped key streamed forever
    (AUDIT_REPORT C1).
    """
    respx.post("https://api.example.com/v1/chat/completions").respond(
        text=OPENAI_SSE, headers={"content-type": "text/event-stream"})
    app = create_app(_config(f"{tmp_path}/c1b.db"))
    async with LifespanManager(app), await _client(app) as c:
        key = await _mint_capped_key(c, 0.005)
        kid = (await c.get("/admin/keys", headers=AUTH)).json()["keys"][0]["id"]
        auth = {"Authorization": f"Bearer {key}"}

        codes = []
        for i in range(4):
            r = await c.post("/v1/chat/completions", headers=auth,
                             json={"model": "gpt-4o", "stream": True,
                                   "messages": [{"role": "user",
                                                 "content": f"q{i}"}]})
            codes.append(r.status_code)

        spend = await _spend_of(c, kid)
        assert spend >= 0.005, (
            f"streaming spend must be recorded, got {spend}")
        assert 402 in codes, (
            "an over-budget key must eventually be refused on the streaming "
            f"surface too; got {codes}")
        assert codes[0] == 200, f"the first request should succeed, got {codes}"


# ---------------------------------------------------------------------------
# C2 — the response cache must key on the whole request
# ---------------------------------------------------------------------------

def _cache_config(db_path: str) -> WiwiConfig:
    return _config(db_path, cache_settings=CacheSettings(enabled=True, ttl_s=600))


@respx.mock
async def test_cache_does_not_collide_on_a_forwarded_extra(tmp_path):
    """Two requests differing only in ``service_tier`` are not the same request.

    ``extras`` rides through to the upstream
    (``providers/openai_adapter.py`` forwards ``_STANDARD``), but the cache key
    ignored it, so the second caller got the first caller's completion with
    ``x-wiwi-cache: HIT`` (AUDIT_REPORT C2).
    """
    route = respx.post("https://api.example.com/v1/chat/completions")
    route.side_effect = [
        httpx.Response(200, json={**OPENAI_BODY, "choices": [
            {"index": 0, "finish_reason": "stop",
             "message": {"role": "assistant", "content": "FLEX"}}]}),
        httpx.Response(200, json={**OPENAI_BODY, "choices": [
            {"index": 0, "finish_reason": "stop",
             "message": {"role": "assistant", "content": "PRIORITY"}}]}),
    ]
    app = create_app(_cache_config(f"{tmp_path}/c2a.db"))
    async with LifespanManager(app), await _client(app) as c:
        r = await c.post("/admin/keys/generate", headers=AUTH,
                         json={"name": "k"})
        auth = {"Authorization": f"Bearer {r.json()['key']}"}
        base = {"model": "gpt-4o",
                "messages": [{"role": "user", "content": "same"}]}

        r1 = await c.post("/v1/chat/completions", headers=auth,
                          json={**base, "service_tier": "flex"})
        r2 = await c.post("/v1/chat/completions", headers=auth,
                          json={**base, "service_tier": "priority"})

        body1 = r1.json()["choices"][0]["message"]["content"]
        body2 = r2.json()["choices"][0]["message"]["content"]
        assert r2.headers.get("x-wiwi-cache") != "HIT", (
            "a request carrying a different forwarded parameter must not be "
            f"served from the cache (got body {body2!r})")
        assert body1 == "FLEX" and body2 == "PRIORITY", (
            f"each request must reach upstream: got {body1!r} / {body2!r}")
        assert route.call_count == 2, (
            f"both requests must reach upstream, got {route.call_count}")


@respx.mock
async def test_identical_request_is_still_served_from_the_cache(tmp_path):
    """Control: the C2 fix must not disable caching outright."""
    respx.post("https://api.example.com/v1/chat/completions").respond(
        json=OPENAI_BODY)
    app = create_app(_cache_config(f"{tmp_path}/c2b.db"))
    async with LifespanManager(app), await _client(app) as c:
        r = await c.post("/admin/keys/generate", headers=AUTH,
                         json={"name": "k"})
        auth = {"Authorization": f"Bearer {r.json()['key']}"}
        body = {"model": "gpt-4o",
                "messages": [{"role": "user", "content": "same"}]}
        first = await c.post("/v1/chat/completions", headers=auth, json=body)
        second = await c.post("/v1/chat/completions", headers=auth, json=body)
        assert first.status_code == 200 and second.status_code == 200
        assert second.headers.get("x-wiwi-cache") == "HIT", (
            "a byte-identical request must still hit the cache")


# ---------------------------------------------------------------------------
# C3 — an admin session must reach the admin surface
# ---------------------------------------------------------------------------

async def _promoted_admin_client(app):
    """Sign up, promote to admin via the master key, then log in by cookie."""
    c = await _client(app)
    r = await c.post("/auth/signup", json={"username": "auditadmin",
                                           "password": "password1"})
    uid = r.json()["user"]["id"]
    r = await c.patch(f"/admin/users/{uid}", headers=AUTH,
                      json={"role": "admin"})
    assert r.status_code == 200, r.text
    c.cookies.clear()  # drop the signup session so the login is the only credential
    r = await c.post("/auth/login", json={"username": "auditadmin",
                                          "password": "password1"})
    assert r.json()["user"]["role"] == "admin", r.text
    return c


@pytest.mark.parametrize("path", [
    "/admin/providers",
    "/admin/provider-catalog",
    "/admin/pricing",
    "/admin/logs/proxy",
    "/admin/alert-rules",
    "/admin/keys",
    "/admin/models",
])
async def test_admin_session_reaches_every_admin_route(tmp_path, path):
    """``README.md``/``docs/ADMIN.md`` promise "master key **or** admin session".

    Pre-fix only three routes used the session-aware guard; the rest compared
    the bearer against the master key alone, so a promoted admin using a cookie
    got 401 (AUDIT_REPORT C3).
    """
    app = create_app(_config(f"{tmp_path}/c3.db"))
    async with LifespanManager(app):
        c = await _promoted_admin_client(app)
        try:
            r = await c.get(path)
            assert r.status_code == 200, (
                f"an authenticated admin session must reach {path}, "
                f"got {r.status_code}: {r.text[:200]}")
        finally:
            await c.aclose()


@pytest.mark.parametrize("path", ["/admin/providers", "/admin/logs/proxy"])
async def test_anonymous_and_plain_user_sessions_are_still_refused(tmp_path, path):
    """Control: widening the guard must not open the admin surface.

    Only admin-only routes belong here. `/admin/keys`, `/admin/models` and
    `/admin/stats/*` are *actor-scoped* by design — a plain user gets 200 with
    only their own rows (``docs/ADMIN.md`` "User … Sees Own keys", pinned by
    ``tests/test_user_accounts.py::test_user_keys_scoped``) — so they are
    covered by the 200 assertion in the sibling test above, not this one.
    """
    app = create_app(_config(f"{tmp_path}/c3ctl.db"))
    async with LifespanManager(app), await _client(app) as c:
        r = await c.get(path)
        assert r.status_code == 401, (
            f"anonymous access to {path} must be 401, got {r.status_code}")

        await c.post("/auth/signup", json={"username": "plainuser",
                                           "password": "password1"})
        r = await c.get(path)
        assert r.status_code == 403, (
            f"a non-admin session must be 403 on {path}, got {r.status_code}")


# ---------------------------------------------------------------------------
# H1 — a provider rename must repair the alias map
# ---------------------------------------------------------------------------

async def test_rename_repairs_alias_to_provider(tmp_path):
    """``alias_to_provider`` is keyed by provider *name*, so a rename must rewrite it.

    Pre-fix the repair was gated on the PATCH also carrying ``alias_id``, so a
    plain rename left the map pointing at the old name: the alias resolved to
    nothing, and a later provider reusing that name silently inherited it
    (AUDIT_REPORT H1).
    """
    app = create_app(_config(f"{tmp_path}/h1.db"))
    async with LifespanManager(app), await _client(app) as c:
        r = await c.post("/admin/providers", headers=AUTH,
                         json={"name": "p2", "provider_type": "openai",
                               "base_url": "https://api.example.com/v1",
                               "alias_id": "myalias", "key": "sk-y"})
        assert r.status_code == 200, r.text
        await c.post("/admin/model-groups/shared/deployments", headers=AUTH,
                     json={"provider": "p2", "model_id": "m"})

        state = app.state.wiwi
        # The alias branch returns the requested name as the group and the
        # provider's deployments as the deps (router.py:429-433), so the
        # contract to assert is *which provider* the alias resolves to.
        _, deps = state.router.resolve_group("myalias")
        assert {d.provider.name for d in deps} == {"p2"}

        # Exactly what the console's "Account settings" card sends.
        r = await c.patch("/admin/providers/p2", headers=AUTH,
                          json={"name": "p2ren"})
        assert r.status_code == 200, r.text

        assert state.router.alias_to_provider.get("myalias") == "p2ren", (
            "the alias map must follow the rename, got "
            f"{state.router.alias_to_provider}")
        _, deps = state.router.resolve_group("myalias")
        assert {d.provider.name for d in deps} == {"p2ren"}, (
            "the alias must keep resolving to the renamed account")


async def test_rename_does_not_strand_an_alias_for_a_reused_name(tmp_path):
    """The dangerous half: a freed name must not capture the alias."""
    app = create_app(_config(f"{tmp_path}/h1b.db"))
    async with LifespanManager(app), await _client(app) as c:
        await c.post("/admin/providers", headers=AUTH,
                     json={"name": "p2", "provider_type": "openai",
                           "base_url": "https://api.example.com/v1",
                           "alias_id": "myalias", "key": "sk-y"})
        await c.post("/admin/model-groups/shared/deployments", headers=AUTH,
                     json={"provider": "p2", "model_id": "m"})
        await c.patch("/admin/providers/p2", headers=AUTH,
                      json={"name": "p2ren"})

        # A new account reuses the now-free name.
        await c.post("/admin/providers", headers=AUTH,
                     json={"name": "p2", "provider_type": "openai",
                           "base_url": "https://api.example.com/v1",
                           "key": "sk-z"})
        await c.post("/admin/model-groups/other/deployments", headers=AUTH,
                     json={"provider": "p2", "model_id": "m"})

        _, deps = app.state.wiwi.router.resolve_group("myalias")
        assert {d.provider.name for d in deps} == {"p2ren"}, (
            "the alias must still point at the renamed account, not at a "
            "different provider that happens to reuse the old name")


# ---------------------------------------------------------------------------
# H2/H3 — the force_stream non-streaming path
# ---------------------------------------------------------------------------

def _force_stream_config(db_path: str) -> WiwiConfig:
    """WorkBuddy is ``force_stream``: its upstream has no non-streaming mode."""
    return WiwiConfig(
        providers=[ProviderDef(name="wb", provider="workbuddy",
                               base_url="https://api.example.com/v1",
                               keys=[KeyDef(label="k1", key="tok")])],
        model_list=[ModelEntry(
            model_name="m",
            wiwi_params=DeploymentParams(provider="wb", model="m"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=f"sqlite+aiosqlite:///{db_path}"),
        router_settings=RouterSettings(num_retries=2),
    )


def test_force_stream_transport_error_becomes_a_retryable_wiwi_error():
    """A mid-body drop must be a ``WiwiError`` so retries/failover/health run.

    Pre-fix the raw ``httpx`` exception escaped ``_complete_via_stream``, and
    the retry loop catches only ``WiwiError`` — so a transport drop produced no
    retry, no failover and no key/deployment penalty (AUDIT_REPORT H2).

    Driven through ``Gateway.complete`` on purpose: key/deployment accounting
    belongs to ``execute_with_retries``' except handler
    (``core/gateway.py:885-886`` — "don't double-count key errors here"), so
    asserting on the private method would pin the wrong layer.
    """
    import asyncio
    import socket
    import threading

    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.providers.base import WiwiError
    from wiwi.router.router import Router

    def serve(sock):
        conn, _ = sock.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                     b"Transfer-Encoding: chunked\r\n\r\n")
        body = b'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n'
        conn.sendall(b"%x\r\n" % len(body) + body + b"\r\n")
        conn.close()  # close WITHOUT the terminating 0-length chunk

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]
    threading.Thread(target=serve, args=(sock,), daemon=True).start()

    cfg = _force_stream_config(":memory:")
    cfg.providers[0].base_url = f"http://127.0.0.1:{port}"

    async def run():
        router = Router(cfg)
        gw = Gateway(router, CostEngine({}), drop_params=True)
        try:
            ctx = RequestContext(surface="chat", ir_req=ir.Request(
                model="m",
                messages=[ir.Message(role="user",
                                     parts=[ir.TextPart("hi")])]))
            # execute_with_retries builds its work queue from ctx.group; without
            # it the loop never runs and no attempt is made.
            ctx.group = "m"
            # num_retries=2 means the same dead peer is retried; every attempt
            # must surface as a WiwiError rather than a raw transport error.
            with pytest.raises(WiwiError):
                await gw.complete(ctx)

            key = router.groups["m"][0].provider.keys[0]
            assert key.err_count > 0 or key.status != "active", (
                "a transport failure must be accounted against the key by the "
                f"retry loop (err_count={key.err_count}, status={key.status})")
        finally:
            await gw.aclose()

    asyncio.run(run())


def test_force_stream_prices_estimated_usage_when_upstream_omits_it():
    """Cline/WorkBuddy pop ``stream_options``, so no-usage is the normal case.

    Pre-fix this path priced the zeros directly and reported
    ``estimated=False``, presenting $0.00 as provider-reported fact
    (AUDIT_REPORT H3).
    """
    import asyncio

    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.providers.registry import fresh_adapter
    from wiwi.router.router import Router

    body = ('data: {"choices":[{"index":0,"delta":{"content":"hello world"}}]}\n\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n")

    async def run():
        cfg = _force_stream_config(":memory:")
        router = Router(cfg)
        cost = CostEngine({})
        cost.prices["workbuddy/m"] = {"input_cost_per_token": 1e-6,
                                      "output_cost_per_token": 1e-6}
        gw = Gateway(router, cost, drop_params=True)
        try:
            dep = router.groups["m"][0]
            key = dep.provider.keys[0]
            ctx = RequestContext(surface="chat", ir_req=ir.Request(
                model="m",
                messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])]))
            adapter = fresh_adapter(dep.provider.provider_type)

            with respx.mock:
                # WorkBuddyAdapter.build_url appends /v2 (its upstream has no
                # plain /chat/completions route), so the mock must match it.
                respx.post("https://api.example.com/v1/v2/chat/completions").respond(
                    text=body, headers={"content-type": "text/event-stream"})
                turn = await gw._complete_via_stream(dep, key, ctx, adapter)

            assert turn.usage.prompt_tokens > 0, (
                "an upstream that omits usage must be estimated, not billed as zero")
            assert turn.usage.estimated is True, (
                "estimated counts must not be presented as provider-reported fact")
            assert ctx.cost > 0, f"a non-zero usage must cost something, got {ctx.cost}"
        finally:
            await gw.aclose()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# H4 — malformed-but-plausible bodies must not 500 the codecs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body,codec", [
    # chat: image_url as a bare string (a shape real clients send)
    ({"model": "m", "messages": [{"role": "tool", "tool_call_id": "1",
                                  "content": [{"type": "image_url",
                                               "image_url": "http://x/y.png"}]}]},
     oc.decode_request),
    # chat: non-string url
    ({"model": "m", "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": 7}}]}]},
     oc.decode_request),
    # chat: non-dict json_schema (the Responses twin of this was AUDIT #100)
    ({"model": "m", "messages": [],
      "response_format": {"type": "json_schema", "json_schema": "abc"}},
     oc.decode_request),
    # anthropic: non-dict source
    ({"model": "m", "max_tokens": 1, "messages": [{"role": "user", "content": [
        {"type": "image", "source": "http://x/i.png"}]}]},
     am.decode_request),
    ({"model": "m", "max_tokens": 1, "messages": [{"role": "user", "content": [
        {"type": "document", "source": 9}]}]},
     am.decode_request),
])
def test_malformed_content_blocks_do_not_raise(body, codec):
    """These escaped as AttributeError -> HTTP 500 (``run_chat_like`` catches
    only ``DialectError``/``ValueError``). Skipping the block is the correct
    behaviour — the neighbouring blocks in the same loops already do that
    (AUDIT_REPORT H4)."""
    codec(body)  # must not raise


def test_non_dict_tool_schema_is_coerced_not_crashed():
    """A non-dict ``parameters`` was stored verbatim and later crashed
    ``validate_tool_args`` *inside the stream pump*, cooling a healthy
    deployment for a caller-controlled shape (AUDIT_REPORT H4)."""
    req = oc.decode_request({
        "model": "m", "messages": [],
        "tools": [{"type": "function",
                   "function": {"name": "t", "parameters": "oops"}}]})
    schema = req.tools[0].parameters_json_schema
    assert isinstance(schema, dict), (
        f"a non-dict tool schema must be coerced at decode, got {schema!r}")


def test_genuinely_malformed_body_still_raises_a_client_error():
    """Control: the guards must not swallow real decode errors.

    The codecs are deliberately lenient about content shapes (a malformed
    *block* is skipped, per the H4 fix), but a body missing the required
    ``model`` is still a client error and must reach the 400 path.
    """
    from wiwi.wire.openai_chat import DialectError
    with pytest.raises((DialectError, ValueError)):
        oc.decode_request({"messages": [{"role": "user", "content": "hi"}]})


# ---------------------------------------------------------------------------
# H5-H8 — adapter decode gaps
# ---------------------------------------------------------------------------

def _tool_frame(arguments):
    return json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 0, "id": "call_1", "type": "function",
         "function": {"name": "weather", "arguments": arguments}}]}}]})


@pytest.mark.parametrize("adapter_name", ["openai", "openrouter", "nvidia-nim"])
def test_dict_valued_tool_arguments_are_normalized(adapter_name):
    """``ToolCallArgsDelta.args_fragment`` is typed ``str``; a dict fragment
    crashed the Responses encoder with ``TypeError: can only concatenate str``
    (AUDIT_REPORT H5). The base adapter already normalizes it."""
    from wiwi.providers.registry import fresh_adapter

    adapter = fresh_adapter(adapter_name)
    out = adapter.decode_stream_event("", _tool_frame({"city": "SF"}))
    frags = [d.args_fragment for d in out if isinstance(d, dl.ToolCallArgsDelta)]
    assert frags, f"{adapter_name} emitted no args delta"
    for f in frags:
        assert isinstance(f, str), (
            f"{adapter_name} emitted a non-str args fragment: {f!r}")


@pytest.mark.parametrize("adapter_name", ["openai", "openrouter", "nvidia-nim"])
def test_done_frame_closes_open_tool_calls(adapter_name):
    """A ``[DONE]``-terminated stream must not leave tool calls open.

    An unterminated call makes the gateway synthesize ``Finish("stop")`` for a
    tool-call turn — the AUDIT #133 corruption that stops Claude Code's agent
    loop. NIM was the third copy-derived site and was missed
    (AUDIT_REPORT H6).
    """
    from wiwi.providers.registry import fresh_adapter

    adapter = fresh_adapter(adapter_name)
    out = adapter.decode_stream_event("", _tool_frame('{"x":1}'))
    out += adapter.decode_stream_event("", "[DONE]")

    opens = sum(1 for d in out if isinstance(d, dl.ToolCallOpen))
    closes = sum(1 for d in out if isinstance(d, dl.ToolCallClose))
    assert opens == closes, (
        f"{adapter_name} left {opens - closes} tool call(s) open on [DONE]")

    finishes = [d for d in out if isinstance(d, dl.Finish)]
    if opens:
        assert finishes and finishes[-1].stop_reason == "tool_call", (
            f"{adapter_name} must report a tool-call finish, got "
            f"{[f.stop_reason for f in finishes]}")


@pytest.mark.parametrize("adapter_name,frame", [
    ("nvidia-nim", {"choices": [None]}),
    ("gemini", {"candidates": [None]}),
])
def test_non_dict_choice_elements_are_skipped(adapter_name, frame):
    """The round-49 fix guarded the SSE *frame* but not its first element, so a
    ``null`` choice raised ``AttributeError`` -> 500 + cooldown for a frame
    carrying no semantics (AUDIT_REPORT H7)."""
    from wiwi.providers.registry import fresh_adapter

    out = fresh_adapter(adapter_name).decode_stream_event("", json.dumps(frame))
    assert out == [] or all(isinstance(d, dl.StreamStart) for d in out), (
        f"{adapter_name} should skip a null choice, got {out}")


def test_gemini_prompt_block_reports_content_filter():
    """The sync decoder maps ``promptFeedback.blockReason`` to
    ``content_filter``; the stream decoder ignored it, so a safety block was
    reported as a mid-stream failure and cooled a healthy deployment
    (AUDIT_REPORT H8)."""
    from wiwi.providers.gemini_adapter import GeminiAdapter

    frame = json.dumps({"promptFeedback": {"blockReason": "SAFETY"},
                        "candidates": [{}]})
    out = GeminiAdapter().decode_stream_event("", frame)
    finishes = [d for d in out if isinstance(d, dl.Finish)]
    assert finishes and finishes[-1].stop_reason == "content_filter", (
        f"a blocked prompt must finish as content_filter, got {out}")
    assert any(isinstance(d, dl.StreamEnd) for d in out), (
        "the stream must terminate cleanly rather than be treated as truncated")


# ---------------------------------------------------------------------------
# H9 — expiring keys must stop authenticating
# ---------------------------------------------------------------------------

async def test_expire_keys_evicts_the_auth_cache(tmp_path):
    """``expire_keys`` updated the DB but not the cache, so a rotated
    credential kept authenticating for the 60 s TTL. Playground keys are minted
    with ``ttl_seconds`` and no budget, so they take exactly that cache branch
    (AUDIT_REPORT H9).

    The owner is a real ``users`` row: ``owner_id`` is only ever set from an
    authenticated user's id, and ``_lookup_db`` rejects a key whose owner row
    is missing (AUDIT #148, fail-closed). A synthetic owner string would make
    the warm-up ``authenticate`` below fail for that unrelated reason instead
    of exercising the cache branch this test guards.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from wiwi.auth.users import UserService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/h9.db")
    try:
        svc = AuthService(engine, "mk", 50)
        await svc.startup()
        owner = await UserService(engine, "mk").create_user("owner1", "password1")
        plaintext, _kid = await svc.create_key("playground", ttl_seconds=3600,
                                               owner_id=owner.id)
        await svc.create_key("playground", ttl_seconds=3600, owner_id=owner.id)

        assert await svc.authenticate(plaintext) is not None  # warm the cache
        expired = await svc.expire_keys(owner_id=owner.id, alias="playground",
                                        keep_newest=1)
        assert expired == 1

        assert await svc.authenticate(plaintext) is None, (
            "an expired key must stop authenticating immediately, not after the "
            "cache TTL lapses")
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# M3/M4 — admin input coercion and the Redis fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["false", "true", 1, 0, None, [], {}])
async def test_disable_key_rejects_non_bool(tmp_path, value):
    """``bool("false")`` is True, so a caller asking to *enable* a key silently
    revoked it (AUDIT_REPORT M3). Sibling routes already reject this class."""
    app = create_app(_config(f"{tmp_path}/m3.db"))
    async with LifespanManager(app), await _client(app) as c:
        kid = (await c.post("/admin/keys/generate", headers=AUTH,
                            json={"name": "k"})).json()["id"]
        r = await c.post(f"/admin/keys/{kid}/disable", headers=AUTH,
                         json={"disabled": value})
        assert r.status_code == 400, (
            f"disabled={value!r} must be a client error, got "
            f"{r.status_code} {r.text[:120]}")


async def test_disable_key_accepts_a_real_bool(tmp_path):
    """Control: the real shapes must keep working, both directions."""
    app = create_app(_config(f"{tmp_path}/m3ctl.db"))
    async with LifespanManager(app), await _client(app) as c:
        kid = (await c.post("/admin/keys/generate", headers=AUTH,
                            json={"name": "k"})).json()["id"]
        r = await c.post(f"/admin/keys/{kid}/disable", headers=AUTH,
                         json={"disabled": True})
        assert r.status_code == 200 and r.json()["disabled"] is True
        r = await c.post(f"/admin/keys/{kid}/disable", headers=AUTH,
                         json={"disabled": False})
        assert r.status_code == 200 and r.json()["disabled"] is False


def test_build_response_cache_falls_back_when_redis_is_unavailable(monkeypatch):
    """The ``except ImportError`` guard could never fire: ``RedisResponseCache``
    defers the redis import to its first use, so the Redis backend was selected
    even without the extra and then missed forever, silently
    (AUDIT_REPORT M4)."""
    import builtins

    from wiwi.cache import build_response_cache
    from wiwi.cache.response_cache import MemoryResponseCache

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "redis" or name.startswith("redis."):
            raise ImportError("redis extra not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    backend = build_response_cache(CacheSettings(enabled=True), "redis://localhost:6379/0")
    assert isinstance(backend, MemoryResponseCache), (
        f"without the redis extra the memory backend must be used, got "
        f"{type(backend).__name__}")


def test_build_response_cache_uses_redis_when_available():
    """Control: with the extra present the Redis backend is still selected."""
    pytest.importorskip("redis")
    from wiwi.cache import build_response_cache
    from wiwi.cache.redis_cache import RedisResponseCache

    backend = build_response_cache(CacheSettings(enabled=True), "redis://localhost:6379/0")
    assert isinstance(backend, RedisResponseCache)


# ---------------------------------------------------------------------------
# A mid-stream failover must not lose the real error, and the usage the client
# sees must equal what the gateway billed.
# ---------------------------------------------------------------------------

class _DyingStream(httpx.AsyncByteStream):
    """Emit one real SSE chunk, then kill the connection mid-body."""

    def __init__(self, first: bytes):
        self._first = first

    async def __aiter__(self):
        yield self._first
        raise httpx.ReadError("upstream died mid-body")

    async def aclose(self) -> None:
        return None


def _resume_config(db: str) -> WiwiConfig:
    """Two accounts in one group, mid-stream resume enabled."""
    return WiwiConfig(
        providers=[
            ProviderDef(name="primary", provider="openai",
                        base_url="https://p.test/v1",
                        keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="fallback", provider="openai",
                        base_url="https://f.test/v1",
                        keys=[KeyDef(label="b", key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="m",
                       wiwi_params=DeploymentParams(provider="primary", model="m")),
            ModelEntry(model_name="m",
                       wiwi_params=DeploymentParams(provider="fallback", model="m")),
        ],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=f"sqlite+aiosqlite:///{db}"),
        router_settings=RouterSettings(fallbacks={"m": ["m"]},
                                       stream_resume="enabled",
                                       stream_resume_max_retries=2),
    )


async def test_pump_cleanup_does_not_mask_a_pre_connect_error():
    """A failure raised before the upstream is open must reach the caller.

    ``_close_upstream`` used to be defined *after* the connect/status block, so
    any exception raised in that block hit the handler with the name unbound:
    the cleanup itself raised ``UnboundLocalError`` and destroyed the real
    cause, leaving only a generic 502 in the log.
    """
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.router.router import Router

    class Boom(RuntimeError):
        pass

    router = Router(_resume_config(":memory:"))
    gw = Gateway(router, CostEngine({}), drop_params=True)

    class ExplodingClient:
        """Raises only for the request itself, so teardown still works."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name == "stream":
                raise Boom("SENTINEL-REAL-CAUSE")
            return getattr(self._real, name)

    gw._client = ExplodingClient(gw._client)
    ctx = RequestContext(surface="chat", ir_req=ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])]))
    ctx.group = "m"
    err: BaseException | None = None
    try:
        async for _ in gw.stream(ctx):
            pass
    except BaseException as e:  # noqa: BLE001 - the contract is "raises"
        err = e
    finally:
        await gw.aclose()

    # A connect-phase failure propagates (it is retryable), so the assertion is
    # that the REAL cause survives — an UnboundLocalError from the cleanup
    # handler means the cleanup destroyed it.
    assert err is not None, "a connect-phase failure must not be swallowed"
    text = f"{type(err).__name__}: {err}"
    assert "SENTINEL-REAL-CAUSE" in text, (
        f"the real cause must survive the cleanup path, got {text!r}")
    assert not isinstance(err, UnboundLocalError), (
        f"the cleanup handler raised instead of releasing: {text!r}")


async def test_client_usage_matches_billed_usage_after_a_mid_stream_resume():
    """The usage block the client receives must equal the tokens billed.

    ``merge_resume_context`` sums the attempts, but it runs in the pump's
    ``finally`` — after the pump already yielded its own ``UsageFinal``. The
    client therefore saw only the last attempt's usage while spend and
    ``ctx.usage`` covered every attempt.
    """
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.router.router import Router

    def sse(*objs):
        return "".join(f"data: {o}\n\n" for o in objs) + "data: [DONE]\n\n"

    first = sse('{"choices":[{"index":0,"delta":{"content":"hello "}}]}')
    # The fallback reports usage for ITS OWN turn only (9/1).
    second = sse('{"choices":[{"index":0,"delta":{"content":"world"}}]}',
                 '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
                 '"usage":{"prompt_tokens":9,"completion_tokens":1}}')

    router = Router(_resume_config(":memory:"))
    gw = Gateway(router, CostEngine({}), drop_params=True)
    with respx.mock:
        respx.post("https://p.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                stream=_DyingStream(first.encode())))
        respx.post("https://f.test/v1/chat/completions").respond(
            text=second, headers={"content-type": "text/event-stream"})

        ctx = RequestContext(surface="chat", ir_req=ir.Request(
            model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])]))
        ctx.group = "m"
        seen: list[dl.UsageFinal] = []
        try:
            async for d in gw.stream(ctx):
                if isinstance(d, dl.UsageFinal):
                    seen.append(d)
        finally:
            await gw.aclose()

    assert len(seen) == 1, (
        f"the streaming contract promises exactly one UsageFinal, got {len(seen)}")
    billed = ctx.usage
    assert billed is not None, "a completed request must be priced"
    assert (seen[0].prompt, seen[0].output) == (billed.prompt_tokens,
                                                billed.completion_tokens), (
        "the client must see the same total that was billed: client saw "
        f"({seen[0].prompt}, {seen[0].output}), billed "
        f"({billed.prompt_tokens}, {billed.completion_tokens})")
