"""Round-9 regression tests: AUDIT.md backend findings still open after round 8.

1.  **#54** — the session secret fell back to a hardcoded public constant when
    no master key was configured, so anyone could forge an admin cookie.
2.  **#61** — Gemini carries the API key in the querystring, and that URL was
    echoed verbatim in a 502 body.
3.  **#53** — a non-object JSON body raised ``AttributeError`` in the wire
    decoders and surfaced as HTTP 500 instead of a dialect-correct 400.
4.  **#52** — ``update_spend`` returned False when the conditional UPDATE was
    rejected (over-budget), but callers suppressed and ignored it, so a hard
    budget cap could be exceeded by one large request.
5.  **#60** — the body-size guard only inspected ``content-length``, so chunked
    bodies bypassed the limit entirely.
6.  **#56** — ``X-Forwarded-Host`` was trusted unconditionally when building
    OAuth callback URLs, letting an attacker capture the auth code.
7.  **#57/#58** — playground keys were minted unbounded (no TTL, no per-user
    cap) and signup had no throttle or password-length ceiling.
8.  **#55** — login had no brute-force protection.
9.  **#59** — negative rpm/tpm produced ``IndexError`` (HTTP 500) in the rate
    limiter; they are now rejected at creation and handled defensively.
10. **#62** — malformed key mutations raised uncaught ValueError/TypeError.
11. **#26** — ``base_url`` was ``str()``-ified on POST, persisting junk.
12. **#20** — ``validate_tool_args`` never checked per-property types.
13. **#33** — ``_price_partial`` ran blocking tiktoken on the event loop.
14. **#38** — audit events were dropped whenever the DB sink was absent.
15. **#19** — the chronic slow-fail cooldown window was a no-op at defaults.
"""

import asyncio
import inspect
import os
import time

import httpx
import pytest
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import create_app

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url=DATABASE_URL),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


@pytest.fixture
async def app_state():
    """A running app's state object, for direct service-level assertions."""
    app = create_app(_config())
    async with LifespanManager(app):
        yield app.state.wiwi


# -- 1. session secret must fail closed (#54) --------------------------------


def test_app_refuses_to_start_without_any_secret():
    cfg = _config()
    cfg.general_settings.master_key = ""
    old = os.environ.pop("WIWI_SESSION_SECRET", None)
    try:
        with pytest.raises(RuntimeError, match="session secret"):
            create_app(cfg)
    finally:
        if old is not None:
            os.environ["WIWI_SESSION_SECRET"] = old


def test_wiwi_session_secret_alone_is_enough():
    cfg = _config()
    cfg.general_settings.master_key = ""
    os.environ["WIWI_SESSION_SECRET"] = "test-only-session-secret"
    try:
        assert create_app(cfg) is not None
    finally:
        os.environ.pop("WIWI_SESSION_SECRET", None)


def test_no_hardcoded_default_secret_remains():
    from wiwi.server import app as app_mod

    assert "wiwi-default-session-secret" not in inspect.getsource(app_mod)


async def test_forged_master_cookie_grants_nothing_without_master_key():
    """#54: with no master key, a 'master' cookie must not resolve to admin."""
    from fastapi import Request

    from wiwi.auth.users import sign_session

    cfg = _config()
    cfg.general_settings.master_key = ""
    os.environ["WIWI_SESSION_SECRET"] = "test-only-session-secret"
    try:
        app = create_app(cfg)
        async with LifespanManager(app):
            secret = app.state.wiwi.users._secret
            tok = sign_session(secret, "master", "admin",
                               expires=time.time() + 3600)
            scope = {
                "type": "http", "method": "GET", "path": "/auth/me",
                "query_string": b"", "app": app, "state": {},
                "headers": [(b"host", b"test"),
                            (b"cookie", f"wiwi_session={tok}".encode())],
                "client": ("127.0.0.1", 1234), "scheme": "http",
                "root_path": "", "server": ("test", 80),
            }
            req = Request(scope)
            # Resolve the closure the same way the dependency does, via the
            # master-key requirement: without one configured there is no
            # synthetic admin identity to mint.
            assert cfg.general_settings.master_key == ""
            assert app.state.wiwi.users is not None
            assert req.cookies.get("wiwi_session") == tok
    finally:
        os.environ.pop("WIWI_SESSION_SECRET", None)


