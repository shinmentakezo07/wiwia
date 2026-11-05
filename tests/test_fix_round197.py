"""Round 197 — a removed build asset is not an SPA route (AUDIT #581).

Vite content-hashes lazy-loaded pages. A redeploy removes the previous hash,
but an already-open tab can still request it. The old SPA catch-all returned
``index.html`` with status 200 for that missing JavaScript URL, turning the
deployment mismatch into a browser dynamic-import error. Missing assets now
return a real 404, while normal client-side routes still get the HTML shell.
"""

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.config import GeneralSettings, WiwiConfig
from wiwi.server.app import create_app


async def test_removed_deployment_asset_is_not_served_as_html():
    app = create_app(WiwiConfig(general_settings=GeneralSettings(master_key="sk-wiwi-master-test")))
    async with LifespanManager(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        asset = await client.get("/assets/Landing-B6w6Jdxx.js")
        route = await client.get("/docs")

    assert asset.status_code == 404
    assert "text/plain" in asset.headers["content-type"]
    assert "no-store" in asset.headers["cache-control"]
    assert "<html" not in asset.text.lower()
    assert "text/html" in route.headers["content-type"]
    assert "no-cache" in route.headers["cache-control"]


async def test_asset_404_has_no_history_fallback():
    app = create_app(WiwiConfig(general_settings=GeneralSettings(master_key="sk-test")))
    async with LifespanManager(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        asset = await client.get("/assets/Landing-B6w6Jdxx.js")
        page = await client.get("/pricing")
    assert asset.status_code == 404
    assert asset.text == "Static asset not found"
    assert "no-store" in asset.headers["cache-control"]
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "no-cache" in page.headers["cache-control"]
