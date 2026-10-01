"""Telemetry facade tests: the disabled path must be a true no-op (spec C)."""

import builtins
import sys

from wiwi.config import TelemetrySettings
from wiwi.core.telemetry import Tracer


def test_disabled_is_a_noop_context_manager():
    t = Tracer()
    t.configure(TelemetrySettings(enabled=False))
    assert t.is_active is False
    assert t.propagation_headers(None) == {}
    assert t.trace_id_of(None) is None
    with t.span("x", surface="chat") as span:
        span.set_attribute("k", "v")
        span.set_status("ok")


def test_missing_sdk_degrades_instead_of_raising(monkeypatch):
    # Simulate a deployment that never installed [otel].
    for name in list(sys.modules):
        if name.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name.startswith("opentelemetry"):
            raise ImportError("no otel extra")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    t = Tracer()
    t.configure(TelemetrySettings(enabled=True, endpoint="http://localhost:4318"))
    assert t.is_active is False          # degraded, not crashed
    with t.span("y"):
        pass


def test_enabled_without_endpoint_stays_inactive():
    t = Tracer()
    t.configure(TelemetrySettings(enabled=True, endpoint=""))
    assert t.is_active is False


def test_unknown_span_kind_is_tolerated_when_disabled():
    t = Tracer()
    t.configure(TelemetrySettings(enabled=False))
    with t.span("z", kind="server"):
        pass


def test_telemetry_defaults_to_off_and_parses_a_section():
    from wiwi.config import load_config_from_string

    cfg = load_config_from_string("model_list: []\n")
    assert cfg.telemetry.enabled is False
    assert cfg.telemetry.endpoint == ""
    assert cfg.telemetry.service_name == "wiwi"
    cfg2 = load_config_from_string(
        "model_list: []\ntelemetry:\n  enabled: true\n"
        "  endpoint: http://collector:4318/v1/traces\n"
        "  headers:\n    x-api-key: k\n")
    assert cfg2.telemetry.enabled is True
    assert cfg2.telemetry.headers["x-api-key"] == "k"
