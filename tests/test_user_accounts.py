"""User accounts + sessions + role-based scoping regression tests."""


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
    cfg_path = tmp_path / "wiwi.yaml"
    cfg_path.write_text(config_yaml)
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
            logs = (await client.get("/admin/logs/requests",
                     headers={"Authorization": "Bearer sk-master-test-123"})).json()["logs"]
            assert any(l["key_id"] and l["key_id"].startswith("k") for l in logs)
