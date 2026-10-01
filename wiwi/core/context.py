"""RequestContext — the single holder passed through handlers, router, and pump."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from wiwi.ir.types import Request, Usage

Surface = Literal["chat", "responses", "messages", "completions"]


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
    # Budget dollars reserved at admission for a budget-capped virtual key
    # (AUDIT #324). 0.0 = nothing reserved (master, uncapped key, or refusal).
    # Reconciled to the actual cost after the response; every early return
    # between reserve and reconcile must refund it.
    budget_reserved: float = 0.0
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
    # The active ``wiwi.request`` telemetry span (spec C), or None when tracing
    # is off. Threaded here rather than reached through the contextvar because
    # async generators and tasks do not reliably inherit it: ``_stream_response``
    # and the gateway pump both run in contexts captured at *creation* time, so
    # a span opened later in the request body would not be their parent. Every
    # stage already receives ``ctx``, so the span rides along for free.
    span: Any = None
    # The in-flight ``wiwi.upstream`` attempt span (spec C), or None. Set by the
    # gateway wrapper that owns the current attempt so ``_headers`` can put this
    # attempt's ``traceparent`` on the outbound request; cleared when it ends.
    attempt_span: Any = None
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


def context_of(span: Any) -> Any:
    """OTel parent context for *span*, or None when there is no live span.

    Kept here (not in ``core.telemetry``) so ``core.context`` stays free of the
    telemetry module and its config import. Imported lazily because
    ``opentelemetry`` is an optional extra: without it this returns None and
    every explicitly-parented span falls back to the ambient context — which is
    exactly right, since without the SDK no span is ever live.
    """
    if span is None:
        return None
    try:
        from opentelemetry.trace import set_span_in_context
    except ImportError:
        return None
    return set_span_in_context(span)
