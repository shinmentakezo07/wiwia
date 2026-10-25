"""Round 171 — one deployment-facing defect: the SPA entry document was
served with no ``Cache-Control``, so browsers heuristically cached it.

The Playground mobile-drawer fix (commit 077ee09) was reported as "still
broken on my phone" even though the served bundle already contained it.
Cause: ``index.html`` is the only unhashed file in the build, and wiwi's
``SPAStaticFiles`` sent it with no ``Cache-Control`` at all. A response
with no freshness information may be heuristically cached (RFC 9111
§4.2.2 — typically 10% of Last-Modified age), so a returning browser
reused the old ``index.html``, which pins the PREVIOUS build's hashed
asset filenames. Every asset request then 404s through the SPA fallback
or keeps serving the old bundle until the heuristic expires. A plain
reload revalidates anyway; the broken case is following a link / reopening
a tab, which is exactly how a phone reaches the Playground.

The fix: the HTML entry document (``/``, ``/index.html``, the SPA
fallback for unknown routes, and any other ``.html``) is served
``Cache-Control: no-cache`` — revalidate every time; the ETag makes that
a cheap 304. Hashed ``/assets/*`` keep their default caching and are
never re-sent unnecessarily.
"""

import httpx
import pytest
from asgi_lifespan import LifespanManager

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app

RELAY_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
providers:
  - name: relay
    provider: anthropic
    base_url: https://relay.example.com
    keys:
      - {label: main, key: sk-test}
model_list:
  - model_name: gpt-test
    wiwi_params:
      provider: relay
      model: claude-x
"""


@pytest.fixture
async def client():
    app = create_app(load_config_from_string(RELAY_CFG))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


def _cc(response: httpx.Response) -> str:
    return response.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# 1. The entry document must not be heuristically cacheable
# ---------------------------------------------------------------------------


async def test_root_index_is_served_no_cache(client):
    """``/`` is the SPA entry; it must always be revalidated."""
    r = await client.get("/")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", ""), r.headers
    assert "no-cache" in _cc(r), (
        f"index.html served with '{_cc(r) or 'no Cache-Control'}' — a "
        "browser may heuristically cache it and pin an old build's "
        "hashed assets")


async def test_explicit_index_html_is_served_no_cache(client):
    r = await client.get("/index.html")
    assert r.status_code == 200, r.text
    assert "no-cache" in _cc(r), _cc(r)


async def test_spa_fallback_for_a_client_side_route_is_no_cache(client):
    """``/playground`` is not a file; the SPA fallback answers it with
    index.html, and that response is the entry document too."""
    r = await client.get("/playground")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", ""), r.headers
    assert "no-cache" in _cc(r), _cc(r)


# ---------------------------------------------------------------------------
# 2. Hashed assets keep their default caching — no-cache is only for HTML
# ---------------------------------------------------------------------------


async def test_hashed_assets_are_not_forced_to_revalidate(client):
    """A content-hashed asset may be cached forever; forcing revalidation
    on it would waste a round trip per asset per visit."""
    r = await client.get("/assets/does-not-exist-123456.js")
    # The file does not exist — the fallback serves index.html (no-cache).
    # Assert on that behaviour explicitly so this test documents the mount
    # semantics rather than silently passing on a 200 asset body.
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", ""), r.headers
    assert "no-cache" in _cc(r), _cc(r)


async def test_real_asset_keeps_default_cache_control(client, tmp_path):
    """Serve a real file next to a real asset and confirm only the HTML
    entry gets ``no-cache`` — the static machinery is untouched."""
    import shutil
    from pathlib import Path

    static_src = Path(__file__).parent.parent / "wiwi" / "server" / "static"
    if not static_src.is_dir():
        pytest.skip("SPA build not present (wiwi/server/static)")

    work = tmp_path / "static"
    shutil.copytree(static_src, work)
    (work / "assets").mkdir(exist_ok=True)
    (work / "assets" / "app-abc123.js").write_text("console.log(1);")

    import os

    old = os.environ.get("WIWI_STATIC_DIR")
    os.environ["WIWI_STATIC_DIR"] = str(work)
    try:
        app = create_app(load_config_from_string(RELAY_CFG))
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as c:
                entry = await c.get("/")
                asset = await c.get("/assets/app-abc123.js")
    finally:
        if old is None:
            del os.environ["WIWI_STATIC_DIR"]
        else:
            os.environ["WIWI_STATIC_DIR"] = old

    assert "no-cache" in _cc(entry), _cc(entry)
    assert "no-cache" not in _cc(asset), (
        f"hashed asset forced to revalidate: '{_cc(asset)}'")
    assert asset.status_code == 200 and "javascript" in asset.headers.get(
        "content-type", ""), asset.headers
