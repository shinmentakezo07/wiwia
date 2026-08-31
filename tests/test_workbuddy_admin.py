"""WorkBuddy admin surface tests: account listing, auths/ JSON import and
export round-trip, and on-demand refresh through the ASGI app."""

from __future__ import annotations

import json
import time

import httpx
import pytest
import respx
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

AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


def _auth_obj(uid: str, nickname: str, *, hours: float = 24.0) -> dict:
    """An auth JSON in the workbuddy2api auths/ file shape."""
    return {
        "auth": {
            "accessToken": f"at-{uid}",
            "refreshToken": f"rt-{uid}",
            "expiresAt": int(time.time() + hours * 3600),
            "domain": "https://www.workbuddy.ai",
    },
        "account": {
            "uid": uid,
            "enterpriseId": "",
            "nickname": nickname,
        },
    }


async def _import(client: httpx.AsyncClient, accounts: list[dict],
                  provider: str = "workbuddy-main") -> httpx.Response:
    return await client.post("/admin/workbuddy/import", headers=AUTH,
                             json={"provider": provider, "accounts": accounts})


async def test_import_requires_master_key(client):
    r = await client.get("/admin/workbuddy/accounts")
    assert r.status_code == 401
    r = await client.post("/admin/workbuddy/import", json={"accounts": []})
    assert r.status_code == 401


async def test_import_creates_provider_and_keys(client):
    r = await _import(client, [_auth_obj("u1", "alice"),
                               _auth_obj("u2", "bob")])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["imported"] == 2
    assert data["labels"] == ["alice", "bob"]

    providers = (await client.get("/admin/providers", headers=AUTH)).json()
    wb = next(p for p in providers["providers"] if p["name"] == "workbuddy-main")
    assert wb["provider_type"] == "workbuddy"
    assert {k["label"] for k in wb["keys"]} == {"alice", "bob"}


async def test_import_single_auth_object_form(client):
    single = _auth_obj("u3", "carol")
    r = await client.post("/admin/workbuddy/import", headers=AUTH,
                          json={"provider": "wb-single", **single})
    assert r.status_code == 200, r.text
    assert r.json()["labels"] == ["carol"]


async def test_import_rejects_invalid_auth_atomically(client):
    bad = _auth_obj("u4", "dave")
    del bad["auth"]["accessToken"]
    r = await _import(client, [_auth_obj("u5", "erin"), bad])
    assert r.status_code == 400
    providers = (await client.get("/admin/providers", headers=AUTH)).json()
    assert all(p["name"] != "workbuddy-main" for p in providers["providers"])


async def test_import_duplicate_label_rejected_atomically(client):
    assert (await _import(client, [_auth_obj("u6", "frank")])).status_code == 200
    r = await _import(client, [_auth_obj("u7", "newguy"),
                               _auth_obj("u8", "frank")])
    assert r.status_code == 409
    providers = (await client.get("/admin/providers", headers=AUTH)).json()
    wb = next(p for p in providers["providers"] if p["name"] == "workbuddy-main")
    assert {k["label"] for k in wb["keys"]} == {"frank"}


async def test_import_rejects_wrong_existing_type(client):
    r = await _import(client, [_auth_obj("u9", "gina")], provider="p1")
    assert r.status_code == 409


async def test_import_appends_to_existing_provider(client):
    assert (await _import(client, [_auth_obj("u10", "henry")])).status_code == 200
    r = await _import(client, [_auth_obj("u11", "iris")])
    assert r.status_code == 200
    accounts = (await client.get("/admin/workbuddy/accounts",
                                 headers=AUTH)).json()["accounts"]
    labels = {a["label"] for a in accounts}
    assert labels == {"henry", "iris"}


async def test_accounts_listing_carries_expiry_state(client):
    await _import(client, [_auth_obj("u12", "jack", hours=1),
                           _auth_obj("u13", "kate", hours=48)])
    accounts = (await client.get("/admin/workbuddy/accounts",
                                 headers=AUTH)).json()["accounts"]
    by_label = {a["label"]: a for a in accounts}
    assert by_label["jack"]["region"] == "global"
    assert by_label["jack"]["valid_auth"] is True
    assert by_label["jack"]["uid"] == "u12"
    assert by_label["kate"]["needs_refresh"] is False


@respx.mock
async def test_export_roundtrips_auths_shape(client):
    original = [_auth_obj("u14", "lena"), _auth_obj("u15", "mona")]
    await _import(client, original)
    r = await client.get("/admin/workbuddy/export", headers=AUTH)
    assert r.status_code == 200, r.text
    exported = r.json()["accounts"]
    assert len(exported) == 2
    by_uid = {e["account"]["uid"]: e for e in exported}
    assert by_uid["u14"]["auth"]["accessToken"] == "at-u14"
    assert by_uid["u14"]["auth"]["refreshToken"] == "rt-u14"
    assert by_uid["u14"]["auth"]["domain"] == "https://www.workbuddy.ai"
    assert by_uid["u14"]["account"]["nickname"] == "lena"
    r2 = await client.post("/admin/workbuddy/import", headers=AUTH,
                           json={"provider": "wb-copy", "accounts": exported})
    assert r2.status_code == 200, r2.text
    assert r2.json()["imported"] == 2


async def test_export_scopes_by_provider(client):
    await _import(client, [_auth_obj("u16", "noah")], provider="wb-a")
    await _import(client, [_auth_obj("u17", "olga")], provider="wb-b")
    scoped = (await client.get("/admin/workbuddy/export?provider=wb-a",
                               headers=AUTH)).json()["accounts"]
    assert [e["account"]["uid"] for e in scoped] == ["u16"]


@respx.mock
async def test_refresh_rotates_secret(client):
    await _import(client, [_auth_obj("u18", "pete")])
    respx.post("https://www.workbuddy.ai/v2/plugin/auth/token/refresh").respond(
        json={"code": 0, "msg": "ok", "data": {
            "accessToken": "at-rotated", "refreshToken": "rt-rotated",
            "expiresIn": 7200, "domain": "https://www.workbuddy.ai"}})
    r = await client.post("/admin/workbuddy/refresh", headers=AUTH,
                          json={"provider": "workbuddy-main", "label": "pete"})
    assert r.status_code == 200, r.text
    assert r.json()["refreshed"] is True
    reveal = await client.get(
        "/admin/providers/workbuddy-main/keys/pete/secret", headers=AUTH)
    secret = json.loads(reveal.json()["secret"])
    assert secret["auth"]["accessToken"] == "at-rotated"
    assert secret["auth"]["refreshToken"] == "rt-rotated"
    assert secret["account"]["uid"] == "u18"


async def test_refresh_unknown_key_is_502(client):
    await _import(client, [_auth_obj("u19", "quinn")])
    r = await client.post("/admin/workbuddy/refresh", headers=AUTH,
                          json={"provider": "workbuddy-main", "label": "ghost"})
    assert r.status_code == 502


async def test_import_empty_accounts_rejected(client):
    r = await _import(client, [])
    assert r.status_code == 400
