"""Round 178 — missing logic in ``wiwi/core/``: five seams where a value was
computed but dropped, or a sibling path omitted a step its twin performs.

Audited: ``gateway.py``, ``context.py``, ``recovery.py``, ``telemetry.py``.

1. ``_stream_usage`` was written only by the server's ``_capture_delta``, which
   runs solely when prompt capture is on. Under the default config it stayed
   ``None``, so a mid-stream failover merged nothing and the client's usage
   block disagreed with the spend log. Now published by ``_price_stream`` —
   the one place every streaming cost is finalized.

2. ``merge_resume_context`` summed attempts, usage and cost but left
   ``cache_hit`` alone, so a resumed attempt that read the provider's prompt
   cache never reached ``wiwi_prompt_cache_hits_total``.

3. ``accumulated_stream_usage`` replaces the per-attempt ``UsageFinal`` that
   every encoder sees, but never set ``cost`` — while its documented twin
   ``merge_resume_context`` sums it.

4. ``resume_ctx`` propagated ``cancel`` and ``forward_headers`` but not
   ``span``, so ``_attempt_span`` parented the failover attempt on nothing and
   one client request appeared as two root traces.

5. ``HealthHealer._sweep`` gathered every probe with no per-target isolation:
   one adapter raising outside ``_probe``'s ``try`` aborted the whole batch,
   and its deployment stayed marked "credited" so no sibling could restore it.

6. ``_complete_via_stream``'s 401-refresh retry fell through to the pump on
   success without recording ``ok_after_refresh``, though ``_call_once``, the
   pump and ``_end_attempt_span``'s docstring all say it does.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wiwi.core.context import RequestContext
from wiwi.core.gateway import accumulated_stream_usage, merge_resume_context
from wiwi.core.recovery import HealthHealer
from wiwi.ir.types import Request, Usage
from wiwi.streaming import deltas as dl


def _ctx(**kw) -> RequestContext:
    base = {"surface": "chat",
            "ir_req": Request(model="gpt-4o", messages=[], stream=True)}
    base.update(kw)
    return RequestContext(**base)


# -- 1. _price_stream publishes the frame the merge/summary read ---------------

def test_price_stream_publishes_stream_usage_without_capture():
    """``ctx._stream_usage`` is set by pricing, not by the server's capture."""
    from wiwi.core.gateway import Gateway

    ctx = _ctx()
    assert getattr(ctx, "_stream_usage", None) is None
    dep = SimpleNamespace(provider=SimpleNamespace(provider_type="openai", name="openai"),
                          model_id="gpt-4o", group="g")
    gw = Gateway.__new__(Gateway)  # only _price_stream/_cache_savings are used
    gw.cost = SimpleNamespace(
        _lookup=lambda *a, **k: None,
        cost_with_status=lambda *a, **k: SimpleNamespace(cost=0.25, unpriced=False,
                                                         model_key=""))
    dep.settle_tokens = lambda *a, **k: None
    u = dl.UsageFinal(prompt=10, output=5, cached=0, reasoning=0,
                      cache_creation=0, estimated=False, cached_in_prompt=None)
    gw._price_stream(ctx, dep, u)
    assert ctx._stream_usage is u
    assert ctx.usage.prompt_tokens == 10
    assert ctx.cost == 0.25


# -- 2/3. merge_resume_context + accumulated_stream_usage parity ---------------

def test_merge_resume_context_ors_cache_hit():
    origin = _ctx()
    origin.cache_hit = False
    resumed = _ctx()
    resumed.cache_hit = True
    merge_resume_context(origin, resumed)
    assert origin.cache_hit is True


def test_merge_resume_context_never_clears_a_hit():
    origin = _ctx()
    origin.cache_hit = True
    resumed = _ctx()
    resumed.cache_hit = False
    merge_resume_context(origin, resumed)
    assert origin.cache_hit is True


def test_accumulated_stream_usage_sums_cost_like_the_merge():
    """The frame every encoder sees must carry the cost its twin sums."""
    origin = _ctx()
    origin.usage = Usage(prompt_tokens=10, completion_tokens=5)
    origin.cost = 0.10
    resumed = _ctx()
    resumed.usage = Usage(prompt_tokens=4, completion_tokens=2)
    resumed.cost = 0.05
    origin._pending_resume_ctxs = [resumed]  # type: ignore[attr-defined]

    total = accumulated_stream_usage(origin)
    assert total is not None
    assert total.prompt == 14 and total.output == 7
    assert total.cost == pytest.approx(0.15)

    # The documented twin reaches the same money by its own arithmetic.
    merge_resume_context(origin, resumed)
    assert origin.cost == pytest.approx(total.cost)