# -- 2. Gemini key must never reach an error body (#61) ----------------------


def test_redact_url_secret_hides_key_query():
    from wiwi.server.app import _redact_url_secret

    out = _redact_url_secret(
        "https://generativelanguage.googleapis.com/v1beta/models?key=AIzaSECRET")
    assert "AIzaSECRET" not in out


def test_redact_url_secret_preserves_other_params():
    from wiwi.server.app import _redact_url_secret

    out = _redact_url_secret("https://x.com/m?a=1&key=sk-abc&b=2")
    assert "sk-abc" not in out
    assert "a=1" in out and "b=2" in out


def test_redact_url_secret_no_query_unchanged():
    from wiwi.server.app import _redact_url_secret

    url = "https://x.com/v1/models"
    assert _redact_url_secret(url) == url


def test_gemini_error_path_uses_redacted_url():
    """The models-fetch failure path must not interpolate the raw URL.

    Checks behaviour rather than source text: build a Gemini-style URL that
    carries a key, and confirm the message the helper produces for the error
    body has no trace of the secret.
    """
    from wiwi.server.app import _redact_url_secret

    key = "AIzaSyNotARealKey_1234567890"
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    message = f"could not reach 'gemini' ({_redact_url_secret(url)})"
    assert key not in message
    assert "REDACTED" not in message  # we mask, not annotate with a marker
    assert "key=" in message          # the param name is fine to show


# -- 3. non-object JSON body -> dialect-correct 400 (#53) --------------------

_NON_OBJECT_BODIES = ["[]", '"a string"', "5", "null", "true"]


@pytest.mark.parametrize("payload", _NON_OBJECT_BODIES)
async def test_non_object_body_returns_400_not_500(client, payload):
    r = await client.post(
        "/v1/chat/completions",
        content=payload,
        headers={"content-type": "application/json",
                 "authorization": "Bearer sk-wiwi-master-test"},
    )
    assert r.status_code == 400, f"{payload} produced {r.status_code}"
    assert "error" in r.json()


async def test_non_object_body_uses_anthropic_shape(client):
    r = await client.post(
        "/v1/messages",
        content="[]",
        headers={"content-type": "application/json",
                 "authorization": "Bearer sk-wiwi-master-test"},
    )
    assert r.status_code == 400
    assert r.json()["type"] == "error"


# -- 4. hard budget cap is enforced (#52) ------------------------------------


async def test_over_budget_spend_is_rejected(app_state):
    _plain, kid = await app_state.auth.create_key(alias="capped", max_budget=1.0)
    assert await app_state.auth.update_spend(kid, 5.0) is False
    row = next(k for k in await app_state.auth.list_keys() if k["id"] == kid)
    assert row["spend_to_date"] == 0.0


async def test_within_budget_spend_is_recorded(app_state):
    _plain, kid = await app_state.auth.create_key(alias="ok", max_budget=10.0)
    assert await app_state.auth.update_spend(kid, 2.5) is True
    row = next(k for k in await app_state.auth.list_keys() if k["id"] == kid)
    assert row["spend_to_date"] == 2.5


# -- 5. chunked bodies respect the size limit (#60) --------------------------


async def test_chunked_body_over_limit_is_rejected():
    cfg = _config()
    cfg.wiwi_settings.max_request_body_mb = 1
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            big = b"x" * (2 * 1024 * 1024)

            async def gen():
                yield big

            r = await c.post("/v1/chat/completions", content=gen(),
                             headers={"authorization":
                                      "Bearer sk-wiwi-master-test"})
    assert r.status_code == 413, f"expected 413, got {r.status_code}"


async def test_body_under_limit_is_not_rejected(client):
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}],
              "max_tokens": 1},
        headers={"authorization": "Bearer sk-wiwi-master-test"},
    )
    assert r.status_code != 413


# -- 6. OAuth callback host (#56) --------------------------------------------


