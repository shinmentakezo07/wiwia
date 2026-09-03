"""CacheBackend protocol + entry types (docs/CORE.md §6 interface seam).

A Redis (or semantic) backend plugs in here without touching the request
path: implement ``get``/``set`` and register an instance where the response
cache is constructed in ``wiwi/server/app.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class CacheEntry:
    payload: bytes  # orjson-serialized response body
    media_headers: dict[str, str] = field(default_factory=dict)
    stored_at: float = 0.0  # unix epoch seconds
    request_id: str = ""
    model: str = ""


class CacheBackend(Protocol):
    async def get(self, key: str) -> CacheEntry | None: ...

    async def set(self, key: str, entry: CacheEntry) -> None: ...

    async def aclose(self) -> None: ...
