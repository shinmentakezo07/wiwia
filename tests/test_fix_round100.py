"""Round 100 — Playground bearer lifetime.

Originally every login minted a fresh playground key and the per-owner cap
(`_MAX_PLAYGROUND_KEYS_PER_USER = 5`) expired the oldest ones, so a key handed
out at login N was dead by login N+5 — and every login left another unused
credential in the owner's key list.

That per-login mint is gone: login and signup start a session and nothing
else, and the Playground wrapper mints one hidden key on first use and reuses
it for the account's whole life. These tests pin that reuse contract and
assert the client contract that the fix must satisfy.
"""

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"

_CONFIG_YAML = f"""
general_settings:
  master_key: {MASTER}
providers:
  - name: openai
    provider: openai
    keys: [{{label: main, key: sk-upstream-fake}}]
model_list:
  - model_name: gpt-4o
    wiwi_params:
      provider: openai
      model: gpt-4o
"""


async def _client_for_config(tmp_path, config_yaml: str) -> AsyncClient:
    """Build an ASGI-backed client with the app lifespan started.

    The DB is a fresh file inside ``tmp_path`` so keys never collide across
    tests or runs. Closing the client also tears the lifespan down.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path}/app.db"
    patched = config_yaml.replace(
        "  master_key:", f"  database_url: {db_url}\n  master_key:", 1)
    cfg_path = tmp_path / "wiwi.yaml"
    cfg_path.write_text(patched)
    app = create_app(_config_from_path(str(cfg_path)))
    lm = LifespanManager(app)
    await lm.__aenter__()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    _orig_close = client.aclose

    async def _close_then_lifespan():
        try:
            await _orig_close()
        finally:
            await lm.__aexit__(None, None, None)

    client.aclose = _close_then_lifespan  # type: ignore[method-assign]
    return client


def _config_from_path(path: str):
    from wiwi.config import load_config
    return load_config(path)


# -- the server contract the client fix depends on ---------------------------


async def test_repeated_logins_do_not_accumulate_playground_keys(tmp_path):
    """Login mints nothing; repeated fallback calls return the same key.

    A login that minted a credential leaked an unused key into the owner's
    list on every sign-in. The wrapper now mints once, on demand, and reuses
    that credential until it expires or is revoked.
    """
    client = await _client_for_config(tmp_path, _CONFIG_YAML)
    for _ in range(6):
        r = await client.post("/auth/login", json={"master_key": MASTER})
        assert r.status_code == 200
        assert "playground_key" not in r.json()

    r = await client.post("/auth/playground-key")
    assert r.status_code == 200
    key = r.json()["key"]
    r = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200

    for _ in range(4):
        again = await client.post("/auth/playground-key")
        assert again.json()["key"] == key, "the live key must be reused"
    await client.aclose()


async def test_one_live_playground_key_after_many_logins(tmp_path):
    """The owner's key list holds exactly one playground credential."""
    client = await _client_for_config(tmp_path, _CONFIG_YAML)
    for _ in range(6):
        await client.post("/auth/login", json={"master_key": MASTER})
    r = await client.post("/auth/playground-key")
    assert r.status_code == 200
    key = r.json()["key"]
    r = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200

    keys = (await client.get("/admin/keys")).json()["keys"]
    playground = [k for k in keys if k["alias"] == "playground"]
    assert len(playground) == 1, playground
    await client.aclose()


# -- the client contract (updated by round 103) ------------------------------
# The Playground now posts to /v1/playground/completions, which authenticates
# with the session cookie and resolves the playground key server-side. The
# bearer/401 re-mint contract is superseded by tests/test_fix_round103.py; the
# assertions below pin that the client-side key machinery is really gone.


def test_playground_no_longer_holds_key_material():
    """The client-side bearer dance is dead: no sessionStorage key, no
    401 re-mint branch, no ensurePlaygroundKey in the auth context.

    `web/` has no test runner (see AUDIT #113), so this asserts the source
    contract rather than running the component.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    playground = (root / "web/src/pages/Playground.tsx").read_text()
    auth = (root / "web/src/api/auth.tsx").read_text()

    assert "/v1/playground/completions" in playground, (
        "Playground must use the cookie-authenticated wrapper endpoint"
    )
    assert "ensurePlaygroundKey" not in playground and "ensurePlaygroundKey" not in auth, (
        "ensurePlaygroundKey must be gone once the server owns the key"
    )
    assert "wiwi.playground_key" not in auth, (
        "sessionStorage must not hold playground key material anymore"
    )
    assert 'fetch("/v1/chat/completions"' not in playground, (
        "Playground must not bypass the wrapper with a raw bearer call"
    )
