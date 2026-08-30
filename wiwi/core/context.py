"""RequestContext — the single holder passed through handlers, router, and pump."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from wiwi.ir.types import Request, Usage
from wiwi.logging_core.events import LogEvent

Surface = Literal["chat", "responses", "messages"]


@dataclass
class AttemptRecord:
    deployment: str
    provider: str
    provider_key_label: str
    status: str  # "ok" | error kind
    latency_ms: int
    detail: str = ""




@dataclass
class RequestContext:
    surface: Surface
    ir_req: Request
    started: float = field(default_factory=time.monotonic)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    auth: Any = None  # AuthInfo from auth service
    raw_body_bytes: int = 0
    # routing
    group: str | None = None
    deployment: Any = None  # Deployment
    provider_key: Any = None  # ProviderKey
    attempts: list[AttemptRecord] = field(default_factory=list)
    # stream state
    first_token_at: float | None = None
    last_token_at: float | None = None

    usage: Usage | None = None
    cost: float = 0.0
    # outcomes
    cache_hit: bool = False
    stop_reason: str | None = None
    status: int = 200
    error: Any = None  # WiwiError
    log_buffer: list[LogEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    # Set by the streaming path: `execute_with_retries` must NOT credit the key
    # at connect time (its call_one returns as soon as the pump connects). The
    # pump credits the key once the stream actually completes. See AUDIT #6.
    _defer_key_credit: bool = False

    def note_attempt(self, deployment: str, provider: str, key_label: str,
                     status: str, latency_ms: int, detail: str = "") -> None:
        self.attempts.append(AttemptRecord(deployment, provider, key_label, status, latency_ms, detail))
