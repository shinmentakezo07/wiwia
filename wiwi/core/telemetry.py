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
import threading
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
                                    headers=dict(settings.headers) or None,
                                    timeout=settings.export_timeout_s)
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

    @staticmethod
    def _kind(kind: str | None) -> Any:
        """Map a SpanKind name ("server"/"client"/…) to the SDK enum."""
        if kind is None:
            return None
        try:
            from opentelemetry.trace import SpanKind
        except ImportError:
            return None
        return getattr(SpanKind, kind.upper(), None)

    def open(self, name: str, kind: str | None = None, context: Any = None,
             **attrs: Any) -> Any:
        """Start a span **without** entering a ``with`` block. Pair with :meth:`close`.

        Used for the request span, which outlives any single function body. It
        is deliberately ``start_span`` and not ``start_as_current_span``: the
        pipeline's downstream spans are parented explicitly through
        ``ctx.span``, so this must not disturb the ambient context.
        """
        if not self.is_active:
            return _NoopSpan()
        # ``kind`` must be omitted rather than passed as None: the SDK stores the
        # None and the OTLP encoder then raises ``KeyError: None`` on
        # ``_SPAN_KIND_MAP[kind]``, dropping the whole batch. Only pass it when
        # it resolves to a real SpanKind.
        otel_kind = self._kind(kind)
        span = (self._tracer.start_span(name, kind=otel_kind, context=context)
                if otel_kind is not None
                else self._tracer.start_span(name, context=context))
        for key, value in attrs.items():
            if value is not None:
                span.set_attribute(key, value)
        return span

    def close(self, span: Any, **attrs: Any) -> None:
        """Set final attributes on a span from :meth:`open` and end it.

        Idempotent: several layers close defensively (the error helper, the
        execution body's ``finally``, the entrypoint's ``finally``), and an
        already-ended span must not be re-annotated or re-ended. The SDK's
        ``is_recording`` reports exactly that.
        """
        if span is None:
            return
        try:
            if not span.is_recording():
                return
        except Exception:  # noqa: BLE001 — a foreign span object must not break
            return         #  the response path
        for key, value in attrs.items():
            if value is not None:
                with contextlib.suppress(Exception):
                    span.set_attribute(key, value)
        with contextlib.suppress(Exception):
            span.end()

    @contextlib.contextmanager
    def span(self, name: str, kind: str | None = None, context: Any = None,
             **attrs: Any) -> Iterator[Any]:
        """Open a span whether or not tracing is on. ``kind`` names an OTel SpanKind."""
        if not self.is_active:
            yield _NoopSpan()
            return
        # See ``open``: a None kind breaks the OTLP encoder.
        otel_kind = self._kind(kind)
        cm = (self._tracer.start_as_current_span(name, kind=otel_kind,
                                                 context=context)
              if otel_kind is not None
              else self._tracer.start_as_current_span(name, context=context))
        with cm as span:
            for key, value in attrs.items():
                if value is not None:
                    span.set_attribute(key, value)
            yield span

    def extract(self, headers: Mapping[str, str]) -> Any:
        """Parse an inbound W3C trace and return it as a parent context.

        Returns rather than attaches: the extracted context has to cover the
        whole request, and ``attach`` would need a matching ``detach`` on every
        exit path of a 500-line handler. Passing it explicitly as the parent of
        the root span gives the same continuation with no lifecycle to get
        wrong — and it matches how every other span in the pipeline is parented.
        """
        if not self.is_active:
            return None
        try:
            from opentelemetry.propagate import extract
            return extract(dict(headers))
        except Exception:  # noqa: BLE001 — a malformed header must not fail a request
            return None

    def propagation_headers(self, span: Any) -> dict[str, str]:
        """W3C ``traceparent``/``tracestate`` for *span*; empty when inactive.

        Derives from the span rather than the ambient context on purpose: the
        request and stream spans are explicitly parented (see :meth:`open`) and
        never pushed as current, so ``inject()`` would emit an unrelated or
        empty context. And the gateway's per-attempt spans are opened with
        ``start_as_current_span`` in a loop body, so the contextvar is right
        only sometimes — reading the span is always right.
        """
        if not self.is_active or span is None:
            return {}
        try:
            from opentelemetry.trace import TraceFlags
            ctx = span.get_span_context()
            if not ctx.is_valid:
                return {}
            flags = ctx.trace_flags if ctx.trace_flags is not None else TraceFlags(1)
            out = {"traceparent": (f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}"
                                   f"-{int(flags):02x}")}
            state = getattr(ctx, "trace_state", None)
            if state:
                out["tracestate"] = str(state)
            return out
        except Exception:  # noqa: BLE001 — a foreign span must not fail a request
            return {}

    def trace_id_of(self, span: Any) -> str | None:
        """Hex trace id of *span*, or None when inactive/invalid."""
        if not self.is_active or span is None:
            return None
        try:
            ctx = span.get_span_context()
            return f"{ctx.trace_id:032x}" if ctx.is_valid else None
        except Exception:  # noqa: BLE001 — a foreign span must not fail a request
            return None

    def force_flush(self, timeout_millis: int = 5000) -> bool:
        """Flush pending spans now. True when there was nothing to flush or it
        succeeded, False on timeout. Used by tests and by ``shutdown``."""
        provider = self._provider
        if provider is None:
            return True
        with contextlib.suppress(Exception):
            return provider.force_flush(timeout_millis)
        return False

    def shutdown(self, timeout: float = 5.0) -> None:
        """Flush and tear down the provider (lifespan shutdown), bounded.

        The OTLP/HTTP exporter is synchronous and the batch processor's flush
        joins an export that can retry a dead collector for tens of seconds.
        Server shutdown must not wait on a telemetry sink: flush on a daemon
        thread and stop waiting after *timeout*. Unflushed spans are dropped,
        which is the correct trade at shutdown — the alternative is a process
        that will not exit because a collector is unreachable.
        """
        provider, self._provider = self._provider, None
        self._tracer = None
        self.is_active = False
        if provider is None:
            return
        thread = threading.Thread(target=provider.shutdown, daemon=True,
                                  name="wiwi-telemetry-shutdown")
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            log.warning("telemetry_shutdown_timeout", timeout_s=timeout)


tracer = Tracer()
