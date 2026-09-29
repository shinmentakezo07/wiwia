"""Round 109 — ``trusted_proxies`` had no environment override.

Round 106 gated the session cookie's ``Secure`` flag (and the OAuth callback
scheme) on ``general_settings.trusted_proxies``.  That list could not be set
from the environment: every scalar in the shipped config is set with
``os.environ/NAME``, and ``_interpolate`` returns a *string*, which a
``list[str]`` field rejects.  So the one knob a container must set to activate
round 106 behind a TLS-terminating ingress (a HuggingFace Space, a PaaS router)
was reachable only by editing ``wiwi.yaml`` — which the deployed image bakes
from the example, with no override.

``WIWI_TRUSTED_PROXIES`` now drives the list, comma- or whitespace-separated,
wired into ``wiwi.yaml.example`` the same way ``DATABASE_URL`` and ``REDIS_URL``
are.  An unset or empty value yields ``[]`` (the fail-closed default), so the
example is safe to ship unset.
"""

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-round109"

# The ``general_settings`` shape shipped in wiwi.yaml.example, reduced to the
# fields this test needs.  ``trusted_proxies`` is the line under test: it must
# resolve from the environment the way the example declares it.
_YAML = """
providers:
  - name: p1
    provider: openai
    keys:
      - {label: a, key: test-key}
model_list:
  - model_name: gpt-4o
    wiwi_params: {provider: p1, model: gpt-4o}
general_settings:
  master_key: os.environ/WIWI_MASTER_KEY
  database_url: os.environ/DATABASE_URL
  trusted_proxies: os.environ/WIWI_TRUSTED_PROXIES
"""


def _config(monkeypatch, tmp_path, trusted_env: str | None):
    monkeypatch.setenv("WIWI_MASTER_KEY", MASTER)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    if trusted_env is None:
        monkeypatch.delenv("WIWI_TRUSTED_PROXIES", raising=False)
    else:
        monkeypatch.setenv("WIWI_TRUSTED_PROXIES", trusted_env)
    return load_config_from_string(_YAML)


def test_env_var_populates_trusted_proxies(monkeypatch, tmp_path):
    """A comma-separated env value becomes the parsed CIDR list."""
    cfg = _config(monkeypatch, tmp_path, "127.0.0.1/32,10.0.0.0/8")
    assert cfg.general_settings.trusted_proxies == ["127.0.0.1/32", "10.0.0.0/8"]


def test_env_var_accepts_whitespace_separator(monkeypatch, tmp_path):
    """Whitespace (and a trailing comma) split the list too."""
    cfg = _config(monkeypatch, tmp_path, " 127.0.0.1/32   10.0.0.0/8 ,")
    assert cfg.general_settings.trusted_proxies == ["127.0.0.1/32", "10.0.0.0/8"]


def test_unset_env_var_is_the_fail_closed_default(monkeypatch, tmp_path):
    """No env var → empty list, i.e. no header is trusted (AUDIT #73)."""
    cfg = _config(monkeypatch, tmp_path, None)
    assert cfg.general_settings.trusted_proxies == []


def test_empty_env_var_is_also_empty(monkeypatch, tmp_path):
    """An explicitly empty value must not produce a ``[""]`` net."""
    cfg = _config(monkeypatch, tmp_path, "   ")
    assert cfg.general_settings.trusted_proxies == []


def test_yaml_list_still_validates(monkeypatch, tmp_path):
    """The pre-existing literal-list form keeps working (the validator is a
    ``mode="before"`` shim, not a replacement)."""
    monkeypatch.setenv("WIWI_MASTER_KEY", MASTER)
    cfg = load_config_from_string(
        _YAML.replace("trusted_proxies: os.environ/WIWI_TRUSTED_PROXIES",
                      'trusted_proxies: ["127.0.0.1/32"]'))
    assert cfg.general_settings.trusted_proxies == ["127.0.0.1/32"]


async def _login_cookie(cfg, headers: dict[str, str]) -> str:
    app = create_app(cfg)
    async with (LifespanManager(app),
                AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://t") as client):
        r = await client.post("/auth/login", json={"master_key": MASTER},
                              headers=headers)
        assert r.status_code == 200, r.text
        session = [c for c in r.headers.get_list("set-cookie")
                   if c.startswith("wiwi_session=")]
        assert session, "no wiwi_session cookie"
        return session[0]


def _has_attr(cookie: str, attr: str) -> bool:
    return any(part.strip().lower() == attr
               for part in cookie.split(";")[1:])


async def test_env_driven_proxy_activates_the_secure_flag(monkeypatch, tmp_path):
    """End to end: the env var alone turns round 106's ``Secure`` flag on.

    No ``WIWI_MASTER_KEY`` ever has to be published to configure a proxy — the
    gap that left the flag dormant on a Space, where the config file is baked.
    """
    cfg = _config(monkeypatch, tmp_path, "127.0.0.1/32")
    cookie = await _login_cookie(cfg, {"X-Forwarded-Proto": "https"})
    assert _has_attr(cookie, "secure"), cookie
    assert _has_attr(cookie, "httponly"), cookie


async def test_env_driven_proxy_still_rejects_an_untrusted_peer(monkeypatch, tmp_path):
    """The env list is the same gate as the YAML list: a peer outside it
    cannot mint a Secure cookie by header injection."""
    cfg = _config(monkeypatch, tmp_path, "10.0.0.0/8")
    cookie = await _login_cookie(cfg, {"X-Forwarded-Proto": "https"})
    assert not _has_attr(cookie, "secure"), cookie
