"""Round-37 regression tests: runtime response-cache toggle.

Scope (reproduced against source before fixing):

``cache_settings.enabled`` was config-file-only. There was no admin endpoint
for it, so turning the response cache on or off meant editing ``wiwi.yaml``
and restarting the gateway. Restarting is precisely what a Redis-backed cache
exists to survive, so the knob was unusable in the deployment where it
matters most.

Worse, ``AppState.__init__`` built the cache exactly once from the loaded
config and ``run_chat_like`` read ``state.response_cache`` from that single
construction. So even a hypothetical API could not have flipped it without
stranding or leaking the backend instance.

This round adds ``GET``/``PUT /admin/cache/settings`` and teaches AppState to
(re)build and close the backend on demand. Contracts:

- the toggle is live: enabling makes the very next request cacheable
- the toggle is durable: persisted to the settings table and applied at
  startup, overriding the YAML value
- disabling closes the backend, so a memory cache stops retaining response
  bodies and a Redis client is not left open
- the Redis URL is never echoed back: it carries a password

Redis is not contacted at construction time (the client is lazy), so the
backend can be selected and reported without a live server.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    CacheSettings,
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
REDIS_URL = "redis://user:supersecret@fake.invalid:6379/0"

UPSTREAM_BODY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}

CHAT_BODY = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def _cfg(tmp_path, *, enabled: bool = False, redis_url: str = "",
         db_path=None) -> WiwiConfig:
    db_url = ("sqlite+aiosqlite:///:memory:" if db_path is None
              else f"sqlite+aiosqlite:///{db_path}")
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                              keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=db_url, redis_url=redis_url),
        router_settings=RouterSettings(
            stream_journal_enabled=False,
            stream_journal_dir=str(tmp_path / "journals")),
        cache_settings=CacheSettings(enabled=enabled, ttl_s=300.0, max_entries=16),
    )


@asynccontextmanager
async def _running(app):
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


async def _two_chat_calls(client):
    """Two identical non-streaming chat calls; return (2nd resp, upstream hits)."""
    with respx.mock:
        route = respx.post("https://api.openai.com/v1/chat/completions")
        route.respond(json=UPSTREAM_BODY)
        await client.post("/v1/chat/completions", json=CHAT_BODY, headers=AUTH)
        second = await client.post("/v1/chat/completions", json=CHAT_BODY,
                                   headers=AUTH)
    return second, route.call_count


# ---------------------------------------------------------------------------
# 1. Auth + shape
# ---------------------------------------------------------------------------

async def test_cache_settings_requires_master(tmp_path):
    from wiwi.server.app import create_app

    async with _running(create_app(_cfg(tmp_path))) as c:
        assert (await c.get("/admin/cache/settings")).status_code == 401
        put = await c.put("/admin/cache/settings", json={"enabled": True})
        assert put.status_code == 401


async def test_cache_settings_reports_disabled_by_default(tmp_path):
    from wiwi.server.app import create_app

    async with _running(create_app(_cfg(tmp_path))) as c:
        r = await c.get("/admin/cache/settings", headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is False
    # No backend is constructed while disabled — nothing is holding memory
    # or an open Redis client.
    assert d["backend"] == "none"
    assert d["ttl_s"] == 300.0
    assert d["max_entries"] == 16
    assert d["bypass_header"] == "x-wiwi-no-cache"
    assert d["redis_configured"] is False


async def test_cache_settings_reports_redis_backend_when_url_set(tmp_path):
    """A configured Redis URL must select the Redis backend, not memory."""
    from wiwi.server.app import create_app

    app = create_app(_cfg(tmp_path, redis_url=REDIS_URL))
    async with _running(app) as c:
        r = await c.put("/admin/cache/settings", json={"enabled": True},
                        headers=AUTH)
        assert r.status_code == 200
        assert r.json()["backend"] == "redis"
        assert r.json()["redis_configured"] is True


async def test_cache_settings_never_leaks_redis_url(tmp_path):
    """The URL embeds a password; it must not be serialised into the response."""
    from wiwi.server.app import create_app

    app = create_app(_cfg(tmp_path, redis_url=REDIS_URL))
    async with _running(app) as c:
        r = await c.get("/admin/cache/settings", headers=AUTH)
    text = r.text
    assert "supersecret" not in text, "redis password leaked into admin API"
    assert "redis://" not in text
    assert r.json()["redis_configured"] is True


# ---------------------------------------------------------------------------
# 2. Live toggle
# ---------------------------------------------------------------------------

async def test_cache_settings_enable_takes_effect_immediately(tmp_path):
    from wiwi.server.app import create_app

    app = create_app(_cfg(tmp_path))
    async with _running(app) as c:
        r = await c.put("/admin/cache/settings", json={"enabled": True},
                        headers=AUTH)
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        assert r.json()["backend"] == "memory"
        assert app.state.wiwi.response_cache is not None

        second, calls = await _two_chat_calls(c)
        assert second.headers.get("x-wiwi-cache") == "HIT"
        assert calls == 1, "second identical request must be served from cache"


async def test_cache_settings_disable_takes_effect_immediately(tmp_path):
    """Disabling must stop caching, not merely relabel it."""
    from wiwi.server.app import create_app

    app = create_app(_cfg(tmp_path, enabled=True))
    async with _running(app) as c:
        r = await c.put("/admin/cache/settings", json={"enabled": False},
                        headers=AUTH)
        assert r.status_code == 200
        assert r.json()["enabled"] is False
        assert r.json()["backend"] == "none"
        assert app.state.wiwi.response_cache is None

        second, calls = await _two_chat_calls(c)
        assert "x-wiwi-cache" not in second.headers
        assert calls == 2, "disabled cache must not short-circuit upstream"


async def test_cache_settings_enable_is_idempotent(tmp_path):
    """Re-enabling must not rebuild the backend — that would drop every
    warm entry, making the second PUT a silent cache flush."""
    from wiwi.server.app import create_app

    app = create_app(_cfg(tmp_path))
    async with _running(app) as c:
        await c.put("/admin/cache/settings", json={"enabled": True}, headers=AUTH)
        first = app.state.wiwi.response_cache
        await c.put("/admin/cache/settings", json={"enabled": True}, headers=AUTH)
        assert app.state.wiwi.response_cache is first


async def test_cache_settings_disable_closes_backend(tmp_path):
    """A disabled cache must not keep response bodies resident."""
    from wiwi.server.app import create_app

    app = create_app(_cfg(tmp_path, enabled=True))
    async with _running(app) as c:
        warm = app.state.wiwi.response_cache
        await _two_chat_calls(c)
        assert len(warm) == 1
        await c.put("/admin/cache/settings", json={"enabled": False}, headers=AUTH)
        assert len(warm) == 0, "backend must be closed, dropping cached bodies"


# ---------------------------------------------------------------------------
# 3. Validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"enabled": "yes"}, {"enabled": 1}, {"enabled": None}, {},
])
async def test_cache_settings_rejects_non_boolean(tmp_path, payload):
    """A 400 must leave the cache state untouched — a rejected toggle that
    still flipped ``enabled`` would be worse than no validation at all."""
    from wiwi.server.app import create_app

    app = create_app(_cfg(tmp_path))
    async with _running(app) as c:
        r = await c.put("/admin/cache/settings", json=payload, headers=AUTH)
        assert r.status_code == 400
        assert "boolean" in r.json()["error"]["message"]
        # Rejected write must not have built a backend or persisted anything.
        assert app.state.wiwi.response_cache is None
        assert app.state.wiwi.config.cache_settings.enabled is False


# ---------------------------------------------------------------------------
# 4. Durability across restarts
# ---------------------------------------------------------------------------

async def test_cache_settings_persists_enabled(tmp_path):
    """Enabled via API, still enabled after a restart on the same DB."""
    from wiwi.server.app import create_app

    db = tmp_path / "wiwi.db"
    async with _running(create_app(_cfg(tmp_path, db_path=db))) as c:
        await c.put("/admin/cache/settings", json={"enabled": True}, headers=AUTH)

    # Fresh process: YAML still says disabled, DB must win.
    async with _running(create_app(_cfg(tmp_path, db_path=db))) as c:
        d = (await c.get("/admin/cache/settings", headers=AUTH)).json()
        assert d["enabled"] is True
        assert d["backend"] == "memory"


async def test_cache_settings_persists_disabled(tmp_path):
    """The inverse: DB records 'off' and must override an enabled YAML."""
    from wiwi.server.app import create_app

    db = tmp_path / "wiwi.db"
    async with _running(create_app(_cfg(tmp_path, enabled=True, db_path=db))) as c:
        await c.put("/admin/cache/settings", json={"enabled": False}, headers=AUTH)

    async with _running(create_app(_cfg(tmp_path, enabled=True, db_path=db))) as c:
        d = (await c.get("/admin/cache/settings", headers=AUTH)).json()
        assert d["enabled"] is False
        assert d["backend"] == "none"


async def test_cache_settings_startup_falls_back_to_yaml(tmp_path):
    """With no DB row the YAML value stands (no phantom override)."""
    from wiwi.server.app import create_app

    db = tmp_path / "wiwi.db"
    async with _running(create_app(_cfg(tmp_path, enabled=True, db_path=db))) as c:
        d = (await c.get("/admin/cache/settings", headers=AUTH)).json()
    assert d["enabled"] is True