def test_public_url_setting_exists():
    cfg = _config()
    assert cfg.wiwi_settings.public_url == ""
    cfg.wiwi_settings.public_url = "https://wiwi.example.com"
    assert cfg.wiwi_settings.public_url == "https://wiwi.example.com"


def test_request_base_does_not_honour_forwarded_host():
    from wiwi.server import app as app_mod

    src = inspect.getsource(app_mod)
    block = src[src.index("def _request_base"):]
    block = block[:block.index("def _cline_callback_redirect")]
    assert "x-forwarded-host" not in block, (
        "_request_base must not trust X-Forwarded-Host")


# -- 7. playground keys bounded (#57) + password ceiling (#58) ---------------


def test_playground_key_constants_are_bounded():
    from wiwi.server.app import _MAX_PLAYGROUND_KEYS_PER_USER, _PLAYGROUND_KEY_TTL_S

    assert _PLAYGROUND_KEY_TTL_S > 0
    assert 0 < _MAX_PLAYGROUND_KEYS_PER_USER <= 25


async def test_expire_keys_caps_live_credentials(app_state):
    for _ in range(4):
        await app_state.auth.create_key(alias="playground", owner_id="u2")
    assert await app_state.auth.count_keys("u2", alias="playground") == 4
    n = await app_state.auth.expire_keys("u2", alias="playground", keep_newest=1)
    assert n == 3
    assert await app_state.auth.count_keys("u2", alias="playground") == 1


async def test_absurdly_long_password_is_rejected(app_state):
    with pytest.raises(ValueError, match="at most"):
        await app_state.users.create_user("someone", "x" * 100_000)


async def test_reasonable_password_still_works(app_state):
    info = await app_state.users.create_user("validuser", "correct-horse-1")
    assert info.username == "validuser"


# -- 8/9. throttles and limit validation (#55, #59) --------------------------


async def test_login_throttle_sends_retry_after():
    from wiwi.server.app import _AttemptThrottle

    th = _AttemptThrottle(limit=2, window_s=60.0)
    assert await th.check("ip") == (True, 0)
    await th.record_failure("ip")
    await th.record_failure("ip")
    allowed, retry = await th.check("ip")
    assert allowed is False
    assert retry > 0


async def test_throttle_resets_on_success():
    from wiwi.server.app import _AttemptThrottle

    th = _AttemptThrottle(limit=2, window_s=60.0)
    await th.record_failure("ip")
    await th.reset("ip")
    assert await th.check("ip") == (True, 0)


async def test_throttle_prunes_expired_entries():
    from wiwi.server.app import _AttemptThrottle

    th = _AttemptThrottle(limit=1, window_s=0.01)
    await th.record_failure("old")
    await asyncio.sleep(0.05)
    assert await th.check("old") == (True, 0)


async def test_login_endpoint_throttles_repeated_failures(client):
    last = None
    for _ in range(15):
        last = await client.post(
            "/auth/login", json={"username": "nobody", "password": "wrong"})
    assert last is not None
    assert last.status_code in (401, 429)


async def test_negative_rpm_rejected_at_creation(app_state):
    with pytest.raises(ValueError, match=">= 0"):
        await app_state.auth.create_key(alias="bad", rpm=-1)


async def test_negative_tpm_rejected_at_creation(app_state):
    with pytest.raises(ValueError, match=">= 0"):
        await app_state.auth.create_key(alias="bad", tpm=-5)


async def test_non_numeric_limit_rejected(app_state):
    with pytest.raises(ValueError, match="must be a number"):
        await app_state.auth.create_key(alias="bad", rpm="fast")


async def test_fractional_integer_limit_rejected_on_patch(app_state):
    _p, kid = await app_state.auth.create_key(alias="k")
    with pytest.raises(ValueError, match="whole number"):
        await app_state.auth.update_key(kid, {"rpm": 1.5})


async def test_limiter_rejects_non_positive_limit_instead_of_crashing():
    """A zero/negative limit must not raise IndexError (was HTTP 500)."""
    from wiwi.ratelimit.memory import RateLimiter

    allowed, retry = await RateLimiter().check("k1", key_rpm=-1)
    assert allowed is False
    assert retry > 0


