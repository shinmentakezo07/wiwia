"""Round 146: the realtime no-usage counter reaches Prometheus (AUDIT #347).

``_RT_COUNTERS["usage_missing"]`` counts realtime sessions that ended without a
single usage event — a failed handshake, an upstream 401/5xx, or the
``max_session_s`` cap cutting the session before the ``response.done`` frame
that carries the turn's usage. Each one is a session wiwi served but could not
price, i.e. a reconciliation gap against the upstream bill.

The counter was surfaced only on ``GET /health``. Every *other* process-lifetime
loss counter in wiwi — ``spend_charge_failures``, the four log-loss counters —
is also exported in the Prometheus exposition, because ``/health`` is a single
JSON snapshot a scrape pipeline never reads and a value nobody alerts on is a
value nobody notices. The gap matters more than usual here: the count is
*only* derivable from a module-level dict, so a ``/health`` poll that samples
once a minute silently misses every session that closes between two polls.

These tests pin the exposition line and the HELP/TYPE declaration, pin that the
counter is emitted at zero rather than omitted (a counter that appears and
disappears is a monitoring bug), and pin that the precedent copied here —
``wiwi_spend_charge_failures_total`` — still works.
"""

from __future__ import annotations

import re

import httpx
import pytest
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.logging_core.subsystem import RequestTotals
from wiwi.server import app as app_mod
from wiwi.server.app import create_app
from wiwi.server.metrics import render_metrics

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}

_COUNTER_TYPE = re.compile(r"^# TYPE (\S+) counter$", re.MULTILINE)


def _samples(text: str) -> dict[str, float]:
    """Map every sample line (name+labels) to its value."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, value = line.rpartition(" ")
        try:
            out[key] = float(value)
        except ValueError:
            continue
    return out


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
        # /metrics is off by default; without this the SPA catch-all answers
        # /metrics with index.html and every assertion below would be
        # comparing against markup.
        router_settings=RouterSettings(prometheus_enabled=True,
                                       prometheus_path="/metrics"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


# -- the counter reaches the exposition --------------------------------------

def test_realtime_usage_missing_is_exported():
    """The no-usage count is scrapable, not just a /health JSON field."""
    text = render_metrics([], totals=RequestTotals(), realtime_usage_missing=6)
    assert _samples(text)["wiwi_realtime_usage_missing_total"] == 6
    assert "# HELP wiwi_realtime_usage_missing_total" in text
    assert "wiwi_realtime_usage_missing_total" in _COUNTER_TYPE.findall(text)
    # It is its own counter, not folded into the log-loss sum: a realtime
    # session is not a lost log event.
    assert _samples(text)["wiwi_log_events_dropped_total"] == 0


def test_realtime_usage_missing_is_present_at_zero():
    """Emitted at 0 by default — a counter that appears and disappears is a bug.

    A counter scraped only while non-zero makes ``increase()`` and ``rate()``
    return nothing for the overwhelming majority of processes.
    """
    text = render_metrics([])
    assert "wiwi_realtime_usage_missing_total 0" in text
    assert _samples(text)["wiwi_realtime_usage_missing_total"] == 0
    assert "wiwi_realtime_usage_missing_total" in _COUNTER_TYPE.findall(text)
    # Also present on the empty-window early return path, which is what a
    # freshly booted gateway serves.
    assert "wiwi_realtime_usage_missing_total" in render_metrics([], totals=None)


async def test_realtime_usage_missing_reaches_the_metrics_endpoint(client):
    """End to end: the /metrics route exports the value /health reports.

    Same counter, one source of truth — the route reads the same module-level
    dict the ``/health`` payload reads, so the two surfaces cannot disagree.
    """
    saved = app_mod._RT_COUNTERS["usage_missing"]
    try:
        app_mod._RT_COUNTERS["usage_missing"] = 3
        r = await client.get("/metrics", headers=AUTH)
        assert r.status_code == 200, r.text
        assert "wiwi_realtime_usage_missing_total 3" in r.text
        assert "# TYPE wiwi_realtime_usage_missing_total counter" in r.text
        # The exposition and /health agree — same object, not a second counter.
        health = await client.get("/health")
        assert health.json()["realtime_usage_missing"] == 3
    finally:
        app_mod._RT_COUNTERS["usage_missing"] = saved


async def test_realtime_usage_missing_exported_at_zero_over_http(client):
    """A quiet gateway still exports the counter, rather than omitting it."""
    saved = app_mod._RT_COUNTERS["usage_missing"]
    try:
        app_mod._RT_COUNTERS["usage_missing"] = 0
        r = await client.get("/metrics", headers=AUTH)
        assert r.status_code == 200, r.text
        assert "wiwi_realtime_usage_missing_total 0" in r.text
        assert _samples(r.text)["wiwi_realtime_usage_missing_total"] == 0
    finally:
        app_mod._RT_COUNTERS["usage_missing"] = saved


# -- the precedent copied here still works ------------------------------------

async def test_spend_charge_failures_still_exported(client):
    """``wiwi_spend_charge_failures_total`` is unchanged by the new argument.

    The new counter was threaded in as a trailing positional at a call site that
    already passes six values positionally; a mis-ordered argument would have
    silently shifted spend failures into a log-loss counter.
    """
    state = client._transport.app.state.wiwi  # type: ignore[attr-defined]
    saved = state.spend_charge_failures
    try:
        state.spend_charge_failures = 4
        r = await client.get("/metrics", headers=AUTH)
        assert r.status_code == 200, r.text
        samples = _samples(r.text)
        assert samples["wiwi_spend_charge_failures_total"] == 4
        assert samples["wiwi_log_events_dropped_total"] == 0, (
            "spend failures must not have been shifted into the log-loss sum"
        )
        assert "wiwi_request_logs_dropped_total 0" in r.text
    finally:
        state.spend_charge_failures = saved


def test_spend_charge_failures_unit_default_unaffected():
    """The round-82 unit assertion still holds verbatim."""
    text = render_metrics([], totals=RequestTotals(), spend_charge_failures=4)
    assert _samples(text)["wiwi_spend_charge_failures_total"] == 4
    assert "# TYPE wiwi_spend_charge_failures_total counter" in text
    default = render_metrics([])
    assert _samples(default)["wiwi_spend_charge_failures_total"] == 0
    assert _samples(default)["wiwi_log_events_dropped_total"] == 0
