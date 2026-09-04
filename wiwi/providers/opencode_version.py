"""Live OpenCode version helper for the ``opencode`` (Zen) provider.

Zen sits behind Cloudflare and expects a real ``User-Agent: opencode/<x.y.z>``
— requests without one get a ``403 error code: 1010`` instead of the API.
Sending a stale pinned version risks an upstream minimum-version gate, so the
adapter reads the version live at request-build time from this module's cache.

The cache is refreshed from the source of truth opencode itself uses for the
``curl`` install method (see ``Installation.latest`` in the opencode repo)::

    GET https://api.github.com/repos/anomalyco/opencode/releases/latest
    -> {"tag_name": "v1.2.3"}  (leading ``v`` stripped)

Refresh policy: 5-minute TTL, background sweep (no restart needed) plus a
stale-while-revalidate fallback in :func:`get_cached_version` so
``OpencodeAdapter.headers()`` — which is synchronous — never blocks a request.
"""

from __future__ import annotations

import asyncio
import time

import structlog

log = structlog.get_logger("wiwi.opencode_version")

GITHUB_LATEST_URL = "https://api.github.com/repos/anomalyco/opencode/releases/latest"
TTL_S = 300.0
TICK_S = 300.0
FIRST_SWEEP_DELAY_S = 10.0
FETCH_TIMEOUT_S = 10.0
FALLBACK_VERSION = "unknown"

_cached_version: str | None = None
_fetched_at: float = 0.0
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def get_cached_version() -> str:
    """Return the last known OpenCode version without doing I/O.

    Used by the synchronous ``headers()`` path. Returns ``"unknown"`` when no
    successful fetch has happened yet (e.g. the first seconds after startup
    before the background sweep runs).
    """
    return _cached_version or FALLBACK_VERSION


def is_stale(now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    return (now - _fetched_at) >= TTL_S


def _parse_tag(tag: object) -> str | None:
    if not isinstance(tag, str) or not tag.strip():
        return None
    text = tag.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    return text or None


async def refresh_version() -> str | None:
    """Fetch the latest release from GitHub and update the cache.

    Returns the new version on success, ``None`` on failure (cache kept).
    Never raises — failures only log, so the background sweep and request
    paths can't crash the gateway on a transient network error.
    """
    global _cached_version, _fetched_at
    try:
        import httpx

        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
            resp = await client.get(
                GITHUB_LATEST_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "wiwi-opencode-version-refresh",
                },
            )
            if resp.status_code != 200:
                log.warning("opencode_version_fetch_bad_status", status=resp.status_code)
                return None
            data = resp.json()
    except Exception as e:  # noqa: BLE001 — network failures keep stale cache
        log.warning("opencode_version_fetch_failed", err=str(e))
        return None
    version = _parse_tag(data.get("tag_name")) if isinstance(data, dict) else None
    if version is None:
        log.warning("opencode_version_bad_payload")
        return None
    async with _get_lock():
        _cached_version = version
        _fetched_at = time.monotonic()
    log.info("opencode_version_refreshed", version=version)
    return version


async def get_version() -> str:
    """Return a fresh version, refreshing first when the cache is stale."""
    if _cached_version is None or is_stale():
        refreshed = await refresh_version()
        if refreshed is not None:
            return refreshed
    return get_cached_version()


def build_user_agent() -> str:
    return f"opencode/{get_cached_version()}"


def _set_cached_for_tests(version: str | None, fetched_at: float) -> None:
    """Test seam: seed/reset the module cache without network."""
    global _cached_version, _fetched_at
    _cached_version = version
    _fetched_at = fetched_at


class OpencodeVersionRefresh:
    """Background task refreshing the cached OpenCode version every 5 min.

    Mirrors ``ClineAutoRefresh``/``WorkBuddyAutoRefresh`` lifecycle
    (``start()``/``stop()`` wired in ``wiwi.server.app.lifespan``) so header
    versions stay live without a server restart or reload.
    """

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="opencode-version-refresh")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        try:
            await asyncio.sleep(FIRST_SWEEP_DELAY_S)
            while not self._stop.is_set():
                try:
                    await refresh_version()
                except Exception:
                    log.exception("opencode_version_sweep_error")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
