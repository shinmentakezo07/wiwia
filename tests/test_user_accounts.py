"""User accounts + sessions + role-based scoping regression tests."""

import asyncio

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.auth.users import (
    UserService,
    hash_password,
    sign_session,
    verify_password,
    verify_session,
)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.execute(sa.text("PRAGMA foreign_keys=ON"))
    return eng


async def test_password_hash_roundtrip():
    h = hash_password("hunter2long")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("hunter2long", h) is True
    assert verify_password("wrong", h) is False


async def test_session_sign_verify_roundtrip():
    secret = "s3cret"
    tok = sign_session(secret, "u1234567", "user", expires=4_000_000_000.0)
    assert verify_session(secret, tok) == ("u1234567", "user", 4_000_000_000.0)


async def test_session_rejects_tamper():
    secret = "s3cret"
    tok = sign_session(secret, "u1", "user", expires=2_000_000_000.0)
    tampered = tok[:-2] + ("00" if tok[-2:] != "00" else "11")
    assert verify_session(secret, tampered) is None


async def test_session_rejects_expired():
    secret = "s3cret"
    tok = sign_session(secret, "u1", "user", expires=1.0)
    assert verify_session(secret, tok) is None


async def test_create_user_stores_and_verifies():
    eng = await _engine()
    svc = UserService(eng, "secret")
    await svc.startup()
    u = await svc.create_user("alice", "password1")
    assert u.role == "user"
    assert u.disabled is False
    again = await svc.verify("alice", "password1")
    assert again is not None and again.id == u.id
    assert await svc.verify("alice", "nope") is None


async def test_create_user_duplicate_409():
    eng = await _engine()
    svc = UserService(eng, "secret")
    await svc.startup()
    await svc.create_user("bob", "password1")
    try:
        await svc.create_user("bob", "password1")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


async def test_list_and_patch_user():
    eng = await _engine()
    svc = UserService(eng, "secret")
    await svc.startup()
    u = await svc.create_user("carol", "password1")
    users = await svc.list_users()
    assert any(x["id"] == u.id for x in users)
    patched = await svc.patch(u.id, role="admin")
    assert patched["role"] == "admin"
    info = await svc.get(u.id)
    assert info.role == "admin"


from wiwi.auth.service import AuthService


async def _auth_svc():
    eng = await _engine()
    svc = AuthService(eng, "master-key-plaintext")
    await svc.startup()
    return svc


async def test_create_key_stamps_owner():
    svc = await _auth_svc()
    plaintext, kid = await svc.create_key("alice-key", owner_id="u1")
    assert plaintext.startswith("sk-wiwi-")
    assert await svc.key_owner(kid) == "u1"


async def test_create_key_no_owner_is_admin():
    svc = await _auth_svc()
    _, kid = await svc.create_key("system-key")
    assert await svc.key_owner(kid) is None


async def test_list_keys_for_owner_filters():
    svc = await _auth_svc()
    _, k1 = await svc.create_key("a1", owner_id="u1")
    _, k2 = await svc.create_key("b1", owner_id="u2")
    _, k3 = await svc.create_key("sys")
    own1 = [k["id"] for k in await svc.list_keys_for_owner("u1")]
    assert own1 == [k1]
    all_ids = [k["id"] for k in await svc.list_keys()]
    assert {k1, k2, k3} <= set(all_ids)


import time

from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent


async def _sink():
    eng = await _engine()
    sink = DBSink(eng)
    await sink.startup()
    return eng, sink


def _req_event(key_id: str, cost: float = 0.01, ts: float | None = None) -> LogEvent:
    return LogEvent(stream="request", ts=ts if ts else time.time(),
                    key_id=key_id, cost=cost, tok_in=10, tok_out=5,
                    status=200, model_group="gpt-4o")


