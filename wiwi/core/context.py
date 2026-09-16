"""RequestContext — the single holder passed through handlers, router, and pump."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from wiwi.ir.types import Request, Usage

Surface = Literal["chat", "responses", "messages"]


@dataclass
class AttemptRecord:
    deployment: str
    provider: str
    provider_key_label: str
    status: str  # "ok" | error kind
    latency_ms: int
    detail: str = ""
    # The provider-native model id this attempt was sent to. ``deployment`` is
    # ``"<group>/<model_id>"`` and BOTH halves may contain "/" (OpenRouter ids
    # such as ``stealth/ox-alpha`` are the normal case), so the model cannot be
    # recovered from the deployment string by splitting on "/": the rollup
    # recorded the bare tail and the retroactive repricer matched on it, which
    # conflated two models sharing a last path segment and billed one model's
    # history at the other's rate (round 65). Record it instead of re-deriving.
    model_id: str = ""




@dataclass
class RequestContext:
    surface: Surface
    ir_req: Request
    started: float = field(default_factory=time.monotonic)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    auth: Any = None  # AuthInfo from auth service
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
    # Estimated prompt+completion tokens for this request, charged against a
    # deployment's per-deployment tpm cap at admission and reconciled to actual
    # usage at pricing time (AUDIT #101). 0 means "unknown": the tpm check then
    # charges nothing for this request.
    est_tokens: int = 0
    # outcomes
    cache_hit: bool = False
    stop_reason: str | None = None
    status: int = 200
    error: Any = None  # WiwiError
    metadata: dict[str, Any] = field(default_factory=dict)
    # Inbound request headers the client expects the upstream to see.
    # Anthropic's Messages format is header-and-body coupled: ``anthropic-beta``
    # gates features that body fields then rely on, so stripping the header
    # while forwarding the body produces hard 400s (and silently disables every
    # header-only capability, e.g. the 1M context window). Populated by the
    # server from the inbound request; merged into the outbound header set by
    # the gateway. Empty for dialects with no header-coupled surface.
    forward_headers: dict[str, str] = field(default_factory=dict)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    # Set by the streaming path: `execute_with_retries` must NOT credit the key
    # at connect time (its call_one returns as soon as the pump connects). The
    # pump credits the key once the stream actually completes. See AUDIT #6.
    _defer_key_credit: bool = False

    def note_attempt(self, deployment: str, provider: str, key_label: str,
                     status: str, latency_ms: int, detail: str = "",
                     model_id: str = "") -> None:
        self.attempts.append(AttemptRecord(deployment, provider, key_label, status,
                                           latency_ms, detail, model_id))