def test_accumulated_stream_usage_first_source_carries_its_cost():
    ctx = _ctx()
    ctx.usage = Usage(prompt_tokens=3, completion_tokens=1)
    ctx.cost = 0.02
    total = accumulated_stream_usage(ctx)
    assert total is not None and total.cost == pytest.approx(0.02)


# -- 5. healer sweep isolation ------------------------------------------------

class _Key:
    def __init__(self, label):
        self.label = label


class _Dep:
    """Just the three attributes ``_sweep`` reads off a deployment."""

    def __init__(self, group):
        self.group = group
        self.provider = SimpleNamespace(name=f"prov-{group}")
        self.model_id = "m"
        self.timeout = None


async def test_sweep_survives_one_raising_probe(monkeypatch):
    """A probe that raises must not abort the batch or eat the credit."""
    healer = HealthHealer.__new__(HealthHealer)
    healer._s = SimpleNamespace(max_probes_per_sweep=10)
    seen: list[str] = []

    def fake_collect():
        return [(_Dep("A"), _Key("a")), (_Dep("B"), _Key("b")),
                (_Dep("C"), _Key("c"))]

    healer._collect_pairs = fake_collect  # type: ignore[attr-defined]

    async def fake_probe_pair(dep, key, credit_dep):
        seen.append((dep.group, credit_dep))
        if dep.group == "A":
            raise RuntimeError("adapter blew up in encode_request")

    healer._probe_pair = fake_probe_pair  # type: ignore[attr-defined]

    issued = await healer._sweep()
    # Every target was attempted despite the first one raising.
    assert issued == 3
    assert [d for d, _ in seen] == ["A", "B", "C"]


async def test_sweep_credits_deployment_after_a_failed_probe():
    """A failed probe returns its credit so a sibling key can still restore."""
    healer = HealthHealer.__new__(HealthHealer)
    healer._s = SimpleNamespace(max_probes_per_sweep=10)
    seen: list[tuple[str, bool]] = []
    first = {"done": False}

    def fake_collect():
        # Two keys of the *same* deployment, then another deployment.
        return [(_Dep("A"), _Key("a1")), (_Dep("A"), _Key("a2")),
                (_Dep("B"), _Key("b"))]

    healer._collect_pairs = fake_collect  # type: ignore[attr-defined]

    async def fake_probe_pair(dep, key, credit_dep):
        seen.append((f"{dep.group}/{key.label}", credit_dep))
        if key.label == "a1" and not first["done"]:
            first["done"] = True
            raise RuntimeError("boom")

    healer._probe_pair = fake_probe_pair  # type: ignore[attr-defined]

    await healer._sweep()
    credits = dict(seen)
    # a1 claimed the credit and failed; a2 must still be allowed to credit.
    assert credits["A/a1"] is True
    assert credits["A/a2"] is True
    assert credits["B/b"] is True

def test_context_of_none_is_none():
    """Guard the premise: without a span the attempt parents on nothing."""
    from wiwi.core.context import context_of

    assert context_of(None) is None


# -- tracer error status ------------------------------------------------------

def test_tracer_close_marks_error_status():
    """A failed span must export ERROR, not just an attribute."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from wiwi.config import TelemetrySettings
    from wiwi.core.telemetry import Tracer

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    t = Tracer()
    t._provider = provider
    t._tracer = provider.get_tracer("wiwi")
    t.is_active = True
    t._settings = TelemetrySettings()

    span = t.open("wiwi.upstream", kind="client")
    t.close(span, status=500, **{"wiwi.attempt_status": "http_500"})
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code.name == "ERROR"
    assert spans[0].status.description == "http_500"


def test_tracer_close_keeps_success_unset():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from wiwi.core.telemetry import Tracer

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    t = Tracer()
    t._provider = provider
    t._tracer = provider.get_tracer("wiwi")
    t.is_active = True

    span = t.open("wiwi.upstream", kind="client")
    t.close(span)
    spans = exporter.get_finished_spans()
    assert spans[0].status.status_code.name == "UNSET"