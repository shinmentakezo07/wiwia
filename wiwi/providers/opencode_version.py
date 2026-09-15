"""Live OpenCode version helper for the ``opencode`` (Zen) provider.

Zen sits behind Cloudflare and expects a real ``User-Agent: opencode/<x.y.z>``
— requests without one get a ``403 error code: 1010`` instead of the API.
Sending a stale pinned version risks an upstream minimum-version gate, so the
adapter reads the version live at request-build time from this module's cache.

The npm registry is the CLI's distribution source of truth (the endpoint
opencode's own ``Installation.latest`` uses for npm/bun/pnpm installs, and the
one the Cline and WorkBuddy version helpers already read)::

    GET https://registry.npmjs.org/opencode-ai/latest
    -> {"version": "x.y.z"}

GitHub's ``releases/latest`` REST API served this before, but anonymous calls
to it are capped at 60 requests/hour **per IP** — a budget the 5-minute sweep
alone spends 12 of, shared with every other API consumer behind the same
egress IP. Once exhausted the API answers a bare ``403``, the cache never
fills, and the adapter sends ``opencode/unknown``.

Refresh policy: 5-minute TTL, background sweep (no restart needed) plus a
stale-while-revalidate fallback in :func:`get_cached_version` so
``OpencodeAdapter.headers()`` — which is synchronous — never blocks a request.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import structlog

log = structlog.get_logger("wiwi.opencode_version")

NPM_LATEST_URL = "https://registry.npmjs.org/opencode-ai/latest"
TTL_S = 300.0
TICK_S = 300.0
FIRST_SWEEP_DELAY_S = 10.0
FETCH_TIMEOUT_S = 10.0
FALLBACK_VERSION = "unknown"

# Header-injection guard: a version string reaches a header value, and the
# registry response is remote input. Same rule as the Cline/WorkBuddy helpers.
_HEADER_VALUE_RE = re.compile(r"[^\r\n\x00]")
_MAX_HEADER_LEN = 256

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


def _parse_version(value: Any) -> str | None:
    """Read the registry's ``version`` field, sanitized for a header value.

    The registry response is remote input and the value lands in a
    ``User-Agent``, so CR/LF/NUL are stripped and the length is capped. A
    non-string is a malformed payload: reject it rather than stringify a
    structure into the header.
    """
    if not isinstance(value, str):
        return None
    text = "".join(_HEADER_VALUE_RE.findall(value)).strip()
    return text[:_MAX_HEADER_LEN] or None


async def _fetch_npm_version(client: Any) -> str | None:
    """Fetch the npm latest version; any failure yields None."""
    try:
        resp = await client.get(
            NPM_LATEST_URL,
            headers={
                "Accept": "application/json",
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
    version = _parse_version(data.get("version")) if isinstance(data, dict) else None
    if version is None:
        log.warning("opencode_version_bad_payload")
        return None
    return version


async def refresh_version() -> str | None:
    """Fetch the latest release from npm and update the cache.

    Returns the new version on success, ``None`` on failure (cache kept).
    Never raises — failures only log, so the background sweep and request
    paths can't crash the gateway on a transient network error.
    """
    global _cached_version, _fetched_at
    try:
        import httpx

        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
            version = await _fetch_npm_version(client)
    except Exception as e:  # noqa: BLE001 — the worker must survive bad runtimes
        log.warning("opencode_version_fetch_failed", err=str(e))
        return None
    if version is None:
        return None
    async with _get_lock():
        _cached_version = version
        _fetched_at = time.monotonic()
    log.info("opencode_version_refreshed", version=version)
    return version


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
