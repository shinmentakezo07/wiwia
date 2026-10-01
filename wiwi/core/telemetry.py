"""OTLP tracing facade (spec C).

Importable with **zero** OpenTelemetry packages installed: the SDK is imported
inside :meth:`Tracer.configure`, and every disabled/absent path degrades to a no-op.
Tracing must never be a serving dependency — a collector outage, a missing extra, or
an operator flipping the switch off all leave the request path exactly as it was.

Spans are opened at seams that already exist (``run_chat_like``, the attempt loop,
the stream pump) and attributes come from fields already on ``RequestContext``, so no
measurement is added to the hot path.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from typing import Any, Self

import structlog

from wiwi.config import TelemetrySettings

log = structlog.get_logger()


class _NoopSpan:
    """Stand-in for an OTel span when tracing is off or the SDK is absent."""

    def set_attribute(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, *args: Any, **kwargs: Any) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class Tracer:
    """Process-wide tracer facade. Disabled unless :meth:`configure` turns it on."""

    def __init__(self) -> None:
        self.is_active = False
        self._tracer: Any = None
        self._provider: Any = None
        self._settings = TelemetrySettings()

    def configure(self, settings: TelemetrySettings) -> None:
        """Turn tracing on. Idempotent; never raises.

        The entire SDK import lives here so a deployment without the ``[otel]``
        extra can still import this module.
        """
        self._settings = settings
        self._tracer = None
        self.is_active = False
        previous, self._provider = self._provider, None
        if previous is not None:
            with contextlib.suppress(Exception):
                previous.shutdown()
        if not settings.enabled:
            return
        if not settings.endpoint:
            log.warning("telemetry_enabled_without_endpoint")
            return
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        except ImportError:
            log.warning("telemetry_extra_missing", hint="install wiwi[otel]")
            return

        ratio = min(max(settings.sample_ratio, 0.0), 1.0)
        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.service_name}),
            sampler=ParentBased(TraceIdRatioBased(ratio)))
        exporter = OTLPSpanExporter(endpoint=settings.endpoint,
                                    headers=dict(settings.headers) or None)
        # BatchSpanProcessor exports off the request path: a collector outage
        # costs dropped spans, never latency.
        provider.add_span_processor(BatchSpanProcessor(exporter))
        # Deliberately *not* ``trace.set_tracer_provider``: that is once-per-process
        # and a re-configure (tests, a settings reload) would keep exporting to the
        # first endpoint. Our own tracer plus the context API give the same nesting
        # and propagation without that global.
        self._provider = provider
        self._tracer = provider.get_tracer("wiwi")
        self.is_active = True
        log.info("telemetry_enabled", endpoint=settings.endpoint, sample_ratio=ratio)

    @contextlib.contextmanager
    def span(self, name: str, kind: str | None = None, **attrs: Any) -> Iterator[Any]:
        """Open a span whether or not tracing is on. ``kind`` names an OTel SpanKind."""
        if not self.is_active:
            yield _NoopSpan()
            return
        otel_kind = None
        if kind is not None:
            with contextlib.suppress(Exception):
                from opentelemetry.trace import SpanKind
                otel_kind = getattr(SpanKind, kind.upper(), None)
        with self._tracer.start_as_current_span(name, kind=otel_kind) as span:
            for key, value in attrs.items():
                if value is not None:
                    span.set_attribute(key, value)
            yield span

    def extract(self, headers: Mapping[str, str]) -> None:
        """Continue an inbound W3C trace. No-op when disabled or header absent."""
        if not self.is_active:
            return
        with contextlib.suppress(Exception):
            from opentelemetry.propagate import extract
            extract(dict(headers))

    def current_traceparent(self) -> str | None:
        """W3C ``traceparent`` for the active span, or None when inactive."""
        if not self.is_active:
            return None
        try:
            from opentelemetry.propagate import inject
            carrier: dict[str, str] = {}
            inject(carrier)
            return carrier.get("traceparent")
        except Exception:  # noqa: BLE001 — a broken global propagator must not
            return None   #  fail a request, and there is nothing to recover here

    def current_trace_id(self) -> str | None:
        """Hex trace id of the active span, or None when inactive/invalid."""
        if not self.is_active:
            return None
        try:
            from opentelemetry.trace import format_trace_id, get_current_span
            ctx = get_current_span().get_span_context()
            return format_trace_id(ctx.trace_id) if ctx.is_valid else None
        except Exception:  # noqa: BLE001 — a broken context must not fail a request
            return None

    def shutdown(self) -> None:
        """Flush and tear down the provider (lifespan shutdown)."""
        provider, self._provider = self._provider, None
        self._tracer = None
        self.is_active = False
        if provider is not None:
            with contextlib.suppress(Exception):
                provider.shutdown()


tracer = Tracer()