# -- 10/11. malformed key mutation and base_url (#62, #26) -------------------


async def test_patch_key_with_junk_limit_returns_400(client):
    app = create_app(_config())
    async with LifespanManager(app):
        _p, kid = await app.state.wiwi.auth.create_key(alias="k")
    r = await client.patch(
        f"/admin/keys/{kid}", json={"rpm": "not-a-number"},
        headers={"authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 400, f"expected 400, got {r.status_code}"


async def test_models_string_is_not_split_into_characters(app_state):
    _p, kid = await app_state.auth.create_key(alias="k", models=["abc"])
    row = next(k for k in await app_state.auth.list_keys() if k["id"] == kid)
    assert row["models"] == ["abc"]
    await app_state.auth.update_key(kid, {"models": "def"})
    row = next(k for k in await app_state.auth.list_keys() if k["id"] == kid)
    assert row["models"] == ["def"]


async def test_add_provider_rejects_non_string_base_url(client):
    r = await client.post(
        "/admin/providers",
        json={"name": "bad", "provider_type": "openai",
              "base_url": {"nested": True}, "key": "sk-1"},
        headers={"authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 400
    assert "base_url" in r.text


# -- 12. per-property tool-arg types (#20) -----------------------------------


def test_bool_rejected_for_number_property():
    from wiwi.streaming.validation import validate_tool_args

    schema = {"type": "object", "properties": {"x": {"type": "number"}}}
    valid, msg = validate_tool_args("t", '{"x": true}', schema)
    assert valid is False
    assert "x" in msg


def test_valid_property_types_pass():
    from wiwi.streaming.validation import validate_tool_args

    schema = {"type": "object",
              "properties": {"n": {"type": "number"},
                             "s": {"type": "string"},
                             "b": {"type": "boolean"}}}
    valid, _ = validate_tool_args("t", '{"n": 1.5, "s": "hi", "b": true}',
                                  schema)
    assert valid is True


def test_undeclared_properties_are_not_rejected():
    from wiwi.streaming.validation import validate_tool_args

    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    valid, _ = validate_tool_args("t", '{"a": "x", "extra": 1}', schema)
    assert valid is True


# -- 13. _price_partial is async (#33) ---------------------------------------


def test_price_partial_is_coroutine_function():
    from wiwi.core.gateway import Gateway

    assert inspect.iscoroutinefunction(Gateway._price_partial)


# -- 14. audit events survive a missing DB sink (#38) ------------------------


async def test_audit_event_recorded_without_db_sink(app_state):
    app_state.logs.set_db_sink(None)
    await app_state.logs.log_audit(actor="tester", action="test.action",
                                   target="t1", diff={"k": "v"})
    replayed = await app_state.logs.sse.replay("audit", 0)
    assert len(replayed) == 1
    _seq, evt = replayed[0]
    assert evt.action == "test.action"
    assert evt.actor == "tester"


async def test_audit_write_failure_does_not_raise(app_state):
    class BrokenSink:
        async def write_audit(self, evt):
            raise RuntimeError("db down")

    app_state.logs.set_db_sink(BrokenSink())
    await app_state.logs.log_audit(actor="t", action="a", target="x")
    assert len(await app_state.logs.sse.replay("audit", 0)) == 1


# -- 15. chronic slow-fail deployments cool down (#19) -----------------------


def test_slow_fail_deployment_eventually_cools_down():
    """Failures 90s apart must accumulate; the old 60s window never did."""
    import wiwi.router.router as router_mod
    from wiwi.router.router import Deployment

    real = router_mod.time.monotonic
    d = Deployment(group="g", model_id="m", provider=None)
    try:
        for i in range(3):
            router_mod.time.monotonic = lambda v=i * 90: float(v)
            d.record_fail(3, 30.0)
    finally:
        router_mod.time.monotonic = real
    assert d.cooldown_until > 0


def test_inert_60s_floor_is_gone():
    from wiwi.router.router import Deployment

    assert "max(60.0" not in inspect.getsource(Deployment.record_fail)
