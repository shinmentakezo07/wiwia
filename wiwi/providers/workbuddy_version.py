"""Live CodeBuddy CLI version helper for the ``workbuddy`` provider.

The WorkBuddy upstream is the CodeBuddy CLI's own backend, and wiwi identifies
itself with the CLI's ``User-Agent`` (``CLI/<v> CodeBuddy/<v>``). That string
was pinned to ``2.63.2`` — a release from 2026-03-17, ~87 versions behind the
current npm latest — so the fingerprint drifts further out of date with every
CLI release and can fail an upstream client-version gate.

The npm registry is the CLI's distribution source of truth:

    GET https://registry.npmjs.org/@tencent-ai/codebuddy-code/latest
    -> {"version": "x.y.z"}

The cache is refreshed by a background task every five minutes. The synchronous
``headers()`` path only reads the cache, so a network failure or slow registry
request never blocks a gateway request.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import structlog

log = structlog.get_logger("wiwi.workbuddy_version")

NPM_LATEST_URL = "https://registry.npmjs.org/@tencent-ai/codebuddy-code/latest"
TTL_S = 300.0
TICK_S = 300.0
FIRST_SWEEP_DELAY_S = 10.0
FETCH_TIMEOUT_S = 10.0
FALLBACK_VERSION = "unknown"

# Header-injection guard: a version string reaches a header value, and the
# registry response is remote input. Same rule as the Cline adapter's _clean.
_HEADER_VALUE_RE = re.compile(r"[^\r\n\x00]")
_MAX_HEADER_LEN = 256

_cached_version: str | None = None
_fetched_at: float = 0.0


def _clean(value: Any) -> str | None:
    """Strip CR/LF/NUL, trim, and cap length. None when nothing usable remains."""
    if value is None:
        return None
    text = "".join(_HEADER_VALUE_RE.findall(str(value))).strip()
    if not text:
        return None
    return text[:_MAX_HEADER_LEN]


def get_cached_version() -> str:
    """Return the last known CodeBuddy CLI version without doing I/O."""
    return _cached_version or FALLBACK_VERSION


def client_version() -> str:
    """Return the cached version sanitized for use in a header value."""
    return _clean(_cached_version) or FALLBACK_VERSION


def client_user_agent() -> str:
    """Return the CLI's ``User-Agent`` fingerprint built from the live version."""
    version = client_version()
    return f"CLI/{version} CodeBuddy/{version}"


def is_stale(now: float | None = None) -> bool:
    """Return whether the cached value has reached the five-minute TTL."""
    now = time.monotonic() if now is None else now
    return (now - _fetched_at) >= TTL_S


def _parse_version(value: Any) -> str | None:
    return _clean(value)


async def _fetch_npm_version(client: Any) -> str | None:
    """Fetch the npm latest version; any failure yields None."""
    try:
        resp = await client.get(
            NPM_LATEST_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "wiwi-workbuddy-version-refresh",
            },
        )
        if resp.status_code != 200:
            log.warning("workbuddy_version_fetch_bad_status",
                        status=resp.status_code)
            return None
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — keep the stale value on network errors
        log.warning("workbuddy_version_fetch_failed", err=str(e))
        return None

    version = _parse_version(data.get("version")) if isinstance(data, dict) else None
    if version is None:
        log.warning("workbuddy_version_bad_payload")
        return None
    return version


async def refresh_version() -> str | None:
    """Fetch the npm latest version and update the cache on success.

    Returns the accepted version, or ``None`` when the fetch failed — in which
    case the previous cache entry is left untouched so a transient registry
    outage keeps the last good fingerprint. Never raises.
    """
    global _cached_version, _fetched_at

    try:
        import httpx

        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
            version = await _fetch_npm_version(client)
    except Exception as e:  # noqa: BLE001 — the worker must survive bad runtimes
        log.warning("workbuddy_version_fetch_failed", err=str(e))
        return None

    if version is None:
        return None

    if version != _cached_version:
        log.info("workbuddy_version_refreshed", version=version,
                 previous=_cached_version)
    _cached_version = version
    _fetched_at = time.monotonic()
    return version


async def get_version() -> str:
    """Return a fresh version, refreshing first when the cache is stale."""
    if _cached_version is None or is_stale():
        refreshed = await refresh_version()
        if refreshed is not None:
            return refreshed
    return get_cached_version()


def _set_cached_for_tests(version: str | None, fetched_at: float) -> None:
    """Test seam: seed or reset the module cache without network."""
    global _cached_version, _fetched_at
    _cached_version = version
    _fetched_at = fetched_at


class WorkBuddyVersionRefresh:
    """Background task refreshing the CodeBuddy CLI version every 5 min."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(),
                                         name="workbuddy-version-refresh")

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
                    log.exception("workbuddy_version_sweep_error")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
