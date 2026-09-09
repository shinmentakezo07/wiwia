"""Live Cline CLI and core version helper for the ``cline`` provider.

Cline's API expects the caller to identify itself with the real Cline client
version, while ``X-CORE-VERSION`` carries the version of Cline's core package.
Using wiwi's package version (or Python's version) for those fields can fail an
upstream client-version gate and makes the fingerprint misleading.

The npm registry is the CLI's distribution source of truth:

    GET https://registry.npmjs.org/cline/latest
    GET https://registry.npmjs.org/@cline/core/latest
    -> {"version": "x.y.z"}

The cache is refreshed by a background task every five minutes. The synchronous
``headers()`` path only reads the cache, so a network failure or slow registry
request never blocks a gateway request.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

log = structlog.get_logger("wiwi.cline_version")

NPM_CLI_LATEST_URL = "https://registry.npmjs.org/cline/latest"
NPM_CORE_LATEST_URL = "https://registry.npmjs.org/@cline/core/latest"
TTL_S = 300.0
TICK_S = 300.0
FIRST_SWEEP_DELAY_S = 10.0
FETCH_TIMEOUT_S = 10.0
FALLBACK_VERSION = "unknown"

_cached_cli_version: str | None = None
_cached_core_version: str | None = None
_cli_fetched_at: float = 0.0
_core_fetched_at: float = 0.0


def get_cached_cli_version() -> str:
    """Return the last known Cline CLI version without doing I/O."""
    return _cached_cli_version or FALLBACK_VERSION


def get_cached_core_version() -> str:
    """Return the last known Cline core version without doing I/O."""
    return _cached_core_version or FALLBACK_VERSION


def is_stale(now: float | None = None) -> bool:
    """Return whether either cached value has reached the five-minute TTL."""
    now = time.monotonic() if now is None else now
    fetched_at = min(_cli_fetched_at, _core_fetched_at)
    return (now - fetched_at) >= TTL_S


def _parse_version(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    version = value.strip()
    return version or None


async def _fetch_npm_version(
    client: Any,
    url: str,
    kind: str,
) -> str | None:
    """Fetch one npm version, isolating failures from the other value."""
    try:
        resp = await client.get(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "wiwi-cline-version-refresh",
            },
        )
        if resp.status_code != 200:
            log.warning(
                "cline_version_fetch_bad_status",
                kind=kind,
                status=resp.status_code,
            )
            return None
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — keep the stale value on network errors
        log.warning("cline_version_fetch_failed", kind=kind, err=str(e))
        return None

    version = _parse_version(data.get("version")) if isinstance(data, dict) else None
    if version is None:
        log.warning("cline_version_bad_payload", kind=kind)
        return None
    return version


async def refresh_version() -> tuple[str | None, str | None]:
    """Fetch both npm versions and update each cache independently.

    Returns ``(cli_version, core_version)`` for values accepted from the
    registry. A failed fetch returns ``None`` for that value and leaves its
    previous cache entry untouched. This method never raises for registry or
    network failures.
    """
    global _cached_cli_version, _cached_core_version
    global _cli_fetched_at, _core_fetched_at

    try:
        import httpx

        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
            cli_version = await _fetch_npm_version(
                client,
                NPM_CLI_LATEST_URL,
                "cli",
            )
            core_version = await _fetch_npm_version(
                client,
                NPM_CORE_LATEST_URL,
                "core",
            )
    except Exception as e:  # noqa: BLE001 — the worker must survive bad runtimes
        log.warning("cline_version_fetch_failed", kind="both", err=str(e))
        return None, None

    fetched_at = time.monotonic()
    if cli_version is not None:
        _cached_cli_version = cli_version
        _cli_fetched_at = fetched_at
    if core_version is not None:
        _cached_core_version = core_version
        _core_fetched_at = fetched_at

    if cli_version is not None or core_version is not None:
        log.info(
            "cline_version_refreshed",
            cli=cli_version,
            core=core_version,
        )
    return cli_version, core_version


async def get_version() -> str:
    """Return a fresh CLI version, refreshing first when its cache is stale."""
    if (
        _cached_cli_version is None
        or (time.monotonic() - _cli_fetched_at) >= TTL_S
    ):
        refreshed, _ = await refresh_version()
        if refreshed is not None:
            return refreshed
    return get_cached_cli_version()


def client_version() -> str:
    """Return the cached Cline CLI version for synchronous header building."""
    return get_cached_cli_version()


def core_version() -> str:
    """Return the cached Cline core version for synchronous header building."""
    return get_cached_core_version()


def _set_cached_for_tests(
    cli: str | None,
    core: str | None,
    fetched_at: float,
) -> None:
    """Test seam: seed or reset both module caches without network."""
    global _cached_cli_version, _cached_core_version
    global _cli_fetched_at, _core_fetched_at
    _cached_cli_version = cli
    _cached_core_version = core
    _cli_fetched_at = fetched_at
    _core_fetched_at = fetched_at


class ClineVersionRefresh:
    """Background task refreshing Cline CLI and core versions every 5 min."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="cline-version-refresh")

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
                    log.exception("cline_version_sweep_error")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