async def test_request_log_carries_key_id_and_filters():
    _eng, sink = await _sink()
    await sink.write_requests([_req_event("k1", 0.10), _req_event("k2", 0.20)])
    all_logs = await sink.read_requests(100)
    assert {l["key_id"] for l in all_logs} == {"k1", "k2"}
    only_k1 = await sink.read_requests(100, key_ids=["k1"])
    assert {l["key_id"] for l in only_k1} == {"k1"}
    ov_all = await sink.read_overview(0)
    assert ov_all["requests"] == 2
    ov_k2 = await sink.read_overview(0, key_ids=["k2"])
    assert ov_k2["requests"] == 1
    assert round(ov_k2["cost"], 2) == 0.20


import httpx
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.server.app import create_app_from_config_path


async def _app_for_config(tmp_path, config_yaml: str):
    # Isolate the DB per test: point database_url at a fresh file inside tmp_path
    # so usernames/keys never collide across tests or runs. Inject the line under
    # general_settings (right after master_key) without mutating the caller's
    # config_yaml — _CONFIG is shared and must stay constant.
    db_url = f"sqlite+aiosqlite:///{tmp_path}/app.db"
    if "database_url:" in config_yaml:
        patched = config_yaml
    else:
        patched = config_yaml.replace(
            "  master_key: sk-master-test-123\n",
            f"  master_key: sk-master-test-123\n  database_url: {db_url}\n",
            1,
        )
    cfg_path = tmp_path / "wiwi.yaml"
    cfg_path.write_text(patched)
    return create_app_from_config_path(str(cfg_path))


_CONFIG = """
general_settings:
  master_key: sk-master-test-123
providers:
  - name: openai
    provider: openai
    keys: [{label: main, key: sk-upstream-fake}]
model_list:
  - model_name: gpt-4o
    wiwi_params:
      provider: openai
      model: gpt-4o
"""


async def _client_for_config(tmp_path, config_yaml: str) -> AsyncClient:
    """Build an ASGI-backed client with the app lifespan started.

    The returned client's ``aclose`` also stops the lifespan, so callers
    just do ``await client.aclose()`` at the end of the test.
    """
    app = await _app_for_config(tmp_path, config_yaml)
    lm = LifespanManager(app)
    await lm.__aenter__()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    # Arrange for lifespan teardown to run when the client closes.
    _orig_close = client.aclose

    async def _close_then_lifespan():
        try:
            await _orig_close()
        finally:
            await lm.__aexit__(None, None, None)

    client.aclose = _close_then_lifespan  # type: ignore[method-assign]
    return client


@respx.mock
async def test_request_logs_key_id_for_virtual_key(tmp_path):
    # Create a virtual key via master, then make a chat call with it.
    app = await _app_for_config(tmp_path, _CONFIG)
    async with LifespanManager(app):  # noqa: SIM117
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as client:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={
                    "id": "chatcmpl-1", "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant",
                      "content": "hi"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                }))
            r = await client.post("/admin/keys/generate", json={"name": "test"},
                                  headers={"Authorization": "Bearer sk-master-test-123"})
            vkey = r.json()["key"]
            r2 = await client.post("/v1/chat/completions",
                                   json={"model": "gpt-4o",
                                         "messages": [{"role": "user", "content": "hi"}]},
                                   headers={"Authorization": f"Bearer {vkey}"})
            assert r2.status_code == 200
            logs = []
            for _ in range(50):
                logs = (await client.get("/admin/logs/requests",
                         headers={"Authorization": "Bearer sk-master-test-123"})).json()["logs"]
                if any(l.get("key_id") for l in logs):
                    break
                await asyncio.sleep(0.05)
            assert any(l["key_id"] and l["key_id"].startswith("k") for l in logs), \
                "no request log with key_id was flushed"


# -- Task 5: current_user resolution ------------------------------------------


