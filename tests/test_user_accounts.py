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
