"""Round 100 — Playground self-heals a revoked/expired bearer.

Every login mints a fresh playground key and the per-owner cap
(`_MAX_PLAYGROUND_KEYS_PER_USER = 5`) expires the oldest ones, so a key
handed out at login N is dead by login N+5. The browser keeps that key in
`sessionStorage` and the Playground component holds it in `bearer` state,
so nothing notices: every `/v1/chat/completions` came back
`401 invalid API key` even though the session cookie was perfectly valid
and `/admin/models` returned 200 in the same breath.

These tests pin the *server* half that makes the cap observable (the
contract the client half relies on) and assert the client contract that
the fix must satisfy.
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


async def test_playground_key_cap_expires_the_oldest_key(tmp_path):
    """The cap must retire the *oldest* live playground key, not the newest.

    The client re-mints on a 401 and retries once, so it needs the key it
    just received to still be usable. If the cap retired the wrong end of
    the list, the retry would loop straight back into another 401.
    """
    client = await _client_for_config(tmp_path, _CONFIG_YAML)
    keys = []
    for _ in range(6):
        r = await client.post("/auth/login", json={"master_key": MASTER})
        keys.append(r.json()["playground_key"])

    # The newest key still authenticates after the cap fired.
    r = await client.get("/v1/models", headers={"Authorization": f"Bearer {keys[-1]}"})
    assert r.status_code == 200
    # The oldest one does not — this is exactly the 401 the Playground used
    # to hand straight to the user.
    r = await client.get("/v1/models", headers={"Authorization": f"Bearer {keys[0]}"})
    assert r.status_code == 401
    await client.aclose()


async def test_reminted_key_is_live_even_at_the_cap(tmp_path):
    """Sanity guard for the fix's retry: re-minting yields a live key even
    once the account is at its cap."""
    client = await _client_for_config(tmp_path, _CONFIG_YAML)
    for _ in range(6):
        await client.post("/auth/login", json={"master_key": MASTER})
    r = await client.post("/auth/playground-key")
    assert r.status_code == 200
    key = r.json()["key"]
    r = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
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
