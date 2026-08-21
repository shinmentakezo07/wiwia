"""LogEvent types for the three streams (request / proxy / audit). Never mixed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

LogStream = Literal["request", "proxy", "audit"]
ProxyLevel = Literal["debug", "info", "warn", "error"]


@dataclass
class LogEvent:
    stream: LogStream
    ts: float
    request_id: str = ""
    surface: str = ""
    key_alias: str = ""
    model_group: str = ""
    provider: str = ""
    provider_key_label: str = ""
    status: int = 200
    error_code: str = ""
    tok_in: int = 0
    tok_cached: int = 0
    tok_reasoning: int = 0
    tok_out: int = 0
    tps: float = 0.0
    ttft_ms: float = 0.0
    latency_ms: float = 0.0
    cost: float = 0.0
    was_stream: bool = False
    cache_hit: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)
    # proxy-log fields
    level: ProxyLevel = "info"
    message: str = ""
    # audit fields
    actor: str = ""
    action: str = ""
    target: str = ""
    diff: dict[str, Any] = field(default_factory=dict)