async def test_master_key_resolves_to_admin_via_bearer(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    me = (await client.get("/auth/me",
            headers={"Authorization": "Bearer sk-master-test-123"})).json()
    assert me["user"]["role"] == "admin"
    assert me["user"]["id"] == "master"
    await client.aclose()


async def test_auth_me_null_when_anonymous(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    me = (await client.get("/auth/me")).json()
    assert me["user"] is None
    await client.aclose()


# -- Task 6: /auth/* endpoints -------------------------------------------------


async def test_signup_sets_cookie_and_creates_user(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/signup", json={"username": "dave",
                                                "password": "password1"})
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "user"
    assert "wiwi_session" in r.cookies
    me = (await client.get("/auth/me")).json()
    assert me["user"]["username"] == "dave"
    await client.aclose()


async def test_signup_duplicate_409(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "eve",
                                            "password": "password1"})
    r = await client.post("/auth/signup", json={"username": "eve",
                                                "password": "password2"})
    assert r.status_code == 409
    await client.aclose()


async def test_signup_short_password_400(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/signup", json={"username": "frank",
                                                "password": "short"})
    assert r.status_code == 400
    await client.aclose()


async def test_login_user_success(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "grace",
                                            "password": "password1"})
    r = await client.post("/auth/login", json={"username": "grace",
                                               "password": "password1"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "user"
    await client.aclose()


async def test_login_user_wrong_password_401(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "heidi",
                                            "password": "password1"})
    r = await client.post("/auth/login", json={"username": "heidi",
                                               "password": "nope"})
    assert r.status_code == 401
    await client.aclose()


async def test_login_master_key_sets_admin(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    assert r.status_code == 200
    assert r.json()["user"] == {"id": "master", "username": "master",
                                "role": "admin"}
    await client.aclose()


async def test_logout_clears_cookie(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "ivan",
                                            "password": "password1"})
    r = await client.post("/auth/logout")
    assert r.status_code == 200
    me = (await client.get("/auth/me")).json()
    assert me["user"] is None
    await client.aclose()


async def test_session_cookie_tamper_rejected(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "judy",
                                            "password": "password1"})
    # Tamper the cookie.
    tok = client.cookies.get("wiwi_session")
    client.cookies.clear()
    client.cookies.set("wiwi_session", tok[:-4] + "0000",
                       domain="t", path="/")
    me = (await client.get("/auth/me")).json()
    assert me["user"] is None
    await client.aclose()


async def test_disabled_user_session_rejected(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/signup", json={"username": "karl",
                                                "password": "password1"})
    uid = r.json()["user"]["id"]
    await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    await client.patch(f"/admin/users/{uid}", json={"disabled": True})
    # New client with karl's old cookie: simulate by re-logging in as karl first
    await client.post("/auth/logout")
    await client.post("/auth/login", json={"username": "karl",
                                           "password": "password1"})
    # disabled now → me should be null
    me = (await client.get("/auth/me")).json()
    assert me["user"] is None
    await client.aclose()


# -- Task 7: /admin/users endpoints -------------------------------------------


async def test_admin_lists_users(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "leo", "password": "password1"})
    await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    r = await client.get("/admin/users")
    assert r.status_code == 200
    assert any(u["username"] == "leo" for u in r.json()["users"])
    await client.aclose()


async def test_promote_user_to_admin(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    r = await client.post("/auth/signup", json={"username": "mia", "password": "password1"})
    uid = r.json()["user"]["id"]
    await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    r2 = await client.patch(f"/admin/users/{uid}", json={"role": "admin"})
    assert r2.status_code == 200
    assert r2.json()["role"] == "admin"
    await client.aclose()


async def test_cannot_demote_last_admin_400(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/login", json={"master_key": "sk-master-test-123"})
    # promote one user to admin, then try to demote them (only admin)
    r = await client.post("/auth/signup", json={"username": "nia", "password": "password1"})
    uid = r.json()["user"]["id"]
    await client.patch(f"/admin/users/{uid}", json={"role": "admin"})
    r2 = await client.patch(f"/admin/users/{uid}", json={"role": "user"})
    assert r2.status_code == 400
    await client.aclose()


async def test_user_cannot_access_admin_users_403(tmp_path):
    client = await _client_for_config(tmp_path, _CONFIG)
    await client.post("/auth/signup", json={"username": "oscar", "password": "password1"})
    r = await client.get("/admin/users")
    assert r.status_code == 403
    await client.aclose()
