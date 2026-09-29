"""Round 106 — the session cookie's Secure flag behind a TLS-terminating proxy.

``_set_session_cookie`` derived ``secure`` from ``request.url.scheme`` alone.
Uvicorn only trusts ``X-Forwarded-Proto`` from ``forwarded_allow_ips`` (loopback
by default), so behind an *external* TLS terminator the ASGI scheme is http and
the cookie was set without ``Secure``: any same-host plain-HTTP path would then
carry the session cookie in cleartext.

The fix honours ``X-Forwarded-Proto: https`` only when the direct peer is in
``general_settings.trusted_proxies`` — the same gate ``_client_ip`` applies to
``X-Forwarded-For`` (AUDIT #73) — so an attacker cannot mint trust by header
injection.
"""

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-round104"


def _config(tmp_path, trusted: list[str] | None = None) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER,
            trusted_proxies=trusted or [],
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
        ),
    )


async def _master_login_cookie(tmp_path, trusted: list[str] | None,
                               headers: dict[str, str]) -> str:
    """POST /auth/login with the master key; return the raw Set-Cookie value."""
    app = create_app(_config(tmp_path, trusted))
    async with (LifespanManager(app),
                AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://t") as client):
            r = await client.post("/auth/login", json={"master_key": MASTER},
                                  headers=headers)
            assert r.status_code == 200, r.text
            cookies = r.headers.get_list("set-cookie")
            session = [c for c in cookies if c.startswith("wiwi_session=")]
            assert session, f"no wiwi_session cookie in {cookies}"
            return session[0]


def _has_attr(cookie: str, attr: str) -> bool:
    return any(part.strip().lower() == attr
               for part in cookie.split(";")[1:])


async def test_secure_flag_when_forwarded_proto_is_trusted(tmp_path):
    """X-Forwarded-Proto https from a trusted proxy → the cookie is Secure."""
    cookie = await _master_login_cookie(
        tmp_path, trusted=["127.0.0.1/32"],
        headers={"X-Forwarded-Proto": "https"})
    assert _has_attr(cookie, "secure"), cookie
    assert _has_attr(cookie, "httponly"), cookie


async def test_forwarded_proto_ignored_without_trusted_proxies(tmp_path):
    """No trusted_proxies configured → the header must not mint a Secure
    cookie (header injection from any peer must stay inert)."""
    cookie = await _master_login_cookie(
        tmp_path, trusted=[],
        headers={"X-Forwarded-Proto": "https"})
    assert not _has_attr(cookie, "secure"), cookie
    assert _has_attr(cookie, "httponly"), cookie


async def test_forwarded_proto_ignored_from_untrusted_peer(tmp_path):
    """A peer outside trusted_proxies cannot flip the flag even when the
    setting is configured for the real proxy."""
    cookie = await _master_login_cookie(
        tmp_path, trusted=["10.0.0.0/8"],
        headers={"X-Forwarded-Proto": "https"})
    assert not _has_attr(cookie, "secure"), cookie
