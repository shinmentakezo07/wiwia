"""Round-21 regression tests: WorkBuddy import of a single auth object.

Regression target: the /console/workbuddy import dialog always nests the paste
under ``accounts`` (the frontend ``workbuddyImport`` builds
``{provider, accounts}`` unconditionally), and when the admin pastes a single
``{auth, account}`` object it sends ``accounts`` as a bare object — not a list.
The backend ``/admin/workbuddy/import`` only accepted a single object at the
flat top level (``{auth, account}``), so ``accounts: <object>`` was rejected
with "…(or a single auth object)" despite the error message promising a single
object was fine. Fix: accept a single auth object for ``accounts`` too, matching
the docstring/error text.
"""

from __future__ import annotations

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

AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p2", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p2",
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


def _auth_obj(uid: str, nickname: str) -> dict:
    """An auth JSON in the workbuddy2api auths/ file shape."""
    return {
        "auth": {
            "accessToken": f"at-{uid}",
            "refreshToken": f"rt-{uid}",
            "expiresAt": int(time.time() + 24 * 3600),
            "domain": "https://www.workbuddy.ai",
        },
        "account": {
            "uid": uid,
            "enterpriseId": "",
            "nickname": nickname,
        },
    }


async def test_import_single_auth_object_under_accounts(client):
    """A single {auth, account} object nested under ``accounts`` (how the
    /console/workbuddy dialog sends a one-file paste) must import, not 400."""
    single = _auth_obj("u1", "sole")
    r = await client.post("/admin/workbuddy/import", headers=AUTH,
                          json={"provider": "wb-accts", "accounts": single})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    assert r.json()["labels"] == ["sole"]

    accounts = (await client.get("/admin/workbuddy/accounts",
                                 headers=AUTH)).json()["accounts"]
    assert [a["uid"] for a in accounts] == ["u1"]


async def test_import_single_auth_object_under_accounts_provider_defaulted(client):
    """The same single-object-under-``accounts`` shape still imports when the
    provider is omitted (defaults to workbuddy-main)."""
    single = _auth_obj("u2", "kolo")
    r = await client.post("/admin/workbuddy/import", headers=AUTH,
                          json={"accounts": single})
    assert r.status_code == 200, r.text
    assert r.json()["labels"] == ["kolo"]
