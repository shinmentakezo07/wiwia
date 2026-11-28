"""Operator surfaces: /health and the configurable Prometheus metrics path.

Extracted verbatim from ``server/app.py`` (round 206). ``_RT_COUNTERS`` is
the process-lifetime realtime loss-counter dict defined there — passed in so
/health and the metrics exporter read the same object and cannot disagree
(AUDIT #347).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from wiwi.server.dependencies import AdminUser
from wiwi.server.metrics import render_metrics


def make_router(state, config, *, metrics_path, _rt_counters):
    """Build the health/metrics router bound to the per-app helpers."""
    api = APIRouter(tags=["system"])

    @api.get("/health")
    async def health():
        """Liveness + readiness in one body, but only liveness in the status.

        ``status`` used to be the constant ``"ok"`` (AUDIT #183), so a gateway
        that could not serve a single request — zero providers, or every
        group's deployments cooled/unhealthy — still reported healthy, and the
        Docker ``HEALTHCHECK`` (which only probes for a 200) kept such a
        container in rotation indefinitely. It is now derived from the router:
        ``degraded`` when there is no provider at all or no group has an
        available deployment.

        The HTTP status stays 200 either way: the container probe and any load
        balancer must be able to tell "the process is up" from "the process can
        route", and a 503 here would make an orchestrator kill and restart a
        process that is running perfectly well but merely has no usable key
        configured.
        """
        router = state.router
        logs = state.logs
        groups = len(router.groups)
        providers = len(router.providers)
        available_groups = sum(
            1 for deps in router.groups.values()
            if any(d.available for d in deps))
        return {
            "status": "ok" if providers and available_groups else "degraded",
            "groups": groups,
            "providers": providers,
            # The field that explains a ``degraded`` status: a group exists but
            # every deployment in it is cooling or has no usable key.
            "available_groups": available_groups,
            # Loss counters. Each is process-lifetime and monotonic, and none
            # can be derived from the request-log ring — a dropped or failed
            # event is by definition absent from it (AUDIT #171/#173). Without
            # them a saturated log queue or a failing log DB was invisible
            # except as a missing row.
            "dropped_request_logs": logs.dropped_request_logs,
            "failed_request_log_writes": logs.failed_request_log_writes,
            "dropped_proxy_logs": logs.dropped_proxy_logs,
            "failed_audit_log_writes": logs.failed_audit_log_writes,
            "dropped_log_events": logs.dropped_log_events,
            # Charges the spend write path failed to persist (AUDIT #179). A
            # non-zero value means the budget cap was not enforced for that
            # many requests, which is an accounting incident, not a warning.
            "spend_charge_failures": state.spend_charge_failures,
            # Flush-cycle failures from the batched spend buffer (0 when
            # batching is off). A failed batch stays pending and is retried;
            # a rising value means the DB write path is unhealthy.
            "spend_flush_failures": (
                state.spend.flush_failures
                if state.spend is not None else 0),
            # Realtime sessions that ended without a single usage event
            # (AUDIT #347): a failed handshake, an upstream 401/5xx, or the
            # ``max_session_s`` cap cutting the session before a
            # ``response.done``. Each one is a session wiwi served but could
            # not price, so this is a reconciliation gap against the upstream
            # bill rather than a warning — and by definition it cannot be
            # derived from the ring, since an unpriced session is the one row
            # whose counts are not provider-reported.
            "realtime_usage_missing": _rt_counters["usage_missing"],
        }

    # -- metrics ---------------------------------------------------------------
    # The route is ALWAYS registered, even when Prometheus is disabled.
    #
    # It used to be registered only inside ``if prometheus_enabled:``, so
    # with Prometheus off — the DEFAULT — the path had no handler at all
    # and the SPA catch-all answered it with ``index.html`` and a 200. A
    # scraper then recorded the SPA's HTML as a successful scrape, and the
    # operator's "is Prometheus up?" check read green (AUDIT #349).
    #
    # Now the path answers 404 with a plain-text explanation when the
    # exporter is off — a truthful signal — and the metrics body when it is
    # on.
    @api.get(metrics_path)
    async def prometheus_metrics(actor: AdminUser):
        if not config.router_settings.prometheus_enabled:
            return PlainTextResponse(
                "Prometheus metrics are disabled. Set "
                "router_settings.prometheus_enabled: true to enable this "
                "endpoint.\n",
                media_type="text/plain; charset=utf-8",
                status_code=404,
            )
        # Authorize like every other operator surface: a signed session cookie
        # OR the bearer master key. This used to be a private bearer-only
        # check, so an admin logged into the console could not load /metrics
        # (the audit's "one guard per surface, not two" rule).
        events = [e for _, e in await state.logs.sse.replay("request", 0)]
        # The loss counters and the process-lifetime totals cannot be
        # derived from the ring: a dropped event is absent from it by
        # definition, and a ring-derived total decreases on every eviction
        # (AUDIT #180), which makes PromQL read every eviction as a process
        # restart. Both are passed in from the subsystem that owns them.
        # ``_rt_counters`` is the same object /health reads, so the two
        # operator surfaces cannot disagree (AUDIT #347).
        text = render_metrics(
            events,
            state.logs.dropped_request_logs,
            state.logs.totals,
            state.logs.failed_request_log_writes,
            state.logs.dropped_proxy_logs,
            state.logs.failed_audit_log_writes,
            state.spend_charge_failures,
            _rt_counters["usage_missing"])
        return PlainTextResponse(text, media_type="text/plain; version=0.0.4")

    return api
